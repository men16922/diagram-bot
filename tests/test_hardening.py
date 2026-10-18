"""Regression tests for server, auth and store robustness (audit items 2.3-2.5, 2.22, 3.7)."""
import asyncio
import json
import stat

import pytest
from fastapi.testclient import TestClient

from diagram_bot.auth import AuditLog, hash_password, load_users, remove_user, save_user, verify_password
from diagram_bot.demo import demo_snapshot
from diagram_bot.server import create_app, read_capped
from diagram_bot.store import SnapshotStore

PASSWORD = "correct horse battery"


@pytest.mark.parametrize("stored", ["pbkdf2_sha256$x$AAAA$AAAA", "pbkdf2_sha256$1000$***$AAAA", "pbkdf2_sha256$0$AAAA$AAAA",
                                    "pbkdf2_sha256$-5$AAAA$AAAA", "pbkdf2_sha256$1000$AAAA", None])
def test_malformed_password_hash_is_a_failed_login_not_an_error(tmp_path, stored):
    assert verify_password("a", stored) is False
    users = tmp_path / "users.json"
    users.write_text(json.dumps({"users": [{"name": "bob", "role": "admin", "password_hash": stored}]}), encoding="utf-8")
    with pytest.raises(ValueError, match="password_hash"):
        load_users(users)
    app = create_app(tmp_path / "data", tmp_path / "no-editor", users={"bob": {"name": "bob", "role": "admin", "password_hash": stored}})
    response = TestClient(app, base_url="https://testserver").post("/api/login", json={"name": "bob", "password": PASSWORD})
    assert response.status_code == 401


def test_audit_tail_skips_a_torn_line_and_reads_only_the_end(tmp_path):
    log = AuditLog(tmp_path / "audit.log")
    for index in range(3000):
        log.write("ada", f"action {index}")
    with log.path.open("a", encoding="utf-8") as handle:
        handle.write('{"time": "2026-10-18T00:00:00", "user": "torn')  # a crash mid-write; the next append continues the line
    log.write("ada", "after the crash")
    log.write("ada", "latest")
    entries = log.tail(5)
    assert [e["action"] for e in entries] == ["latest", "action 2999", "action 2998", "action 2997"]
    assert [e["action"] for e in log.tail(1000)][-1] == "action 2002"


def test_user_file_writes_are_atomic_and_private(tmp_path):
    users = tmp_path / "users.json"
    for name in ("vera", "eddie"):
        save_user(users, name, "viewer", PASSWORD)
    users.chmod(0o644)
    remove_user(users, "vera")
    assert stat.S_IMODE(users.stat().st_mode) == 0o600
    assert [u["name"] for u in json.loads(users.read_text(encoding="utf-8"))["users"]] == ["eddie"]
    assert [p.name for p in tmp_path.iterdir()] == ["users.json"]  # no temporary file left behind


def test_body_limit_rejects_a_declared_length_before_reading(tmp_path):
    app = create_app(tmp_path / "data", tmp_path / "no-editor", users={"bob": {"name": "bob", "role": "admin",
                                                                               "password_hash": hash_password(PASSWORD)}})
    client = TestClient(app, base_url="https://testserver")
    body = json.dumps({"name": "bob", "password": PASSWORD}).encode()
    declared = client.post("/api/login", content=body, headers={"Content-Type": "application/json", "Content-Length": str(64 * 1024 * 1024)})
    assert declared.status_code == 413 and declared.json()["detail"] == "파일은 최대 15 MiB입니다."
    assert client.post("/api/login", json={"name": "bob", "password": PASSWORD}).status_code == 200


def test_body_limit_stops_reading_a_chunked_body(tmp_path):
    sent = []

    class Chunked:  # no Content-Length: the body must be read only up to the limit
        headers = {}

        async def stream(self):
            for _ in range(64):
                sent.append(1)
                yield b" " * (1024 * 1024)

    assert asyncio.run(read_capped(Chunked())) is None and len(sent) == 16
    client = TestClient(create_app(tmp_path / "data", tmp_path / "no-editor"))
    client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
    big = (b" " * (1024 * 1024) for _ in range(16))
    assert client.post("/api/snapshots", content=big, headers={"Content-Type": "application/json"}).status_code == 413
    assert client.post("/api/snapshots", json=demo_snapshot()).status_code == 201  # a normal body still reaches the route


def test_snapshot_list_skips_one_corrupt_file(tmp_path):
    store = SnapshotStore(tmp_path)
    store.save(demo_snapshot(), "good")
    (store.directory / "broken.json").write_text('{"metadata": {"finished_at"', encoding="utf-8")
    (store.directory / "nometa.json").write_text("[]", encoding="utf-8")
    assert [row["id"] for row in store.list()] == ["good"]


def test_snapshot_get_survives_a_concurrent_eviction(tmp_path):
    store = SnapshotStore(tmp_path)
    store.save(demo_snapshot(), "demo")
    store.get("demo")

    class Evicting(dict):
        def __contains__(self, key):  # another request evicts the key right after this check
            found = dict.__contains__(self, key)
            self.pop(key, None)
            return found

    store.cache = Evicting(store.cache)
    assert store.get("demo")["metadata"]["account_id"]


def test_error_code_names_the_aws_code_or_the_exception():
    from botocore.exceptions import ClientError, EndpointConnectionError

    from diagram_bot.errors import error_code
    assert error_code(ClientError({"Error": {"Code": "AccessDenied"}}, "op")) == "AccessDenied"
    assert error_code(ClientError({}, "op")) == "ClientError"
    assert error_code(EndpointConnectionError(endpoint_url="https://x")) == "EndpointConnectionError"


def test_the_server_does_not_import_the_cli_for_its_version():
    import subprocess
    import sys
    code = "import sys, diagram_bot.server; assert 'diagram_bot.cli' not in sys.modules"
    assert subprocess.run([sys.executable, "-c", code], capture_output=True).returncode == 0
