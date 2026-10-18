"""Team mode: local user accounts, sessions, roles and an append-only audit log.

Passwords are stored as PBKDF2-SHA256 hashes (per-user salt, 600,000 iterations). Sessions are random
server-side tokens in an HttpOnly cookie; they expire and are lost on restart (users sign in again).
"""
import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from .fileio import atomic_write

ROLES = ("viewer", "editor", "admin")
ITERATIONS = 600_000
SESSION_SECONDS = 8 * 3600
# Failed logins in the window: per (client, name), and looser per client. Behind an authenticating proxy every user
# shares one client address, so the per-client cap only stops guessing across many names.
FAILURE_WINDOW, FAILURE_LIMIT, CLIENT_FAILURE_LIMIT = 15 * 60, 5, 50


def hash_password(password, salt=None, iterations=ITERATIONS):
    salt = salt or secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    encode = lambda raw: base64.b64encode(raw).decode()
    return f"pbkdf2_sha256${iterations}${encode(salt)}${encode(digest)}"


def parse_hash(stored):
    """(iterations, salt, digest) of a stored hash, or None when it is not a well-formed pbkdf2_sha256 hash."""
    try:
        scheme, iterations, salt, digest = stored.split("$")
        if scheme == "pbkdf2_sha256" and iterations.isdigit() and int(iterations) > 0:
            return int(iterations), base64.b64decode(salt, validate=True), base64.b64decode(digest, validate=True)
    except (AttributeError, ValueError):  # binascii.Error is a ValueError
        pass
    return None


def verify_password(password, stored):
    parsed = parse_hash(stored)
    if not parsed:
        return False
    iterations, salt, digest = parsed
    return hmac.compare_digest(hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations), digest)


def load_users(path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    users = {u["name"]: u for u in data.get("users", [])}
    for user in users.values():
        if user.get("role") not in ROLES or not parse_hash(user.get("password_hash")):
            raise ValueError(f"사용자 {user.get('name')}: role 또는 password_hash가 잘못되었습니다.")
    if not users:
        raise ValueError("사용자 파일에 사용자가 없습니다. diagram-bot user add로 추가하세요.")
    return users


def save_user(path, name, role, password):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {"users": []}
    data["users"] = [u for u in data["users"] if u["name"] != name] + [{"name": name, "role": role, "password_hash": hash_password(password)}]
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


def remove_user(path, name):
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    remaining = [u for u in data["users"] if u["name"] != name]
    if len(remaining) == len(data["users"]):
        raise ValueError(f"사용자 {name}이(가) 없습니다.")
    data["users"] = remaining
    atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2))


class Authenticator:
    """Users, sessions and login throttling for one server process."""

    def __init__(self, users):
        self.users = users
        self.sessions = {}
        self.failures = {}
        self.lock = threading.Lock()
        # Unknown names still cost one hash, so response time does not reveal which names exist.
        self.dummy = hash_password(secrets.token_urlsafe(16))

    def recent_failures(self, key, now):
        recent = [t for t in self.failures.get(key, []) if now - t < FAILURE_WINDOW]
        if recent:
            self.failures[key] = recent
        else:
            self.failures.pop(key, None)
        return len(recent)

    def throttled(self, client, name):
        now = time.monotonic()
        with self.lock:
            return (self.recent_failures((client, name), now) >= FAILURE_LIMIT
                    or self.recent_failures(client, now) >= CLIENT_FAILURE_LIMIT)

    def login(self, name, password, client):
        user = self.users.get(name)
        ok = verify_password(password, user["password_hash"] if user else self.dummy) and user is not None
        with self.lock:
            if not ok:
                for key in ((client, name), client):
                    self.failures.setdefault(key, []).append(time.monotonic())
                return None
            self.failures.pop((client, name), None)
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
        """Newest first. Reads only the end of the file; a torn line (a crash mid-write) is skipped, not fatal."""
        if not self.path.exists():
            return []
        with self.path.open("rb") as handle:
            position, data = handle.seek(0, os.SEEK_END), b""
            while position and data.count(b"\n") <= limit:
                step = min(position, 64 * 1024)
                position -= step
                handle.seek(position)
                data = handle.read(step) + data
        entries = []
        for line in reversed(data.splitlines()[-limit:]):
            try:
                entry = json.loads(line)
            except ValueError:
                continue
            if isinstance(entry, dict):
                entries.append(entry)
        return entries
