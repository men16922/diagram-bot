"""Parse, inspect, rule-align, orthogonally route, and import existing `drawio.xml` files.

Bridges existing company `drawio.xml` workflows (where standard architecture XML templates are
manipulated at the code level) with Diagram Bot's deterministic layout and snapshot pipeline:
- `inspect_drawio_xml(xml_text)`: audit overlaps, out-of-bounds children, unaligned AZ subnet tiers,
  unrouted edges, edge-icon collisions, and auto-extract the document's `DrawioSpec`.
- `align_drawio_xml(xml_text, ...)`:
  - `mode="preserve"` (default): keeps all original cell IDs, labels, and custom attributes while
    reparenting flat visual children, snapping resources into grids, synchronizing subnet tiers across
    AZ columns, auto-fitting containers bottom-up, and routing orthogonal edges around obstacles.
  - `mode="rebuild"`: imports the XML into a Diagram Bot `Snapshot` and renders it through `generate_document`.
- `import_drawio_xml(xml_text, ...)`: converts an existing AWS `drawio.xml` into a valid Diagram Bot
  `Snapshot` (`raw` + `graph`) so it can be edited with `designer.Design`, reviewed with `findings.review`,
  diffed with `diff.diff`, or exported to CloudFormation with `iac.template`.
"""
import copy
import json
import math
from pathlib import Path
import re
from xml.etree.ElementTree import Element, SubElement, tostring

from .designer import STANDARDS, register_standard, slug
from .drawio import ICONS, RESOURCE_ICONS, generate_document
from .drawio_spec import (format_style, iter_cells, parse_style, patch_style, read_document, resolve_spec, set_default_spec,
                          spec_from_xml)
from .errors import InputError
from .graph import build_graph
from .samples import Sample

__all__ = ["adopt_company_xml", "align_drawio_xml", "import_drawio_xml", "inspect_drawio_xml"]

# Reverse lookup from draw.io aws4 icon names to Diagram Bot (kind, variant).
_ICON_TO_KIND = {}
for _k, (_icon_name, _) in ICONS.items():
    _ICON_TO_KIND.setdefault(_icon_name, _k)
for _k, (_icon_name, _) in RESOURCE_ICONS.items():
    _ICON_TO_KIND[_icon_name] = _k

_SUBNET_TIER_WORDS = (
    (0, ("public", "퍼블릭", "pub", "dmz", "ingress")),
    (1, ("web", "front", "웹")),
    (3, ("isolated", "data", "db", "rds", "database", "cache", "격리", "데이터")),
    (2, ("app", "private", "service", "was", "eks", "ecs", "프라이빗", "앱")),
)

_BAND_ORDER = {
    "internet_gateway": 0, "vpn_gateway": 0, "vpc_peering": 0, "tgw_attachment": 0,
    "vpc_endpoint": 0, "bedrock": 0, "sagemaker": 0, "eks_cluster": 0, "lambda_function": 0,
    "application": 1, "network": 1, "gateway": 1, "load_balancer": 1,
    "target_group": 2,
}


MAX_COORDINATE, MAX_DEPTH = 1e7, 64


def _number(geo, key, default, cid):
    raw = geo.get(key)
    try:
        value = float(raw) if raw not in (None, "") else default
    except ValueError:
        raise InputError(f"셀 {cid}의 {key} 값이 숫자가 아닙니다: {raw[:20]}") from None
    if not math.isfinite(value) or abs(value) > MAX_COORDINATE or (key in ("width", "height") and value < 0):
        raise InputError(f"셀 {cid}의 {key} 값이 범위를 벗어났습니다: {raw[:20]}")
    return value


def _classify_vertex(cid, label, style_str, w, h, wrapper_el):
    """Return `(role, kind, subnet_tier)` for a vertex cell."""
    flags, kv = parse_style(style_str)
    gr = kv.get("grIcon", "").replace("mxgraph.aws4.", "")
    shape = kv.get("shape", "").replace("mxgraph.aws4.", "")
    res_icon = kv.get("resIcon", "").replace("mxgraph.aws4.", "")
    text = (label or "").lower()
    res_attr = wrapper_el.get("resourceId") or wrapper_el.get("resource") or ""

    if cid == "heading" or ("text" in flags and kv.get("fontSize") in ("18", "19", "20", "22")):
        return "title", "title", None
    if cid == "scope" or ("text" in flags and kv.get("strokeColor") == "none" and kv.get("fillColor") == "none"):
        return "caption", "caption", None
    if res_icon == "waf" and w <= 28 and h <= 28:
        return "badge", "waf_web_acl", None
    if wrapper_el.get("summary") == "1":
        return "summary", "vpc", None

    if "group_aws_cloud" in gr or "aws cloud" in text:
        return "cloud", "cloud", None
    if "group_region" in gr or (kv.get("container") == "1" and re.search(r"\b(ap|us|eu|sa|ca|me|af)-[a-z]+-\d\b", text) and not re.search(r"\d[a-f]\b", text)):
        return "region", "region", None
    if "group_account" in gr or (kv.get("container") in ("0", "1") and ("account" in text or "계정" in text) and "group" in shape):
        return "account", "account", None
    if "group_vpc" in gr or "/vpc/" in res_attr or (kv.get("container") == "1" and "vpc" in text and "subnet" not in text and "엔드포인트" not in text):
        return "vpc", "vpc", None
    if "group_auto_scaling_group" in gr or ("auto scaling" in text and ("group" in shape or kv.get("dashed") == "1")):
        return "asg", "auto_scaling_group", None
    if "group_corporate_data_center" in gr or "corporate data center" in text or "on-prem" in text or "데이터 센터" in text:
        return "onprem", "onprem", None
    if cid == "references" or "vpc 외" in text or "배치 미확인" in text:
        return "references", "references", None
    if "서브넷 미확인" in text:
        return "unplaced", "unplaced", None

    is_subnet = (
        "group_security_group" in gr
        or "/subnet/" in res_attr
        or (kv.get("container") == "1" and any(w in text for w in ("subnet", "서브넷", "public-", "private-", "isolated-", "app-", "db-", "web-", "data-")))
    )
    if is_subnet:
        fill = kv.get("fillColor", "").upper()
        stroke = kv.get("strokeColor", "").upper()
        tier = 2
        if fill in ("#F2F6E8", "#E8F5E9") or stroke == "#7AA116" or "0.0.0.0/0 → igw" in text:
            tier = 0
        elif fill in ("#ECEFF8", "#EDE7F6") or "인터넷 경로 없음" in text:
            tier = 3
        else:
            for rank, words in _SUBNET_TIER_WORDS:
                if any(word in text for word in words):
                    tier = rank
                    break
        return "subnet", "subnet", tier

    if kv.get("container") == "1" and (re.search(r"\b[a-z]{2}-[a-z0-9]+-\d[a-f]\b", text) or re.search(r"\baz[-_\s]?[a-f1-4]\b", text) or "가용 영역" in text):
        return "az", "az", None

    if kv.get("container") == "0" and kv.get("dashed") == "1" and kv.get("strokeColor", "").upper() == "#ED7100":
        return "ecs_frame", "ecs_service", None

    # Resource detection
    icon_key = res_icon or shape
    kind = _ICON_TO_KIND.get(icon_key)
    if not kind:
        if "/load_balancer/" in res_attr or "alb" in text or "nlb" in text or "load balancer" in text:
            kind = "network" if "nlb" in text else "application"
        elif "/target_group/" in res_attr or "target group" in text or "대상 그룹" in text or text.startswith("tg-"):
            kind = "target_group"
        elif "/ec2/" in res_attr or "ec2" in text or "instance" in text:
            kind = "ec2"
        elif "/db_instance/" in res_attr or "rds" in text or "postgres" in text or "mysql" in text:
            kind = "db_instance"
        elif "/db_cluster/" in res_attr or "aurora" in text:
            kind = "aurora"
        elif "redis" in text or "elasticache" in text or "valkey" in text:
            kind = "redis"
        elif "nat" in text:
            kind = "nat_gateway"
        elif "igw" in text or "internet gateway" in text:
            kind = "internet_gateway"
        elif "tgw" in text or "transit gateway" in text:
            kind = "tgw_attachment" if "attach" in text else "transit_gateway"
        elif "eks" in text:
            kind = "eks_cluster"
        elif "ecs" in text or "fargate" in text:
            kind = "ecs_task" if "task" in text else "ecs_service"
        elif "lambda" in text:
            kind = "lambda_function"
        elif "opensearch" in text:
            kind = "search_node"
        elif "msk" in text or "kafka" in text:
            kind = "msk_broker"
        elif "efs" in text:
            kind = "efs_standard"
        elif "fsx" in text or "lustre" in text:
            kind = "fsx_lustre"
        elif "sagemaker" in text:
            kind = "sagemaker_endpoint"
        elif "bedrock" in text:
            kind = "bedrock"
        elif "endpoint" in text or "vpce" in text:
            kind = "vpc_endpoint"
        elif "cloudfront" in text:
            kind = "cloudfront_distribution"
        elif "waf" in text:
            kind = "waf_web_acl"
        elif "direct connect" in text or "dx" in text:
            kind = "dx_connection"
        elif "vpn" in text:
            kind = "vpn_gateway"
        elif "cgw" in text or "customer gateway" in text:
            kind = "customer_gateway"
        else:
            kind = "ec2"
    return "resource", kind, None


def _parse_page_model(graph_root):
    """Parse vertices and edges from a single `<root>` element into structured dicts."""
    vertices = {}
    edges = []
    for wrapper, inner, cid, label in iter_cells(graph_root):
        if cid in ("0", "1", "", None):
            continue
        style_str = inner.get("style") or ""
        geo = inner.find("mxGeometry")
        if inner.get("vertex") == "1" and geo is not None:
            x = _number(geo, "x", 0.0, cid)
            y = _number(geo, "y", 0.0, cid)
            w = _number(geo, "width", 52.0, cid)
            h = _number(geo, "height", 52.0, cid)
            parent = inner.get("parent") or "1"
            role, kind, tier = _classify_vertex(cid, label, style_str, w, h, wrapper)
            vertices[cid] = {
                "id": cid, "wrapper": wrapper, "cell": inner, "geo": geo,
                "label": label, "style": style_str, "parent": parent,
                "x": x, "y": y, "w": w, "h": h,
                "role": role, "kind": kind, "tier": tier,
            }
        elif inner.get("edge") == "1":
            pts = []
            if geo is not None:
                arr = geo.find("Array[@as='points']")
                if arr is not None:
                    for pt in arr.findall("mxPoint"):
                        pts.append((_number(pt, "x", 0.0, cid), _number(pt, "y", 0.0, cid)))
            edges.append({
                "id": cid, "wrapper": wrapper, "cell": inner, "geo": geo,
                "label": label, "style": style_str,
                "source": inner.get("source") or "", "target": inner.get("target") or "",
                "parent": inner.get("parent") or "1", "points": pts,  # points are relative to the parent's origin
            })
    return vertices, edges


def _compute_abs(vertices):
    """Page coordinates `(abs_x, abs_y)` of every vertex; iterative, so a hostile parent chain cannot exhaust the stack.

    A parent cycle or nesting deeper than MAX_DEPTH is an InputError; a parent that is not a vertex counts as the page."""
    abs_pos = {"0": (0.0, 0.0), "1": (0.0, 0.0)}
    for start in vertices:
        chain, cid = [], start
        while cid not in abs_pos and cid in vertices:
            if len(chain) >= MAX_DEPTH or cid in chain:
                raise InputError(f"셀 {start}의 부모 관계가 순환하거나 {MAX_DEPTH}단계보다 깊습니다.")
            chain.append(cid)
            cid = vertices[cid]["parent"]
        x, y = abs_pos.get(cid, (0.0, 0.0))
        for cid in reversed(chain):
            x, y = x + vertices[cid]["x"], y + vertices[cid]["y"]
            abs_pos[cid] = (x, y)
    return abs_pos


def _fraction(value):
    """An exit/entry port fraction from a style, or None when absent or not a finite number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _boxes_overlap(a, b, pad=0.0):
    return (a[0] + pad) < (b[2] - pad) and (b[0] + pad) < (a[2] - pad) and (a[1] + pad) < (b[3] - pad) and (b[1] + pad) < (a[3] - pad)


def inspect_drawio_xml(xml_text) -> dict:
    """Inspect a `drawio.xml` string and return layout/routing diagnostics and its extracted `DrawioSpec`."""
    root = read_document(xml_text)
    graph_root = root.find("diagram/mxGraphModel/root") if root.tag == "mxfile" else root.find("root")
    vertices, edges = _parse_page_model(graph_root)
    abs_pos = _compute_abs(vertices)

    roles = {}
    for v in vertices.values():
        roles[v["role"]] = roles.get(v["role"], 0) + 1

    overlaps = []
    by_parent = {}
    for cid, v in vertices.items():
        if v["role"] in ("asg", "account", "ecs_frame", "badge", "title", "caption"):
            continue
        by_parent.setdefault(v["parent"], []).append(cid)

    for members in by_parent.values():
        for i, a_id in enumerate(members):
            ax, ay = abs_pos[a_id]
            va = vertices[a_id]
            box_a = (ax, ay, ax + va["w"], ay + va["h"])
            for b_id in members[i + 1:]:
                bx, by = abs_pos[b_id]
                vb = vertices[b_id]
                box_b = (bx, by, bx + vb["w"], by + vb["h"])
                if _boxes_overlap(box_a, box_b, pad=0.5):
                    overlaps.append((a_id, b_id))

    out_of_bounds = []
    for cid, v in vertices.items():
        pid = v["parent"]
        if pid in vertices and v["role"] not in ("badge", "asg", "account", "ecs_frame"):
            p = vertices[pid]
            if v["x"] < -1 or v["y"] < -1 or (v["x"] + v["w"]) > (p["w"] + 1) or (v["y"] + v["h"]) > (p["h"] + 1):
                out_of_bounds.append((cid, pid))

    unaligned_tiers = []
    # Check subnets of the same tier across sibling AZ columns in each VPC
    az_by_vpc = {}
    for cid, v in vertices.items():
        if v["role"] == "az":
            az_by_vpc.setdefault(v["parent"], []).append(cid)
    for az_ids in az_by_vpc.values():
        tier_subnets = {}
        for az_id in az_ids:
            for s in vertices.values():
                if s["parent"] == az_id and s["role"] == "subnet":
                    tier_subnets.setdefault(s["tier"], []).append(s)
        for subs in tier_subnets.values():
            if len(subs) > 1:
                y0, h0 = subs[0]["y"], subs[0]["h"]
                for other in subs[1:]:
                    if abs(other["y"] - y0) > 1 or abs(other["h"] - h0) > 1:
                        unaligned_tiers.append((subs[0]["id"], other["id"]))

    unrouted_edges = []
    edge_collisions = []
    resource_boxes = {
        cid: (abs_pos[cid][0] + 2, abs_pos[cid][1] + 2, abs_pos[cid][0] + v["w"] - 2, abs_pos[cid][1] + v["h"] - 2)
        for cid, v in vertices.items() if v["role"] == "resource"
    }

    for e in edges:
        src, dst = e["source"], e["target"]
        if src not in vertices or dst not in vertices:
            continue
        _, kv = parse_style(e["style"])
        ports = [_fraction(kv.get(k)) for k in ("exitX", "exitY", "entryX", "entryY")]
        if None in ports or kv.get("edgeStyle") != "orthogonalEdgeStyle":
            unrouted_edges.append(e["id"])
            continue
        sx, sy = abs_pos[src]
        dx, dy = abs_pos[dst]
        sv, dv = vertices[src], vertices[dst]
        p_start = (sx + ports[0] * sv["w"], sy + ports[1] * sv["h"])
        p_end = (dx + ports[2] * dv["w"], dy + ports[3] * dv["h"])
        ox, oy = abs_pos.get(e["parent"], (0.0, 0.0))
        path = [p_start, *((x + ox, y + oy) for x, y in e["points"]), p_end]
        if len(path) == 2 and abs(p_start[0] - p_end[0]) > 1 and abs(p_start[1] - p_end[1]) > 1:
            unrouted_edges.append(e["id"])
            continue
        for (x1, y1), (x2, y2) in zip(path, path[1:]):
            if abs(x1 - x2) > 1 and abs(y1 - y2) > 1:
                if e["id"] not in unrouted_edges:
                    unrouted_edges.append(e["id"])
                break
            seg = (min(x1, x2), min(y1, y2), max(x1, x2) + 0.1, max(y1, y2) + 0.1)
            for rid, rbox in resource_boxes.items():
                if rid not in (src, dst) and _boxes_overlap(seg, rbox):
                    edge_collisions.append((e["id"], rid))

    extracted = spec_from_xml(xml_text)
    container_roles = {"cloud", "region", "account", "vpc", "az", "subnet", "onprem", "references", "unplaced", "summary"}
    return {
        "wrapper": extracted.cell_wrapper,
        "node_count": sum(1 for v in vertices.values() if v["role"] == "resource"),
        "container_count": sum(1 for v in vertices.values() if v["role"] in container_roles),
        "edge_count": len(edges),
        "roles": roles,
        "overlaps": overlaps,
        "out_of_bounds": out_of_bounds,
        "unaligned_tiers": unaligned_tiers,
        "unrouted_edges": unrouted_edges,
        "edge_collisions": edge_collisions,
        "clean": not (overlaps or out_of_bounds or unaligned_tiers or unrouted_edges or edge_collisions),
        "spec": extracted.to_dict(),
    }


def _snap(val, grid=10):
    return float(int(math.ceil(val / grid) * grid)) if grid > 1 else float(round(val))


def _reparent_visual_children(vertices):
    """Reparent flat (`parent == "1"`) cells into the innermost container that visually encloses their center."""
    abs_pos = _compute_abs(vertices)
    container_order = ("subnet", "onprem", "references", "unplaced", "az", "vpc", "region", "cloud")
    containers = [
        (cid, v, abs_pos[cid][0], abs_pos[cid][1], abs_pos[cid][0] + v["w"], abs_pos[cid][1] + v["h"])
        for cid, v in vertices.items()
        if v["role"] in container_order
    ]
    # Smallest area first so a resource inside a subnet picks the subnet before the AZ or VPC.
    containers.sort(key=lambda item: (container_order.index(item[1]["role"]), (item[4] - item[2]) * (item[5] - item[3])))

    for cid, v in vertices.items():
        if v["parent"] != "1" or v["role"] in ("title", "caption", "cloud", "account", "asg", "ecs_frame"):
            continue
        ax, ay = abs_pos[cid]
        cx, cy = ax + v["w"] / 2, ay + v["h"] / 2
        for cont_id, cont, x1, y1, x2, y2 in containers:
            if cont_id == cid:
                continue
            # A container cannot be reparented into a narrower role
            if v["role"] in container_order and container_order.index(v["role"]) >= container_order.index(cont["role"]):
                continue
            if x1 <= cx <= x2 and y1 <= cy <= y2:
                v["parent"] = cont_id
                v["cell"].set("parent", cont_id)
                v["x"] = ax - x1
                v["y"] = ay - y1
                break


def _align_page_in_place(graph_root, *, spec=None, reparent=True, route_edges=True, normalize_styles=False, grid_size=10):
    """Align containers, resource grids, cross-AZ tiers, and orthogonal routes in-place on `graph_root`."""
    vertices, edges = _parse_page_model(graph_root)
    if not vertices:
        return

    # Capture original absolute centers before reparenting/resizing so overlay frames (ASG/ECS) can track members.
    orig_abs = _compute_abs(vertices)
    overlay_members = {}
    for cid, v in vertices.items():
        if v["role"] in ("asg", "ecs_frame", "account"):
            ox, oy = orig_abs[cid]
            target_role = "summary" if v["role"] == "account" else "resource"
            members = [
                mid for mid, mv in vertices.items()
                if mv["role"] == target_role
                and ox <= orig_abs[mid][0] + mv["w"] / 2 <= ox + v["w"]
                and oy <= orig_abs[mid][1] + mv["h"] / 2 <= oy + v["h"]
            ]
            if members:
                overlay_members[cid] = members

    if reparent:
        _reparent_visual_children(vertices)

    children_of = {}
    for v in vertices.values():
        children_of.setdefault(v["parent"], []).append(v)

    SLOT_W, ROW_H, ICON_SIZE = 150.0, 144.0, 52.0
    SUBNET_HDR, AZ_HDR, VPC_HDR, OUTER_HDR = 46.0, 36.0, 42.0, 40.0

    def layout_leaf_container(cont, header_h=SUBNET_HDR, min_w=190.0, min_h=120.0):
        kids = children_of.get(cont["id"], [])
        res_kids = [k for k in kids if k["role"] == "resource"]
        cap_kids = [k for k in kids if k["role"] == "caption"]
        sub_conts = [k for k in kids if k["role"] not in ("resource", "caption", "badge", "title")]
        if sub_conts:
            return
        res_kids.sort(key=lambda r: (round(r["y"] / 40.0), r["x"], r["id"]))
        if res_kids:
            cols = max(1, min(len(res_kids), max(1, round((max(cont["w"], min_w) - 40.0) / SLOT_W))))
            rows = int(math.ceil(len(res_kids) / cols))
            for idx, r in enumerate(res_kids):
                row, col = divmod(idx, cols)
                if "mxgraph.aws4" in r["style"]:
                    r["w"] = ICON_SIZE
                    r["h"] = ICON_SIZE
                r["x"] = round(20.0 + col * SLOT_W + (SLOT_W - r["w"]) / 2.0, 1)
                r["y"] = round(header_h + 24.0 + row * ROW_H, 1)
            cont["w"] = _snap(max(min_w, cols * SLOT_W + 40.0), grid_size)
            cont["h"] = _snap(max(min_h, header_h + rows * ROW_H + 24.0), grid_size)
        else:
            for c in cap_kids:
                c["x"] = 20.0
                c["y"] = header_h + 14.0
                c["w"] = max(140.0, cont["w"] - 40.0)
            cont["w"] = _snap(max(min_w, cont["w"]), grid_size)
            cont["h"] = _snap(max(min_h, header_h + 56.0), grid_size)

    # 1. Align leaf containers (subnets, onprem, references, unplaced)
    for v in vertices.values():
        if v["role"] in ("subnet", "onprem", "references", "unplaced"):
            layout_leaf_container(v, header_h=SUBNET_HDR if v["role"] == "subnet" else 38.0)

    # 2. Align AZ columns and synchronize subnet tiers inside each VPC (or Region)
    vpcs = [v for v in vertices.values() if v["role"] == "vpc"]
    for vpc in vpcs:
        vkids = children_of.get(vpc["id"], [])
        azs = sorted([k for k in vkids if k["role"] == "az"], key=lambda a: (a["x"], a["id"]))
        top_res = sorted([k for k in vkids if k["role"] == "resource"], key=lambda r: (_BAND_ORDER.get(r["kind"], 3), r["x"], r["id"]))
        unplaced_boxes = [k for k in vkids if k["role"] == "unplaced"]

        # Layout top-of-VPC resource bands (gateways/endpoints -> load balancers -> target groups)
        bands = {}
        for r in top_res:
            bands.setdefault(_BAND_ORDER.get(r["kind"], 1), []).append(r)

        cur_y = VPC_HDR + 16.0
        max_band_w = 0.0
        for b_idx in sorted(bands):
            row_items = bands[b_idx]
            max_band_w = max(max_band_w, len(row_items) * SLOT_W + 56.0)
            for idx, r in enumerate(row_items):
                if "mxgraph.aws4" in r["style"]:
                    r["w"] = ICON_SIZE
                    r["h"] = ICON_SIZE
                r["x"] = round(28.0 + idx * SLOT_W + (SLOT_W - r["w"]) / 2.0, 1)
                r["y"] = round(cur_y + 16.0, 1)
            cur_y += ROW_H

        if top_res:
            cur_y += 24.0  # channel between top resources and AZ columns

        if azs:
            # Synchronize subnet widths within each AZ and tier heights across all AZs
            all_tiers = sorted({
                s["tier"] if s["tier"] is not None else 2
                for az in azs for s in children_of.get(az["id"], []) if s["role"] == "subnet"
            })
            tier_max_h = {t: 120.0 for t in all_tiers}
            az_inner_w = {}
            for az in azs:
                subs = [s for s in children_of.get(az["id"], []) if s["role"] == "subnet"]
                inner_w = max([s["w"] for s in subs] + [190.0])
                az_inner_w[az["id"]] = inner_w
                for s in subs:
                    if s["w"] < inner_w:
                        # Re-center resources horizontally when widening subnet
                        shift_x = (inner_w - s["w"]) / 2.0
                        for rk in children_of.get(s["id"], []):
                            if rk["role"] == "resource":
                                rk["x"] = round(rk["x"] + shift_x, 1)
                        s["w"] = inner_w
                    t = s["tier"] if s["tier"] is not None else 2
                    tier_max_h[t] = max(tier_max_h.get(t, 120.0), s["h"])

            # Compute deterministic Y offset per tier
            tier_y = {}
            ty = AZ_HDR + 12.0
            for t in all_tiers:
                tier_y[t] = ty
                ty += tier_max_h[t] + 16.0
            az_h = _snap(max(ty + 8.0, 180.0), grid_size)

            gutter = 36.0
            ax = 28.0
            for az in azs:
                subs = sorted([s for s in children_of.get(az["id"], []) if s["role"] == "subnet"],
                              key=lambda s: (s["tier"] if s["tier"] is not None else 2, s["y"], s["id"]))
                # Group by tier in case an AZ has 2 subnets of the same tier
                used_in_tier = {}
                for s in subs:
                    t = s["tier"] if s["tier"] is not None else 2
                    extra_idx = used_in_tier.get(t, 0)
                    used_in_tier[t] = extra_idx + 1
                    s["x"] = 16.0
                    s["y"] = tier_y[t] + extra_idx * (tier_max_h[t] + 16.0)
                    s["h"] = tier_max_h[t]
                extra_bottom = max([s["y"] + s["h"] + 16.0 for s in subs] + [az_h])
                az["x"] = ax
                az["y"] = cur_y
                az["w"] = _snap(az_inner_w[az["id"]] + 32.0, grid_size)
                az["h"] = _snap(extra_bottom, grid_size)
                ax += az["w"] + gutter

            common_az_h = max(az["h"] for az in azs)
            for az in azs:
                az["h"] = common_az_h
            cur_y += common_az_h + 24.0
            vpc_w = max(max_band_w, ax - gutter + 28.0)
        else:
            # Direct subnets inside VPC (without AZ wrapper)
            direct_subs = sorted([k for k in vkids if k["role"] == "subnet"],
                                 key=lambda s: (s["tier"] if s["tier"] is not None else 2, s["x"], s["id"]))
            if direct_subs:
                sx = 28.0
                max_sh = max(s["h"] for s in direct_subs)
                for s in direct_subs:
                    s["x"] = sx
                    s["y"] = cur_y
                    s["h"] = max_sh
                    sx += s["w"] + 28.0
                cur_y += max_sh + 24.0
                vpc_w = max(max_band_w, sx - 28.0 + 28.0)
            else:
                vpc_w = max(max_band_w, vpc["w"], 320.0)

        for up in unplaced_boxes:
            up["x"] = 28.0
            up["y"] = cur_y
            up["w"] = max(up["w"], vpc_w - 56.0)
            cur_y += up["h"] + 20.0

        vpc["w"] = _snap(max(vpc_w, 320.0), grid_size)
        vpc["h"] = _snap(max(cur_y + 8.0, 220.0), grid_size)

    # 3. Bottom-up auto-fit for outer containers (region -> cloud -> root "1")
    for outer_role in ("region", "cloud"):
        for cont in [v for v in vertices.values() if v["role"] == outer_role]:
            kids = [k for k in children_of.get(cont["id"], []) if k["role"] not in ("badge", "asg", "ecs_frame", "account")]
            if not kids:
                continue
            # Ensure children inside region/cloud have at least minimum top/left padding and don't overlap horizontally
            kids.sort(key=lambda k: (round(k["y"] / 60.0), k["x"], k["id"]))
            min_x = min(k["x"] for k in kids)
            min_y = min(k["y"] for k in kids)
            dx = (28.0 - min_x) if min_x < 24.0 else 0.0
            dy = (OUTER_HDR + 8.0 - min_y) if min_y < OUTER_HDR else 0.0
            for k in kids:
                k["x"] = round(k["x"] + dx, 1)
                k["y"] = round(k["y"] + dy, 1)
            # Resolve any horizontal sibling container overlaps inside region/cloud
            for i, a in enumerate(kids):
                for b in kids[i + 1:]:
                    box_a = (a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"])
                    box_b = (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])
                    if _boxes_overlap(box_a, box_b, pad=-12.0):
                        if b["x"] >= a["x"]:
                            b["x"] = a["x"] + a["w"] + 36.0
                        else:
                            b["y"] = a["y"] + a["h"] + 36.0
            max_r = max(k["x"] + k["w"] for k in kids)
            max_b = max(k["y"] + k["h"] for k in kids)
            cont["w"] = _snap(max(cont["w"], max_r + 28.0), grid_size)
            cont["h"] = _snap(max(cont["h"], max_b + 28.0), grid_size)

    # Also ensure top-level containers on parent=="1" (e.g., cloud and onprem) don't overlap
    top_conts = sorted(
        [v for v in vertices.values() if v["parent"] == "1" and v["role"] not in ("title", "caption", "asg", "ecs_frame", "account", "badge")],
        key=lambda v: (0 if v["role"] == "cloud" else 1, v["x"], v["id"]),
    )
    for i, a in enumerate(top_conts):
        for b in top_conts[i + 1:]:
            box_a = (a["x"], a["y"], a["x"] + a["w"], a["y"] + a["h"])
            box_b = (b["x"], b["y"], b["x"] + b["w"], b["y"] + b["h"])
            if _boxes_overlap(box_a, box_b, pad=-20.0):
                b["x"] = a["x"] + a["w"] + 48.0

    # 4. Re-anchor badges and recompute overlay frames (ASG / ECS / Account)
    abs_pos = _compute_abs(vertices)
    for cid, v in vertices.items():
        if v["role"] == "badge" and v["parent"] in vertices:
            p = vertices[v["parent"]]
            v["x"] = round(p["w"] - v["w"] / 2.0 - 2.0, 1)
            v["y"] = round(-v["h"] / 2.0 + 2.0, 1)
        elif cid in overlay_members:
            mids = [m for m in overlay_members[cid] if m in vertices]
            if mids:
                px, py = abs_pos.get(v["parent"], (0.0, 0.0))
                left = min(abs_pos[m][0] for m in mids) - 14.0
                top = min(abs_pos[m][1] for m in mids) - 18.0
                right = max(abs_pos[m][0] + vertices[m]["w"] for m in mids) + 14.0
                bottom = max(abs_pos[m][1] + vertices[m]["h"] + 48.0 for m in mids) + 18.0
                v["x"] = round(left - px, 1)
                v["y"] = round(top - py, 1)
                v["w"] = round(right - left, 1)
                v["h"] = round(bottom - top, 1)

    # Write updated vertex geometry back to XML elements
    for v in vertices.values():
        v["geo"].set("x", f"{v['x']:g}")
        v["geo"].set("y", f"{v['y']:g}")
        v["geo"].set("width", f"{v['w']:g}")
        v["geo"].set("height", f"{v['h']:g}")

    # 5. Obstacle-avoiding orthogonal edge routing
    if route_edges and edges:
        _route_edges_orthogonally(vertices, edges)

    # 6. Apply DrawioSpec style & wrapper normalization if requested
    if normalize_styles or spec is not None:
        cfg = resolve_spec(spec)
        _apply_spec_to_page(graph_root, vertices, edges, cfg)


def _route_edges_orthogonally(vertices, edges):
    """Compute explicit exit/entry border constraints and obstacle-avoiding orthogonal waypoints for all edges."""
    abs_pos = _compute_abs(vertices)
    obstacles = {
        cid: (abs_pos[cid][0] - 4.0, abs_pos[cid][1] - 4.0, abs_pos[cid][0] + v["w"] + 4.0, abs_pos[cid][1] + v["h"] + 4.0)
        for cid, v in vertices.items() if v["role"] == "resource"
    }

    # First pass: decide exit/entry faces for each valid edge
    face_Attachment = {}  # (node_id, face) -> list of (sort_coord, edge_dict, is_source)
    valid_edges = []
    for e in edges:
        src, dst = e["source"], e["target"]
        if src not in vertices or dst not in vertices:
            continue
        sx, sy = abs_pos[src]
        dx, dy = abs_pos[dst]
        sv, dv = vertices[src], vertices[dst]
        scx, scy = sx + sv["w"] / 2.0, sy + sv["h"] / 2.0
        dcx, dcy = dx + dv["w"] / 2.0, dy + dv["h"] / 2.0
        vx, vy = dcx - scx, dcy - scy
        if abs(vy) >= abs(vx) * 0.65:
            exit_face = "S" if vy >= 0 else "N"
            entry_face = "N" if vy >= 0 else "S"
            sort_s, sort_d = dcx, scx
        else:
            exit_face = "E" if vx >= 0 else "W"
            entry_face = "W" if vx >= 0 else "E"
            sort_s, sort_d = dcy, scy
        e["_faces"] = (exit_face, entry_face)
        face_Attachment.setdefault((src, exit_face), []).append((sort_s, e["id"], e, True))
        face_Attachment.setdefault((dst, entry_face), []).append((sort_d, e["id"], e, False))
        valid_edges.append(e)

    # Distribute port fractions along each face so edges sharing a face don't overlap at the border
    ports = {}  # (edge_id, "exit"|"entry") -> float in [0.2, 0.8]
    for (_nid, _face), items in face_Attachment.items():
        items.sort(key=lambda item: (item[0], item[1]))
        n = len(items)
        for i, (_, eid, _, is_src) in enumerate(items):
            frac = 0.5 if n == 1 else round(0.2 + 0.6 * (i / (n - 1)), 3)
            ports[(eid, "exit" if is_src else "entry")] = frac

    def port_coords(nid, face, frac):
        x, y = abs_pos[nid]
        w, h = vertices[nid]["w"], vertices[nid]["h"]
        if face == "N":
            return (round(x + frac * w, 1), round(y, 1)), (frac, 0.0)
        if face == "S":
            return (round(x + frac * w, 1), round(y + h, 1)), (frac, 1.0)
        if face == "W":
            return (round(x, 1), round(y + frac * h, 1)), (0.0, frac)
        return (round(x + w, 1), round(y + frac * h, 1)), (1.0, frac)

    def hits_obstacle(p1, p2, ignore_ids):
        seg = (min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]) + 0.1, max(p1[1], p2[1]) + 0.1)
        for oid, obox in obstacles.items():
            if oid not in ignore_ids and _boxes_overlap(seg, obox):
                return True
        return False

    def path_clear(pts, ignore_ids):
        return all(not hits_obstacle(a, b, ignore_ids) for a, b in zip(pts, pts[1:]))

    used_mid_lines = []
    for idx, e in enumerate(valid_edges):
        src, dst = e["source"], e["target"]
        exit_face, entry_face = e["_faces"]
        p_start, (ex_x, ex_y) = port_coords(src, exit_face, ports[(e["id"], "exit")])
        p_end, (en_x, en_y) = port_coords(dst, entry_face, ports[(e["id"], "entry")])
        ignore = {src, dst}

        waypoints = []
        if exit_face in ("N", "S") and entry_face in ("N", "S"):
            # Vertical-Horizontal-Vertical Z-route
            base_mid_y = round((p_start[1] + p_end[1]) / 2.0, 1)
            chosen_y = base_mid_y
            for step in range(0, 18):
                for sign in (1, -1):
                    cand_y = round(base_mid_y + sign * step * 10.0, 1)
                    if any(abs(cand_y - uy) < 6.0 for uy in used_mid_lines):
                        continue
                    cand_pts = [p_start, (p_start[0], cand_y), (p_end[0], cand_y), p_end]
                    if path_clear(cand_pts, ignore):
                        chosen_y = cand_y
                        break
                else:
                    continue
                break
            used_mid_lines.append(chosen_y)
            waypoints = [(p_start[0], chosen_y), (p_end[0], chosen_y)]
            if not path_clear([p_start, *waypoints, p_end], ignore):
                # Bypass around obstacles via an outer vertical gutter X
                sx, _ = abs_pos[src]
                dx, _ = abs_pos[dst]
                bypass_x = round(min(sx, dx) - 28.0 - (idx % 4) * 8.0, 1)
                y1 = round(p_start[1] + (18.0 if exit_face == "S" else -18.0), 1)
                y2 = round(p_end[1] + (-18.0 if entry_face == "N" else 18.0), 1)
                waypoints = [(p_start[0], y1), (bypass_x, y1), (bypass_x, y2), (p_end[0], y2)]
        else:
            # Horizontal-Vertical-Horizontal Z-route
            base_mid_x = round((p_start[0] + p_end[0]) / 2.0, 1)
            chosen_x = base_mid_x
            for step in range(0, 18):
                for sign in (1, -1):
                    cand_x = round(base_mid_x + sign * step * 10.0, 1)
                    cand_pts = [p_start, (cand_x, p_start[1]), (cand_x, p_end[1]), p_end]
                    if path_clear(cand_pts, ignore):
                        chosen_x = cand_x
                        break
                else:
                    continue
                break
            waypoints = [(chosen_x, p_start[1]), (chosen_x, p_end[1])]

        ox, oy = abs_pos.get(e["parent"], (0.0, 0.0))  # draw.io offsets an edge's points by its parent's origin
        e["points"] = [(round(px - ox, 1), round(py - oy, 1)) for px, py in waypoints]
        e["style"] = patch_style(
            e["style"] or "edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;",
            edgeStyle="orthogonalEdgeStyle",
            exitX=f"{ex_x:g}", exitY=f"{ex_y:g}", exitDx="0", exitDy="0",
            entryX=f"{en_x:g}", entryY=f"{en_y:g}", entryDx="0", entryDy="0",
        )
        e["cell"].set("style", e["style"])
        if e["geo"] is None:
            e["geo"] = SubElement(e["cell"], "mxGeometry", {"relative": "1", "as": "geometry"})
        arr = e["geo"].find("Array[@as='points']")
        if arr is not None:
            e["geo"].remove(arr)
        arr = SubElement(e["geo"], "Array", {"as": "points"})
        for px, py in e["points"]:
            SubElement(arr, "mxPoint", {"x": f"{px:g}", "y": f"{py:g}"})


def _apply_spec_to_page(graph_root, vertices, edges, cfg):
    """Apply `DrawioSpec` styles, header visibility, and wrapper tags (`object`/`UserObject`/`mxCell`) to `graph_root`."""
    if not cfg.include_header:
        for hid in ("heading", "scope"):
            if hid in vertices:
                graph_root.remove(vertices[hid]["wrapper"])
                del vertices[hid]
        top_ys = [v["y"] for v in vertices.values() if v["parent"] == "1"]
        if top_ys:
            dy = cfg.margin_y - min(top_ys)
            for v in vertices.values():
                if v["parent"] == "1":
                    v["y"] = round(v["y"] + dy, 1)
                    v["geo"].set("y", f"{v['y']:g}")
            for e in edges:
                if e["geo"] is not None and e["parent"] == "1":  # nested edges move with their parent
                    arr = e["geo"].find("Array[@as='points']")
                    if arr is not None:
                        for pt in arr.findall("mxPoint"):
                            pt.set("y", f"{_number(pt, 'y', 0.0, e['id']) + dy:g}")

    for v in vertices.values():
        role = v["role"]
        if role == "subnet":
            if v["tier"] == 0:
                role = "public_subnet"
            elif cfg.isolated_subnet_distinct and v["tier"] == 3:
                role = "isolated_subnet"
            else:
                role = "private_subnet"
        if role in cfg.styles:
            v["style"] = cfg.role_style(role)
        elif cfg.font_family != "Arial":
            v["style"] = patch_style(v["style"], fontFamily=cfg.font_family)
        v["cell"].set("style", v["style"])

    edge_base = cfg.effective_edge_style()
    _, base_kv = parse_style(edge_base)
    for e in edges:
        _, ekv = parse_style(e["style"])
        merged = {**ekv}
        for k in ("fontFamily", "endArrow", "endFill", "rounded"):
            if k in base_kv:
                merged[k] = base_kv[k]
        e["style"] = format_style([], merged)
        e["cell"].set("style", e["style"])

    # Normalize wrapper tags if needed (`object` vs `UserObject` vs `mxCell`)
    current_children = list(graph_root)
    for el in current_children:
        if el.tag == "mxCell" and el.get("id") in ("0", "1"):
            continue
        inner = el if el.tag == "mxCell" else el.find("mxCell")
        if inner is None:
            continue
        cid = el.get("id") or inner.get("id") or ""
        label = el.get("label") or el.get("value") or inner.get("value") or ""
        if cfg.cell_wrapper == "mxCell" and el.tag != "mxCell":
            idx = list(graph_root).index(el)
            graph_root.remove(el)
            inner.set("id", cid)
            inner.set("value", label)
            graph_root.insert(idx, inner)
        elif cfg.cell_wrapper in ("object", "UserObject") and el.tag != cfg.cell_wrapper:
            idx = list(graph_root).index(el)
            graph_root.remove(el)
            if el.tag == "mxCell":
                inner.attrib.pop("id", None)
                inner.attrib.pop("value", None)
                wrapper = Element(cfg.cell_wrapper, {"id": cid, "label": label})
                wrapper.append(inner)
                graph_root.insert(idx, wrapper)
            else:
                el.tag = cfg.cell_wrapper
                graph_root.insert(idx, el)


def align_drawio_xml(
    xml_text,
    *,
    mode="preserve",
    spec=None,
    reparent=True,
    route_edges=True,
    normalize_styles=False,
    grid_size=10,
) -> str:
    """Auto-align, resize containers, and orthogonally route an existing `drawio.xml` document.

    - `mode="preserve"` (default): preserves existing cell IDs, labels, and custom attributes while fixing
      containment, grid alignment, cross-AZ subnet tier heights, container bounds, and orthogonal edge routes.
    - `mode="rebuild"`: imports the XML into a Diagram Bot `Snapshot` and renders it through `generate_document`.
    """
    if mode == "rebuild":
        read_document(xml_text)
        snapshot = import_drawio_xml(xml_text)
        target_spec = resolve_spec(spec) if spec is not None else spec_from_xml(xml_text)
        vpcs = [n["resource_id"] for n in snapshot["graph"]["nodes"] if n["kind"] == "vpc"]
        view = "detail" if len(vpcs) == 1 else "overview"
        vpc_id = vpcs[0] if len(vpcs) == 1 else None
        return generate_document(snapshot, vpc_id=vpc_id, view=view, spec=target_spec)
    if mode != "preserve":
        raise InputError(f"알 수 없는 정렬 모드입니다: {mode} ('preserve' 또는 'rebuild')")

    root = read_document(xml_text)
    cfg = resolve_spec(spec) if spec is not None else None
    if cfg is not None and root.tag == "mxfile":
        root.set("host", cfg.host)

    models = root.findall("diagram/mxGraphModel") if root.tag == "mxfile" else [root]
    for model in models:
        if cfg is not None:
            model.set("background", cfg.background)
            model.set("gridSize", str(cfg.grid_size))
        graph_root = model.find("root")
        if graph_root is not None:
            _align_page_in_place(
                graph_root,
                spec=cfg,
                reparent=reparent,
                route_edges=route_edges,
                normalize_styles=normalize_styles,
                grid_size=cfg.grid_size if cfg is not None else grid_size,
            )
    return tostring(root, encoding="unicode")


def import_drawio_xml(xml_text, *, title=None, region="ap-northeast-2", account="000000000000") -> dict:
    """Convert an existing AWS `drawio.xml` diagram into a valid Diagram Bot `Snapshot` (`raw` + `graph`)."""
    root = read_document(xml_text)
    graph_root = root.find("diagram/mxGraphModel/root") if root.tag == "mxfile" else root.find("root")
    vertices, edges = _parse_page_model(graph_root)
    _reparent_visual_children(vertices)

    # Detect region / title from heading or region containers if present
    for v in vertices.values():
        if v["role"] == "title" and not title:
            title = v["label"].split("·")[0].replace("SAMPLE", "").replace("DESIGN", "").strip(" /")
        m = re.search(r"\b(ap|us|eu|sa|ca|me|af)-[a-z0-9]+-\d\b", v["label"])
        if m:
            region = m.group(0)

    sample = Sample("imported", title or "draw.io 가져온 아키텍처", ["draw.io XML에서 가져온 구성"], region=region, account=account)

    def ancestor_of_role(cid, target_role):
        cur = vertices.get(cid)
        seen = set()
        while cur and cur["id"] not in seen:
            seen.add(cur["id"])
            if cur["role"] == target_role:
                return cur
            cur = vertices.get(cur["parent"])
        return None

    # 1. VPCs
    vpc_cells = [v for v in vertices.values() if v["role"] == "vpc"]
    vpc_map = {}  # vertex_id -> vpc_id
    if not vpc_cells:
        default_vpc = sample.vpc("vpc-main", "10.0.0.0/16", "main-vpc")
    else:
        for idx, v in enumerate(vpc_cells):
            raw_name = (v["label"].splitlines()[0] if v["label"] else f"vpc-{idx + 1}").strip()
            name = re.sub(r"\s*\([^)]*\)", "", raw_name).strip() or raw_name
            cidr_m = re.search(r"\b\d+\.\d+\.\d+\.\d+/\d+\b", v["label"])
            cidr = cidr_m.group(0) if cidr_m else f"10.{idx}.0.0/16"
            slug_id = "vpc-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or f"imported-{idx + 1}")
            if slug_id in vpc_map.values():
                slug_id = f"{slug_id}-{idx + 1}"
            vpc_map[v["id"]] = sample.vpc(slug_id, cidr, name)
        default_vpc = next(iter(vpc_map.values()))

    # 2. AZs & Subnets
    az_letters = "abcdef"
    az_map = {}
    for idx, v in enumerate(sorted([v for v in vertices.values() if v["role"] == "az"], key=lambda a: (a["x"], a["id"]))):
        m = re.search(r"([a-f])\b", v["label"].lower())
        az_map[v["id"]] = m.group(1) if m else az_letters[idx % 3]

    subnet_cells = sorted([v for v in vertices.values() if v["role"] == "subnet"], key=lambda s: (s["y"], s["x"], s["id"]))
    subnet_map = {}  # vertex_id -> subnet_id
    vpc_tier_subnets = {}  # (vpc_id, tier_name) -> [subnet_ids]
    tier_names = {0: "public", 1: "web", 2: "app", 3: "data"}

    if not subnet_cells:
        # Synthesize default public/app/data subnets across 2 AZs so resources can be placed cleanly
        for ai, az_ch in enumerate(("a", "b")):
            pub = sample.subnet(f"subnet-pub-{az_ch}", default_vpc, az_ch, f"10.0.{ai}.0/24", f"public-{az_ch}", "public")
            app = sample.subnet(f"subnet-app-{az_ch}", default_vpc, az_ch, f"10.0.{ai + 10}.0/24", f"app-{az_ch}", "app")
            dat = sample.subnet(f"subnet-data-{az_ch}", default_vpc, az_ch, f"10.0.{ai + 20}.0/24", f"data-{az_ch}", "data")
            vpc_tier_subnets.setdefault((default_vpc, "public"), []).append(pub)
            vpc_tier_subnets.setdefault((default_vpc, "app"), []).append(app)
            vpc_tier_subnets.setdefault((default_vpc, "data"), []).append(dat)
    else:
        for idx, s in enumerate(subnet_cells):
            vpc_cell = ancestor_of_role(s["id"], "vpc")
            vpc_id = vpc_map.get(vpc_cell["id"], default_vpc) if vpc_cell else default_vpc
            az_cell = ancestor_of_role(s["id"], "az")
            az_ch = az_map.get(az_cell["id"], az_letters[idx % 2]) if az_cell else az_letters[idx % 2]
            t_name = tier_names.get(s["tier"] if s["tier"] is not None else 2, "app")
            cidr_m = re.search(r"\b\d+\.\d+\.\d+\.\d+/\d+\b", s["label"])
            cidr = cidr_m.group(0) if cidr_m else f"10.0.{idx + 1}.0/24"
            raw_name = (s["label"].splitlines()[0] if s["label"] else f"{t_name}-{az_ch}").strip()
            name = re.sub(r"\s*\([^)]*\)", "", raw_name).strip() or raw_name
            sid = f"subnet-{idx + 1}-{az_ch}"
            sample.subnet(sid, vpc_id, az_ch, cidr, name, t_name)
            subnet_map[s["id"]] = sid
            vpc_tier_subnets.setdefault((vpc_id, t_name), []).append(sid)

    def pick_subnet(v, preferred_tier="app"):
        sub_cell = ancestor_of_role(v["id"], "subnet")
        if sub_cell and sub_cell["id"] in subnet_map:
            return subnet_map[sub_cell["id"]]
        vpc_cell = ancestor_of_role(v["id"], "vpc")
        vid = vpc_map.get(vpc_cell["id"], default_vpc) if vpc_cell else default_vpc
        for t in (preferred_tier, "app", "web", "public", "data"):
            if vpc_tier_subnets.get((vid, t)):
                return vpc_tier_subnets[(vid, t)][0]
        return sample.raw["subnets"][0]["SubnetId"]

    def pick_vpc(v):
        vpc_cell = ancestor_of_role(v["id"], "vpc")
        return vpc_map.get(vpc_cell["id"], default_vpc) if vpc_cell else default_vpc

    # 3. Resources
    lb_by_vertex = {}
    tg_by_vertex = {}
    ec2_by_vertex = {}
    res_cells = [v for v in vertices.values() if v["role"] == "resource"]

    def az_letter_of(subnet_id):
        sub = next((s for s in sample.raw["subnets"] if s["SubnetId"] == subnet_id), None)
        return sub["AvailabilityZone"][-1] if sub else "a"

    for idx, v in enumerate(res_cells):
        kind = v["kind"]
        name = (v["label"].splitlines()[0] if v["label"] else f"{kind}-{idx + 1}").strip()[:48] or f"{kind}-{idx + 1}"
        vid = pick_vpc(v)
        if kind == "internet_gateway":
            sample.internet_gateway(f"igw-{idx + 1}", vid, name)
        elif kind == "nat_gateway":
            sample.nat_gateway(f"nat-{idx + 1}", vid, pick_subnet(v, "public"), name)
        elif kind in ("application", "network", "gateway", "load_balancer"):
            lb_type = kind if kind in ("application", "network", "gateway") else "application"
            sub_cell = ancestor_of_role(v["id"], "subnet")
            is_internal = (sub_cell and sub_cell.get("tier") not in (0, None)) or "internal" in v["label"].lower() or "내부" in v["label"]
            subs = vpc_tier_subnets.get((vid, "app" if is_internal else "public")) or [pick_subnet(v, "public")]
            lb_by_vertex[v["id"]] = sample.load_balancer(
                re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-") or f"lb-{idx + 1}",
                vid, subs[:2], kind=lb_type, scheme="internal" if is_internal else "internet-facing",
            )
        elif kind == "target_group":
            tg_by_vertex[v["id"]] = name
        elif kind in ("ec2", "p3", "p4d", "p4de", "g4dn", "g4ad", "trainium", "inferentia", "dl1"):
            itype = {"p3": "p3.2xlarge", "p4d": "p4d.24xlarge", "g4dn": "g4dn.xlarge", "trainium": "trn1.32xlarge", "inferentia": "inf2.xlarge"}.get(kind, "t3.medium")
            ec2_by_vertex[v["id"]] = sample.instance(f"i-imp{idx + 1:04d}", pick_subnet(v, "app"), name, kind=itype)
        elif kind in ("db_instance", "db_standby", "documentdb", "neptune"):
            engine = "docdb" if kind == "documentdb" else ("neptune" if kind == "neptune" else "postgres")
            subs = vpc_tier_subnets.get((vid, "data")) or [pick_subnet(v, "data")]
            primary_az = az_letter_of(subs[0])
            standby_az = az_letter_of(subs[1]) if len(subs) > 1 and az_letter_of(subs[1]) != primary_az else None
            sample.database(
                re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or f"db-{idx + 1}",
                vid, subs[:2], primary=primary_az, engine=engine, standby=standby_az,
            )
        elif kind == "aurora":
            subs = (vpc_tier_subnets.get((vid, "data")) or [pick_subnet(v, "data")])[:2]
            writer = az_letter_of(subs[0])
            readers = [az_letter_of(s) for s in subs[1:]]
            sample.aurora(re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or f"aurora-{idx + 1}", vid, subs, writer=writer, readers=readers)
        elif kind in ("redis", "valkey", "memcached"):
            subs = (vpc_tier_subnets.get((vid, "data")) or [pick_subnet(v, "data")])[:2]
            primary = az_letter_of(subs[0])
            replicas = [az_letter_of(s) for s in subs[1:]]
            sample.redis(re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or f"cache-{idx + 1}", vid, subs, primary=primary, replicas=replicas)
        elif kind == "eks_cluster":
            subs = (vpc_tier_subnets.get((vid, "app")) or [pick_subnet(v, "app")])[:2]
            sample.eks(re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-") or f"eks-{idx + 1}", vid, subs, nodegroups=[])
        elif kind in ("ecs_service", "ecs_task"):
            subs = (vpc_tier_subnets.get((vid, "app")) or [pick_subnet(v, "app")])[:2]
            tasks = [(s, sample.address(s, 50 + i)) for i, s in enumerate(subs)]
            sample.ecs_service("imported-cluster", re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-") or f"ecs-{idx + 1}", vid, tasks)
        elif kind == "lambda_function":
            subs = (vpc_tier_subnets.get((vid, "app")) or [pick_subnet(v, "app")])[:2]
            sample.function(re.sub(r"[^a-zA-Z0-9-_]+", "-", name).strip("-") or f"fn-{idx + 1}", vid, subs)
        elif kind in ("vpc_endpoint", "bedrock", "sagemaker"):
            svc = "bedrock-runtime" if kind == "bedrock" else ("sagemaker.runtime" if kind == "sagemaker" else "ssm")
            subs = (vpc_tier_subnets.get((vid, "app")) or [pick_subnet(v, "app")])[:2]
            sample.interface_endpoint(f"vpce-imp{idx + 1:04d}", vid, svc, subs)
        elif kind in ("efs_standard", "efs_one_zone"):
            subs = (vpc_tier_subnets.get((vid, "data")) or [pick_subnet(v, "data")])[:2]
            sample.efs(f"fs-imp{idx + 1:04d}", name, vid, subs, one_zone=az_letter_of(subs[0]) if kind == "efs_one_zone" else None)
        elif kind in ("fsx_lustre", "fsx_file_system", "fsx_ontap", "fsx_openzfs", "fsx_windows"):
            sample.fsx_lustre(f"fs-fsx{idx + 1:04d}", name, vid, pick_subnet(v, "data"))
        elif kind in ("sagemaker_endpoint",):
            sample.sagemaker_endpoint(re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-") or f"sm-{idx + 1}", "ml.g5.2xlarge", 2, [pick_subnet(v, "app")])
        elif kind == "cloudfront_distribution":
            origins = list(lb_by_vertex.values())[:1] or ["imported-origin"]
            sample.cloudfront(f"EIMP{idx + 1:08d}", name, origins)

    # Wire Target Groups from edges (LB -> TG -> EC2, or LB -> EC2 directly)
    adj = {}
    for e in edges:
        adj.setdefault(e["source"], []).append(e["target"])
        adj.setdefault(e["target"], []).append(e["source"])

    for tg_vid, tg_name in tg_by_vertex.items():
        neighbors = adj.get(tg_vid, [])
        lbs = [lb_by_vertex[n] for n in neighbors if n in lb_by_vertex]
        targets = [ec2_by_vertex[n] for n in neighbors if n in ec2_by_vertex]
        vid = pick_vpc(vertices[tg_vid])
        sample.target_group(
            re.sub(r"[^a-zA-Z0-9-]+", "-", tg_name).strip("-") or "tg-imported",
            vid, "HTTP", 80, targets or list(ec2_by_vertex.values())[:2], lbs=lbs or list(lb_by_vertex.values())[:1],
        )

    if lb_by_vertex and ec2_by_vertex and not tg_by_vertex:
        sample.target_group("tg-imported", default_vpc, "HTTP", 80, list(ec2_by_vertex.values()), lbs=list(lb_by_vertex.values())[:1])

    # Ensure basic route tables exist for public/private/isolated subnet classification
    if not sample.raw["route_tables"] and sample.raw["subnets"]:
        for vid in set(vpc_map.values()) | {default_vpc}:
            v_subs = [s for s in sample.raw["subnets"] if s["VpcId"] == vid]
            if not v_subs:
                continue
            igw_id = next((g["InternetGatewayId"] for g in sample.raw["internet_gateways"]
                           if any(a.get("VpcId") == vid for a in g.get("Attachments", []))), None)
            if not igw_id:
                igw_id = sample.internet_gateway(f"igw-{vid}", vid, f"{vid}-igw")
            pub_subs = [s["SubnetId"] for s in v_subs if s.get("MapPublicIpOnLaunch")]
            priv_subs = [
                s["SubnetId"] for s in v_subs
                if not s.get("MapPublicIpOnLaunch")
                and not any(t.get("Key") == "Tier" and t.get("Value") == "data" for t in s.get("Tags", []))
            ]
            if pub_subs:
                sample.route_table(f"rtb-{vid}-public", vid, f"{vid}-public", subnets=pub_subs, gateway=igw_id)
            nat_id = next((n["NatGatewayId"] for n in sample.raw["nat_gateways"] if n.get("VpcId") == vid), None)
            if not nat_id and pub_subs:
                nat_id = sample.nat_gateway(f"nat-{vid}-a", vid, pub_subs[0], f"{vid}-nat-a")
            if priv_subs:
                sample.route_table(f"rtb-{vid}-private", vid, f"{vid}-private", subnets=priv_subs, nat=nat_id)
            sample.route_table(f"rtb-{vid}-main", vid, f"{vid}-isolated", main=True)

    snap = sample.snapshot()
    snap["graph"] = build_graph(snap)
    return snap


def adopt_company_xml(
    xml_or_path,
    *,
    name="company-standard",
    title=None,
    region="ap-northeast-2",
    account="000000000000",
    set_default=False,
    save_spec=None,
    allow_path=True,
    register=True,
) -> dict:
    """Adopt a company standard architecture `drawio.xml` file or string as both a `DrawioSpec` and a `Design` standard.

    1. Extracts the company's `DrawioSpec` (`spec_from_xml`: wrapper, container hierarchy, compact/full subnet labels,
       container/subnet/resource styles, font, and arrow styles).
    2. Aligns and orthogonally routes the template XML (`align_drawio_xml(..., mode="preserve")`).
    3. Imports the template XML into a Diagram Bot `Snapshot` (`import_drawio_xml`) and registers it in
       `designer.COMPANY_STANDARDS` under `name` so `Design().apply_standard(name)` loads and extends it.
    4. Renders a standardized `drawio.xml` from the imported snapshot using the extracted company `DrawioSpec`.
    5. Optionally sets the extracted `DrawioSpec` as the process-wide default (`set_default=True`) and/or saves it as JSON (`save_spec`).

    `register=False` skips step 3's registration (the HTTP route registers only for an admin's make_default); a name that
    collides with a built-in standard is refused. `allow_path=False` accepts XML text only (request data).
    """
    if not allow_path and not (isinstance(xml_or_path, str) and xml_or_path.lstrip().startswith("<")):
        raise InputError("draw.io XML 본문이 필요합니다 (파일 경로는 CLI·라이브러리에서만).")
    if isinstance(xml_or_path, Path) or (isinstance(xml_or_path, str) and not xml_or_path.lstrip().startswith("<") and Path(xml_or_path).is_file()):
        path = Path(xml_or_path)
        xml_text = path.read_text(encoding="utf-8")
        if name == "company-standard":
            name = re.sub(r"[^a-z0-9-]+", "-", path.stem.lower()).strip("-") or "company-standard"
    else:
        xml_text = str(xml_or_path)

    try:
        std_key = slug(name)
    except ValueError as exc:
        raise InputError(str(exc)) from exc
    if register and std_key in STANDARDS:
        raise InputError(f"기본 표준 아키텍처와 같은 이름은 쓸 수 없습니다: {std_key}")
    before = inspect_drawio_xml(xml_text)
    spec = spec_from_xml(xml_text, name=name)
    aligned_xml = align_drawio_xml(xml_text, spec=spec, mode="preserve")
    after = inspect_drawio_xml(aligned_xml)
    snapshot = import_drawio_xml(aligned_xml, title=title or f"사내 표준 ({name})", region=region, account=account)
    if register:
        register_standard(std_key, lambda: copy.deepcopy(snapshot), description=title or f"사내 표준 아키텍처 ({name})")

    vpc_id = snapshot["raw"]["vpcs"][0]["VpcId"] if snapshot["raw"].get("vpcs") else None
    standardized_xml = generate_document(snapshot, view="detail" if vpc_id else "overview", vpc_id=vpc_id, spec=spec)

    if set_default:
        set_default_spec(spec)
    if save_spec:
        spec_path = Path(save_spec)
        spec_path.parent.mkdir(parents=True, exist_ok=True)
        spec_path.write_text(json.dumps(spec.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "name": std_key,
        "spec": spec,
        "snapshot": snapshot,
        "aligned_xml": aligned_xml,
        "standardized_xml": standardized_xml,
        "before": before,
        "after": after,
    }

