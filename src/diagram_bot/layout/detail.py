"""Detail and focus views. AWS Architecture Icons nesting: AWS Cloud > Region > VPC > Availability Zone > subnet;
CloudFront at the edge, outside the Region. Inside a VPC, top to bottom: the attachment row, load balancers, target
groups, the edge channel, AZ columns of subnets by tier, then VPC resources whose subnet was not confirmed. Resources
outside every drawn VPC go to a reference box, on-premises targets to a data center box beside the AWS Cloud."""
from types import SimpleNamespace

from ..views import AUXILIARY, EDGE, GROUP_TARGETS, GROUPS, REGIONAL, REMOTE
from .base import AZ_HEADER, ICON, LANE_TOP, ROW, SIDE_SLOT, SLOT, SUBNET_HEADER, VPC_HEADER, cell_id, is_public, outside_vpc, tier_rank
from .edge_column import edge_column, edge_offset, waf_badge
from .routing import route
from .text import cloud_label, fit_label, group_title, icon_label, icon_variant

# The VPC's attachment row: edges to the internet, AWS services and other networks, and managed
# services whose network interfaces span several subnets (EKS control plane, VPC Lambda, ECS services
# whose tasks were not collected).
EDGE_KINDS = {"internet_gateway": 0, "vpn_gateway": 1, "vpc_peering": 2, "tgw_attachment": 3, "vpc_endpoint": 4,
              "eks_cluster": 5, "ecs_service": 6, "lambda_function": 7, "sagemaker_endpoint": 8}
KIND_ORDER = {"transit_gateway": -1, "load_balancer": 0, "target_group": 1, "ec2": 2, "registered_target": 3,
              "security_group": 4, "network_interface": 5, "volume": 6}
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


class Detail:
    """What one detail view has placed so far, and the routing facts each VPC leaves for route()."""

    def __init__(self, canvas, graph, selection, nodes, vpc_of, subnet_of):
        self.canvas, self.graph, self.nodes, self.vpc_of, self.subnet_of = canvas, graph, nodes, vpc_of, subnet_of
        self.visible, self.focus, self.drawn_edges = set(selection["nodes"]), selection["focus"], selection["edges"]
        self.lanes = {}  # per VPC routing geometry, absolute coordinates
        self.placed = {}
        self.suppressed = set()  # registrations drawn as one target group -> Auto Scaling group edge
        self.frames_at = {}  # Auto Scaling group -> (frame cell, leftmost AZ column)
        self.tg_order = {}
        self.subnet_at = {}  # subnet -> (cell, AZ column) for placement routes
        self.refs_votes = {}  # target outside every VPC -> gutter sides of the target groups feeding it
        # The graph's edges are scanned once per view, not once per VPC and group.
        self.by_relation = {}
        for e in graph["edges"]:
            self.by_relation.setdefault(e["relation"], []).append(e)
        self.peer_of = {e["source"]: e["target"] for e in graph["edges"] if e["relation"] == "peer_vpc"}
        self.asg_of = {e["target"]: e["source"] for e in graph["edges"] if e["relation"] in GROUPS.values()}
        self.group_members = {}
        for e in graph["edges"]:
            if e["relation"] in GROUPS.values():
                self.group_members.setdefault((e["source"], e["relation"]), []).append(e["target"])

    def resource(self, key, parent, x, y, side):
        n = self.nodes[key]
        shape = self.canvas.add(cell_id(key), parent, x, y, ICON, ICON, "resource", icon_label(n, side), resource=key, kind=n["kind"],
                                reference=n["reference_only"], label_side=side, emphasis=key == self.focus, tooltip=n["name"],
                                outside_vpc=outside_vpc(n), variant=icon_variant(n))
        self.placed[key] = shape
        waf_badge(self.canvas, shape, n)
        return shape

    def place_box(self, key, role, label, parent, x, y, items, min_width):
        """One row: routes enter from the lane above it, so a second row would put routes through labels."""
        box_width = max(min_width, len(items) * SLOT + 60)
        per_row = max(1, int((box_width - 60) // SLOT))
        votes = self.refs_votes
        right = [n for n in items if votes.get(n["id"]) and sum(votes[n["id"]]) * 2 > len(votes[n["id"]])]
        left = [n for n in items if n not in right]
        # Targets reached from the right margin sit at the right end, so their routes stay short.
        slots = [(n, i) for i, n in enumerate(left)] + [(n, per_row - len(right) + i) for i, n in enumerate(right)]
        ids = {n["id"] for n in items}
        lane_top = max(LANE_TOP, 12 + 4 * sum(1 for e in self.drawn_edges if e["target"] in ids or e["source"] in ids))
        box = self.canvas.add(key, parent, x, y, box_width, SUBNET_HEADER + ROW - LANE_TOP + lane_top + 8, role, label)
        for n, slot in slots:
            self.resource(n["id"], key, 30 + slot * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + lane_top, "bottom")["lane_top"] = lane_top
        return box


def detail(canvas, snapshot, graph, selection, nodes, vpc_of, subnet_of):
    d = Detail(canvas, graph, selection, nodes, vpc_of, subnet_of)
    vpcs = sorted((nodes[k] for k in d.visible if nodes[k]["kind"] == "vpc"), key=lambda n: (n["name"], n["id"]))
    meta = snapshot["metadata"]
    if meta.get("combined"):
        # One VPC (or resource) of a combined snapshot: its own account and region frame the drawing. The focused
        # resource decides when there is one (a TGW focus shows VPCs of several accounts), else the first VPC by name.
        subject = nodes.get(d.focus) or (vpcs[0] if vpcs else None)
        if subject:
            meta = {**meta, "combined": False, "account_id": subject["account"], "region": subject["region"]}
    distributions = sorted((nodes[k] for k in d.visible if nodes[k]["kind"] == "cloudfront_distribution"), key=lambda n: (n["name"], n["id"]))
    cloud = canvas.add("cloud", "1", 24, 112, 0, 0, "cloud", cloud_label(meta))
    region = canvas.add("region", "cloud", edge_offset(distributions, [e for e in d.drawn_edges if e["relation"] in EDGE]), 44, 0, 0, "region",
                        f"Region · {meta['region']}")
    cursor = 46
    for vpc in vpcs:
        cursor += vpc_block(d, snapshot, vpc, cursor) + 36
    # Auto Scaling groups and ECS services with placed tasks are drawn only as frames around their members.
    others = sorted((nodes[k] for k in d.visible if k not in d.placed and nodes[k]["kind"] not in {"vpc", "subnet", "cloudfront_distribution", *GROUPS}),
                    key=lambda n: (KIND_ORDER.get(n["kind"], 9), n["name"], n["id"]))
    width = max([sh["w"] for sh in canvas.shapes if sh["role"] == "vpc"] + [5 * SLOT + 60])
    inside = [n for n in others if not outside_vpc(n)]
    if inside:
        # Routes to the box come down the VPCs' gutters. Starting the box right of the left gutter, those routes enter
        # from its side below the caption; the two-line caption ends before the first gutter between AZ columns.
        inset = max([g["gutters"][0][1] - g["gutters"][0][0] for g in d.lanes.values() if len(g["gutters"]) > 1] + [0])
        cursor += d.place_box("references", "references", "VPC 밖 리전 리소스 · 배치 미확인\n참조만 확인 (점선 = 참조)",
                              "region", 20 + inset, cursor, inside, width)["h"] + 36
    region["w"] = max([sh["x"] + sh["w"] for sh in canvas.shapes if sh["parent"] == "region"] + [400]) + 20
    region["h"] = cursor
    cloud["w"], cloud["h"] = region["x"] + region["w"] + 20, region["y"] + region["h"] + 20
    d.placed.update(edge_column(canvas, nodes, cloud, distributions))
    remote = [n for n in others if outside_vpc(n)]
    if remote:
        # Beside the AWS Cloud: routes leave the VPC's right margin above the load balancer row.
        d.place_box("onprem", "onprem", "Corporate data center · 고객 게이트웨이·DX · AvailabilityZone=all 로 등록된 VPC 밖 대상", "1",
                    cloud["x"] + cloud["w"] + 48, cloud["y"], remote, 520)
    route(canvas, [e for e in d.drawn_edges if e["id"] not in d.suppressed], vpc_of, d.placed, d.lanes, d.tg_order, d.subnet_at, d.frames_at)


def vpc_block(d, snapshot, vpc, cursor):
    """One VPC at `cursor` in the Region: bands, AZ columns, group frames and the unplaced band; returns its height."""
    m = vpc_members(d, vpc)
    demand, placements = gutter_demand(d, vpc, m)
    size_columns(m, demand)
    group, y, geometry = draw_bands(d, vpc, m, cursor, placements)
    vid = group["id"]
    if not m.subnets:
        no_subnets_caption(d, snapshot, vpc, vid, m.gutters[0], y)
        y += 56
    metrics, arranged = subnet_rows(d, m)
    block_top, column_height = tier_tops(m, metrics)
    draw_az_columns(d, vpc, m, vid, y, geometry, metrics, arranged, block_top, column_height)
    draw_frames(d, m, vid)
    suppress_registrations(d, m)
    ox = d.canvas.abs[vid][0]
    geometry["gutters"].append((ox + m.width - m.gutters[m.last], ox + m.width))
    y += column_height + 24
    if m.unplaced:
        band = d.canvas.add(cell_id(vpc["id"] + "/unplaced"), vid, m.gutters[0], y, m.width - m.gutters[0] - m.gutters[m.last],
                            SUBNET_HEADER + ROW, "unplaced", "서브넷 미확인 · VPC 소속만 확인된 리소스")
        for i, n in enumerate(m.unplaced):
            d.resource(n["id"], band["id"], 20 + i * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + LANE_TOP, "bottom")["column"] = 0
        y += band["h"] + 24
    group["h"] = y
    d.lanes[vpc["id"]] = geometry
    return y


def balancer_order(d, members):
    """Load balancers, internet-facing first (left), and target groups under the load balancers they serve."""
    lbs = sorted((n for n in members if n["kind"] == "load_balancer"),
                 key=lambda n: (n["attributes"].get("Scheme") != "internet-facing", n["name"], n["id"]))
    tgs = [n for n in members if n["kind"] == "target_group"]
    lb_index = {n["id"]: i for i, n in enumerate(lbs)}
    associated = {}
    for e in d.by_relation.get("associated_load_balancer", []):
        if e["target"] in lb_index:
            associated.setdefault(e["source"], []).append(lb_index[e["target"]])
    tgs.sort(key=lambda n: (sum(associated.get(n["id"], [len(lbs)])) / len(associated.get(n["id"], [0])), n["name"], n["id"]))
    return lbs, tgs, lb_index


def vpc_members(d, vpc):
    """Where each of the VPC's resources goes: bands, subnets (children under the target groups registering them),
    group frames, the unplaced band, and the AZ column each sits in."""
    nodes, visible, subnet_of = d.nodes, d.visible, d.subnet_of
    members = sorted((nodes[k] for k in visible if k not in d.placed and (d.vpc_of.get(k) == vpc["id"] or d.peer_of.get(k) == vpc["id"])),
                     key=lambda n: (n["name"], n["id"]))
    lbs, tgs, lb_index = balancer_order(d, members)
    tg_index = {n["id"]: i for i, n in enumerate(tgs)}
    d.tg_order.update(tg_index)
    registered = {}
    for e in d.by_relation.get("registered_target", []):
        if e["source"] in tg_index:
            registered.setdefault(e["target"], []).append(tg_index[e["source"]])
    barycenter = lambda n: (sum(registered.get(n["id"], [len(tgs)])) / len(registered.get(n["id"], [0])), n["name"], n["id"])
    subnets = [n for n in members if n["kind"] == "subnet"]
    children = {s["id"]: sorted((nodes[k] for k in visible if subnet_of.get(k) == s["id"]), key=barycenter) for s in subnets}
    # Auto Scaling groups and ECS services are framed around their members placed in this VPC's subnets.
    group_inside = {n["id"]: sorted(t for t in d.group_members.get((n["id"], GROUPS[n["kind"]]), [])
                                    if t in visible and subnet_of.get(t) in children)
                    for n in members if n["kind"] in GROUPS}
    gateways = sorted((n for n in members if n["kind"] in EDGE_KINDS and not group_inside.get(n["id"])),
                      key=lambda n: (EDGE_KINDS[n["kind"]], n["name"], n["id"]))
    unplaced = sorted((n for n in members if n["kind"] not in {"subnet", "load_balancer", "target_group", *GROUPS,
                                                               "route_table", *EDGE_KINDS}
                       and subnet_of.get(n["id"]) not in children), key=barycenter)
    azs = sorted({s["attributes"].get("AvailabilityZone", "AZ 미확인") for s in subnets})
    column = {c["id"]: i for i, az in enumerate(azs) for s in subnets
              if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az for c in children[s["id"]]}
    column.update({n["id"]: 0 for n in unplaced})
    last = len(azs)  # gutter i is left of AZ column i; gutter `last` is the VPC's right margin
    # Target groups in the right half send outside-VPC targets down the right margin.
    side = {t["id"]: last if len(tgs) > 1 and tg_index[t["id"]] >= len(tgs) / 2 else 0 for t in tgs}
    # Auto Scaling groups are framed across AZ columns when their members share one tier row;
    # a target group attached to a framed group then gets one edge instead of one per instance.
    frames = {gid: inside for gid, inside in group_inside.items()
              if inside and len({tier_rank(nodes[subnet_of[m]]) for m in inside}) == 1}
    return SimpleNamespace(lbs=lbs, tgs=tgs, lb_index=lb_index, tg_index=tg_index, subnets=subnets, children=children, gateways=gateways,
                           unplaced=unplaced, azs=azs, last=last, column=column, side=side, frames=frames)


def gutter_demand(d, vpc, m):
    """The route groups each gutter holds, and the load balancer -> subnet placement routes. Gutters reserve room for
    both the registrations and the group edge; which one is drawn is decided once the frame is known to enclose only
    its own members."""
    demand = [set() for _ in range(m.last + 1)]
    placements = []
    for e in d.drawn_edges:
        if e["relation"] in GROUP_TARGETS and e["source"] in m.frames:
            demand[min(m.column[member] for member in m.frames[e["source"]])].add(e["target"])
        elif e["relation"] == "registered_target" and e["source"] in m.tg_index and d.vpc_of.get(e["target"]) in (None, vpc["id"]):
            g = m.column.get(e["target"], m.side[e["source"]])
            demand[g].add(e["source"])
            if e["target"] not in m.column:
                # The final side depends on where the target lands in the reference box.
                demand[0].add(e["source"])
                demand[m.last].add(e["source"])
                d.refs_votes.setdefault(e["target"], []).append(g == m.last and m.last > 0)
        elif e["relation"] in AUXILIARY | REGIONAL and e["relation"] not in REMOTE and d.vpc_of.get(e["source"]) == vpc["id"]:
            demand[m.column.get(e["source"], 0)].add(e["id"])
        elif e["relation"] == "in_subnet" and e["source"] in m.lb_index and e["target"] in m.children:
            placements.append(e["id"])
            demand[0].add(e["id"])
            demand[m.azs.index(d.nodes[e["target"]]["attributes"].get("AvailabilityZone", "AZ 미확인"))].add(e["id"])
    return demand, placements


def size_columns(m, demand):
    """Gutter widths, subnet slots per row, each AZ column's subnets (by tier) and width, and the VPC width."""
    m.gutters = [max(28, 16 + 8 * len(routes)) for routes in demand]
    m.per_row = {s["id"]: 2 if len(m.children[s["id"]]) <= 4 else 3 for s in m.subnets}
    m.az_subnets = {az: sorted((s for s in m.subnets if s["attributes"].get("AvailabilityZone", "AZ 미확인") == az),
                               key=lambda s: (tier_rank(s), s["name"], s["id"])) for az in m.azs}
    m.col_width = {az: max((m.per_row[s["id"]] * SLOT + 40 for s in m.az_subnets[az]), default=SLOT * 2 + 40) + 32 for az in m.azs}
    az_width = sum(m.gutters[:m.last]) + sum(m.col_width.values()) + 24 + m.gutters[m.last]
    band_width = max(len(m.lbs), len(m.tgs)) * SIDE_SLOT + 2 * max(m.gutters)
    bottom_width = len(m.unplaced) * SLOT + m.gutters[0] + m.gutters[m.last] + 40
    m.width = max(720, az_width, band_width, bottom_width)


def draw_bands(d, vpc, m, cursor, placements):
    """The VPC container with its attachment row (gateways, endpoints, EKS, Lambda), load balancers and target groups,
    then the channel rows below them. Returns (container, y below the channel, routing geometry)."""
    # The attachment row wraps instead of widening the VPC: a private VPC with a dozen interface
    # endpoints would otherwise be three times as wide as its AZ columns.
    per_band = max(1, int((m.width - 2 * max(m.gutters)) // SIDE_SLOT))
    gateway_rows = [m.gateways[i:i + per_band] for i in range(0, len(m.gateways), per_band)]
    vid = cell_id(vpc["id"])
    vpc_label = f"VPC  ·  {vpc['name']}  ·  {vpc['attributes'].get('CidrBlock', '')}"
    group = d.canvas.add(vid, "region", 20, cursor, m.width, 0, "vpc", fit_label(vpc_label, m.width - 60, 1), resource=vpc["id"],
                         emphasis=vpc["id"] == d.focus)
    y = VPC_HEADER + LANE_TOP + 6
    oy = d.canvas.abs[vid][1]
    geometry = {"last": m.last, "lb_mid": {}, "gutters": []}
    for band in (*gateway_rows, m.lbs, m.tgs):
        if not band:
            continue
        start = (m.width - len(band) * SIDE_SLOT) / 2 + 24
        for i, n in enumerate(band):
            d.resource(n["id"], vid, start + i * SIDE_SLOT, y, "right")["column"] = 0
            if n["kind"] == "load_balancer":
                geometry["lb_mid"][n["id"]] = oy + y + ICON + 8 + 6 * (i % 4)
        y += ICON + (34 if band is m.lbs or band is m.tgs else 40)
    geometry["channel"] = oy + y - 6
    geometry["placements"] = {key: oy + y - 6 + 7 * (len(m.tgs) + i) for i, key in enumerate(placements)}
    y += 7 * (len(m.tgs) + len(placements)) + 10
    return group, y, geometry


def no_subnets_caption(d, snapshot, vpc, vid, x, y):
    status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_subnets"), "unavailable")
    text = "이 VPC에서 조회된 서브넷이 없습니다." if status == "complete" else "서브넷 조회가 완료되지 않아 배치를 확인할 수 없습니다."
    if d.focus and any(n["kind"] == "subnet" and d.vpc_of.get(k) == vpc["id"] for k, n in d.nodes.items()):
        # A focus view draws only the subnets its resources sit in; the VPC's other subnets are hidden, not missing.
        text = "선택 리소스와 직접 관계된 서브넷 없음 · 서브넷은 상세 구성에서 확인"
    d.canvas.add(cell_id(vpc["id"] + "/empty"), vid, x, y, 420, 40, "caption", text)


def subnet_rows(d, m):
    """Each subnet's children in slots, and its (lane space above a row, row pitch, height): each route entering or
    leaving a row needs its own lane above that row."""
    metrics, arranged = {}, {}
    for sub in m.subnets:
        arranged[sub["id"]], rows = arrange(m.children[sub["id"]], m.per_row[sub["id"]], d.asg_of, d.nodes)
        busiest = max((sum(1 for e in d.drawn_edges for c, row, _ in arranged[sub["id"]]
                           if row == r and c["id"] in (e["source"], e["target"]) and e["relation"] != "in_subnet")
                       for r in range(rows)), default=0)
        lane_top = max(LANE_TOP, 12 + 4 * busiest)
        pitch = ROW - LANE_TOP + lane_top
        metrics[sub["id"]] = (lane_top, pitch, SUBNET_HEADER + rows * pitch + 8 if rows else SUBNET_HEADER + 56)
    return metrics, arranged


def tier_tops(m, metrics):
    """Same tier sits at the same height in every AZ column, as in AWS reference diagrams. Returns each tier's top and
    the column height."""
    ranks = sorted({tier_rank(sub) for sub in m.subnets})
    block = {r: max(sum(metrics[sub["id"]][2] + 16 for sub in m.az_subnets[az] if tier_rank(sub) == r) for az in m.azs) for r in ranks}
    block_top, top = {}, AZ_HEADER + 10
    for r in ranks:
        block_top[r], top = top, top + block[r]
    return block_top, top


def draw_az_columns(d, vpc, m, vid, y, geometry, metrics, arranged, block_top, height):
    """AZ columns between their gutters, each subnet at its tier's height."""
    ox = d.canvas.abs[vid][0]
    x = 0
    for index, az in enumerate(m.azs):
        geometry["gutters"].append((ox + x, ox + x + m.gutters[index]))
        x += m.gutters[index]
        az_id = cell_id(vpc["id"] + "/az/" + az)
        d.canvas.add(az_id, vid, x, y, m.col_width[az], height, "az", az)
        offsets = dict(block_top)
        for sub in m.az_subnets[az]:
            sy = offsets[tier_rank(sub)]
            offsets[tier_rank(sub)] += metrics[sub["id"]][2] + 16
            draw_subnet(d, m, sub, az_id, index, sy, metrics[sub["id"]], arranged[sub["id"]])
        x += m.col_width[az]


def draw_subnet(d, m, sub, az_id, index, sy, metrics, arranged):
    lane_top, pitch, sh = metrics
    sid = cell_id(sub["id"])
    d.subnet_at[sub["id"]] = (sid, index)
    per_row = m.per_row[sub["id"]]
    d.canvas.add(sid, az_id, 16, sy, per_row * SLOT + 40, sh, "subnet",
                 fit_label(sub["name"], per_row * SLOT - 20, 1, 11) + "\n" +
                 fit_label(" · ".join(filter(None, (sub["attributes"].get("CidrBlock", ""), ROUTES.get(sub.get("route_class"))))),
                           per_row * SLOT - 20, 1, 11),
                 resource=sub["id"], emphasis=sub["id"] == d.focus, public=is_public(sub),
                 route_class=sub.get("route_class"), tier=tier_rank(sub))
    for n, row, slot in arranged:
        shape = d.resource(n["id"], sid, 20 + slot * SLOT + (SLOT - ICON) / 2, SUBNET_HEADER + row * pitch + lane_top, "bottom")
        shape.update(column=index, lane_top=lane_top)
    if not m.children[sub["id"]]:
        d.canvas.add(cell_id(sub["id"] + "/empty"), sid, 20, SUBNET_HEADER + 14, per_row * SLOT, 30, "caption",
                     "선택 리소스와 직접 관계된 인스턴스 없음" if d.focus else "조회된 인스턴스 없음")


def draw_frames(d, m, vid):
    """Auto Scaling group and ECS service frames around their members (icons and labels) across AZ columns."""
    canvas, placed = d.canvas, d.placed
    ox, oy = canvas.abs[vid]
    for asg_id, inside in m.frames.items():
        boxes = [(canvas.abs[placed[k]["id"]], canvas.label_box(placed[k])) for k in inside]
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
                           fit_label(group_title(d.graph, d.nodes, asg_id), right - left - 40, 1, 11), resource=asg_id,
                           emphasis=asg_id == d.focus, frame="ecs" if d.nodes[asg_id]["kind"] == "ecs_service" else "asg")
        d.frames_at[asg_id] = (frame["id"], min(m.column[k] for k in inside))


def suppress_registrations(d, m):
    """A target group attached to a framed group gets one edge from the frame instead of one per instance; one attached
    to a group without a frame keeps its registrations instead."""
    for e in d.drawn_edges:
        # DescribeAutoScalingGroups.TargetGroupARNs: source is the group, target the target group.
        if e["relation"] in GROUP_TARGETS and e["target"] in m.tg_index:
            if e["source"] in d.frames_at:
                d.suppressed.update(r["id"] for r in d.drawn_edges if r["relation"] == "registered_target"
                                    and r["source"] == e["target"] and r["target"] in m.frames[e["source"]])
            else:
                d.suppressed.add(e["id"])
