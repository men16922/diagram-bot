from datetime import datetime, timezone

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError


def now():
    return datetime.now(timezone.utc).isoformat()


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
]


def permission(service, operation):
    """IAM action needed for a read operation, shown next to denied or partial coverage."""
    prefix = {"elbv2": "elasticloadbalancing"}.get(service, service)
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


def collect(region, profile=None, expected_account=None, session=None):
    session = session or boto3.Session(profile_name=profile, region_name=region)
    config = Config(retries={"mode": "standard", "total_max_attempts": 4},
                    connect_timeout=10, read_timeout=30)
    started = now()
    identity = session.client("sts", region_name=region, config=config).get_caller_identity()
    account = identity["Account"]
    if expected_account and account != expected_account:
        raise ValueError(f"계정 불일치: 요청 {expected_account}, 현재 인증 {account}")
    clients = {service: session.client(service, region_name=region, config=config)
               for service in sorted({service for service, *_ in OPERATIONS})}
    raw, coverage = {}, []
    for service, operation, key, name in OPERATIONS:
        rows, result = query(clients[service], operation, key)
        result.update(service=service, permission=permission(service, operation))
        coverage.append(result)
        raw[name] = ([i for reservation in rows for i in reservation.get("Instances", [])]
                     if name == "instances" else rows)
        result["items"] = len(raw[name])
    raw["target_health"] = []
    for group in raw["target_groups"]:
        arn = group["TargetGroupArn"]
        rows, result = query(clients["elbv2"], "describe_target_health",
                             "TargetHealthDescriptions", {"TargetGroupArn": arn})
        result.update(service="elbv2", permission=permission("elbv2", "describe_target_health"))
        coverage.append(result)
        raw["target_health"].append({"TargetGroupArn": arn, "Descriptions": rows})
    raw["eks_clusters"] = []
    for name in raw.pop("eks_cluster_names"):
        details = []
        for operation, key, params in (("describe_cluster", "cluster", {"name": name}),
                                       ("list_nodegroups", "nodegroups", {"clusterName": name})):
            rows, result = query(clients["eks"], operation, key, params)
            result.update(service="eks", permission=permission("eks", operation))
            coverage.append(result)
            details.append(rows)
        if details[0]:
            cluster = {**details[0][0], "nodegroups": []}
            for group in details[1]:
                rows, result = query(clients["eks"], "describe_nodegroup", "nodegroup", {"clusterName": name, "nodegroupName": group})
                result.update(service="eks", permission=permission("eks", "describe_nodegroup"))
                coverage.append(result)
                cluster["nodegroups"] += rows
            raw["eks_clusters"].append(cluster)
    if any(c["status"] != "complete" for c in coverage if c["operation"] == "describe_target_groups"):
        coverage.append({"service": "elbv2", "operation": "describe_target_health",
                         "parameters": {}, "status": "not_discovered",
                         "reason": "대상 그룹 조회가 불완전하여 미발견 그룹의 대상은 조회하지 못했습니다."})
    return {"schema_version": 1, "metadata": {
        "account_id": account, "region": region, "profile": profile,
        "partition": identity["Arn"].split(":")[1],
        "started_at": started, "finished_at": now(), "demo": False,
        "status": "complete" if all(c["status"] == "complete" for c in coverage) else "partial",
        "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget", "InternetGateway",
                  "NatGateway", "RouteTable", "RDS", "Aurora", "AutoScalingGroup", "VpcEndpoint", "TransitGatewayAttachment",
                  "EKS", "MSK", "ElastiCache", "Lambda"],
        "limitations": ["설정 관계만 표시하며 실제 통신 여부는 확인하지 않습니다.",
                        "여러 API의 순차 조회 결과이며 단일 시점의 원자적 스냅샷이 아닙니다.",
                        "보안 그룹·ENI·EBS는 EC2/ELB 응답의 참조만 표시합니다. 별도 상세 조회는 하지 않습니다.",
                        "EKS·리스너 규칙·보안 그룹 규칙·VPN/Direct Connect·실제 트래픽은 조회 범위에 포함되지 않습니다."]},
        "coverage": coverage, "raw": raw}
