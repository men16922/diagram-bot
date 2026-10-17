"""Deterministic AWS topology -> native, editable draw.io XML."""
import hashlib
import json
import math
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml.ElementTree import fromstring

from .graph import build_graph

RELATIONS = {
    "associated_load_balancer": "LB 연결", "registered_target": "대상 등록",
    "in_subnet": "서브넷 배치", "uses_security_group": "SG 연결",
    "attached_interface": "ENI 장착", "attached_volume": "EBS 장착",
}
STYLE = {
    "ec2": ("ec2", "#ED7100"),
    "load_balancer": ("elastic_load_balancing", "#8C4FFF"),
    "target_group": ("elastic_load_balancing", "#6D5DB3"),
    "security_group": ("group_security_group", "#DD344C"),
    "network_interface": ("elastic_network_interface", "#8C4FFF"),
    "volume": ("elastic_block_store", "#7AA116"),
}


def cell_id(value):
    return "r-" + hashlib.sha256(value.encode()).hexdigest()[:24]


def validate_document(xml):
    if len(xml.encode()) > 10 * 1024 * 1024:
        raise ValueError("문서는 최대 10 MiB입니다.")
    root = fromstring(xml)
    if root.tag not in {"mxfile", "mxGraphModel"}:
        raise ValueError("draw.io XML 문서가 아닙니다.")
    # Keep an inspectable XML document, not compressed/unbounded input.
    if root.tag == "mxfile" and not root.findall("diagram/mxGraphModel"):
        raise ValueError("압축하지 않은 draw.io XML이 필요합니다.")
    return root


def generate_document(snapshot, vpc_id=None, include_references=False):
    graph = build_graph(snapshot)
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    parent = {e["source"]: e["target"] for e in edges if e["relation"] == "in_vpc"}
    subnet_of = {e["source"]: e["target"] for e in edges if e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] == "ec2"}
    selected_vpcs = [n for n in nodes.values() if n["kind"] == "vpc" and (not vpc_id or n["resource_id"] == vpc_id)]
    if vpc_id and not selected_vpcs:
        raise ValueError("선택한 VPC가 스냅샷에 없습니다.")
    visible = {n["id"] for n in selected_vpcs}
    visible.update(n["id"] for n in nodes.values() if parent.get(n["id"]) in visible)
    visible.update(e["target"] for e in edges if e["source"] in visible and e["relation"] == "registered_target")
    if not vpc_id:
        visible.update(n["id"] for n in nodes.values() if not n["reference_only"])
    if include_references:
        visible.update(e["target"] for e in edges if e["source"] in visible and nodes[e["target"]]["reference_only"])
    else:
        visible = {key for key in visible if not nodes[key]["reference_only"] or nodes[key]["kind"] in {"vpc", "subnet"}}
    file = Element("mxfile", {"host": "Diagram Bot", "version": "1", "compressed": "false"})
    page = SubElement(file, "diagram", {"id": "aws", "name": vpc_id or "AWS infrastructure"})
    model = SubElement(page, "mxGraphModel", {"dx": "1200", "dy": "800", "grid": "1", "gridSize": "10", "page": "0", "background": "#ffffff"})
    root = SubElement(model, "root")
    SubElement(root, "mxCell", {"id": "0"})
    SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    drawn = set()
    positions = {"1": (0, 0)}
    channels = {}

    def add(key, label, x, y, w, h, style, parent_id="1", resource=None):
        px, py = positions[parent_id]
        positions[key] = (px + x, py + y)
        attrs = {"id": key, "label": label}
        if resource:
            attrs["resourceId"] = resource
        obj = SubElement(root, "object", attrs)
        cell = SubElement(obj, "mxCell", {"vertex": "1", "parent": parent_id, "style": style})
        SubElement(cell, "mxGeometry", {"x": str(x), "y": str(y), "width": str(w), "height": str(h), "as": "geometry"})

    common = "html=0;fontFamily=Arial;fontSize=12;fontColor=#24364B;"
    group_style = common + "swimlane;startSize=38;horizontal=1;rounded=1;arcSize=10;collapsible=1;whiteSpace=wrap;fillColor=#F1F7FC;swimlaneFillColor=#FFFFFF;strokeColor=#A7BFD4;fontStyle=1;align=left;spacingLeft=16;"

    def resource(n, x, y, container):
        shape, color = STYLE.get(n["kind"], ("", "#687B90"))
        style = common + "whiteSpace=wrap;labelWidth=140;align=center;verticalLabelPosition=bottom;verticalAlign=top;spacingTop=8;"
        if shape:
            style += f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{shape};fillColor={color};strokeColor=#FFFFFF;gradientColor=none;"
        else:
            style += "rounded=1;fillColor=#E8EFF6;strokeColor=#A7BFD4;"
        if n["reference_only"]:
            style += "dashed=1;opacity=65;"
        label = n["name"]
        if n["kind"] == "registered_target":
            target = n["attributes"].get("Target", {})
            label = f"{target.get('Id', label)}:{target.get('Port', '')}"
        if len(label) > 45:
            label = label[:42] + "…"
        add(cell_id(n["id"]), label, x, y, 52, 52, style, container, n["id"])
        drawn.add(n["id"])

    meta = snapshot["metadata"]
    heading = f"{'SAMPLE · ' if meta.get('demo') else ''}AWS / {meta['account_id']} / {meta['region']}"
    add("heading", heading, 24, 12, 920, 30, common + "text;align=left;fontSize=19;fontStyle=1;strokeColor=none;fillColor=none;")
    status = "부분 조회" if meta["status"] != "complete" else "조회 완료"
    add("scope", f"{status} · {meta['finished_at']}\n설정 관계 · 실제 통신 미확인 · 사용자 주석은 수집 데이터와 별도", 24, 48, 920, 42, common + "text;align=left;fontSize=10;fontColor=#62748A;strokeColor=none;fillColor=none;")
    cursor_y = 112
    for vpc in selected_vpcs:
        subnets = [n for n in nodes.values() if n["kind"] == "subnet" and parent.get(n["id"]) == vpc["id"]]
        azs = sorted({n["attributes"].get("AvailabilityZone", "AZ 미확인") for n in subnets})
        azs = azs or ["AZ 미확인"]
        top = [nodes[key] for key in sorted(visible) if parent.get(key) == vpc["id"] and nodes[key]["kind"] != "subnet" and key not in subnet_of]
        top_height = max(130, math.ceil(len(top) / max(1, len(azs)*2)) * 110 + 30)
        az_heights = []
        for az in azs:
            group_subnets = [s for s in subnets if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az]
            az_heights.append(50 + sum(max(150, math.ceil(sum(1 for key in visible if subnet_of.get(key) == s["id"])/2)*115+60)+18 for s in group_subnets))
        width = max(720, len(azs)*320+48)
        height = 52 + top_height + max(az_heights)
        vid = cell_id(vpc["id"])
        add(vid, f"VPC  ·  {vpc['name']}  ·  {vpc['attributes'].get('CidrBlock', '')}", 24, cursor_y, width, height, group_style, resource=vpc["id"])
        drawn.add(vpc["id"])
        channels[vpc["id"]] = cursor_y + 46 + top_height - 20
        for i, n in enumerate(top):
            resource(n, 60+(i % (len(azs)*2))*155, 64+(i//(len(azs)*2))*110, vid)
        for column, az in enumerate(azs):
            az_id = cell_id(vpc["id"]+"/az/"+az)
            add(az_id, az, 22+column*320, 46+top_height, 302, max(az_heights), group_style + "dashed=1;fillColor=#F4F3FA;strokeColor=#CCC4E2;", vid)
            sy = 48
            for subnet in [s for s in subnets if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az]:
                children = [nodes[key] for key in sorted(visible) if subnet_of.get(key) == subnet["id"]]
                sh = max(150, math.ceil(len(children)/2)*115+60)
                sid = cell_id(subnet["id"])
                add(sid, f"{subnet['name']}\n{subnet['attributes'].get('CidrBlock','')}", 16, sy, 270, sh, group_style + "startSize=46;fillColor=#EAF6F2;strokeColor=#8BBCA9;fontSize=10;", az_id, subnet["id"])
                drawn.add(subnet["id"])
                for i, n in enumerate(children):
                    resource(n, 38+(i%2)*128, 67+(i//2)*115, sid)
                sy += sh+18
        cursor_y += height + 35
    others = [nodes[key] for key in sorted(visible-drawn)]
    if others:
        height = 60+math.ceil(len(others)/5)*120
        add("references", "배치 미확인 / VPC 외 리소스 · 점선은 참조만 확인됨", 24, cursor_y, 860, height, group_style + "dashed=1;fillColor=#FFF6E7;strokeColor=#D5B47A;")
        for i,n in enumerate(others):
            resource(n, 45+(i%5)*164, 65+(i//5)*120, "references")
    for i, edge in enumerate(edges):
        if edge["source"] not in drawn or edge["target"] not in drawn or edge["relation"] == "in_vpc":
            continue
        if edge["relation"] == "in_subnet" and nodes[edge["source"]]["kind"] == "ec2":
            continue
        # Placement is visible on selection; keep the default overview readable.
        if edge["relation"] == "in_subnet":
            continue
        key = cell_id(json.dumps(edge, sort_keys=True, default=str))
        obj = SubElement(root,"object",{"id": key,"label": RELATIONS.get(edge["relation"],edge["relation"]),"relationIndex": str(i),"evidence": edge["evidence"]})
        routed_target = edge["relation"] == "registered_target" and parent.get(edge["source"]) == parent.get(edge["target"]) and parent.get(edge["source"]) in channels
        ports = "exitX=1;exitY=0.5;entryX=1;entryY=0.5;noLabel=1;" if routed_target else ""
        cell = SubElement(obj,"mxCell",{"edge":"1","parent":"1","source":cell_id(edge["source"]),"target":cell_id(edge["target"]),"style":ports+"edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;fontFamily=Arial;fontSize=10;fontColor=#536A82;strokeColor=#7C91A8;strokeWidth=1.5;endArrow=none;labelBackgroundColor=#FFFFFF;"})
        geometry = SubElement(cell,"mxGeometry",{"relative":"1","as":"geometry"})
        if routed_target:
            sx, sy = positions[cell_id(edge["source"])]
            tx, ty = positions[cell_id(edge["target"])]
            channel = channels[parent[edge["source"]]]
            points = SubElement(geometry, "Array", {"as": "points"})
            for x, y in ((sx + 80, sy + 26), (sx + 80, channel), (tx + 80, channel), (tx + 80, ty + 26)):
                SubElement(points, "mxPoint", {"x": str(x), "y": str(y)})
    return tostring(file, encoding="unicode")
