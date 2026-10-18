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
import ipaddress
import json
import math
from pathlib import Path
import re
from xml.etree.ElementTree import Element, SubElement, tostring

from .designer import STANDARDS, register_standard, slug
from .drawio import generate_document
from .drawio_spec import (AZ_CODE, REGION_CODE, classify_cell, format_style, iter_cells, pages, parse_style, patch_style,
                          read_document, resolve_spec, set_default_spec, spec_from_xml)
from .errors import InputError
from .graph import build_graph
from .samples import Sample

__all__ = ["adopt_company_xml", "align_drawio_xml", "align_report", "import_drawio_xml", "inspect_drawio_xml"]
# Attributes draw.io reads on an <mxCell>; anything else on a bare mxCell moves to the wrapper when one is added.
MXCELL_ATTRIBUTES = {"id", "value", "style", "vertex", "edge", "parent", "source", "target", "connectable", "collapsed", "visible"}

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


def _parse_page_model(graph_root):
    """Parse vertices and edges from a single `<root>` element into structured dicts."""
    vertices = {}
    edges = []
    cells = [cell for cell in iter_cells(graph_root) if cell[2] not in ("0", "1", "", None)]
    edge_ids = {cid for _, inner, cid, _ in cells if inner.get("edge") == "1"}
    parents = {inner.get("parent") for _, inner, _, _ in cells}
    for wrapper, inner, cid, label in cells:
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
            if parent in edge_ids or geo.get("relative") == "1":
                role, kind, tier = "label", "label", None  # an edge's label cell moves with its edge
            else:
                role, kind, tier = classify_cell(label, style_str, wrapper.attrib, cell_id=cid, container=cid in parents,
                                                 small=w <= 28 and h <= 28)
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


class _Grid:
    """Uniform-grid index over boxes (x1, y1, x2, y2): a query visits only the boxes in the cells it covers.

    Boxes or queries spanning more than SPAN cells (huge containers, page-long segments) fall back to a scan."""
    SIZE, SPAN = 160.0, 1024

    def __init__(self, boxes):
        self.boxes, self.cells, self.wide = boxes, {}, []
        self.checks = 0  # candidate boxes visited, for tests that bound the work rather than the time
        for key, box in boxes.items():
            span = self._span(box)
            if span is None:
                self.wide.append(key)
            else:
                for cell in span:
                    self.cells.setdefault(cell, []).append(key)

    def _span(self, box):
        x1, y1, x2, y2 = (int(math.floor(c / self.SIZE)) for c in box)
        if (x2 - x1 + 1) * (y2 - y1 + 1) > self.SPAN:
            return None
        return ((gx, gy) for gx in range(x1, x2 + 1) for gy in range(y1, y2 + 1))

    def near(self, box):
        """Keys of the boxes that may intersect `box` (a superset), each once, in insertion order."""
        span = self._span(box)
        if span is None:
            keys = list(self.boxes)
        else:
            seen = dict.fromkeys(self.wide)
            for cell in span:
                seen.update(dict.fromkeys(self.cells.get(cell, ())))
            keys = list(seen)
        self.checks += len(keys)
        return keys


def _boxes_overlap(a, b, pad=0.0):
    return (a[0] + pad) < (b[2] - pad) and (b[0] + pad) < (a[2] - pad) and (a[1] + pad) < (b[3] - pad) and (b[1] + pad) < (a[3] - pad)


def _segment_hits(p1, p2, box):
    """Whether the segment p1-p2 passes through the box (Liang-Barsky clipping; exact for diagonal segments too)."""
    (x1, y1), (x2, y2) = p1, p2
    t0, t1, dx, dy = 0.0, 1.0, x2 - x1, y2 - y1
    for p, q in ((-dx, x1 - box[0]), (dx, box[2] - x1), (-dy, y1 - box[1]), (dy, box[3] - y1)):
        if p == 0:
            if q < 0:
                return False
        elif p < 0:
            t0 = max(t0, q / p)
        else:
            t1 = min(t1, q / p)
        if t0 > t1:
            return False
    return True


ISSUES = ("overlaps", "out_of_bounds", "unaligned_tiers", "unrouted_edges", "edge_collisions")
FREE_ROLES = ("annotation", "label", "group")  # cells align and import leave where they are
OVERLAY_ROLES = ("asg", "account", "ecs_frame", "badge", "title", "caption") + FREE_ROLES
CONTAINER_ROLES = {"cloud", "region", "account", "vpc", "az", "subnet", "onprem", "references", "unplaced", "summary"}


def _inspect_page(graph_root):
    """Layout and routing issues of one page, in page coordinates (edge points offset by their parent's origin)."""
    vertices, edges = _parse_page_model(graph_root)
    abs_pos = _compute_abs(vertices)
    report = {"vertices": vertices, "edges": edges, **{key: [] for key in ISSUES}}

    by_parent = {}
    for cid, v in vertices.items():
        if v["role"] not in OVERLAY_ROLES:
            by_parent.setdefault(v["parent"], []).append(cid)
    for members in by_parent.values():
        boxes = {cid: (abs_pos[cid][0], abs_pos[cid][1], abs_pos[cid][0] + vertices[cid]["w"], abs_pos[cid][1] + vertices[cid]["h"])
                 for cid in members}
        order, grid = {cid: i for i, cid in enumerate(members)}, _Grid(boxes)
        for a_id in members:
            for b_id in grid.near(boxes[a_id]):
                if order[b_id] > order[a_id] and _boxes_overlap(boxes[a_id], boxes[b_id], pad=0.5):
                    report["overlaps"].append((a_id, b_id))

    for cid, v in vertices.items():
        pid = v["parent"]
        if pid in vertices and v["role"] not in OVERLAY_ROLES:
            p = vertices[pid]
            if v["x"] < -1 or v["y"] < -1 or (v["x"] + v["w"]) > (p["w"] + 1) or (v["y"] + v["h"]) > (p["h"] + 1):
                report["out_of_bounds"].append((cid, pid))

    # A tier row (the k-th subnet of a tier in each AZ column of a VPC) starts at the same y in every column, as
    # layout.py draws it; heights may differ (an empty subnet is shorter).
    az_by_vpc, subnets_of = {}, {}
    for cid, v in vertices.items():
        if v["role"] == "az":
            az_by_vpc.setdefault(v["parent"], []).append(cid)
        elif v["role"] == "subnet":
            subnets_of.setdefault(v["parent"], []).append(v)
    for az_ids in az_by_vpc.values():
        rows = {}
        for az_id in az_ids:
            seen = {}
            for s in sorted(subnets_of.get(az_id, ()), key=lambda s: (s["y"], s["id"])):
                seen[s["tier"]] = seen.get(s["tier"], -1) + 1
                rows.setdefault((s["tier"], seen[s["tier"]]), []).append(s)
        for subs in rows.values():
            for other in subs[1:]:
                if abs(other["y"] - subs[0]["y"]) > 1:
                    report["unaligned_tiers"].append((subs[0]["id"], other["id"]))

    resource_boxes = {
        cid: (abs_pos[cid][0] + 2, abs_pos[cid][1] + 2, abs_pos[cid][0] + v["w"] - 2, abs_pos[cid][1] + v["h"] - 2)
        for cid, v in vertices.items() if v["role"] == "resource"
    }
    resource_grid = _Grid(resource_boxes)
    for e in edges:
        src, dst = e["source"], e["target"]
        if src not in vertices or dst not in vertices:
            continue
        _, kv = parse_style(e["style"])
        ports = [_fraction(kv.get(k)) for k in ("exitX", "exitY", "entryX", "entryY")]
        ox, oy = abs_pos.get(e["parent"], (0.0, 0.0))
        points = [(x + ox, y + oy) for x, y in e["points"]]
        (sx, sy), (dx, dy) = abs_pos[src], abs_pos[dst]
        sv, dv = vertices[src], vertices[dst]
        orthogonal = kv.get("edgeStyle") == "orthogonalEdgeStyle"
        if None in ports or not orthogonal:
            report["unrouted_edges"].append(e["id"])
            if orthogonal and not points:
                continue  # draw.io picks this route itself: nothing to measure
            # A straight edge (or one through its waypoints) as draw.io draws it, from centre to centre.
            path = [(sx + sv["w"] / 2, sy + sv["h"] / 2), *points, (dx + dv["w"] / 2, dy + dv["h"] / 2)]
        else:
            path = [(sx + ports[0] * sv["w"], sy + ports[1] * sv["h"]), *points, (dx + ports[2] * dv["w"], dy + ports[3] * dv["h"])]
            if any(abs(a[0] - b[0]) > 1 and abs(a[1] - b[1]) > 1 for a, b in zip(path, path[1:])):
                report["unrouted_edges"].append(e["id"])
        hit = set()
        for a, b in zip(path, path[1:]):
            seg = (min(a[0], b[0]), min(a[1], b[1]), max(a[0], b[0]) + 0.1, max(a[1], b[1]) + 0.1)
            for rid in resource_grid.near(seg):
                if rid not in (src, dst) and rid not in hit and _segment_hits(a, b, resource_boxes[rid]):
                    hit.add(rid)
                    report["edge_collisions"].append((e["id"], rid))
    return report


def _counts(report):
    return {key: len(report[key]) for key in ISSUES}


def inspect_drawio_xml(xml_text) -> dict:
    """Layout and routing diagnostics of every page of a `drawio.xml` string, and its extracted `DrawioSpec`.

    The issue lists and counts cover all pages; `pages` has each page's counts and whether it holds AWS cells at all
    (a cover or legend page does not, and import skips it)."""
    root = read_document(xml_text)
    roles, totals, page_reports = {}, {key: [] for key in ISSUES}, []
    nodes = containers = edge_count = 0
    for name, graph_root in pages(root):
        page = _inspect_page(graph_root)
        vertices = page["vertices"].values()
        for v in vertices:
            roles[v["role"]] = roles.get(v["role"], 0) + 1
        for key in ISSUES:
            totals[key] += page[key]
        page_nodes = sum(1 for v in vertices if v["role"] == "resource")
        nodes, edge_count = nodes + page_nodes, edge_count + len(page["edges"])
        containers += sum(1 for v in vertices if v["role"] in CONTAINER_ROLES)
        page_reports.append({"name": name, "aws": any(v["role"] in ("vpc", "subnet", "resource", "summary") for v in vertices),
                             "node_count": page_nodes, **_counts(page), "clean": not any(page[key] for key in ISSUES)})
    extracted = spec_from_xml(xml_text)
    return {
        "wrapper": extracted.cell_wrapper,
        "node_count": nodes,
        "container_count": containers,
        "edge_count": edge_count,
        "roles": roles,
        **totals,
        "clean": not any(totals.values()),
        "pages": page_reports,
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
        if v["parent"] != "1" or v["role"] in ("title", "caption", "cloud", "account", "asg", "ecs_frame", *FREE_ROLES):
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


def _align_page_in_place(graph_root, *, reparent=True, route_edges=True, grid_size=10):
    """Align containers, resource grids, cross-AZ tiers, and orthogonal routes in-place on `graph_root` (geometry only)."""
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
        sub_conts = [k for k in kids if k["role"] in CONTAINER_ROLES or k["role"] == "group"]
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
            kids = [k for k in children_of.get(cont["id"], []) if k["role"] not in ("badge", "asg", "ecs_frame", "account", *FREE_ROLES)]
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
        [v for v in vertices.values() if v["parent"] == "1" and v["role"] not in OVERLAY_ROLES],
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

    grid = _Grid(obstacles)

    def hits_obstacle(p1, p2, ignore_ids):
        seg = (min(p1[0], p2[0]), min(p1[1], p2[1]), max(p1[0], p2[0]) + 0.1, max(p1[1], p2[1]) + 0.1)
        return any(oid not in ignore_ids and _boxes_overlap(seg, obstacles[oid]) for oid in grid.near(seg))

    def path_clear(pts, ignore_ids):
        return all(not hits_obstacle(a, b, ignore_ids) for a, b in zip(pts, pts[1:]))

    # Horizontal mid lines already taken, bucketed by 6 px: a candidate within 6 px of one is skipped.
    def line_taken(y):
        bucket = math.floor(y / 6.0)
        return any(abs(y - u) < 6.0 for b in (bucket - 1, bucket, bucket + 1) for u in used_buckets.get(b, ()))

    used_buckets = {}
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
                    if line_taken(cand_y):
                        continue
                    cand_pts = [p_start, (p_start[0], cand_y), (p_end[0], cand_y), p_end]
                    if path_clear(cand_pts, ignore):
                        chosen_y = cand_y
                        break
                else:
                    continue
                break
            used_buckets.setdefault(math.floor(chosen_y / 6.0), set()).add(chosen_y)
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

    # Wrapper tags (`object` / `UserObject` / `mxCell`). A wrapper carrying custom attributes (tooltip, link, placeholders,
    # resourceId...) is never downgraded to a bare mxCell: draw.io would drop them. It is kept and counted instead.
    kept = 0
    for idx, el in enumerate(list(graph_root)):
        inner = el if el.tag == "mxCell" else el.find("mxCell")
        if inner is None or (el.tag == "mxCell" and el.get("id") in ("0", "1")) or el.tag == cfg.cell_wrapper:
            continue
        cid = el.get("id") or inner.get("id") or ""
        label = el.get("label") or el.get("value") or inner.get("value") or ""
        if cfg.cell_wrapper == "mxCell":
            if set(el.attrib) - {"id", "label"}:
                kept += 1
                continue
            inner.set("id", cid)
            inner.set("value", label)
            graph_root.remove(el)
            graph_root.insert(idx, inner)
        elif el.tag == "mxCell":
            custom = {k: inner.attrib.pop(k) for k in list(inner.attrib) if k not in MXCELL_ATTRIBUTES}
            inner.attrib.pop("id", None)
            inner.attrib.pop("value", None)
            wrapper = Element(cfg.cell_wrapper, {"id": cid, "label": label, **custom})
            wrapper.append(inner)
            graph_root.remove(el)
            graph_root.insert(idx, wrapper)
        else:
            el.tag = cfg.cell_wrapper
    return kept


GUARDED = ("overlaps", "out_of_bounds", "edge_collisions")


def align_report(xml_text, *, mode="preserve", spec=None, reparent=True, route_edges=True, normalize_styles=False, grid_size=10):
    """`align_drawio_xml` with what happened to each page: {"xml", "pages": [{name, status, before, after, kept_wrappers}]}.

    Status per page: "clean" (no issues: geometry untouched), "aligned", or "reverted" when the aligned page had more
    overlaps, out-of-bounds cells or edge-icon collisions than the input (the input geometry is kept). A spec is applied
    to every page either way; kept_wrappers counts wrappers not downgraded to <mxCell> because they carry attributes."""
    if mode == "rebuild":
        read_document(xml_text)
        snapshot = import_drawio_xml(xml_text)
        target_spec = resolve_spec(spec) if spec is not None else spec_from_xml(xml_text)
        vpcs = [n["resource_id"] for n in snapshot["graph"]["nodes"] if n["kind"] == "vpc"]
        view = "detail" if len(vpcs) == 1 else "overview"
        vpc_id = vpcs[0] if len(vpcs) == 1 else None
        return {"xml": generate_document(snapshot, vpc_id=vpc_id, view=view, spec=target_spec), "pages": [{"status": "rebuilt"}]}
    if mode != "preserve":
        raise InputError(f"알 수 없는 정렬 모드입니다: {mode} ('preserve' 또는 'rebuild')")

    root = read_document(xml_text)
    cfg = resolve_spec(spec) if spec is not None or normalize_styles else None
    if cfg is not None and root.tag == "mxfile":
        root.set("host", cfg.host)
    models = [(d.get("name") or d.get("id") or f"page-{i + 1}", d.find("mxGraphModel")) for i, d in enumerate(root.findall("diagram"))
              if d.find("mxGraphModel/root") is not None] if root.tag == "mxfile" else [("", root)]
    report = []
    for name, model in models:
        graph_root = model.find("root")
        before = _counts(_inspect_page(graph_root))
        entry = {"name": name, "status": "clean", "before": before, "after": before}
        if any(before.values()):
            original = copy.deepcopy(graph_root)
            _align_page_in_place(graph_root, reparent=reparent, route_edges=route_edges,
                                 grid_size=cfg.grid_size if cfg is not None else grid_size)
            after = _counts(_inspect_page(graph_root))
            if any(after[key] > before[key] for key in GUARDED):
                graph_root[:] = list(original)
                entry["status"] = "reverted"
            else:
                entry.update(status="aligned", after=after)
        if cfg is not None:
            model.set("background", cfg.background)
            model.set("gridSize", str(cfg.grid_size))
            entry["kept_wrappers"] = _apply_spec_to_page(graph_root, *_parse_page_model(graph_root), cfg)
        report.append(entry)
    return {"xml": tostring(root, encoding="unicode"), "pages": report}


def align_drawio_xml(xml_text, *, mode="preserve", spec=None, reparent=True, route_edges=True, normalize_styles=False,
                     grid_size=10) -> str:
    """Auto-align, resize containers, and orthogonally route an existing `drawio.xml` document; every page.

    - `mode="preserve"` (default): keeps cell IDs, labels and custom attributes while fixing containment, grid alignment,
      cross-AZ subnet tier heights, container bounds and orthogonal edge routes. A page without issues is left as it is,
      and a page the aligner would make worse is returned unchanged (`align_report` says which).
    - `mode="rebuild"`: imports the XML into a Diagram Bot `Snapshot` and renders it through `generate_document`.
    """
    return align_report(xml_text, mode=mode, spec=spec, reparent=reparent, route_edges=route_edges,
                        normalize_styles=normalize_styles, grid_size=grid_size)["xml"]


# Availability Zone letters per region, in order; a label's AZ is used only when it is one of these.
REGION_AZS = {"us-east-1": "abcdef", "us-east-2": "abc", "us-west-1": "bc", "us-west-2": "abcd", "ca-central-1": "abd",
              "eu-west-1": "abc", "eu-west-2": "abc", "eu-central-1": "abc", "ap-northeast-1": "acd", "ap-northeast-2": "abcd",
              "ap-northeast-3": "abc", "ap-southeast-1": "abc", "ap-southeast-2": "abc", "ap-south-1": "abc", "sa-east-1": "abc"}
TIER_NAMES = {0: "public", 1: "web", 2: "app", 3: "data"}
INSTANCE_TYPES = {"p3": "p3.2xlarge", "p4d": "p4d.24xlarge", "p4de": "p4de.24xlarge", "g4dn": "g4dn.xlarge", "g4ad": "g4ad.xlarge",
                  "trainium": "trn1.32xlarge", "inferentia": "inf2.xlarge", "dl1": "dl1.24xlarge"}
IMPORTED_KINDS = {"internet_gateway", "nat_gateway", "application", "network", "gateway", "load_balancer", "target_group", "ec2",
                  *INSTANCE_TYPES, "db_instance", "db_standby", "documentdb", "neptune", "aurora", "redis", "valkey", "memcached",
                  "eks_cluster", "ecs_service", "ecs_task", "lambda_function", "vpc_endpoint", "bedrock", "sagemaker", "efs_standard",
                  "efs_one_zone", "fsx_lustre", "fsx_file_system", "fsx_ontap", "fsx_openzfs", "fsx_windows", "sagemaker_endpoint",
                  "cloudfront_distribution"}
CIDR = re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}/\d{1,2}\b")


def _network(text):
    """The first IPv4 network written in a label (0.0.0.0/0 route captions excluded), or None."""
    for match in CIDR.finditer(text or ""):
        try:
            network = ipaddress.ip_network(match.group(0), strict=False)
        except ValueError:
            continue
        if network.prefixlen >= 8:
            return network
    return None


def _name(label, fallback):
    """A resource name from a label's first line, without its '(cidr)' part, 'VPC ·' captions or CIDR parts."""
    first = (label or "").splitlines()[0] if label else ""
    parts = [p.strip() for p in re.sub(r"\s*\([^)]*\)", "", first).split("·")]
    parts = [p for p in parts if p and p.lower() != "vpc" and not CIDR.fullmatch(p)]
    return (parts[0] if parts else "")[:60] or fallback


def _az_letter(label, region, letters):
    """The AZ letter a label names, if it is a real AZ of `region`: 'ap-northeast-2a', 'AZ a', 'Zone B', 'AZ 2'."""
    text = (label or "").lower()
    match = AZ_CODE.search(text)
    if match:
        return match.group(2) if match.group(1) == region and match.group(2) in letters else None
    match = re.search(r"(?:\baz|\bzone|가용\s*영역|availability zone)\s*[-_:]?\s*([a-f])\b", text)
    if match:
        return match.group(1) if match.group(1) in letters else None
    match = re.search(r"\baz\s*[-_]?\s*([1-9])\b", text)
    return letters[int(match.group(1)) - 1] if match and int(match.group(1)) <= len(letters) else None


class _Cidrs:
    """Subnet CIDRs inside one VPC: labels' CIDRs when they fit, then free blocks of the VPC for the rest."""

    def __init__(self, network):
        self.network, self.used = network, []

    def claim(self, network):
        if network and network.version == self.network.version and network.subnet_of(self.network) \
                and not any(network.overlaps(u) for u in self.used):
            self.used.append(network)
            return str(network)
        return None

    def allocate(self):
        prefix = min(28, max(24, self.network.prefixlen + 4))
        for candidate in self.network.subnets(new_prefix=max(prefix, self.network.prefixlen)):
            if self.claim(candidate):
                return str(candidate)
        raise InputError(f"VPC {self.network}에 서브넷을 더 둘 주소가 없습니다.")


def _own_id(v, prefix):
    """The AWS id in a Diagram Bot cell's resourceId when it has the right form (vpc-, subnet-, i-...), else None."""
    rid = (v["wrapper"].get("resourceId") or "").rsplit("/", 1)[-1]
    return rid if re.fullmatch(prefix + r"-[0-9a-z][0-9a-z-]{2,60}", rid) else None


def import_drawio_xml(xml_text, *, title=None, region="ap-northeast-2", account="000000000000") -> dict:
    """Convert an existing AWS `drawio.xml` diagram, every page, into a Diagram Bot `Snapshot` (`raw` + `graph`).

    Pages without AWS cells (a cover, a legend) are skipped. A resource drawn on several pages (an overview card and its
    detail page) is imported once: Diagram Bot's own cells match by resourceId, others by kind, name and VPC.
    `metadata.import` records `cells` (draw.io cell id -> snapshot resource id), `pages`, `unmapped` cells (shapes the
    importer has no resource for, reported instead of guessed) and what was `synthesized` (a default VPC, subnets, an IGW).
    Labels are inference, not API evidence: the snapshot is marked `metadata.imported`."""
    root = read_document(xml_text)
    models = []
    for name, graph_root in pages(root):
        vertices, edges = _parse_page_model(graph_root)
        _reparent_visual_children(vertices)
        for v in vertices.values():  # the counts and glance icons inside an overview card are not resources
            if v["role"] != "summary" and _ancestor(vertices, v["parent"], "summary"):
                v["role"] = "glance"
        models.append({"name": name, "vertices": vertices, "edges": edges,
                       "aws": any(v["role"] in ("vpc", "subnet", "resource", "summary") for v in vertices.values())})
    used = [m for m in models if m["aws"]]
    cells = [(pi, v) for pi, m in enumerate(used) for v in sorted(m["vertices"].values(), key=lambda c: (c["y"], c["x"], c["id"]))]
    report = {"cells": {}, "pages": [{"name": m["name"], "imported": m["aws"]} for m in models], "unmapped": [], "synthesized": []}

    def own_parts(v):
        parts = (v["wrapper"].get("resourceId") or "").split("/")
        return parts if len(parts) >= 4 else None

    detected = next((p[1] for _, v in cells if (p := own_parts(v)) and REGION_CODE.fullmatch(p[1])), None)
    for _, v in cells:
        match = None if detected else (AZ_CODE.search(v["label"].lower()) or REGION_CODE.search(v["label"].lower()))
        detected = detected or (match and (match.group(1) if match.re is AZ_CODE else match.group(0)))
    region = detected or region
    for m in used:
        for v in m["vertices"].values():
            if v["role"] == "title" and not title:
                title = v["label"].split("·")[0].replace("SAMPLE", "").replace("DESIGN", "").strip(" /")
    sample = Sample("imported", title or "draw.io 가져온 아키텍처", ["draw.io XML에서 가져온 구성"], region=region, account=account)
    letters = REGION_AZS.get(region, "abc")
    imported = {}  # (page index, cell id) -> resource id

    def record(pi, v, resource_id):
        imported[(pi, v["id"])] = resource_id
        key = v["id"] if report["cells"].get(v["id"], resource_id) == resource_id else f"{used[pi]['name']}/{v['id']}"
        report["cells"][key] = resource_id

    def owner(pi, v, role):
        cell = _ancestor(used[pi]["vertices"], v["id"], role)
        return imported.get((pi, cell["id"])) if cell else None

    # 1. VPCs: containers, and overview cards whose VPC has no detail page.
    seen, nets = {}, {}
    for pi, v in cells:
        own = own_parts(v)
        if v["role"] != "vpc" and not (v["role"] == "summary" and own and own[2] == "vpc"):
            continue
        network = _network(v["label"])
        name = _name(v["label"], f"vpc-{len(nets) + 1}")
        key = v["wrapper"].get("resourceId") or ("vpc", str(network) if network else name)
        if key not in seen:
            network = network or ipaddress.ip_network(f"10.{len(nets) % 256}.0.0/16")
            vid = _own_id(v, "vpc") or "vpc-" + (re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:20] or "imported")
            vid = vid if vid not in nets else f"{vid}-{len(nets) + 1}"
            seen[key] = sample.vpc(vid, str(network), name)
            nets[vid] = _Cidrs(network)
        record(pi, v, seen[key])
    if not nets:
        nets[sample.vpc("vpc-main", "10.0.0.0/16", "main-vpc")] = _Cidrs(ipaddress.ip_network("10.0.0.0/16"))
        report["synthesized"].append("vpc-main")
    default_vpc = next(iter(nets))

    # 2. AZ columns: the label's AZ when it is real in this region, else the region's AZs in column order.
    az_of = {}
    for vid in nets:
        columns = [(pi, v) for pi, v in cells if v["role"] == "az" and (owner(pi, v, "vpc") or default_vpc) == vid]
        named = {(pi, v["id"]): _az_letter(v["label"], region, letters) for pi, v in columns}
        spare = [letter for letter in letters if letter not in named.values()] or list(letters)
        for i, (pi, v) in enumerate(sorted(columns, key=lambda c: (c[0], c[1]["x"], c[1]["id"]))):
            az_of[(pi, v["id"])] = named[(pi, v["id"])] or spare[i % len(spare)]

    # 3. Subnets: a label's CIDR when it lies inside its VPC (and is free), else the next free block of that VPC.
    subnets, tier_subnets, pending = {}, {}, []
    for pi, v in cells:
        if v["role"] != "subnet":
            continue
        vid = owner(pi, v, "vpc") or default_vpc
        network = _network(v["label"])
        name = _name(v["label"], f"subnet-{len(pending) + 1}")
        key = v["wrapper"].get("resourceId") or ("subnet", vid, str(network) if network else name)
        if key in subnets:
            record(pi, v, subnets[key])
            continue
        az_cell = _ancestor(used[pi]["vertices"], v["id"], "az")
        letter = az_of.get((pi, az_cell["id"])) if az_cell else letters[len(pending) % min(2, len(letters))]
        subnets[key] = None
        pending.append((pi, v, vid, key, nets[vid].claim(network), letter, name))
    for i, (pi, v, vid, key, cidr, letter, name) in enumerate(pending):
        tier = TIER_NAMES[v["tier"] if v["tier"] is not None else 2]
        sid = _own_id(v, "subnet") or f"subnet-{i + 1}-{letter}"
        subnets[key] = sample.subnet(sid, vid, letter, cidr or nets[vid].allocate(), name, tier)
        tier_subnets.setdefault((vid, tier), []).append(sid)
        record(pi, v, sid)
    resources = [(pi, v) for pi, v in cells if v["role"] == "resource"]
    if not pending and resources:
        for letter in letters[:2]:
            for tier in ("public", "app", "data"):
                sid = sample.subnet(f"subnet-{tier}-{letter}", default_vpc, letter, nets[default_vpc].allocate(), f"{tier}-{letter}", tier)
                tier_subnets.setdefault((default_vpc, tier), []).append(sid)
                report["synthesized"].append(sid)

    def subnet_for(pi, v, vid, preferred="app"):
        return owner(pi, v, "subnet") or next((tier_subnets[(vid, t)][0] for t in (preferred, "app", "web", "public", "data")
                                              if tier_subnets.get((vid, t))), None) or sample.raw["subnets"][0]["SubnetId"]

    def tier_of(vid, tier, pi, v):
        return (tier_subnets.get((vid, tier)) or [subnet_for(pi, v, vid, tier)])[:2]

    def az_letter(subnet_id):
        return sample.az_of(subnet_id)[-1]

    # 4. Resources, once each.
    done, lbs, tgs, ec2s = {}, {}, {}, {}
    for idx, (pi, v) in enumerate(resources):
        kind, vid = v["kind"], owner(pi, v, "vpc") or default_vpc
        name = _name(v["label"], f"{kind or 'resource'}-{idx + 1}")[:48]
        slug_name = re.sub(r"[^a-zA-Z0-9-]+", "-", name).strip("-") or f"{kind}-{idx + 1}"
        key = v["wrapper"].get("resourceId") or (kind, name, vid)
        if kind == "target_group":  # created after every page is read, wired by the edges drawn to it
            tgs.setdefault(key, (slug_name, vid, []))[2].append((pi, v["id"]))
            continue
        if key in done:
            if done[key]:
                record(pi, v, done[key])
            continue
        if kind not in IMPORTED_KINDS:
            done[key] = None
            report["unmapped"].append({"page": used[pi]["name"], "id": v["id"], "label": name, "kind": kind})
            continue
        rid = None
        if kind == "internet_gateway":
            rid = sample.internet_gateway(_own_id(v, "igw") or f"igw-{idx + 1}", vid, name)
        elif kind == "nat_gateway":
            rid = sample.nat_gateway(_own_id(v, "nat") or f"nat-{idx + 1}", vid, subnet_for(pi, v, vid, "public"), name)
        elif kind in ("application", "network", "gateway", "load_balancer"):
            subnet = _ancestor(used[pi]["vertices"], v["id"], "subnet")
            internal = (subnet and subnet.get("tier") not in (0, None)) or "internal" in v["label"].lower() or "내부" in v["label"]
            rid = sample.load_balancer(slug_name, vid, tier_of(vid, "app" if internal else "public", pi, v),
                                       kind=kind if kind != "load_balancer" else "application",
                                       scheme="internal" if internal else "internet-facing")
            lbs[(pi, v["id"])] = rid
        elif kind == "ec2" or kind in INSTANCE_TYPES:
            rid = sample.instance(_own_id(v, "i") or f"i-imp{idx + 1:04d}", subnet_for(pi, v, vid), name,
                                  kind=INSTANCE_TYPES.get(kind, "t3.medium"))
            ec2s[(pi, v["id"])] = rid
        elif kind in ("db_instance", "db_standby", "documentdb", "neptune"):
            subs = tier_of(vid, "data", pi, v)
            standby = az_letter(subs[1]) if len(subs) > 1 and az_letter(subs[1]) != az_letter(subs[0]) else None
            rid = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or f"db-{idx + 1}"
            sample.database(rid, vid, subs, primary=az_letter(subs[0]), standby=standby,
                            engine={"documentdb": "docdb", "neptune": "neptune"}.get(kind, "postgres"))
        elif kind == "aurora":
            subs = tier_of(vid, "data", pi, v)
            rid = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or f"aurora-{idx + 1}"
            sample.aurora(rid, vid, subs, writer=az_letter(subs[0]), readers=[az_letter(s) for s in subs[1:]])
        elif kind in ("redis", "valkey", "memcached"):
            subs = tier_of(vid, "data", pi, v)
            rid = re.sub(r"[^a-z0-9-]+", "-", name.lower()).strip("-") or f"cache-{idx + 1}"
            sample.redis(rid, vid, subs, primary=az_letter(subs[0]), replicas=[az_letter(s) for s in subs[1:]])
        elif kind == "eks_cluster":
            rid = slug_name
            sample.eks(rid, vid, tier_of(vid, "app", pi, v), nodegroups=[])
        elif kind in ("ecs_service", "ecs_task"):
            rid = slug_name
            subs = tier_of(vid, "app", pi, v)
            sample.ecs_service("imported-cluster", rid, vid, [(s, sample.address(s, 50 + i)) for i, s in enumerate(subs)])
        elif kind == "lambda_function":
            rid = re.sub(r"[^a-zA-Z0-9-_]+", "-", name).strip("-") or f"fn-{idx + 1}"
            sample.function(rid, vid, tier_of(vid, "app", pi, v))
        elif kind in ("vpc_endpoint", "bedrock", "sagemaker"):
            rid = _own_id(v, "vpce") or f"vpce-imp{idx + 1:04d}"
            service = {"bedrock": "bedrock-runtime", "sagemaker": "sagemaker.runtime"}.get(kind, "ssm")
            sample.interface_endpoint(rid, vid, service, tier_of(vid, "app", pi, v))
        elif kind in ("efs_standard", "efs_one_zone"):
            subs = tier_of(vid, "data", pi, v)
            rid = _own_id(v, "fs") or f"fs-imp{idx + 1:04d}"
            sample.efs(rid, name, vid, subs, one_zone=az_letter(subs[0]) if kind == "efs_one_zone" else None)
        elif kind.startswith("fsx_"):
            rid = f"fs-fsx{idx + 1:04d}"
            sample.fsx_lustre(rid, name, vid, subnet_for(pi, v, vid, "data"))
        elif kind == "sagemaker_endpoint":
            rid = slug_name
            sample.sagemaker_endpoint(rid, "ml.g5.2xlarge", 2, [subnet_for(pi, v, vid)])
        elif kind == "cloudfront_distribution":
            rid = f"EIMP{idx + 1:08d}"
            sample.cloudfront(rid, name, list(lbs.values())[:1] or ["imported-origin"])
        done[key] = rid
        if rid:
            record(pi, v, rid)

    # Target groups: wired to the load balancers and instances their edges reach (on any page they are drawn).
    neighbours = {}
    for pi, m in enumerate(used):
        for e in m["edges"]:
            neighbours.setdefault((pi, e["source"]), []).append((pi, e["target"]))
            neighbours.setdefault((pi, e["target"]), []).append((pi, e["source"]))
    for name, vid, drawn in tgs.values():
        near = [n for cell in drawn for n in neighbours.get(cell, ())]
        arn = sample.target_group(name, vid, "HTTP", 80, list(dict.fromkeys(ec2s[n] for n in near if n in ec2s)),
                                  lbs=list(dict.fromkeys(lbs[n] for n in near if n in lbs)))
        for pi, cid in drawn:
            record(pi, used[pi]["vertices"][cid], arn)
    if lbs and ec2s and not tgs:
        sample.target_group("tg-imported", default_vpc, "HTTP", 80, list(dict.fromkeys(ec2s.values())), lbs=list(lbs.values())[:1])
        report["synthesized"].append("tg-imported")

    # Route tables from what is drawn: public subnets to an internet gateway (added when none is drawn), private
    # subnets to a drawn NAT gateway (none is invented), the rest on the main table (isolated).
    for vid in nets:
        vpc_subnets = [s for s in sample.raw["subnets"] if s["VpcId"] == vid]
        if not vpc_subnets:
            continue
        public = [s["SubnetId"] for s in vpc_subnets if s["MapPublicIpOnLaunch"]]
        private = [s["SubnetId"] for s in vpc_subnets if not s["MapPublicIpOnLaunch"]
                   and not any(t["Key"] == "Tier" and t["Value"] == "data" for t in s["Tags"])]
        if public:
            igw = next((g["InternetGatewayId"] for g in sample.raw["internet_gateways"]
                        if any(a.get("VpcId") == vid for a in g["Attachments"])), None)
            if not igw:
                igw = sample.internet_gateway(f"igw-{vid}", vid, f"{vid}-igw")
                report["synthesized"].append(igw)
            sample.route_table(f"rtb-{vid}-public", vid, f"{vid}-public", subnets=public, gateway=igw)
        nat = next((n["NatGatewayId"] for n in sample.raw["nat_gateways"] if n["VpcId"] == vid), None)
        if private and nat:
            sample.route_table(f"rtb-{vid}-private", vid, f"{vid}-private", subnets=private, nat=nat)
        sample.route_table(f"rtb-{vid}-main", vid, f"{vid}-isolated", main=True)

    snap = sample.snapshot()
    snap["metadata"]["imported"] = True
    snap["metadata"]["import"] = report
    snap["graph"] = build_graph(snap)
    return snap


def _ancestor(vertices, cid, role):
    """The nearest cell of `role` among `cid` and its ancestors (`cid` itself included)."""
    cur, seen = vertices.get(cid), set()
    while cur and cur["id"] not in seen:
        if cur["role"] == role:
            return cur
        seen.add(cur["id"])
        cur = vertices.get(cur["parent"])
    return None


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

