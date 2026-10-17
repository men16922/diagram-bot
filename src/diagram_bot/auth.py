"""Team mode: local user accounts, sessions, roles and an append-only audit log.

Passwords are stored as PBKDF2-SHA256 hashes (per-user salt, 600,000 iterations). Sessions are random
server-side tokens in an HttpOnly cookie; they expire and are lost on restart (users sign in again).
"""
import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

ROLES = ("viewer", "editor", "admin")
ITERATIONS = 600_000
SESSION_SECONDS = 8 * 3600
FAILURE_WINDOW, FAILURE_LIMIT = 15 * 60, 5


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    encode = lambda raw: base64.b64encode(raw).decode()
    return f"pbkdf2_sha256${iterations}${encode(salt)}${encode(digest)}"


def verify_password(password, stored):
    try:
        scheme, iterations, salt, digest = stored.split("$")
    except (AttributeError, ValueError):
        return False
    if scheme != "pbkdf2_sha256":
        return False
    candidate = hashlib.pbkdf2_hmac("sha256", password.encode(), base64.b64decode(salt), int(iterations))
    return hmac.compare_digest(candidate, base64.b64decode(digest))


def load_users(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    users = {u["name"]: u for u in data.get("users", [])}
    for user in users.values():
        if user.get("role") not in ROLES or not str(user.get("password_hash", "")).startswith("pbkdf2_sha256$"):
            raise ValueError(f"사용자 {user.get('name')}: role 또는 password_hash가 잘못되었습니다.")
    if not users:
        raise ValueError("사용자 파일에 사용자가 없습니다. diagram-bot user add로 추가하세요.")
    return users


def save_user(path, name, role, password):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"users": []}
    data["users"] = [u for u in data["users"] if u["name"] != name] + [{"name": name, "role": role, "password_hash": hash_password(password)}]
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(path)


def remove_user(path, name):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    remaining = [u for u in data["users"] if u["name"] != name]
    if len(remaining) == len(data["users"]):
        raise ValueError(f"사용자 {name}이(가) 없습니다.")
    data["users"] = remaining
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


class Authenticator:
    """Users, sessions and login throttling for one server process."""

    def __init__(self, users):
        self.users = users
        self.sessions = {}
        self.failures = {}
        self.lock = threading.Lock()
        # Unknown names still cost one hash, so response time does not reveal which names exist.
        self.dummy = hash_password(secrets.token_urlsafe(16))

    def throttled(self, client):
        now = time.monotonic()
        with self.lock:
            recent = [t for t in self.failures.get(client, []) if now - t < FAILURE_WINDOW]
            self.failures[client] = recent
            return len(recent) >= FAILURE_LIMIT

    def login(self, name, password, client):
        user = self.users.get(name)
        ok = verify_password(password, user["password_hash"] if user else self.dummy) and user is not None
        with self.lock:
            if not ok:
                self.failures.setdefault(client, []).append(time.monotonic())
                return None
            self.failures.pop(client, None)
            token = secrets.token_urlsafe(32)
            self.sessions[token] = {"name": name, "role": user["role"], "expires": time.monotonic() + SESSION_SECONDS}
            return token

    def start_session(self, name, role):
        """Session for an identity verified elsewhere (OpenID Connect)."""
        token = secrets.token_urlsafe(32)
        with self.lock:
            self.sessions[token] = {"name": name, "role": role, "expires": time.monotonic() + SESSION_SECONDS}
        return token

    def session(self, token):
        with self.lock:
            entry = self.sessions.get(token or "")
            if entry and entry["expires"] > time.monotonic():
                return entry
            self.sessions.pop(token or "", None)
            return None

    def logout(self, token):
        with self.lock:
            self.sessions.pop(token or "", None)


def allowed(role, method, path):
    """viewer: read; editor: edit documents and import snapshots; admin: also start AWS collection."""
    if method in ("GET", "HEAD") or path in ("/api/logout",):
        return True
    if path.startswith(("/api/collections", "/api/sso")):
        return role == "admin"
    return role in ("editor", "admin")


class AuditLog:
    """Append-only JSON Lines: who did what to which snapshot or document, from where, with what result."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock = threading.Lock()

    def write(self, user, action, target=None, result="ok", client=None, **extra):
        entry = {"time": datetime.now(UTC).isoformat(), "user": user, "action": action, "target": target, "result": result,
                 "client": client, **extra}
        with self.lock, self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")

    def tail(self, limit=200):
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()[-limit:]
        return [json.loads(line) for line in reversed(lines)]
