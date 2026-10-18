"""Regression tests for the company draw.io XML feature: hostile input, spec validation, coordinate frames, roles."""
import base64
import json
import subprocess
import sys
import urllib.parse
import zlib

import pytest
from defusedxml.ElementTree import fromstring
from fastapi.testclient import TestClient

from diagram_bot import api
from diagram_bot.drawio_spec import DrawioSpec, request_spec
from diagram_bot.errors import InputError
from diagram_bot.server import create_app
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
