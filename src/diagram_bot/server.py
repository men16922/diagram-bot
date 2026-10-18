"""Workspace server: composes the stores, AWS access and the HTTP routers (routes/) into one FastAPI app.

Loopback-only by default; team mode adds accounts, roles, TLS and an audit log. AWS credentials stay in the server
process. To embed the API elsewhere, build a routes.Context and include the routers you need (see routes/__init__.py).
"""
import secrets
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse

import boto3
from botocore.exceptions import BotoCoreError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .auth import AuditLog, Authenticator, allowed
from .aws_access import AwsAccess
from .cli import app_version
from .demo import demo_snapshot
from .editor import VERSION, editor_path
from .routes import Context, install_error_handlers, role
from .routes import agent, aws, documents, session, snapshots
from .routes.agent import default_chat  # noqa: F401 - the default chat_factory, importable from here as before
from .routes.session import COOKIE, PUBLIC
from .samples import SAMPLE_VERSION, SAMPLES
from .store import DocumentStore, SnapshotStore, data_path

LOOPBACK = ["localhost", "127.0.0.1", "[::1]", "testserver"]


def create_app(directory=None, editor_directory=None, users=None, allowed_hosts=(), secure=False, oidc=None, chat_factory=None,
               sso=None, egress="server", sso_start=None, relay=None):
    """users: accounts for team mode (None = single local user). secure: served over TLS (Secure cookie, HSTS).
    chat_factory(workspace, model) builds the design/collection chat agent (replaced in tests).
    egress: "server" (the server calls AWS) or "browser" (the server signs, the user's browser sends; egress.py).
    sso_start: (start URL, region) offered first in the sign-in dialog."""
    directory = Path(directory or data_path())
    static = files("diagram_bot").joinpath("static")
    editor = Path(editor_directory or editor_path())
    context = Context(snapshots=SnapshotStore(directory), documents=DocumentStore(directory),
                      aws=AwsAccess(egress, sso=sso, relay=relay, sso_start=sso_start), audit=AuditLog(directory / "audit.log"),
                      auth=Authenticator(users or {}) if users or oidc else None, oidc=oidc, secure=secure,
                      token=secrets.token_urlsafe(32), chat_factory=chat_factory)
    context.snapshots.seed(demo_snapshot, SAMPLES, SAMPLE_VERSION)
    auth, audit, token = context.auth, context.audit, context.token
    aws_routes = aws.router(context)

    @asynccontextmanager
    async def lifespan(app):
        yield
        aws_routes.executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Diagram Bot", lifespan=lifespan, docs_url=None, redoc_url=None)
    install_error_handlers(app)

    @app.middleware("http")
    async def protect_mutations(request, call_next):
        path, method = request.url.path, request.method
        client = request.client.host if request.client else None
        request.state.user = "local"
        mutation = method in {"POST", "PUT", "DELETE", "PATCH"}
        if auth and path not in PUBLIC:
            signed_in = auth.session(request.cookies.get(COOKIE))
            if not signed_in:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
                return RedirectResponse("/login", status_code=303)
            request.state.user, request.state.role = signed_in["name"], signed_in["role"]
            if not allowed(signed_in["role"], method, path):
                audit.write(signed_in["name"], f"{method} {path}", result="denied", client=client)
                return JSONResponse({"detail": f"{signed_in['role']} 역할로는 할 수 없는 작업입니다."}, status_code=403)
        if mutation:
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "허용되지 않은 요청 출처"}, status_code=403)
            # Login has no page token yet; it is protected by the Origin check, JSON body and throttling.
            if path != "/api/login" and request.headers.get("x-diagram-bot") != token:
                return JSONResponse({"detail": "앱 세션이 만료되었습니다. 새로고침하세요."}, status_code=403)
            if len(await request.body()) > 15 * 1024 * 1024:
                return JSONResponse({"detail": "파일은 최대 15 MiB입니다."}, status_code=413)
        response = await call_next(request)
        # Relay answers are one per AWS request; the collection or chat turn they serve is audited instead.
        if mutation and path not in ("/api/login", "/api/logout") and not path.startswith("/api/relay/"):
            audit.write(request.state.user, f"{method} {path}", target=str(request.url.query) or None,
                        result=response.status_code, client=client)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if secure:
            response.headers["Strict-Transport-Security"] = "max-age=31536000"
        if request.url.path.startswith("/api/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/":
            response.headers["X-Frame-Options"] = "DENY"
        return response

    # Registered last so it runs first: an unknown Host is rejected before any session or redirect logic.
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=LOOPBACK + list(allowed_hosts))

    @app.get("/", response_class=HTMLResponse)
    def home():
        return static.joinpath("app.html").read_text(encoding="utf-8")

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        try:
            profiles = boto3.Session().available_profiles
        except BotoCoreError:
            profiles = []
        return {"token": token, "editor_ready": (editor / "js/app.min.js").is_file(), "editor_version": VERSION, "profiles": profiles,
                "version": app_version(), "team": bool(auth), "egress": context.aws.egress,
                "user": {"name": request.state.user, "role": role(request)}}

    for module in (session, snapshots, documents, agent):
        app.include_router(module.router(context))
    app.include_router(aws_routes)

    @app.get("/editor/js/app.min.js")
    def editor_with_adapter():
        if not (editor / "js/app.min.js").exists():
            raise HTTPException(503, "diagram-bot setup-editor를 먼저 실행하세요.")
        payload = (editor / "js/app.min.js").read_bytes() + b"\n" + static.joinpath("editor-bridge.js").read_bytes()
        return Response(payload, media_type="application/javascript", headers={"Cache-Control": "public, max-age=3600"})

    app.mount("/static", StaticFiles(directory=str(static)), name="static")
    if editor.is_dir():
        app.mount("/editor", StaticFiles(directory=str(editor), html=True), name="editor")
    return app
