from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import boto3
from botocore.config import Config
from botocore.exceptions import BotoCoreError, ClientError

from .egress import RelayDisconnected
from .errors import error_code


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
    # AI/ML infrastructure: cluster placement groups (EFA training), FSx file systems (Lustre training data and
    # checkpoints), SageMaker real-time endpoints and HyperPod clusters.
    ("ec2", "describe_placement_groups", "PlacementGroups", "placement_groups"),
    ("fsx", "describe_file_systems", "FileSystems", "fsx_file_systems"),
    ("sagemaker", "list_endpoints", "Endpoints", "sagemaker_endpoint_names"),
    ("sagemaker", "list_clusters", "ClusterSummaries", "sagemaker_cluster_names"),
    # Static addresses, Transit Gateway routing domains, the edge in front of the load balancers (CloudFront is a global
    # API answered in any region) and dedicated connectivity (Direct Connect gateways are global to the account).
    ("ec2", "describe_addresses", "Addresses", "elastic_ips"),
    ("ec2", "describe_transit_gateway_route_tables", "TransitGatewayRouteTables", "transit_gateway_route_tables"),
    ("cloudfront", "list_distributions", "DistributionList.Items", "cloudfront_distributions"),
    ("directconnect", "describe_connections", "connections", "dx_connections"),
    ("directconnect", "describe_virtual_interfaces", "virtualInterfaces", "dx_virtual_interfaces"),
    ("directconnect", "describe_direct_connect_gateways", "directConnectGateways", "dx_gateways"),
]
# Snapshot metadata of a collection: what it covers and what it does not claim.
SCOPE = ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget", "InternetGateway",
         "NatGateway", "RouteTable", "RDS", "Aurora", "AutoScalingGroup", "VpcEndpoint", "TransitGatewayAttachment",
         "EKS", "MSK", "ElastiCache", "Lambda", "OpenSearch", "ECS", "EFS", "Redshift", "VpcPeering", "VPN", "SecurityGroupRules",
         "PlacementGroup", "FSx", "SageMakerEndpoint", "SageMakerHyperPod", "ElasticIP", "TransitGatewayRouteTable",
         "CloudFront", "WAF", "DirectConnect"]
LIMITATIONS = ["설정 관계만 표시하며 실제 통신 여부는 확인하지 않습니다.",
               "여러 API의 조회 결과이며 단일 시점의 원자적 스냅샷이 아닙니다.",
               "ENI·EBS는 EC2 응답의 참조만 표시합니다. 보안 그룹 규칙은 검토 결과와 속성 패널에만 쓰며 구성도에 선으로 그리지 않습니다.",
               "CloudFront·CloudFront용 WAF·Direct Connect 게이트웨이는 계정 전역 리소스라 어느 리전에서 수집해도 같습니다.",
               "리스너 규칙·NACL·실제 트래픽은 조회 범위에 포함되지 않습니다."]
# IAM service prefixes that differ from the boto3 client name.
IAM_PREFIX = {"elbv2": "elasticloadbalancing", "opensearch": "es", "efs": "elasticfilesystem"}
# IAM action names whose capitalisation differs from the boto3 operation name.
ACTIONS = {"DescribeDbInstances": "DescribeDBInstances", "DescribeDbClusters": "DescribeDBClusters",
           "ListWebAcls": "ListWebACLs", "ListResourcesForWebAcl": "ListResourcesForWebACL"}
# Operations without a boto3 paginator that page with a marker (same field name in request and response).
MARKERS = {"list_web_acls": "NextMarker"}
# Where the CLOUDFRONT-scope WAF API is served in each partition.
GLOBAL_REGION = {"aws": "us-east-1", "aws-cn": "cn-northwest-1", "aws-us-gov": "us-gov-west-1"}


def permission(service, operation):
    """IAM action needed for a read operation, shown next to denied or partial coverage."""
    prefix = IAM_PREFIX.get(service, service)
    action = "".join(part.title() for part in operation.split("_"))
    return prefix + ":" + ACTIONS.get(action, action)


def marker_pages(client, operation, params, field):
    page = getattr(client, operation)(**params)
    yield page
    # An empty page ends the listing even if it carries a marker.
    while page.get(field) and any(isinstance(v, list) and v for v in page.values()):
        page = getattr(client, operation)(**params, **{field: page[field]})
        yield page


def extract(page, key):
    """Rows of one page; key None keeps the whole response, a dotted key walks nested objects (DistributionList.Items)."""
    if key is None:
        return {k: v for k, v in page.items() if k != "ResponseMetadata"}
    *parents, last = key.split(".")
    for part in parents:
        page = page.get(part) or {}
    return page.get(last, [])


def query(client, operation, key, params=None):
    """Keep successful pages even if a later page is inaccessible."""
    params = params or {}
    rows = []
    coverage = {"operation": operation, "parameters": params,
                "started_at": now(), "status": "complete", "pages": 0}
    try:
        if client.can_paginate(operation):
            pages = client.get_paginator(operation).paginate(**params)
        elif operation in MARKERS:
            pages = marker_pages(client, operation, params, MARKERS[operation])
        else:
            pages = [getattr(client, operation)(**params)]
        for page in pages:
            # key None: the response itself describes one resource (SageMaker Describe* calls).
            value = extract(page, key)
            # Describe calls for one named resource return an object, list calls an array.
            rows.extend(value if isinstance(value, list) else [value])
            coverage["pages"] += 1
    except (ClientError, BotoCoreError) as exc:
        coverage["status"] = "partial" if coverage["pages"] else "unavailable"
        coverage["error_code"] = error_code(exc)
    except RelayDisconnected:
        raise  # the browser went away: the collection fails instead of saving every remaining service as unavailable
    except Exception as exc:  # noqa: BLE001 - one unexpected SDK/endpoint failure must not discard the other services
        coverage["status"] = "partial" if coverage["pages"] else "unavailable"
        coverage["error_code"] = error_code(exc)
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


def batches(items, size):
    return [items[i:i + size] for i in range(0, len(items), size)]


# Phase 2 details: each reads the phase-1 listings in `raw` and returns the collections it adds, querying through
# call(log, service, operation, key, params=None, client=None), which logs coverage and returns the rows.
def target_health(raw, call, log):
    return {"target_health": [{"TargetGroupArn": g["TargetGroupArn"], "Descriptions": call(
        log, "elbv2", "describe_target_health", "TargetHealthDescriptions", {"TargetGroupArn": g["TargetGroupArn"]})}
        for g in raw["target_groups"]]}


def eks(raw, call, log):
    clusters = []
    for name in raw["eks_cluster_names"]:
        cluster = call(log, "eks", "describe_cluster", "cluster", {"name": name})
        groups = call(log, "eks", "list_nodegroups", "nodegroups", {"clusterName": name})
        if cluster:
            clusters.append({**cluster[0], "nodegroups": [g for group in groups for g in call(
                log, "eks", "describe_nodegroup", "nodegroup", {"clusterName": name, "nodegroupName": group})]})
    return {"eks_clusters": clusters}


def msk(raw, call, log):
    # Which subnet each broker runs in.
    return {"msk_nodes": [{**node, "ClusterArn": c["ClusterArn"]} for c in raw["msk_clusters"]
                          if c.get("ClusterType", "PROVISIONED") == "PROVISIONED"
                          for node in call(log, "kafka", "list_nodes", "NodeInfoList", {"ClusterArn": c["ClusterArn"]})]}


def opensearch(raw, call, log):
    names = [d["DomainName"] for d in raw["opensearch_domain_names"]]
    domains = [d for batch in batches(names, 5) for d in call(log, "opensearch", "describe_domains", "DomainStatusList", {"DomainNames": batch})]
    nodes = [{**node, "DomainName": d["DomainName"]} for d in domains if (d.get("VPCOptions") or {}).get("SubnetIds")
             for node in call(log, "opensearch", "describe_domain_nodes", "DomainNodesStatusList", {"DomainName": d["DomainName"]})]
    return {"opensearch_domains": domains, "opensearch_nodes": nodes}


def ecs(raw, call, log):
    services, tasks = [], []
    for cluster in raw["ecs_cluster_arns"]:
        arns = call(log, "ecs", "list_services", "serviceArns", {"cluster": cluster})
        services += [s for batch in batches(arns, 10) for s in call(log, "ecs", "describe_services", "services", {"cluster": cluster, "services": batch})]
        running = call(log, "ecs", "list_tasks", "taskArns", {"cluster": cluster, "desiredStatus": "RUNNING"})
        tasks += [t for batch in batches(running, 100) for t in call(log, "ecs", "describe_tasks", "tasks", {"cluster": cluster, "tasks": batch})]
    return {"ecs_services": services, "ecs_tasks": tasks}


def efs(raw, call, log):
    return {"efs_mount_targets": [m for fs in raw["efs_file_systems"]
                                  for m in call(log, "efs", "describe_mount_targets", "MountTargets", {"FileSystemId": fs["FileSystemId"]})]}


def sagemaker(raw, call, log):
    # An endpoint's instance type comes from its config, its subnets from the models' VpcConfig.
    endpoints, clusters = [], []
    for summary in raw["sagemaker_endpoint_names"]:
        endpoint = call(log, "sagemaker", "describe_endpoint", None, {"EndpointName": summary["EndpointName"]})
        if not endpoint:
            continue
        config = call(log, "sagemaker", "describe_endpoint_config", None, {"EndpointConfigName": endpoint[0]["EndpointConfigName"]})
        variants = config[0].get("ProductionVariants", []) if config else []
        models = [m for name in sorted({v["ModelName"] for v in variants if v.get("ModelName")})
                  for m in call(log, "sagemaker", "describe_model", None, {"ModelName": name})]
        endpoints.append({**endpoint[0], "EndpointConfig": config[0] if config else {}, "Models": models})
    for summary in raw["sagemaker_cluster_names"]:
        clusters += call(log, "sagemaker", "describe_cluster", None, {"ClusterName": summary["ClusterName"]})
    return {"sagemaker_endpoints": endpoints, "sagemaker_clusters": clusters}


def transit_routes(raw, call, log):
    # Which attachments each Transit Gateway route table is associated with and learns from, and its routes.
    associations, propagations, routes = [], [], []
    for table in raw["transit_gateway_route_tables"]:
        ident = {"TransitGatewayRouteTableId": table["TransitGatewayRouteTableId"]}
        associations += [{**a, **ident} for a in call(log, "ec2", "get_transit_gateway_route_table_associations", "Associations", dict(ident))]
        propagations += [{**p, **ident} for p in call(log, "ec2", "get_transit_gateway_route_table_propagations",
                                                      "TransitGatewayRouteTablePropagations", dict(ident))]
        # Active and blackhole routes only (pending/deleted ones carry no path); one call returns at most 1,000 routes.
        found = call(log, "ec2", "search_transit_gateway_routes", None,
                     {**ident, "Filters": [{"Name": "state", "Values": ["active", "blackhole"]}]})
        page = found[0] if found else {}
        routes += [{**r, **ident} for r in page.get("Routes", [])]
        log[-1]["items"] = len(page.get("Routes", []))
        if page.get("AdditionalRoutesAvailable"):
            log[-1].update(status="partial", error_code="AdditionalRoutesAvailable", reason="경로가 1,000개를 넘어 일부만 조회했습니다.")
    return {"transit_gateway_route_table_associations": associations, "transit_gateway_route_table_propagations": propagations,
            "transit_gateway_routes": routes}


def web_acls(raw, call, log):
    # Regional web ACLs and the load balancers they protect. CloudFront ACLs come from the global scope; their
    # distributions name them in ListDistributions.WebACLId.
    acls = []
    for acl in call(log, "wafv2", "list_web_acls", "WebACLs", {"Scope": "REGIONAL", "Limit": 100}):
        arns = call(log, "wafv2", "list_resources_for_web_acl", "ResourceArns",
                    {"WebACLArn": acl["ARN"], "ResourceType": "APPLICATION_LOAD_BALANCER"})
        acls.append({**acl, "Scope": "REGIONAL", "ResourceArns": arns})
    acls += [{**acl, "Scope": "CLOUDFRONT"} for acl in call(log, "wafv2", "list_web_acls", "WebACLs", {"Scope": "CLOUDFRONT", "Limit": 100},
                                                            client="wafv2/global")]
    return {"waf_web_acls": acls}


def direct_connect(raw, call, log):
    # The virtual private gateways and Transit Gateways each Direct Connect gateway is associated with.
    return {"dx_gateway_associations": [a for g in raw["dx_gateways"] for a in call(
        log, "directconnect", "describe_direct_connect_gateway_associations", "directConnectGatewayAssociations",
        {"directConnectGatewayId": g["directConnectGatewayId"]})]}


DETAILS = (target_health, eks, msk, opensearch, ecs, efs, sagemaker, transit_routes, web_acls, direct_connect)


def collect(region, profile=None, expected_account=None, session=None, role_arn=None, external_id=None, workers=8, prepare=None):
    """prepare(session) is applied to every session the collection uses, incl. the assumed role's (browser egress)."""
    prepare = prepare or (lambda s: s)
    session = prepare(session or boto3.Session(profile_name=profile, region_name=region))
    # Adaptive retries back off client-side when an account is throttled during large collections.
    config = Config(retries={"mode": "adaptive", "total_max_attempts": 8}, connect_timeout=10, read_timeout=30,
                    max_pool_connections=16)
    started = now()
    if role_arn:
        session = prepare(assume(session, role_arn, external_id, region, config))
    identity = session.client("sts", region_name=region, config=config).get_caller_identity()
    account = identity["Account"]
    if expected_account and account != expected_account:
        raise ValueError(f"계정 불일치: 요청 {expected_account}, 현재 인증 {account}")
    clients = {service: session.client(service, region_name=region, config=config)
               for service in sorted({service for service, *_ in OPERATIONS} | {"wafv2"})}
    # Web ACLs for CloudFront are served from the partition's global WAF region (us-east-1 in aws).
    partition = identity["Arn"].split(":")[1]
    clients["wafv2/global"] = session.client("wafv2", region_name=GLOBAL_REGION.get(partition, "us-east-1"), config=config)

    def call(log, service, operation, key, params=None, client=None):
        rows, result = query(clients[client or service], operation, key, params)
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

    # Phase 2: per-resource detail, again one thread per service.
    detail_logs = [[] for _ in DETAILS]
    with ThreadPoolExecutor(max_workers=workers) as pool:
        updates = list(pool.map(lambda pair: pair[0](raw, call, pair[1]), zip(DETAILS, detail_logs)))
    for update, log in zip(updates, detail_logs):
        raw.update(update)
        coverage += log
    for name in ("eks_cluster_names", "opensearch_domain_names", "ecs_cluster_arns", "sagemaker_endpoint_names", "sagemaker_cluster_names"):
        raw.pop(name)
    if any(c["status"] != "complete" for c in coverage if c["operation"] == "describe_target_groups"):
        coverage.append({"service": "elbv2", "operation": "describe_target_health",
                         "parameters": {}, "status": "not_discovered",
                         "reason": "대상 그룹 조회가 불완전하여 미발견 그룹의 대상은 조회하지 못했습니다."})
    return {"schema_version": 1, "metadata": {
        "account_id": account, "region": region, "profile": profile, "role_arn": role_arn,
        "partition": partition,
        "started_at": started, "finished_at": now(), "demo": False,
        "status": "complete" if all(c["status"] == "complete" for c in coverage) else "partial",
        "scope": SCOPE, "limitations": LIMITATIONS},
        "coverage": coverage, "raw": raw}
