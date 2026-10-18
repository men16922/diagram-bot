"""Browser egress: AWS requests are built and signed on the server and sent by the signed-in user's browser.

For networks where the server cannot reach AWS APIs (outbound firewall) and Permission Sets deny calls from
outside the company's public addresses (aws:SourceIp). The server keeps the Identity Center sign-in and the role
credentials; botocore serializes and signs each request as usual, and at `before-send` the prepared request is
handed to the user's browser, which fetches it from the PC's address (the AWS service APIs used here answer CORS)
and returns the response for botocore to parse, retry and paginate as if it had sent it.

Only requests made by the server's own boto3 clients are signed: there is no endpoint that signs caller input,
and the browser receives a request signature (valid for that request only), never the secret key. Identity Center
itself (sso-oidc, portal.sso) does not answer CORS, so device-code sign-in and GetRoleCredentials stay on the server.
"""
import base64
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from botocore.awsrequest import AWSResponse

AWS_HOST = re.compile(r"^[a-z0-9.-]+\.amazonaws\.com(\.cn)?$")
BROWSER_MANAGED = {"host", "content-length"}  # set by the browser from the URL and body; fetch cannot set them


class RelayError(ConnectionError):
    """The browser could not carry a request: not connected, timed out, or blocked (network, CORS)."""


class RelayDisconnected(RelayError):
    """No browser is carrying this user's requests (page closed or not answering): every later call would fail too."""


class Body:
    def __init__(self, data):
        self.data = data

    def stream(self, **_):
        yield self.data


def text(value):
    return value.decode() if isinstance(value, bytes) else str(value)


def outgoing(request):
    """The prepared request as the browser sends it: URL, method, the signed headers and the body (base64)."""
    url = urlparse(request.url)
    if url.scheme != "https" or not AWS_HOST.match(url.hostname or ""):
        raise RelayError(f"AWS API 엔드포인트가 아닌 주소는 중계하지 않습니다: {url.hostname}")
    headers = {k: text(v) for k, v in request.headers.items()}
    match = re.search(r"SignedHeaders=([^,\s]+)", headers.get("Authorization", ""))
    signed = set(match.group(1).split(";")) if match else {"content-type"}
    # Every signed header must reach AWS unchanged; unsigned SDK extras (user agent, invocation id) would only widen
    # the CORS preflight.
    sent = {k: v for k, v in headers.items() if (k.lower() in signed or k.lower() == "authorization") and k.lower() not in BROWSER_MANAGED}
    body = request.body or b""
    if hasattr(body, "read"):
        body = body.read()
    body = body.encode() if isinstance(body, str) else bytes(body)
    return {"url": request.url, "method": request.method, "headers": sent, "body": base64.b64encode(body).decode() if body else None}


class BrowserRelay:
    """Per-user queues between the server's boto3 clients and the browser's relay loop (long-poll + answer)."""

    def __init__(self, timeout=90, idle=30, poll=20, clock=time.monotonic):
        # timeout: one request's round trip; idle: a browser silent this long is not connected; poll: long-poll length.
        self.timeout, self.idle, self.poll, self.clock = timeout, idle, poll, clock
        self.pending = {}  # id -> {"owner", "request", "done": Event, "result"}
        self.seen = {}  # owner -> last time their browser asked for work
        self.ready = threading.Condition()

    def attach(self, session, owner):
        """Route every request of this boto3 session through `owner`'s browser."""
        def relay(request, **_):
            return self.send(owner, request)
        # First, so no other before-send handler (an emulator, a stubber) answers in the browser's place.
        session.events.register_first("before-send", relay, unique_id=f"diagram-bot-browser-egress-{id(self)}")
        return session

    def connected(self, owner):
        return owner in self.seen and self.clock() - self.seen[owner] < self.idle

    def send(self, owner, request):
        if not self.connected(owner):
            raise RelayDisconnected("브라우저 중계가 연결되어 있지 않습니다. 앱 화면을 연 채로 다시 시도하세요 (AWS 호출은 브라우저에서 나갑니다).")
        identifier = secrets.token_urlsafe(12)
        item = {"owner": owner, "request": outgoing(request), "done": threading.Event(), "result": None}
        with self.ready:
            self.pending[identifier] = item
            self.ready.notify_all()
        finished = item["done"].wait(self.timeout)
        with self.ready:
            self.pending.pop(identifier, None)
        result = item["result"]
        if not finished or result is None:
            raise RelayDisconnected("브라우저가 AWS 요청을 시간 안에 전달하지 못했습니다.")
        if not result.get("status"):
            raise RelayError(f"브라우저에서 AWS 호출이 막혔습니다 (네트워크·CORS·프록시): {str(result.get('error') or '')[:200]}")
        return AWSResponse(request.url, int(result["status"]), result.get("headers") or {},
                           Body(base64.b64decode(result.get("body") or "")))

    def next(self, owner, wait=None):
        """Requests waiting for this user's browser; waits up to `wait` (default: poll) seconds for the first one."""
        deadline = self.clock() + (self.poll if wait is None else wait)
        with self.ready:
            while True:
                self.seen[owner] = self.clock()
                taken = [(k, v) for k, v in self.pending.items() if v["owner"] == owner and not v.get("taken")]
                if taken or self.clock() >= deadline:
                    for _, v in taken:
                        v["taken"] = True
                    return [{"id": k, **v["request"]} for k, v in taken]
                self.ready.wait(min(1.0, max(0.0, deadline - self.clock())))

    def answer(self, owner, identifier, result):
        with self.ready:
            item = self.pending.get(identifier)
            if not item or item["owner"] != owner or item["done"].is_set():
                return False
            item["result"] = result
            item["done"].set()
            return True

    def disconnect(self, owner):
        with self.ready:
            self.seen.pop(owner, None)
