"""Loopback-only demo workspace. AWS credentials stay in the SDK process."""
import hashlib
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
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from .collector import collect, now
from .demo import demo_snapshot
from .drawio import generate_document, validate_document
from .editor import VERSION, editor_path
from .graph import build_graph


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


class DocumentRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    revision: int = Field(ge=0)


def create_app(directory=None, editor_directory=None):
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

    @asynccontextmanager
    async def lifespan(app):
        yield
        executor.shutdown(wait=False, cancel_futures=True)

    app = FastAPI(title="Diagram Bot", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["localhost", "127.0.0.1", "[::1]", "testserver"])

    @app.middleware("http")
    async def protect_mutations(request, call_next):
        if request.method in {"POST", "PUT", "DELETE", "PATCH"}:
            if request.headers.get("x-diagram-bot") != token:
                return JSONResponse({"detail": "앱 세션이 만료되었습니다. 새로고침하세요."}, status_code=403)
            origin = request.headers.get("origin")
            if origin and urlparse(origin).netloc != request.headers.get("host"):
                return JSONResponse({"detail": "허용되지 않은 요청 출처"}, status_code=403)
            if len(await request.body()) > 15 * 1024 * 1024:
                return JSONResponse({"detail": "파일은 최대 15 MiB입니다."}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        if request.url.path.startswith("/api/") or request.url.path == "/":
            response.headers["Cache-Control"] = "no-store"
        if request.url.path == "/":
            response.headers["X-Frame-Options"] = "DENY"
        return response

    def get_snapshot(identifier):
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", identifier):
            raise HTTPException(404, "스냅샷을 찾을 수 없습니다.")
        path = snapshots / f"{identifier}.json"
        if not path.is_file():
            raise HTTPException(404, "스냅샷을 찾을 수 없습니다.")
        return json.loads(path.read_text(encoding="utf-8"))

    def doc_path(identifier, vpc, references):
        key = hashlib.sha256(f"{identifier}|{vpc}|{references}".encode()).hexdigest()
        return documents / f"{key}.json"

    @app.get("/", response_class=HTMLResponse)
    def home():
        return static.joinpath("app.html").read_text(encoding="utf-8")

    @app.get("/api/bootstrap")
    def bootstrap():
        try:
            profiles = boto3.Session().available_profiles
        except BotoCoreError:
            profiles = []
        return {"token": token, "editor_ready": (editor / "js/app.min.js").is_file(), "editor_version": VERSION, "profiles": profiles}

    @app.get("/api/snapshots")
    def list_snapshots():
        result = []
        for path in snapshots.glob("*.json"):
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            result.append({"id": path.stem, "metadata": snapshot["metadata"], "resource_count": len(snapshot["graph"]["nodes"])})
        return sorted(result, key=lambda row: row["metadata"]["finished_at"], reverse=True)

    @app.get("/api/snapshots/{identifier}")
    def read_snapshot(identifier: str):
        return get_snapshot(identifier)

    @app.post("/api/snapshots", status_code=201)
    def import_snapshot(snapshot: dict):
        try:
            return {"id": save_snapshot(snapshot)}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(422, f"스냅샷 형식을 확인하세요: {exc}") from exc

    @app.get("/api/snapshots/{identifier}/document")
    def read_document(identifier: str, vpc: str | None = None, references: bool = False, reset: bool = False):
        snapshot = get_snapshot(identifier)
        path = doc_path(identifier, vpc, references)
        saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if saved and not reset:
            return saved
        try:
            xml = generate_document(snapshot, vpc, references)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"xml": xml, "revision": saved["revision"] if saved else 0, "saved_at": None}

    @app.put("/api/snapshots/{identifier}/document")
    def save_document(identifier: str, document: DocumentRequest, vpc: str | None = None, references: bool = False):
        snapshot = get_snapshot(identifier)
        if vpc and not any(n["kind"] == "vpc" and n["resource_id"] == vpc for n in snapshot["graph"]["nodes"]):
            raise HTTPException(422, "선택한 VPC가 스냅샷에 없습니다.")
        try:
            validate_document(document.xml)
        except Exception as exc:
            raise HTTPException(422, "유효한 압축 해제 draw.io XML이 아닙니다.") from exc
        path = doc_path(identifier, vpc, references)
        with mutex:
            saved = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"revision": 0}
            if saved["revision"] != document.revision:
                raise HTTPException(409, "다른 창에서 편집본을 저장했습니다. 새로고침 후 다시 편집하세요.")
            if saved.get("xml") == document.xml:
                return saved
            saved = {"xml": document.xml, "revision": document.revision+1, "saved_at": now()}
            atomic_json(path, saved)
        return saved

    @app.post("/api/collections", status_code=202)
    def start_collection(params: CollectionRequest):
        with mutex:
            if any(j["status"] in {"queued", "running"} for j in jobs.values()):
                raise HTTPException(409, "현재 수집이 진행 중입니다.")
            identifier = uuid4().hex
            jobs[identifier] = {"id": identifier, "status": "queued", "started_at": now()}

        def work():
            jobs[identifier]["status"] = "running"
            try:
                snapshot = collect(params.region, params.profile, params.expected_account)
                jobs[identifier].update(status=snapshot["metadata"]["status"], snapshot_id=save_snapshot(snapshot))
            except (BotoCoreError, ClientError) as exc:
                code = exc.response["Error"]["Code"] if isinstance(exc, ClientError) else type(exc).__name__
                jobs[identifier].update(status="failed", error=f"{code}: 기존 SSO 로그인·프로파일·권한을 확인하세요.")
            except ValueError as exc:
                jobs[identifier].update(status="failed", error=str(exc))
            except Exception:
                jobs[identifier].update(status="failed", error="수집을 완료하지 못했습니다. 마지막 스냅샷은 보존됩니다.")
            jobs[identifier]["finished_at"] = now()

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
