"""Offline acceptance check of a ported subset: python -m diagram_bot.port_check --level N [--json].

--level N runs the checks of levels 0..N. A check of level N imports only modules of that level's closure, the files
scripts/port_copy.py copies for it (tests/test_port_kit.py runs this against each copy). No AWS call, no network:
AWS answers come from a botocore before-call handler or a fake browser tab, and the stores live in a temp directory.
"""
import argparse
import atexit
import base64
import functools
import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import time
from pathlib import Path

LEVELS = (0, 1, 2, 3, 4)
ACCOUNT, REGION = "111122223333", "ap-northeast-2"
CHECKS = []  # (level, name, function returning a detail string)


class Skip(Exception):
    """The check needs an optional package; it is reported, not failed."""


def check(level, name):
    def register(fn):
        CHECKS.append((level, name, fn))
        return fn
    return register


def ids(xml):
    from .drawio_spec import read_document
    return {e.get("id") for e in read_document(xml).iter() if e.get("id")}


def issues(report):
    from .xml_align import ISSUES
    return sum(len(report[key]) for key in ISSUES)


# --- Level 0: draw.io XML alignment and the company spec -----------------------------------------------------------
@functools.cache
def sample():
    from .drawio import generate_document
    from .graph import build_graph
    from .samples import SAMPLES
    snapshot = SAMPLES["three-tier"]()
    snapshot["graph"] = build_graph(snapshot)
    vpc = snapshot["raw"]["vpcs"][0]["VpcId"]
    return snapshot, vpc, generate_document(snapshot, vpc, view="detail")


@check(0, "render-sample")
def render_sample():
    from .drawio import validate_document
    _, vpc, xml = sample()
    validate_document(xml)
    return f"three-tier detail {vpc}: {len(xml):,} chars"


@check(0, "inspect")
def inspect():
    from .xml_align import inspect_drawio_xml
    report = inspect_drawio_xml(sample()[2])
    assert report["node_count"] > 0 and report["pages"], report
    return f"{report['node_count']} resources, {report['container_count']} containers, {issues(report)} issues"


@check(0, "align-preserve")
def align_preserve():
    from .drawio import validate_document
    from .xml_align import align_drawio_xml, inspect_drawio_xml
    xml = sample()[2]
    out = align_drawio_xml(xml, mode="preserve")
    validate_document(out)
    before, after = issues(inspect_drawio_xml(xml)), issues(inspect_drawio_xml(out))
    assert after <= before, (before, after)
    assert ids(xml) <= ids(out), "preserve mode dropped cell ids"
    return f"bot output: issues {before} -> {after}, ids kept"


@check(0, "align-fixes-overlap")
def align_fixes_overlap():
    from xml.etree.ElementTree import tostring
    from .drawio_spec import read_document
    from .xml_align import align_report, inspect_drawio_xml
    root = read_document(sample()[2])
    icons = [c.find("mxGeometry") for c in root.iter("mxCell") if "mxgraph.aws4" in (c.get("style") or "") and c.find("mxGeometry") is not None]
    icons[1].set("x", icons[0].get("x"))
    icons[1].set("y", icons[0].get("y"))
    broken = tostring(root, encoding="unicode")
    before = inspect_drawio_xml(broken)
    result = align_report(broken, mode="preserve")
    after = inspect_drawio_xml(result["xml"])
    assert before["overlaps"] and not after["overlaps"] and issues(after) < issues(before), (issues(before), issues(after))
    return f"overlapped copy: issues {issues(before)} -> {issues(after)}, pages {[p['status'] for p in result['pages']]}"


@check(0, "spec-from-xml")
def spec_from_sample():
    from .drawio import generate_document, validate_document
    from .drawio_spec import spec_from_xml
    snapshot, vpc, xml = sample()
    spec = spec_from_xml(xml)
    validate_document(generate_document(snapshot, vpc, view="detail", spec=spec))
    return f"wrapper={spec.cell_wrapper}, {len(spec.to_dict())} fields, re-render valid"


@check(0, "import-roundtrip")
def import_roundtrip():
    from .xml_align import import_drawio_xml
    snapshot, _, xml = sample()
    imported = import_drawio_xml(xml)
    counts = lambda s: (len(s["raw"]["vpcs"]), len(s["raw"]["subnets"]))
    assert counts(imported) == counts(snapshot), (counts(imported), counts(snapshot))
    return "vpcs={} subnets={}".format(*counts(imported))


# --- Level 1: Design, review, CloudFormation, diff, combine ---------------------------------------------------------
@functools.cache
def designs():
    from .designer import Design
    d = Design(title="port-check", region=REGION)
    d.apply_standard("three-tier")
    before = d.snapshot()
    added = d.add_cache(vpc=d.describe()["vpcs"][0]["name"], name="session-redis")
    return d, before, d.snapshot(), added


@check(1, "design-standard")
def design_standard():
    d = designs()[0]
    described = d.describe()
    assert described["standard"] == "three-tier" and described["resources"]["vpc"] == 1
    return f"three-tier: {sum(described['resources'].values())} resources"


@check(1, "design-step")
def design_step():
    added = designs()[3]
    assert added["cache"] == "session-redis", added
    return f"add_cache -> {added}"


@check(1, "review")
def review_design():
    from .findings import review, summary
    d, _, after, _ = designs()
    result = d.review()
    counts = summary(review(d.graph(), after.get("coverage", [])))
    assert set(counts) >= {"high", "medium", "low"}, counts
    return f"Design.review keys {sorted(result)}, findings {counts}"


@check(1, "render-spec")
def render_spec():
    from .drawio import carry_annotations, generate_document, validate_document
    after = designs()[2]
    vpc = after["raw"]["vpcs"][0]["VpcId"]
    xml = generate_document(after, vpc, view="detail", spec="clean")
    validate_document(carry_annotations(xml, xml))
    return f"spec=clean detail {len(xml):,} chars, carry_annotations ok"


@check(1, "iac-template")
def iac_template():
    from .iac import template
    cfn = template(designs()[2])
    assert cfn["AWSTemplateFormatVersion"] and cfn["Resources"]
    return f"{len(cfn['Resources'])} CloudFormation resources"


@check(1, "diff")
def diff_designs():
    from .diff import diff
    _, before, after, _ = designs()
    changes = diff(before, after)["summary"]
    assert changes["added"] > 0 and not changes["removed"], changes
    return f"{changes}"


@check(1, "combine")
def combine():
    from .combine import combine_snapshots
    from .designer import Design
    from .graph import build_graph
    other = Design(title="edge", region="us-east-1")
    other.add_vpc("edge", cidr="10.20.0.0/16", azs=2)
    combined = combine_snapshots([designs()[2], other.snapshot()], "port-check")
    vpcs = sum(1 for n in build_graph(combined)["nodes"] if n["kind"] == "vpc")
    assert len(combined["metadata"]["parts"]) == 2 and vpcs == 2, vpcs
    return f"2 parts, {vpcs} vpcs"


# --- Level 2: AWS access, offline collection, browser egress, stores -------------------------------------------------
def static_session():
    from .sessions import credentials_session
    return credentials_session({"AccessKeyId": "testing", "SecretAccessKey": "testing", "SessionToken": "testing"}, REGION)


@check(2, "session-factory")
def session_factory():
    from .sessions import refreshing_session

    def fetch():
        raise AssertionError("refreshing_session fetched credentials before first use")
    assert static_session().region_name == REGION
    assert refreshing_session(fetch, REGION).region_name == REGION
    return "credentials_session and refreshing_session build without an AWS call"


@check(2, "collect-offline")
def collect_offline():
    from botocore.awsrequest import AWSResponse
    from . import api
    calls = []

    def answer(model, **_):
        calls.append(model.name)
        body = {"Account": ACCOUNT, "Arn": f"arn:aws:iam::{ACCOUNT}:user/port-check", "UserId": "port-check"} if model.name == "GetCallerIdentity" else {}
        return AWSResponse("https://offline.invalid", 200, {}, None), body

    def prepare(session):  # the hook browser egress uses (relay.attach); here it answers every call offline
        session.events.register("before-call", answer)
        return session
    snapshot = api.collect_snapshot(static_session(), REGION, expected_account=ACCOUNT, prepare=prepare)
    statuses = {c["status"] for c in snapshot["coverage"]}
    assert snapshot["metadata"]["account_id"] == ACCOUNT and statuses == {"complete"}, statuses
    api.render(snapshot)
    api.review_snapshot(snapshot)
    return f"{len(calls)} answered calls, {len(snapshot['coverage'])} coverage rows complete, api.render/review ok"


@check(2, "aws-access")
def aws_access():
    from .aws_access import AwsAccess
    from .egress import BrowserRelay
    server, browser = AwsAccess("server"), AwsAccess("browser")
    assert server.relay is None and server.prepare_for("alice") is None and server.via() == "sdk"
    assert isinstance(browser.relay, BrowserRelay) and browser.via("Viewer") == "sso:Viewer/browser"
    try:
        AwsAccess("proxy")
    except ValueError:
        return "server / browser egress, invalid egress refused"
    raise AssertionError("AwsAccess accepted egress='proxy'")


@check(2, "browser-relay")
def browser_relay():
    from .egress import BrowserRelay, RelayDisconnected
    relay, seen, stop = BrowserRelay(timeout=10, idle=5, poll=1, tick=0.05), [], threading.Event()
    try:
        relay.attach(static_session(), "nobody").client("sts").get_caller_identity()
        raise AssertionError("a request without an open tab was sent")
    except RelayDisconnected:
        pass
    xml = (f'<GetCallerIdentityResponse xmlns="https://sts.amazonaws.com/doc/2011-06-15/"><GetCallerIdentityResult>'
           f"<Arn>arn:aws:iam::{ACCOUNT}:user/relay</Arn><UserId>relay</UserId><Account>{ACCOUNT}</Account></GetCallerIdentityResult>"
           "<ResponseMetadata><RequestId>port-check</RequestId></ResponseMetadata></GetCallerIdentityResponse>")

    def tab():  # what static/js/relay.js does: poll, send the signed request, post the answer
        while not stop.is_set():
            for item in relay.next("alice", wait=0.2, tab="port-check"):
                seen.append(item)
                relay.answer("alice", item["id"], {"status": 200, "headers": {"content-type": "text/xml"}, "body": base64.b64encode(xml.encode()).decode()})
    thread = threading.Thread(target=tab, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while not relay.connected("alice"):
            assert time.monotonic() < deadline, "fake tab did not connect"
            time.sleep(0.01)
        identity = relay.attach(static_session(), "alice").client("sts").get_caller_identity()
    finally:
        stop.set()
        thread.join()
    headers = {k.lower() for k in seen[0]["headers"]}
    assert identity["Account"] == ACCOUNT and "authorization" in headers and seen[0]["url"].startswith("https://sts."), seen[0]
    return f"no tab -> RelayDisconnected; tab relayed a server-signed {seen[0]['method']} {seen[0]['url']}"


@check(2, "stores")
def stores():
    from .samples import SAMPLES
    from .store import Conflict, DocumentStore, SnapshotStore
    with tempfile.TemporaryDirectory() as directory:
        snapshots, documents = SnapshotStore(directory), DocumentStore(directory)
        identifier = snapshots.save(SAMPLES["three-tier"](), "sample-three-tier")
        assert snapshots.get(identifier)["graph"]["nodes"] and [r["id"] for r in snapshots.list()] == [identifier]
        scope = ("detail", "vpc-a", None, False, None)
        saved, _ = documents.replace(identifier, scope, 0, "<mxfile/>")
        saved, history = documents.replace(identifier, scope, saved["revision"], "<mxfile><diagram/></mxfile>")
        try:
            documents.replace(identifier, scope, 0, "<mxfile/>")
            raise AssertionError("a stale revision was saved")
        except Conflict:
            pass
    return f"SnapshotStore save/get/list; DocumentStore revision {saved['revision']}, {len(history)} archived, stale -> Conflict"


# --- Level 3: routers in a company FastAPI app ----------------------------------------------------------------------
def needs_test_client():
    if importlib.util.find_spec("httpx2") is None:
        raise Skip("httpx2 (test extra: pip install 'httpx2>=2.13,<3') is not installed; Starlette TestClient needs it")


@functools.cache
def company_app():
    """A fresh FastAPI app as a company would build it: its middleware sets request.state.user/role and applies
    auth.allowed (the viewer rule lives in server.create_app's middleware, not in the routers)."""
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    from starlette.testclient import TestClient
    from .auth import AuditLog, allowed
    from .aws_access import AwsAccess
    from .routes import Context, aws, documents, install_error_handlers, snapshots
    from .samples import SAMPLES
    from .store import DocumentStore, SnapshotStore
    directory = Path(tempfile.mkdtemp(prefix="port-check-"))
    atexit.register(shutil.rmtree, directory, True)
    context = Context(snapshots=SnapshotStore(directory), documents=DocumentStore(directory), aws=AwsAccess("server"),
                      audit=AuditLog(directory / "audit.log"))
    context.snapshots.save(SAMPLES["three-tier"](), "sample-three-tier")
    app = FastAPI()

    @app.middleware("http")
    async def company_auth(request, call_next):
        request.state.user = request.headers.get("x-user", "alice")
        request.state.role = request.headers.get("x-role", "editor")
        if request.headers.get("x-enforce", "1") == "1" and not allowed(request.state.role, request.method, request.url.path.removeprefix("/diagram")):
            return JSONResponse({"detail": "forbidden"}, status_code=403)
        return await call_next(request)
    install_error_handlers(app)
    for router in (snapshots.router(context), documents.router(context), aws.router(context)):
        app.include_router(router, prefix="/diagram")
    return TestClient(app), context


def document_url():
    return "/diagram/api/snapshots/sample-three-tier/document"


def vpc_params(context):
    return {"view": "detail", "vpc": context.snapshots.get("sample-three-tier")["raw"]["vpcs"][0]["VpcId"]}


@check(3, "routers-snapshots")
def routers_snapshots():
    needs_test_client()
    client = company_app()[0]
    rows = client.get("/diagram/api/snapshots").json()
    assert [r["id"] for r in rows] == ["sample-three-tier"], rows
    return "GET /diagram/api/snapshots -> 1 snapshot"


@check(3, "document-revision")
def document_revision():
    needs_test_client()
    client, context = company_app()
    params = vpc_params(context)
    doc = client.get(document_url(), params=params).json()
    saved = client.put(document_url(), params=params, json={"xml": doc["xml"], "revision": doc["revision"]})
    assert saved.status_code == 200 and saved.json()["revision"] == doc["revision"] + 1, saved.text
    reread = client.get(document_url(), params=params).json()
    assert reread["revision"] == saved.json()["revision"]
    stale = client.put(document_url(), params=params, json={"xml": doc["xml"], "revision": doc["revision"]})
    assert stale.status_code == 409, stale.text
    return f"GET rev {doc['revision']} -> PUT rev {reread['revision']} -> stale PUT 409"


@check(3, "xml-align")
def xml_align_endpoint():
    needs_test_client()
    client, context = company_app()
    xml = client.get(document_url(), params=vpc_params(context)).json()["xml"]
    aligned = client.post("/diagram/api/xml/align", json={"xml": xml})
    assert aligned.status_code == 200 and set(aligned.json()) == {"xml", "before", "after", "pages"}, aligned.text
    path_spec = client.post("/diagram/api/xml/align", json={"xml": xml, "spec": "/etc/hosts"}).status_code
    not_xml = client.post("/diagram/api/xml/align", json={"xml": "/etc/passwd"}).status_code
    assert path_spec == not_xml == 422, (path_spec, not_xml)
    return "POST /api/xml/align 200; spec path 422, path as xml 422 (request_spec / read_document)"


@check(3, "roles")
def roles():
    needs_test_client()
    client, context = company_app()
    params, viewer = vpc_params(context), {"x-role": "viewer"}
    doc = client.get(document_url(), params=params, headers=viewer).json()
    body = {"xml": doc["xml"], "revision": doc["revision"]}
    enforced = client.put(document_url(), params=params, json=body, headers=viewer).status_code
    routers_only = client.put(document_url(), params=params, json=body, headers={**viewer, "x-enforce": "0"}).status_code
    adopt = client.post("/diagram/api/xml/adopt", json={"xml": doc["xml"], "make_default": True, "save": False},
                        headers={"x-role": "editor"}).status_code
    assert (enforced, routers_only, adopt) == (403, 200, 403), (enforced, routers_only, adopt)
    return "viewer PUT 403 only via auth.allowed in the company middleware (routers alone: 200); editor make_default 403 (router)"


# --- Level 4: chat agent tools ---------------------------------------------------------------------------------------
@check(4, "workspace")
def workspace():
    from .agent import Workspace
    saved = []

    def no_aws(region):
        raise AssertionError("Workspace called AWS")
    w = Workspace(lambda snapshot: saved.append(snapshot) or f"s{len(saved)}", session_factory=no_aws, region=REGION)
    w.new_design("port-check")
    result = w.publish_design(w.design.add_vpc("main", azs=2))
    assert result["snapshot_id"] == "s1" and saved[0]["metadata"]["design"] is True, result
    return "Workspace + Design.add_vpc + publish_design without strands or AWS"


@check(4, "build-tools")
def build_tools_schemas():
    if importlib.util.find_spec("strands") is None:
        raise Skip("strands-agents (agent extra) is not installed: register designer.Design methods in the company agent loop instead")
    from .agent import Workspace, build_tools
    specs = [t.tool_spec for t in build_tools(Workspace(lambda snapshot: "x"))]
    names = [s["name"] for s in specs]
    assert len(names) == len(set(names)) == 29, names
    assert all(s["description"].strip() and s["inputSchema"]["json"]["type"] == "object" for s in specs)
    return f"{len(specs)} tools with descriptions and JSON schemas, no Bedrock call"


@check(4, "agent-router")
def agent_router():
    needs_test_client()
    from .routes import agent
    client, context = company_app()
    client.app.include_router(agent.router(context), prefix="/diagram")
    info = client.get("/diagram/api/agent").json()
    assert info["default"] in {m["key"] for m in info["models"]}, info
    return f"GET /api/agent ready={info['ready']} default={info['default']} (no Bedrock call)"


# --- runner ------------------------------------------------------------------------------------------------------------
def offline_environment():
    """No profile, shared file or instance metadata can turn a check into a real AWS call."""
    for name in ("AWS_PROFILE", "AWS_DEFAULT_PROFILE", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN"):
        os.environ.pop(name, None)
    os.environ.update(AWS_CONFIG_FILE=os.devnull, AWS_SHARED_CREDENTIALS_FILE=os.devnull, AWS_EC2_METADATA_DISABLED="true")


def run(level):
    results = []
    for check_level, name, fn in CHECKS:
        if check_level > level:
            continue
        try:
            row = {"status": "PASS", "detail": fn()}
        except Skip as exc:
            row = {"status": "SKIP", "detail": str(exc)}
        except Exception as exc:  # noqa: BLE001 - every failure is one reported line, the other checks still run
            row = {"status": "FAIL", "detail": f"{type(exc).__name__}: {str(exc)[:300]}"}
        results.append({"level": check_level, "name": name, **row})
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(prog="python -m diagram_bot.port_check", description=__doc__.splitlines()[0])
    parser.add_argument("--level", type=int, choices=LEVELS, required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    offline_environment()
    results = run(args.level)
    package = Path(__file__).resolve().parent
    modules = {name: getattr(module, "__file__", None) for name, module in sorted(sys.modules.items()) if name.split(".")[0] == __package__}
    ok = all(r["status"] != "FAIL" for r in results)
    if args.json:
        print(json.dumps({"level": args.level, "ok": ok, "package": str(package), "modules": modules, "checks": results}, ensure_ascii=False, indent=1))
    else:
        for r in results:
            print(f"{r['status']} L{r['level']} {r['name']}: {r['detail']}")
        counts = {s: sum(r["status"] == s for r in results) for s in ("PASS", "SKIP", "FAIL")}
        print(f"port_check level {args.level}: {counts['PASS']} passed, {counts['SKIP']} skipped, {counts['FAIL']} failed ({package})")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
