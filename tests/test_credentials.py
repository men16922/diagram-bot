"""Credential lifetime and lock scope for Identity Center and the chat (audit items 2.6-2.8)."""
import json
import threading
import time

from fastapi.testclient import TestClient
from test_sso import Clock, FakeIdentityCenter

from diagram_bot.aws_access import AwsAccess
from diagram_bot.egress import BrowserRelay
from diagram_bot.server import create_app
from diagram_bot.sso import SsoError, SsoLogins

START = "https://d-1234567890.awsapps.com/start"


def signed_in(fake, owner="alice"):
    logins = SsoLogins(client=fake)
    started = logins.start(START, "us-east-1", owner)
    fake.approved = True
    assert logins.poll(started["id"], owner)["status"] == "authorized"
    return logins, started["id"]


class Expiring(FakeIdentityCenter):
    """Role credentials that expire `lifetime` seconds after they are issued; each issue gets a new key."""

    def __init__(self, lifetime):
        super().__init__()
        self.lifetime, self.issued = lifetime, []

    def get_role_credentials(self, **kwargs):
        self.issued.append(kwargs["accessToken"])
        return {"roleCredentials": {"accessKeyId": f"ASIA{len(self.issued)}", "secretAccessKey": "s" * 40, "sessionToken": "token",
                                    "expiration": int((time.time() + self.lifetime) * 1000)}}


def test_sso_poll_calls_identity_center_outside_the_lock():
    fake, held = FakeIdentityCenter(), []
    logins = SsoLogins(client=fake, clock=Clock())
    create_token, paginator = fake.create_token, fake.get_paginator

    def free():
        held.append(not logins.lock.acquire(blocking=False))
        if not held[-1]:
            logins.lock.release()

    def token(**kwargs):
        free()
        return create_token(**kwargs)

    def pages(name):
        free()
        return paginator(name)
    fake.create_token, fake.get_paginator = token, pages
    started = logins.start(START, "us-east-1", "alice")
    fake.approved = True
    assert logins.poll(started["id"], "alice")["accounts"] == [{"id": "111122223333", "name": "prod"}]
    assert held == [False, False]  # CreateToken and ListAccounts ran without the process-wide lock


def test_a_concurrent_poll_does_not_repeat_or_undo_the_exchange():
    fake, clock = FakeIdentityCenter(), Clock()
    logins = SsoLogins(client=fake, clock=clock)
    started = logins.start(START, "us-east-1", "alice")
    inner = []
    create_token = fake.create_token

    def token(**kwargs):
        # Another tab polls while CreateToken is in flight: it answers at once and does not call CreateToken again.
        other = threading.Thread(target=lambda: inner.append(logins.poll(started["id"], "alice")["status"]), daemon=True)
        other.start()
        other.join(2)
        return create_token(**kwargs)
    fake.create_token, fake.approved = token, True
    assert logins.poll(started["id"], "alice")["status"] == "authorized"
    assert inner == ["pending"] and fake.calls == ["create_token"]


def test_chat_model_credentials_are_fetched_on_use_and_refreshed_before_expiry():
    fake = Expiring(lifetime=60)  # inside botocore's refresh window: every use fetches again
    logins, login = signed_in(fake)
    access = AwsAccess("server", sso=logins)
    access.choose("alice", login, "111122223333", "CloudOpsReadOnly")
    session = access.model_session("alice", "us-east-1")
    assert fake.issued == []  # building the chat's session calls no AWS
    first = session.get_credentials().get_frozen_credentials().access_key
    second = session.get_credentials().get_frozen_credentials().access_key
    assert (first, second) == ("ASIA1", "ASIA2")
    # A new sign-in is picked up at the next refresh; a forgotten one is an SsoError the chat turns into a 409.
    access.forget("alice")
    try:
        session.get_credentials().get_frozen_credentials()
        raise AssertionError("expected SsoError")
    except SsoError as exc:
        assert "다시 로그인" in str(exc)


def test_long_lived_credentials_are_reused():
    fake = Expiring(lifetime=3600)
    logins, login = signed_in(fake)
    access = AwsAccess("server", sso=logins)
    access.choose("alice", login, "111122223333", "CloudOpsReadOnly")
    session = access.model_session("alice", "us-east-1")
    keys = {session.get_credentials().get_frozen_credentials().access_key for _ in range(3)}
    assert keys == {"ASIA1"} and len(fake.issued) == 1


class ExpiredChat:
    def __init__(self, workspace, model):
        self.workspace, self.model = workspace, model

    def ask(self, message, on_event=None):
        raise SsoError("SSO 로그인이 완료되지 않았거나 만료됐습니다. 다시 로그인하세요.")


def test_an_expired_sso_login_is_a_409_on_both_chat_endpoints(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor", chat_factory=ExpiredChat)) as client:
        client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
        response = client.post("/api/agent/chat", json={"message": "hi"})
        assert response.status_code == 409 and "다시 로그인" in response.json()["detail"]
        events = [json.loads(line) for line in client.post("/api/agent/chat/stream", json={"message": "hi"}).text.splitlines()]
        assert events == [{"type": "error", "detail": "SSO 로그인이 완료되지 않았거나 만료됐습니다. 다시 로그인하세요."}]


def test_chat_creation_with_browser_egress_makes_no_identity_center_call(tmp_path):
    fake = Expiring(lifetime=3600)
    logins, login = signed_in(fake, "local")  # the single local user
    built = []

    def factory(workspace, model):
        if workspace.model_session:
            built.append(workspace.model_session())
        raise SsoError("SSO 로그인이 완료되지 않았거나 만료됐습니다. 다시 로그인하세요.")

    app = create_app(tmp_path / "data", tmp_path / "no-editor", sso=logins, egress="browser", relay=BrowserRelay(), chat_factory=factory)
    with TestClient(app) as client:
        client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
        assert client.post(f"/api/sso/login/{login}/use", json={"account_id": "111122223333", "role": "CloudOpsReadOnly"}).status_code == 200
        for path in ("/api/agent/chat", "/api/agent/chat/stream"):
            response = client.post(path, json={"message": "hi"})
            assert response.status_code == 409 and "다시 로그인" in response.json()["detail"]
    assert len(built) == 2 and fake.issued == []


def test_a_failed_start_url_discovery_is_tried_again(monkeypatch):
    found = iter([None, {"start_url": START, "region": "us-east-1"}])
    monkeypatch.setattr("diagram_bot.aws_access.discover_start_url", lambda: next(found))
    access = AwsAccess("server", sso=SsoLogins(client=FakeIdentityCenter()))
    assert access.start_urls() == []
    assert access.start_urls() == [{"name": "Identity Center", "start_url": START, "region": "us-east-1"}]
    assert access.start_urls() == [{"name": "Identity Center", "start_url": START, "region": "us-east-1"}]  # a hit is kept
