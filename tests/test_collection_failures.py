"""Collection jobs fail visibly: a lost browser fails the job, an unexpected error is logged (audit items 2.9, 2.10)."""
import base64
import logging
import threading
import time

import boto3
import pytest
from fastapi.testclient import TestClient
from test_egress import IDENTITY

from diagram_bot.collector import collect, query
from diagram_bot.egress import BrowserRelay, RelayDisconnected, RelayError
from diagram_bot.server import create_app


def wait(client, job):
    for _ in range(200):
        job = client.get(f"/api/collections/{job['id']}").json()
        if job["status"] not in ("queued", "running"):
            return job
        time.sleep(0.01)
    raise AssertionError(job)


def test_a_lost_browser_fails_the_collection_instead_of_saving_an_empty_partial_snapshot():
    relay, closed = BrowserRelay(timeout=1, idle=0.3), threading.Event()
    identity = {"status": 200, "headers": {"content-type": "text/xml"}, "body": base64.b64encode(IDENTITY).decode()}

    def page():  # answers the identity call, then the tab closes while the first service call is in flight
        while not closed.is_set():
            for request in relay.next("alice", wait=0.1):
                if request["url"].startswith("https://sts."):
                    relay.answer("alice", request["id"], identity)
                else:
                    closed.set()
    relay.next("alice", wait=0)
    threading.Thread(target=page, daemon=True).start()
    session = boto3.Session(aws_access_key_id="ASIAEXAMPLE", aws_secret_access_key="secret", aws_session_token="token", region_name="us-east-1")
    with pytest.raises(RelayDisconnected):
        collect("us-east-1", session=session, expected_account="111122223333", prepare=lambda s: relay.attach(s, "alice"))
    assert closed.is_set()


class Blocked:
    """One service the browser cannot reach (a proxy blocks it): only that service is unavailable."""

    def can_paginate(self, operation):
        return False

    def describe_vpcs(self, **params):
        raise RelayError("브라우저에서 AWS 호출이 막혔습니다 (네트워크·CORS·프록시): TypeError")


class Gone(Blocked):
    def describe_vpcs(self, **params):
        raise RelayDisconnected("브라우저 중계가 연결되어 있지 않습니다.")


def test_a_blocked_call_stays_one_unavailable_service_but_a_disconnect_propagates():
    rows, coverage = query(Blocked(), "describe_vpcs", "Vpcs")
    assert rows == [] and coverage["status"] == "unavailable" and coverage["error_code"] == "RelayError"
    with pytest.raises(RelayDisconnected):
        query(Gone(), "describe_vpcs", "Vpcs")


def test_an_unexpected_collection_failure_is_logged_with_its_traceback(tmp_path, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise KeyError("Reservations")
    monkeypatch.setattr("diagram_bot.routes.aws.collect", broken)
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor")) as client:
        client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
        with caplog.at_level(logging.ERROR, logger="diagram_bot.routes.aws"):
            job = wait(client, client.post("/api/collections", json={"region": "us-east-1", "expected_account": "111122223333"}).json())
    assert job["status"] == "failed" and job["error"].startswith("수집을 완료하지 못했습니다")
    [record] = [r for r in caplog.records if r.name == "diagram_bot.routes.aws"]
    assert record.exc_info and record.exc_info[0] is KeyError
