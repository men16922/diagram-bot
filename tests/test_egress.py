"""Browser egress: the server signs, the browser sends. A thread plays the browser: it long-polls the relay,
sends each request (to the AWS emulator through `requests`, which moto intercepts) and answers with the response."""
import asyncio
import base64
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import boto3
import pytest
import requests
from fastapi.testclient import TestClient

from diagram_bot.collector import collect
from diagram_bot.egress import BrowserRelay, RelayDisconnected, RelayError
from diagram_bot.graph import build_graph
from diagram_bot.server import create_app
from test_aws_emulator import ACCOUNT, REGION, environment  # noqa: F401 - fixture

IDENTITY = (b'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><GetCallerIdentityResult>'
            b'<Arn>arn:aws:sts::111122223333:assumed-role/CloudOpsReadOnly/alice</Arn><UserId>AROA:alice</UserId>'
            b'<Account>111122223333</Account></GetCallerIdentityResult></GetCallerIdentityResponse>')


class Browser:
    """Answers relayed requests for one user until stopped; `send` decides each response."""

    def __init__(self, relay, owner, send):
        self.relay, self.owner, self.send, self.seen = relay, owner, send, []
        self.running = True
        self.thread = threading.Thread(target=self.loop, daemon=True)

    def loop(self):
        while self.running:
            for request in self.relay.next(self.owner, wait=0.1):
                self.seen.append(request)
                self.relay.answer(self.owner, request["id"], self.send(request))

    def __enter__(self):
        self.relay.next(self.owner, wait=0)  # the page has loaded and asked once
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.running = False
        self.thread.join(2)


def sts_session():
    return boto3.Session(aws_access_key_id="ASIAEXAMPLE", aws_secret_access_key="secret", aws_session_token="token",
                         region_name="us-east-1")


def test_the_browser_receives_a_signed_request_and_botocore_parses_its_answer():
    relay = BrowserRelay()
    with Browser(relay, "alice", lambda r: {"status": 200, "headers": {"content-type": "text/xml"},
                                            "body": base64.b64encode(IDENTITY).decode()}) as browser:
        identity = relay.attach(sts_session(), "alice").client("sts").get_caller_identity()
    assert identity["Account"] == "111122223333"
    [sent] = browser.seen
    assert sent["url"].startswith("https://sts.") and sent["method"] == "POST"
    # Only what the signature covers (plus the Authorization header) goes to the browser: no user agent or SDK
    # invocation headers that would widen the CORS preflight, never the secret key.
    assert {k.lower() for k in sent["headers"]} == {"authorization", "content-type", "x-amz-date", "x-amz-security-token"}
    assert "Signature=" in sent["headers"]["Authorization"] and "secret" not in str(sent)
    assert base64.b64decode(sent["body"]).decode().startswith("Action=GetCallerIdentity")


def test_relay_failures_are_reported_not_hung():
    relay = BrowserRelay(timeout=1)
    session = relay.attach(sts_session(), "alice")
    started = time.monotonic()
    with pytest.raises(RelayError, match="연결되어 있지 않습니다"):
        session.client("sts").get_caller_identity()  # no browser has asked for work
    assert time.monotonic() - started < 1
    with Browser(relay, "alice", lambda r: {"status": 0, "error": "TypeError: Failed to fetch"}):
        with pytest.raises(RelayError, match="CORS"):
            session.client("sts").get_caller_identity()
    with Browser(relay, "alice", lambda r: {"status": 200, "body": ""}):
        # Bob's browser cannot take or answer Alice's requests, and an endpoint that is not AWS is never relayed.
        assert relay.next("bob", wait=0) == [] and relay.answer("bob", "nope", {"status": 200}) is False
        local = relay.attach(boto3.Session(aws_access_key_id="a", aws_secret_access_key="b", region_name="us-east-1"), "alice")
        with pytest.raises(RelayError, match="AWS API 엔드포인트가 아닌"):
            local.client("sts", endpoint_url="http://127.0.0.1:9/").get_caller_identity()


class Tab:
    """A page's relay loop that polls with its own id; `answering` False makes it take requests and sit on them
    (a slow fetch), `close()` stops its polls (the tab was closed)."""

    def __init__(self, relay, name, answering=True):
        self.relay, self.name, self.answering, self.taken, self.open = relay, name, answering, [], True
        self.relay.next("alice", wait=0, tab=name)
        self.thread = threading.Thread(target=self.loop, daemon=True)
        self.thread.start()

    def loop(self):
        while self.open:
            for request in self.relay.next("alice", wait=0.05, tab=self.name):
                self.taken.append(request["id"])
                if self.answering:
                    self.relay.answer("alice", request["id"], {"status": 200, "body": ""})

    def close(self):
        self.open = False
        self.thread.join(2)


def relayed(relay, operation):
    request = SimpleNamespace(url="https://sts.us-east-1.amazonaws.com/", method="POST", headers={}, body=b"")
    return ThreadPoolExecutor(1).submit(relay.send, "alice", request, f"before-send.sts.{operation}")


def test_a_closed_tab_hands_reads_to_another_tab_and_fails_the_rest_at_once():
    relay = BrowserRelay(timeout=30, idle=0.3, tick=0.02)
    slow = Tab(relay, "slow", answering=False)
    started = time.monotonic()
    read = relayed(relay, "DescribeVpcs")
    while not slow.taken:
        time.sleep(0.01)
    other = Tab(relay, "other")
    time.sleep(0.6)  # a tab that keeps polling is alive however long it holds a request (a slow Bedrock call)
    assert not read.done()
    slow.close()
    # Gone: the read goes to the other tab, long before the 30 s round-trip timeout.
    assert read.result(2).status_code == 200 and time.monotonic() - started < 3
    other.close()
    sitting = Tab(relay, "sitting", answering=False)
    converse = relayed(relay, "Converse")  # may have reached AWS: never sent twice
    while not sitting.taken:
        time.sleep(0.01)
    sitting.close()
    started = time.monotonic()
    with pytest.raises(RelayDisconnected, match="탭이 닫혔습니다"):
        converse.result(5)
    assert time.monotonic() - started < 2


def test_a_read_fails_at_once_when_the_last_tab_closes():
    relay = BrowserRelay(timeout=30, idle=0.3, tick=0.02)
    only = Tab(relay, "only", answering=False)
    read = relayed(relay, "ListClusters")
    while not only.taken:
        time.sleep(0.01)
    only.close()
    with pytest.raises(RelayDisconnected):
        read.result(3)
    assert relay.tabs == {} or not relay.connected("alice")


def test_async_polls_wait_without_a_thread_and_wake_on_work():
    relay = BrowserRelay(poll=5)

    async def tabs():
        polls = [asyncio.create_task(relay.wait_next("alice", f"tab{i}")) for i in range(60)]  # more than the threadpool
        bob = asyncio.create_task(relay.wait_next("bob", "b"))
        await asyncio.sleep(0.05)
        assert relay.connected("alice") and relay.connected("bob")
        started = time.monotonic()
        answer = await asyncio.to_thread(lambda: relayed(relay, "GetCallerIdentity"))
        done, _ = await asyncio.wait(polls, timeout=2, return_when=asyncio.FIRST_COMPLETED)
        assert time.monotonic() - started < 1  # woken by the request, not by the 5 s poll end
        [request] = done.pop().result()
        relay.answer("alice", request["id"], {"status": 200, "body": ""})
        assert (await asyncio.wrap_future(answer)).status_code == 200
        for task in [*polls, bob]:
            task.cancel()
        await asyncio.gather(*polls, bob, return_exceptions=True)
        assert all(t["polls"] == 0 for t in relay.tabs.values()) and not relay.waiters

    asyncio.run(tabs())


def test_a_tab_that_left_mid_poll_is_gone_at_once():
    relay = BrowserRelay(poll=5)
    assert asyncio.run(relay.wait_next("alice", "t", wait=2, gone=lambda: True)) == [] and not relay.connected("alice")


def through_requests(request):
    """The browser's fetch: the emulator answers AWS URLs sent through `requests`."""
    body = base64.b64decode(request["body"]) if request["body"] else None
    try:
        response = requests.request(request["method"], request["url"], headers=request["headers"], data=body, timeout=30)
    except Exception as exc:  # noqa: BLE001 - an operation the emulator lacks: the page sees a failed fetch
        return {"status": 0, "error": f"{type(exc).__name__}: {exc}"}
    return {"status": response.status_code, "headers": dict(response.headers), "body": base64.b64encode(response.content).decode()}


def test_a_collection_through_the_browser_equals_a_direct_one(environment):  # noqa: F811
    session, built, _ = environment
    direct = collect(REGION, session=session, expected_account=ACCOUNT)
    relay = BrowserRelay()
    with Browser(relay, "alice", through_requests) as browser:
        relayed = collect(REGION, session=boto3.Session(region_name=REGION), expected_account=ACCOUNT,
                          prepare=lambda s: relay.attach(s, "alice"))
    status = lambda snapshot: {(c["service"], c["operation"]): c["status"] for c in snapshot["coverage"]}
    assert status(relayed) == status(direct)
    ids = lambda snapshot: sorted(n["id"] for n in build_graph(snapshot)["nodes"])
    assert ids(relayed) == ids(direct) and built["vpc"] in str(ids(relayed))
    # Every AWS request of the collection went out through the browser (identity, each service, pages, details).
    assert len(browser.seen) > len(relayed["coverage"])
    assert {r["url"].split("/")[2].split(".")[0] for r in browser.seen} >= {"sts", "ec2", "elasticloadbalancing", "rds", "lambda"}


def test_the_app_collects_through_the_browser_relay_api(environment, tmp_path):  # noqa: F811
    """End to end over HTTP: the job signs on the server, the page loop takes /api/relay/next and answers each request."""
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor", egress="browser", relay=BrowserRelay(poll=0.2))) as client:
        boot = client.get("/api/bootstrap").json()
        assert boot["egress"] == "browser"
        client.headers["X-Diagram-Bot"] = boot["token"]
        assert client.post("/api/relay/missing", json={"status": 200}).status_code == 404
        stop = threading.Event()

        def page():
            while not stop.is_set():
                for request in client.get("/api/relay/next?tab=page").json()["requests"]:
                    client.post(f"/api/relay/{request['id']}", json=through_requests(request))

        loop = threading.Thread(target=page, daemon=True)
        loop.start()
        time.sleep(0.2)
        job = client.post("/api/collections", json={"region": REGION, "expected_account": ACCOUNT}).json()
        for _ in range(600):
            job = client.get(f"/api/collections/{job['id']}").json()
            if job["status"] not in ("queued", "running"):
                break
            time.sleep(0.05)
        stop.set()
        assert job["status"] in ("complete", "partial") and job.get("snapshot_id"), job
        snapshot = client.get(f"/api/snapshots/{job['snapshot_id']}").json()
        assert any(n["kind"] == "vpc" and not n["reference_only"] for n in snapshot["graph"]["nodes"])
        log = (tmp_path / "data" / "audit.log").read_text()
        # One audit line for the collection (via the browser), none per relayed AWS request.
        assert '"via": "sdk/browser"' in log and "/api/relay/" not in log
    with TestClient(create_app(tmp_path / "server", tmp_path / "no-editor")) as plain:
        assert plain.get("/api/bootstrap").json()["egress"] == "server" and plain.get("/api/relay/next").status_code == 404


def test_open_relay_polls_do_not_starve_other_requests(tmp_path):
    """More open tabs than the worker threadpool (40): their long-polls are async, so other calls still get a thread."""
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor", egress="browser", relay=BrowserRelay(poll=2))) as client:
        assert client.get("/api/relay/next?tab=bad/id").status_code == 422
        with ThreadPoolExecutor(45) as pool:
            polls = [pool.submit(client.get, f"/api/relay/next?tab=t{i}") for i in range(45)]
            time.sleep(0.3)
            started = time.monotonic()
            assert client.get("/api/bootstrap").status_code == 200
            assert time.monotonic() - started < 1
            assert all(p.result().json() == {"requests": []} for p in polls)
