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
import asyncio
import base64
import re
import secrets
import threading
import time
from urllib.parse import urlparse

from botocore.awsrequest import AWSResponse

AWS_HOST = re.compile(r"^[a-z0-9.-]+\.amazonaws\.com(\.cn)?$")
BROWSER_MANAGED = {"host", "content-length"}  # set by the browser from the URL and body; fetch cannot set them
READ = re.compile(r"^(Describe|List|Get)[A-Z]")  # operations that change nothing: safe to send again


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
    """Per-user queues between the server's boto3 clients and the browser tabs' relay loops (long-poll + answer).

    Each tab polls with its own id and keeps a poll open while it carries requests, so an open poll is its heartbeat.
    A request whose tab is gone (no poll open or made for `idle` seconds) goes back to the queue for another tab of the
    user when sending it twice is harmless (a read: Describe/List/Get), and fails at once otherwise (it may have reached
    AWS), instead of waiting out the round-trip `timeout`."""

    def __init__(self, timeout=90, idle=15, poll=20, tick=1.0, clock=time.monotonic):
        # timeout: one request's round trip; idle: a tab without a poll this long is gone; poll: long-poll length;
        # tick: how often a waiting request checks on the tab carrying it.
        self.timeout, self.idle, self.poll, self.tick, self.clock = timeout, idle, poll, tick, clock
        self.pending = {}  # id -> {"owner", "request", "retry", "tab": (owner, tab) that took it or None, "done", "result"}
        self.tabs = {}  # (owner, tab) -> {"seen": last poll start or end, "polls": polls open now}
        self.waiters = {}  # owner -> {(event loop, asyncio.Event)} of the async polls waiting for work
        self.ready = threading.Condition()

    def attach(self, session, owner):
        """Route every request of this boto3 session through `owner`'s browser."""
        def relay(request, event_name="", **_):
            return self.send(owner, request, event_name)
        # First, so no other before-send handler (an emulator, a stubber) answers in the browser's place.
        session.events.register_first("before-send", relay, unique_id=f"diagram-bot-browser-egress-{id(self)}")
        return session

    def alive(self, key):
        tab = self.tabs.get(key)
        return bool(tab) and (tab["polls"] > 0 or self.clock() - tab["seen"] < self.idle)

    def connected(self, owner):
        with self.ready:
            return any(self.alive(key) for key in self.tabs if key[0] == owner)

    def send(self, owner, request, event_name=""):
        if not self.connected(owner):
            raise RelayDisconnected("브라우저 중계가 연결되어 있지 않습니다. 앱 화면을 연 채로 다시 시도하세요 (AWS 호출은 브라우저에서 나갑니다).")
        identifier = secrets.token_urlsafe(12)
        # event_name: before-send.<service>.<Operation>, from botocore
        item = {"owner": owner, "request": outgoing(request), "retry": bool(READ.match(event_name.rpartition(".")[2])),
                "tab": None, "done": threading.Event(), "result": None}
        with self.ready:
            self.pending[identifier] = item
            self.wake(owner)
        deadline = self.clock() + self.timeout
        try:
            while not item["done"].wait(self.tick):
                with self.ready:
                    if item["done"].is_set():
                        break
                    if item["tab"] and not self.alive(item["tab"]):
                        if not (item["retry"] and self.connected(owner)):
                            raise RelayDisconnected("AWS 요청을 맡은 브라우저 탭이 닫혔습니다. 앱 화면을 연 채로 다시 시도하세요.")
                        item["tab"] = None  # a read another open tab can send again
                        self.wake(owner)
                    elif not item["tab"] and not self.connected(owner):
                        raise RelayDisconnected("브라우저 중계가 끊겼습니다. 앱 화면을 연 채로 다시 시도하세요.")
                if self.clock() >= deadline:
                    raise RelayDisconnected("브라우저가 AWS 요청을 시간 안에 전달하지 못했습니다.")
        finally:
            with self.ready:
                self.pending.pop(identifier, None)
        result = item["result"]
        if not result.get("status"):
            raise RelayError(f"브라우저에서 AWS 호출이 막혔습니다 (네트워크·CORS·프록시): {str(result.get('error') or '')[:200]}")
        return AWSResponse(request.url, int(result["status"]), result.get("headers") or {},
                           Body(base64.b64decode(result.get("body") or "")))

    def wake(self, owner):
        self.ready.notify_all()
        for loop, event in self.waiters.get(owner, ()):
            try:
                loop.call_soon_threadsafe(event.set)
            except RuntimeError:  # that poll's event loop has closed
                pass

    def opened(self, key):
        now = self.clock()
        for gone in [k for k, t in self.tabs.items() if not t["polls"] and now - t["seen"] >= self.idle]:
            del self.tabs[gone]  # a tab that is not listed is gone
        tab = self.tabs.setdefault(key, {"seen": now, "polls": 0})
        tab["polls"] += 1
        tab["seen"] = now

    def closed(self, key, left=False):
        tab = self.tabs[key]
        tab["polls"] -= 1
        tab["seen"] = float("-inf") if left else self.clock()

    def take(self, key):
        taken = [(k, v) for k, v in self.pending.items() if v["owner"] == key[0] and not v["tab"] and not v["done"].is_set()]
        for _, v in taken:
            v["tab"] = key
        return [{"id": k, **v["request"]} for k, v in taken]

    def next(self, owner, wait=None, tab=""):
        """Requests waiting for this user's browser; waits up to `wait` (default: poll) seconds for the first one."""
        key, deadline = (owner, tab), self.clock() + (self.poll if wait is None else wait)
        with self.ready:
            self.opened(key)
            try:
                while not (taken := self.take(key)) and self.clock() < deadline:
                    self.ready.wait(min(1.0, max(0.0, deadline - self.clock())))
                return taken
            finally:
                self.closed(key)

    async def wait_next(self, owner, tab="", wait=None, gone=None):
        """next() for an async endpoint: waiting holds no thread. gone() is true once the client has left."""
        loop = asyncio.get_running_loop()
        key, deadline, left = (owner, tab), self.clock() + (self.poll if wait is None else wait), False
        with self.ready:
            self.opened(key)
        try:
            while True:
                with self.ready:
                    if (taken := self.take(key)) or self.clock() >= deadline:
                        return taken
                    waiter = (loop, asyncio.Event())
                    self.waiters.setdefault(owner, set()).add(waiter)
                try:
                    await asyncio.wait_for(waiter[1].wait(), min(1.0, max(0.0, deadline - self.clock())))
                except TimeoutError:
                    pass
                finally:
                    with self.ready:
                        self.waiters[owner].discard(waiter)
                        if not self.waiters[owner]:
                            del self.waiters[owner]
                if gone and gone():
                    left = True
                    return []
        finally:
            with self.ready:
                self.closed(key, left)

    def answer(self, owner, identifier, result):
        with self.ready:
            item = self.pending.get(identifier)
            if not item or item["owner"] != owner or item["done"].is_set():
                return False
            item["result"] = result
            item["done"].set()
            return True
