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
ROW = 24 + ICON + 8 + MAX_LINES * LINE + 8
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
        gutter = max(28, 12 + 6 * len(tgs))
        per_row = {s["id"]: 2 if len(children[s["id"]]) <= 4 else 3 for s in subnets}
        az_subnets = {az: sorted((s for s in subnets if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az),
                                 key=lambda s: (s["name"], s["id"])) for az in azs}
        col_width = {az: max((per_row[s["id"]] * SLOT + 40 for s in az_subnets[az]), default=SLOT * 2 + 40) + 32 for az in azs}
        az_width = gutter + sum(col_width[az] + gutter for az in azs)
        band_width = max(len(lbs), len(tgs)) * SIDE_SLOT + 2 * gutter
        bottom_width = len(unplaced) * SLOT + 2 * gutter + 40
        width = max(720, az_width, band_width, bottom_width)
        vid = cell_id(vpc["id"])
        vpc_label = f"VPC  ·  {vpc['name']}  ·  {vpc['attributes'].get('CidrBlock', '')}"
        group = canvas.add(vid, "1", 24, cursor, width, 0, "vpc", fit_label(vpc_label, width - 40, 1), resource=vpc["id"],
                           emphasis=vpc["id"] == focus)
        y = VPC_HEADER + 22
        ox, oy = canvas.abs[vid]
        geometry = {"vpc_x": ox, "col0": ox + 6}
        for band in (lbs, tgs):
            if not band:
                continue
            start = (width - len(band) * SIDE_SLOT) / 2 + 24
            for i, n in enumerate(band):
                resource(n["id"], vid, start + i * SIDE_SLOT, y, "right")["gutter"] = ox + 6
            geometry["lb_bottom" if band is lbs else "tg_top"] = oy + y + (ICON if band is lbs else 0)
            y += ICON + 34
        geometry["channel"] = oy + y - 6
        y += 6 * len(tgs) + 10
        if not subnets:
            status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_subnets"), "unavailable")
            text = "이 VPC에서 조회된 서브넷이 없습니다." if status == "complete" else "서브넷 조회가 완료되지 않아 배치를 확인할 수 없습니다."
            canvas.add(cell_id(vpc["id"] + "/empty"), vid, gutter, y, 420, 40, "caption", text)
            y += 56
        x = gutter
        column_height = 0
        columns = []
        for az in azs:
            az_id = cell_id(vpc["id"] + "/az/" + az)
            az_group = canvas.add(az_id, vid, x, y, col_width[az], 0, "az", az)
            columns.append(az_group)
            geometry.setdefault("gutters", {})[az] = ox + x - gutter + 4
            sy = AZ_HEADER + 10
            for s in az_subnets[az]:
                rows = -(-len(children[s["id"]]) // per_row[s["id"]])
                sh = SUBNET_HEADER + rows * ROW + 8 if rows else SUBNET_HEADER + 56
                sid = cell_id(s["id"])
                canvas.add(sid, az_id, 16, sy, per_row[s["id"]] * SLOT + 40, sh, "subnet",
                           fit_label(f"{s['name']}\n{s['attributes'].get('CidrBlock', '')}", per_row[s["id"]] * SLOT, 1, 10),
                           resource=s["id"], emphasis=s["id"] == focus)
                for i, n in enumerate(children[s["id"]]):
                    row, column = divmod(i, per_row[s["id"]])
                    shape = resource(n["id"], sid, 20 + column * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + row * ROW + 24, "bottom")
                    shape["gutter"] = geometry["gutters"][az]
                if not children[s["id"]]:
                    canvas.add(cell_id(s["id"] + "/empty"), sid, 20, SUBNET_HEADER + 14, per_row[s["id"]] * SLOT, 30, "caption",
                               "선택 리소스와 직접 관계된 인스턴스 없음" if focus else "조회된 인스턴스 없음")
                sy += sh + 16
            column_height = max(column_height, sy)
            x += col_width[az] + gutter
        for az_group in columns:
            az_group["h"] = column_height
        y += column_height + 24
        if unplaced:
            band = canvas.add(cell_id(vpc["id"] + "/unplaced"), vid, gutter, y, width - 2 * gutter, SUBNET_HEADER + ROW, "unplaced",
                              "서브넷 미확인 · VPC 소속만 확인된 리소스")
            for i, n in enumerate(unplaced):
                shape = resource(n["id"], band["id"], 20 + i * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + 24, "bottom")
                shape["gutter"] = geometry["col0"]
            y += band["h"] + 24
        group["h"] = y
        lanes[vpc["id"]] = geometry
        cursor += y + 36
    others = sorted((nodes[k] for k in visible if k not in placed and nodes[k]["kind"] not in {"vpc", "subnet"}),
                    key=lambda n: (KIND_ORDER.get(n["kind"], 9), n["name"], n["id"]))
    if others:
        width = max([s["w"] for s in canvas.shapes if s["role"] == "vpc"] + [5 * SLOT + 60])
        per_row = max(1, int((width - 60) // SLOT))
        rows = -(-len(others) // per_row)
        box = canvas.add("references", "1", 24, cursor, width, SUBNET_HEADER + rows * ROW + 8, "references",
                         "배치 미확인 / VPC 외 리소스 · 점선은 참조만 확인됨")
        for i, n in enumerate(others):
            row, column = divmod(i, per_row)
            resource(n["id"], box["id"], 30 + column * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + row * ROW + 24, "bottom")
    route(canvas, drawn_edges, vpc_of, placed, lanes, tg_order)


def route(canvas, drawn_edges, vpc_of, placed, lanes, tg_order):
    """Explicit orthogonal routes through channels, gutters and the lanes above icon rows."""
    incoming, outgoing, side = {}, {}, {}
    for e in drawn_edges:
        incoming.setdefault(e["target"], []).append(e)
        outgoing.setdefault(e["source"], []).append(e)
    position = lambda key: canvas.abs[placed[key]["id"]]
    order = lambda key: (position(key), key) if key in placed else ((0, 0), key)
    for e in sorted(drawn_edges, key=lambda e: e["id"]):
        entry = {"id": cell_id("edge/" + e["id"]), "relation_id": e["id"], "relation": e["relation"],
                 "source": cell_id(e["source"]), "target": cell_id(e["target"]), "evidence": e["evidence"],
                 "points": [], "constraints": "", "label": "", "label_at": 0.85,
                 "auxiliary": e["relation"] not in {"registered_target", "associated_load_balancer"}}
        geometry = lanes.get(vpc_of.get(e["source"]))
        both = e["source"] in placed and e["target"] in placed
        if both:
            peers = sorted(incoming[e["target"]], key=lambda x: (tg_order.get(x["source"], 99), order(x["source"]), x["id"]))
            entry_x = (peers.index(e) + 1) / (len(peers) + 1)
            sx, sy = position(e["source"])
            tx, ty = position(e["target"])
        if both and e["relation"] == "registered_target" and geometry and "channel" in geometry:
            k = tg_order.get(e["source"], 0)
            same_pair = sorted((x for x in peers if x["source"] == e["source"]), key=lambda x: (x["qualifiers"].get("port") or 0, x["id"]))
            j = same_pair.index(e)
            siblings = sorted(outgoing[e["source"]], key=lambda x: (order(x["target"]), x["id"]))
            exit_x = (siblings.index(e) + 1) / (len(siblings) + 1)
            gutter = placed[e["target"]].get("gutter") if vpc_of.get(e["target"]) == vpc_of.get(e["source"]) else None
            gutter = (gutter if gutter is not None else geometry["col0"]) + 6 * ((k + j) % 4)
            channel = geometry["channel"] + 6 * k
            lane = ty - 8 - 4 * ((k + j) % 4)
            entry["constraints"] = f"exitX={exit_x:.3f};exitY=1;entryX={entry_x:.3f};entryY=0;"
            entry["points"] = [(sx + exit_x * ICON, channel), (gutter, channel), (gutter, lane), (tx + entry_x * ICON, lane)]
            if len(same_pair) > 1 and e["qualifiers"].get("port") is not None:
                entry["label"], entry["label_at"] = f":{e['qualifiers']['port']}", 0.85 - 0.25 * j
        elif both and e["relation"] == "associated_load_balancer" and geometry and "lb_bottom" in geometry \
                and placed[e["target"]]["parent"] == placed[e["source"]]["parent"]:
            middle = (geometry["lb_bottom"] + geometry["tg_top"]) / 2
            entry["constraints"] = f"exitX=0.5;exitY=0;entryX={entry_x:.3f};entryY=1;"
            entry["points"] = [(sx + ICON / 2, middle), (tx + entry_x * ICON, middle)]
        elif both and entry["auxiliary"] and placed[e["target"]]["parent"] == "references" \
                and placed[e["source"]]["parent"] != "references" and placed[e["source"]].get("gutter") is not None:
            # Leave from the left side, rise to the lane above the source row, then use the gutter.
            i = side.setdefault(e["source"], sorted((x for x in outgoing[e["source"]] if x["relation"] in AUXILIARY),
                                                    key=lambda x: x["id"])).index(e)
            n = len(side[e["source"]])
            exit_y = (i + 1) / (n + 1)
            gutter = placed[e["source"]]["gutter"] + 20 - 3 * (i % 4)
            above = sy - 14 - 3 * (i % 3)
            lane = ty - 8 - 3 * (peers.index(e) % 5)
            entry["constraints"] = f"exitX=0;exitY={exit_y:.3f};entryX={entry_x:.3f};entryY=0;"
            entry["points"] = [(sx - 10 - 3 * i, sy + exit_y * ICON), (sx - 10 - 3 * i, above), (gutter, above), (gutter, lane), (tx + entry_x * ICON, lane)]
        else:
            entry["label"] = {"in_subnet": "서브넷 배치"}.get(e["relation"], "")
        canvas.edges.append(entry)
