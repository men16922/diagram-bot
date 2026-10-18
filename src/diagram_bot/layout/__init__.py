"""Deterministic layered layout for one view. Produces geometry only; drawio.py serializes it.

Bands inside a VPC, top to bottom: load balancers, target groups, an edge channel, AZ columns
holding subnets and their instances, then VPC resources whose subnet was not confirmed.
Resources outside every drawn VPC go to a separate reference container. Edges from target
groups run through the channel and the gutters between AZ columns, so they never cross a
subnet or an icon. Identical input and options always yield identical coordinates.
"""
from ..views import AUXILIARY, EDGE, GROUP_TARGETS, GROUPS, HUB_ORDER, REGIONAL, REMOTE, SITE_KINDS, UNDRAWN_KINDS, containment, edge_links, overview_links
from .base import (AZ_HEADER, BOTTOM_LABEL, ENGINE, ICON, LANE_TOP, LINE, MAX_LINES, ROW, SIDE_LABEL, SIDE_LINES, SIDE_SLOT, SLOT, SUBNET_HEADER, VERSION,
                   VPC_HEADER, cell_id, is_public, outside_vpc, tier_rank)
from .text import (ACCELERATED_ICON, ENGINES, GPU, LABELERS, ML_ENDPOINTS, accelerated_icon, cloud_label, fit_label, fit_name, group_title, icon_label,
                   icon_variant, instance_family, label_lines, resource_label, text_width)
from .edge_column import EDGE_COLUMN, EDGE_SLOT, align_edge, edge_column, edge_offset, edge_path, waf_badge
from .routing import OUTSIDE_BOXES, TG_STROKES, edge_between, edge_entry, route, target_on_right, tg_stroke

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
    # Connected VPCs come first so their cards sit in the rows right above the network hubs.
    connected = {a for _, a, _ in to_hub}
    combined = bool(snapshot["metadata"].get("combined"))
    # Connected VPCs come first; a combined overview keeps each account's VPCs together.
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
    members = {}
    for key, vpc in vpc_of.items():
        members.setdefault(vpc, []).append(nodes[key])
    vpc_status = next((c["status"] for c in snapshot["coverage"] if c["operation"] == "describe_vpcs"), "unavailable")
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
    if not vpcs:
        message = "조회된 VPC가 없습니다." if vpc_status == "complete" else "VPC 조회가 완료되지 않았습니다.\n빈 환경이 아니라 권한·조회 실패일 수 있습니다."
        cards.insert(0, (message, None, True))
    card_h = max([112] + [36 + 15 * (label.count("\n") + 1) for label, _, _ in cards]
                 + [heads[r] + glance_height(glances[r]) + 6 for r in glances])

    def fill():
        for shape in [s for s in canvas.shapes if s["role"] == "summary" and s.get("resource") in glances]:
            draw_glance(canvas, shape, glances[shape["resource"]], heads[shape["resource"]])
        cloud["w"] = group["x"] + group["w"] + 20
        overview_edge(canvas, nodes, cloud, distributions, fronted, glances)
    # A combined overview without VPCs has no card rows for network(): the plain grid shows its message and cards.
    if not to_hub and not combined or not vpcs:
        for i, (label, resource, empty) in enumerate(cards):
            canvas.add(cell_id(resource or f"summary/{i}"), group["id"], 24 + (i % columns) * (card_w + gap),
                       VPC_HEADER + 18 + (i // columns) * (card_h + gap), card_w, card_h, "summary", label,
                       resource=resource, empty=empty)
        group["h"] = VPC_HEADER + 18 + -(-len(cards) // columns) * (card_h + gap)
        cloud["h"] = group["y"] + group["h"] + 20
        fill()
        return
    account_of = {v["id"]: v["account"] for v in vpcs} if combined else None
    network(canvas, nodes, cloud, group, cards, card_w, card_h, columns, len(vpcs) if combined else len(connected), to_hub, to_site,
            account_of, to_peer)
    fill()


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
    top = cards[:top_count]
    rest = cards[top_count:]
    # A combined overview frames each account's VPC cards (rows never mix accounts).
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
    head = 34 if account_of else 0
    to_peer = to_peer or []
    hubs = sorted({b for _, _, b in to_hub} | {a for _, a, _ in to_site} | {k for _, a, b in to_peer for k in (a, b)},
                  key=lambda k: (HUB_ORDER.get(nodes[k]["kind"], 9), nodes[k]["name"], k))
    sites = sorted({b for _, _, b in to_site}, key=lambda k: (nodes[k]["name"], k))
    hub_index = {k: i for i, k in enumerate(hubs)}
    order = lambda link: (hub_index[link[2]], link[0]["id"])
    by_card = {}
    for link in sorted(to_hub, key=order):
        by_card.setdefault(link[1], []).append(link)
    row_of = {resource: (r, c) for r, row in enumerate(rows) for c, (_, resource, _) in enumerate(row)}
    # Routes from upper rows drop through the gap right of their card; widen gaps and row gutters to fit them.
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
    ox, oy = canvas.abs[group["id"]][0], canvas.abs[group["id"]][1]
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
    entering = {}
    for link in sorted(to_hub, key=order):
        entering.setdefault(link[2], []).append(link)
    entry_x = {link[0]["id"]: (i + 1) / (len(group_) + 1) for group_ in entering.values() for i, link in enumerate(group_)}
    # A hub's bottom edge carries its links to other hubs (ordered by the other hub's position) and then its links to
    # on-premises sites, each from its own point.
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
    # Hub to hub (Direct Connect gateway association): down from both hubs to a lane of its own under the hub band.
    peer_lanes = hub_y + ICON + 16
    for k, (e, a, b) in enumerate(sorted(to_peer, key=lambda link: (hub_index[link[1]], hub_index[link[2]], link[0]["id"]))):
        (ax, ay), (bx, _) = canvas.abs[hub_shapes[a]["id"]], canvas.abs[hub_shapes[b]["id"]]
        fa, fb = exit_x[(e["id"], "from")], exit_x[(e["id"], "to")]
        ly = oy + peer_lanes + 6 * k
        path = [(ax + ICON * fa, ay + ICON), (ax + ICON * fa, ly), (bx + ICON * fb, ly), (bx + ICON * fb, ay + ICON)]
        canvas.edges.append(edge_between(e, a, b, path, f"exitX={fa:.3f};exitY=1;entryX={fb:.3f};entryY=1;"))
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
    if not to_site:
        return
    # Customer gateways and Direct Connect connections stacked in a data center box right of the AWS Cloud, each
    # entered from the left.
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
        ly = oy + site_lanes + 6 * k
        x = corridor + 6 * k
        path = [(sx, hy + ICON), (sx, ly), (x, ly), (x, cy + ICON / 2), (cx, cy + ICON / 2)]
        canvas.edges.append(edge_between(e, hub, site, path, f"exitX={exit_x[(e['id'], 'from')]:.3f};exitY=1;entryX=0;entryY=0.5;"))


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


