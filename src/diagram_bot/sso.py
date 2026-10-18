"""IAM Identity Center (AWS SSO) sign-in from the app with the OAuth device authorization grant.

The browser only ever sees the user code and verification link; the client secret, device code, access token and
role credentials stay in this process's memory (never on disk, never in responses or logs). One flow per sign-in,
bound to the app user who started it.
"""
import re
import secrets
import threading
import time
from datetime import UTC, datetime

import boto3
import botocore.session
from botocore.credentials import DeferredRefreshableCredentials
from botocore.exceptions import ClientError

from .errors import error_code
from .validation import ACCOUNT, REGION

GRANT = "urn:ietf:params:oauth:grant-type:device_code"
START_URL = re.compile(r"^https://[A-Za-z0-9.-]+(?::\d+)?/[^\s]*$")


class SsoError(ValueError):
    """A sign-in problem the user can act on; the message is shown in the app."""


class SsoLogins:
    def __init__(self, client=None, clock=time.time):
        self.client = client or (lambda service, region: boto3.client(service, region_name=region))
        self.clock = clock
        self.flows = {}
        self.lock = threading.Lock()

    def flow(self, identifier, owner):
        flow = self.flows.get(identifier)
        if not flow or flow["owner"] != owner:
            raise SsoError("SSO 로그인 세션을 찾을 수 없습니다. 다시 로그인하세요.")
        return flow

    def start(self, start_url, region, owner):
        start_url, region = (start_url or "").strip(), (region or "").strip()
        if not START_URL.match(start_url) or len(start_url) > 512:
            raise SsoError("SSO 시작 URL은 https://로 시작해야 합니다. 예: https://d-1234567890.awsapps.com/start")
        if not re.fullmatch(REGION, region):
            raise SsoError(f"SSO 리전 형식이 아닙니다: {region}")
        oidc = self.client("sso-oidc", region)
        try:
            registration = oidc.register_client(clientName="diagram-bot", clientType="public", scopes=["sso:account:access"])
            device = oidc.start_device_authorization(clientId=registration["clientId"], clientSecret=registration["clientSecret"],
                                                     startUrl=start_url)
        except ClientError as exc:
            raise SsoError(f"SSO 로그인을 시작하지 못했습니다({error_code(exc)}). 시작 URL과 리전을 확인하세요.") from exc
        identifier = secrets.token_urlsafe(16)
        now = self.clock()
        with self.lock:
            # Abandoned flows of the same user are dropped so a user holds at most a few secrets in memory.
            for key in [k for k, f in self.flows.items() if f["owner"] == owner and f["status"] != "authorized"]:
                self.flows.pop(key)
            self.flows[identifier] = {
                "owner": owner, "region": region, "start_url": start_url, "status": "pending",
                "client_id": registration["clientId"], "client_secret": registration["clientSecret"],
                "device_code": device["deviceCode"], "interval": max(1, int(device.get("interval", 5))),
                "expires_at": now + int(device.get("expiresIn", 600)), "next_poll": now, "token": None, "accounts": None}
        return {"id": identifier, "status": "pending", "user_code": device["userCode"],
                "verification_uri": device["verificationUri"],
                "verification_uri_complete": device.get("verificationUriComplete") or device["verificationUri"],
                "expires_in": int(device.get("expiresIn", 600)), "interval": max(1, int(device.get("interval", 5)))}

    def poll(self, identifier, owner):
        """Advance the device grant at most once per interval and report the state; accounts once authorized.
        Identity Center is called outside the lock (copy under it, call, commit under it): one slow answer must not stall
        every other user's sign-in, role list and session."""
        with self.lock:
            flow = self.flow(identifier, owner)
            now = self.clock()
            if flow["status"] == "authorized" and now >= flow["token_expires_at"]:
                flow.update(status="expired", token=None)
            if flow["status"] == "pending" and now >= flow["expires_at"]:
                flow["status"] = "expired"
            grant = None
            if flow["status"] == "pending" and now >= flow["next_poll"]:
                grant = {k: flow[k] for k in ("region", "client_id", "client_secret", "device_code", "interval")}
                flow["next_poll"] = now + flow["interval"]  # a concurrent poll waits for this exchange instead of repeating it
        if grant:
            outcome = self.exchange(grant, now)
            with self.lock:
                if flow["status"] == "pending":
                    flow.update(outcome)
        with self.lock:
            listing = (flow["region"], flow["token"]) if flow["status"] == "authorized" and flow["accounts"] is None else None
        if listing:
            accounts = self.accounts(*listing)
            with self.lock:
                if flow["token"] == listing[1]:
                    flow["accounts"] = accounts
        with self.lock:
            view = {"id": identifier, "status": flow["status"], "interval": flow["interval"]}
            if flow["status"] != "pending" and not flow.get("reported"):
                flow["reported"] = view["changed"] = True  # first poll after the outcome: the server audits it once
            if flow["status"] == "authorized":
                view["accounts"] = flow["accounts"]
                view["expires_in"] = int(flow["token_expires_at"] - now)
            return view

    def exchange(self, grant, now):
        """One CreateToken call for a pending device grant; returns the changes to the flow."""
        try:
            token = self.client("sso-oidc", grant["region"]).create_token(
                clientId=grant["client_id"], clientSecret=grant["client_secret"], grantType=GRANT, deviceCode=grant["device_code"])
        except ClientError as exc:
            code = error_code(exc)
            if code == "AuthorizationPendingException":
                return {}
            if code == "SlowDownException":
                return {"interval": grant["interval"] + 5, "next_poll": now + grant["interval"] + 5}
            if code in ("ExpiredTokenException", "InvalidGrantException"):
                return {"status": "expired"}
            return {"status": "denied" if code == "AccessDeniedException" else "failed"}
        return {"status": "authorized", "token": token["accessToken"], "token_expires_at": now + int(token.get("expiresIn", 3600)),
                "client_secret": None, "device_code": None}

    def accounts(self, region, token):
        sso = self.client("sso", region)
        rows = []
        for page in sso.get_paginator("list_accounts").paginate(accessToken=token):
            rows += [{"id": a["accountId"], "name": a.get("accountName") or a["accountId"]} for a in page.get("accountList", [])]
        return sorted(rows, key=lambda a: (a["name"], a["id"]))

    def roles(self, identifier, owner, account_id):
        with self.lock:
            flow = self.authorized(identifier, owner)
            token, region = flow["token"], flow["region"]
        if not re.fullmatch(ACCOUNT, account_id or ""):
            raise SsoError("12자리 계정 ID가 필요합니다.")
        sso = self.client("sso", region)
        names = []
        for page in sso.get_paginator("list_account_roles").paginate(accessToken=token, accountId=account_id):
            names += [r["roleName"] for r in page.get("roleList", [])]
        return sorted(names)

    def authorized_flow(self, identifier, owner):
        with self.lock:
            self.authorized(identifier, owner)

    def authorized(self, identifier, owner):
        flow = self.flow(identifier, owner)
        if flow["status"] != "authorized" or self.clock() >= flow["token_expires_at"]:
            raise SsoError("SSO 로그인이 완료되지 않았거나 만료됐습니다. 다시 로그인하세요.")
        return flow

    def credentials(self, identifier, owner, account_id, role_name):
        """The role's short-lived credentials (GetRoleCredentials) in botocore's refresh format."""
        with self.lock:
            flow = self.authorized(identifier, owner)
            token, sso_region = flow["token"], flow["region"]
        try:
            credentials = self.client("sso", sso_region).get_role_credentials(
                accessToken=token, accountId=account_id, roleName=role_name)["roleCredentials"]
        except ClientError as exc:
            raise SsoError(f"SSO 역할 자격 증명을 받지 못했습니다({error_code(exc)}). 계정·역할 할당을 확인하세요.") from exc
        expires = credentials.get("expiration") or (time.time() + 900) * 1000  # epoch milliseconds
        return {"access_key": credentials["accessKeyId"], "secret_key": credentials["secretAccessKey"],
                "token": credentials["sessionToken"], "expiry_time": datetime.fromtimestamp(expires / 1000, UTC).isoformat()}

    def session(self, identifier, owner, account_id, role_name, region):
        """A boto3 session with the role's short-lived credentials (fetched for each collection)."""
        credentials = self.credentials(identifier, owner, account_id, role_name)
        return boto3.Session(aws_access_key_id=credentials["access_key"], aws_secret_access_key=credentials["secret_key"],
                             aws_session_token=credentials["token"], region_name=region)

    def logout(self, identifier, owner):
        with self.lock:
            flow = self.flows.get(identifier)
            if not flow or flow["owner"] != owner:
                return False
            self.flows.pop(identifier)
        if flow.get("token"):
            try:
                self.client("sso", flow["region"]).logout(accessToken=flow["token"])
            except ClientError:
                pass  # the local token is discarded either way
        return True


def refreshing_session(fetch, region):
    """A session whose credentials come from fetch() on first use and again before they expire (botocore refreshes),
    for a client that outlives one role session, such as the chat's Bedrock model. Nothing is fetched here."""
    core = botocore.session.Session()
    core._credentials = DeferredRefreshableCredentials(fetch, "diagram-bot-sso")
    return boto3.Session(botocore_session=core, region_name=region)


def discover_start_url(session=None, region=None):
    """Identity Center start URL and home region from the current credentials, when they may list instances."""
    session = session or boto3.Session()
    region = region or session.region_name or "us-east-1"
    try:
        instances = session.client("sso-admin", region_name=region).list_instances().get("Instances", [])
    except Exception:  # noqa: BLE001 - discovery is a convenience; any failure just leaves the field empty
        return None
    store = next((i.get("IdentityStoreId") for i in instances if i.get("Status", "ACTIVE") == "ACTIVE"), None)
    return {"start_url": f"https://{store}.awsapps.com/start", "region": region} if store else None
