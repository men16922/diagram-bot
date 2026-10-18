"""Deterministic layered layout for one view. Produces geometry only; drawio.py serializes it.

Bands inside a VPC, top to bottom: load balancers, target groups, an edge channel, AZ columns
holding subnets and their instances, then VPC resources whose subnet was not confirmed.
Resources outside every drawn VPC go to a separate reference container. Edges from target
groups run through the channel and the gutters between AZ columns, so they never cross a
subnet or an icon. Identical input and options always yield identical coordinates.
"""
from ..views import AUXILIARY, EDGE, GROUP_TARGETS, GROUPS, REGIONAL, REMOTE, containment
from .base import (AZ_HEADER, BOTTOM_LABEL, ENGINE, ICON, LANE_TOP, LINE, MAX_LINES, ROW, SIDE_LABEL, SIDE_LINES, SIDE_SLOT, SLOT, SUBNET_HEADER, VERSION,
                   VPC_HEADER, cell_id, is_public, outside_vpc, tier_rank)
from .text import (ACCELERATED_ICON, ENGINES, GPU, LABELERS, ML_ENDPOINTS, accelerated_icon, cloud_label, fit_label, fit_name, group_title, icon_label,
                   icon_variant, instance_family, label_lines, resource_label, text_width)
from .edge_column import EDGE_COLUMN, EDGE_SLOT, align_edge, edge_column, edge_offset, edge_path, waf_badge
from .routing import OUTSIDE_BOXES, TG_STROKES, edge_between, edge_entry, route, target_on_right, tg_stroke
from .overview import (GLANCE, GLANCE_ICON, GLANCE_KINDS, GLANCE_LEFT, SLOT_W, TIER_NAMES, draw_glance, glance_height, glance_key, glance_label,
                       glance_lines, network, overview, overview_edge, vpc_glance)

__all__ = ["ACCELERATED_ICON", "AZ_HEADER", "BOTTOM_LABEL", "EDGE_COLUMN", "EDGE_KINDS", "EDGE_SLOT", "ENGINE", "ENGINES", "GLANCE", "GLANCE_ICON",
           "GLANCE_KINDS", "GLANCE_LEFT", "GPU", "ICON", "KIND_ORDER", "LABELERS", "LANE_TOP", "LINE", "MAX_LINES", "ML_ENDPOINTS", "OUTSIDE_BOXES", "ROUTES",
           "ROW", "SIDE_LABEL", "SIDE_LINES", "SIDE_SLOT", "SLOT", "SLOT_W", "SUBNET_HEADER", "TG_STROKES", "TIER_NAMES", "VERSION", "VPC_HEADER",
           "Canvas", "accelerated_icon", "align_edge", "arrange", "cell_id", "cloud_label", "detail", "draw_glance", "edge_between", "edge_column",
           "edge_entry", "edge_offset", "edge_path", "fit_label", "fit_name", "glance_height", "glance_key", "glance_label", "glance_lines",
           "group_title", "icon_label", "icon_variant", "instance_family", "is_public", "label_lines", "layout_view", "network", "outside_vpc",
           "overview", "overview_edge", "resource_label", "route", "target_on_right", "text_width", "tg_stroke", "tier_rank", "vpc_glance",
           "waf_badge"]

# The VPC's attachment row: edges to the internet, AWS services and other networks, and managed
# services whose network interfaces span several subnets (EKS control plane, VPC Lambda, ECS services
# whose tasks were not collected).
EDGE_KINDS = {"internet_gateway": 0, "vpn_gateway": 1, "vpc_peering": 2, "tgw_attachment": 3, "vpc_endpoint": 4,
              "eks_cluster": 5, "ecs_service": 6, "lambda_function": 7, "sagemaker_endpoint": 8}
KIND_ORDER = {"transit_gateway": -1, "load_balancer": 0, "target_group": 1, "ec2": 2, "registered_target": 3,
              "security_group": 4, "network_interface": 5, "volume": 6}


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


ROUTES = {"public": "0.0.0.0/0 → IGW", "nat": "0.0.0.0/0 → NAT", "tgw": "0.0.0.0/0 → TGW", "vgw": "0.0.0.0/0 → VGW",
          "isolated": "인터넷 경로 없음"}


def arrange(kids, size, asg_of, nodes):
    """Slots inside a subnet: members of one Auto Scaling group share rows, each group starting a new row,
    so a group's frame across AZ columns never encloses another group's instance."""
    groups = {}
    for kid in kids:
        groups.setdefault(asg_of.get(kid["id"]), []).append(kid)
    slots, row = [], 0
    for key in sorted(groups, key=lambda g: (g is None, nodes[g]["name"] if g else "", g or "")):
        slots += [(kid, row + i // size, i % size) for i, kid in enumerate(groups[key])]
        row += -(-len(groups[key]) // size)
    return slots, row


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


def detail(canvas, snapshot, graph, selection, nodes, vpc_of, subnet_of):
    visible = set(selection["nodes"])
    focus = selection["focus"]
    drawn_edges = selection["edges"]
    incoming = {}
    for e in drawn_edges:
        incoming.setdefault(e["target"], []).append(e)
    vpcs = sorted((nodes[k] for k in visible if nodes[k]["kind"] == "vpc"), key=lambda n: (n["name"], n["id"]))
    lanes = {}  # per VPC routing geometry, absolute coordinates
    placed = {}
    suppressed = set()  # registrations drawn as one target group -> Auto Scaling group edge
    frames_at = {}  # Auto Scaling group -> (frame cell, leftmost AZ column)
    meta = snapshot["metadata"]
    if meta.get("combined"):
        # One VPC (or resource) of a combined snapshot: its own account and region frame the drawing.
        subject = next((nodes[k] for k in visible if nodes[k]["kind"] == "vpc"), None) or nodes.get(focus)
        if subject:
            meta = {**meta, "combined": False, "account_id": subject["account"], "region": subject["region"]}
    # AWS Architecture Icons nesting: AWS Cloud > Region > VPC > Availability Zone > subnet; CloudFront at the edge,
    # outside the Region.
    distributions = sorted((nodes[k] for k in visible if nodes[k]["kind"] == "cloudfront_distribution"), key=lambda n: (n["name"], n["id"]))
    cloud = canvas.add("cloud", "1", 24, 112, 0, 0, "cloud", cloud_label(meta))
    region = canvas.add("region", "cloud", edge_offset(distributions, [e for e in drawn_edges if e["relation"] in EDGE]), 44, 0, 0, "region",
                        f"Region · {meta['region']}")
    cursor = 46
    tg_order = {}

    def resource(key, parent, x, y, side):
        n = nodes[key]
        shape = canvas.add(cell_id(key), parent, x, y, ICON, ICON, "resource", icon_label(n, side), resource=key, kind=n["kind"],
                           reference=n["reference_only"], label_side=side, emphasis=key == focus, tooltip=n["name"],
                           outside_vpc=outside_vpc(n), variant=icon_variant(n))
        placed[key] = shape
        waf_badge(canvas, shape, n)
        return shape

    subnet_at = {}  # subnet -> (cell, AZ column) for placement routes
    refs_votes = {}  # target outside every VPC -> gutter sides of the target groups feeding it
    peer_of = {e["source"]: e["target"] for e in graph["edges"] if e["relation"] == "peer_vpc"}
    for vpc in vpcs:
        members = sorted((nodes[k] for k in visible if k not in placed and (vpc_of.get(k) == vpc["id"] or peer_of.get(k) == vpc["id"])),
                         key=lambda n: (n["name"], n["id"]))
        # Internet-facing entry points first (left), then internal ones.
        lbs = sorted((n for n in members if n["kind"] == "load_balancer"),
                     key=lambda n: (n["attributes"].get("Scheme") != "internet-facing", n["name"], n["id"]))
        tgs = [n for n in members if n["kind"] == "target_group"]
        lb_index = {n["id"]: i for i, n in enumerate(lbs)}
        associated = {}
        for e in graph["edges"]:
            if e["relation"] == "associated_load_balancer" and e["target"] in lb_index:
                associated.setdefault(e["source"], []).append(lb_index[e["target"]])
        tgs.sort(key=lambda n: (sum(associated.get(n["id"], [len(lbs)])) / len(associated.get(n["id"], [0])), n["name"], n["id"]))
        tg_index = {n["id"]: i for i, n in enumerate(tgs)}
        tg_order.update(tg_index)
        registered = {}
        for e in graph["edges"]:
            if e["relation"] == "registered_target" and e["source"] in tg_index:
                registered.setdefault(e["target"], []).append(tg_index[e["source"]])
        barycenter = lambda n: (sum(registered.get(n["id"], [len(tgs)])) / len(registered.get(n["id"], [0])), n["name"], n["id"])
        subnets = [n for n in members if n["kind"] == "subnet"]
        children = {s["id"]: sorted((nodes[k] for k in visible if subnet_of.get(k) == s["id"]), key=barycenter) for s in subnets}
        asg_of = {e["target"]: e["source"] for e in graph["edges"] if e["relation"] in GROUPS.values()}
        # Auto Scaling groups and ECS services are framed around their members placed in this VPC's subnets.
        group_inside = {n["id"]: sorted(e["target"] for e in graph["edges"] if e["source"] == n["id"] and e["relation"] == GROUPS[n["kind"]]
                                        and e["target"] in visible and subnet_of.get(e["target"]) in children)
                        for n in members if n["kind"] in GROUPS}
        gateways = sorted((n for n in members if n["kind"] in EDGE_KINDS and not group_inside.get(n["id"])),
                          key=lambda n: (EDGE_KINDS[n["kind"]], n["name"], n["id"]))
        unplaced = sorted((n for n in members if n["kind"] not in {"subnet", "load_balancer", "target_group", *GROUPS,
                                                                   "route_table", *EDGE_KINDS}
                           and subnet_of.get(n["id"]) not in children), key=barycenter)
        azs = sorted({s["attributes"].get("AvailabilityZone", "AZ 미확인") for s in subnets})
        last = len(azs)  # gutter i is left of AZ column i; gutter `last` is the VPC's right margin
        column = {c["id"]: i for i, az in enumerate(azs) for s in subnets
                  if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az for c in children[s["id"]]}
        column.update({n["id"]: 0 for n in unplaced})
        # Target groups in the right half send outside-VPC targets down the right margin.
        side = {t["id"]: last if len(tgs) > 1 and tg_index[t["id"]] >= len(tgs) / 2 else 0 for t in tgs}
        # Auto Scaling groups are framed across AZ columns when their members share one tier row;
        # a target group attached to a framed group then gets one edge instead of one per instance.
        frames = {gid: inside for gid, inside in group_inside.items()
                  if inside and len({tier_rank(nodes[subnet_of[m]]) for m in inside}) == 1}
        # Gutters reserve room for both the registrations and the group edge; which one is drawn
        # is decided once the frame is known to enclose only its own members.
        demand = [set() for _ in range(last + 1)]
        placements = []
        for e in drawn_edges:
            if e["relation"] in GROUP_TARGETS and e["source"] in frames:
                demand[min(column[m] for m in frames[e["source"]])].add(e["target"])
            elif e["relation"] == "registered_target" and e["source"] in tg_index and vpc_of.get(e["target"]) in (None, vpc["id"]):
                g = column.get(e["target"], side[e["source"]])
                demand[g].add(e["source"])
                if e["target"] not in column:
                    # The final side depends on where the target lands in the reference box.
                    demand[0].add(e["source"])
                    demand[last].add(e["source"])
                    refs_votes.setdefault(e["target"], []).append(g == last and last > 0)
            elif e["relation"] in AUXILIARY | REGIONAL and e["relation"] not in REMOTE and vpc_of.get(e["source"]) == vpc["id"]:
                demand[column.get(e["source"], 0)].add(e["id"])
            elif e["relation"] == "in_subnet" and e["source"] in lb_index and e["target"] in children:
                placements.append(e["id"])
                demand[0].add(e["id"])
                demand[azs.index(nodes[e["target"]]["attributes"].get("AvailabilityZone", "AZ 미확인"))].add(e["id"])
        gutters = [max(28, 16 + 8 * len(d)) for d in demand]
        per_row = {s["id"]: 2 if len(children[s["id"]]) <= 4 else 3 for s in subnets}
        az_subnets = {az: sorted((s for s in subnets if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az),
                                 key=lambda s: (tier_rank(s), s["name"], s["id"])) for az in azs}
        col_width = {az: max((per_row[s["id"]] * SLOT + 40 for s in az_subnets[az]), default=SLOT * 2 + 40) + 32 for az in azs}
        az_width = sum(gutters[:last]) + sum(col_width.values()) + 24 + gutters[last]
        band_width = max(len(lbs), len(tgs)) * SIDE_SLOT + 2 * max(gutters)
        bottom_width = len(unplaced) * SLOT + gutters[0] + gutters[last] + 40
        width = max(720, az_width, band_width, bottom_width)
        # The attachment row wraps instead of widening the VPC: a private VPC with a dozen interface
        # endpoints would otherwise be three times as wide as its AZ columns.
        per_band = max(1, int((width - 2 * max(gutters)) // SIDE_SLOT))
        gateway_rows = [gateways[i:i + per_band] for i in range(0, len(gateways), per_band)]
        vid = cell_id(vpc["id"])
        vpc_label = f"VPC  ·  {vpc['name']}  ·  {vpc['attributes'].get('CidrBlock', '')}"
        group = canvas.add(vid, "region", 20, cursor, width, 0, "vpc", fit_label(vpc_label, width - 60, 1), resource=vpc["id"],
                           emphasis=vpc["id"] == focus)
        y = VPC_HEADER + LANE_TOP + 6
        ox, oy = canvas.abs[vid]
        geometry = {"last": last, "lb_mid": {}, "gutters": []}
        # Attachment row (gateways, endpoints, EKS, Lambda), then load balancers, then target groups.
        for band in (*gateway_rows, lbs, tgs):
            if not band:
                continue
            start = (width - len(band) * SIDE_SLOT) / 2 + 24
            for i, n in enumerate(band):
                resource(n["id"], vid, start + i * SIDE_SLOT, y, "right")["column"] = 0
                if n["kind"] == "load_balancer":
                    geometry["lb_mid"][n["id"]] = oy + y + ICON + 8 + 6 * (i % 4)
            y += ICON + (34 if band is lbs or band is tgs else 40)
        geometry["channel"] = oy + y - 6
        geometry["placements"] = {key: oy + y - 6 + 7 * (len(tgs) + i) for i, key in enumerate(placements)}
        y += 7 * (len(tgs) + len(placements)) + 10
        if not subnets:
            status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_subnets"), "unavailable")
            text = "이 VPC에서 조회된 서브넷이 없습니다." if status == "complete" else "서브넷 조회가 완료되지 않아 배치를 확인할 수 없습니다."
            if focus and any(n["kind"] == "subnet" and vpc_of.get(k) == vpc["id"] for k, n in nodes.items()):
                # A focus view draws only the subnets its resources sit in; the VPC's other subnets are hidden, not missing.
                text = "선택 리소스와 직접 관계된 서브넷 없음 · 서브넷은 상세 구성에서 확인"
            canvas.add(cell_id(vpc["id"] + "/empty"), vid, gutters[0], y, 420, 40, "caption", text)
            y += 56
        metrics, arranged = {}, {}
        for sub in subnets:
            arranged[sub["id"]], rows = arrange(children[sub["id"]], per_row[sub["id"]], asg_of, nodes)
            # Each route entering or leaving a row needs its own lane above that row.
            busiest = max((sum(1 for e in drawn_edges for c, row, _ in arranged[sub["id"]]
                               if row == r and c["id"] in (e["source"], e["target"]) and e["relation"] != "in_subnet")
                           for r in range(rows)), default=0)
            lane_top = max(LANE_TOP, 12 + 4 * busiest)
            pitch = ROW - LANE_TOP + lane_top
            metrics[sub["id"]] = (lane_top, pitch, SUBNET_HEADER + rows * pitch + 8 if rows else SUBNET_HEADER + 56)
        # Same tier sits at the same height in every AZ column, as in AWS reference diagrams.
        ranks = sorted({tier_rank(sub) for sub in subnets})
        block = {r: max(sum(metrics[sub["id"]][2] + 16 for sub in az_subnets[az] if tier_rank(sub) == r) for az in azs) for r in ranks}
        block_top, top = {}, AZ_HEADER + 10
        for r in ranks:
            block_top[r], top = top, top + block[r]
        x = 0
        columns = []
        for index, az in enumerate(azs):
            geometry["gutters"].append((ox + x, ox + x + gutters[index]))
            x += gutters[index]
            az_id = cell_id(vpc["id"] + "/az/" + az)
            columns.append(canvas.add(az_id, vid, x, y, col_width[az], top, "az", az))
            offsets = dict(block_top)
            for sub in az_subnets[az]:
                lane_top, pitch, sh = metrics[sub["id"]]
                sy = offsets[tier_rank(sub)]
                offsets[tier_rank(sub)] += sh + 16
                sid = cell_id(sub["id"])
                subnet_at[sub["id"]] = (sid, index)
                canvas.add(sid, az_id, 16, sy, per_row[sub["id"]] * SLOT + 40, sh, "subnet",
                           fit_label(sub["name"], per_row[sub["id"]] * SLOT - 20, 1, 11) + "\n" +
                           fit_label(" · ".join(filter(None, (sub["attributes"].get("CidrBlock", ""), ROUTES.get(sub.get("route_class"))))),
                                     per_row[sub["id"]] * SLOT - 20, 1, 11),
                           resource=sub["id"], emphasis=sub["id"] == focus, public=is_public(sub),
                           route_class=sub.get("route_class"), tier=tier_rank(sub))
                for n, row, slot in arranged[sub["id"]]:
                    shape = resource(n["id"], sid, 20 + slot * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + row * pitch + lane_top, "bottom")
                    shape.update(column=index, lane_top=lane_top)
                if not children[sub["id"]]:
                    canvas.add(cell_id(sub["id"] + "/empty"), sid, 20, SUBNET_HEADER + 14, per_row[sub["id"]] * SLOT, 30, "caption",
                               "선택 리소스와 직접 관계된 인스턴스 없음" if focus else "조회된 인스턴스 없음")
            x += col_width[az]
        column_height = top
        for asg_id, inside in frames.items():
            boxes = [(canvas.abs[placed[m]["id"]], canvas.label_box(placed[m])) for m in inside]
            left = min(min(icon[0], label[0]) for icon, label in boxes) - 10
            right = max(max(icon[0] + ICON, label[0] + label[2]) for icon, label in boxes) + 10
            top_y = min(icon[1] for icon, _ in boxes) - 18
            bottom = max(label[1] + label[3] for _, label in boxes) + 24
            strangers = [k for k, shape in placed.items() if k not in inside and shape["role"] == "resource"
                         and left < canvas.abs[shape["id"]][0] + ICON and canvas.abs[shape["id"]][0] < right
                         and top_y < canvas.abs[shape["id"]][1] + ICON and canvas.abs[shape["id"]][1] < bottom]
            if strangers:
                continue  # a frame would claim other resources; keep per-instance edges instead
            frame = canvas.add(cell_id(asg_id), vid, left - ox, top_y - oy, right - left, bottom - top_y, "asg",
                               fit_label(group_title(graph, nodes, asg_id), right - left - 40, 1, 11), resource=asg_id,
                               emphasis=asg_id == focus, frame="ecs" if nodes[asg_id]["kind"] == "ecs_service" else "asg")
            frames_at[asg_id] = (frame["id"], min(column[m] for m in inside))
        for e in drawn_edges:
            # DescribeAutoScalingGroups.TargetGroupARNs: source is the group, target the target group.
            if e["relation"] in GROUP_TARGETS and e["target"] in tg_index:
                if e["source"] in frames_at:
                    suppressed.update(r["id"] for r in drawn_edges if r["relation"] == "registered_target"
                                      and r["source"] == e["target"] and r["target"] in frames[e["source"]])
                else:
                    suppressed.add(e["id"])
        geometry["gutters"].append((ox + width - gutters[last], ox + width))
        y += column_height + 24
        if unplaced:
            band = canvas.add(cell_id(vpc["id"] + "/unplaced"), vid, gutters[0], y, width - gutters[0] - gutters[last],
                              SUBNET_HEADER + ROW, "unplaced", "서브넷 미확인 · VPC 소속만 확인된 리소스")
            for i, n in enumerate(unplaced):
                resource(n["id"], band["id"], 20 + i * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + LANE_TOP, "bottom")["column"] = 0
            y += band["h"] + 24
        group["h"] = y
        lanes[vpc["id"]] = geometry
        cursor += y + 36
    # Auto Scaling groups and ECS services with placed tasks are drawn only as frames around their members.
    others = sorted((nodes[k] for k in visible if k not in placed and nodes[k]["kind"] not in {"vpc", "subnet", "cloudfront_distribution", *GROUPS}),
                    key=lambda n: (KIND_ORDER.get(n["kind"], 9), n["name"], n["id"]))
    width = max([sh["w"] for sh in canvas.shapes if sh["role"] == "vpc"] + [5 * SLOT + 60])

    def place_box(key, role, label, parent, x, y, items, min_width):
        """One row: routes enter from the lane above it, so a second row would put routes through labels."""
        box_width = max(min_width, len(items) * SLOT + 60)
        per_row = max(1, int((box_width - 60) // SLOT))
        right = [n for n in items if refs_votes.get(n["id"]) and sum(refs_votes[n["id"]]) * 2 > len(refs_votes[n["id"]])]
        left = [n for n in items if n not in right]
        # Targets reached from the right margin sit at the right end, so their routes stay short.
        slots = [(n, i) for i, n in enumerate(left)] + [(n, per_row - len(right) + i) for i, n in enumerate(right)]
        ids = {n["id"] for n in items}
        lane_top = max(LANE_TOP, 12 + 4 * sum(1 for e in drawn_edges if e["target"] in ids or e["source"] in ids))
        box = canvas.add(key, parent, x, y, box_width, SUBNET_HEADER + ROW - LANE_TOP + lane_top + 8, role, label)
        for n, slot in slots:
            resource(n["id"], key, 30 + slot * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + lane_top, "bottom")["lane_top"] = lane_top
        return box

    inside = [n for n in others if not outside_vpc(n)]
    if inside:
        # Routes to the box come down the VPCs' gutters. Starting the box right of the left gutter, those routes enter
        # from its side below the caption; the two-line caption ends before the first gutter between AZ columns.
        inset = max([g["gutters"][0][1] - g["gutters"][0][0] for g in lanes.values() if len(g["gutters"]) > 1] + [0])
        cursor += place_box("references", "references", "VPC 밖 리전 리소스 · 배치 미확인\n참조만 확인 (점선 = 참조)",
                            "region", 20 + inset, cursor, inside, width)["h"] + 36
    region["w"] = max([sh["x"] + sh["w"] for sh in canvas.shapes if sh["parent"] == "region"] + [400]) + 20
    region["h"] = cursor
    cloud["w"], cloud["h"] = region["x"] + region["w"] + 20, region["y"] + region["h"] + 20
    placed.update(edge_column(canvas, nodes, cloud, distributions))
    remote = [n for n in others if outside_vpc(n)]
    if remote:
        # Beside the AWS Cloud: routes leave the VPC's right margin above the load balancer row.
        place_box("onprem", "onprem", "Corporate data center · 고객 게이트웨이·DX · AvailabilityZone=all 로 등록된 VPC 밖 대상", "1",
                  cloud["x"] + cloud["w"] + 48, cloud["y"], remote, 520)
    route(canvas, [e for e in drawn_edges if e["id"] not in suppressed], vpc_of, placed, lanes, tg_order, subnet_at, frames_at)


