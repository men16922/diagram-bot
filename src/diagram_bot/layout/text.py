"""Label text: width estimates, truncation, the per-kind resource labels, icon variants and frame captions."""
import re

from .base import BOTTOM_LABEL, MAX_LINES, SIDE_LABEL, SIDE_LINES, outside_vpc


def text_width(text, size=12):
    # Conservative Arial estimate: CJK glyphs are about one em, Latin about 0.6 em.
    return sum(size if ord(c) > 0x2E7F else size * 0.62 for c in text)


def fit_label(text, width, lines=MAX_LINES, size=12):
    """Truncate so the label fits `lines` wrapped lines; full text stays in the inspector."""
    # Wrapped labels lose width at word breaks; a single truncated line does not.
    budget = width * lines * (0.95 if lines == 1 else 0.85)
    if text_width(text, size) <= budget:
        return text
    out = ""
    for c in text:
        if text_width(out + c + "…", size) > budget:
            break
        out += c
    # Cut at a word break when one is near, so a half word next to the ellipsis does not read
    # as running into the type line below ("…전용 인스…" over "m6i.large").
    space = out.rfind(" ")
    if space >= len(out) * 0.6:
        out = out[:space].rstrip(" (·-")
    return out + "…"


def fit_name(text, width):
    """One-line name: names ending in a number (node, shard, replica) keep their tail, others their start."""
    if text_width(text) <= width * 0.95 or not text[-1:].isdigit():
        return fit_label(text, width, 1)
    keep = len(fit_label(text, width, 1)) - 1  # characters that fit beside the ellipsis
    back = max(4, keep // 2)
    return text[:keep - back] + "…" + text[-back:]


def label_lines(text, width, size=12):
    """Estimated wrapped lines, using the same single-line allowance as fit_label."""
    def lines(part):
        measured = text_width(part, size)
        return 1 if measured <= width * 0.95 else -(-int(measured / 0.85) // width)
    return sum(lines(part) for part in text.split("\n"))


# Accelerated EC2 families that have their own AWS Architecture Icon. The GPU icons print their
# family (P3, P4d, G4dn), so newer GPU families (p5, g6e…) keep the generic icon and a "GPU" label.
ACCELERATED_ICON = {"p3": "p3", "p3dn": "p3", "p4d": "p4d", "p4de": "p4de", "g4dn": "g4dn", "g4ad": "g4ad", "dl1": "dl1"}
GPU = re.compile(r"^(p|g)\d")
ML_ENDPOINTS = re.compile(r"\.(bedrock|sagemaker)[.-]?")


def instance_family(node):
    return str(node["attributes"].get("InstanceType", "")).split(".")[0]


def accelerated_icon(node):
    family = instance_family(node)
    return ACCELERATED_ICON.get(family) or ("trainium" if family.startswith("trn") else "inferentia" if family.startswith("inf") else None)


def icon_variant(node):
    """Icon choice within a kind: load balancer type, database and cache engine, file system class,
    accelerated instance family and AI service endpoints."""
    attrs = node["attributes"]
    if node["kind"] == "ec2" and accelerated_icon(node):
        return accelerated_icon(node)
    if node["kind"] == "vpc_endpoint":
        match = ML_ENDPOINTS.search(str(attrs.get("ServiceName", "")) + ".")
        return match.group(1) if match else None
    if node["kind"] == "load_balancer":
        return attrs.get("Type")
    if node["kind"] in ("db_instance", "db_standby"):
        engine = str(attrs.get("Engine", ""))
        return "aurora" if engine.startswith("aurora") else {"docdb": "documentdb", "neptune": "neptune"}.get(engine)
    if node["kind"] == "cache_node":
        return str(attrs.get("Engine", "redis"))
    if node["kind"] == "efs_mount_target":
        return "efs_one_zone" if attrs.get("OneZone") else "efs_standard"
    if node["kind"] == "fsx_file_system":
        return {"LUSTRE": "fsx_lustre", "ONTAP": "fsx_ontap", "OPENZFS": "fsx_openzfs", "WINDOWS": "fsx_windows"}.get(attrs.get("FileSystemType"))
    if node["kind"] == "registered_target" and attrs.get("TargetType") == "ip" and not outside_vpc(node):
        return "ip_target"
    return None


ENGINES = {"aurora-postgresql": "Aurora PostgreSQL", "aurora-mysql": "Aurora MySQL", "postgres": "PostgreSQL", "mysql": "MySQL",
           "mariadb": "MariaDB", "redis": "Redis", "valkey": "Valkey", "memcached": "Memcached", "docdb": "DocumentDB",
           "neptune": "Neptune", "oracle-ee": "Oracle EE", "oracle-se2": "Oracle SE2", "sqlserver-ee": "SQL Server EE",
           "sqlserver-se": "SQL Server SE", "sqlserver-ex": "SQL Server Express", "sqlserver-web": "SQL Server Web"}


def stack(*parts):
    """Label lines, skipping empty facts."""
    return "\n".join(str(p) for p in parts if p not in (None, ""))


def registered_target_label(node, attrs, name):
    target = attrs.get("Target", {})
    return f"{target.get('Id', name)}:{target.get('Port', '')}"


def ec2_label(node, attrs, name):
    # AI/ML placement facts from DescribeInstances: EFA interface, Capacity Block or reservation, cluster placement group.
    efa = any(i.get("InterfaceType") == "efa" for i in attrs.get("NetworkInterfaces", []))
    capacity = {"capacity-block": "Capacity Block", "spot": "spot"}.get(attrs.get("InstanceLifecycle")) \
        or ("ODCR" if attrs.get("CapacityReservationId") else None)
    group = (attrs.get("Placement") or {}).get("GroupName")
    eip = node.get("elastic_ips") or []
    # One fact group per line so a 140 px label never cuts EFA or the capacity source.
    return stack(name, attrs.get("InstanceType"),
                 " · ".join(filter(None, ("GPU" if GPU.match(instance_family(node)) else None, "EFA" if efa else None,
                                          f"EIP {eip[0]}" if eip else None))),
                 " · ".join(filter(None, (capacity, f"PG {group}" if group else None))))


def load_balancer_label(node, attrs, name):
    if not attrs.get("Type"):
        return name
    short = {"application": "ALB", "network": "NLB", "gateway": "GWLB"}.get(attrs["Type"], attrs["Type"])
    zones = len(attrs.get("AvailabilityZones", []))
    eips = len(node.get("elastic_ips") or [])
    facts = " · ".join(filter(None, (f"AZ {zones}개" if zones else None, f"EIP {eips}" if eips else None, "WAF" if node.get("waf") else None)))
    return stack(name, f"{short} · {attrs.get('Scheme', '')}".rstrip(" ·"), facts)


def cloudfront_label(node, attrs, name):
    kinds = [t for t, _ in node.get("origins") or []]
    origins = " · ".join(f"{t} {kinds.count(t)}" for t in dict.fromkeys(kinds))
    return stack(name, "CloudFront" + ("" if attrs.get("Enabled", True) else " · 비활성"),
                 attrs.get("DomainName") if attrs.get("DomainName") != name else None, f"오리진 {origins}" if origins else None)


def dx_connection_label(node, attrs, name):
    vifs = node.get("virtual_interfaces") or []
    if node["reference_only"]:
        return stack(name, "Direct Connect 연결 (참조)", f"VIF {len(vifs)}" if vifs else None)
    return stack(name, f"Direct Connect · {attrs.get('bandwidth', '')}".rstrip(" ·"), attrs.get("location"),
                 " · ".join(filter(None, (f"VIF {len(vifs)}" if vifs else None, attrs.get("connectionState")))))


def vpc_peering_label(node, attrs, name):
    cidr = lambda side: (attrs.get(side) or {}).get("CidrBlock", "?")
    status = (attrs.get("Status") or {}).get("Code", "")
    return stack(name, f"VPC peering · {status}".rstrip(" ·"), f"{cidr('RequesterVpcInfo')}↔{cidr('AccepterVpcInfo')}")


def customer_gateway_label(node, attrs, name):
    tunnels = attrs.get("VpnTunnels") or []
    return stack(name, "Customer gateway", attrs.get("IpAddress"), f"VPN 터널 {', '.join(tunnels)} UP" if tunnels else None)


def vpc_endpoint_label(node, attrs, name):
    endpoint = attrs.get("VpcEndpointType", "")
    where = f" · 서브넷 {len(attrs.get('SubnetIds', []))}개" if endpoint == "Interface" else ""
    return stack(attrs.get("ServiceShortName") or name, f"{endpoint}{where}")


def transit_gateway_label(node, attrs, name):
    asn = (attrs.get("Options") or {}).get("AmazonSideAsn")
    tables = node.get("route_tables") or []
    return stack(name, "Transit Gateway", " · ".join(filter(None, (f"ASN {asn}" if asn else None,
                                                                     f"라우팅 테이블 {len(tables)}" if tables else None))))


def nat_gateway_label(node, attrs, name):
    eip = node.get("elastic_ips") or []
    address = f"EIP {eip[0]}" if eip else next((a.get("PublicIp") for a in attrs.get("NatGatewayAddresses", []) if a.get("PublicIp")), None)
    return stack(name, "NAT gateway", address or ("private NAT" if attrs.get("ConnectivityType") == "private" else None))


def eks_cluster_label(node, attrs, name):
    config = attrs.get("resourcesVpcConfig") or {}
    access = "+".join(a for a, on in (("public", config.get("endpointPublicAccess")), ("private", config.get("endpointPrivateAccess"))) if on)
    return stack(name, f"EKS {attrs.get('version', '')} control plane".replace("  ", " "), f"endpoint {access}" if access else None)


def msk_broker_label(node, attrs, name):
    ids = attrs.get("BrokerIds") or []
    return stack(attrs.get("ClusterName") or name, f"MSK 브로커 {attrs.get('Brokers', '')}개", attrs.get("InstanceType"),
                 f"broker {', '.join(str(i) for i in ids)}" if ids else None)


def search_node_label(node, attrs, name):
    engine, _, version = str(attrs.get("EngineVersion") or "OpenSearch").partition("_")
    counts = " · ".join(f"{label} {attrs[key]}" for key, label in (("Data", "data"), ("Master", "master"), ("Warm", "warm")) if attrs.get(key))
    state = "standby" if attrs.get("Standby") else ("active" if attrs.get("MultiAZWithStandby") else attrs.get("InstanceType"))
    return stack(attrs.get("DomainName") or name, f"{engine} {version}".strip(), ("~" if attrs.get("Estimated") else "") + counts, state)


def fsx_label(node, attrs, name):
    kind_name = {"LUSTRE": "Lustre", "ONTAP": "NetApp ONTAP", "OPENZFS": "OpenZFS", "WINDOWS": "Windows File Server"}.get(
        attrs.get("FileSystemType"), attrs.get("FileSystemType", ""))
    config = attrs.get("LustreConfiguration") or attrs.get("OntapConfiguration") or attrs.get("OpenZFSConfiguration") \
        or attrs.get("WindowsConfiguration") or {}
    size = f"{attrs['StorageCapacity'] / 1024:g} TiB" if attrs.get("StorageCapacity") else None
    return stack(name, f"FSx for {kind_name}", config.get("DeploymentType"), size)


def sagemaker_cluster_label(node, attrs, name):
    groups = attrs.get("InstanceGroups", [])
    orchestrator = "EKS" if (attrs.get("Orchestrator") or {}).get("Eks") else "Slurm"
    # The largest instance groups, without the "ml." prefix (full types in the inspector).
    top = sorted(groups, key=lambda g: -(g.get("CurrentCount") or g.get("TargetCount") or 0))[:2]
    return stack(name, f"HyperPod · {orchestrator}",
                 *[f"{str(g.get('InstanceType', '')).removeprefix('ml.')} × {g.get('CurrentCount', g.get('TargetCount', ''))}" for g in top])


def cache_node_label(node, attrs, name):
    engine = f"{ENGINES.get(attrs.get('Engine'), attrs.get('Engine', ''))} {attrs.get('EngineVersion', '')}".strip()
    role = attrs.get("CurrentRole")
    if role and attrs.get("Shard"):
        role = f"샤드 {int(attrs['Shard']) if str(attrs['Shard']).isdigit() else attrs['Shard']} · {role}"
    return stack(name, engine, role, attrs.get("CacheNodeType"))


def db_label(node, attrs, name):
    replicas = attrs.get("ReadReplicaDBInstanceIdentifiers")
    role = node.get("db_role") or ("standby" if node["kind"] == "db_standby" else ("primary" if attrs.get("MultiAZ") or replicas else "Single-AZ"))
    size = "Serverless v2" if attrs.get("DBInstanceClass") == "db.serverless" else attrs.get("DBInstanceClass")
    return stack(attrs.get("DBInstanceIdentifier", name).replace(" (standby)", ""), ENGINES.get(attrs.get("Engine"), attrs.get("Engine", "")),
                 role, size)


LABELERS = {
    "registered_target": registered_target_label,
    "ec2": ec2_label,
    "load_balancer": load_balancer_label,
    "cloudfront_distribution": cloudfront_label,
    "dx_gateway": lambda node, attrs, name: stack(name, "Direct Connect gateway", f"ASN {attrs['amazonSideAsn']}" if attrs.get("amazonSideAsn") else None),
    "dx_connection": dx_connection_label,
    "internet_gateway": lambda node, attrs, name: stack(name, "Internet gateway"),
    "vpn_gateway": lambda node, attrs, name: stack(name, "Virtual private gateway", f"ASN {attrs['AmazonSideAsn']}" if attrs.get("AmazonSideAsn") else None),
    "vpc_peering": vpc_peering_label,
    "customer_gateway": customer_gateway_label,
    "vpc_endpoint": vpc_endpoint_label,
    "tgw_attachment": lambda node, attrs, name: stack(name, "TGW attachment"),
    "transit_gateway": transit_gateway_label,
    "nat_gateway": nat_gateway_label,
    "eks_cluster": eks_cluster_label,
    "ecs_service": lambda node, attrs, name: stack(name, f"ECS service · {attrs.get('LaunchSummary') or ''}".rstrip(" ·"),
                                                   f"태스크 {attrs.get('runningCount', 0)}/{attrs.get('desiredCount', 0)}"),
    "ecs_task": lambda node, attrs, name: stack(attrs.get("ServiceName") or name, f"{attrs.get('launchType') or 'ECS'} task", attrs.get("PrivateIp")),
    "lambda_function": lambda node, attrs, name: stack(name, f"Lambda · {attrs.get('Runtime', '')}".rstrip(" ·"),
                                                       f"서브넷 {len((attrs.get('VpcConfig') or {}).get('SubnetIds', []))}개"),
    "msk_broker": msk_broker_label,
    "search_node": search_node_label,
    "efs_mount_target": lambda node, attrs, name: stack(name, "EFS mount target", "One Zone" if attrs.get("OneZone") else "Regional", attrs.get("IpAddress")),
    "fsx_file_system": fsx_label,
    "sagemaker_endpoint": lambda node, attrs, name: stack(name, f"SageMaker · {attrs.get('EndpointStatus', '')}".rstrip(" ·"),
                                                          f"{attrs.get('InstanceType', '')} × {attrs.get('InstanceCount', 0)}"),
    "sagemaker_cluster": sagemaker_cluster_label,
    "redshift_cluster": lambda node, attrs, name: stack(attrs.get("ClusterIdentifier") or name, "Redshift",
                                                        f"{attrs.get('NodeType', '')} × {attrs.get('NodesHere', '')}", attrs.get("Role") or "single-AZ"),
    "cache_node": cache_node_label,
    "db_instance": db_label,
    "db_standby": db_label,
    "target_group": lambda node, attrs, name: stack(name, f"{attrs['Protocol']}:{attrs.get('Port', '')} · {attrs.get('TargetType', '')}")
    if attrs.get("Protocol") else name,
}


def resource_label(node):
    """Name first, then type and the high-availability role; full detail stays in the inspector."""
    labeler = LABELERS.get(node["kind"])
    return labeler(node, node["attributes"], node["name"]) if labeler else node["name"]


def cloud_label(meta):
    if meta.get("combined"):
        return f"AWS Cloud · 계정 {len(meta['accounts'])}개 (계정·리전별 수집 통합)"
    return "AWS Cloud · 설계안 (실제 계정 아님)" if meta.get("design") else f"AWS Cloud · 계정 {meta['account_id']}"


def icon_label(n, side):
    width = SIDE_LABEL if side == "right" else BOTTOM_LABEL
    parts = resource_label(n).split("\n")
    # The name may wrap into whatever the detail lines leave of the label's line budget.
    first = max(1, (SIDE_LINES if side == "right" else MAX_LINES) - len(parts) + 1)
    return "\n".join((fit_name(part, width) if first == 1 else fit_label(part, width, first)) if i == 0 else fit_label(part, width, 1)
                     for i, part in enumerate(parts))


def group_title(graph, nodes, key):
    """Frame caption: what the group is and its capacity."""
    attrs = nodes[key]["attributes"]
    if nodes[key]["kind"] == "ecs_service":
        return (f"ECS service · {nodes[key]['name']} · {attrs.get('LaunchSummary') or ''} · 태스크 {attrs.get('runningCount', 0)}"
                f"/{attrs.get('desiredCount', 0)}").replace(" ·  ·", " ·")
    capacity = f"min {attrs.get('MinSize', '?')} · desired {attrs.get('DesiredCapacity', '?')} · max {attrs.get('MaxSize', '?')}"
    # An EKS managed node group is backed by this Auto Scaling group (DescribeNodegroup.resources).
    owner = next((e for e in graph["edges"] if e["relation"] == "eks_nodegroup" and e["target"] == key), None)
    if owner:
        return f"EKS node group · {owner['details'].get('nodegroup')} ({nodes[owner['source']]['name']}) · {capacity}"
    return f"Auto Scaling group · {nodes[key]['name']} · {capacity}"
