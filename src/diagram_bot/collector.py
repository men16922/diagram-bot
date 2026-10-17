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
    def call(service, operation, key, params):
        rows, result = query(clients[service], operation, key, params)
        result.update(service=service, permission=permission(service, operation))
        coverage.append(result)
        return rows

    def batches(items, size):
        return [items[i:i + size] for i in range(0, len(items), size)]

    raw["eks_clusters"] = []
    for name in raw.pop("eks_cluster_names"):
        cluster = call("eks", "describe_cluster", "cluster", {"name": name})
        groups = call("eks", "list_nodegroups", "nodegroups", {"clusterName": name})
        if cluster:
            raw["eks_clusters"].append({**cluster[0], "nodegroups": [g for group in groups for g in call(
                "eks", "describe_nodegroup", "nodegroup", {"clusterName": name, "nodegroupName": group})]})
    # Per-resource detail: which AZ each broker, search node and task actually runs in.
    raw["msk_nodes"] = [{**node, "ClusterArn": cluster["ClusterArn"]} for cluster in raw["msk_clusters"]
                        if cluster.get("ClusterType", "PROVISIONED") == "PROVISIONED"
                        for node in call("kafka", "list_nodes", "NodeInfoList", {"ClusterArn": cluster["ClusterArn"]})]
    names = [d["DomainName"] for d in raw.pop("opensearch_domain_names")]
    raw["opensearch_domains"] = [d for batch in batches(names, 5)
                                 for d in call("opensearch", "describe_domains", "DomainStatusList", {"DomainNames": batch})]
    raw["opensearch_nodes"] = [{**node, "DomainName": domain["DomainName"]} for domain in raw["opensearch_domains"]
                               if (domain.get("VPCOptions") or {}).get("SubnetIds")
                               for node in call("opensearch", "describe_domain_nodes", "DomainNodesStatusList", {"DomainName": domain["DomainName"]})]
    raw["ecs_services"], raw["ecs_tasks"] = [], []
    for cluster in raw.pop("ecs_cluster_arns"):
        arns = call("ecs", "list_services", "serviceArns", {"cluster": cluster})
        raw["ecs_services"] += [s for batch in batches(arns, 10)
                                for s in call("ecs", "describe_services", "services", {"cluster": cluster, "services": batch})]
        tasks = call("ecs", "list_tasks", "taskArns", {"cluster": cluster, "desiredStatus": "RUNNING"})
        raw["ecs_tasks"] += [t for batch in batches(tasks, 100) for t in call("ecs", "describe_tasks", "tasks", {"cluster": cluster, "tasks": batch})]
    raw["efs_mount_targets"] = [m for fs in raw["efs_file_systems"]
                                for m in call("efs", "describe_mount_targets", "MountTargets", {"FileSystemId": fs["FileSystemId"]})]
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
                  "EKS", "MSK", "ElastiCache", "Lambda", "OpenSearch", "ECS", "EFS", "Redshift", "VpcPeering", "VPN", "SecurityGroupRules"],
        "limitations": ["설정 관계만 표시하며 실제 통신 여부는 확인하지 않습니다.",
                        "여러 API의 순차 조회 결과이며 단일 시점의 원자적 스냅샷이 아닙니다.",
                        "ENI·EBS는 EC2 응답의 참조만 표시합니다. 보안 그룹 규칙은 검토 결과와 속성 패널에만 쓰며 구성도에 선으로 그리지 않습니다.",
                        "리스너 규칙·NACL·Direct Connect·실제 트래픽은 조회 범위에 포함되지 않습니다."]},
        "coverage": coverage, "raw": raw}
