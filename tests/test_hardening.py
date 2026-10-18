"""Regression tests for server, auth and store robustness (audit items 2.3-2.5, 2.22, 3.7)."""
import json
import stat

import pytest
from fastapi.testclient import TestClient

from diagram_bot.auth import AuditLog, load_users, remove_user, save_user, verify_password
from diagram_bot.demo import demo_snapshot
from diagram_bot.server import create_app
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
