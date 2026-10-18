"""Deterministic architecture review: reliability and security checks on collected API fields only.

Every finding names the resource, the API fields it was decided from and a recommendation. A check
that lacks its input (permission denied, field absent) reports nothing rather than guessing; the
coverage finding lists what could not be read.
"""
import hashlib
import re

SEVERITIES = ("high", "medium", "low")
PILLARS = {"reliability": "안정성", "security": "보안", "operations": "운영", "ai": "AI/ML", "cost": "비용"}
# Ports that should never be open to the internet: remote administration and data stores.
ADMIN_PORTS = {22: "SSH", 3389: "RDP"}
DATA_PORTS = {3306: "MySQL", 5432: "PostgreSQL", 1433: "SQL Server", 1521: "Oracle", 6379: "Redis", 11211: "Memcached",
              9200: "OpenSearch", 27017: "MongoDB/DocumentDB", 9092: "Kafka", 5439: "Redshift", 2049: "NFS"}
WORLD = {"0.0.0.0/0", "::/0"}
# EFA-capable training families (tightly coupled; EFA traffic cannot cross AZs) and accelerated inference families.
TRAINING = re.compile(r"^(p4d|p5|p6|trn)")
INFERENCE = re.compile(r"^(g\d|inf)")
ACCELERATED = re.compile(r"^(p|g|trn|inf|dl)\d")
# Notebook, experiment-tracking, distributed-compute and model-serving consoles that should never face the internet.
AI_PORTS = {8888: "Jupyter", 6006: "TensorBoard", 8265: "Ray 대시보드", 10001: "Ray 클라이언트", 5000: "MLflow", 7860: "Gradio",
            8501: "Streamlit", 11434: "Ollama", 8000: "vLLM/Triton HTTP", 8001: "Triton gRPC"}
# IPs a node takes from its subnet (SageMaker HyperPod prerequisites: P5 on EKS 81, on Slurm 32).
NODE_IPS = {True: 81, False: 32}
ML_ENDPOINT = re.compile(r"\.(bedrock[a-z-]*|sagemaker\.[a-z.-]+)$")


def efa_node(attrs):
    """Tightly coupled training node: an EFA interface, or an EFA training family."""
    return any(i.get("InterfaceType") == "efa" for i in attrs.get("NetworkInterfaces", [])) or bool(TRAINING.match(str(attrs.get("InstanceType", ""))))


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
    def instance_types(group):
        return {nodes.get(e["target"], {}).get("attributes", {}).get("InstanceType", "") for e in out(group["id"], "asg_member")}

    for group in collected("auto_scaling_group"):
        subnets = [s for s in (group["attributes"].get("VPCZoneIdentifier") or "").split(",") if s]
        zones = {zone_of.get(s) for s in subnets} - {None}
        types = instance_types(group)
        training, inference = any(TRAINING.match(t) for t in types), any(INFERENCE.match(t) for t in types)
        evidence = f"DescribeAutoScalingGroups.VPCZoneIdentifier → AZ {', '.join(sorted(zones))} · InstanceType {', '.join(sorted(types))}"
        if training and len(zones) > 1:
            add("REL-GPU-TRAIN-AZ", "medium", "ai", group["id"], "EFA 학습 노드 그룹이 여러 AZ에 걸쳐 있음", evidence,
                "EFA 트래픽은 AZ를 넘지 못합니다. 강결합 학습 노드는 한 AZ(클러스터 배치 그룹 또는 Capacity Block)에 두고, "
                "체크포인트(FSx for Lustre·S3)로 다른 AZ에서 다시 시작하세요.")
        elif training and len(zones) == 1:
            add("REL-ASG-AZ", "low", "ai", group["id"], "학습 노드 그룹이 한 AZ에만 있음 (강결합 학습은 의도적 단일 AZ)", evidence,
                "EFA 학습은 단일 AZ가 맞습니다. 체크포인트 저장소와 다른 AZ에서 다시 시작하는 경로(IaC·용량)를 확인하세요.")
        elif inference and len(zones) == 1:
            add("REL-GPU-INFER-AZ", "medium", "ai", group["id"], "GPU 추론 노드 그룹이 한 AZ에만 있음", evidence,
                "추론 노드를 두 개 이상의 AZ에 분산하고 로드 밸런서 뒤에서 자동 확장하세요 (GENREL02-BP01).")
        elif subnets and len(zones) == 1:
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
        if a.get("PublicIpAddress") and not ACCELERATED.match(str(a.get("InstanceType", ""))):
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
                for port, name in AI_PORTS.items():
                    if low <= port <= high:
                        add("SEC-AI-PORT-OPEN", "high", "ai", group["id"], f"인터넷(0.0.0.0/0)에 {name} {port} 허용",
                            f"DescribeSecurityGroups.IpPermissions: {protocol} {low}-{high} ← {', '.join(sorted(sources & WORLD))}",
                            "노트북·실험 추적·분산 컴퓨팅 콘솔과 모델 서버는 인터넷에 열지 말고 SSM 포트 포워딩·내부 ALB·VPC 엔드포인트로만 접근하세요 "
                            "(ML Lens MLSEC03-BP02: ML 환경의 무제한 인터넷 접근은 안티 패턴).")
            for name, port in exposed.items():
                add("SEC-SG-OPEN", "high", "security", group["id"], f"인터넷(0.0.0.0/0)에 {name}{f' {port}' if port else ''} 허용",
                    f"DescribeSecurityGroups.IpPermissions: {protocol} {low}-{high} ← {', '.join(sorted(sources & WORLD))}",
                    "관리 접근은 Session Manager, 데이터 저장소는 애플리케이션 보안 그룹만 허용하도록 좁히세요.")
    groups = {g["resource_id"]: g for g in collected("security_group")}
    in_group = {e["target"]: e["source"] for e in edges if e["relation"] == "asg_member"}
    reported = set()
    for instance in collected("ec2"):
        a = instance["attributes"]
        sgs = [g.get("GroupId") for g in a.get("SecurityGroups", [])]
        # Judged only when every attached group was collected; one finding per Auto Scaling group.
        if not efa_node(a) or not sgs or any(sg not in groups for sg in sgs):
            continue
        self_all = any(str(rule.get("IpProtocol")) == "-1" and any(pair.get("GroupId") == sg for pair in rule.get("UserIdGroupPairs", []))
                       for sg in sgs for rule in groups[sg]["attributes"].get("IpPermissions", []))
        key = in_group.get(instance["id"], instance["id"])
        if not self_all and key not in reported:
            reported.add(key)
            add("SEC-EFA-SG", "medium", "ai", key, "EFA 학습 노드의 보안 그룹에 자기 참조 전체 허용 규칙 없음",
                f"DescribeSecurityGroups.IpPermissions ({', '.join(sgs)}): IpProtocol -1 ← 같은 그룹 없음",
                "EFA 인스턴스는 같은 보안 그룹을 쓰고 그 그룹 자신에서 오는 모든 트래픽을 허용해야 합니다. 없으면 EFA 통신이 조용히 실패합니다.")
    for cluster in collected("eks_cluster"):
        config = cluster["attributes"].get("resourcesVpcConfig") or {}
        if config.get("endpointPublicAccess") and "0.0.0.0/0" in config.get("publicAccessCidrs", ["0.0.0.0/0"]):
            add("SEC-EKS-PUBLIC", "medium", "security", cluster["id"], "EKS API 엔드포인트가 인터넷 전체에 공개",
                "DescribeCluster.resourcesVpcConfig.endpointPublicAccess = true, publicAccessCidrs = 0.0.0.0/0",
                "프라이빗 엔드포인트를 쓰거나 publicAccessCidrs를 회사 대역으로 제한하세요.")

    ai_review(graph, nodes, edges, collected, out, add)
    edge_review(nodes, edges, collected, coverage, add)

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


def ai_review(graph, nodes, edges, collected, out, add):
    """AI/ML workload checks (Well-Architected Generative AI and Machine Learning lenses, HPC lens), from API fields only."""
    subnets = {n["resource_id"]: n for n in collected("subnet")}
    zone = lambda subnet: (subnets.get(subnet) or {}).get("attributes", {}).get("AvailabilityZone")
    vpc_of = {e["source"]: e["target"] for e in edges if e["relation"] == "in_vpc"}
    group_of = {e["target"]: e["source"] for e in edges if e["relation"] == "asg_member"}
    placement = {e["source"]: nodes.get(e["target"], {}) for e in edges if e["relation"] == "in_placement_group"}
    instances = collected("ec2")
    accelerated = [i for i in instances if ACCELERATED.match(str(i["attributes"].get("InstanceType", "")))]
    once = set()

    def per_group(rule, instance):
        """One finding per Auto Scaling group (or per instance outside a group)."""
        key = group_of.get(instance["id"], instance["id"])
        if (rule, key) in once:
            return None
        once.add((rule, key))
        return key

    for instance in accelerated:
        a = instance["attributes"]
        subnet = subnets.get(a.get("SubnetId"))
        public = a.get("PublicIpAddress") or (subnet and subnet.get("route_class") == "public")
        if public and (key := per_group("SEC-GPU-PUBLIC", instance)):
            add("SEC-GPU-PUBLIC", "high", "ai", key, "GPU/가속 인스턴스가 인터넷에 직접 노출",
                f"DescribeInstances.PublicIpAddress = {a.get('PublicIpAddress') or '없음'} · 서브넷 경로 = {(subnet or {}).get('route_class', '?')}",
                "GPU 노드는 프라이빗 서브넷에 두고 모델·데이터 접근은 VPC 엔드포인트(S3·ECR·Bedrock)로, 관리 접근은 Session Manager로 하세요 (MLSEC03-BP02, GENSEC01-BP02).")
        if not efa_node(a):
            continue
        if a.get("InstanceLifecycle") == "spot" and (key := per_group("REL-GPU-SPOT-EFA", instance)):
            add("REL-GPU-SPOT-EFA", "medium", "ai", key, "EFA 학습 노드가 Spot 인스턴스",
                f"DescribeInstances.InstanceLifecycle = spot · InstanceType {a.get('InstanceType')}",
                "같은 AZ·배치 그룹에 모인 EFA 노드는 Spot 회수가 한꺼번에 일어날 수 있습니다. 강결합 학습은 Capacity Block·ODCR·Savings Plans를 쓰고 "
                "Spot은 체크포인트가 잦은 소규모 작업에만 쓰세요 (EKS AI/ML Networking).")
        group = placement.get(instance["id"])
        clustered = group and group.get("attributes", {}).get("Strategy") == "cluster"
        known = group is None or group.get("attributes", {}).get("Strategy")  # a named group that was not collected: undecided
        if not clustered and known and a.get("InstanceLifecycle") != "capacity-block" and (key := per_group("OPS-GPU-PLACEMENT", instance)):
            add("OPS-GPU-PLACEMENT", "low", "ai", key, "EFA 학습 노드가 클러스터 배치 그룹·Capacity Block 밖에 있음",
                f"DescribeInstances.Placement.GroupName = {(a.get('Placement') or {}).get('GroupName') or '없음'} · InstanceLifecycle = {a.get('InstanceLifecycle') or '온디맨드'}",
                "노드 간 지연을 줄이려면 클러스터 배치 그룹(한 유형·한 번의 시작 요청)이나 Capacity Block(UltraCluster 자동 배치)을 쓰세요. "
                "둘은 함께 쓸 수 없습니다 (EC2 placement strategies, Capacity Blocks).")

    # IP headroom for GPU node groups that still scale out (each P5 takes 81 IPs on EKS, 32 elsewhere).
    for group in collected("auto_scaling_group"):
        members = [nodes[e["target"]] for e in out(group["id"], "asg_member") if e["target"] in nodes]
        p5 = [m for m in members if str(m["attributes"].get("InstanceType", "")).startswith("p5")]
        if not p5:
            continue
        eks = any(t.get("Key") == "eks:cluster-name" for t in p5[0]["attributes"].get("Tags", []))
        grow = max(0, int(group["attributes"].get("MaxSize") or 0) - len(members))
        chosen = [s for s in (group["attributes"].get("VPCZoneIdentifier") or "").split(",") if s]
        free = [subnets[s]["attributes"].get("AvailableIpAddressCount") for s in chosen if s in subnets]
        if grow and free and None not in free and sum(free) < grow * NODE_IPS[eks]:
            add("REL-GPU-SUBNET-IP", "medium", "ai", group["id"], "GPU 노드 그룹 확장에 서브넷 IP가 부족",
                f"MaxSize {group['attributes'].get('MaxSize')} − 현재 {len(members)} = {grow}대 × {NODE_IPS[eks]} IP > 가용 {sum(free)} "
                f"(DescribeSubnets.AvailableIpAddressCount)",
                "서브넷 CIDR은 나중에 늘릴 수 없습니다. GPU 노드용 큰 서브넷(/20 이상)이나 보조 CIDR(100.64.0.0/10)·접두사 위임을 쓰세요 "
                "(SageMaker HyperPod 사전 조건, EKS IP 최적화).")

    # Private access to Bedrock and SageMaker from every AZ where clients run.
    compute_azs = {}
    for n in instances + collected("ecs_task"):
        az = n["attributes"].get("Placement", {}).get("AvailabilityZone") or n["attributes"].get("availabilityZone")
        if az and n["id"] in vpc_of:
            compute_azs.setdefault(vpc_of[n["id"]], set()).add(az)
    for endpoint in collected("vpc_endpoint"):
        a = endpoint["attributes"]
        match = ML_ENDPOINT.search(str(a.get("ServiceName", "")))
        if not match or a.get("VpcEndpointType") != "Interface":
            continue
        if a.get("PrivateDnsEnabled") is False:
            add("SEC-AI-ENDPOINT-DNS", "medium", "ai", endpoint["id"], f"{match.group(1)} 엔드포인트의 프라이빗 DNS 꺼짐",
                "DescribeVpcEndpoints.PrivateDnsEnabled = false",
                "SDK의 기본 호출이 공용 엔드포인트로 나갑니다. 프라이빗 DNS를 켜서 모델 호출이 VPC 안에 머물게 하세요 (GENSEC01-BP02).")
        covered = {zone(s) for s in a.get("SubnetIds", [])} - {None}
        missing = compute_azs.get(vpc_of.get(endpoint["id"]), set()) - covered
        if covered and missing:
            add("REL-AI-ENDPOINT-AZ", "medium", "ai", endpoint["id"], f"{match.group(1)} 엔드포인트가 클라이언트가 있는 AZ 일부에 없음",
                f"DescribeVpcEndpoints.SubnetIds → AZ {', '.join(sorted(covered))} · 컴퓨팅 AZ {', '.join(sorted(missing))} 없음",
                "모델을 호출하는 컴퓨팅이 있는 모든 AZ에 엔드포인트 서브넷을 두세요. AZ 장애 시 호출 경로가 함께 끊깁니다 "
                "(GENREL02-BP01, SageMaker 런타임 엔드포인트는 클라이언트 AZ마다 필요).")

    # Data next to training: FSx for Lustre in the AZ of the training nodes, persistent rather than scratch.
    lustre = [f for f in collected("fsx_file_system") if f["attributes"].get("FileSystemType") == "LUSTRE"]
    lustre_azs = {}
    for system in lustre:
        lustre_azs.setdefault(vpc_of.get(system["id"]), set()).update(zone(s) for s in system["attributes"].get("SubnetIds", []))
        deployment = (system["attributes"].get("LustreConfiguration") or {}).get("DeploymentType", "")
        if deployment.startswith("SCRATCH"):
            add("OPS-FSX-SCRATCH", "low", "ai", system["id"], f"FSx for Lustre {deployment}: 데이터 복제 없음",
                f"DescribeFileSystems.LustreConfiguration.DeploymentType = {deployment}",
                "Scratch는 서버 장애 시 데이터가 남지 않습니다. 체크포인트·장기 데이터는 PERSISTENT_2와 S3 연동(데이터 리포지토리)에 두세요 (GENREL06-BP01).")
    for instance in instances:
        a = instance["attributes"]
        vpc = vpc_of.get(instance["id"])
        azs = lustre_azs.get(vpc)
        if efa_node(a) and azs and (a.get("Placement") or {}).get("AvailabilityZone") not in azs and (key := per_group("REL-FSX-TRAIN-AZ", instance)):
            add("REL-FSX-TRAIN-AZ", "medium", "ai", key, "학습 노드와 FSx for Lustre가 다른 AZ",
                f"학습 노드 AZ {(a.get('Placement') or {}).get('AvailabilityZone')} · Lustre AZ {', '.join(sorted(z for z in azs if z))}",
                "Lustre는 학습 노드와 같은 AZ에 두세요. AZ 간 트래픽은 지연·비용이 늘고 그 AZ 장애에 학습이 묶입니다 (HPCREL01-BP01, MLCOST06-BP03).")
    endpoints_s3 = {vpc_of.get(e["id"]) for e in collected("vpc_endpoint") if str(e["attributes"].get("ServiceName", "")).endswith(".s3")
                    and e["attributes"].get("VpcEndpointType") == "Gateway"}
    for vpc in sorted({vpc_of.get(i["id"]) for i in accelerated} - endpoints_s3 - {None}):
        add("OPS-GPU-S3-ENDPOINT", "low", "ai", vpc, "GPU 워크로드 VPC에 S3 게이트웨이 엔드포인트 없음",
            "DescribeVpcEndpoints: com.amazonaws.<region>.s3 Gateway 없음",
            "데이터셋·모델 가중치·체크포인트를 NAT로 주고받으면 비용이 크고 인터넷 경로를 탑니다. S3 게이트웨이 엔드포인트를 추가하세요 (MLSEC03-BP02).")

    # SageMaker: endpoints inside the VPC and spread over AZs; HyperPod in private subnets.
    for endpoint in collected("sagemaker_endpoint"):
        a = endpoint["attributes"]
        chosen = a.get("Subnets") or []
        if not chosen:
            add("SEC-SAGEMAKER-VPC", "medium", "ai", endpoint["id"], "SageMaker 엔드포인트가 VPC 밖(서비스 네트워크)에서 실행",
                "DescribeModel.VpcConfig 없음", "모델에 VpcConfig(프라이빗 서브넷·보안 그룹)를 지정해 데이터 경로를 VPC 안에 두세요 (MLSEC03-BP02).")
        azs = {zone(s) for s in chosen} - {None}
        if int(a.get("InstanceCount") or 0) < 2 or (chosen and len(azs) < 2):
            add("REL-SAGEMAKER-AZ", "medium", "ai", endpoint["id"], "SageMaker 엔드포인트가 AZ 장애를 견디지 못함",
                f"인스턴스 {a.get('InstanceCount')}대 · 서브넷 AZ {', '.join(sorted(azs)) or '서비스 관리'}",
                "인스턴스를 2대 이상으로 하고 서로 다른 AZ의 서브넷을 2개 이상 지정하세요. SageMaker가 인스턴스를 AZ에 나눠 배치합니다 (MLCOST06-BP03, MLREL05-BP01).")
    for cluster in collected("sagemaker_cluster"):
        chosen = (cluster["attributes"].get("VpcConfig") or {}).get("Subnets", [])
        if any((subnets.get(s) or {}).get("route_class") == "public" for s in chosen):
            add("SEC-HYPERPOD-PUBLIC", "high", "ai", cluster["id"], "HyperPod 클러스터가 퍼블릭 서브넷에 있음",
                "DescribeCluster.VpcConfig.Subnets → 0.0.0.0/0 → IGW",
                "HyperPod는 프라이빗 서브넷에 두고 S3 엔드포인트와 필요한 인터페이스 엔드포인트로 통신하세요 (GENSEC01-BP02, HyperPod 사전 조건).")


def waf_collected(coverage, node):
    """Regional WAF associations were read completely for the load balancer's account and region."""
    rows = [c for c in coverage if c.get("service") == "wafv2" and (c.get("parameters") or {}).get("Scope") != "CLOUDFRONT"
            and c.get("account_id", node["account"]) == node["account"] and c.get("region", node["region"]) == node["region"]]
    return any(c.get("operation") == "list_web_acls" for c in rows) and all(c.get("status") == "complete" for c in rows)


def edge_review(nodes, edges, collected, coverage, add):
    """Edge protection, static addresses and Transit Gateway routing, from API fields only (never a reachability test)."""
    fronts = {}
    for e in edges:
        if e["relation"] == "cloudfront_origin" and e["source"] in nodes:
            fronts.setdefault(e["target"], []).append(nodes[e["source"]])
    groups = {g["resource_id"]: g for g in collected("security_group")}
    for lb in collected("load_balancer"):
        a = lb["attributes"]
        if a.get("Type") != "application" or a.get("Scheme") != "internet-facing":
            continue
        edge_waf = [d for d in fronts.get(lb["id"], []) if d["attributes"].get("WebACLId")]
        if not lb.get("waf") and not edge_waf and waf_collected(coverage, lb):
            via = f" · 앞단 CloudFront {', '.join(d['name'] for d in fronts[lb['id']])}도 WebACLId 없음" if fronts.get(lb["id"]) else ""
            add("SEC-ALB-WAF", "low", "security", lb["id"], "인터넷 ALB에 AWS WAF 웹 ACL 없음",
                f"ListWebACLs(REGIONAL)·ListResourcesForWebACL: 이 ALB 없음 · DescribeLoadBalancers.Scheme = internet-facing{via}",
                "AWS WAF 웹 ACL(관리형 규칙: 핵심 규칙 세트·알려진 악성 입력·IP 평판)을 ALB에 연결하거나, WAF가 연결된 CloudFront 뒤에 두세요 "
                "(인터넷 진입점 보호, docs/ARCHITECTURE.md C6 · REL02-BP01).")
        sgs = a.get("SecurityGroups") or []
        if not fronts.get(lb["id"]) or not sgs or any(sg not in groups for sg in sgs):
            continue  # judged only for a CloudFront origin whose every security group was collected
        open_rules = [f"{sg}: {r.get('IpProtocol')} {r.get('FromPort', '*')}-{r.get('ToPort', '*')} ← {', '.join(sorted(sources))}"
                      for sg in sgs for r in groups[sg]["attributes"].get("IpPermissions", [])
                      if (sources := ({x.get("CidrIp") for x in r.get("IpRanges", [])} | {x.get("CidrIpv6") for x in r.get("Ipv6Ranges", [])}) & WORLD)]
        if open_rules:
            add("SEC-CF-ORIGIN-OPEN", "low", "security", lb["id"], "CloudFront 오리진 ALB에 인터넷(0.0.0.0/0)에서 직접 접근 허용 (설정상)",
                f"ListDistributions.Origins → 이 ALB ({', '.join(d['name'] for d in fronts[lb['id']])}) · DescribeSecurityGroups.IpPermissions {'; '.join(open_rules[:3])}",
                "CloudFront와 WAF를 우회해 ALB로 직접 들어올 수 있는 설정입니다. 보안 그룹을 CloudFront 관리형 접두사 목록"
                "(com.amazonaws.global.cloudfront.origin-facing)으로 좁히거나, CloudFront가 넣는 사용자 지정 헤더를 리스너 규칙에서 확인하거나, "
                "CloudFront VPC 오리진과 내부 ALB를 쓰세요. 실제 접근 시험은 하지 않았습니다.")
    joined = {e["source"] for e in edges if e["relation"] == "eip_associated"}
    for address in collected("elastic_ip"):
        a = address["attributes"]
        if not (a.get("AssociationId") or a.get("InstanceId") or a.get("NetworkInterfaceId") or address["id"] in joined):
            add("COST-EIP-IDLE", "low", "cost", address["id"], f"연결되지 않은 Elastic IP {a.get('PublicIp', '')}".strip(),
                f"DescribeAddresses: AssociationId·InstanceId·NetworkInterfaceId 없음 ({a.get('AllocationId') or a.get('PublicIp')})",
                "퍼블릭 IPv4 주소는 연결 여부와 관계없이 시간당 과금됩니다. 쓰지 않으면 해제(ReleaseAddress)하고, 고정 IP를 예약해 둔 것이면 "
                "태그로 용도와 담당을 남기세요.")
    for table in collected("tgw_route_table"):
        holes = [r for r in table["attributes"].get("Routes", []) if r.get("State") == "blackhole"]
        if holes:
            listed = ", ".join(f"{r.get('DestinationCidrBlock') or r.get('PrefixListId')} ({r.get('Type', '?')})" for r in holes[:5])
            add("REL-TGW-BLACKHOLE", "low", "reliability", table["id"], f"TGW 라우팅 테이블에 blackhole 경로 {len(holes)}개 (일치하는 트래픽은 버려짐)",
                f"SearchTransitGatewayRoutes(state=blackhole): {listed}",
                "스포크 간 격리처럼 의도한 차단이면 그대로 두고 설명 태그를 남기세요. 삭제된 attachment를 가리키다 남은 경로라면 정리하거나 "
                "올바른 attachment로 바꾸세요 (TGW 라우팅 테이블 격리 설계).")


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
