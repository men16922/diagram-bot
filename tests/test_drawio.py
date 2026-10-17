import time
from xml.etree import ElementTree as ET

import pytest

from diagram_bot.demo import ALB, demo_snapshot
from diagram_bot.drawio import cell_id, generate_document, layout_document, validate_document
from diagram_bot.editor import install_editor
from diagram_bot.graph import build_graph
from fixtures import scale_snapshot

VIEWS = [("overview", None, None, False), ("detail", "vpc-demo", None, False), ("detail", "vpc-demo", None, True),
         ("detail", "vpc-staging", None, False), ("detail", "vpc-empty", None, False),
         ("focus", None, "i-demo-a", True), ("focus", None, ALB, False), ("focus", None, "subnet-app-a", False)]


def boxes(layout):
    absolute = layout["absolute"]
    result = {}
    for shape in layout["shapes"]:
        x, y = absolute[shape["id"]]
        box = (x, y, x + shape["w"], y + shape["h"])
        label = layout["labels"].get(shape["id"])
        if label:
            box = (min(box[0], label[0]), min(box[1], label[1]), max(box[2], label[0] + label[2]), max(box[3], label[1] + label[3]))
        result[shape["id"]] = box
    return result


def overlap(a, b):
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def assert_readable(layout):
    """No sibling overlap, nothing clipped by its container, and no route through an icon or label."""
    shapes = {s["id"]: s for s in layout["shapes"]}
    area = boxes(layout)
    for shape in layout["shapes"]:
        if shape["parent"] != "1":
            outer, inner = area[shape["parent"]], area[shape["id"]]
            parent = layout["absolute"][shape["parent"]]
            frame = (parent[0], parent[1], parent[0] + shapes[shape["parent"]]["w"], parent[1] + shapes[shape["parent"]]["h"])
            assert frame[0] <= inner[0] and frame[1] <= inner[1] and inner[2] <= frame[2] and inner[3] <= frame[3], shape["label"]
            assert outer
    siblings = {}
    for shape in layout["shapes"]:
        siblings.setdefault(shape["parent"], []).append(shape["id"])
    for members in siblings.values():
        for i, a in enumerate(members):
            for b in members[i + 1:]:
                assert not overlap(area[a], area[b]), (shapes[a]["label"], shapes[b]["label"])
    resources = [s for s in layout["shapes"] if s["role"] == "resource"]
    for edge in layout["edges"]:
        if not edge["points"]:
            continue
        points = edge["path"]
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            assert abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01, ("not orthogonal", edge["relation"])
            segment = (min(x1, x2), min(y1, y2), max(x1, x2) + 0.01, max(y1, y2) + 0.01)
            for shape in resources:
                if shape["id"] in (edge["source"], edge["target"]):
                    continue
                x, y = layout["absolute"][shape["id"]]
                assert not overlap(segment, (x + 1, y + 1, x + shape["w"] - 1, y + shape["h"] - 1)), (edge["relation"], shape["label"])
                label = layout["labels"].get(shape["id"])
                assert not overlap(segment, (label[0] + 2, label[1] + 2, label[0] + label[2] - 2, label[1] + label[3] - 2)), (edge["relation"], shape["label"])


def segments(edge):
    for (x1, y1), (x2, y2) in zip(edge["path"], edge["path"][1:]):
        if abs(y1 - y2) < 0.01:
            yield "h", y1, min(x1, x2), max(x1, x2)
        else:
            yield "v", x1, min(y1, y2), max(y1, y2)


def assert_no_shared_segments(layout):
    """Two routes may share a segment only when they share a source (a bus) or a target."""
    routed = [e for e in layout["edges"] if e["points"]]
    for i, a in enumerate(routed):
        for b in routed[i + 1:]:
            if a["source"] == b["source"] or a["target"] == b["target"]:
                continue
            for axis, c, lo, hi in segments(a):
                for axis2, c2, lo2, hi2 in segments(b):
                    if axis == axis2 and abs(c - c2) < 1 and min(hi, hi2) - max(lo, lo2) > 1:
                        raise AssertionError((a["relation"], b["relation"], axis, c))


@pytest.mark.parametrize("view,vpc,resource,references", VIEWS)
def test_views_are_readable_and_reproducible(view, vpc, resource, references):
    snapshot = demo_snapshot()
    layout = layout_document(snapshot, view, vpc, resource, references)
    assert_readable(layout)
    assert_no_shared_segments(layout)
    assert layout_document(snapshot, view, vpc, resource, references)["shapes"] == layout["shapes"]
    xml = generate_document(snapshot, vpc, references, view, resource)
    assert xml == generate_document(snapshot, vpc, references, view, resource)
    root = validate_document(xml)
    scope = root.find(".//object[@id='scope']")
    assert scope.get("layout") == "diagram-bot-layered/2" and scope.get("view") == view


def test_native_hierarchy_and_links_are_consistent():
    snapshot = demo_snapshot()
    graph = build_graph(snapshot)
    root = validate_document(generate_document(snapshot, "vpc-demo"))
    objects = {o.attrib["id"]: o for o in root.findall(".//object")}
    ids = {"0", "1", *objects}
    for cell in root.findall(".//mxCell"):
        for attr in ("parent", "source", "target"):
            if attr in cell.attrib:
                assert cell.attrib[attr] in ids
    nodes = {n["id"]: n for n in graph["nodes"]}
    for node in graph["nodes"]:
        if node["kind"] == "ec2" and not node["reference_only"] and node["attributes"]["VpcId"] == "vpc-demo":
            subnet = next(e["target"] for e in graph["edges"] if e["source"] == node["id"] and e["relation"] == "in_subnet")
            assert objects[cell_id(node["id"])].find("mxCell").attrib["parent"] == cell_id(subnet)
        if node["kind"] in ("load_balancer", "target_group"):
            assert objects[cell_id(node["id"])].find("mxCell").attrib["parent"] == cell_id(f"{node['account']}/{node['region']}/vpc/vpc-demo")
    relations = root.findall(".//object[@relationId]")
    expected = [e for e in graph["edges"] if e["relation"] in ("registered_target", "associated_load_balancer")
                and nodes[e["source"]]["attributes"].get("VpcId") == "vpc-demo"]
    assert sorted(o.get("relationId") for o in relations) == sorted(e["id"] for e in expected)
    assert all(o.get("evidence").startswith("Describe") for o in relations)
    ports = sorted(o.get("label") for o in relations if o.get("label"))
    assert ports == [":443", ":8443"]


def test_report_document_has_overview_and_every_vpc_page():
    root = validate_document(generate_document(demo_snapshot()))
    assert [d.get("name") for d in root.findall("diagram")] == ["전체 구성", "vpc-demo", "vpc-empty", "vpc-staging"]
    overview = root.find("diagram")
    cards = overview.findall(".//object[@summary='1']")
    assert {c.get("resourceId", "").rsplit("/", 1)[-1] for c in cards} >= {"vpc-demo", "vpc-staging", "vpc-empty"}


def test_references_and_ip_targets_do_not_invent_placement():
    snapshot = demo_snapshot()
    assert "sg-web" not in generate_document(snapshot, "vpc-demo")
    assert "sg-web" in generate_document(snapshot, "vpc-demo", True)
    root = validate_document(generate_document(snapshot, "vpc-demo"))
    target = next(o for o in root.findall(".//object") if "/registered_target/" in o.get("resourceId", ""))
    assert target.get("label") == "10.20.11.25:9000"
    assert target.find("mxCell").get("parent") == "references"
    missing = next(o for o in root.findall(".//object") if o.get("resourceId", "").endswith("/ec2/i-demo-missing"))
    assert missing.find("mxCell").get("parent") == "references" and "dashed=1" in missing.find("mxCell").get("style")


def test_long_korean_names_are_truncated_with_full_tooltip():
    root = validate_document(generate_document(demo_snapshot(), "vpc-demo"))
    batch = next(o for o in root.findall(".//object") if o.get("resourceId", "").endswith("i-demo-batch"))
    assert batch.get("label").endswith("…") and batch.get("tooltip").startswith("주문 정산 야간 배치")


def test_empty_unknown_scope_and_xml_labels():
    snapshot = demo_snapshot()
    label = '<script>"& 검토</script>'
    snapshot["raw"]["instances"][0]["Tags"][0]["Value"] = label
    root = validate_document(generate_document(snapshot, "vpc-demo"))
    assert any(o.get("label") == label for o in root.findall(".//object"))
    with pytest.raises(ValueError):
        generate_document(snapshot, "vpc-missing")
    empty = validate_document(generate_document(snapshot, "vpc-empty"))
    assert any("서브넷이 없습니다" in o.get("label", "") for o in empty.findall(".//object"))
    snapshot["raw"] = {}
    assert "조회된 VPC가 없습니다" in generate_document(snapshot)
    snapshot["coverage"] = [{"service": "ec2", "operation": "describe_vpcs", "status": "unavailable", "error_code": "AccessDenied"}]
    denied = generate_document(snapshot)
    assert "빈 환경이 아니라" in denied and ET.fromstring(denied).tag == "mxfile"


def test_hundred_resource_layout_is_readable_and_fast():
    snapshot = scale_snapshot()
    assert len(build_graph(snapshot)["nodes"]) == 100
    started = time.perf_counter()
    generate_document(snapshot)
    elapsed = time.perf_counter() - started
    assert elapsed < 5, elapsed
    for vpc in ("vpc-scale-0", "vpc-scale-1"):
        layout = layout_document(snapshot, "detail", vpc, None, True)
        assert_readable(layout)
        assert_no_shared_segments(layout)


@pytest.mark.parametrize("xml", [
    "<html/>", "<mxfile><diagram>compressed</diagram></mxfile>",
    '<!DOCTYPE mxGraphModel [<!ENTITY x SYSTEM "file:///etc/passwd">]><mxGraphModel>&x;</mxGraphModel>',
    "<mxGraphModel>",
])
def test_reject_invalid_or_unsafe_xml(xml):
    with pytest.raises(Exception):
        validate_document(xml)


def test_editor_checksum_failure_keeps_existing_files(tmp_path):
    archive = tmp_path / "invalid.war"
    archive.write_bytes(b"not the official archive")
    destination = tmp_path / "editor"
    destination.mkdir()
    marker = destination / "keep.txt"
    marker.write_text("existing")
    with pytest.raises(ValueError, match="SHA-256"):
        install_editor(archive, destination)
    assert marker.read_text() == "existing"
