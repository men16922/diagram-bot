"""Deterministic layered layout for one view. Produces geometry only; drawio.py serializes it.

Bands inside a VPC, top to bottom: load balancers, target groups, an edge channel, AZ columns
holding subnets and their instances, then VPC resources whose subnet was not confirmed.
Resources outside every drawn VPC go to a separate reference container. Edges from target
groups run through the channel and the gutters between AZ columns, so they never cross a
subnet or an icon. Identical input and options always yield identical coordinates.
"""
import hashlib

from .views import AUXILIARY, containment

ENGINE, VERSION = "diagram-bot-layered", 2
ICON, LINE, MAX_LINES = 52, 15, 3
BOTTOM_LABEL, SIDE_LABEL = 140, 200
SLOT, SIDE_SLOT = 150, 290
LANE_TOP = 36  # space above an icon row for up to LANES entering routes
LANES = (LANE_TOP - 8) // 4
ROW = LANE_TOP + ICON + 8 + MAX_LINES * LINE + 8
SUBNET_HEADER, AZ_HEADER, VPC_HEADER = 46, 32, 38
KIND_ORDER = {"load_balancer": 0, "target_group": 1, "ec2": 2, "registered_target": 3,
              "security_group": 4, "network_interface": 5, "volume": 6}


def cell_id(value):
    return "r-" + hashlib.sha256(value.encode()).hexdigest()[:24]


def text_width(text, size=12):
    # Conservative Arial estimate: CJK glyphs are about one em, Latin about 0.6 em.
    return sum(size if ord(c) > 0x2E7F else size * 0.62 for c in text)


def fit_label(text, width, lines=MAX_LINES, size=12):
    """Truncate so the label fits `lines` wrapped lines; full text stays in the inspector."""
    budget = width * lines * 0.85
    if text_width(text, size) <= budget:
        return text
    out = ""
    for c in text:
        if text_width(out + c + "…", size) > budget:
            break
        out += c
    return out + "…"


def label_lines(text, width, size=12):
    return sum(max(1, -(-int(text_width(part, size) / 0.85) // width)) for part in text.split("\n"))


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
        lines = label_lines(shape["label"], BOTTOM_LABEL if shape.get("label_side") == "bottom" else SIDE_LABEL)
        if shape.get("label_side") == "bottom":
            return (x + shape["w"] / 2 - BOTTOM_LABEL / 2, y + shape["h"] + 8, BOTTOM_LABEL, lines * LINE)
        if shape.get("label_side") == "right":
            height = lines * LINE
            return (x + shape["w"] + 8, y + shape["h"] / 2 - height / 2, SIDE_LABEL, height)
        return None


def resource_label(node):
    attrs = node["attributes"]
    if node["kind"] == "registered_target":
        target = attrs.get("Target", {})
        return f"{target.get('Id', node['name'])}:{target.get('Port', '')}"
    if node["kind"] == "load_balancer" and attrs.get("Type"):
        kind = {"application": "ALB", "network": "NLB", "gateway": "GWLB"}.get(attrs["Type"], attrs["Type"])
        return f"{fit_label(node['name'], SIDE_LABEL, 2)}\n{kind} · {attrs.get('Scheme', '')}".rstrip(" ·")
    if node["kind"] == "target_group" and attrs.get("Protocol"):
        return f"{fit_label(node['name'], SIDE_LABEL, 2)}\n{attrs['Protocol']}:{attrs.get('Port', '')} · {attrs.get('TargetType', '')}"
    return node["name"]


def layout_view(snapshot, graph, selection):
    canvas = Canvas()
    nodes, vpc_of, subnet_of = containment(graph)
    meta, counts, view = snapshot["metadata"], selection["counts"], selection["view"]
    heading = f"{'SAMPLE · ' if meta.get('demo') else ''}AWS / {meta['account_id']} / {meta['region']}"
    titles = {"overview": "전체 구성", "detail": "상세 구성", "focus": "선택 리소스"}
    subject = ""
    if view["kind"] == "detail":
        subject = " · " + nodes[next(k for k in selection["nodes"] if nodes[k]["kind"] == "vpc" and nodes[k]["resource_id"] == view["vpc"])]["name"]
    elif view["kind"] == "focus":
        subject = " · " + nodes[selection["focus"]]["name"]
    canvas.add("heading", "1", 24, 12, 1200, 30, "title", fit_label(heading + " · " + titles[view["kind"]] + subject, 1180, 1, 19))
    incomplete = [f"{c['operation']}({c.get('error_code', c['status'])})" for c in snapshot["coverage"] if c["status"] != "complete"]
    status = "부분 조회: " + ", ".join(incomplete) if incomplete else "조회 완료"
    scope = (f"{status} · {meta['finished_at']}\n표시 {counts['visible']}개 / 수집 {counts['collected']}개 · 참조 {counts['references']}개"
             f" · 숨김 {counts['hidden']}개 · 설정 관계만 표시, 실제 통신 미확인 · 사용자 주석은 수집 데이터와 별도")
    canvas.add("scope", "1", 24, 48, 960, 42, "caption", scope, layout=f"{ENGINE}/{VERSION}",
               view=view["kind"], vpc=view["vpc"] or "", focus=view["resource"] or "",
               references="1" if view["references"] else "0")
    if view["kind"] == "overview":
        overview(canvas, snapshot, graph, nodes, vpc_of)
    else:
        detail(canvas, snapshot, graph, selection, nodes, vpc_of, subnet_of)
    width = max(s["x"] + s["w"] for s in canvas.shapes if s["parent"] == "1")
    height = max(s["y"] + s["h"] for s in canvas.shapes if s["parent"] == "1")
    labels = {s["id"]: canvas.label_box(s) for s in canvas.shapes if s.get("label_side")}
    return {"engine": ENGINE, "version": VERSION, "view": view, "counts": counts, "width": width, "height": height,
            "shapes": canvas.shapes, "edges": canvas.edges, "absolute": dict(canvas.abs), "labels": labels}


def overview(canvas, snapshot, graph, nodes, vpc_of):
    vpcs = sorted((n for n in nodes.values() if n["kind"] == "vpc"), key=lambda n: (n["name"], n["id"]))
    columns = 3
    card_w, card_h, gap = 320, 112, 24
    rows = -(-(len(vpcs) + 1) // columns)
    width = 32 + columns * (card_w + gap)
    group = canvas.add("account", "1", 24, 112, width, VPC_HEADER + 24 + rows * (card_h + gap), "account",
                       f"계정 {snapshot['metadata']['account_id']} · {snapshot['metadata']['region']} · VPC {len(vpcs)}개 · 요약 카드를 선택하면 상세 구성으로 이동")
    members = {}
    for key, vpc in vpc_of.items():
        members.setdefault(vpc, []).append(nodes[key])
    vpc_status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_vpcs"), "unavailable")
    cards = []
    for vpc in vpcs:
        inside = members.get(vpc["id"], [])
        count = lambda kind: sum(1 for n in inside if n["kind"] == kind)
        azs = sorted({n["attributes"].get("AvailabilityZone") for n in inside if n["kind"] == "subnet"} - {None})
        label = (f"VPC · {fit_label(vpc['name'], 290, 1)}\n{vpc['attributes'].get('CidrBlock', '')} · {vpc['resource_id']}\n"
                 f"Subnet {count('subnet')} · EC2 {count('ec2')} · LB {count('load_balancer')} · TG {count('target_group')}\n"
                 f"AZ {', '.join(azs) if azs else '미확인'}")
        cards.append((label, vpc["id"], len(inside) == 0))
    outside = [n for n in nodes.values() if n["kind"] != "vpc" and n["id"] not in vpc_of]
    if outside:
        refs = sum(n["reference_only"] for n in outside)
        cards.append((f"VPC 외 · 배치 미확인\n리소스 {len(outside)}개 (참조만 확인 {refs}개)\n보안 그룹·ENI·EBS·IP 대상 등\n상세 보기에서 관계 확인", None, False))
    if not vpcs:
        message = "조회된 VPC가 없습니다." if vpc_status == "complete" else "VPC 조회가 완료되지 않았습니다.\n빈 환경이 아니라 권한·조회 실패일 수 있습니다."
        cards.insert(0, (message, None, True))
    for i, (label, resource, empty) in enumerate(cards):
        canvas.add(cell_id(resource or f"summary/{i}"), group["id"], 24 + (i % columns) * (card_w + gap),
                   VPC_HEADER + 18 + (i // columns) * (card_h + gap), card_w, card_h, "summary", label,
                   resource=resource, empty=empty)
    group["h"] = VPC_HEADER + 18 + -(-len(cards) // columns) * (card_h + gap)


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
    cursor = 112
    tg_order = {}

    def resource(key, parent, x, y, side):
        n = nodes[key]
        width = SIDE_LABEL if side == "right" else BOTTOM_LABEL
        label = resource_label(n)
        label = "\n".join(fit_label(part, width, MAX_LINES if i == 0 else 1) for i, part in enumerate(label.split("\n")))
        shape = canvas.add(cell_id(key), parent, x, y, ICON, ICON, "resource", label, resource=key, kind=n["kind"],
                           reference=n["reference_only"], label_side=side, emphasis=key == focus, tooltip=n["name"])
        placed[key] = shape
        return shape

    subnet_at = {}  # subnet -> (cell, AZ column) for placement routes
    refs_votes = {}  # target outside every VPC -> gutter sides of the target groups feeding it
    for vpc in vpcs:
        members = sorted((nodes[k] for k in visible if vpc_of.get(k) == vpc["id"]), key=lambda n: (n["name"], n["id"]))
        lbs = [n for n in members if n["kind"] == "load_balancer"]
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
        unplaced = sorted((n for n in members if n["kind"] not in {"subnet", "load_balancer", "target_group"}
                           and subnet_of.get(n["id"]) not in children), key=barycenter)
        azs = sorted({s["attributes"].get("AvailabilityZone", "AZ 미확인") for s in subnets})
        last = len(azs)  # gutter i is left of AZ column i; gutter `last` is the VPC's right margin
        column = {c["id"]: i for i, az in enumerate(azs) for s in subnets
                  if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az for c in children[s["id"]]}
        column.update({n["id"]: 0 for n in unplaced})
        # Target groups in the right half send outside-VPC targets down the right margin.
        side = {t["id"]: last if len(tgs) > 1 and tg_index[t["id"]] >= len(tgs) / 2 else 0 for t in tgs}
        demand = [set() for _ in range(last + 1)]
        placements = []
        for e in drawn_edges:
            if e["relation"] == "registered_target" and e["source"] in tg_index and vpc_of.get(e["target"]) in (None, vpc["id"]):
                g = column.get(e["target"], side[e["source"]])
                demand[g].add(e["source"])
                if e["target"] not in column:
                    # The final side depends on where the target lands in the reference box.
                    demand[0].add(e["source"])
                    demand[last].add(e["source"])
                    refs_votes.setdefault(e["target"], []).append(g == last and last > 0)
            elif e["relation"] in AUXILIARY and vpc_of.get(e["source"]) == vpc["id"]:
                demand[column.get(e["source"], 0)].add(e["id"])
            elif e["relation"] == "in_subnet" and e["source"] in lb_index and e["target"] in children:
                placements.append(e["id"])
                demand[0].add(e["id"])
                demand[azs.index(nodes[e["target"]]["attributes"].get("AvailabilityZone", "AZ 미확인"))].add(e["id"])
        gutters = [max(28, 16 + 8 * len(d)) for d in demand]
        per_row = {s["id"]: 2 if len(children[s["id"]]) <= 4 else 3 for s in subnets}
        az_subnets = {az: sorted((s for s in subnets if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az),
                                 key=lambda s: (s["name"], s["id"])) for az in azs}
        col_width = {az: max((per_row[s["id"]] * SLOT + 40 for s in az_subnets[az]), default=SLOT * 2 + 40) + 32 for az in azs}
        az_width = sum(gutters[:last]) + sum(col_width.values()) + 24 + gutters[last]
        band_width = max(len(lbs), len(tgs)) * SIDE_SLOT + 2 * max(gutters)
        bottom_width = len(unplaced) * SLOT + gutters[0] + gutters[last] + 40
        width = max(720, az_width, band_width, bottom_width)
        vid = cell_id(vpc["id"])
        vpc_label = f"VPC  ·  {vpc['name']}  ·  {vpc['attributes'].get('CidrBlock', '')}"
        group = canvas.add(vid, "1", 24, cursor, width, 0, "vpc", fit_label(vpc_label, width - 40, 1), resource=vpc["id"],
                           emphasis=vpc["id"] == focus)
        y = VPC_HEADER + LANE_TOP + 6
        ox, oy = canvas.abs[vid]
        geometry = {"last": last, "lb_mid": {}, "gutters": []}
        for band in (lbs, tgs):
            if not band:
                continue
            start = (width - len(band) * SIDE_SLOT) / 2 + 24
            for i, n in enumerate(band):
                resource(n["id"], vid, start + i * SIDE_SLOT, y, "right")["column"] = 0
                if band is lbs:
                    geometry["lb_mid"][n["id"]] = oy + y + ICON + 8 + 6 * (i % 4)
            y += ICON + 34
        geometry["channel"] = oy + y - 6
        geometry["placements"] = {key: oy + y - 6 + 7 * (len(tgs) + i) for i, key in enumerate(placements)}
        y += 7 * (len(tgs) + len(placements)) + 10
        if not subnets:
            status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_subnets"), "unavailable")
            text = "이 VPC에서 조회된 서브넷이 없습니다." if status == "complete" else "서브넷 조회가 완료되지 않아 배치를 확인할 수 없습니다."
            canvas.add(cell_id(vpc["id"] + "/empty"), vid, gutters[0], y, 420, 40, "caption", text)
            y += 56
        x = 0
        column_height = 0
        columns = []
        for index, az in enumerate(azs):
            geometry["gutters"].append((ox + x, ox + x + gutters[index]))
            x += gutters[index]
            az_id = cell_id(vpc["id"] + "/az/" + az)
            az_group = canvas.add(az_id, vid, x, y, col_width[az], 0, "az", az)
            columns.append(az_group)
            sy = AZ_HEADER + 10
            for s in az_subnets[az]:
                rows = -(-len(children[s["id"]]) // per_row[s["id"]])
                sh = SUBNET_HEADER + rows * ROW + 8 if rows else SUBNET_HEADER + 56
                sid = cell_id(s["id"])
                subnet_at[s["id"]] = (sid, index)
                canvas.add(sid, az_id, 16, sy, per_row[s["id"]] * SLOT + 40, sh, "subnet",
                           fit_label(f"{s['name']}\n{s['attributes'].get('CidrBlock', '')}", per_row[s["id"]] * SLOT, 1, 10),
                           resource=s["id"], emphasis=s["id"] == focus)
                for i, n in enumerate(children[s["id"]]):
                    row, slot = divmod(i, per_row[s["id"]])
                    resource(n["id"], sid, 20 + slot * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + row * ROW + LANE_TOP, "bottom")["column"] = index
                if not children[s["id"]]:
                    canvas.add(cell_id(s["id"] + "/empty"), sid, 20, SUBNET_HEADER + 14, per_row[s["id"]] * SLOT, 30, "caption",
                               "선택 리소스와 직접 관계된 인스턴스 없음" if focus else "조회된 인스턴스 없음")
                sy += sh + 16
            column_height = max(column_height, sy)
            x += col_width[az]
        geometry["gutters"].append((ox + width - gutters[last], ox + width))
        for az_group in columns:
            az_group["h"] = column_height
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
    others = sorted((nodes[k] for k in visible if k not in placed and nodes[k]["kind"] not in {"vpc", "subnet"}),
                    key=lambda n: (KIND_ORDER.get(n["kind"], 9), n["name"], n["id"]))
    if others:
        width = max([s["w"] for s in canvas.shapes if s["role"] == "vpc"] + [5 * SLOT + 60])
        per_row = max(1, int((width - 60) // SLOT))
        right = [n for n in others if refs_votes.get(n["id"]) and sum(refs_votes[n["id"]]) * 2 > len(refs_votes[n["id"]])]
        left = [n for n in others if n not in right]
        # Targets reached from the right margin sit at the right end, so their routes stay short.
        if len(left) + len(right) <= per_row:
            slots = [(n, 0, i) for i, n in enumerate(left)] + [(n, 0, per_row - len(right) + i) for i, n in enumerate(right)]
        else:
            ordered = left + right
            slots = [(n, i // per_row, i % per_row) for i, n in enumerate(ordered)]
        rows = max(row for _, row, _ in slots) + 1
        box = canvas.add("references", "1", 24, cursor, width, SUBNET_HEADER + rows * ROW + 8, "references",
                         "배치 미확인 / VPC 외 리소스 · 점선은 참조만 확인됨")
        for n, row, slot in slots:
            resource(n["id"], box["id"], 30 + slot * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + row * ROW + LANE_TOP, "bottom")
    route(canvas, drawn_edges, vpc_of, placed, lanes, tg_order, subnet_at)


def route(canvas, drawn_edges, vpc_of, placed, lanes, tg_order, subnet_at):
    """Explicit orthogonal routes. Only edges sharing a source (a bus) or a target may share a segment.

    Each target group drops one trunk to its own channel row; every gutter gives each source group
    its own vertical slot (deeper turns further out); every icon row gives each entering edge its
    own lane, the edge going further sitting higher, so parallel routes never coincide.
    """
    position = lambda key: canvas.abs[placed[key]["id"]]
    shapes = {shape["id"]: shape for shape in canvas.shapes}
    specs, plain = {}, []
    for e in sorted(drawn_edges, key=lambda e: e["id"]):
        geometry = lanes.get(vpc_of.get(e["source"]))
        if e["relation"] == "in_subnet" and geometry and e["source"] in placed and e["id"] in geometry["placements"] \
                and e["target"] in subnet_at:
            cell, g = subnet_at[e["target"]]
            x, y = canvas.abs[cell]
            specs[e["id"]] = {"edge": e, "kind": "place", "geometry": geometry, "gutter": g, "group": ("place", e["id"]),
                              "depth": y, "far": x, "subnet": (x, y, shapes[cell]["h"])}
            continue
        if not (geometry and e["source"] in placed and e["target"] in placed):
            plain.append(e)
            continue
        target = placed[e["target"]]
        tx, ty = position(e["target"])
        same = vpc_of.get(e["target"]) == vpc_of.get(e["source"]) and "column" in target
        outside = target["parent"] == "references"
        spec = {"edge": e, "geometry": geometry, "depth": ty, "far": tx}
        if e["relation"] == "registered_target" and (same or outside):
            g = target["column"] if same else (geometry["last"] if target_on_right(canvas, target, geometry) else 0)
            specs[e["id"]] = {**spec, "kind": "bus", "gutter": g, "group": ("bus", e["source"])}
        elif e["relation"] in AUXILIARY and outside and "column" in placed[e["source"]]:
            specs[e["id"]] = {**spec, "kind": "aux", "gutter": placed[e["source"]]["column"], "group": ("aux", e["id"])}
        elif e["relation"] == "associated_load_balancer" and e["target"] in geometry["lb_mid"] \
                and placed[e["target"]]["parent"] == placed[e["source"]]["parent"]:
            specs[e["id"]] = {**spec, "kind": "lb"}
        else:
            plain.append(e)
    # Gutter slots per (VPC, gutter, source group). Placement routes also descend the left margin
    # from the lane above their load balancer to the channel; that leg is a shallow slot in gutter 0.
    groups = {}
    for spec in specs.values():
        if spec["kind"] == "lb":
            continue
        groups.setdefault((id(spec["geometry"]), spec["gutter"]), {}).setdefault(spec["group"], []).append(spec)
        if spec["kind"] == "place":
            channel = spec["geometry"]["placements"][spec["edge"]["id"]]
            leg = {"geometry": spec["geometry"], "depth": channel, "far": position(spec["edge"]["source"])[0], "leg_of": spec}
            groups.setdefault((id(spec["geometry"]), 0), {}).setdefault(("leg", spec["edge"]["id"]), []).append(leg)
    for (_, g), members in groups.items():
        geometry = next(iter(members.values()))[0]["geometry"]
        left, right = geometry["gutters"][g]
        turn_left = g == geometry["last"] and g > 0 or len(geometry["gutters"]) == 1
        ordered = sorted(members.items(), key=lambda item: (-max(m["depth"] for m in item[1]),
                                                            max(m["far"] for m in item[1]) * (-1 if turn_left else 1), item[0]))
        for i, (_, group) in enumerate(ordered):
            for member in group:
                x = right - 8 - 8 * i if turn_left else left + 8 + 8 * i
                if "leg_of" in member:
                    member["leg_of"]["leg_x"] = x
                else:
                    member["x"], member["turn_left"] = x, turn_left
    # Entry points per target, then lanes per icon row.
    entering = {}
    for spec in specs.values():
        if spec["kind"] in ("bus", "aux"):
            entering.setdefault(spec["edge"]["target"], []).append(spec)
    for members in entering.values():
        members.sort(key=lambda s: (s["x"] if not s["turn_left"] else -s["x"], s["edge"]["id"]))
        for i, spec in enumerate(members):
            fraction = (i + 1) / (len(members) + 1)
            spec["entry"] = 1 - fraction if spec["turn_left"] else fraction
    rows, leaving = {}, {}
    for spec in specs.values():
        if spec["kind"] in ("bus", "aux"):
            tx, ty = position(spec["edge"]["target"])
            spec["end"] = tx + spec["entry"] * ICON
            rows.setdefault((placed[spec["edge"]["target"]]["parent"], ty), []).append(("in", spec, spec["end"], False))
        if spec["kind"] in ("aux", "place"):
            leaving.setdefault((spec["kind"], spec["edge"]["source"]), []).append(spec)
    for (kind, source), members in leaving.items():
        members.sort(key=lambda s: s["edge"]["id"])
        sx, sy = position(source)
        for i, spec in enumerate(members):
            spec["exit"] = (i + 1) / (len(members) + 1)
            # Reference routes leave from the left side; placement routes from the top.
            spec["stub"] = sx - 14 - 6 * i if kind == "aux" else sx + spec["exit"] * ICON
            rows.setdefault((placed[source]["parent"], sy), []).append(("out", spec, spec["stub"], False))
    for (_, top), members in rows.items():
        members.sort(key=lambda m: (m[1].get("turn_left", False) and m[0] == "in",
                                    m[2] if m[1].get("turn_left") and m[0] == "in" else -m[2], m[1]["edge"]["id"]))
        for i, (direction, spec, _, _) in enumerate(members):
            spec["lane_in" if direction == "in" else "lane_out"] = top - LANE_TOP + 4 + 4 * (i % LANES)
    for e in plain:
        canvas.edges.append(edge_entry(e, [], "", {"in_subnet": "서브넷 배치"}.get(e["relation"], "")))
    for spec in sorted(specs.values(), key=lambda s: s["edge"]["id"]):
        e, geometry = spec["edge"], spec["geometry"]
        sx, sy = position(e["source"])
        if spec["kind"] == "place":
            x, y, h = spec["subnet"]
            channel = geometry["placements"][e["id"]]
            entry_y = y + SUBNET_HEADER / 2
            path = [(spec["stub"], sy), (spec["stub"], spec["lane_out"]), (spec["leg_x"], spec["lane_out"]), (spec["leg_x"], channel),
                    (spec["x"], channel), (spec["x"], entry_y), (x, entry_y)]
            canvas.edges.append(edge_entry(e, path, f"exitX={spec['exit']:.3f};exitY=0;entryX=0;entryY={SUBNET_HEADER / 2 / h:.3f};"))
            continue
        tx, ty = position(e["target"])
        if spec["kind"] == "lb":
            middle = geometry["lb_mid"][e["target"]]
            path = [(sx + ICON / 2, sy), (sx + ICON / 2, middle), (tx + ICON / 2, middle), (tx + ICON / 2, ty + ICON)]
            canvas.edges.append(edge_entry(e, path, "exitX=0.5;exitY=0;entryX=0.5;entryY=1;"))
            continue
        end = spec["end"]
        if spec["kind"] == "bus":
            channel = geometry["channel"] + 7 * tg_order.get(e["source"], 0)
            path = [(sx + ICON / 2, sy + ICON), (sx + ICON / 2, channel), (spec["x"], channel), (spec["x"], spec["lane_in"]),
                    (end, spec["lane_in"]), (end, ty)]
            constraints = f"exitX=0.5;exitY=1;entryX={spec['entry']:.3f};entryY=0;"
        else:
            exit_y = sy + spec["exit"] * ICON
            path = [(sx, exit_y), (spec["stub"], exit_y), (spec["stub"], spec["lane_out"]), (spec["x"], spec["lane_out"]),
                    (spec["x"], spec["lane_in"]), (end, spec["lane_in"]), (end, ty)]
            constraints = f"exitX=0;exitY={spec['exit']:.3f};entryX={spec['entry']:.3f};entryY=0;"
        ports = [x for x in entering[e["target"]] if x["edge"]["source"] == e["source"]]
        label = f":{e['qualifiers']['port']}" if len(ports) > 1 and e["qualifiers"].get("port") is not None else ""
        entry = edge_entry(e, path, constraints, label)
        entry["label_offset"] = (-16 if spec["entry"] < 0.5 else 18, -9)
        canvas.edges.append(entry)


def target_on_right(canvas, target, geometry):
    """Outside-VPC targets placed in the right half of the reference box use the right margin."""
    return geometry["last"] > 0 and canvas.abs[target["id"]][0] > (geometry["gutters"][0][0] + geometry["gutters"][-1][1]) / 2


def edge_entry(e, path, constraints, label=""):
    return {"id": cell_id("edge/" + e["id"]), "relation_id": e["id"], "relation": e["relation"],
            "source": cell_id(e["source"]), "target": cell_id(e["target"]), "evidence": e["evidence"],
            "points": path[1:-1], "path": path, "constraints": constraints, "label": label,
            "auxiliary": e["relation"] not in {"registered_target", "associated_load_balancer"}}
