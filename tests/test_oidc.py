import base64
import json
import time
from urllib.parse import parse_qs, urlparse

import pytest
from fastapi.testclient import TestClient

from diagram_bot.oidc import OidcProvider, challenge, load_config
from diagram_bot.server import create_app

ISSUER = "https://idp.example.com"
CONFIG = {"issuer": ISSUER, "client_id": "diagram-bot", "client_secret": "s3cret", "redirect_uri": "https://testserver/auth/oidc/callback",
          "scope": "openid email profile groups", "role_claim": "groups",
          "role_map": {"cloud-architects": "admin", "platform-team": "editor", "auditors": "viewer"}}


def jwt(claims):
    encode = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).rstrip(b"=").decode()
    return f"{encode({'alg': 'RS256'})}.{encode(claims)}.signature"


class FakeIdP:
    """Discovery, an authorize step, a PKCE-checking token endpoint and userinfo."""

    def __init__(self, issuer=ISSUER):
        self.issuer, self.codes, self.tamper = issuer, {}, {}

    def authorize(self, url, user):
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}
        assert query["code_challenge_method"] == "S256" and query["response_type"] == "code"
        code = f"code-{len(self.codes)}"
        self.codes[code] = {**query, "user": user}
        return code, query["state"]

    def __call__(self, method, url, data=None, headers=None):
        if url.endswith("/.well-known/openid-configuration"):
            return {"issuer": self.issuer, "authorization_endpoint": ISSUER + "/authorize", "token_endpoint": ISSUER + "/token",
                    "userinfo_endpoint": ISSUER + "/userinfo"}
        if url.endswith("/token"):
            grant = self.codes.pop(data["code"])
            assert challenge(data["code_verifier"]) == grant["code_challenge"], "PKCE verifier mismatch"
            assert data["client_secret"] == "s3cret" and data["redirect_uri"] == CONFIG["redirect_uri"]
            claims = {"iss": ISSUER, "aud": "diagram-bot", "sub": grant["user"]["sub"], "nonce": grant["nonce"], "exp": time.time() + 300,
                      **self.tamper}
            self.last = grant["user"]
            return {"access_token": "at-" + grant["user"]["sub"], "id_token": jwt(claims)}
        if url.endswith("/userinfo"):
            assert headers["Authorization"] == "Bearer at-" + self.last["sub"]
            return self.last
        raise AssertionError(url)


@pytest.fixture
def sso(tmp_path):
    idp = FakeIdP()
    app = create_app(tmp_path / "data", tmp_path / "no-editor", secure=True, oidc=OidcProvider(dict(CONFIG), http=idp))
    return TestClient(app, base_url="https://testserver", follow_redirects=False), idp


def sign_in(client, idp, user):
    start = client.get("/auth/oidc/start")
    assert start.status_code == 303 and start.headers["location"].startswith(ISSUER + "/authorize?")
    assert "samesite=lax" in start.headers["set-cookie"].lower() and "httponly" in start.headers["set-cookie"].lower()
    code, state = idp.authorize(start.headers["location"], user)
    return client.get(f"/auth/oidc/callback?code={code}&state={state}")


def test_sso_sign_in_maps_groups_to_the_highest_role(sso):
    client, idp = sso
    page = client.get("/login").text
    assert "/auth/oidc/start" in page and '<div hidden>' in page  # no local accounts: password form hidden
    result = sign_in(client, idp, {"sub": "u-1", "email": "kim@corp.example", "groups": ["auditors", "cloud-architects"]})
    assert result.status_code == 200 and "samesite=strict" in result.headers["set-cookie"].lower()
    boot = client.get("/api/bootstrap").json()
    assert boot["user"] == {"name": "kim@corp.example", "role": "admin"}
    entry = client.get("/api/audit").json()[0]
    assert (entry["user"], entry["action"], entry["result"], entry["method"]) == ("kim@corp.example", "login", "ok", "oidc")


def test_sso_without_a_mapped_group_is_refused(sso):
    client, idp = sso
    result = sign_in(client, idp, {"sub": "u-2", "email": "guest@corp.example", "groups": ["sales"]})
    assert result.status_code == 403 and "역할이 없는 계정" in result.text
    assert client.get("/api/snapshots").status_code == 401


@pytest.mark.parametrize("tamper,message", [({"nonce": "replayed"}, "nonce"), ({"aud": "other-app"}, "aud"),
                                            ({"iss": "https://evil.example"}, "발급자"), ({"exp": 1}, "만료")])
def test_tampered_id_tokens_are_rejected(sso, tamper, message):
    client, idp = sso
    idp.tamper = tamper
    result = sign_in(client, idp, {"sub": "u-3", "email": "x@corp.example", "groups": ["platform-team"]})
    assert result.status_code == 403 and message in result.text


def test_state_must_come_back_in_the_same_browser(sso):
    client, idp = sso
    start = client.get("/auth/oidc/start")
    code, state = idp.authorize(start.headers["location"], {"sub": "u-4", "groups": ["platform-team"]})
    other = TestClient(client.app, base_url="https://testserver", follow_redirects=False)
    assert other.get(f"/auth/oidc/callback?code={code}&state={state}").status_code == 403  # no state cookie: login CSRF
    assert client.get("/auth/oidc/callback?error=access_denied").status_code == 403


def test_discovery_issuer_mismatch_and_bad_configuration(tmp_path, monkeypatch):
    app = create_app(tmp_path / "d", tmp_path / "e", secure=True, oidc=OidcProvider(dict(CONFIG), http=FakeIdP("https://other.example")))
    assert TestClient(app, base_url="https://testserver").get("/auth/oidc/start").status_code == 502
    path = tmp_path / "oidc.json"
    for broken in ({**CONFIG, "issuer": "http://idp.example.com"}, {**CONFIG, "role_map": {"g": "root"}}, {k: v for k, v in CONFIG.items() if k != "client_id"}):
        path.write_text(json.dumps(broken))
        with pytest.raises(ValueError):
            load_config(path)
    path.write_text(json.dumps({**CONFIG, "client_secret": None, "client_secret_env": "OIDC_SECRET_TEST"}))
    monkeypatch.setenv("OIDC_SECRET_TEST", "from-env")
    assert load_config(path)["client_secret"] == "from-env"
