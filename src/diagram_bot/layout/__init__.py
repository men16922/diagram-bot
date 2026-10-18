"""Deterministic layered layout for one view. Produces geometry only; drawio.py serializes it.

Bands inside a VPC, top to bottom: load balancers, target groups, an edge channel, AZ columns
holding subnets and their instances, then VPC resources whose subnet was not confirmed.
Resources outside every drawn VPC go to a separate reference container. Edges from target
groups run through the channel and the gutters between AZ columns, so they never cross a
subnet or an icon. Identical input and options always yield identical coordinates.

Modules: base (sizes, cell ids, subnet tiers), text (labels and icon variants), overview (VPC cards,
network hubs), detail (VPC bands, AZ columns, frames, reference and data center boxes), routing
(orthogonal routes and edge cells), edge_column (CloudFront at the edge). Every name below stays
importable from diagram_bot.layout.
"""
from ..views import containment
from .base import (AZ_HEADER, BOTTOM_LABEL, ENGINE, ICON, LANE_TOP, LINE, MAX_LINES, ROW, SIDE_LABEL, SIDE_LINES, SIDE_SLOT, SLOT, SUBNET_HEADER, VERSION,
                   VPC_HEADER, cell_id, is_public, outside_vpc, tier_rank)
from .detail import EDGE_KINDS, KIND_ORDER, ROUTES, arrange, detail
from .edge_column import EDGE_COLUMN, EDGE_SLOT, align_edge, edge_column, edge_offset, edge_path, waf_badge
from .overview import (GLANCE, GLANCE_ICON, GLANCE_KINDS, GLANCE_LEFT, SLOT_W, TIER_NAMES, draw_glance, glance_height, glance_key, glance_label,
                       glance_lines, network, overview, overview_edge, vpc_glance)
from .routing import OUTSIDE_BOXES, TG_STROKES, edge_between, edge_entry, route, target_on_right, tg_stroke
from .text import (ACCELERATED_ICON, ENGINES, GPU, LABELERS, ML_ENDPOINTS, accelerated_icon, cloud_label, fit_label, fit_name, group_title, icon_label,
                   icon_variant, instance_family, label_lines, resource_label, text_width)

__all__ = ["ACCELERATED_ICON", "AZ_HEADER", "BOTTOM_LABEL", "EDGE_COLUMN", "EDGE_KINDS", "EDGE_SLOT", "ENGINE", "ENGINES", "GLANCE", "GLANCE_ICON",
           "GLANCE_KINDS", "GLANCE_LEFT", "GPU", "ICON", "KIND_ORDER", "LABELERS", "LANE_TOP", "LINE", "MAX_LINES", "ML_ENDPOINTS", "OUTSIDE_BOXES", "ROUTES",
           "ROW", "SIDE_LABEL", "SIDE_LINES", "SIDE_SLOT", "SLOT", "SLOT_W", "SUBNET_HEADER", "TG_STROKES", "TIER_NAMES", "VERSION", "VPC_HEADER",
           "Canvas", "accelerated_icon", "align_edge", "arrange", "cell_id", "cloud_label", "detail", "draw_glance", "edge_between", "edge_column",
           "edge_entry", "edge_offset", "edge_path", "fit_label", "fit_name", "glance_height", "glance_key", "glance_label", "glance_lines",
           "group_title", "icon_label", "icon_variant", "instance_family", "is_public", "label_lines", "layout_view", "network", "outside_vpc",
           "overview", "overview_edge", "resource_label", "route", "target_on_right", "text_width", "tg_stroke", "tier_rank", "vpc_glance",
           "waf_badge"]


class Canvas:
    def __init__(self):
        self.shapes, self.edges, self.abs, self.slots = [], [], {"1": (0, 0)}, {}

    def add(self, key, parent, x, y, w, h, role, label="", **extra):
        px, py = self.abs[parent]
        self.abs[key] = (px + x, py + y)
        shape = {"id": key, "parent": parent, "x": x, "y": y, "w": w, "h": h, "role": role, "label": label, **extra}
        self.shapes.append(shape)
        return shape

    def label_box(self, shape):
        x, y = self.abs[shape["id"]]
        width = shape.get("label_width") or (BOTTOM_LABEL if shape.get("label_side") == "bottom" else SIDE_LABEL)
        lines = label_lines(shape["label"], width)
        if shape.get("label_side") == "bottom":
            return (x + shape["w"] / 2 - width / 2, y + shape["h"] + 8, width, lines * LINE)
        if shape.get("label_side") == "right":
            height = lines * LINE
            return (x + shape["w"] + 8, y + shape["h"] / 2 - height / 2, SIDE_LABEL, height)
        return None


def layout_view(snapshot, graph, selection):
    canvas = Canvas()
    nodes, vpc_of, subnet_of = containment(graph)
    meta, counts, view = snapshot["metadata"], selection["counts"], selection["view"]
    heading = (f"DESIGN · {meta.get('title', '')} / {meta['region']}" if meta.get("design")
               else f"{'SAMPLE · ' if meta.get('demo') else ''}AWS / {meta['account_id']} / {meta['region']}")
    titles = {"overview": "전체 구성", "detail": "상세 구성", "focus": "선택 리소스"}
    subject = ""
    if view["kind"] == "detail":
        subject = " · " + nodes[next(k for k in selection["nodes"] if nodes[k]["kind"] == "vpc" and nodes[k]["resource_id"] == view["vpc"])]["name"]
    elif view["kind"] == "focus":
        subject = " · " + nodes[selection["focus"]]["name"]
    routes = selection.get("routes")
    if routes:
        subject += " · 경로 " + routes["name"]
    canvas.add("heading", "1", 24, 12, 1200, 30, "title", fit_label(heading + " · " + titles[view["kind"]] + subject, 1180, 1, 19))
    incomplete = [f"{c['operation']}({c.get('error_code', c['status'])})" for c in snapshot["coverage"] if c["status"] != "complete"]
    status = "부분 조회: " + ", ".join(incomplete) if incomplete else "조회 완료"
    # A route filter says what it left out: hidden resources are part of the hidden count, omitted relations are counted.
    narrowed = (f"\n경로 필터: {'대상 그룹' if routes['kind'] == 'target_group' else '로드 밸런서'} {routes['name']}"
                f" · 대상 그룹 {routes['target_groups']}개의 경로만 표시 · 다른 로드 밸런서·대상 그룹과 그 대상 {routes['hidden']}개 숨김"
                f" · 관계 {routes['hidden_relations']}개 생략 · 흐린 리소스는 이 경로 밖") if routes else ""
    scope = (f"{status} · {meta['finished_at']}\n표시 {counts['visible']}개 / 수집 {counts['collected']}개 · 참조 {counts['references']}개"
             f" · 숨김 {counts['hidden']}개 · 설정 관계만 표시, 실제 통신 미확인 · 사용자 주석은 수집 데이터와 별도" + narrowed
             + ("\n서브넷 구분: 라우팅 테이블의 0.0.0.0/0 경로 (IGW = 퍼블릭, NAT·TGW·VGW = 프라이빗, 없음 = 격리)"
                if any(part["raw"].get("route_tables") for part in snapshot.get("parts") or [snapshot])
                else "\n퍼블릭 서브넷 표시 기준: MapPublicIpOnLaunch 또는 이름·Tier 태그 (라우팅 테이블 미조회)"))
    canvas.add("scope", "1", 24, 48, 960, 42 + (15 if routes else 0), "caption", scope, layout=f"{ENGINE}/{VERSION}",
               view=view["kind"], vpc=view["vpc"] or "", focus=view["resource"] or "",
               references="1" if view["references"] else "0", routes=view.get("routes") or "")
    if view["kind"] == "overview":
        overview(canvas, snapshot, graph, nodes, vpc_of, subnet_of)
    else:
        detail(canvas, snapshot, graph, selection, nodes, vpc_of, subnet_of)
    if routes:
        # Resources and groups off the selected path stay in place for context, dimmed.
        path = set(routes["path"])
        for shape in canvas.shapes:
            if shape["role"] in ("resource", "asg") and shape.get("resource") not in path:
                shape["muted"] = True
    width = max(s["x"] + s["w"] for s in canvas.shapes if s["parent"] == "1")
    height = max(s["y"] + s["h"] for s in canvas.shapes if s["parent"] == "1")
    labels = {s["id"]: canvas.label_box(s) for s in canvas.shapes if s.get("label_side")}
    return {"engine": ENGINE, "version": VERSION, "view": view, "counts": counts, "width": width, "height": height,
            "shapes": canvas.shapes, "edges": canvas.edges, "absolute": dict(canvas.abs), "labels": labels}


