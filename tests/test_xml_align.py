"""Tests for DrawioSpec customization, spec extraction, in-place XML auto-alignment, and XML-to-Snapshot import."""
import json
import subprocess
import sys

from fastapi.testclient import TestClient

from diagram_bot import api
from diagram_bot.drawio import validate_document
from diagram_bot.server import create_app
from diagram_bot.store import validate_snapshot

MESSY_COMPANY_XML = """<mxfile host="Company-Arch-Tool" version="2" compressed="false">
  <diagram id="company-1" name="Company Standard 3-Tier">
    <mxGraphModel dx="1200" dy="800" grid="1" gridSize="10" page="0" background="#ffffff">
      <root>
        <mxCell id="0"/>
        <mxCell id="1" parent="0"/>
        <mxCell id="vpc-corp" value="corp-prod-vpc (10.20.0.0/16)"
          style="shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_vpc2;container=1;strokeColor=#8C4FFF;fillColor=none;fontFamily=Pretendard;" vertex="1" parent="1">
          <mxGeometry x="40" y="40" width="420" height="300" as="geometry"/>
        </mxCell>
        <mxCell id="az-a" value="ap-northeast-2a" style="container=1;dashed=1;strokeColor=#147EBA;fillColor=none;fontFamily=Pretendard;" vertex="1" parent="vpc-corp">
          <mxGeometry x="20" y="60" width="180" height="220" as="geometry"/>
        </mxCell>
        <mxCell id="az-b" value="ap-northeast-2b" style="container=1;dashed=1;strokeColor=#147EBA;fillColor=none;fontFamily=Pretendard;" vertex="1" parent="vpc-corp">
          <mxGeometry x="210" y="60" width="180" height="250" as="geometry"/>
        </mxCell>
        <mxCell id="sub-pub-a" value="public-subnet-a (10.20.1.0/24)"
          style="shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#7AA116;fillColor=#F2F6E8;fontFamily=Pretendard;" vertex="1" parent="az-a">
          <mxGeometry x="10" y="30" width="150" height="80" as="geometry"/>
        </mxCell>
        <mxCell id="sub-app-a" value="app-subnet-a (10.20.11.0/24)"
          style="shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#00A4A6;fillColor=#E6F6F7;fontFamily=Pretendard;" vertex="1" parent="az-a">
          <mxGeometry x="10" y="120" width="150" height="90" as="geometry"/>
        </mxCell>
        <mxCell id="sub-pub-b" value="public-subnet-b (10.20.2.0/24)"
          style="shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#7AA116;fillColor=#F2F6E8;fontFamily=Pretendard;" vertex="1" parent="az-b">
          <mxGeometry x="10" y="45" width="160" height="100" as="geometry"/>
        </mxCell>
        <mxCell id="sub-app-b" value="app-subnet-b (10.20.12.0/24)"
          style="shape=mxgraph.aws4.group;grIcon=mxgraph.aws4.group_security_group;container=1;strokeColor=#00A4A6;fillColor=#E6F6F7;fontFamily=Pretendard;" vertex="1" parent="az-b">
          <mxGeometry x="10" y="155" width="160" height="110" as="geometry"/>
        </mxCell>
        <!-- Flat parent="1" ALB visually inside vpc-corp and overlapping NAT inside sub-pub-a -->
        <mxCell id="alb-1" value="corp-ext-alb" style="shape=mxgraph.aws4.application_load_balancer;fillColor=#8C4FFF;fontFamily=Pretendard;" vertex="1" parent="1">
          <mxGeometry x="95" y="145" width="52" height="52" as="geometry"/>
        </mxCell>
        <mxCell id="nat-a" value="corp-nat-a" style="shape=mxgraph.aws4.nat_gateway;fillColor=#8C4FFF;fontFamily=Pretendard;" vertex="1" parent="sub-pub-a">
          <mxGeometry x="30" y="20" width="52" height="52" as="geometry"/>
        </mxCell>
        <!-- Overlapping EC2 instances inside sub-app-a, one sticking out of bounds -->
        <mxCell id="ec2-a1" value="web-was-a1" style="shape=mxgraph.aws4.instance2;fillColor=#ED7100;fontFamily=Pretendard;" vertex="1" parent="sub-app-a">
          <mxGeometry x="25" y="30" width="52" height="52" as="geometry"/>
        </mxCell>
        <mxCell id="ec2-a2" value="web-was-a2" style="shape=mxgraph.aws4.instance2;fillColor=#ED7100;fontFamily=Pretendard;" vertex="1" parent="sub-app-a">
          <mxGeometry x="40" y="45" width="52" height="52" as="geometry"/>
        </mxCell>
        <mxCell id="ec2-b1" value="web-was-b1" style="shape=mxgraph.aws4.instance2;fillColor=#ED7100;fontFamily=Pretendard;" vertex="1" parent="sub-app-b">
          <mxGeometry x="130" y="80" width="52" height="52" as="geometry"/>
        </mxCell>
        <mxCell id="rds-1" value="corp-postgres-primary" style="shape=mxgraph.aws4.rds_instance;fillColor=#C925D1;fontFamily=Pretendard;" vertex="1" parent="sub-app-b">
          <mxGeometry x="135" y="85" width="52" height="52" as="geometry"/>
        </mxCell>
        <!-- Unrouted edges without exitX/entryX or waypoints -->
        <mxCell id="edge-1" value="" style="endArrow=classic;html=0;strokeColor=#333333;" edge="1" parent="1" source="alb-1" target="ec2-a1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge-2" value="" style="endArrow=classic;html=0;strokeColor=#333333;" edge="1" parent="1" source="alb-1" target="ec2-b1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
        <mxCell id="edge-3" value="" style="endArrow=classic;html=0;strokeColor=#333333;" edge="1" parent="1" source="ec2-a1" target="rds-1">
          <mxGeometry relative="1" as="geometry"/>
        </mxCell>
      </root>
    </mxGraphModel>
  </diagram>
</mxfile>"""


def test_drawio_spec_presets_and_extraction():
    sample = api.SAMPLES["three-tier"]()
    vpc_id = sample["raw"]["vpcs"][0]["VpcId"]
    default_xml = api.render(sample, view="detail", vpc=vpc_id)
    root_def = validate_document(default_xml)
    assert root_def.find(".//object[@id='heading']") is not None
    assert root_def.find(".//object[@id='scope']") is not None

    # Clean enterprise spec omits heading/scope banner, adds block arrows and distinct isolated subnet colour
    clean_xml = api.render(sample, view="detail", vpc=vpc_id, spec="clean")
    root_clean = validate_document(clean_xml)
    assert root_clean.find(".//object[@id='heading']") is None
    assert root_clean.find(".//object[@id='scope']") is None
    cloud_geo = root_clean.find(".//object[@id='cloud']/mxCell/mxGeometry")
    assert float(cloud_geo.get("y")) == 24.0
    styles = [c.get("style") or "" for c in root_clean.iter("mxCell")]
    assert any("#ECEFF8" in s for s in styles)  # distinct isolated subnet fill
    assert any("endArrow=block" in s for s in styles)

    # Plain mxCell spec omits <object> wrappers
    mx_xml = api.render(sample, view="detail", vpc=vpc_id, spec="mxcell")
    root_mx = validate_document(mx_xml)
    assert root_mx.find(".//object") is None
    assert root_mx.find(".//mxCell[@id='cloud']") is not None

    # Extract spec from company XML and verify attributes (hierarchy, compact subnet labels, resource styles)
    extracted = api.spec_from_xml(MESSY_COMPANY_XML)
    assert extracted.cell_wrapper == "mxCell"
    assert extracted.include_header is False
    assert extracted.use_cloud_box is False
    assert extracted.use_region_box is False
    assert extracted.use_az_box is True
    assert extracted.subnet_label_style == "compact"
    assert extracted.font_family == "Pretendard"
    assert extracted.edge_end_arrow == "classic"
    assert {"application", "nat_gateway", "ec2", "db_instance"} <= set(extracted.resource_styles)
    roundtrip = api.DrawioSpec.from_dict(extracted.to_dict())
    assert roundtrip == extracted

    # Rendering a built-in sample with the extracted company spec omits cloud/region boxes and uses compact subnet labels
    corp_sample_xml = api.render(sample, view="detail", vpc=vpc_id, spec=extracted)
    root_corp = validate_document(corp_sample_xml)
    assert root_corp.find(".//mxCell[@id='cloud']") is None
    assert root_corp.find(".//mxCell[@id='region']") is None
    assert any("public-a (10.10.0.0/24)" == (c.get("value") or "") for c in root_corp.iter("mxCell"))


def test_align_drawio_xml_fixes_overlaps_bounds_tiers_and_routes():
    before = api.inspect_xml(MESSY_COMPANY_XML)
    assert not before["clean"]
    assert before["overlaps"]
    assert before["out_of_bounds"]
    assert before["unaligned_tiers"]
    assert before["unrouted_edges"]

    aligned = api.align_xml(MESSY_COMPANY_XML, mode="preserve")
    after = api.inspect_xml(aligned)
    assert after["overlaps"] == []
    assert after["out_of_bounds"] == []
    assert after["unaligned_tiers"] == []
    assert after["unrouted_edges"] == []
    assert after["edge_collisions"] == []
    assert after["clean"] is True

    # Original cell IDs are preserved in mode="preserve"
    root = validate_document(aligned)
    ids = {el.get("id") for el in root.find("diagram/mxGraphModel/root")}
    assert {"vpc-corp", "az-a", "az-b", "sub-pub-a", "sub-app-a", "alb-1", "ec2-a1", "ec2-a2", "rds-1", "edge-1"} <= ids


def test_import_drawio_xml_into_snapshot_design_review_and_cfn():
    snapshot = api.import_xml(MESSY_COMPANY_XML, title="사내 표준 이식")
    validate_snapshot(snapshot)
    kinds = {n["kind"] for n in snapshot["graph"]["nodes"]}
    assert {"vpc", "subnet", "load_balancer", "target_group", "ec2", "db_instance", "nat_gateway"} <= kinds

    # Imported company XML can immediately be extended with Design (brownfield), reviewed, and exported to CloudFormation
    design = api.Design()
    design.start_from_snapshot(snapshot)
    design.add_subnet_tier(vpc="corp-prod-vpc", tier="data")
    design.add_cache(vpc="corp-prod-vpc", name="corp-redis")
    extended = design.snapshot()
    assert any(n["kind"] == "cache_node" for n in api.build_graph(extended)["nodes"])
    assert "summary" in api.review_snapshot(extended)
    assert api.cloudformation(extended)["Resources"]

    # Rebuild mode produces a clean Diagram Bot layout in the company's spec
    rebuilt_xml = api.align_xml(MESSY_COMPANY_XML, mode="rebuild")
    validate_document(rebuilt_xml)


def test_adopt_company_xml_overhauls_format_and_registers_standard(tmp_path):
    spec_json = tmp_path / "corp-spec.json"
    try:
        adopted = api.adopt_xml(MESSY_COMPANY_XML, name="corp-3tier", set_default=True, save_spec=spec_json)
        assert adopted["name"] == "corp-3tier"
        assert adopted["after"]["clean"] is True
        assert spec_json.is_file()

        # Because set_default=True, Design.apply_standard("corp-3tier") + api.render(snapshot) automatically outputs in the company format!
        d = api.Design()
        d.apply_standard("corp-3tier")
        d.add_subnet_tier(vpc="corp-prod-vpc", tier="data")
        d.add_cache(vpc="corp-prod-vpc", name="corp-redis")
        snap = d.snapshot()
        vpc_id = snap["raw"]["vpcs"][0]["VpcId"]
        rendered_xml = api.render(snap, view="detail", vpc=vpc_id)
        root = validate_document(rendered_xml)
        assert root.find(".//object") is None
        assert root.find(".//mxCell[@id='cloud']") is None
        assert root.find(".//mxCell[@id='region']") is None
        assert any("fontFamily=Pretendard" in (c.get("style") or "") for c in root.iter("mxCell"))
    finally:
        api.set_default_spec(None)


def test_http_and_cli_xml_align_import_and_adopt(tmp_path):
    app = create_app(tmp_path / "data")
    try:
        with TestClient(app) as client:
            token = client.get("/api/bootstrap").json()["token"]
            headers = {"X-Diagram-Bot": token}

            res = client.post("/api/xml/align", json={"xml": MESSY_COMPANY_XML, "mode": "preserve"}, headers=headers)
            assert res.status_code == 200
            body = res.json()
            assert body["before"]["clean"] is False
            assert body["after"]["clean"] is True

            imp = client.post("/api/xml/import", json={"xml": MESSY_COMPANY_XML, "title": "Imported XML"}, headers=headers)
            assert imp.status_code == 200
            sid = imp.json()["id"]
            doc = client.get(f"/api/snapshots/{sid}/document?spec=clean").json()
            assert validate_document(doc["xml"]).find(".//object[@id='heading']") is None

            adopt = client.post(
                "/api/xml/adopt",
                json={"xml": MESSY_COMPANY_XML, "name": "corp-http-std", "set_default": False},
                headers=headers,
            )
            assert adopt.status_code == 200
            adopt_body = adopt.json()
            assert adopt_body["name"] == "corp-http-std"
            assert adopt_body["after"]["clean"] is True
            assert adopt_body["spec"]["use_cloud_box"] is False
    finally:
        api.set_default_spec(None)

    in_xml = tmp_path / "messy.drawio"
    out_xml = tmp_path / "aligned.drawio"
    snap_out = tmp_path / "imported.json"
    render_out = tmp_path / "rebuilt.drawio"
    adopt_dir = tmp_path / "adopted-report"
    in_xml.write_text(MESSY_COMPANY_XML, encoding="utf-8")

    subprocess.run([sys.executable, "-m", "diagram_bot.cli", "align-xml", str(in_xml), "-o", str(out_xml)], check=True)
    assert api.inspect_xml(out_xml.read_text(encoding="utf-8"))["clean"] is True

    subprocess.run(
        [sys.executable, "-m", "diagram_bot.cli", "import-xml", str(in_xml), "-o", str(snap_out), "--render", str(render_out), "--spec", "clean"],
        check=True,
    )
    loaded = json.loads(snap_out.read_text(encoding="utf-8"))
    validate_snapshot(loaded)
    validate_document(render_out.read_text(encoding="utf-8"))

    subprocess.run(
        [sys.executable, "-m", "diagram_bot.cli", "adopt-template", str(in_xml), "-o", str(adopt_dir), "--name", "corp-cli-std"],
        check=True,
    )
    assert (adopt_dir / "company-spec.json").is_file()
    assert (adopt_dir / "aligned.drawio").is_file()
    assert (adopt_dir / "topology.drawio").is_file()
    topo_root = validate_document((adopt_dir / "topology.drawio").read_text(encoding="utf-8"))
    assert topo_root.find(".//object") is None

