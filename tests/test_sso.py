import json
import time

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient

from diagram_bot.server import create_app
from diagram_bot.sso import FLOW_GRACE, GRANT, SsoError, SsoLogins


def error(code):
    return ClientError({"Error": {"Code": code, "Message": code}}, "op")


class Pager:
    def __init__(self, pages):
        self.pages = pages

    def paginate(self, **kwargs):
        return iter(self.pages(kwargs))


class FakeIdentityCenter:
    """sso-oidc + sso: the device grant answers pending until approve() is called."""

    def __init__(self):
        self.approved, self.calls, self.logged_out = False, [], []

    def __call__(self, service, region):
        assert region == "us-east-1"
        return self

    def register_client(self, **kwargs):
        assert kwargs["clientType"] == "public" and kwargs["scopes"] == ["sso:account:access"]
        return {"clientId": "client", "clientSecret": "client-secret"}

    def start_device_authorization(self, **kwargs):
        assert kwargs["startUrl"] == "https://d-1234567890.awsapps.com/start"
        return {"deviceCode": "device-secret", "userCode": "ABCD-EFGH", "verificationUri": "https://device.sso.us-east-1.amazonaws.com/",
                "verificationUriComplete": "https://device.sso.us-east-1.amazonaws.com/?user_code=ABCD-EFGH", "expiresIn": 600, "interval": 1}

    def create_token(self, **kwargs):
        self.calls.append("create_token")
        assert kwargs["grantType"] == GRANT and kwargs["deviceCode"] == "device-secret"
        if not self.approved:
            raise error("AuthorizationPendingException")
        return {"accessToken": "access-secret", "expiresIn": 3600}

    def get_paginator(self, name):
        if name == "list_accounts":
            return Pager(lambda kw: [{"accountList": [{"accountId": "111122223333", "accountName": "prod"}]}])
        return Pager(lambda kw: [{"roleList": [{"roleName": "CloudOpsRunbookOperator"}, {"roleName": "CloudOpsReadOnly"}]}])

    def get_role_credentials(self, **kwargs):
        assert kwargs == {"accessToken": "access-secret", "accountId": "111122223333", "roleName": "CloudOpsReadOnly"}
        return {"roleCredentials": {"accessKeyId": "ASIAEXAMPLE", "secretAccessKey": "s" * 40, "sessionToken": "token"}}

    def logout(self, accessToken):
        self.logged_out.append(accessToken)


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_device_grant_flow_keeps_secrets_server_side():
    fake, clock = FakeIdentityCenter(), Clock()
    logins = SsoLogins(client=fake, clock=clock)
    started = logins.start("https://d-1234567890.awsapps.com/start", "us-east-1", "alice")
    assert started["user_code"] == "ABCD-EFGH" and "user_code=ABCD-EFGH" in started["verification_uri_complete"]
    assert "secret" not in json.dumps(started)
    assert logins.poll(started["id"], "alice")["status"] == "pending"
    assert logins.poll(started["id"], "alice")["status"] == "pending" and fake.calls == ["create_token"]  # waits one interval
    with pytest.raises(SsoError):
        logins.poll(started["id"], "mallory")  # bound to the user who started it
    fake.approved, clock.now = True, clock.now + 1
    view = logins.poll(started["id"], "alice")
    assert view["status"] == "authorized" and view["accounts"] == [{"id": "111122223333", "name": "prod"}]
    assert "secret" not in json.dumps(view)
    assert logins.roles(started["id"], "alice", "111122223333") == ["CloudOpsReadOnly", "CloudOpsRunbookOperator"]
    session = logins.session(started["id"], "alice", "111122223333", "CloudOpsReadOnly", "ap-northeast-2")
    assert session.region_name == "ap-northeast-2" and session.get_credentials().access_key == "ASIAEXAMPLE"
    clock.now += 3600
    assert logins.poll(started["id"], "alice")["status"] == "expired"
    with pytest.raises(SsoError, match="만료"):
        logins.session(started["id"], "alice", "111122223333", "CloudOpsReadOnly", "us-east-1")


@pytest.mark.parametrize("code, status", [("ExpiredTokenException", "expired"), ("AccessDeniedException", "denied"),
                                          ("InvalidGrantException", "expired"), ("InternalServerException", "failed")])
def test_device_grant_outcomes(code, status):
    fake = FakeIdentityCenter()
    fake.create_token = lambda **kw: (_ for _ in ()).throw(error(code))
    logins = SsoLogins(client=fake, clock=Clock())
    started = logins.start("https://d-1234567890.awsapps.com/start", "us-east-1", "alice")
    assert logins.poll(started["id"], "alice")["status"] == status


def test_slow_down_and_code_expiry():
    fake, clock = FakeIdentityCenter(), Clock()
    fake.create_token = lambda **kw: (_ for _ in ()).throw(error("SlowDownException"))
    logins = SsoLogins(client=fake, clock=clock)
    started = logins.start("https://d-1234567890.awsapps.com/start", "us-east-1", "alice")
    assert logins.poll(started["id"], "alice")["interval"] == 6
    clock.now += 601
    assert logins.poll(started["id"], "alice")["status"] == "expired"


@pytest.mark.parametrize("url, region", [("http://d-1.awsapps.com/start", "us-east-1"), ("javascript:alert(1)", "us-east-1"),
                                         ("https://d-1234567890.awsapps.com/start", "mars-1")])
def test_start_validation(url, region):
    with pytest.raises(SsoError):
        SsoLogins(client=FakeIdentityCenter()).start(url, region, "alice")


@pytest.mark.parametrize("egress", ["server", "browser"])
def test_sso_collection_through_the_api(tmp_path, monkeypatch, egress):
    """Identity Center sign-in and GetRoleCredentials stay on the server; with browser egress the role's session is relayed."""
    fake = FakeIdentityCenter()
    collected = {}

    def fake_collect(region, profile=None, expected_account=None, session=None, role_arn=None, external_id=None, prepare=None):
        collected.update(region=region, profile=profile, account=expected_account, key=session.get_credentials().access_key)
        if prepare:
            # The role's session is relayed: with no browser connected its first AWS call fails fast instead of leaving the server.
            from diagram_bot.egress import RelayError
            with pytest.raises(RelayError):
                prepare(session).client("sts", region_name="us-east-1").get_caller_identity()
        from diagram_bot.demo import demo_snapshot
        return demo_snapshot()
    monkeypatch.setattr("diagram_bot.routes.aws.collect", fake_collect)
    logins = SsoLogins(client=fake)
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor", sso=logins, egress=egress,
                               sso_start=("https://d-1234567890.awsapps.com/start", "us-east-1"))) as client:
        client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
        assert client.get("/api/sso/defaults").json()[0] == {"name": "회사 Identity Center", "start_url": "https://d-1234567890.awsapps.com/start",
                                                             "region": "us-east-1"}
        assert client.post("/api/sso/login", json={"start_url": "ftp://x/start", "region": "us-east-1"}).status_code == 422
        started = client.post("/api/sso/login", json={"start_url": "https://d-1234567890.awsapps.com/start", "region": "us-east-1"}).json()
        assert started["user_code"] == "ABCD-EFGH"
        assert client.get(f"/api/sso/login/{started['id']}").json()["status"] == "pending"
        fake.approved = True
        logins.flows[started["id"]]["next_poll"] = 0
        view = client.get(f"/api/sso/login/{started['id']}").json()
        assert view["status"] == "authorized" and view["accounts"][0]["id"] == "111122223333"
        assert client.get(f"/api/sso/login/{started['id']}/roles", params={"account_id": "111122223333"}).json()["roles"][0] == "CloudOpsReadOnly"
        job = client.post("/api/collections", json={"region": "ap-northeast-2", "expected_account": "111122223333",
                                                    "sso_login": started["id"], "sso_role": "CloudOpsReadOnly", "profile": "ignored"}).json()
        for _ in range(200):
            job = client.get(f"/api/collections/{job['id']}").json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.01)
        assert job["status"] == "partial" and job.get("snapshot_id"), job
        assert collected == {"region": "ap-northeast-2", "profile": None, "account": "111122223333", "key": "ASIAEXAMPLE"}
        # "이 역할로 로그인": the chat agent reads with this account and role from now on.
        use = {"account_id": "111122223333", "role": "CloudOpsReadOnly"}
        assert client.post("/api/sso/login/unknown/use", json=use).status_code == 422
        assert client.post(f"/api/sso/login/{started['id']}/use", json=use).json() == {"account": "111122223333", "role": "CloudOpsReadOnly"}
        assert client.post(f"/api/sso/login/{started['id']}/logout").json() == {"logged_out": True}
        assert fake.logged_out == ["access-secret"]
        log = (tmp_path / "data" / "audit.log").read_text()
        assert "sso login started" in log and "sso login authorized" in log and "sso role selected" in log
        assert ('"via": "sso:CloudOpsReadOnly/browser"' if egress == "browser" else '"via": "sso:CloudOpsReadOnly"') in log
        assert "access-secret" not in log and "client-secret" not in log


def test_only_admins_start_sso_sign_in():
    from diagram_bot.auth import allowed
    assert allowed("admin", "POST", "/api/sso/login")
    assert not allowed("editor", "POST", "/api/sso/login") and not allowed("viewer", "POST", "/api/sso/login/x/logout")


def test_flows_past_their_expiry_leave_memory():
    fake, clock = FakeIdentityCenter(), Clock()
    logins = SsoLogins(client=fake, clock=clock)
    abandoned = logins.start("https://d-1234567890.awsapps.com/start", "us-east-1", "alice")["id"]
    fake.approved = True
    signed_in = logins.start("https://d-1234567890.awsapps.com/start", "us-east-1", "bob")["id"]
    assert logins.poll(signed_in, "bob")["status"] == "authorized"
    clock.now += 600 + FLOW_GRACE - 1
    assert logins.poll(abandoned, "alice")["status"] == "expired"  # a late poll still learns what happened
    clock.now += 61  # purged at most once a minute
    assert logins.poll(signed_in, "bob")["status"] == "expired"  # the token's 3600 s are over; the flow stays its grace
    assert abandoned not in logins.flows and signed_in in logins.flows
    clock.now += 3600
    logins.start("https://d-1234567890.awsapps.com/start", "us-east-1", "carol")
    assert signed_in not in logins.flows and len(logins.flows) == 1
