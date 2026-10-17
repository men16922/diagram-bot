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
]


def permission(service, operation):
    """IAM action needed for a read operation, shown next to denied or partial coverage."""
    prefix = {"ec2": "ec2", "elbv2": "elasticloadbalancing"}[service]
    return prefix + ":" + "".join(part.title() for part in operation.split("_"))


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
            rows.extend(page.get(key, []))
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
               for service in ("ec2", "elbv2")}
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
    if any(c["status"] != "complete" for c in coverage if c["operation"] == "describe_target_groups"):
        coverage.append({"service": "elbv2", "operation": "describe_target_health",
                         "parameters": {}, "status": "not_discovered",
                         "reason": "대상 그룹 조회가 불완전하여 미발견 그룹의 대상은 조회하지 못했습니다."})
    return {"schema_version": 1, "metadata": {
        "account_id": account, "region": region, "profile": profile,
        "partition": identity["Arn"].split(":")[1],
        "started_at": started, "finished_at": now(), "demo": False,
        "status": "complete" if all(c["status"] == "complete" for c in coverage) else "partial",
        "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget"],
        "limitations": ["설정 관계만 표시하며 실제 통신 여부는 확인하지 않습니다.",
                        "여러 API의 순차 조회 결과이며 단일 시점의 원자적 스냅샷이 아닙니다.",
                        "보안 그룹·ENI·EBS는 EC2/ELB 응답의 참조만 표시합니다. 별도 상세 조회는 하지 않습니다.",
                        "RDS·EKS·라우팅·리스너 규칙·실제 트래픽은 이번 조회 범위에 포함되지 않습니다."]},
        "coverage": coverage, "raw": raw}
