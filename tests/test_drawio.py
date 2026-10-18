import time
from xml.etree import ElementTree as ET

import pytest

from diagram_bot.demo import ALB, demo_snapshot
from diagram_bot.drawio import cell_id, generate_document, layout_document, resource_style, validate_document
from diagram_bot.editor import install_editor
from diagram_bot.graph import build_graph
from diagram_bot.layout import fit_label, icon_variant, text_width
from diagram_bot.samples import SAMPLES
from fixtures import edge_snapshot, full_design, scale_snapshot

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
    assert len(shapes) == len(layout["shapes"]), "duplicate cell IDs"
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
        # Auto Scaling group frames deliberately overlay AZ columns, and account frames their VPC cards, as in AWS diagrams.
        members = [m for m in members if shapes[m]["role"] not in ("asg", "account")]
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
    """Two routes may share a segment only when they meet at a common shape (a target group's bus, whether the
    route is a registration leaving it or a group frame edge arriving at it)."""
    routed = [e for e in layout["edges"] if e["points"]]
    for i, a in enumerate(routed):
        for b in routed[i + 1:]:
            if {a["source"], a["target"]} & {b["source"], b["target"]}:
                continue
            for axis, c, lo, hi in segments(a):
                for axis2, c2, lo2, hi2 in segments(b):
                    if axis == axis2 and abs(c - c2) < 1 and min(hi, hi2) - max(lo, lo2) > 1:
                        raise AssertionError((a["relation"], b["relation"], axis, c))


# Where draw.io draws a container's caption: (left inset or None when centred, font size, line height, top inset).
# AWS group styles put their group icon left of the text, so the caption box starts at the container's edge.
CAPTIONS = {"cloud": (30, 12, 15, 4), "region": (30, 12, 15, 4), "vpc": (30, 12, 15, 4), "subnet": (30, 11, 14, 4),
            "onprem": (30, 12, 15, 4), "account": (30, 12, 15, 4), "references": (12, 12, 15, 6), "unplaced": (12, 11, 14, 6),
            "az": (None, 12, 15, 6)}


def assert_captions_clear(layout):
    """No route runs through a container's caption (group icon and title) other than its own endpoints'."""
    boxes = []
    for shape in layout["shapes"]:
        if shape["role"] not in CAPTIONS:
            continue
        left, size, line, top = CAPTIONS[shape["role"]]
        x, y = layout["absolute"][shape["id"]]
        lines = shape["label"].split("\n")
        width = max(text_width(part, size) for part in lines)
        start = x + shape["w"] / 2 - width / 2 if left is None else x + 1
        boxes.append((shape, (start, y + 1, (start + width) if left is None else x + left + width, y + top + line * len(lines))))
    for edge in layout["edges"]:
        for (x1, y1), (x2, y2) in zip(edge["path"], edge["path"][1:]):
            segment = (min(x1, x2), min(y1, y2), max(x1, x2) + 0.01, max(y1, y2) + 0.01)
            for shape, box in boxes:
                if shape["id"] not in (edge["source"], edge["target"]):
                    assert not overlap(segment, box), (edge["relation"], shape["role"], shape["label"][:40])


def assert_routed(layout):
    """Every edge carries its own orthogonal route: waypoints (or one straight segment) whose ends sit on the source and
    target borders exactly where the exit/entry constraints put them, so draw.io never routes an edge by itself."""
    shapes = {s["id"]: s for s in layout["shapes"]}
    for edge in layout["edges"]:
        path = edge["path"]
        assert len(path) >= 2 and (edge["points"] or len(path) == 2), ("auto-routed", edge["relation"])
        for (x1, y1), (x2, y2) in zip(path, path[1:]):
            assert abs(x1 - x2) < 0.01 or abs(y1 - y2) < 0.01, ("not orthogonal", edge["relation"])
        constraints = dict(part.split("=") for part in edge["constraints"].strip(";").split(";"))
        for end, point in (("exit", path[0]), ("entry", path[-1])):
            shape = shapes[edge["source" if end == "exit" else "target"]]
            x, y = layout["absolute"][shape["id"]]
            fx, fy = float(constraints[end + "X"]), float(constraints[end + "Y"])
            assert abs(x + fx * shape["w"] - point[0]) < 1 and abs(y + fy * shape["h"] - point[1]) < 1, (end, edge["relation"])
            assert fx in (0, 1) or fy in (0, 1), (end, "constraint off the border", edge["relation"])


def assert_xml_routed(xml):
    """The serialized document keeps every route: each edge cell has waypoints and fixed exit/entry points."""
    root = validate_document(xml)
    for cell in root.iter("mxCell"):
        if cell.get("edge") == "1":
            style = cell.get("style")
            assert "exitX=" in style and "entryX=" in style and "orthogonalEdgeStyle" in style
            assert cell.find("mxGeometry/Array[@as='points']/mxPoint") is not None, style[:60]


@pytest.mark.parametrize("view,vpc,resource,references", VIEWS)
def test_views_are_readable_and_reproducible(view, vpc, resource, references):
    snapshot = demo_snapshot()
    layout = layout_document(snapshot, view, vpc, resource, references)
    assert_readable(layout)
    assert_no_shared_segments(layout)
    assert_routed(layout)
    assert_captions_clear(layout)
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
    # Placed only because its IP lies in exactly one subnet CIDR of the target group's VPC, in the stated AZ;
    # it stays a separate target, never merged with the instance that has the same address.
    graph = build_graph(snapshot)
    placement = next(e for e in graph["edges"] if e["source"] == target.get("resourceId") and e["relation"] == "in_subnet")
    assert placement["evidence"] == "DescribeTargetHealth.Target.Id ∈ DescribeSubnets.CidrBlock"
    assert target.find("mxCell").get("parent") == cell_id(placement["target"])
    # AvailabilityZone=all marks an address outside the VPC: never placed in a subnet.
    for result in snapshot["raw"]["target_health"]:
        for description in result["Descriptions"]:
            if description["Target"]["Id"] == "10.20.11.25":
                description["Target"]["AvailabilityZone"] = "all"
    assert not any(e["relation"] == "in_subnet" and "/registered_target/" in e["source"] for e in build_graph(snapshot)["edges"])
    missing = next(o for o in root.findall(".//object") if o.get("resourceId", "").endswith("/ec2/i-demo-missing"))
    assert missing.find("mxCell").get("parent") == "references" and "dashed=1" in missing.find("mxCell").get("style")


def test_long_korean_names_are_truncated_with_full_tooltip():
    root = validate_document(generate_document(demo_snapshot(), "vpc-demo"))
    batch = next(o for o in root.findall(".//object") if o.get("resourceId", "").endswith("i-demo-batch"))
    assert batch.get("label").split("\n")[0].endswith("…") and batch.get("tooltip").startswith("주문 정산 야간 배치")


def test_empty_unknown_scope_and_xml_labels():
    snapshot = demo_snapshot()
    label = '<script>"& 검토</script>'
    snapshot["raw"]["instances"][0]["Tags"][0]["Value"] = label
    root = validate_document(generate_document(snapshot, "vpc-demo"))
    assert any(o.get("label", "").split("\n")[0] == label for o in root.findall(".//object"))
    with pytest.raises(ValueError):
        generate_document(snapshot, "vpc-missing")
    empty = validate_document(generate_document(snapshot, "vpc-empty"))
    assert any("서브넷이 없습니다" in o.get("label", "") for o in empty.findall(".//object"))
    snapshot["raw"] = {}
    assert "조회된 VPC가 없습니다" in generate_document(snapshot)
    snapshot["coverage"] = [{"service": "ec2", "operation": "describe_vpcs", "status": "unavailable", "error_code": "AccessDenied"}]
    denied = generate_document(snapshot)
    assert "빈 환경이 아니라" in denied and ET.fromstring(denied).tag == "mxfile"


FOCUSED = {"load_balancer", "target_group", "ecs_service", "auto_scaling_group", "customer_gateway", "vpn_gateway", "vpc_peering",
           "search_node", "db_instance", "cache_node", "redshift_cluster", "efs_mount_target", "ecs_task"}


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_best_practice_samples_are_readable(key):
    snapshot = SAMPLES[key]()
    graph = build_graph(snapshot)
    assert snapshot["metadata"]["demo"] is True and snapshot["metadata"]["practices"]
    assert_readable(layout_document(snapshot, "overview"))
    assert_routed(layout_document(snapshot, "overview"))
    for vpc in (n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc"):
        for references in (False, True):
            layout = layout_document(snapshot, "detail", vpc, None, references)
            assert_readable(layout)
            assert_no_shared_segments(layout)
            assert_routed(layout)
            assert_captions_clear(layout)
    for node in graph["nodes"]:
        if node["kind"] in FOCUSED and not node["reference_only"]:
            layout = layout_document(snapshot, "focus", None, node["id"], True)
            assert_readable(layout)
            assert_no_shared_segments(layout)


FOCUS_SOURCES = {**SAMPLES, "demo": demo_snapshot, "design": lambda: full_design().snapshot()}


@pytest.mark.parametrize("key", sorted(FOCUS_SOURCES))
def test_every_focus_document_routes_its_edges(key):
    """Focus views of every resource of every sample, with and without references: no edge is left to draw.io's
    auto-routing (placement edges included), routes stay orthogonal, clear of icons, labels and captions."""
    snapshot = FOCUS_SOURCES[key]()
    graph = build_graph(snapshot)
    placements = 0
    for node in graph["nodes"]:
        for references in (False, True):
            layout = layout_document(snapshot, "focus", None, node["id"], references, graph)
            assert_routed(layout)
            assert_readable(layout)
            assert_no_shared_segments(layout)
            assert_captions_clear(layout)
            placements += sum(e["relation"] == "in_subnet" for e in layout["edges"])
    assert placements or not any(n["kind"] == "load_balancer" for n in graph["nodes"])
    focus = next(n["id"] for n in graph["nodes"] if n["kind"] in ("load_balancer", "transit_gateway", "ec2"))
    assert_xml_routed(generate_document(snapshot, None, True, "focus", focus))


def test_focus_view_does_not_claim_missing_subnets():
    """A focus view hides the subnets its resources do not sit in; it must not say the VPC has none."""
    snapshot = SAMPLES["environments"]()
    hub = next(n for n in build_graph(snapshot)["nodes"] if n["kind"] == "transit_gateway")
    labels = [s["label"] for s in layout_document(snapshot, "focus", None, hub["id"])["shapes"]]
    assert not any("조회된 서브넷이 없습니다" in label for label in labels)
    assert sum("선택 리소스와 직접 관계된 서브넷 없음" in label for label in labels) == 4


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


def crossings(layout):
    """Points where two different routes cross at right angles."""
    parts = [(e["id"], s) for e in layout["edges"] for s in segments(e)]
    count = 0
    for i, (a, (axis, c, lo, hi)) in enumerate(parts):
        for b, (axis2, c2, lo2, hi2) in parts[i + 1:]:
            count += a != b and axis != axis2 and lo < c2 < hi and lo2 < c < hi2
    return count


def test_route_filter_keeps_dense_views_readable():
    """About 100 resources (≈40 routes, hundreds of crossings): filtered to one target group the detail view draws only
    that group's routes without a single crossing; filtered to one load balancer, a tenth of the crossings or fewer."""
    snapshot = scale_snapshot()
    graph = build_graph(snapshot)
    full = layout_document(snapshot, "detail", "vpc-scale-0", None, False, graph)
    assert crossings(full) > 300
    vpc = [n for n in graph["nodes"] if n["attributes"].get("VpcId") == "vpc-scale-0"]
    for node in (n for n in vpc if n["kind"] in ("target_group", "load_balancer")):
        layout = layout_document(snapshot, "detail", "vpc-scale-0", None, False, graph, node["id"])
        assert_readable(layout)
        assert_no_shared_segments(layout)
        assert_routed(layout)
        assert_captions_clear(layout)
        assert layout["width"] <= full["width"] and layout["height"] <= full["height"]
        if node["kind"] == "target_group":
            registrations = [e for e in graph["edges"] if e["source"] == node["id"] and e["relation"] in ("registered_target", "associated_load_balancer")]
            assert sorted(e["relation_id"] for e in layout["edges"]) == sorted(e["id"] for e in registrations)
            assert crossings(layout) == 0
        else:
            assert crossings(layout) * 10 <= crossings(full) and len(layout["edges"]) * 2 < len(full["edges"])
    # The document states the filter and keeps the off-path resources, dimmed.
    lb = next(n["id"] for n in vpc if n["name"] == "scale-0-2")
    root = validate_document(generate_document(snapshot, "vpc-scale-0", False, "detail", None, lb))
    scope = root.find(".//object[@id='scope']")
    assert scope.get("routes") == lb and "경로 필터: 로드 밸런서 scale-0-2" in scope.get("label") and "숨김" in scope.get("label")
    assert any("opacity=30" in c.get("style", "") for c in root.iter("mxCell"))
    # 300 instances and 18 target groups in one VPC: one target group's routes still never cross.
    dense = scale_snapshot(vpcs=1, instances_per_subnet=100, load_balancers=6, groups_per_lb=3)
    groups = [n for n in build_graph(dense)["nodes"] if n["kind"] == "target_group"][:3]
    for group in groups:
        layout = layout_document(dense, "detail", "vpc-scale-0", None, False, None, group["id"])
        assert_readable(layout)
        assert crossings(layout) == 0 and len(layout["edges"]) <= 20


def test_large_vpc_and_account_scale():
    """300 instances and 18 target groups in one VPC stay readable; a 2,500-resource account renders in seconds."""
    dense = scale_snapshot(vpcs=1, instances_per_subnet=100, load_balancers=6, groups_per_lb=3)
    started = time.perf_counter()
    layout = layout_document(dense, "detail", "vpc-scale-0")
    assert time.perf_counter() - started < 5
    assert_readable(layout)
    assert_no_shared_segments(layout)
    account = scale_snapshot(vpcs=50)
    graph = build_graph(account)
    assert len(graph["nodes"]) == 2500
    started = time.perf_counter()
    root = validate_document(generate_document(account))
    assert time.perf_counter() - started < 20
    assert len(root.findall("diagram")) == 51


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


def test_chat_design_is_readable():
    """Designs built by the chat agent's tools pass the same overlap, containment and routing checks as samples."""
    snapshot = full_design().snapshot()
    graph = build_graph(snapshot)
    assert_readable(layout_document(snapshot, "overview"))
    assert_routed(layout_document(snapshot, "overview"))
    for vpc in (n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc"):
        for references in (False, True):
            layout = layout_document(snapshot, "detail", vpc, None, references)
            assert_readable(layout)
            assert_no_shared_segments(layout)
            assert_routed(layout)
            assert_captions_clear(layout)
    for node in graph["nodes"]:
        if node["kind"] in FOCUSED and not node["reference_only"]:
            layout = layout_document(snapshot, "focus", None, node["id"], True)
            assert_readable(layout)
            assert_no_shared_segments(layout)


def test_truncated_label_ends_at_a_word():
    """A cut name ends on a whole word before the ellipsis, so it cannot run into the type line below."""
    name = "주문 정산 야간 배치 처리 서버 (월말 대사 작업 전용 인스턴스)"
    assert fit_label(name, 140, 3) == "주문 정산 야간 배치 처리 서버 (월말 대사 작업 전용…"
    assert fit_label("platform-orders-api-writer-primary-cluster", 140, 1).endswith("…")
    assert fit_label("short name", 140, 1) == "short name"


@pytest.mark.parametrize("instance_type, shape", [
    ("p4d.24xlarge", "p4d_instance"), ("p4de.24xlarge", "ec2_p4de_instance"), ("g4dn.xlarge", "g4dn"), ("trn2.48xlarge", "trainium_instance"),
    ("inf2.xlarge", "inferentia"), ("dl1.24xlarge", "ec2_dl1_instance"), ("p5.48xlarge", "instance2"), ("g6e.2xlarge", "instance2"),
    ("m7g.large", "instance2"), ("i4i.large", "instance2")])
def test_accelerated_instances_use_their_aws_icons(instance_type, shape):
    """Family-specific icons only where AWS draws that family; the GPU icons print their family name."""
    node = {"kind": "ec2", "attributes": {"InstanceType": instance_type}}
    assert f"shape=mxgraph.aws4.{shape};" in resource_style({"kind": "ec2", "variant": icon_variant(node)})


def test_gpu_instances_are_labelled():
    from diagram_bot.layout import resource_label
    label = lambda t: resource_label({"kind": "ec2", "name": "node", "attributes": {"InstanceType": t}})
    assert label("p5.48xlarge") == "node\np5.48xlarge\nGPU" and label("g6e.2xlarge").endswith("\nGPU")
    assert label("m7g.large") == "node\nm7g.large" and label("inf2.xlarge") == "node\ninf2.xlarge"


@pytest.mark.parametrize("service, icon", [("bedrock-runtime", "bedrock"), ("bedrock-agent-runtime", "bedrock"),
                                           ("sagemaker.runtime", "sagemaker"), ("ecr.api", None), ("s3", None)])
def test_ai_service_endpoints_use_their_service_icons(service, icon):
    node = {"kind": "vpc_endpoint", "attributes": {"ServiceName": f"com.amazonaws.us-east-1.{service}"}}
    style = resource_style({"kind": "vpc_endpoint", "variant": icon_variant(node)})
    assert (f"resIcon=mxgraph.aws4.{icon};" in style) if icon else "shape=mxgraph.aws4.endpoints;" in style


def assert_routes_avoid_cards(layout):
    """Overview routes never run through a summary card other than the one they leave."""
    cards = [s for s in layout["shapes"] if s["role"] == "summary"]
    for edge in layout["edges"]:
        for (x1, y1), (x2, y2) in zip(edge["path"], edge["path"][1:]):
            segment = (min(x1, x2), min(y1, y2), max(x1, x2) + 0.01, max(y1, y2) + 0.01)
            for card in cards:
                if card["id"] in (edge["source"], edge["target"]):
                    continue
                x, y = layout["absolute"][card["id"]]
                assert not overlap(segment, (x + 1, y + 1, x + card["w"] - 1, y + card["h"] - 1)), (edge["relation"], card["label"][:30])


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_overview_draws_the_network_between_vpcs(key):
    snapshot = SAMPLES[key]()
    layout = layout_document(snapshot, "overview")
    assert_readable(layout)
    assert_no_shared_segments(layout)
    assert_routes_avoid_cards(layout)
    validate_document(generate_document(snapshot))
    relations = sorted(e["relation"] for e in layout["edges"])
    expected = {
        "three-tier": ["cloudfront_origin"],
        "environments": ["attached_transit_gateway"] * 4,
        "hybrid": ["attached_transit_gateway", "dx_gateway_association"] + ["dx_virtual_interface"] * 2 + ["vpn_connection"] * 2,
        "data-platform": ["attached_transit_gateway"] * 2 + ["vpn_connection"] * 2,
        "ai-platform": ["attached_transit_gateway"],
        "multi-account": ["attached_transit_gateway"] * 3 + ["vpn_connection"] * 2,
    }.get(key, [])
    assert relations == expected


def test_overview_draws_peering_and_vpn_gateways():
    snapshot = full_design().snapshot()
    layout = layout_document(snapshot, "overview")
    assert_readable(layout)
    assert_no_shared_segments(layout)
    assert_routes_avoid_cards(layout)
    kinds = {s.get("kind") for s in layout["shapes"] if s["role"] == "resource"}
    assert {"vpc_peering", "vpn_gateway", "customer_gateway"} <= kinds
    assert sorted(e["relation"] for e in layout["edges"]) == ["in_vpc", "in_vpc", "peer_vpc", "vpn_connection"]


def test_edge_and_direct_connect_views_are_readable():
    """CloudFront at the edge (one VPC on the TGW, one below the hub band), WAF badges, a DX gateway joined to the TGW and
    a private VIF onto a virtual private gateway pass the same checks as the samples in every view."""
    snapshot = edge_snapshot()
    graph = build_graph(snapshot)
    nodes = {n["id"]: n for n in graph["nodes"]}
    overview = layout_document(snapshot, "overview")
    assert_readable(overview)
    assert_no_shared_segments(overview)
    assert_routes_avoid_cards(overview)
    assert sorted(e["relation"] for e in overview["edges"]) == sorted(
        ["attached_transit_gateway", "in_vpc", "cloudfront_origin", "cloudfront_origin", "dx_gateway_association", "dx_virtual_interface",
         "dx_virtual_interface", "dx_virtual_interface", "vpn_connection"])
    shapes = {s.get("resource"): s for s in overview["shapes"] if s["role"] == "resource"}
    # CloudFront stands outside the Region box, in the AWS Cloud; its routes enter the VPC cards from above.
    for cf in (n for n in graph["nodes"] if n["kind"] == "cloudfront_distribution"):
        assert shapes[cf["id"]]["parent"] == "cloud"
    region = next(s for s in overview["shapes"] if s["id"] == "region")
    assert all(overview["absolute"][shapes[k]["id"]][0] + 52 < overview["absolute"]["region"][0] for k in shapes if nodes[k]["kind"] == "cloudfront_distribution")
    assert region["x"] > 20
    # WAF badge on the protected distribution only, opening its web ACL.
    badges = [s for s in overview["shapes"] if s["role"] == "badge"]
    assert [nodes[b["resource"]]["name"] for b in badges] == ["edge-acl"]
    assert all(b["parent"] == shapes[k]["id"] for b in badges for k in shapes if nodes[k].get("waf"))
    # Direct Connect connections sit in the data center box with the customer gateway.
    box = next(s for s in overview["shapes"] if s["id"] == "onprem")
    assert box["label"].endswith("·DX")
    assert {nodes[k]["kind"] for k, s in shapes.items() if s["parent"] == "onprem"} == {"customer_gateway", "dx_connection"}
    for vpc in ("vpc-edge-web", "vpc-edge-shop", "vpc-edge-corp"):
        for references in (False, True):
            layout = layout_document(snapshot, "detail", vpc, None, references)
            assert_readable(layout)
            assert_no_shared_segments(layout)
    corp = layout_document(snapshot, "detail", "vpc-edge-corp")
    assert sorted(e["relation"] for e in corp["edges"] if e["relation"] in ("vpn_connection", "dx_virtual_interface")) == ["dx_virtual_interface", "vpn_connection"]
    web = layout_document(snapshot, "detail", "vpc-edge-web")
    edge = next(e for e in web["edges"] if e["relation"] == "cloudfront_origin")
    assert edge["path"][0][0] < web["absolute"]["region"][0] < edge["path"][-1][0] and not edge["auxiliary"]
    assert any(s["role"] == "badge" for s in web["shapes"])
    for kind in ("cloudfront_distribution", "dx_connection", "dx_gateway", "load_balancer", "vpn_gateway", "transit_gateway"):
        for node in (n for n in graph["nodes"] if n["kind"] == kind and not n["reference_only"]):
            layout = layout_document(snapshot, "focus", None, node["id"], True)
            assert_readable(layout)
            assert_no_shared_segments(layout)
    xml = generate_document(snapshot)
    validate_document(xml)
    for icon in ("resIcon=mxgraph.aws4.cloudfront;", "resIcon=mxgraph.aws4.waf;", "resIcon=mxgraph.aws4.direct_connect;"):
        assert icon in xml
    assert generate_document(snapshot) == xml


def test_edge_labels_carry_elastic_ips_waf_and_route_tables():
    from diagram_bot.layout import resource_label
    snapshot = edge_snapshot()
    graph = build_graph(snapshot)
    by_name = {n["name"]: n for n in graph["nodes"] if not n["reference_only"]}
    assert resource_label(by_name["edge-nat-a"]).splitlines()[-1] == "EIP 203.0.113.80"
    assert "EIP 203.0.113.81" in resource_label(by_name["web-a"])
    assert resource_label(by_name["edge-tgw"]).endswith("라우팅 테이블 1")
    assert resource_label(by_name["www.example.org"]).splitlines()[1:] == ["CloudFront", "d" + by_name["www.example.org"]["attributes"]["DomainName"][1:],
                                                                            "오리진 ELB 1 · S3 1"]
    assert resource_label(by_name["edge-dx-1"]) == "edge-dx-1\nDirect Connect · 10Gbps\nloc-a\nVIF 1 · available"
    card = next(s for s in layout_document(snapshot, "overview")["shapes"] if s.get("resource", "").endswith("/vpc/vpc-edge-web") and s["role"] == "summary")
    assert "TGW 라우팅 테이블 · edge-spokes" in card["label"]
    nlb = layout_document(SAMPLES["nlb-alb"](), "detail", "vpc-0a1b2c3d4e5f60003")
    labels = {s["label"].split("\n")[0]: s["label"] for s in nlb["shapes"] if s["role"] == "resource"}
    assert labels["partner-nlb"].endswith("AZ 2개 · EIP 2") and labels["partner-alb"].endswith("AZ 2개 · WAF")


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_overview_cards_differ_per_sample_and_hide_drawn_groups(key):
    """Every sample's overview shows its own services per tier; no card counts logical groups or on-premises targets as unplaced."""
    layout = layout_document(SAMPLES[key](), "overview")
    labels = [s["label"] for s in layout["shapes"]]
    assert not any(label.startswith("VPC 외 · 배치 미확인") for label in labels), key
    assert sum(1 for s in layout["shapes"] if s["role"] == "resource" and s.get("label_width")) >= 2  # counted service icons
