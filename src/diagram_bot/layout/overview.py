"""Overview: one summary card per VPC (services per tier, counted), the network hubs and on-premises sites they
connect to, and CloudFront distributions in front of their load balancer origins."""
from ..views import HUB_ORDER, SITE_KINDS, UNDRAWN_KINDS, edge_links, overview_links
from .base import ICON, LINE, SIDE_SLOT, VPC_HEADER, cell_id, outside_vpc, tier_rank
from .edge_column import EDGE_COLUMN, align_edge, edge_column, edge_offset, edge_path
from .routing import edge_between
from .text import ENGINES, GPU, accelerated_icon, cloud_label, fit_label, icon_label, icon_variant, instance_family, label_lines

# Overview cards show each VPC at a glance: its services per tier (public, private, data), counted.
TIER_NAMES = ("퍼블릭", "프라이빗", "격리")  # the subnet legend: internet route, NAT/TGW/VGW route, none
GLANCE = {"igw": "IGW", "nat": "NAT", "alb": "ALB", "nlb": "NLB", "gwlb": "GWLB", "ec2": "EC2", "eks": "EKS", "ecs": "ECS 서비스",
          "lambda": "Lambda", "sagemaker": "SageMaker", "hyperpod": "HyperPod", "endpoint": "엔드포인트", "rds": "RDS", "aurora": "Aurora",
          "documentdb": "DocumentDB", "neptune": "Neptune", "cache": "ElastiCache", "msk": "MSK", "opensearch": "OpenSearch", "efs": "EFS",
          "fsx": "FSx", "redshift": "Redshift"}
GLANCE_KINDS = {"internet_gateway": "igw", "nat_gateway": "nat", "ec2": "ec2", "eks_cluster": "eks", "ecs_service": "ecs",
                "lambda_function": "lambda", "sagemaker_endpoint": "sagemaker", "sagemaker_cluster": "hyperpod", "vpc_endpoint": "endpoint",
                "cache_node": "cache", "msk_broker": "msk", "search_node": "opensearch", "efs_mount_target": "efs",
                "fsx_file_system": "fsx", "redshift_cluster": "redshift"}
SLOT_W, GLANCE_ICON, GLANCE_LEFT = 80, 32, 70


def glance_key(n):
    if n["kind"] == "load_balancer":
        return {"network": "nlb", "gateway": "gwlb"}.get(n["attributes"].get("Type"), "alb")
    if n["kind"] == "db_instance":
        return {"aurora": "aurora", "documentdb": "documentdb", "neptune": "neptune"}.get(icon_variant(n), "rds")
    return GLANCE_KINDS.get(n["kind"])


def glance_label(key, members, standby, grouped):
    """'EC2 × 6' over one detail line (scaling groups, scheme, Multi-AZ, endpoint types…)."""
    a = lambda n: n["attributes"]
    distinct = {"efs": lambda n: a(n).get("FileSystemId"), "redshift": lambda n: n["resource_id"].removesuffix("#secondary"),
                "opensearch": lambda n: a(n).get("DomainName"), "msk": lambda n: n["resource_id"].split("#")[0]}.get(key, lambda n: n["id"])
    count = len({distinct(n) for n in members})
    detail = None
    if key == "ec2":
        groups = len({grouped[n["id"]] for n in members if n["id"] in grouped})
        fast = sum(bool(GPU.match(instance_family(n)) or accelerated_icon(n)) for n in members)
        detail = " · ".join(filter(None, (f"ASG {groups}개" if groups else None, f"가속 {fast}" if fast else None)))
    elif key in ("alb", "nlb", "gwlb"):
        internal = sum(a(n).get("Scheme") == "internal" for n in members)
        detail = "내부" if internal == len(members) else "인터넷" if not internal else f"인터넷 {len(members) - internal} · 내부 {internal}"
        detail += " · WAF" if any(n.get("waf") for n in members) else ""
    elif key in ("rds", "aurora", "documentdb", "neptune"):
        detail = "Multi-AZ" if standby or len(members) > 1 else "Single-AZ"
    elif key == "endpoint":
        types = [a(n).get("VpcEndpointType") for n in members]
        detail = " · ".join(f"{t} {types.count(t)}" for t in ("Interface", "Gateway") if t in types)
    elif key == "cache":
        detail = ENGINES.get(a(members[0]).get("Engine"), a(members[0]).get("Engine"))
    elif key == "fsx":
        detail = ", ".join(sorted({(a(n).get("LustreConfiguration") or {}).get("DeploymentType") or a(n).get("FileSystemType", "") for n in members}))
    elif key == "sagemaker":
        detail = a(members[0]).get("InstanceType")
    elif key == "ecs":
        detail = f"태스크 {sum(int(a(n).get('runningCount') or 0) for n in members)}"
    return GLANCE[key] + ("" if key == "igw" else f" × {count}") + (f"\n{detail}" if detail else "")


def vpc_glance(vpc, nodes, graph, vpc_of, subnet_of):
    """Rows of (members, key, label) per tier for one VPC card."""
    inside = [n for k, n in nodes.items() if vpc_of.get(k) == vpc["id"] and not n["reference_only"]]
    grouped = {e["target"]: e["source"] for e in graph["edges"] if e["relation"] == "asg_member"}
    standby = any(n["kind"] == "db_standby" for n in inside)

    def tier(n):
        # Public: an internet route; data: no internet route at all (isolated) or a data-tier subnet; else private.
        subnet = nodes.get(subnet_of.get(n["id"]))
        if n["kind"] == "internet_gateway" or (subnet and tier_rank(subnet) == 0):
            return 0
        return 2 if subnet and (subnet.get("route_class") == "isolated" or tier_rank(subnet) == 3) else 1
    groups = {}
    for n in sorted(inside, key=lambda n: n["id"]):
        key = glance_key(n)
        if key:
            groups.setdefault((tier(n), key), []).append(n)
    order = list(GLANCE)
    rows = [[] for _ in TIER_NAMES]
    for (t, key), members in sorted(groups.items(), key=lambda item: (item[0][0], order.index(item[0][1]))):
        rows[t].append((members, key, glance_label(key, members, standby, grouped)))
    return rows


def glance_lines(items):
    """Height of each line of three icons: the icon plus its tallest wrapped label."""
    return [GLANCE_ICON + 18 + LINE * max(label_lines(label, SLOT_W - 2) for _, _, label in items[i:i + 3])
            for i in range(0, len(items), 3)]


def glance_height(rows):
    return sum(sum(glance_lines(items)) for items in rows)


def draw_glance(canvas, card, rows, top):
    """Tier captions and counted service icons inside a VPC card; an icon opens its first resource in the inspector."""
    y = top
    for t, items in enumerate(rows):
        if not items:
            continue
        canvas.add(cell_id(card["resource"] + f"/tier/{t}"), card["id"], 12, y + 14, GLANCE_LEFT - 16, 20, "caption", TIER_NAMES[t])
        lines = glance_lines(items)
        for i, (members, key, label) in enumerate(items):
            first = members[0]
            canvas.add(cell_id(card["resource"] + f"/glance/{t}/{key}"), card["id"],
                       GLANCE_LEFT + (i % 3) * SLOT_W + (SLOT_W - GLANCE_ICON) / 2, y + sum(lines[:i // 3]) + 6, GLANCE_ICON, GLANCE_ICON,
                       "resource", label, resource=first["id"], kind=first["kind"], label_side="bottom", label_width=SLOT_W - 2,
                       font_size=10, tooltip=f"{label.splitlines()[0]} · 선택하면 {first['name']} 속성", variant=None if key == "endpoint" else icon_variant(first))
        y += sum(lines)


def overview(canvas, snapshot, graph, nodes, vpc_of, subnet_of):
    vpcs = sorted((n for n in nodes.values() if n["kind"] == "vpc"), key=lambda n: (n["name"], n["id"]))
    links = overview_links(graph, nodes)
    to_hub = [(e, a, b) for e, a, b in links if nodes[a]["kind"] == "vpc"]
    to_site = [(e, a, b) for e, a, b in links if nodes[b]["kind"] in SITE_KINDS]
    to_peer = [(e, a, b) for e, a, b in links if (e, a, b) not in to_hub and (e, a, b) not in to_site]
    fronted = edge_links(graph, nodes, vpc_of)
    distributions = sorted((n for n in nodes.values() if n["kind"] == "cloudfront_distribution" and not n["reference_only"]),
                           key=lambda n: (n["name"], n["id"]))
    # Connected VPCs come first so their cards sit in the rows right above the network hubs; a combined overview
    # keeps each account's VPCs together.
    connected = {a for _, a, _ in to_hub}
    combined = bool(snapshot["metadata"].get("combined"))
    vpcs.sort(key=lambda n: (n["account"] if combined else "", n["id"] not in connected))
    columns = 3
    card_w, gap = 320, 24
    width = 32 + columns * (card_w + gap)
    meta = snapshot["metadata"]
    cloud = canvas.add("cloud", "1", 24, 112, width + 40, 0, "cloud", cloud_label(meta))
    dx = any(e["relation"] in ("dx_virtual_interface", "dx_gateway_association") for e, _, _ in links)
    drawn = ["TGW·피어링·VPN" if links else "", "Direct Connect" if dx else "", "CloudFront 오리진" if fronted else ""]
    group = canvas.add("region", "cloud", edge_offset(distributions, fronted), 44, width, 0, "region",
                       f"Region · {meta['region']} · VPC {len(vpcs)}개 · 요약 카드를 선택하면 상세 구성으로 이동"
                       + (f" · 연결선은 {'·'.join(filter(None, drawn))} 설정" if any(drawn) else ""))
    cards, glances, heads = vpc_cards(snapshot, graph, vpcs, nodes, vpc_of, subnet_of, combined)
    cards += unplaced_cards(nodes, vpc_of)
    if not vpcs:
        vpc_status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_vpcs"), "unavailable")
        message = "조회된 VPC가 없습니다." if vpc_status == "complete" else "VPC 조회가 완료되지 않았습니다.\n빈 환경이 아니라 권한·조회 실패일 수 있습니다."
        cards.insert(0, (message, None, True))
    card_h = max([112] + [36 + 15 * (label.count("\n") + 1) for label, _, _ in cards]
                 + [heads[r] + glance_height(glances[r]) + 6 for r in glances])
    # A combined overview without VPCs has no card rows for network(): the plain grid shows its message and cards.
    if not to_hub and not combined or not vpcs:
        for i, (label, resource, empty) in enumerate(cards):
            canvas.add(cell_id(resource or f"summary/{i}"), group["id"], 24 + (i % columns) * (card_w + gap),
                       VPC_HEADER + 18 + (i // columns) * (card_h + gap), card_w, card_h, "summary", label,
                       resource=resource, empty=empty)
        group["h"] = VPC_HEADER + 18 + -(-len(cards) // columns) * (card_h + gap)
        cloud["h"] = group["y"] + group["h"] + 20
    else:
        account_of = {v["id"]: v["account"] for v in vpcs} if combined else None
        network(canvas, nodes, cloud, group, cards, card_w, card_h, columns, len(vpcs) if combined else len(connected), to_hub, to_site,
                account_of, to_peer)
    for shape in [s for s in canvas.shapes if s["role"] == "summary" and s.get("resource") in glances]:
        draw_glance(canvas, shape, glances[shape["resource"]], heads[shape["resource"]])
    cloud["w"] = group["x"] + group["w"] + 20
    overview_edge(canvas, nodes, cloud, distributions, fronted, glances)


def vpc_cards(snapshot, graph, vpcs, nodes, vpc_of, subnet_of, combined):
    """One card per VPC: (label, VPC, empty), plus its glance rows and the height of its label above them."""
    members = {}
    for key, vpc in vpc_of.items():
        members.setdefault(vpc, []).append(nodes[key])
    cards, glances, heads = [], {}, {}
    for vpc in vpcs:
        inside = members.get(vpc["id"], [])
        azs = sorted({n["attributes"].get("AvailabilityZone") for n in inside if n["kind"] == "subnet"} - {None})
        subnets = sum(n["kind"] == "subnet" for n in inside)
        where = (f"\n계정 {vpc['account']}" + (f" · {vpc['region']}" if len(snapshot["metadata"]["regions"]) > 1 else "")) if combined else ""
        # The Transit Gateway route table its attachment is associated with: the VPC's routing domain.
        domains = sorted({n["tgw_route_table"] for n in inside if n["kind"] == "tgw_attachment" and n.get("tgw_route_table")})
        label = (f"VPC · {fit_label(vpc['name'], 250, 1)}{where}\n{vpc['attributes'].get('CidrBlock', '')} · AZ {len(azs)}개 · 서브넷 {subnets}개"
                 + (f"\n{fit_label('TGW 라우팅 테이블 · ' + ', '.join(domains), 280, 1)}" if domains else "")
                 + ("" if inside else "\n조회된 리소스 없음"))
        glances[vpc["id"]] = vpc_glance(vpc, nodes, graph, vpc_of, subnet_of)
        heads[vpc["id"]] = 22 + 15 * (label.count("\n") + 1)
        cards.append((label, vpc["id"], len(inside) == 0))
    return cards, glances, heads


def unplaced_cards(nodes, vpc_of):
    """Cards counting what no VPC card shows: regional services outside every VPC, and collected resources whose VPC is unknown."""
    cards = []
    # Collected security groups are rule sets, not placed resources; referenced ones still count.
    # Lambda functions without VpcConfig run outside every VPC; their placement is known, not missing.
    regional = [n for n in nodes.values() if n["kind"] in ("lambda_function", "sagemaker_endpoint") and n["id"] not in vpc_of]
    # Only collected resources whose VPC is unknown; references (SG/ENI/EBS IDs seen in other responses) are not placed resources.
    # Logical groups (DB cluster, replication group, file system…) are drawn through their members, and on-premises
    # IP targets are outside every VPC by design: neither is an unplaced resource.
    outside = [n for n in nodes.values() if n["kind"] not in ("vpc", "lambda_function", "sagemaker_endpoint", "security_group",
                                                               "transit_gateway", "customer_gateway", "vpn_gateway", "vpc_peering",
                                                               "cloudfront_distribution", "dx_gateway", *UNDRAWN_KINDS)
               and n["id"] not in vpc_of and not n["reference_only"] and not outside_vpc(n)]
    if regional:
        kinds = " · ".join(f"{label} {count}개" for label, kind in (("Lambda", "lambda_function"), ("SageMaker 엔드포인트", "sagemaker_endpoint"))
                           if (count := sum(n["kind"] == kind for n in regional)))
        cards.append((f"VPC 미연결 리전 서비스\n{kinds}\n네트워크 구성도에는 그리지 않음\n리소스 목록·CSV에서 확인", None, False))
    if outside:
        kinds = sorted({n["kind"] for n in outside})
        cards.append((f"VPC 외 · 배치 미확인\n수집 리소스 {len(outside)}개\n{', '.join(kinds[:3])}{' 등' if len(kinds) > 3 else ''}\n상세 보기·리소스 목록에서 확인", None, False))
    return cards



def overview_edge(canvas, nodes, cloud, distributions, fronted, glances):
    """CloudFront -> VPC card of its load balancer origin, entering the card's top above the load balancer's glance icon."""
    shapes = edge_column(canvas, nodes, cloud, distributions)
    cards = {s["resource"]: s for s in canvas.shapes if s["role"] == "summary" and s.get("resource")}
    routes = sorted((link for link in fronted if link[2] in cards),
                    key=lambda link: ([n["id"] for n in distributions].index(link[1]), link[0]["id"]))
    entering, rows, lanes = {}, {}, {}
    for link in routes:
        entering.setdefault(link[2], []).append(link)
    plan = []
    for e, source, vpc, balancer in routes:
        cx, cy = canvas.abs[cards[vpc]["id"]]
        # Above the load balancer's glance icon (its column in the card), else the card's middle.
        slot = next((i for row in glances.get(vpc, []) for i, (members, _, _) in enumerate(row) if any(m["id"] == balancer for m in members)), None)
        here = entering[vpc].index((e, source, vpc, balancer))
        ex = cx + (GLANCE_LEFT + (slot % 3) * SLOT_W + SLOT_W / 2 if slot is not None else cards[vpc]["w"] / 2) + 6 * here
        lane = cy - 6 - 4 * (rows.setdefault(cy, [0])[0] % 3)
        rows[cy][0] += 1
        lanes.setdefault(source, []).append(lane)
        plan.append((e, source, vpc, cx, cy, ex, lane))
    exits = align_edge(canvas, shapes, lanes)
    corridor = canvas.abs["cloud"][0] + 20 + EDGE_COLUMN + 12
    for k, (e, source, vpc, cx, cy, ex, lane) in enumerate(plan):
        sx, sy = canvas.abs[shapes[source]["id"]]
        exit_y = exits[source][lanes[source].index(lane)]
        path = edge_path(sx + ICON, sy + ICON * exit_y, corridor + 6 * k, lane, ex, cy)
        canvas.edges.append(edge_between(e, source, vpc, path, f"exitX=1;exitY={exit_y:.3f};entryX={(ex - cx) / cards[vpc]['w']:.3f};entryY=0;"))


def network(canvas, nodes, cloud, group, cards, card_w, card_h, columns, top_count, to_hub, to_site, account_of=None, to_peer=None):
    """Overview with connections: connected VPC cards in rows, a band of network hubs (Transit Gateway,
    VPN gateway, peering) below them, the customer gateways in a data center box right of the AWS Cloud,
    and the remaining cards under the hubs.

    Every route has its own lane: a card's routes leave from separate points on its bottom edge, routes from
    upper rows drop through the gap right of their card, and each route has its own horizontal lane above the
    hubs, so two routes share a segment only where they meet at the same shape."""
    rest = cards[top_count:]
    blocks, rows, row_block = card_rows(cards[:top_count], columns, account_of)
    head = 34 if account_of else 0
    to_peer = to_peer or []
    hubs = sorted({b for _, _, b in to_hub} | {a for _, a, _ in to_site} | {k for _, a, b in to_peer for k in (a, b)},
                  key=lambda k: (HUB_ORDER.get(nodes[k]["kind"], 9), nodes[k]["name"], k))
    sites = sorted({b for _, _, b in to_site}, key=lambda k: (nodes[k]["name"], k))
    hub_index = {k: i for i, k in enumerate(hubs)}
    by_card = {}
    for link in sorted(to_hub, key=lambda link: (hub_index[link[2]], link[0]["id"])):
        by_card.setdefault(link[1], []).append(link)
    gaps, xs, ys = card_grid(rows, row_block, by_card, columns, card_w, card_h, head)
    origin = canvas.abs[group["id"]][0], canvas.abs[group["id"]][1]
    draw_cards(canvas, group, blocks, rows, row_block, xs, ys, card_w, card_h, head)
    cards_bottom = ys[-1] + card_h + (10 if account_of else 0)
    lane_top = cards_bottom + 14
    hub_y = lane_top + 6 * len(to_hub) + 18
    grid_width = xs[-1] + card_w + gaps[-1]
    band = len(hubs) * SIDE_SLOT
    start = 24 + max(0, (grid_width - 24 - band) / 2)
    hub_shapes = {}
    for i, key in enumerate(hubs):
        hub_shapes[key] = canvas.add(cell_id(key), group["id"], start + i * SIDE_SLOT, hub_y, ICON, ICON, "resource",
                                     icon_label(nodes[key], "right"), resource=key, kind=nodes[key]["kind"],
                                     reference=nodes[key]["reference_only"], label_side="right", tooltip=nodes[key]["name"],
                                     variant=icon_variant(nodes[key]))
    entry_x, exit_x = hub_ports(to_hub, to_peer, to_site, hub_index)
    card_routes(canvas, by_card, rows, xs, ys, card_w, card_h, columns, origin, lane_top, hub_shapes, entry_x)
    peer_lanes = hub_y + ICON + 16
    peer_routes(canvas, to_peer, hub_shapes, hub_index, exit_x, origin[1] + peer_lanes)
    site_lanes = peer_lanes + (6 * len(to_peer) + 10 if to_peer else 0)
    next_y = site_lanes + 6 * len(to_site) + (24 if to_site or to_peer else 0)
    for i, (label, resource, empty) in enumerate(rest):
        canvas.add(cell_id(resource or f"summary/{i}"), group["id"], 24 + (i % columns) * (card_w + 24),
                   next_y + (i // columns) * (card_h + 24), card_w, card_h, "summary", label, resource=resource, empty=empty)
    width = max(grid_width, start + band + 24, 32 + columns * (card_w + 24) if rest else 0)
    group["w"] = width
    group["h"] = next_y + (-(-len(rest) // columns) * (card_h + 24) if rest else 0) + 6
    cloud["w"] = group["x"] + width + 20
    cloud["h"] = group["y"] + group["h"] + 20
    if to_site:
        site_box(canvas, nodes, cloud, sites, to_site, hub_shapes, hub_index, exit_x, origin[1] + site_lanes)


def card_rows(top, columns, account_of):
    """Rows of connected VPC cards; a combined overview frames each account's cards (rows never mix accounts).
    Returns the account blocks, the rows and each row's block."""
    blocks = []
    for card in top:
        account = account_of.get(card[1]) if account_of else None
        if not blocks or blocks[-1][0] != account:
            blocks.append((account, []))
        blocks[-1][1].append(card)
    rows, row_block = [], []
    for b, (_, members) in enumerate(blocks):
        for i in range(0, len(members), columns):
            rows.append(members[i:i + columns])
            row_block.append(b)
    return blocks, rows, row_block


def card_grid(rows, row_block, by_card, columns, card_w, card_h, head):
    """Card x per column and y per row. Routes from upper rows drop through the gap right of their card; widen gaps
    and row gutters to fit them. Returns (gaps, xs, ys)."""
    gap_use = [sum(len(by_card.get(resource, [])) for row in rows[:-1] for c, (_, resource, _) in enumerate(row) if c == col)
               for col in range(columns)]
    gaps = [24 + 6 * n for n in gap_use]
    gutters = [24 + 6 * sum(len(by_card.get(resource, [])) for _, resource, _ in row) for row in rows[:-1]] + [0]
    xs = [24 + sum(card_w + gaps[c] for c in range(col)) for col in range(columns)]
    ys, y = [], VPC_HEADER + 18
    for r in range(len(rows)):
        y += head if r == 0 or row_block[r] != row_block[r - 1] else 0
        ys.append(y)
        y += card_h + gutters[r]
    return gaps, xs, ys


def draw_cards(canvas, group, blocks, rows, row_block, xs, ys, card_w, card_h, head):
    """Account frames (combined overview), then the connected VPC cards."""
    for b, (account, _) in enumerate(blocks):
        if account is None:
            continue
        mine = [r for r in range(len(rows)) if row_block[r] == b]
        right = max(xs[len(rows[r]) - 1] + card_w for r in mine) + 4  # routes drop at card edge + 8: outside the frame
        canvas.add(cell_id("account/" + account), group["id"], xs[0] - 12, ys[mine[0]] - head + 4, right - xs[0] + 12,
                   ys[mine[-1]] + card_h + 10 - (ys[mine[0]] - head + 4), "account", f"AWS 계정 · {account}")
    for r, row in enumerate(rows):
        for c, (label, resource, empty) in enumerate(row):
            canvas.add(cell_id(resource), group["id"], xs[c], ys[r], card_w, card_h, "summary", label, resource=resource, empty=empty)


def hub_ports(to_hub, to_peer, to_site, hub_index):
    """Entry fractions on each hub's top (one per VPC route) and exit fractions on its bottom: links to other hubs
    (ordered by the other hub's position), then links to on-premises sites, each from its own point."""
    entering = {}
    for link in sorted(to_hub, key=lambda link: (hub_index[link[2]], link[0]["id"])):
        entering.setdefault(link[2], []).append(link)
    entry_x = {link[0]["id"]: (i + 1) / (len(group_) + 1) for group_ in entering.values() for i, link in enumerate(group_)}
    leaving = {}
    for link in sorted(to_peer, key=lambda link: (hub_index[link[2]], link[0]["id"])):
        leaving.setdefault(link[1], []).append((link, "from"))
    for link in sorted(to_peer, key=lambda link: (hub_index[link[1]], link[0]["id"])):
        leaving.setdefault(link[2], []).append((link, "to"))
    for hub in leaving:
        leaving[hub].sort(key=lambda item: hub_index[item[0][2] if item[1] == "from" else item[0][1]])
    for link in sorted(to_site, key=lambda link: (hub_index[link[1]], link[0]["id"])):
        leaving.setdefault(link[1], []).append((link, "from"))
    exit_x = {(link[0]["id"], end): (i + 1) / (len(group_) + 1) for group_ in leaving.values() for i, (link, end) in enumerate(group_)}
    return entry_x, exit_x


def card_routes(canvas, by_card, rows, xs, ys, card_w, card_h, columns, origin, lane_top, hub_shapes, entry_x):
    """VPC card -> hub: from the card's bottom edge (through the gap right of the card from upper rows) to a lane of
    its own above the hub band, then down into the hub."""
    ox, oy = origin
    row_of = {resource: (r, c) for r, row in enumerate(rows) for c, (_, resource, _) in enumerate(row)}
    lane = 0
    gap_slot = [0] * columns
    row_slot = [0] * len(rows)
    for resource, card_links in sorted(by_card.items(), key=lambda item: row_of[item[0]]):
        r, c = row_of[resource]
        for i, (e, vpc, hub) in enumerate(card_links):
            fx = (i + 1) / (len(card_links) + 1)
            sx, sy = ox + xs[c] + card_w * fx, oy + ys[r] + card_h
            hx, hy = canvas.abs[hub_shapes[hub]["id"]]
            tx = hx + ICON * entry_x[e["id"]]
            ly = oy + lane_top + 6 * lane
            lane += 1
            if r < len(rows) - 1:
                gy = sy + 12 + 6 * row_slot[r]
                gx = ox + xs[c] + card_w + 8 + 6 * gap_slot[c]
                row_slot[r] += 1
                gap_slot[c] += 1
                path = [(sx, sy), (sx, gy), (gx, gy), (gx, ly), (tx, ly), (tx, hy)]
            else:
                path = [(sx, sy), (sx, ly), (tx, ly), (tx, hy)]
            canvas.edges.append(edge_between(e, vpc, hub, path, f"exitX={fx:.3f};exitY=1;entryX={entry_x[e['id']]:.3f};entryY=0;"))


def peer_routes(canvas, to_peer, hub_shapes, hub_index, exit_x, lanes_y):
    """Hub to hub (Direct Connect gateway association): down from both hubs to a lane of its own under the hub band."""
    for k, (e, a, b) in enumerate(sorted(to_peer, key=lambda link: (hub_index[link[1]], hub_index[link[2]], link[0]["id"]))):
        (ax, ay), (bx, _) = canvas.abs[hub_shapes[a]["id"]], canvas.abs[hub_shapes[b]["id"]]
        fa, fb = exit_x[(e["id"], "from")], exit_x[(e["id"], "to")]
        ly = lanes_y + 6 * k
        path = [(ax + ICON * fa, ay + ICON), (ax + ICON * fa, ly), (bx + ICON * fb, ly), (bx + ICON * fb, ay + ICON)]
        canvas.edges.append(edge_between(e, a, b, path, f"exitX={fa:.3f};exitY=1;entryX={fb:.3f};entryY=1;"))


def site_box(canvas, nodes, cloud, sites, to_site, hub_shapes, hub_index, exit_x, lanes_y):
    """Customer gateways and Direct Connect connections stacked in a data center box right of the AWS Cloud, each
    entered from the left."""
    corridor = cloud["x"] + cloud["w"] + 12
    box_x = corridor + 6 * len(to_site) + 24
    dx = any(nodes[k]["kind"] == "dx_connection" for k in sites)
    box = canvas.add("onprem", "1", box_x, cloud["y"], SIDE_SLOT + 40, 44 + len(sites) * (ICON + 40), "onprem",
                     "Corporate data center · 고객 게이트웨이" + ("·DX" if dx else ""))
    site_shapes = {key: canvas.add(cell_id(key), box["id"], 20, 44 + i * (ICON + 40), ICON, ICON, "resource",
                                   icon_label(nodes[key], "right"), resource=key, kind=nodes[key]["kind"],
                                   reference=nodes[key]["reference_only"], label_side="right", tooltip=nodes[key]["name"],
                                   outside_vpc=True, variant=icon_variant(nodes[key]))
                   for i, key in enumerate(sites)}
    for k, (e, hub, site) in enumerate(sorted(to_site, key=lambda link: (hub_index[link[1]], link[0]["id"]))):
        hx, hy = canvas.abs[hub_shapes[hub]["id"]]
        cx, cy = canvas.abs[site_shapes[site]["id"]]
        sx = hx + ICON * exit_x[(e["id"], "from")]
        ly = lanes_y + 6 * k
        x = corridor + 6 * k
        path = [(sx, hy + ICON), (sx, ly), (x, ly), (x, cy + ICON / 2), (cx, cy + ICON / 2)]
        canvas.edges.append(edge_between(e, hub, site, path, f"exitX={exit_x[(e['id'], 'from')]:.3f};exitY=1;entryX=0;entryY=0.5;"))
