"""HTTP API as FastAPI routers, one module per domain, over a shared Context.

server.create_app() composes them with the middleware, the page and the editor. Another FastAPI application can mount
the routers it needs (`app.include_router(snapshots.router(context))`) with its own authentication middleware, as long
as that middleware sets request.state.user (and request.state.role in team mode) and install_error_handlers(app) runs.
"""
import threading
from dataclasses import dataclass, field

from fastapi import Request
from fastapi.responses import JSONResponse

from ..auth import AuditLog
from ..aws_access import AwsAccess
from ..store import Conflict, DocumentStore, NotFound, SnapshotStore


@dataclass
class Context:
    snapshots: SnapshotStore
    documents: DocumentStore
    aws: AwsAccess
    audit: AuditLog
    auth: object = None  # Authenticator in team mode, else None (single local user)
    oidc: object = None
    secure: bool = False
    token: str = ""  # per-process page token every mutation must carry (X-Diagram-Bot)
    chat_factory: object = None
    chats: dict = field(default_factory=dict)  # user -> {"chat", "model"}
    mutex: threading.Lock = field(default_factory=threading.Lock)


def user(request: Request):
    return request.state.user


def role(request: Request):
    return getattr(request.state, "role", "admin")


def install_error_handlers(app):
    """Store errors become HTTP errors with the message the user sees."""
    @app.exception_handler(NotFound)
    async def not_found(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=409)
