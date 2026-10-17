"""Deterministic architecture review: reliability and security checks on collected API fields only.

Every finding names the resource, the API fields it was decided from and a recommendation. A check
that lacks its input (permission denied, field absent) reports nothing rather than guessing; the
coverage finding lists what could not be read.
"""
import hashlib

SEVERITIES = ("high", "medium", "low")
PILLARS = {"reliability": "안정성", "security": "보안", "operations": "운영"}
# Ports that should never be open to the internet: remote administration and data stores.
ADMIN_PORTS = {22: "SSH", 3389: "RDP"}
DATA_PORTS = {3306: "MySQL", 5432: "PostgreSQL", 1433: "SQL Server", 1521: "Oracle", 6379: "Redis", 11211: "Memcached",
              9200: "OpenSearch", 27017: "MongoDB/DocumentDB", 9092: "Kafka", 5439: "Redshift", 2049: "NFS"}
WORLD = {"0.0.0.0/0", "::/0"}


def review(graph, coverage=()):
    nodes = {n["id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    found = []

    def add(rule, severity, pillar, key, title, evidence, recommendation):
        node = nodes.get(key, {})
        found.append({"id": "f-" + hashlib.sha256(f"{rule}|{key}|{title}".encode()).hexdigest()[:16], "rule": rule, "severity": severity,
                      "pillar": pillar, "resource": key, "resource_name": node.get("name", key), "kind": node.get("kind"),
                      "title": title, "evidence": evidence, "recommendation": recommendation})

    def out(key, relation):
        return [e for e in edges if e["source"] == key and e["relation"] == relation]

    collected = lambda kind: [n for n in graph["nodes"] if n["kind"] == kind and not n["reference_only"]]
    subnet_az = {n["id"]: n["attributes"].get("AvailabilityZone") for n in collected("subnet")}
    zone_of = {n["resource_id"]: n["attributes"].get("AvailabilityZone") for n in collected("subnet")}
    subnet_of = {e["source"]: e["target"] for e in edges if e["relation"] == "in_subnet"}

    # --- Reliability -------------------------------------------------------------------------
    for lb in collected("load_balancer"):
        zones = {z.get("ZoneName") for z in lb["attributes"].get("AvailabilityZones", [])} - {None}
        if len(zones) < 2:
            add("REL-LB-AZ", "high", "reliability", lb["id"], "로드밸런서가 한 AZ에서만 동작",
                f"DescribeLoadBalancers.AvailabilityZones = {sorted(zones) or '없음'}",
                "두 개 이상의 AZ에 서브넷을 지정해 AZ 장애 시에도 트래픽을 받도록 하세요.")
    for group in collected("target_group"):
        registrations = out(group["id"], "registered_target")
        if not registrations:
            continue
        zones = set()
        for e in registrations:
            target = nodes.get(e["target"], {})
            zone = e["qualifiers"].get("availability_zone") or target.get("attributes", {}).get("Placement", {}).get("AvailabilityZone") \
                or target.get("attributes", {}).get("availabilityZone")
            if zone and zone != "all":
                zones.add(zone)
        if len(zones) == 1:
            add("REL-TG-AZ", "medium", "reliability", group["id"], "대상 그룹의 모든 대상이 한 AZ에 있음",
                f"DescribeTargetHealth 대상 {len(registrations)}개의 AZ = {sorted(zones)[0]}",
                "대상을 두 개 이상의 AZ에 분산하세요 (Auto Scaling 그룹·ECS 서비스의 서브넷을 여러 AZ로).")
        unhealthy = [e for e in registrations if (e["details"].get("TargetHealth") or {}).get("State") in ("unhealthy", "unavailable")]
        if unhealthy:
            add("REL-TG-UNHEALTHY", "medium", "reliability", group["id"], f"비정상 대상 {len(unhealthy)}개",
                "DescribeTargetHealth.TargetHealth.State = unhealthy/unavailable (조회 시점)",
                "헬스 체크 경로·보안 그룹·애플리케이션 상태를 확인하세요.")
    for group in collected("auto_scaling_group"):
        subnets = [s for s in (group["attributes"].get("VPCZoneIdentifier") or "").split(",") if s]
        zones = {zone_of.get(s) for s in subnets} - {None}
        if subnets and len(zones) == 1:
            add("REL-ASG-AZ", "medium", "reliability", group["id"], "Auto Scaling 그룹이 한 AZ의 서브넷만 사용",
                f"DescribeAutoScalingGroups.VPCZoneIdentifier → AZ {sorted(zones)[0]}",
                "여러 AZ의 서브넷을 지정해 AZ 장애 시 다른 AZ에서 인스턴스를 띄울 수 있게 하세요.")
    for db in collected("db_instance"):
        a = db["attributes"]
        if not a.get("MultiAZ") and not a.get("DBClusterIdentifier") and not a.get("ReadReplicaSourceDBInstanceIdentifier"):
            add("REL-RDS-SINGLE-AZ", "medium", "reliability", db["id"], "RDS 인스턴스가 Single-AZ",
                "DescribeDBInstances.MultiAZ = false, 클러스터·복제본 아님",
                "운영 DB는 Multi-AZ 배포(동기식 대기) 또는 Multi-AZ DB 클러스터를 사용하세요.")
    for cluster in collected("db_cluster"):
        members = out(cluster["id"], "cluster_member")
        zones = {nodes.get(e["target"], {}).get("attributes", {}).get("AvailabilityZone") for e in members} - {None}
        engine = str(cluster["attributes"].get("Engine", ""))
        if len(members) == 1:
            add("REL-CLUSTER-SINGLE", "medium", "reliability", cluster["id"], "DB 클러스터에 writer만 있고 reader 없음",
                f"DescribeDBClusters.DBClusterMembers = 1 ({engine})",
                "다른 AZ에 reader를 두어 장애 조치 대상을 확보하세요.")
        elif len(members) > 1 and len(zones) == 1:
            add("REL-CLUSTER-AZ", "medium", "reliability", cluster["id"], "DB 클러스터 멤버가 모두 한 AZ",
                f"DBClusterMembers의 AvailabilityZone = {sorted(zones)[0]}", "reader를 다른 AZ에 배치하세요.")
    for group in collected("cache_replication_group"):
        a = group["attributes"]
        if a.get("AutomaticFailover") not in (None, "enabled", "enabling"):
            add("REL-CACHE-FAILOVER", "medium", "reliability", group["id"], "ElastiCache 자동 장애 조치 꺼짐",
                f"DescribeReplicationGroups.AutomaticFailover = {a.get('AutomaticFailover')}, MultiAZ = {a.get('MultiAZ')}",
                "replica를 다른 AZ에 두고 자동 장애 조치와 Multi-AZ를 켜세요.")
    for cache in collected("cache_node"):
        a = cache["attributes"]
        if a.get("Engine") in ("redis", "valkey") and not a.get("ReplicationGroupId"):
            add("REL-CACHE-STANDALONE", "medium", "reliability", cache["id"], "복제 그룹 없는 단일 캐시 노드",
                "DescribeCacheClusters.ReplicationGroupId 없음", "replica가 있는 복제 그룹으로 구성하세요.")
    nat_az = {n["resource_id"]: subnet_az.get(subnet_of.get(n["id"])) for n in collected("nat_gateway")}
    for table in collected("route_table"):
        routes = [e for e in out(table["id"], "routes_to") if nodes.get(e["target"], {}).get("kind") == "nat_gateway"
                  and e["qualifiers"].get("destination") == "0.0.0.0/0"]
        for route in routes:
            nat = nodes[route["target"]]["resource_id"]
            zones = {subnet_az.get(e["target"]) for e in out(table["id"], "associated_subnet")} - {None, nat_az.get(nat)}
            if nat_az.get(nat) and zones:
                add("REL-NAT-CROSS-AZ", "low", "reliability", table["id"], "다른 AZ의 NAT 게이트웨이에 의존하는 프라이빗 서브넷",
                    f"라우팅 테이블 0.0.0.0/0 → {nat} ({nat_az[nat]}), 연결 서브넷 AZ {sorted(zones)}",
                    "AZ마다 NAT 게이트웨이를 두면 한 AZ 장애가 다른 AZ의 외부 통신을 끊지 않습니다 (비용과 절충).")
    for domain in collected("opensearch_domain"):
        config = domain["attributes"].get("ClusterConfig") or {}
        if config and not config.get("ZoneAwarenessEnabled"):
            add("REL-SEARCH-AZ", "medium", "reliability", domain["id"], "OpenSearch 영역 인식(Multi-AZ) 꺼짐",
                "DescribeDomains.ClusterConfig.ZoneAwarenessEnabled = false", "2~3개 AZ에 노드를 분산하고 복제본을 두세요.")
        if config and (not config.get("DedicatedMasterEnabled") or config.get("DedicatedMasterCount", 0) < 3):
            add("REL-SEARCH-MASTER", "low", "reliability", domain["id"], "OpenSearch 전용 마스터 노드 3개 미만",
                f"DedicatedMasterEnabled = {config.get('DedicatedMasterEnabled')}, DedicatedMasterCount = {config.get('DedicatedMasterCount')}",
                "운영 도메인은 서로 다른 AZ에 전용 마스터 노드 3개를 권장합니다.")
    for cluster in collected("msk_cluster"):
        subnets = ((cluster["attributes"].get("Provisioned") or {}).get("BrokerNodeGroupInfo") or {}).get("ClientSubnets", [])
        if subnets and len(subnets) < 3:
            add("REL-MSK-AZ", "low", "reliability", cluster["id"], "MSK 브로커가 3개 미만 AZ에 분산",
                f"BrokerNodeGroupInfo.ClientSubnets = {len(subnets)}개", "세 AZ에 브로커를 두고 복제 계수 3을 사용하세요.")
    for cluster in collected("redshift_cluster"):
        if cluster["resource_id"].endswith("#secondary"):
            continue
        if str(cluster["attributes"].get("MultiAZ", "Disabled")).lower() != "enabled":
            add("REL-REDSHIFT-AZ", "low", "reliability", cluster["id"], "Redshift Single-AZ",
                "DescribeClusters.MultiAZ = Disabled", "RA3 클러스터는 Multi-AZ 배포로 AZ 장애에 대비할 수 있습니다.")
    for system in collected("efs_file_system"):
        if system["attributes"].get("AvailabilityZoneName"):
            add("REL-EFS-ONE-ZONE", "low", "reliability", system["id"], "EFS One Zone 파일 시스템",
                f"DescribeFileSystems.AvailabilityZoneName = {system['attributes']['AvailabilityZoneName']}",
                "AZ 장애에도 접근해야 하는 데이터는 Regional(Standard) 클래스를 사용하세요.")
    for edge in edges:
        if edge["relation"] == "vpn_connection":
            up, _, total = str(edge["details"].get("Tunnels", "")).partition(" ")[0].partition("/")
            if up.isdigit() and total.isdigit() and int(up) < int(total):
                add("REL-VPN-TUNNEL", "high" if int(up) == 0 else "medium", "reliability", edge["target"],
                    f"VPN 터널 {up}/{total}개만 UP ({edge['details'].get('VpnConnectionId')})",
                    "DescribeVpnConnections.VgwTelemetry[].Status (조회 시점)",
                    "두 터널을 모두 연결하고 BGP로 경로를 받아 한 터널 장애에도 연결을 유지하세요.")
    for service in collected("ecs_service"):
        a = service["attributes"]
        if a.get("desiredCount", 0) and a.get("runningCount", 0) < a["desiredCount"]:
            add("REL-ECS-RUNNING", "medium", "reliability", service["id"], "ECS 실행 태스크가 원하는 수보다 적음",
                f"DescribeServices runningCount {a.get('runningCount')} / desiredCount {a['desiredCount']} (조회 시점)",
                "서비스 이벤트와 태스크 중지 사유를 확인하세요.")
        zones = {nodes.get(e["target"], {}).get("attributes", {}).get("availabilityZone") for e in out(service["id"], "service_task")} - {None}
        if a.get("desiredCount", 0) >= 2 and len(zones) == 1:
            add("REL-ECS-AZ", "medium", "reliability", service["id"], "ECS 서비스 태스크가 모두 한 AZ",
                f"DescribeTasks.availabilityZone = {sorted(zones)[0]}", "서비스 서브넷을 여러 AZ로 지정하고 AZ 재조정을 켜세요.")

    # --- Security ----------------------------------------------------------------------------
    for db in collected("db_instance"):
        a = db["attributes"]
        if a.get("PubliclyAccessible"):
            add("SEC-RDS-PUBLIC", "high", "security", db["id"], "DB 인스턴스가 퍼블릭 접근 가능",
                "DescribeDBInstances.PubliclyAccessible = true", "DB는 프라이빗 서브넷에 두고 PubliclyAccessible을 끄세요.")
        if a.get("StorageEncrypted") is False:
            add("SEC-ENCRYPTION", "medium", "security", db["id"], "DB 스토리지 암호화 안 됨",
                "DescribeDBInstances.StorageEncrypted = false", "KMS 암호화된 스냅샷으로 복원해 암호화하세요.")
    for key, kind, field, value, api in (
            ("efs_file_system", "EFS", "Encrypted", False, "DescribeFileSystems.Encrypted"),
            ("redshift_cluster", "Redshift", "Encrypted", False, "DescribeClusters.Encrypted"),
            ("cache_replication_group", "ElastiCache", "AtRestEncryptionEnabled", False, "DescribeReplicationGroups.AtRestEncryptionEnabled")):
        for n in collected(key):
            if n["attributes"].get(field) is value and not n["resource_id"].endswith("#secondary"):
                add("SEC-ENCRYPTION", "medium", "security", n["id"], f"{kind} 저장 데이터 암호화 안 됨", f"{api} = false",
                    "저장 데이터 암호화(KMS)를 켠 리소스로 교체하세요.")
    for domain in collected("opensearch_domain"):
        a = domain["attributes"]
        if (a.get("EncryptionAtRestOptions") or {}).get("Enabled") is False:
            add("SEC-ENCRYPTION", "medium", "security", domain["id"], "OpenSearch 저장 데이터 암호화 안 됨",
                "DescribeDomains.EncryptionAtRestOptions.Enabled = false", "저장 데이터 암호화를 켜세요.")
        if not (a.get("VPCOptions") or {}).get("SubnetIds"):
            add("SEC-SEARCH-PUBLIC", "medium", "security", domain["id"], "OpenSearch 도메인이 VPC 밖 퍼블릭 엔드포인트",
                "DescribeDomains.VPCOptions 없음", "VPC 액세스로 생성하거나 세분화된 액세스 제어와 IP 제한을 적용하세요.")
    for group in collected("cache_replication_group"):
        if group["attributes"].get("TransitEncryptionEnabled") is False:
            add("SEC-TRANSIT", "low", "security", group["id"], "ElastiCache 전송 중 암호화 꺼짐",
                "DescribeReplicationGroups.TransitEncryptionEnabled = false", "TLS(전송 중 암호화)를 켜세요.")
    for cluster in collected("msk_cluster"):
        tls = (((cluster["attributes"].get("Provisioned") or {}).get("EncryptionInfo") or {}).get("EncryptionInTransit") or {}).get("ClientBroker")
        if tls in ("PLAINTEXT", "TLS_PLAINTEXT"):
            add("SEC-TRANSIT", "low", "security", cluster["id"], "MSK 클라이언트-브로커 평문 허용",
                f"EncryptionInTransit.ClientBroker = {tls}", "ClientBroker를 TLS로 제한하세요.")
    for instance in collected("ec2"):
        a = instance["attributes"]
        if (a.get("MetadataOptions") or {}).get("HttpTokens") == "optional":
            add("SEC-IMDSV1", "medium", "security", instance["id"], "IMDSv1 허용 (IMDSv2 강제 안 됨)",
                "DescribeInstances.MetadataOptions.HttpTokens = optional", "HttpTokens를 required로 바꿔 IMDSv2만 허용하세요.")
        if a.get("PublicIpAddress"):
            add("SEC-EC2-PUBLIC-IP", "low", "security", instance["id"], "인스턴스에 퍼블릭 IP 직접 할당",
                f"DescribeInstances.PublicIpAddress = {a['PublicIpAddress']}",
                "로드밸런서·NAT 뒤 프라이빗 서브넷에 두고 관리 접근은 Session Manager를 사용하세요.")
    for group in collected("security_group"):
        for rule in group["attributes"].get("IpPermissions", []):
            sources = {r.get("CidrIp") for r in rule.get("IpRanges", [])} | {r.get("CidrIpv6") for r in rule.get("Ipv6Ranges", [])}
            if not sources & WORLD:
                continue
            protocol = str(rule.get("IpProtocol"))
            low, high = rule.get("FromPort", 0), rule.get("ToPort", 65535)
            if protocol == "-1":
                exposed = {"모든 포트": None}
            else:
                exposed = {name: port for port, name in {**ADMIN_PORTS, **DATA_PORTS}.items() if low <= port <= high}
            for name, port in exposed.items():
                add("SEC-SG-OPEN", "high", "security", group["id"], f"인터넷(0.0.0.0/0)에 {name}{f' {port}' if port else ''} 허용",
                    f"DescribeSecurityGroups.IpPermissions: {protocol} {low}-{high} ← {', '.join(sorted(sources & WORLD))}",
                    "관리 접근은 Session Manager, 데이터 저장소는 애플리케이션 보안 그룹만 허용하도록 좁히세요.")
    for cluster in collected("eks_cluster"):
        config = cluster["attributes"].get("resourcesVpcConfig") or {}
        if config.get("endpointPublicAccess") and "0.0.0.0/0" in config.get("publicAccessCidrs", ["0.0.0.0/0"]):
            add("SEC-EKS-PUBLIC", "medium", "security", cluster["id"], "EKS API 엔드포인트가 인터넷 전체에 공개",
                "DescribeCluster.resourcesVpcConfig.endpointPublicAccess = true, publicAccessCidrs = 0.0.0.0/0",
                "프라이빗 엔드포인트를 쓰거나 publicAccessCidrs를 회사 대역으로 제한하세요.")

    # --- Operations --------------------------------------------------------------------------
    missing = [c for c in coverage if c.get("status") not in ("complete", None)]
    if missing:
        permissions = sorted({c.get("permission") for c in missing if c.get("permission")})
        found.append({"id": "f-coverage", "rule": "OPS-COVERAGE", "severity": "low", "pillar": "operations", "resource": None,
                      "resource_name": "수집 범위", "kind": None, "title": f"조회하지 못한 API {len(missing)}건 — 검토 결과가 불완전할 수 있음",
                      "evidence": ", ".join(f"{c['operation']}({c.get('error_code', c['status'])})" for c in missing[:8]),
                      "recommendation": "읽기 전용 권한 추가 후 다시 수집하세요: " + (", ".join(permissions) or "권한 확인")})
    order = {s: i for i, s in enumerate(SEVERITIES)}
    unique = {f["id"]: f for f in found}
    return sorted(unique.values(), key=lambda f: (order[f["severity"]], f["pillar"], f["rule"], f["resource_name"] or ""))


def summary(findings):
    return {s: sum(1 for f in findings if f["severity"] == s) for s in SEVERITIES}


def markdown(findings):
    counts = summary(findings)
    lines = ["## 아키텍처 검토 결과", "", f"높음 {counts['high']} · 중간 {counts['medium']} · 낮음 {counts['low']} — 수집된 설정만으로 판정했으며 "
             "실제 트래픽·애플리케이션 수준 이중화는 별도 확인이 필요합니다.", ""]
    if not findings:
        return lines + ["- 해당 항목 없음", ""]
    lines += ["| 심각도 | 영역 | 규칙 | 리소스 | 내용 | 근거 | 권장 조치 |", "| --- | --- | --- | --- | --- | --- | --- |"]
    label = {"high": "높음", "medium": "중간", "low": "낮음"}
    for f in findings:
        cells = [label[f["severity"]], PILLARS[f["pillar"]], f["rule"], f["resource_name"] or "-", f["title"], f["evidence"], f["recommendation"]]
        lines.append("| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in cells) + " |")
    return lines + [""]
