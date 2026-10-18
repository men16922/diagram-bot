"""How the app reaches AWS for a user: which credentials, and which network path.

Credentials: IAM Identity Center (device code on the server, role credentials from GetRoleCredentials, sso.py), else the
server's SDK chain (profile, environment, .env). Path: the server itself, or with browser egress the user's browser
(egress.py). Everything that calls AWS for a user gets its boto3 session here, so replacing the sign-in or the path
(for instance with a company's own token service) means replacing this class only.
"""
import threading

import boto3

from .egress import BrowserRelay
from .sso import SsoError, SsoLogins, discover_start_url, refreshing_session


class AwsAccess:
    def __init__(self, egress="server", sso=None, relay=None, sso_start=None):
        if egress not in ("server", "browser"):
            raise ValueError(f"egress는 server 또는 browser입니다: {egress}")
        self.egress = egress
        self.sso = sso or SsoLogins()
        self.relay = (relay or BrowserRelay()) if egress == "browser" else None
        self.sso_start = sso_start  # (start URL, region) offered first in the sign-in dialog
        self.choices = {}  # user -> (login id, account, role) they signed in with; the chat agent reads with it
        self.lock = threading.Lock()
        self._defaults = None

    # --- network path ----------------------------------------------------------------------------
    def outbound(self, session, user):
        """With browser egress the session's requests leave from `user`'s browser; otherwise unchanged."""
        return self.relay.attach(session, user) if self.relay else session

    def prepare_for(self, user):
        """For collect(prepare=...): every session of a collection, incl. an assumed role's, takes the same path."""
        return (lambda session: self.relay.attach(session, user)) if self.relay else None

    # --- credentials ---------------------------------------------------------------------------------
    def choose(self, user, login, account, role):
        with self.lock:
            self.choices[user] = (login, account, role)

    def forget(self, user, login=None):
        with self.lock:
            if login is None or self.choices.get(user, (None,))[0] == login:
                self.choices.pop(user, None)

    def chosen_account(self, user):
        return (self.choices.get(user) or (None, None))[1]

    def session(self, user, region):
        """The user's session for read-only calls: their Identity Center role if they chose one, else the SDK chain."""
        choice = self.choices.get(user)
        session = self.sso.session(choice[0], user, choice[1], choice[2], region) if choice else boto3.Session(region_name=region)
        return self.outbound(session, user)

    def model_session(self, user, region):
        """The user's session for a long-lived client (the chat's Bedrock model). With an Identity Center role the
        credentials are fetched on first use and again before they expire, for the role the user has chosen by then,
        so a chat outlives the role session and picks up a new sign-in; building it makes no AWS call."""
        if user not in self.choices:
            return self.outbound(boto3.Session(region_name=region), user)

        def fetch():
            choice = self.choices.get(user)
            if not choice:
                raise SsoError("SSO 로그인이 완료되지 않았거나 만료됐습니다. 다시 로그인하세요.")
            return self.sso.credentials(choice[0], user, choice[1], choice[2])
        return self.outbound(refreshing_session(fetch, region), user)

    def collection_session(self, user, login, account, role, region):
        """Session for a collection request: an Identity Center role (remembered for the chat), else None (profile/SDK chain)."""
        if not login:
            return None
        if not role:
            raise ValueError("SSO 역할을 선택하세요.")
        session = self.sso.session(login, user, account, role, region)
        self.choose(user, login, account, role)
        return session

    def via(self, sso_role=None):
        """How a collection reached AWS, for the audit log."""
        return (f"sso:{sso_role}" if sso_role else "sdk") + ("/browser" if self.relay else "")

    def start_urls(self):
        """The configured start URL, then ~/.aws/config sso-sessions/profiles, else discovered from the server's credentials."""
        if self._defaults is None:
            import botocore.session
            from botocore.exceptions import BotoCoreError
            rows = [{"name": "회사 Identity Center", "start_url": self.sso_start[0], "region": self.sso_start[1]}] if self.sso_start else []
            try:
                config = botocore.session.Session().full_config
                for name, item in config.get("sso_sessions", {}).items():
                    if item.get("sso_start_url"):
                        rows.append({"name": name, "start_url": item["sso_start_url"], "region": item.get("sso_region", "us-east-1")})
                for name, item in config.get("profiles", {}).items():
                    if item.get("sso_start_url") and not any(r["start_url"] == item["sso_start_url"] for r in rows):
                        rows.append({"name": name, "start_url": item["sso_start_url"], "region": item.get("sso_region", "us-east-1")})
            except BotoCoreError:
                pass
            # Discovery calls sso-admin from the server, which browser egress assumes cannot reach AWS.
            if not rows and not self.relay and (found := discover_start_url()):
                rows.append({"name": "Identity Center", **found})
            self._defaults = rows
        return self._defaults
