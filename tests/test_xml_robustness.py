"""Regression tests for the company draw.io XML feature: hostile input, spec validation, coordinate frames, roles."""
import base64
import ipaddress
import json
import subprocess
import sys
import urllib.parse
import zlib
from collections import Counter

import pytest
from defusedxml.ElementTree import fromstring
from fastapi.testclient import TestClient

from diagram_bot import api
from diagram_bot.drawio_spec import DrawioSpec, classify_cell, pages, read_document, request_spec
from diagram_bot.errors import InputError
from diagram_bot.server import create_app
from diagram_bot.store import validate_snapshot
from test_xml_align import MESSY_COMPANY_XML

VPC = "shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc2;container=1;strokeColor=#8C4FFF;fillColor=none;"
AZ = "container=1;dashed=1;strokeColor=#147EBA;fillColor=none;"
PUB = "shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#7AA116;fillColor=#F2F6E8;"
PRIV = "shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#00A4A6;fillColor=#E6F6F7;"
EC2 = "shape=mxgraph.aws4.instance2;fillColor=#ED7100;"
ALB = "shape=mxgraph.aws4.application_load_balancer;fillColor=#8C4FFF;"


def mx(cells=(), pages=None):
    pages = pages or [("p1", cells)]
    body = "".join(f'<diagram id="{n}" name="{n}"><mxGraphModel><root><mxCell id="0"/><mxCell id="1" parent="0"/>{"".join(c)}'
                   "</root></mxGraphModel></diagram>" for n, c in pages)
    return f'<mxfile host="t" compressed="false">{body}</mxfile>'


def v(cid, label, style, x, y, w, h, parent="1"):
    return (f'<mxCell id="{cid}" value="{label}" style="{style}" vertex="1" parent="{parent}">'
            f'<mxGeometry x="{x}" y="{y}" width="{w}" height="{h}" as="geometry"/></mxCell>')


def e(cid, s, t, parent="1", style="endArrow=classic;html=0;"):
    return (f'<mxCell id="{cid}" value="" style="{style}" edge="1" parent="{parent}" source="{s}" target="{t}">'
            '<mxGeometry relative="1" as="geometry"/></mxCell>')


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "data")) as c:
        c.headers["X-Diagram-Bot"] = c.get("/api/bootstrap").json()["token"]
        yield c


def first_snapshot(client):
    return client.get("/api/snapshots").json()[0]["id"]


# --- a. spec: no server files from request data, validated model --------------------------------------------------

def test_request_spec_never_reads_server_files(client, tmp_path):
    leaked = tmp_path / "leak.json"
    leaked.write_text('{"host": "LEAKED-host-value", "font_family": "Leaky"}', encoding="utf-8")
    sid = first_snapshot(client)
    for value in (str(leaked), "/etc/hosts", "/etc/does-not-exist", "/private/etc", "<mxfile/>"):
        res = client.get(f"/api/snapshots/{sid}/document", params={"spec": value})
        assert res.status_code == 422 and "LEAKED" not in res.text, value
        res = client.post("/api/xml/align", json={"xml": MESSY_COMPANY_XML, "spec": value})
        assert res.status_code == 422 and "LEAKED" not in res.text, value
    assert client.get(f"/api/snapshots/{sid}/document", params={"spec": "clean"}).status_code == 200
    with pytest.raises(InputError):
        request_spec(str(leaked))
    assert api.resolve_spec(str(leaked)).host == "LEAKED-host-value"  # trusted callers (CLI, library) still read files


@pytest.mark.parametrize("data", [
    {"cell_wrapper": "script"}, {"cell_wrapper": "a b"}, {"cell_wrapper": 3},
    {"attribute_map": {"resource": "on click"}}, {"attribute_map": {"resource": "id"}}, {"attribute_map": {"resource": "Parent"}},
    {"attribute_map": {"nonsense": "x"}}, {"resource_styles": {"ec2": 1}}, {"resource_styles": {"EC2 X": "a=1;"}},
    {"styles": {"vpc": None}}, {"styles": {"unknown_role": "a=1;"}}, {"edge_end_arrow": 5}, {"edge_end_arrow": "x;y"},
    {"margin_x": "x"}, {"margin_x": float("inf")}, {"margin_x": 10 ** 9}, {"font_size": True}, {"font_family": "Arial;shape=x"},
    {"font_color": "red;image=http://x"}, {"subnet_label_style": "tiny"}, {"include_header": "yes"}, {"base": "nope"},
    {"unknown_key": 1}, {"service_icons": {"ec2": ["instance2"]}},
])
def test_spec_validation_rejects_bad_values(data):
    with pytest.raises(InputError):
        DrawioSpec.from_dict(data)


def test_spec_validation_normalizes_and_rejects_over_http(client):
    for bad in (None, [], "x"):
        with pytest.raises(InputError):
            DrawioSpec.from_dict(bad)
    assert DrawioSpec.from_dict({"cell_wrapper": "MXCELL"}).cell_wrapper == "mxCell"
    assert DrawioSpec(cell_wrapper="userobject").cell_wrapper == "UserObject"
    sample = api.SAMPLES["three-tier"]()
    xml = api.render(sample, spec={"cell_wrapper": "mxcell", "attribute_map": None})
    assert fromstring(xml).find(".//object") is None and fromstring(xml).find(".//mxCell[@id='cloud']") is not None
    with pytest.raises(InputError):
        api.render(sample, spec={"cell_wrapper": "script"})
    for spec in ({"cell_wrapper": "a b"}, {"resource_styles": {"ec2": 1}}, {"attribute_map": {"resource": "on click"}}):
        res = client.post("/api/xml/align", json={"xml": MESSY_COMPANY_XML, "mode": "rebuild", "spec": spec})
        assert res.status_code == 422, (spec, res.text)
    spec = DrawioSpec.from_dict({"base": "clean", "font_family": "Pretendard", "resource_icons": {"ec2": ["instance2", "#ED7100"]}})
    assert DrawioSpec.from_dict(spec.to_dict()) == spec


def test_cli_spec_accepts_a_company_drawio_file(tmp_path):
    good = tmp_path / "good.drawio"
    good.write_text(MESSY_COMPANY_XML, encoding="utf-8")
    out = tmp_path / "out.drawio"
    run = subprocess.run([sys.executable, "-m", "diagram_bot.cli", "align-xml", str(good), "--spec", str(good), "-o", str(out)],
                         capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    assert fromstring(out.read_text(encoding="utf-8")).get("host") == "Company-Arch-Tool"


# --- c. one input gate: 422 / exit 2 for bad documents, compressed pages decoded --------------------------------------

def compress(model_xml):
    deflater = zlib.compressobj(9, zlib.DEFLATED, -15)
    return base64.b64encode(deflater.compress(urllib.parse.quote(model_xml).encode()) + deflater.flush()).decode()


ARCH = [v("vpc", "prod-vpc (10.2.0.0/16)", VPC, 0, 0, 500, 300), v("s", "app-subnet (10.2.1.0/24)", PRIV, 20, 50, 300, 200, "vpc"),
        v("i", "web-1", EC2, 40, 60, 52, 52, "s")]
BAD_DOCUMENTS = {
    "malformed": "<mxfile><diagram><mxGraphModel><root><mxCell id='0'/>",
    "billion-laughs": '<?xml version="1.0"?><!DOCTYPE l [<!ENTITY a "x"><!ENTITY b "&a;&a;">]><mxfile><diagram><mxGraphModel>'
                      '<root><mxCell id="0" value="&b;"/></root></mxGraphModel></diagram></mxfile>',
    "xxe": '<?xml version="1.0"?><!DOCTYPE x [<!ENTITY e SYSTEM "file:///etc/passwd">]><mxfile><diagram><mxGraphModel><root>'
           '<mxCell id="0" value="&e;"/></root></mxGraphModel></diagram></mxfile>',
    "not drawio": "<html/>",
    "no root": '<mxfile><diagram name="x"/></mxfile>',
    "bad compressed page": '<mxfile><diagram name="z">not base64 !!</diagram></mxfile>',
    "width=inf": mx([v("vpc", "vpc-x", VPC, 0, 0, 400, 300), v("sub", "app-subnet", PRIV, 10, 40, "inf", 100, "vpc")]),
    "width=nan": mx([v("i", "ec2", EC2, 0, 0, "nan", 52)]),
    "x=1e308": mx([v("i", "ec2", EC2, "1e308", "1e308", 52, 52)]),
    "x=abc": mx([v("a", "ec2", EC2, "abc", 0, 52, 52)]),
    "negative width": mx([v("a", "ec2", EC2, 0, 0, -52, 52)]),
    "waypoint=inf": mx(ARCH + [e("x", "i", "vpc").replace('as="geometry"/>', 'as="geometry"><Array as="points"><mxPoint x="inf" y="1"/>'
                                                                         '</Array></mxGeometry>')]),
    "1500-deep chain": mx([v(f"c{i}", "box", "container=1;", 1, 1, 100, 100, f"c{i - 1}") for i in range(1500, 0, -1)]
                          + [v("c0", "box", "container=1;", 0, 0, 100, 100)]),
    "parent cycle": mx([v("a", "box", "container=1;", 0, 0, 100, 100, "b"), v("b", "box", "container=1;", 0, 0, 100, 100, "a")]),
}


@pytest.mark.parametrize("name", list(BAD_DOCUMENTS))
def test_bad_documents_are_input_errors_everywhere(client, name):
    xml = BAD_DOCUMENTS[name]
    for call in (api.inspect_xml, api.align_xml, api.import_xml, lambda x: api.align_xml(x, mode="rebuild")):
        with pytest.raises(InputError):
            call(xml)
    for path, body in (("/api/xml/align", {"xml": xml}), ("/api/xml/import", {"xml": xml, "save": False}),
                       ("/api/xml/adopt", {"xml": xml, "save": False})):
        res = client.post(path, json=body)
        assert res.status_code == 422, (path, res.status_code, res.text[:200])


def test_cli_reports_bad_xml_without_a_traceback(tmp_path):
    bad = tmp_path / "bad.drawio"
    bad.write_text("<mxfile><diagram><mxGraphModel><root>", encoding="utf-8")
    for args in (["align-xml", str(bad), "-o", str(tmp_path / "o.drawio")], ["import-xml", str(bad)],
                 ["adopt-template", str(bad), "-o", str(tmp_path / "rep")]):
        run = subprocess.run([sys.executable, "-m", "diagram_bot.cli", *args], capture_output=True, text=True)
        assert run.returncode == 2, (args, run.stderr)
        assert "Traceback" not in run.stderr and "입력 오류" in run.stderr


def test_compressed_pages_are_decoded_and_written_uncompressed(client):
    model = mx(ARCH).split("<mxGraphModel>", 1)[1].rsplit("</mxGraphModel>", 1)[0]
    blob = compress(f"<mxGraphModel>{model}</mxGraphModel>")
    compressed = f'<mxfile host="app.diagrams.net" compressed="true"><diagram id="z" name="arch">{blob}</diagram></mxfile>'
    assert api.inspect_xml(compressed)["node_count"] == 1
    out = api.align_xml(compressed)
    root = fromstring(out)
    assert root.get("compressed") == "false" and blob not in out and root.find("diagram/mxGraphModel/root") is not None
    assert {i["Tags"][0]["Value"] for i in api.import_xml(compressed)["raw"]["instances"]} == {"web-1"}
    assert client.post("/api/xml/align", json={"xml": compressed}).status_code == 200
    # Stored documents stay uncompressed: the save path refuses compressed pages.
    sid = first_snapshot(client)
    revision = client.get(f"/api/snapshots/{sid}/document").json()["revision"]
    assert client.put(f"/api/snapshots/{sid}/document", json={"xml": compressed, "revision": revision}).status_code == 422


def test_zip_bombs_and_oversized_documents_are_refused():
    bomb = compress("<mxGraphModel><root>" + " " * (11 * 1024 * 1024) + "</root></mxGraphModel>")
    with pytest.raises(InputError, match="10 MiB"):
        api.inspect_xml(f'<mxfile><diagram name="b">{bomb}</diagram></mxfile>')
    with pytest.raises(InputError, match="10,000"):
        api.inspect_xml(mx([v(f"n{i}", "", "", 0, 0, 1, 1) for i in range(10_001)]))


def test_adopt_over_http_never_reads_a_server_path(client, tmp_path):
    secret = tmp_path / "company.drawio"
    secret.write_text(MESSY_COMPANY_XML, encoding="utf-8")
    res = client.post("/api/xml/adopt", json={"xml": str(secret), "save": False})
    assert res.status_code == 422


# --- b. edge waypoints live in the edge parent's frame --------------------------------------------------------------

def rendered_route(xml, edge_id):
    """Page coordinates of an edge as draw.io draws it: exit port, waypoints offset by the edge parent's origin, entry port."""
    cells = {}
    for el in fromstring(xml).iter():
        inner = el if el.tag == "mxCell" else el.find("mxCell")
        if el.tag in ("mxCell", "object", "UserObject") and inner is not None and el.get("id"):
            cells[el.get("id")] = inner

    def origin(cid):
        x = y = 0.0
        while cid in cells and cid not in ("0", "1"):
            geo = cells[cid].find("mxGeometry")
            x, y, cid = x + float(geo.get("x", 0)), y + float(geo.get("y", 0)), cells[cid].get("parent")
        return x, y

    def box(cid):
        x, y = origin(cid)
        geo = cells[cid].find("mxGeometry")
        return x, y, float(geo.get("width")), float(geo.get("height"))

    edge = cells[edge_id]
    style = dict(part.split("=", 1) for part in edge.get("style").split(";") if "=" in part)
    sx, sy, sw, sh = box(edge.get("source"))
    tx, ty, tw, th = box(edge.get("target"))
    ox, oy = origin(edge.get("parent"))
    points = [(float(p.get("x")) + ox, float(p.get("y")) + oy) for p in edge.find("mxGeometry/Array")]
    return [(sx + float(style["exitX"]) * sw, sy + float(style["exitY"]) * sh), *points,
            (tx + float(style["entryX"]) * tw, ty + float(style["entryY"]) * th)]


def test_edge_inside_a_group_is_routed_in_its_parent_frame():
    cells = [v("vpc", "vpc-a (10.0.0.0/16)", VPC, 200, 300, 600, 400),
             v("az", "ap-northeast-2a", AZ, 20, 50, 400, 330, "vpc"),
             v("pub", "public-subnet", PUB, 10, 30, 300, 120, "az"), v("app", "app-subnet", PRIV, 10, 170, 300, 140, "az"),
             v("alb", "alb", ALB, 40, 40, 52, 52, "pub"), v("ec2", "web-1", EC2, 40, 40, 52, 52, "app"),
             e("edge", "alb", "ec2", parent="vpc"), e("flat", "alb", "ec2")]
    out = api.align_xml(mx(cells))
    for edge_id in ("edge", "flat"):
        route = rendered_route(out, edge_id)
        assert all(abs(a[0] - b[0]) < 0.5 or abs(a[1] - b[1]) < 0.5 for a, b in zip(route, route[1:])), (edge_id, route)
    assert api.inspect_xml(out)["unrouted_edges"] == []


def test_inspect_reads_nested_edge_points_in_the_parent_frame():
    cells = [v("vpc", "vpc-a", VPC, 200, 300, 600, 400), v("a", "web-1", EC2, 40, 40, 52, 52, "vpc"),
             v("b", "web-2", EC2, 40, 240, 52, 52, "vpc")]
    route = ('<mxCell id="x" style="edgeStyle=orthogonalEdgeStyle;exitX=0.5;exitY=1;entryX=0.5;entryY=0;" edge="1" parent="{p}" '
             'source="a" target="b"><mxGeometry relative="1" as="geometry"><Array as="points">'
             '<mxPoint x="{x}" y="{y1}"/><mxPoint x="{x}" y="{y2}"/></Array></mxGeometry></mxCell>')
    relative = mx(cells + [route.format(p="vpc", x=66, y1=150, y2=200)])  # page x = 266, the icons' centre line
    absolute_in_group = mx(cells + [route.format(p="vpc", x=266, y1=450, y2=500)])  # page coordinates under a group: wrong
    assert api.inspect_xml(relative)["unrouted_edges"] == []
    assert api.inspect_xml(absolute_in_group)["unrouted_edges"] == ["x"]


# --- d. adopt is per request unless an admin makes it the default; roles; one audit line per mutation ----------------

@pytest.fixture
def team(tmp_path):
    from diagram_bot.auth import hash_password
    users = {name: {"name": name, "password_hash": hash_password("pw-" + name), "role": name} for name in ("viewer", "editor", "admin")}
    app = create_app(tmp_path / "data", users=users)

    def login(name):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": name, "password": "pw-" + name}).status_code == 200
        c.headers["X-Diagram-Bot"] = c.get("/api/bootstrap").json()["token"]
        return c
    try:
        yield login, tmp_path / "data" / "audit.log"
    finally:
        api.set_default_spec(None)
        for key in ("corp-team-std", "editor-std"):
            api.COMPANY_STANDARDS.pop(key, None)


MXCELL_TEMPLATE = mx([v("vpc", "corp-vpc (10.9.0.0/16)", VPC, 0, 0, 400, 300),
                      v("s", "app-subnet (10.9.1.0/24)", PRIV, 20, 50, 300, 200, "vpc"), v("i", "web-1", EC2, 40, 60, 52, 52, "s")])


def audit_actions(path, since):
    return [json.loads(line)["action"] for line in path.read_text().splitlines()[since:]]


def test_xml_routes_follow_the_snapshot_and_collection_roles(team):
    login, audit = team
    viewer, editor, admin = login("viewer"), login("editor"), login("admin")
    sid = viewer.get("/api/snapshots").json()[0]["id"]
    assert viewer.get(f"/api/snapshots/{sid}/document", params={"spec": "clean"}).status_code == 200  # read-only preview
    for path in ("/api/xml/align", "/api/xml/import", "/api/xml/adopt"):
        assert viewer.post(path, json={"xml": MESSY_COMPANY_XML, "save": False}).status_code == 403, path

    since = len(audit.read_text().splitlines())
    res = editor.post("/api/xml/import", json={"xml": MESSY_COMPANY_XML})
    assert res.status_code == 200 and res.json()["id"]
    assert audit_actions(audit, since) == ["POST /api/xml/import"]

    doc_before = viewer.get(f"/api/snapshots/{sid}/document", params={"reset": True}).json()["xml"]
    since = len(audit.read_text().splitlines())
    res = editor.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": "editor-std"})
    assert res.status_code == 200 and res.json()["default"] is False
    assert audit_actions(audit, since) == ["POST /api/xml/adopt"]
    assert api.get_default_spec() is api.DEFAULT_SPEC and "editor-std" not in api.COMPANY_STANDARDS
    assert viewer.get(f"/api/snapshots/{sid}/document", params={"reset": True}).json()["xml"] == doc_before
    assert editor.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "make_default": True}).status_code == 403
    assert api.get_default_spec() is api.DEFAULT_SPEC

    res = admin.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": "corp-team-std", "make_default": True, "save": False})
    assert res.status_code == 200 and res.json()["default"] is True
    assert api.get_default_spec().cell_wrapper == "mxCell" and "corp-team-std" in api.COMPANY_STANDARDS
    # The app's own documents keep the wrapper attributes the editor selects by; an explicit preview is the export.
    doc = viewer.get(f"/api/snapshots/{sid}/document", params={"reset": True}).json()["xml"]
    assert doc.count("resourceId=") == doc_before.count("resourceId=") > 0
    assert admin.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": "three-tier", "make_default": True}).status_code == 422
    for bad in ({"region": "../etc"}, {"name": "x" * 61}, {"title": "t" * 201}):
        assert editor.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, **bad}).status_code == 422, bad
    assert editor.post("/api/xml/import", json={"xml": MXCELL_TEMPLATE, "region": "nowhere"}).status_code == 422
    assert editor.post("/api/xml/align", json={"xml": MXCELL_TEMPLATE, "mode": "shuffle"}).status_code == 422


# --- e. a spec preview cannot be saved over the edited document ----------------------------------------------------

def test_spec_preview_is_not_saveable(client):
    sid = first_snapshot(client)
    doc = client.get(f"/api/snapshots/{sid}/document").json()
    edited = doc["xml"].replace("</root>", '<mxCell id="note-1" value="keep me" vertex="1" parent="1"><mxGeometry x="5" y="5" '
                                           'width="80" height="30" as="geometry"/></mxCell></root>', 1)
    saved = client.put(f"/api/snapshots/{sid}/document", json={"xml": edited, "revision": doc["revision"]}).json()
    preview = client.get(f"/api/snapshots/{sid}/document", params={"spec": "mxcell"}).json()
    assert preview["revision"] is None and preview["preview"] is True and "keep me" not in preview["xml"]
    assert fromstring(preview["xml"]).find(".//object") is None  # the export as the spec writes it
    res = client.put(f"/api/snapshots/{sid}/document", json={"xml": preview["xml"], "revision": preview["revision"]})
    assert res.status_code == 422
    current = client.get(f"/api/snapshots/{sid}/document").json()
    assert current["revision"] == saved["revision"] and "keep me" in current["xml"]


# --- g. carry_annotations with cells that have no id ---------------------------------------------------------------

def test_relayout_keeps_notes_and_skips_id_less_cells(client):
    sid = first_snapshot(client)
    doc = client.get(f"/api/snapshots/{sid}/document").json()
    edited = doc["xml"].replace("</root>", '<mxCell value="no id" vertex="1" parent="1"><mxGeometry width="9" height="9" as="geometry"/>'
                                           '</mxCell><UserObject label="note"><mxCell id="n1" vertex="1" parent="1"><mxGeometry '
                                           'width="9" height="9" as="geometry"/></mxCell></UserObject></root>', 1)
    saved = client.put(f"/api/snapshots/{sid}/document", json={"xml": edited, "revision": doc["revision"]}).json()
    res = client.post(f"/api/snapshots/{sid}/document/relayout", json={"revision": saved["revision"]})
    assert res.status_code == 200, res.text
    assert "no id" not in res.json()["xml"] and 'label="note"' in res.json()["xml"]
    assert fromstring(api.carry_annotations(edited, doc["xml"])).find(".//UserObject[@id='n1']/mxCell") is not None


# --- h. work grows linearly with the drawing, not with its square ----------------------------------------------------

def test_align_and_inspect_do_linear_work(monkeypatch):
    from diagram_bot import xml_align
    calls = []
    original = xml_align._boxes_overlap
    monkeypatch.setattr(xml_align, "_boxes_overlap", lambda *a, **k: calls.append(1) or original(*a, **k))
    for n in (300, 1200):
        cells = [v("vpc", "vpc-x (10.0.0.0/16)", VPC, 0, 0, 2000, 2000), v("s", "app-subnet", PRIV, 20, 50, 1900, 1900, "vpc")]
        cells += [v(f"i{k}", f"web-{k}", EC2, (k % 30) * 60, (k // 30) * 60, 52, 52, "s") for k in range(n)]
        cells += [e(f"e{k}", f"i{k}", f"i{(k * 7 + 3) % n}") for k in range(n)]
        calls.clear()
        api.inspect_xml(api.align_xml(mx(cells)))
        assert len(calls) < 200 * n, (n, len(calls))  # the pairwise scan made ~20,000 checks per shape at n=1,600


# --- f. align never returns something worse than its input; preserve keeps custom attributes -------------------------

@pytest.mark.parametrize("name", sorted(api.SAMPLES))
def test_align_on_own_output_is_a_no_op_or_an_improvement(name):
    xml = api.render(api.SAMPLES[name]())
    result = api.align_report(xml)
    for page in result["pages"]:
        assert all(page["after"][key] <= page["before"][key] for key in page["before"]), page
    assert all(page["status"] == "clean" for page in result["pages"]) and result["xml"] == xml  # no-op on own output
    assert api.align_xml(result["xml"]) == api.align_xml(api.align_xml(result["xml"]))


def test_align_reverts_a_page_it_would_make_worse(monkeypatch):
    from diagram_bot import xml_align
    xml = MESSY_COMPANY_XML

    def stack_everything(graph_root, **_):
        for el in graph_root.iter("mxGeometry"):
            if el.get("width"):
                el.set("x", "0")
                el.set("y", "0")
    monkeypatch.setattr(xml_align, "_align_page_in_place", stack_everything)
    result = api.align_report(xml)
    assert result["pages"][0]["status"] == "reverted"
    assert result["pages"][0]["after"] == result["pages"][0]["before"]
    assert api.inspect_xml(result["xml"])["overlaps"] == api.inspect_xml(xml)["overlaps"]


def test_preserve_keeps_custom_attributes_when_rewrapping():
    user_object = ('<UserObject id="u1" label="web-1 %name%" placeholders="1" tooltip="owner: team-a" link="https://wiki/x">'
                   f'<mxCell style="{EC2}" vertex="1" parent="1"><mxGeometry x="10" y="10" width="52" height="52" as="geometry"/>'
                   '</mxCell></UserObject>')
    bare = '<mxCell id="b1" value="plain" style="rounded=1;" vertex="1" parent="1"><mxGeometry x="200" y="10" width="80" height="40" as="geometry"/></mxCell>'
    tagged = ('<mxCell id="t1" value="tagged" owner="ops" style="rounded=1;" vertex="1" parent="1">'
              '<mxGeometry x="400" y="10" width="80" height="40" as="geometry"/></mxCell>')
    xml = mx([user_object, bare, tagged])
    result = api.align_report(xml, spec="mxcell")
    root = fromstring(result["xml"])
    kept = root.find(".//UserObject[@id='u1']")
    assert kept is not None and kept.get("tooltip") == "owner: team-a" and kept.get("link") == "https://wiki/x"
    assert root.find(".//mxCell[@id='b1']") is not None and result["pages"][0]["kept_wrappers"] == 1
    wrapped = fromstring(api.align_xml(xml, spec="default"))
    assert wrapped.find(".//object[@id='t1']").get("owner") == "ops"
    assert wrapped.find(".//object[@id='t1']/mxCell").get("owner") is None
    assert wrapped.find(".//object[@id='u1']").get("placeholders") == "1"


# --- j. one classification table: shape first, label words as whole words, unknown shapes are annotations ----------

@pytest.mark.parametrize("label", ["Legend", "Weekly batch job", "International users", "Albert's laptop", "Index service",
                                   "Was migrated 2024", "Corporate office", "web-was-a1"])
def test_unknown_shapes_are_annotations_not_ec2(label):
    assert classify_cell(label, "rounded=1;whiteSpace=wrap;")[0] == "annotation"


@pytest.mark.parametrize("label, style, expected", [
    ("corp-ext-alb", "rounded=1;", ("resource", "application")), ("web server (EC2)", "rounded=1;", ("resource", "ec2")),
    ("tg-web", "rounded=1;", ("resource", "target_group")), ("corp-nat-a", "rounded=1;", ("resource", "nat_gateway")),
    ("anything", "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.elastic_load_balancing;fillColor=#6D5DB3;",
     ("resource", "target_group")),
    ("anything", "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.elastic_load_balancing;fillColor=#8C4FFF;",
     ("resource", "load_balancer")),
    ("Legend", "shape=mxgraph.aws4.instance2;", ("resource", "ec2")), ("x", "shape=mxgraph.aws4.s3;", ("resource", None)),
    ("app-subnet for accounting", "container=1;", ("subnet", "subnet")), ("Finance account", "container=1;", ("account", "account")),
    ("VPC 밖 리전 리소스 · 배치 미확인", "container=1;", ("references", "references")),
    ("Corporate data center · VPC", "container=1;", ("onprem", "onprem")), ("가용 영역 Zone A", "container=1;", ("az", "az")),
    ("ap-northeast-2a", "container=1;", ("az", "az")), ("Region ap-northeast-2", "container=1;", ("region", "region")),
    ("prod-vpc · ap-northeast-2", "container=1;", ("vpc", "vpc")), ("Team boundary", "container=1;", ("group", "group")),
])
def test_classification_signals(label, style, expected):
    assert classify_cell(label, style)[:2] == expected


def test_subnet_tiers_match_layout_tier_rank_on_own_output():
    from diagram_bot.layout import tier_rank
    from diagram_bot.xml_align import _parse_page_model
    checked = 0
    for name in ("three-tier", "microservices", "eks-platform", "hybrid", "data-platform"):
        snapshot = api.SAMPLES[name]()
        nodes = {n["id"]: n for n in api.build_graph(snapshot)["nodes"]}
        for _, page in pages(read_document(api.render(snapshot))):
            for cell in _parse_page_model(page)[0].values():
                node = nodes.get(cell["wrapper"].get("resourceId"))
                if node and node["kind"] == "subnet":
                    assert cell["role"] == "subnet" and cell["tier"] == tier_rank(node), (name, cell["label"])
                    checked += 1
    assert checked > 30


def test_own_hybrid_page_classifies_like_the_graph():
    from diagram_bot.xml_align import _parse_page_model
    snapshot = api.SAMPLES["hybrid"]()
    graph = api.build_graph(snapshot)
    vpc = next(n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc")
    cells = _parse_page_model(fromstring(api.render(snapshot, view="detail", vpc=vpc)).find("diagram/mxGraphModel/root"))[0]
    roles = [c["role"] for c in cells.values()]
    assert roles.count("vpc") == 1 and roles.count("references") == 1 and roles.count("onprem") == 1
    kinds = {c["wrapper"].get("resourceId"): c["kind"] for c in cells.values() if c["role"] == "resource"}
    truth = {n["id"]: n["kind"] for n in graph["nodes"]}
    assert all(kind == "target_group" for rid, kind in kinds.items() if truth.get(rid) == "target_group")


def test_spec_extraction_learns_only_what_the_template_shows():
    template = mx([v("vpc", "vpc-a (10.0.0.0/16)", VPC, 0, 0, 600, 400), v("p", "public-a (10.0.1.0/24)", PUB, 20, 40, 200, 120, "vpc"),
                   v("q", "app-subnet for accounting (10.0.2.0/24)", PRIV, 240, 40, 200, 120, "vpc")])
    spec = api.spec_from_xml(template)
    assert spec.isolated_subnet_distinct is False and spec.styles["private_subnet"] == PRIV and spec.styles["account"] != PRIV
    isolated = "shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#3F51B5;fillColor=#ECEFF8;"
    two_pages = mx(pages=[("cover", [v("t", "Cover", "text;", 0, 0, 100, 20)]),
                          ("arch", [v("vpc", "vpc-a", VPC, 0, 0, 600, 400), v("d", "data-a", isolated, 20, 40, 200, 120, "vpc")])])
    spec = api.spec_from_xml(two_pages)
    assert spec.isolated_subnet_distinct is True and spec.styles["vpc"] == VPC


# --- i. every page: inspect, import and align; own multi-page output round-trips --------------------------------------

def test_multi_page_inspect_and_import_skip_the_cover_page():
    xml = mx(pages=[("cover", [v("t", "Cover", "text;", 0, 0, 200, 40), v("l", "Legend", "rounded=1;", 0, 60, 120, 40)]),
                    ("arch", ARCH)])
    report = api.inspect_xml(xml)
    assert report["node_count"] == 1 and [p["aws"] for p in report["pages"]] == [False, True]
    snapshot = api.import_xml(xml)
    assert [x["CidrBlock"] for x in snapshot["raw"]["vpcs"]] == ["10.2.0.0/16"]
    assert [i["Tags"][0]["Value"] for i in snapshot["raw"]["instances"]] == ["web-1"]
    meta = snapshot["metadata"]
    assert meta["imported"] is True and [p["imported"] for p in meta["import"]["pages"]] == [False, True]
    assert meta["import"]["cells"] == {"vpc": "vpc-prod-vpc", "s": snapshot["raw"]["subnets"][0]["SubnetId"],
                                       "i": snapshot["raw"]["instances"][0]["InstanceId"]}


@pytest.mark.parametrize("name", sorted(api.SAMPLES))
def test_own_multi_page_output_round_trips(name):
    snapshot = api.SAMPLES[name]()
    original_nodes = api.build_graph(snapshot)["nodes"]
    original = Counter(n["kind"] for n in original_nodes)
    imported = api.import_xml(api.render(snapshot))
    got = Counter(n["kind"] for n in imported["graph"]["nodes"])
    for kind in ("vpc", "subnet", "ec2", "load_balancer", "target_group", "nat_gateway", "internet_gateway"):
        assert got[kind] == original[kind], (kind, original[kind], got[kind])
    for kind in ("vpc", "subnet", "ec2"):  # Diagram Bot's own cells keep their AWS ids through resourceId
        assert {n["resource_id"] for n in imported["graph"]["nodes"] if n["kind"] == kind} == \
            {n["resource_id"] for n in original_nodes if n["kind"] == kind}
    for subnet in imported["raw"]["subnets"]:
        vpc = next(x for x in imported["raw"]["vpcs"] if x["VpcId"] == subnet["VpcId"])
        assert ipaddress.ip_network(subnet["CidrBlock"]).subnet_of(ipaddress.ip_network(vpc["CidrBlock"]))
    cells = imported["metadata"]["import"]["cells"]
    raw_ids = {x["VpcId"] for x in imported["raw"]["vpcs"]} | {x["SubnetId"] for x in imported["raw"]["subnets"]}
    assert raw_ids <= set(cells.values())
    validate_snapshot(imported)
    api.render(imported)


def test_import_az_names_and_fallback_cidrs():
    cells = [v("vpc", "vpc-x (10.1.0.0/16)", VPC, 0, 0, 700, 400),
             v("za", "가용 영역 Zone A", AZ, 20, 40, 300, 300, "vpc"), v("zb", "가용 영역 Zone B", AZ, 340, 40, 300, 300, "vpc"),
             v("sa", "app-subnet-1", PRIV, 10, 40, 200, 120, "za"), v("sb", "app-subnet-2 (192.168.9.0/24)", PRIV, 10, 40, 200, 120, "zb"),
             v("pa", "public-1 (10.1.7.0/24)", PUB, 10, 180, 200, 100, "za")]
    subnets = {s["Tags"][0]["Value"]: s for s in api.import_xml(mx(cells))["raw"]["subnets"]}
    assert subnets["app-subnet-1"]["AvailabilityZone"] == "ap-northeast-2a" and subnets["app-subnet-2"]["AvailabilityZone"] == "ap-northeast-2b"
    assert subnets["public-1"]["CidrBlock"] == "10.1.7.0/24"
    vpc = ipaddress.ip_network("10.1.0.0/16")
    assert all(ipaddress.ip_network(s["CidrBlock"]).subnet_of(vpc) for s in subnets.values())
    assert len({s["CidrBlock"] for s in subnets.values()}) == 3
    # AZ names that are not AZs of the region fall back to the region's AZs in column order.
    tokyo = [v("vpc", "vpc-t (10.3.0.0/16) ap-northeast-1", VPC, 0, 0, 700, 400),
             v("za", "us-east-1b", AZ, 20, 40, 300, 300, "vpc"), v("zb", "AZ b", AZ, 340, 40, 300, 300, "vpc"),
             v("sa", "app-1", PRIV, 10, 40, 200, 120, "za"), v("sb", "app-2", PRIV, 10, 40, 200, 120, "zb")]
    imported = api.import_xml(mx(tokyo))
    assert imported["metadata"]["region"] == "ap-northeast-1"
    assert sorted(s["AvailabilityZone"] for s in imported["raw"]["subnets"]) == ["ap-northeast-1a", "ap-northeast-1c"]


def test_import_reports_unmapped_shapes_instead_of_inventing_resources():
    cells = ARCH + [v("leg", "Legend", "rounded=1;", 600, 0, 120, 40), v("s3", "logs", "shape=mxgraph.aws4.s3;", 40, 140, 52, 52, "s"),
                    e("x", "i", "s3"), '<mxCell id="lbl" value="HTTPS" style="edgeLabel;" vertex="1" connectable="0" parent="x">'
                    '<mxGeometry x="-0.2" relative="1" as="geometry"><mxPoint as="offset"/></mxGeometry></mxCell>']
    snapshot = api.import_xml(mx(cells))
    assert [i["Tags"][0]["Value"] for i in snapshot["raw"]["instances"]] == ["web-1"]
    assert snapshot["metadata"]["import"]["unmapped"] == [{"page": "p1", "id": "s3", "label": "logs", "kind": None}]
    assert not snapshot["raw"]["nat_gateways"]  # no NAT gateway is invented for private subnets
