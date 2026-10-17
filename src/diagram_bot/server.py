"""Workspace server. Loopback-only by default; team mode adds accounts, roles, TLS and an audit log.
AWS credentials stay in the SDK process."""
import hashlib
import html
import json
import os
import re
import secrets
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .collector import collect, now
from .demo import demo_snapshot
from .samples import SAMPLES
from .drawio import carry_annotations, generate_document, validate_document
from .editor import VERSION, editor_path
from .auth import SESSION_SECONDS, AuditLog, Authenticator, allowed
from .cli import app_version
from .diff import diff
from .findings import review, summary
from .graph import build_graph, upgrade_graph
from .views import view_graph


def data_path():
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "diagram-bot"


def atomic_json(path, value):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, default=str), encoding="utf-8")
    temporary.replace(path)


class CollectionRequest(BaseModel):
    profile: str | None = Field(default=None, max_length=128)
    region: str = Field(pattern=r"^[a-z]{2}(?:-[a-z0-9]+)+-\d+$", max_length=50)
    expected_account: str = Field(pattern=r"^\d{12}$")
    role_arn: str | None = Field(default=None, max_length=2048, pattern=r"^arn:aws[a-z-]*:iam::\d{12}:role/[\w+=,.@/-]+$")
    external_id: str | None = Field(default=None, min_length=2, max_length=1224, pattern=r"^[\w+=,.@:/-]+$")


class DocumentRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    revision: int = Field(ge=0)


class LoginRequest(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=256)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=0)


class RestoreRequest(RevisionRequest):
    from_revision: int = Field(ge=0)


HISTORY = 20


COOKIE, OIDC_COOKIE = "diagram_bot_session", "diagram_bot_oidc"
PUBLIC = {"/login", "/api/login", "/auth/oidc/start", "/auth/oidc/callback"}
LOOPBACK = ["localhost", "127.0.0.1", "[::1]", "testserver"]
LOGIN_PAGE = """<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Diagram Bot 로그인</title><style>body{font-family:-apple-system,"Segoe UI","Noto Sans KR",Arial,sans-serif;background:#F3F5F8;display:grid;
place-items:center;min-height:100vh;margin:0;color:#233247}form{background:#fff;border:1px solid #DFE6EF;border-radius:12px;padding:32px;width:320px}
h1{font-size:20px;margin:0 0 20px}label{display:block;font-size:12px;color:#5A6C86;margin:14px 0 6px}input{width:100%;box-sizing:border-box;padding:10px;
border:1px solid #DCE5F0;border-radius:6px;font-size:14px}button{margin-top:20px;width:100%;padding:11px;border:0;border-radius:6px;background:#4773DD;
color:#fff;font-size:14px}p{color:#B4403F;font-size:12px;min-height:1em}a.sso{display:block;text-align:center;padding:11px;border-radius:6px;
background:#232F3E;color:#fff;text-decoration:none;font-size:14px;margin-bottom:8px}</style></head><body><form id="f"><h1>Diagram Bot</h1>__SSO__
<div __LOCAL__><label for="n">사용자</label><input id="n" autocomplete="username" required><label for="p">비밀번호</label>
<input id="p" type="password" autocomplete="current-password" required><button>로그인</button></div><p id="e" role="alert">__ERROR__</p></form>
<script>document.getElementById('f').onsubmit=async e=>{e.preventDefault();const r=await fetch('/api/login',{method:'POST',
headers:{'Content-Type':'application/json'},body:JSON.stringify({name:document.getElementById('n').value,password:document.getElementById('p').value})});
if(r.ok)location.href='/';else document.getElementById('e').textContent=(await r.json()).detail;};</script></body></html>"""


def create_app(directory=None, editor_directory=None, users=None, allowed_hosts=(), secure=False, oidc=None):
    """users: accounts for team mode (None = single local user). secure: served over TLS (Secure cookie, HSTS)."""
    directory = Path(directory or data_path())
    snapshots = directory / "snapshots"
    documents = directory / "documents"
    snapshots.mkdir(parents=True, exist_ok=True)
    documents.mkdir(parents=True, exist_ok=True)
    static = files("diagram_bot").joinpath("static")
    editor = Path(editor_directory or editor_path())
    token = secrets.token_urlsafe(32)
    mutex = threading.Lock()
    executor = ThreadPoolExecutor(max_workers=1)
    jobs = {}
    auth = Authenticator(users or {}) if users or oidc else None
    audit = AuditLog(directory / "audit.log")

    def save_snapshot(snapshot, identifier=None):
        if snapshot.get("schema_version") != 1:
            raise ValueError("지원하지 않는 스냅샷 버전입니다.")
        metadata = snapshot["metadata"]
        for name in ("account_id", "region", "started_at", "finished_at", "status"):
            if not isinstance(metadata.get(name), str):
                raise ValueError(f"스냅샷 metadata.{name} 필드가 필요합니다.")
        if not isinstance(snapshot.get("coverage"), list) or not isinstance(snapshot.get("raw"), dict):
            raise ValueError("스냅샷 coverage/raw 형식이 잘못되었습니다.")
        snapshot = {**snapshot, "graph": build_graph(snapshot)}
        identifier = identifier or uuid4().hex
        atomic_json(snapshots / f"{identifier}.json", snapshot)
        return identifier

    if not (snapshots / "demo.json").exists():
        save_snapshot(demo_snapshot(), "demo")
    for key, build in SAMPLES.items():
        if not (snapshots / f"sample-{key}.json").exists():
            save_snapshot(build(), f"sample-{key}")

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Diagram Bot", lifespan=lifespan, docs_url=None, redoc_url=None)
    @app.middleware("http")
    async def protect_mutations(request, call_next):
        path, method = request.url.path, request.method
        client = request.client.host if request.client else None
        request.state.user = "local"
        mutation = method in {"POST", "PUT", "DELETE", "PATCH"}
        if auth and path not in PUBLIC:
            session = auth.session(request.cookies.get(COOKIE))
            if not session:
                if path.startswith("/api/"):
                    return JSONResponse({"detail": "로그인이 필요합니다."}, status_code=401)
                return RedirectResponse("/login", status_code=303)
            request.state.user, request.state.role = session["name"], session["role"]
            if not allowed(session["role"], method, path):
                audit.write(session["name"], f"{method} {path}", result="denied", client=client)
                return JSONResponse({"detail": f"{session['role']} 역할로는 할 수 없는 작업입니다."}, status_code=403)
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
        if mutation and path not in ("/api/login", "/api/logout"):
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

    cache, summaries = {}, {}  # parsed snapshots (a few) and list rows, keyed by file modification time

    def get_snapshot(identifier):
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", identifier):
            raise HTTPException(404, "스냅샷을 찾을 수 없습니다.")
        path = snapshots / f"{identifier}.json"
        if not path.is_file():
            raise HTTPException(404, "스냅샷을 찾을 수 없습니다.")
        key = (identifier, path.stat().st_mtime_ns)
        if key not in cache:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            upgrade_graph(snapshot["graph"])
            with mutex:
                for old in [k for k in cache if k[0] == identifier] + list(cache)[:max(0, len(cache) - 7)]:
                    cache.pop(old, None)
                cache[key] = snapshot
        return cache[key]

    def doc_path(identifier, scope):
        view, vpc, resource, references = scope
        # Detail views keep the pre-view key so documents saved by earlier versions stay attached.
        key = (f"{identifier}|{vpc}|{references}" if view == "detail"
               else f"{identifier}|{view}|{vpc}|{resource}|{references}")
        return documents / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def read_json(path, default=None):
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default

    def view_scope(snapshot, view, vpc, resource, references):
        try:
            selection = view_graph(snapshot["graph"], view, vpc, resource, references)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        v = selection["view"]
        return (v["kind"], v["vpc"], v["resource"], v["references"]), selection

    def describe(document, selection, history):
        return {**document, "view": selection["view"], "counts": selection["counts"], "visible": selection["nodes"],
                "history": [{k: h[k] for k in ("revision", "saved_at", "reason")} for h in history]}

    def archive(path, saved, reason):
        """Keep superseded revisions so an explicit relayout or overwrite never loses an edit."""
        history_path = path.with_suffix(".history.json")
        history = read_json(history_path, [])
        if saved.get("xml"):
            history = (history + [{"revision": saved["revision"], "saved_at": saved.get("saved_at"), "reason": reason, "xml": saved["xml"]}])[-HISTORY:]
            atomic_json(history_path, history)
        return history

    @app.get("/", response_class=HTMLResponse)
    def home():
        return static.joinpath("app.html").read_text(encoding="utf-8")

    @app.get("/login", response_class=HTMLResponse)
    def login_page():
        if not auth:
            return RedirectResponse("/", status_code=303)
        return login_html()

    def login_html(error="", status=200):
        page = (LOGIN_PAGE.replace("__SSO__", '<a class="sso" href="/auth/oidc/start">회사 계정(SSO)으로 로그인</a>' if oidc else "")
                .replace("__LOCAL__", "" if auth.users else "hidden").replace("__ERROR__", html.escape(error)))
        return HTMLResponse(page, status_code=status, headers={"X-Frame-Options": "DENY", "Cache-Control": "no-store"})

    @app.get("/auth/oidc/start")
    def oidc_start():
        if not oidc:
            raise HTTPException(404, "SSO가 설정되지 않았습니다.")
        try:
            url, state = oidc.start()
        except (OSError, ValueError) as exc:
            return login_html(f"SSO 제공자에 연결하지 못했습니다: {exc}", 502)
        response = RedirectResponse(url, status_code=303)
        # Lax: the provider sends the browser back with a top-level cross-site GET.
        response.set_cookie(OIDC_COOKIE, state, max_age=600, httponly=True, secure=secure, samesite="lax", path="/auth/oidc")
        return response

    @app.get("/auth/oidc/callback")
    def oidc_callback(request: Request, code: str = "", state: str = "", error: str = ""):
        client = request.client.host if request.client else None
        if not oidc:
            raise HTTPException(404, "SSO가 설정되지 않았습니다.")
        try:
            if error or not code:
                raise ValueError(f"SSO 제공자가 로그인을 거부했습니다: {error or 'code 없음'}")
            name, role, _ = oidc.finish(code, state, request.cookies.get(OIDC_COOKIE))
        except (OSError, ValueError, KeyError) as exc:
            audit.write(None, "login", result="failed", client=client, method="oidc", reason=str(exc)[:200])
            return login_html(str(exc), 403)
        token = auth.start_session(name, role)
        audit.write(name, "login", result="ok", client=client, method="oidc", role=role)
        # A same-site page load, so the Strict session cookie is sent on the next request.
        response = HTMLResponse('<!doctype html><meta http-equiv="refresh" content="0;url=/"><a href="/">계속</a>',
                                headers={"Cache-Control": "no-store"})
        response.set_cookie(COOKIE, token, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
        response.delete_cookie(OIDC_COOKIE, path="/auth/oidc")
        return response

    @app.post("/api/login")
    def login(credentials: LoginRequest, request: Request):
        client = request.client.host if request.client else None
        if not auth:
            raise HTTPException(404, "팀 모드가 아닙니다.")
        if auth.throttled(client):
            audit.write(credentials.name, "login", result="throttled", client=client)
            raise HTTPException(429, "로그인 실패가 많습니다. 15분 후 다시 시도하세요.")
        session = auth.login(credentials.name, credentials.password, client)
        audit.write(credentials.name, "login", result="ok" if session else "failed", client=client)
        if not session:
            raise HTTPException(401, "사용자 이름 또는 비밀번호가 올바르지 않습니다.")
        response = JSONResponse({"name": credentials.name})
        response.set_cookie(COOKIE, session, max_age=SESSION_SECONDS, httponly=True, secure=secure, samesite="strict", path="/")
        return response

    @app.post("/api/logout")
    def logout(request: Request):
        if auth:
            auth.logout(request.cookies.get(COOKIE))
            audit.write(request.state.user, "logout", client=request.client.host if request.client else None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(COOKIE, path="/")
        return response

    @app.get("/api/audit")
    def read_audit(request: Request, limit: int = 200):
        if auth and request.state.role != "admin":
            raise HTTPException(403, "감사 로그는 admin만 볼 수 있습니다.")
        return audit.tail(max(1, min(limit, 1000)))

    @app.get("/api/bootstrap")
    def bootstrap(request: Request):
        try:
            profiles = boto3.Session().available_profiles
        except BotoCoreError:
            profiles = []
        return {"token": token, "editor_ready": (editor / "js/app.min.js").is_file(), "editor_version": VERSION, "profiles": profiles,
                "version": app_version(), "team": bool(auth),
                "user": {"name": request.state.user, "role": getattr(request.state, "role", "admin")}}

    @app.get("/api/snapshots")
    def list_snapshots():
        result = []
        for path in snapshots.glob("*.json"):
            key = (path.stem, path.stat().st_mtime_ns)
            if key not in summaries:
                snapshot = json.loads(path.read_text(encoding="utf-8"))
                summaries[key] = {"id": path.stem, "metadata": snapshot["metadata"], "resource_count": len(snapshot["graph"]["nodes"])}
            result.append(summaries[key])
        return sorted(result, key=lambda row: row["metadata"]["finished_at"], reverse=True)

    @app.get("/api/snapshots/{identifier}")
    def read_snapshot(identifier: str):
        return get_snapshot(identifier)

    @app.get("/api/snapshots/{identifier}/findings")
    def read_findings(identifier: str):
        snapshot = get_snapshot(identifier)
        findings = review(snapshot["graph"], snapshot["coverage"])
        return {"summary": summary(findings), "findings": findings}

    @app.get("/api/snapshots/{identifier}/diff/{other}")
    def compare_snapshots(identifier: str, other: str):
        """Changes from `other` (before) to `identifier` (after)."""
        return diff(get_snapshot(other), get_snapshot(identifier))

    @app.post("/api/snapshots", status_code=201)
    def import_snapshot(snapshot: dict):
        try:
            return {"id": save_snapshot(snapshot)}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(422, f"스냅샷 형식을 확인하세요: {exc}") from exc

    @app.get("/api/snapshots/{identifier}/document")
    def read_document(identifier: str, view: str | None = None, vpc: str | None = None, resource: str | None = None,
                      references: bool = False, reset: bool = False):
        snapshot = get_snapshot(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references)
        path = doc_path(identifier, scope)
        saved = read_json(path)
        history = read_json(path.with_suffix(".history.json"), [])
        if saved and not reset:
            return describe(saved, selection, history)
        xml = generate_document(snapshot, scope[1], scope[3], scope[0], scope[2])
        return describe({"xml": xml, "revision": saved["revision"] if saved else 0, "saved_at": None}, selection, history)

    @app.put("/api/snapshots/{identifier}/document")
    def save_document(identifier: str, document: DocumentRequest, view: str | None = None, vpc: str | None = None,
                      resource: str | None = None, references: bool = False):
        snapshot = get_snapshot(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references)
        try:
            validate_document(document.xml)
        except Exception as exc:
            raise HTTPException(422, "유효한 압축 해제 draw.io XML이 아닙니다.") from exc
        path = doc_path(identifier, scope)
        with mutex:
            saved = read_json(path, {"revision": 0})
            if saved["revision"] != document.revision:
                raise HTTPException(409, "다른 창에서 편집본을 저장했습니다. 새로고침 후 다시 편집하세요.")
            if saved.get("xml") == document.xml:
                return describe(saved, selection, read_json(path.with_suffix(".history.json"), []))
            history = archive(path, saved, "편집 저장")
            saved = {"xml": document.xml, "revision": document.revision + 1, "saved_at": now()}
            atomic_json(path, saved)
        return describe(saved, selection, history)

    @app.post("/api/snapshots/{identifier}/document/relayout")
    def relayout_document(identifier: str, request: RevisionRequest, view: str | None = None, vpc: str | None = None,
                          resource: str | None = None, references: bool = False):
        """Explicit automatic layout: archive the current edit, regenerate, keep user-added notes."""
        snapshot = get_snapshot(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references)
        path = doc_path(identifier, scope)
        xml = generate_document(snapshot, scope[1], scope[3], scope[0], scope[2])
        with mutex:
            saved = read_json(path, {"revision": 0})
            if saved["revision"] != request.revision:
                raise HTTPException(409, "다른 창에서 편집본을 저장했습니다. 새로고침 후 다시 편집하세요.")
            if saved.get("xml"):
                xml = carry_annotations(saved["xml"], xml)
            history = archive(path, saved, "자동 재배치 전")
            saved = {"xml": xml, "revision": saved["revision"] + 1, "saved_at": now()}
            atomic_json(path, saved)
        return describe(saved, selection, history)

    @app.post("/api/snapshots/{identifier}/document/restore")
    def restore_document(identifier: str, request: RestoreRequest, view: str | None = None, vpc: str | None = None,
                         resource: str | None = None, references: bool = False):
        snapshot = get_snapshot(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references)
        path = doc_path(identifier, scope)
        with mutex:
            saved = read_json(path, {"revision": 0})
            if saved["revision"] != request.revision:
                raise HTTPException(409, "다른 창에서 편집본을 저장했습니다. 새로고침 후 다시 편집하세요.")
            history = read_json(path.with_suffix(".history.json"), [])
            source = next((h for h in history if h["revision"] == request.from_revision), None)
            if not source:
                raise HTTPException(404, "보관된 편집본을 찾을 수 없습니다.")
            history = archive(path, saved, f"v{source['revision']} 복원 전")
            saved = {"xml": source["xml"], "revision": saved["revision"] + 1, "saved_at": now()}
            atomic_json(path, saved)
        return describe(saved, selection, history)

    @app.post("/api/collections", status_code=202)
    def start_collection(params: CollectionRequest, request: Request):
        user = request.state.user
        with mutex:
            if any(j["status"] in {"queued", "running"} for j in jobs.values()):
                raise HTTPException(409, "현재 수집이 진행 중입니다.")
            identifier = uuid4().hex
            jobs[identifier] = {"id": identifier, "status": "queued", "started_at": now()}

        def work():
            jobs[identifier]["status"] = "running"
            try:
                snapshot = collect(params.region, params.profile, params.expected_account,
                                   role_arn=params.role_arn, external_id=params.external_id)
                jobs[identifier].update(status=snapshot["metadata"]["status"], snapshot_id=save_snapshot(snapshot))
            except (BotoCoreError, ClientError) as exc:
                code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
                jobs[identifier].update(status="failed", error=f"{code}: 기존 SSO 로그인·프로파일·권한을 확인하세요.")
            except ValueError as exc:
                jobs[identifier].update(status="failed", error=str(exc))
            except Exception:
                jobs[identifier].update(status="failed", error="수집을 완료하지 못했습니다. 마지막 스냅샷은 보존됩니다.")
            jobs[identifier]["finished_at"] = now()
            audit.write(user, "collection", target=f"{params.role_arn or params.expected_account}/{params.region}",
                        result=jobs[identifier]["status"], snapshot=jobs[identifier].get("snapshot_id"))

        executor.submit(work)
        return jobs[identifier]

    @app.get("/api/collections/{identifier}")
    def collection_status(identifier: str):
        if identifier not in jobs:
            raise HTTPException(404, "수집 작업을 찾을 수 없습니다. 앱 재시작 시 진행 상태가 초기화됩니다.")
        return jobs[identifier]

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
