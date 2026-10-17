"""OpenID Connect sign-in for team mode: authorization code flow with PKCE, state and nonce.

Tokens come straight from the provider's token endpoint over TLS, so the ID token is checked for
issuer, audience, nonce and expiry without verifying its signature (OpenID Connect Core 3.1.3.7);
user claims are read from the userinfo endpoint. Roles come from a claim (e.g. groups) mapped in the
configuration; a user without a mapped role is refused unless a default role is configured.
"""
import base64
import hashlib
import json
import secrets
import threading
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

ROLE_ORDER = {"viewer": 0, "editor": 1, "admin": 2}
PENDING_SECONDS = 600


def http_json(method, url, data=None, headers=None):
    body = urlencode(data).encode() if data else None
    request = Request(url, data=body, method=method, headers={"Accept": "application/json", **(headers or {})})  # noqa: S310 - https enforced
    with urlopen(request, timeout=10) as response:  # noqa: S310
        return json.loads(response.read().decode())


def secure_url(url):
    """Providers must use https; plain http is accepted only on the loopback interface (local test IdPs)."""
    parsed = urlparse(url)
    return parsed.scheme == "https" or (parsed.scheme == "http" and parsed.hostname in ("localhost", "127.0.0.1"))


def load_config(path):
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    for key in ("issuer", "client_id", "redirect_uri", "role_claim", "role_map"):
        if not config.get(key):
            raise ValueError(f"OIDC 설정에 {key}가 필요합니다.")
    if not config.get("client_secret") and config.get("client_secret_env"):
        import os
        config["client_secret"] = os.environ.get(config["client_secret_env"])
    if not secure_url(config["issuer"]) or not secure_url(config["redirect_uri"]):
        raise ValueError("OIDC issuer와 redirect_uri는 https여야 합니다.")
    if any(role not in ROLE_ORDER for role in list(config["role_map"].values()) + [config.get("default_role") or "viewer"]):
        raise ValueError("role_map 값은 viewer, editor, admin 중 하나여야 합니다.")
    return config


def challenge(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()


def claims_of(token):
    try:
        payload = token.split(".")[1]
        return json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    except (AttributeError, IndexError, ValueError):
        raise ValueError("ID 토큰 형식이 올바르지 않습니다.") from None


class OidcProvider:
    def __init__(self, config, http=http_json):
        self.config, self.http = config, http
        self.pending = {}
        self.lock = threading.Lock()
        self._discovery = None

    def discovery(self):
        if self._discovery is None:
            issuer = self.config["issuer"].rstrip("/")
            document = self.http("GET", issuer + "/.well-known/openid-configuration")
            if document.get("issuer", "").rstrip("/") != issuer:
                raise ValueError("OIDC discovery 문서의 issuer가 설정과 다릅니다.")
            for key in ("authorization_endpoint", "token_endpoint"):
                if not secure_url(document.get(key, "")):
                    raise ValueError(f"OIDC {key}가 https가 아닙니다.")
            self._discovery = document
        return self._discovery

    def start(self):
        """Authorization URL plus the state that must come back in the browser's cookie."""
        state, nonce, verifier = (secrets.token_urlsafe(32) for _ in range(3))
        with self.lock:
            now = time.monotonic()
            self.pending = {k: v for k, v in self.pending.items() if v["expires"] > now}
            self.pending[state] = {"nonce": nonce, "verifier": verifier, "expires": now + PENDING_SECONDS}
        query = {"response_type": "code", "client_id": self.config["client_id"], "redirect_uri": self.config["redirect_uri"],
                 "scope": self.config.get("scope", "openid email profile"), "state": state, "nonce": nonce,
                 "code_challenge": challenge(verifier), "code_challenge_method": "S256"}
        return self.discovery()["authorization_endpoint"] + "?" + urlencode(query), state

    def finish(self, code, state, cookie_state):
        """Exchange the code; return (user name, role, claims). Raises ValueError with a user-facing reason."""
        if not state or not secrets.compare_digest(state, cookie_state or ""):
            raise ValueError("로그인 요청이 이 브라우저에서 시작되지 않았습니다. 다시 로그인하세요.")
        with self.lock:
            pending = self.pending.pop(state, None)
        if not pending or pending["expires"] < time.monotonic():
            raise ValueError("로그인 요청이 만료되었습니다. 다시 로그인하세요.")
        endpoints = self.discovery()
        form = {"grant_type": "authorization_code", "code": code, "redirect_uri": self.config["redirect_uri"],
                "client_id": self.config["client_id"], "code_verifier": pending["verifier"]}
        if self.config.get("client_secret"):
            form["client_secret"] = self.config["client_secret"]
        tokens = self.http("POST", endpoints["token_endpoint"], form, {"Content-Type": "application/x-www-form-urlencoded"})
        claims = claims_of(tokens.get("id_token"))
        audience = claims.get("aud")
        if claims.get("iss", "").rstrip("/") != self.config["issuer"].rstrip("/"):
            raise ValueError("ID 토큰 발급자가 다릅니다.")
        if self.config["client_id"] not in (audience if isinstance(audience, list) else [audience]):
            raise ValueError("ID 토큰 대상(aud)이 이 앱이 아닙니다.")
        if not secrets.compare_digest(str(claims.get("nonce", "")), pending["nonce"]):
            raise ValueError("ID 토큰 nonce가 일치하지 않습니다.")
        if float(claims.get("exp", 0)) < time.time():
            raise ValueError("ID 토큰이 만료되었습니다.")
        if endpoints.get("userinfo_endpoint") and tokens.get("access_token"):
            info = self.http("GET", endpoints["userinfo_endpoint"], headers={"Authorization": "Bearer " + tokens["access_token"]})
            if info.get("sub") != claims.get("sub"):
                raise ValueError("userinfo의 사용자가 ID 토큰과 다릅니다.")
            claims = {**claims, **info}
        role = self.role(claims)
        if not role:
            raise ValueError("이 앱에 대한 역할이 없는 계정입니다. 관리자에게 그룹 할당을 요청하세요.")
        name = claims.get("email") or claims.get("preferred_username") or claims["sub"]
        return name, role, claims

    def role(self, claims):
        values = claims.get(self.config["role_claim"], [])
        values = values if isinstance(values, list) else [values]
        roles = [self.config["role_map"][v] for v in values if v in self.config["role_map"]]
        if not roles and self.config.get("default_role"):
            roles = [self.config["default_role"]]
        return max(roles, key=ROLE_ORDER.get) if roles else None
