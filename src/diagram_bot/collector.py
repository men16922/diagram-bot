from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def now():
    return datetime.now(UTC).isoformat()


OPERATIONS = [
    ("ec2", "describe_vpcs", "Vpcs", "vpcs"),
    ("ec2", "describe_subnets", "Subnets", "subnets"),
    ("ec2", "describe_instances", "Reservations", "instances"),
    ("elbv2", "describe_load_balancers", "LoadBalancers", "load_balancers"),
    ("elbv2", "describe_target_groups", "TargetGroups", "target_groups"),
    # Infrastructure context for architecture diagrams: internet path, egress, routing, data tier
    # and scaling groups. All are read-only Describe calls with the same partial-failure handling.
    ("ec2", "describe_internet_gateways", "InternetGateways", "internet_gateways"),
    ("ec2", "describe_nat_gateways", "NatGateways", "nat_gateways"),
    ("ec2", "describe_route_tables", "RouteTables", "route_tables"),
    ("rds", "describe_db_instances", "DBInstances", "db_instances"),
    ("autoscaling", "describe_auto_scaling_groups", "AutoScalingGroups", "auto_scaling_groups"),
    ("ec2", "describe_vpc_endpoints", "VpcEndpoints", "vpc_endpoints"),
    ("ec2", "describe_transit_gateways", "TransitGateways", "transit_gateways"),
    ("ec2", "describe_transit_gateway_vpc_attachments", "TransitGatewayVpcAttachments", "transit_gateway_attachments"),
    # Commonly used VPC-attached services.
    ("rds", "describe_db_clusters", "DBClusters", "db_clusters"),
    ("elasticache", "describe_cache_clusters", "CacheClusters", "cache_clusters"),
    ("elasticache", "describe_replication_groups", "ReplicationGroups", "replication_groups"),
    ("elasticache", "describe_cache_subnet_groups", "CacheSubnetGroups", "cache_subnet_groups"),
    ("kafka", "list_clusters_v2", "ClusterInfoList", "msk_clusters"),
    ("lambda", "list_functions", "Functions", "lambda_functions"),
    ("eks", "list_clusters", "clusters", "eks_cluster_names"),
    # Search, containers, file storage, warehouse and hybrid connectivity.
    ("opensearch", "list_domain_names", "DomainNames", "opensearch_domain_names"),
    ("ecs", "list_clusters", "clusterArns", "ecs_cluster_arns"),
    ("efs", "describe_file_systems", "FileSystems", "efs_file_systems"),
    ("redshift", "describe_clusters", "Clusters", "redshift_clusters"),
    ("redshift", "describe_cluster_subnet_groups", "ClusterSubnetGroups", "redshift_subnet_groups"),
    ("ec2", "describe_vpc_peering_connections", "VpcPeeringConnections", "vpc_peering_connections"),
    ("ec2", "describe_vpn_gateways", "VpnGateways", "vpn_gateways"),
    ("ec2", "describe_vpn_connections", "VpnConnections", "vpn_connections"),
    ("ec2", "describe_customer_gateways", "CustomerGateways", "customer_gateways"),
    # Security group rules feed the architecture review (internet-exposed admin and data ports).
    ("ec2", "describe_security_groups", "SecurityGroups", "security_groups"),
]
# IAM service prefixes that differ from the boto3 client name.
IAM_PREFIX = {"elbv2": "elasticloadbalancing", "opensearch": "es", "efs": "elasticfilesystem"}


def permission(service, operation):
    """IAM action needed for a read operation, shown next to denied or partial coverage."""
    prefix = IAM_PREFIX.get(service, service)
    action = "".join(part.title() for part in operation.split("_"))
    return prefix + ":" + {"DescribeDbInstances": "DescribeDBInstances", "DescribeDbClusters": "DescribeDBClusters"}.get(action, action)


def query(client, operation, key, params=None):
    """Keep successful pages even if a later page is inaccessible."""
    params = params or {}
    rows = []
    coverage = {"operation": operation, "parameters": params,
                "started_at": now(), "status": "complete", "pages": 0}
    try:
        if client.can_paginate(operation):
            pages = client.get_paginator(operation).paginate(**params)
        else:
            pages = [getattr(client, operation)(**params)]
        for page in pages:
            value = page.get(key, [])
            # Describe calls for one named resource return an object, list calls an array.
            rows.extend(value if isinstance(value, list) else [value])
            coverage["pages"] += 1
    except (ClientError, BotoCoreError) as exc:
        coverage["status"] = "partial" if coverage["pages"] else "unavailable"
        coverage["error_code"] = (exc.response["Error"]["Code"]
                                  if isinstance(exc, ClientError) else type(exc).__name__)
    coverage.update(finished_at=now(), items=len(rows))
    return rows, coverage


def assume(session, role_arn, external_id, region, config):
    """Cross-account collection: temporary credentials for the role, capped by the ReadOnlyAccess session policy
    so that even a broader role can only read during this session."""
    partition = role_arn.split(":")[1]
    params = {"RoleArn": role_arn, "RoleSessionName": "diagram-bot-readonly", "DurationSeconds": 3600,
              "PolicyArns": [{"arn": f"arn:{partition}:iam::aws:policy/ReadOnlyAccess"}]}
    if external_id:
        params["ExternalId"] = external_id
    credentials = session.client("sts", region_name=region, config=config).assume_role(**params)["Credentials"]
    return boto3.Session(aws_access_key_id=credentials["AccessKeyId"], aws_secret_access_key=credentials["SecretAccessKey"],
                         aws_session_token=credentials["SessionToken"], region_name=region)


def collect(region, profile=None, expected_account=None, session=None, role_arn=None, external_id=None, workers=8):
    session = session or boto3.Session(profile_name=profile, region_name=region)
    # Adaptive retries back off client-side when an account is throttled during large collections.
    config = Config(retries={"mode": "adaptive", "total_max_attempts": 8}, connect_timeout=10, read_timeout=30,
                    max_pool_connections=16)
    started = now()
    if role_arn:
        session = assume(session, role_arn, external_id, region, config)
    identity = session.client("sts", region_name=region, config=config).get_caller_identity()
    account = identity["Account"]
    if expected_account and account != expected_account:
        raise ValueError(f"계정 불일치: 요청 {expected_account}, 현재 인증 {account}")
    clients = {service: session.client(service, region_name=region, config=config)
               for service in sorted({service for service, *_ in OPERATIONS})}

    def call(log, service, operation, key, params=None):
        rows, result = query(clients[service], operation, key, params)
        result.update(service=service, permission=permission(service, operation))
        log.append(result)
        return rows

    # Phase 1: one thread per service, its calls in order (per-service throttling stays as before);
    # services run concurrently. Coverage keeps the OPERATIONS order regardless of completion order.
    by_service = {}
    for index, (service, *_rest) in enumerate(OPERATIONS):
        by_service.setdefault(service, []).append(index)
    logs = [[] for _ in OPERATIONS]
    rows_of = [None] * len(OPERATIONS)

    def listing(service):
        for index in by_service[service]:
            _, operation, key, _ = OPERATIONS[index]
            rows_of[index] = call(logs[index], service, operation, key)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(listing, by_service))
    raw, coverage = {}, []
    for (*_, name), rows, log in zip(OPERATIONS, rows_of, logs, strict=True):
        raw[name] = [i for reservation in rows for i in reservation.get("Instances", [])] if name == "instances" else rows
        log[0]["items"] = len(raw[name])
        coverage += log

    def batches(items, size):
        return [items[i:i + size] for i in range(0, len(items), size)]

    # Phase 2: per-resource detail, again one thread per service.
    def target_health(log):
        return {"target_health": [{"TargetGroupArn": g["TargetGroupArn"], "Descriptions": call(
            log, "elbv2", "describe_target_health", "TargetHealthDescriptions", {"TargetGroupArn": g["TargetGroupArn"]})}
            for g in raw["target_groups"]]}

    def eks(log):
        clusters = []
        for name in raw["eks_cluster_names"]:
            cluster = call(log, "eks", "describe_cluster", "cluster", {"name": name})
            groups = call(log, "eks", "list_nodegroups", "nodegroups", {"clusterName": name})
            if cluster:
                clusters.append({**cluster[0], "nodegroups": [g for group in groups for g in call(
                    log, "eks", "describe_nodegroup", "nodegroup", {"clusterName": name, "nodegroupName": group})]})
        return {"eks_clusters": clusters}

    def msk(log):
        # Which subnet each broker runs in.
        return {"msk_nodes": [{**node, "ClusterArn": c["ClusterArn"]} for c in raw["msk_clusters"]
                              if c.get("ClusterType", "PROVISIONED") == "PROVISIONED"
                              for node in call(log, "kafka", "list_nodes", "NodeInfoList", {"ClusterArn": c["ClusterArn"]})]}

    def opensearch(log):
        names = [d["DomainName"] for d in raw["opensearch_domain_names"]]
        domains = [d for batch in batches(names, 5) for d in call(log, "opensearch", "describe_domains", "DomainStatusList", {"DomainNames": batch})]
        nodes = [{**node, "DomainName": d["DomainName"]} for d in domains if (d.get("VPCOptions") or {}).get("SubnetIds")
                 for node in call(log, "opensearch", "describe_domain_nodes", "DomainNodesStatusList", {"DomainName": d["DomainName"]})]
        return {"opensearch_domains": domains, "opensearch_nodes": nodes}

    def ecs(log):
        services, tasks = [], []
        for cluster in raw["ecs_cluster_arns"]:
            arns = call(log, "ecs", "list_services", "serviceArns", {"cluster": cluster})
            services += [s for batch in batches(arns, 10) for s in call(log, "ecs", "describe_services", "services", {"cluster": cluster, "services": batch})]
            running = call(log, "ecs", "list_tasks", "taskArns", {"cluster": cluster, "desiredStatus": "RUNNING"})
            tasks += [t for batch in batches(running, 100) for t in call(log, "ecs", "describe_tasks", "tasks", {"cluster": cluster, "tasks": batch})]
        return {"ecs_services": services, "ecs_tasks": tasks}

    def efs(log):
        return {"efs_mount_targets": [m for fs in raw["efs_file_systems"]
                                      for m in call(log, "efs", "describe_mount_targets", "MountTargets", {"FileSystemId": fs["FileSystemId"]})]}

    details = (target_health, eks, msk, opensearch, ecs, efs)
    detail_logs = [[] for _ in details]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        updates = list(pool.map(lambda pair: pair[0](pair[1]), zip(details, detail_logs)))
    for update, log in zip(updates, detail_logs):
        raw.update(update)
        coverage += log
    for name in ("eks_cluster_names", "opensearch_domain_names", "ecs_cluster_arns"):
        raw.pop(name)
    if any(c["status"] != "complete" for c in coverage if c["operation"] == "describe_target_groups"):
        coverage.append({"service": "elbv2", "operation": "describe_target_health",
                         "parameters": {}, "status": "not_discovered",
                         "reason": "대상 그룹 조회가 불완전하여 미발견 그룹의 대상은 조회하지 못했습니다."})
    return {"schema_version": 1, "metadata": {
        "account_id": account, "region": region, "profile": profile, "role_arn": role_arn,
        "partition": identity["Arn"].split(":")[1],
        "started_at": started, "finished_at": now(), "demo": False,
        "status": "complete" if all(c["status"] == "complete" for c in coverage) else "partial",
        "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget", "InternetGateway",
                  "NatGateway", "RouteTable", "RDS", "Aurora", "AutoScalingGroup", "VpcEndpoint", "TransitGatewayAttachment",
                  "EKS", "MSK", "ElastiCache", "Lambda", "OpenSearch", "ECS", "EFS", "Redshift", "VpcPeering", "VPN", "SecurityGroupRules"],
        "limitations": ["설정 관계만 표시하며 실제 통신 여부는 확인하지 않습니다.",
                        "여러 API의 조회 결과이며 단일 시점의 원자적 스냅샷이 아닙니다.",
                        "ENI·EBS는 EC2 응답의 참조만 표시합니다. 보안 그룹 규칙은 검토 결과와 속성 패널에만 쓰며 구성도에 선으로 그리지 않습니다.",
                        "리스너 규칙·NACL·Direct Connect·실제 트래픽은 조회 범위에 포함되지 않습니다."]},
        "coverage": coverage, "raw": raw}
