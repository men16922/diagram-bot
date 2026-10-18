import io
import json

import pytest
from fastapi.testclient import TestClient

from diagram_bot import cli
from diagram_bot.auth import CLIENT_FAILURE_LIMIT, SESSION_SECONDS, Authenticator, hash_password, load_users, save_user, verify_password
from diagram_bot.demo import demo_snapshot
from diagram_bot.server import create_app
from test_sso import Clock

PASSWORD = "correct horse battery"


@pytest.fixture
def team(tmp_path):
    users = tmp_path / "users.json"
    for name, role in (("vera", "viewer"), ("eddie", "editor"), ("ada", "admin")):
        save_user(users, name, role, PASSWORD)
    app = create_app(tmp_path / "data", tmp_path / "no-editor", users=load_users(users), allowed_hosts=["diagrams.example.com"], secure=True)
    return app, tmp_path


def signed_in(app, name):
    client = TestClient(app, base_url="https://testserver")
    response = client.post("/api/login", json={"name": name, "password": PASSWORD})
    assert response.status_code == 200, response.text
    client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
    return client, response


def test_password_hashes_are_salted_and_verified():
    first, second = hash_password("secret-value-1"), hash_password("secret-value-1")
    assert first != second and first.startswith("pbkdf2_sha256$600000$")
    assert verify_password("secret-value-1", first) and not verify_password("secret-value-2", first)
    assert not verify_password("x", "md5$1$2$3")


def test_everything_requires_a_session(team):
    app, _ = team
    client = TestClient(app, base_url="https://testserver", follow_redirects=False)
    assert client.get("/").headers["location"] == "/login"
    assert client.get("/static/app.js").status_code == 303
    assert client.get("/api/snapshots").status_code == 401
    assert client.get("/login").status_code == 200
    assert client.get("/", headers={"Host": "evil.example"}).status_code == 400
    assert TestClient(app, base_url="https://diagrams.example.com").get("/login").status_code == 200


def test_login_cookie_is_locked_down_and_failures_are_throttled(team):
    app, _ = team
    _, response = signed_in(app, "vera")
    cookie = response.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=strict" in cookie
    assert response.headers["strict-transport-security"].startswith("max-age=")
    client = TestClient(app, base_url="https://testserver")
    for _ in range(5):
        assert client.post("/api/login", json={"name": "vera", "password": "wrong password"}).status_code == 401
    assert client.post("/api/login", json={"name": "vera", "password": PASSWORD}).status_code == 429
    # Behind an authenticating proxy every user comes from one address: Vera's lockout is not Eddie's.
    assert client.post("/api/login", json={"name": "eddie", "password": PASSWORD}).status_code == 200
    assert client.post("/api/login", json={"name": "ghost", "password": PASSWORD}, headers={"Origin": "https://evil.example"}).status_code == 403


def test_guessing_across_many_names_hits_the_looser_per_client_cap():
    auth = Authenticator({"vera": {"name": "vera", "role": "viewer", "password_hash": hash_password(PASSWORD)}})
    auth.dummy = hash_password("x", iterations=1)  # unknown names cost one hash; keep the test fast
    for i in range(CLIENT_FAILURE_LIMIT - 1):
        assert not auth.throttled("10.0.0.1", f"guess{i}") and auth.login(f"guess{i}", "wrong", "10.0.0.1") is None
    assert not auth.throttled("10.0.0.1", "vera") and not auth.throttled("10.0.0.2", "vera")
    assert auth.login("nobody", "wrong", "10.0.0.1") is None
    assert auth.throttled("10.0.0.1", "vera") and not auth.throttled("10.0.0.2", "vera")


def test_roles_limit_what_each_user_can_change(team):
    app, _ = team
    viewer, _ = signed_in(app, "vera")
    assert viewer.get("/api/bootstrap").json()["user"] == {"name": "vera", "role": "viewer"}
    assert viewer.get("/api/snapshots/demo/findings").status_code == 200
    document = viewer.get("/api/snapshots/demo/document?view=overview").json()
    assert viewer.put("/api/snapshots/demo/document?view=overview", json={"xml": document["xml"], "revision": 0}).status_code == 403
    assert viewer.post("/api/snapshots", json=demo_snapshot()).status_code == 403
    assert viewer.get("/api/audit").status_code == 403
    editor, _ = signed_in(app, "eddie")
    assert editor.put("/api/snapshots/demo/document?view=overview", json={"xml": document["xml"], "revision": 0}).status_code == 200
    assert editor.post("/api/snapshots", json=demo_snapshot()).status_code == 201
    collection = {"region": "ap-northeast-2", "expected_account": "111122223333"}
    assert editor.post("/api/collections", json=collection).status_code == 403
    admin, _ = signed_in(app, "ada")
    assert admin.post("/api/collections", json={**collection, "region": "bad"}).status_code == 422  # authorised, then validated
    assert editor.post("/api/logout").status_code == 200
    assert editor.get("/api/snapshots").status_code == 401


def test_audit_log_records_who_did_what(team):
    app, tmp_path = team
    TestClient(app, base_url="https://testserver").post("/api/login", json={"name": "eddie", "password": "nope nope nope"})
    editor, _ = signed_in(app, "eddie")
    editor.post("/api/snapshots", json=demo_snapshot())
    viewer, _ = signed_in(app, "vera")
    viewer.post("/api/snapshots", json=demo_snapshot())
    admin, _ = signed_in(app, "ada")
    entries = admin.get("/api/audit").json()
    seen = [(e["user"], e["action"], e["result"]) for e in reversed(entries)]
    assert ("eddie", "login", "failed") in seen and ("eddie", "login", "ok") in seen
    assert ("eddie", "POST /api/snapshots", 201) in seen and ("vera", "POST /api/snapshots", "denied") in seen
    lines = (tmp_path / "data" / "audit.log").read_text(encoding="utf-8").splitlines()
    assert all(json.loads(line)["time"] for line in lines) and "correct horse" not in "\n".join(lines)


def test_local_mode_is_unchanged_and_audited(tmp_path):
    client = TestClient(create_app(tmp_path / "data", tmp_path / "no-editor"))
    boot = client.get("/api/bootstrap").json()
    assert boot["team"] is False and boot["user"] == {"name": "local", "role": "admin"}
    client.headers["X-Diagram-Bot"] = boot["token"]
    assert client.post("/api/snapshots", json=demo_snapshot()).status_code == 201
    assert client.get("/api/audit").json()[0]["user"] == "local"
    assert client.get("/login", follow_redirects=False).status_code == 303


def test_cli_refuses_unauthenticated_network_exposure_and_manages_users(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["diagram-bot", "serve", "--host", "0.0.0.0"])
    with pytest.raises(SystemExit):
        cli.main()
    assert "인증 없는 네트워크 노출 금지" in capsys.readouterr().err
    users = tmp_path / "users.json"
    monkeypatch.setattr("sys.stdin", io.StringIO(PASSWORD + "\n"))
    monkeypatch.setattr("sys.argv", ["diagram-bot", "user", "add", "kim", "--role", "editor", "--file", str(users), "--password-stdin"])
    assert cli.main() == 0
    assert load_users(users)["kim"]["role"] == "editor" and (users.stat().st_mode & 0o077) == 0
    monkeypatch.setattr("sys.stdin", io.StringIO("short\n"))
    monkeypatch.setattr("sys.argv", ["diagram-bot", "user", "add", "lee", "--file", str(users), "--password-stdin"])
    with pytest.raises(SystemExit):
        cli.main()
    users.write_text(json.dumps({"users": [{"name": "x", "role": "root", "password_hash": "pbkdf2_sha256$1$a$b"}]}))
    with pytest.raises(ValueError):
        load_users(users)


def test_expired_sessions_and_old_failures_leave_memory():
    clock = Clock()
    auth = Authenticator({"vera": {"name": "vera", "role": "viewer", "password_hash": hash_password(PASSWORD, iterations=1)}}, clock=clock)
    auth.dummy = hash_password("x", iterations=1)
    old = auth.login("vera", PASSWORD, "10.0.0.1")
    for i in range(3):
        auth.login(f"guess{i}", "wrong", f"10.0.0.{i}")
    assert len(auth.sessions) == 1 and len(auth.failures) == 6
    clock.now += SESSION_SECONDS + 1
    fresh = auth.start_session("vera", "viewer")
    assert auth.session(fresh) and not auth.session(old)
    assert not auth.throttled("10.9.9.9", "vera")
    assert list(auth.sessions) == [fresh] and auth.failures == {}  # dropped on access, without a timer
