"""Chat agent (Strands Agents on Amazon Bedrock) that collects AWS read-only and draws standard architectures.

Two tool families, both plain functions so they are testable without a model:
- collection: boto3 read-only calls through the existing collector (STS identity, full collection, queries, review);
- design: intent-level steps on a `Design`, each saved as a new snapshot the workspace renders.
The model only chooses tools; every resource, relation and finding comes from code.
"""
import json
import os
import threading

import boto3

from .collector import collect
from .designer import STANDARDS, Design
from .findings import review, summary
from .graph import build_graph

MODELS = {
    "nova-2-lite": ("Amazon Nova 2 Lite", "us.amazon.nova-2-lite-v1:0"),
    "gpt-6-luna": ("OpenAI GPT-6 Luna", "global.openai.gpt-6-luna"),
    "claude-sonnet-4.5": ("Anthropic Claude Sonnet 4.5", "us.anthropic.claude-sonnet-4-5-20250929-v1:0"),
}
# Nova 2 Lite by the owner's choice (2026-10-17): GPT-6 Luna is not yet enabled for the account. Another chosen model
# falls back to Nova 2 Lite when Bedrock refuses it.
DEFAULT_MODEL = "nova-2-lite"
FALLBACK_MODEL = "nova-2-lite"

SYSTEM_PROMPT = """당신은 AWS 표준 인프라 아키텍처 설계 도우미입니다. 사용자는 한국어로 요청합니다.

원칙
- 구성도는 반드시 도구로만 그립니다. 리소스를 지어내 설명하지 말고, 도구 결과에 있는 내용만 말하세요.
- 조언할 때는 describe_design·review_design 결과에 실제로 있는 리소스만 다루고, 설계에 없는 서비스나 확인하지 않은 가격·수치는 말하지 마세요.
- 새 설계는 new_design → add_vpc → (로드 밸런서·서비스·데이터) 순서로 만듭니다. 표준 패턴을 원하면 list_standard_architectures 후 apply_standard_architecture를 씁니다.
- 기본값은 AWS Well-Architected 모범 사례: AZ 3개(최소 2개), 퍼블릭에는 로드 밸런서·NAT만, 앱·데이터는 프라이빗,
  DB는 Multi-AZ, AZ별 NAT, S3 게이트웨이·SSM 인터페이스 엔드포인트. 사용자가 비용 절감을 원하면 NAT 1개 등으로 조정하고 그 영향을 알려주세요.
- AI/GPU 워크로드(Well-Architected Generative AI·ML 렌즈): 추론 노드는 여러 AZ, EFA 학습 노드는 한 AZ의 전용 서브넷·배치 그룹
  (add_gpu_node_group training=true), 학습 데이터·체크포인트는 같은 AZ의 FSx for Lustre(add_fsx_lustre), Bedrock·SageMaker는
  인터페이스 엔드포인트(bedrock-runtime 등)로 사설 호출, 자체 호스팅 모델은 VPC 안 SageMaker 엔드포인트 2대 이상.
  표준으로는 ai-platform(EKS GPU), genai-rag(Bedrock RAG), gpu-training(분산 학습)을 먼저 고려하세요.
- "지금 구성에 ~를 추가", "현재 계정 기준으로 개선안"처럼 기존 구성에서 출발하는 요청은 start_from_current로 불러온 뒤
  결과의 VPC·로드 밸런서 이름으로 add_* 도구를 씁니다. 표준 아키텍처를 불러온 뒤에도 같은 VPC 이름으로 요소를 더할 수 있습니다.
- 설계를 마치면 review_design으로 검토하고, 중요한 결과와 개선안을 짧게 정리합니다.
- 실제 계정은 aws_identity, collect_aws_resources, find_collected_resources, review_collected_resources로 읽기 전용 조회만 합니다.
  리소스를 만들거나 바꾸는 AWS 호출은 하지 않습니다(도구도 없습니다).
- 도구를 쓰겠다고 말만 하고 끝내지 말고, 필요한 도구를 바로 호출한 뒤 결과를 말하세요.
- 메시지 끝의 [현재 화면] 메모는 앱이 붙인 정보입니다. 사용자가 "이 구성", "지금 구성", "현재"를 말하면 그 스냅샷에서 start_from_current로 시작하고,
  new_design으로 새로 그리지 마세요. 이미 그 스냅샷에서 시작한 설계를 이어 가는 중이면 다시 부르지 않습니다.
- 도구가 오류를 돌려주면 이유를 읽고 고쳐서 다시 시도하거나 사용자에게 확인하세요.
- 답변은 간결한 한국어로, 무엇을 그렸는지와 다음에 할 수 있는 일을 알려주세요."""


def available_models():
    return [{"key": key, "label": label, "model_id": model_id} for key, (label, model_id) in MODELS.items()]


class Workspace:
    """State shared by one conversation's tools: the current design, the last collection and where snapshots go."""

    def __init__(self, save, session_factory=None, region=None, allow_collection=True, default_account=None, load=None):
        self.save, self.load, self.allow_collection = save, load, allow_collection
        self.current_snapshot = None  # the snapshot open in the app when the user sent the message
        self.model_session = None  # browser egress: () -> the user's boto3 session for Bedrock (see server.default_chat)
        # Expected account for collections: AWS_ACCOUNT_ID (e.g. from --env-file) guards against the wrong account.
        self.default_account = default_account or (lambda: os.environ.get("AWS_ACCOUNT_ID"))
        self.session_factory = session_factory or (lambda region: boto3.Session(region_name=region))
        self.region = region or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-east-1"
        self.design = Design(region=self.region)
        self.collected = None
        self.changes = []  # snapshot ids saved during the current turn
        self.on_publish = None  # set while a streamed turn runs, so each saved snapshot can be shown at once
        self.lock = threading.Lock()

    def publish(self, snapshot):
        identifier = self.save(snapshot)
        self.changes.append(identifier)
        if self.on_publish:
            self.on_publish(identifier)
        return identifier

    def publish_design(self, result):
        return {**result, "snapshot_id": self.publish(self.design.snapshot())}

    # --- collection (read-only) ----------------------------------------------------------------
    def aws_identity(self, region=None):
        identity = self.session_factory(region or self.region).client("sts").get_caller_identity()
        return {"account": identity["Account"], "arn": identity["Arn"], "region": region or self.region}

    def collect_aws_resources(self, region=None, expected_account=None):
        if not self.allow_collection:
            raise PermissionError("AWS 수집은 admin 역할만 할 수 있습니다. 설계 도구는 계속 쓸 수 있습니다.")
        region = region or self.region
        expected = expected_account or self.default_account() or None
        snapshot = collect(region, session=self.session_factory(region), expected_account=expected)
        graph = build_graph(snapshot)
        snapshot["graph"] = graph
        self.collected = snapshot
        kinds = {}
        for node in graph["nodes"]:
            if not node["reference_only"]:
                kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        problems = [{"operation": c["operation"], "status": c["status"], "error": c.get("error_code"), "permission": c.get("permission")}
                    for c in snapshot["coverage"] if c["status"] != "complete"]
        return {"account": snapshot["metadata"]["account_id"], "region": region, "status": snapshot["metadata"]["status"],
                "resources": kinds, "incomplete_calls": problems, "findings": summary(review(graph, snapshot["coverage"])),
                "snapshot_id": self.publish(snapshot)}

    def find_collected_resources(self, kind=None, search=None, vpc=None, limit=30):
        if not self.collected:
            raise ValueError("아직 수집한 스냅샷이 없습니다. 먼저 collect_aws_resources를 호출하세요.")
        graph = self.collected["graph"]
        nodes = {n["id"]: n for n in graph["nodes"]}
        vpc_of = {e["source"]: e["target"] for e in graph["edges"] if e["relation"] == "in_vpc"}
        scope = None
        if vpc:
            scope = {n["id"] for n in nodes.values() if n["kind"] == "vpc" and vpc.lower() in (n["resource_id"].lower(), n["name"].lower())}
            if not scope:
                known = ", ".join(f"{n['name']} ({n['resource_id']})" for n in nodes.values() if n["kind"] == "vpc")
                raise ValueError(f"VPC '{vpc}'가 스냅샷에 없습니다. 수집된 VPC: {known}")
        text = (search or "").lower()
        rows = [n for n in nodes.values() if not n["reference_only"] and (not kind or n["kind"] == kind)
                and (scope is None or vpc_of.get(n["id"]) in scope or n["id"] in scope)
                and text in json.dumps([n["name"], n["resource_id"], n["attributes"]], ensure_ascii=False, default=str).lower()]
        keep = ("SubnetId", "CidrBlock", "AvailabilityZone", "InstanceType", "Engine", "Runtime", "Type", "Scheme", "State", "MultiAZ")
        return {"total": len(rows), "resources": [
            {"kind": n["kind"], "name": n["name"], "id": n["resource_id"],
             **({"vpc": nodes[vpc_of[n["id"]]]["name"]} if n["id"] in vpc_of else {}),
             **{k: n["attributes"][k] for k in keep if isinstance(n["attributes"].get(k), (str, int, bool))}}
            for n in rows[:max(1, min(int(limit), 100))]]}

    def review_collected_resources(self):
        if not self.collected:
            raise ValueError("아직 수집한 스냅샷이 없습니다. 먼저 collect_aws_resources를 호출하세요.")
        findings = review(self.collected["graph"], self.collected["coverage"])
        return {"summary": summary(findings),
                "findings": [{k: f[k] for k in ("severity", "pillar", "rule", "resource_name", "title", "recommendation")}
                             for f in findings[:20]]}

    # --- design ----------------------------------------------------------------------------------
    def start_from_snapshot(self, snapshot_id=None):
        """Brownfield design: explicit snapshot, else the one open in the app, else the last collection."""
        identifier = snapshot_id or self.current_snapshot
        if identifier and self.load:
            try:
                snapshot = self.load(identifier)
            except Exception as exc:  # noqa: BLE001 - the server's 404 becomes a message the model can act on
                raise ValueError(f"스냅샷을 찾을 수 없습니다: {identifier}") from exc
        elif self.collected:
            snapshot = self.collected
        else:
            raise ValueError("시작할 스냅샷이 없습니다. 앱에서 스냅샷을 열거나 collect_aws_resources로 먼저 수집하세요.")
        return self.publish_design(self.design.start_from_snapshot(snapshot))

    def context_note(self):
        """What the user is looking at, appended to their message so "this configuration" has a referent."""
        snapshot = None
        if self.current_snapshot and self.load:
            try:
                snapshot = self.load(self.current_snapshot)
            except Exception:  # noqa: BLE001 - a missing snapshot only means no note
                snapshot = None
        snapshot = snapshot or self.collected
        if not snapshot:
            return ""
        meta = snapshot["metadata"]
        kind = "설계안" if meta.get("design") else "수집한 구성"
        name = meta.get("title") or f"{meta.get('account_id')} {meta.get('region')}"
        started = " · 진행 중인 설계가 이 스냅샷에서 시작함" if self.design.base and self.design.base.get("title") == meta.get("title") else ""
        return f"\n\n[현재 화면] {kind} '{name}' ({meta.get('region')}, VPC {len(snapshot['raw'].get('vpcs', []))}개){started}"

    def new_design(self, title, region=None):
        self.design.reset(title, region or self.region)
        return {"title": title, "region": self.design.region, "next": "add_vpc로 네트워크를 만드세요."}


def build_tools(workspace):
    """Strands tools bound to one workspace. Docstrings are the descriptions the model reads."""
    from strands import tool

    w, d = workspace, lambda: workspace.design

    def change(method, **kwargs):
        with w.lock:
            return w.publish_design(getattr(d(), method)(**kwargs))

    @tool
    def aws_identity(region: str = "") -> dict:
        """현재 AWS 자격 증명(계정 ID, IAM ARN)을 STS로 확인합니다. 읽기 전용."""
        return w.aws_identity(region or None)

    @tool
    def collect_aws_resources(region: str = "", expected_account: str = "") -> dict:
        """실제 AWS 계정의 한 리전을 읽기 전용으로 수집해 현행 구성도를 만듭니다(VPC, 서브넷, EC2, ELB, RDS, EKS, ECS, Lambda 등).
        region을 비우면 기본 리전. 결과에 리소스 종류별 개수, 미완료 호출, 검토 요약, snapshot_id가 있습니다."""
        return w.collect_aws_resources(region or None, expected_account or None)

    @tool
    def find_collected_resources(kind: str = "", search: str = "", vpc: str = "", limit: int = 30) -> dict:
        """마지막으로 수집한 스냅샷에서 리소스를 찾습니다. kind 예: vpc, subnet, ec2, load_balancer, target_group, auto_scaling_group,
        db_instance, lambda_function, security_group, eks_cluster. vpc는 VPC 이름 또는 ID로 그 VPC 안의 리소스만 찾습니다
        (특정 VPC의 구성을 물으면 search 대신 vpc를 쓰세요). search는 리소스 이름·ID·속성 텍스트 검색."""
        return w.find_collected_resources(kind or None, search or None, vpc or None, limit)

    @tool
    def review_collected_resources() -> dict:
        """마지막으로 수집한 스냅샷의 아키텍처 검토 결과(신뢰성·보안·운영 규칙)와 권장 조치를 돌려줍니다."""
        return w.review_collected_resources()

    @tool
    def list_standard_architectures() -> dict:
        """바로 쓸 수 있는 표준 아키텍처 목록(이름과 설명)."""
        return STANDARDS

    @tool
    def apply_standard_architecture(name: str) -> dict:
        """표준 아키텍처 하나로 현재 설계를 바꿉니다. name은 list_standard_architectures의 키."""
        with w.lock:
            return w.publish_design(d().apply_standard(name))

    @tool
    def start_from_current(snapshot_id: str = "") -> dict:
        """현재 구성(수집한 계정 또는 앱에서 열어 둔 스냅샷)을 설계의 출발점으로 불러옵니다(기존 설계는 지웁니다).
        이후 add_* 도구에 결과의 VPC 이름을 쓰면 기존 VPC·서브넷·로드 밸런서에 요소를 더합니다. 실제 계정은 바뀌지 않습니다.
        snapshot_id를 비우면 앱에서 보고 있는 스냅샷, 없으면 마지막 수집 결과를 씁니다."""
        with w.lock:
            return w.start_from_snapshot(snapshot_id or None)

    @tool
    def new_design(title: str, region: str = "") -> dict:
        """빈 설계를 시작합니다(기존 설계는 지웁니다). region 예: ap-northeast-2."""
        with w.lock:
            return w.new_design(title, region or None)

    @tool
    def add_vpc(name: str, cidr: str = "10.0.0.0/16", azs: int = 3, tiers: list[str] | None = None, nat: str = "per_az") -> dict:
        """VPC와 AZ별 계층 서브넷, IGW, NAT, 라우트 테이블을 만듭니다.
        tiers: public, web, app, data 중 선택(기본 public, app, data). nat: per_az(AZ별, 권장), single(비용 절감), none."""
        return change("add_vpc", name=name, cidr=cidr, azs=azs, tiers=tiers or ["public", "app", "data"], nat=nat)

    @tool
    def add_subnet_tier(vpc: str, tier: str) -> dict:
        """VPC에 없는 계층(public, web, app, data)을 AZ마다 /24 서브넷으로 추가합니다. 기존 구성에서 시작한 설계에서
        data 계층이 없어 DB를 넣을 수 없을 때 등에 씁니다. public은 IGW, web·app은 같은 AZ의 NAT 라우트, data는 격리."""
        return change("add_subnet_tier", vpc=vpc, tier=tier)

    @tool
    def add_load_balancer(vpc: str, name: str, kind: str = "application", scheme: str = "internet-facing") -> dict:
        """로드 밸런서를 추가합니다. kind: application 또는 network. scheme: internet-facing(퍼블릭 서브넷) 또는 internal(앱 서브넷)."""
        return change("add_load_balancer", vpc=vpc, name=name, kind=kind, scheme=scheme)

    @tool
    def add_ec2_service(vpc: str, name: str, tier: str = "app", instance_type: str = "m6i.large", per_az: int = 1,
                        load_balancer: str = "", port: int = 8080, autoscaling: bool = True) -> dict:
        """EC2 서비스(AZ마다 인스턴스, Auto Scaling 그룹, 로드 밸런서 대상 그룹)를 추가합니다. tier: web 또는 app."""
        return change("add_ec2_service", vpc=vpc, name=name, tier=tier, instance_type=instance_type, per_az=per_az,
                      load_balancer=load_balancer or None, port=port, autoscaling=autoscaling)

    @tool
    def add_ecs_service(vpc: str, name: str, tasks_per_az: int = 1, load_balancer: str = "", port: int = 8080, tier: str = "app") -> dict:
        """ECS Fargate 서비스(awsvpc 태스크를 AZ에 분산, 로드 밸런서는 태스크 IP를 대상으로 등록)를 추가합니다."""
        return change("add_ecs_service", vpc=vpc, name=name, tasks_per_az=tasks_per_az, load_balancer=load_balancer or None,
                      port=port, tier=tier)

    @tool
    def add_eks_cluster(vpc: str, name: str, node_type: str = "m6i.xlarge", nodes_per_az: int = 1, tier: str = "app") -> dict:
        """EKS 클러스터(비공개 엔드포인트)와 AZ에 분산된 관리형 노드 그룹을 추가합니다."""
        return change("add_eks_cluster", vpc=vpc, name=name, node_type=node_type, nodes_per_az=nodes_per_az, tier=tier)

    @tool
    def add_gpu_node_group(vpc: str, cluster: str, name: str, instance_type: str, per_az: int = 1, training: bool = False) -> dict:
        """기존 EKS 클러스터에 GPU/가속 노드 그룹을 추가합니다. 추론(training=false)은 클러스터의 모든 AZ에 분산하고,
        EFA 학습(training=true)은 한 AZ에만 두고 자기 참조 보안 그룹을 만듭니다. 예: g6e.2xlarge(추론), p5.48xlarge(학습)."""
        return change("add_gpu_node_group", vpc=vpc, cluster=cluster, name=name, instance_type=instance_type, per_az=per_az,
                      training=training)

    @tool
    def add_fsx_lustre(vpc: str, name: str, deployment: str = "PERSISTENT_2", capacity_tib: float = 4.8) -> dict:
        """FSx for Lustre(학습 데이터·체크포인트)를 추가합니다. 학습 노드 그룹이 있으면 그 전용 서브넷(같은 AZ)에 둡니다.
        deployment는 PERSISTENT_2 권장(SCRATCH는 복제 없음)."""
        return change("add_fsx_lustre", vpc=vpc, name=name, deployment=deployment, capacity_tib=capacity_tib)

    @tool
    def add_sagemaker_endpoint(vpc: str, name: str, instance_type: str = "ml.g5.xlarge", count: int = 2, tier: str = "app") -> dict:
        """VPC 안 SageMaker 실시간 엔드포인트(모델 VpcConfig = 계층의 모든 AZ 서브넷)를 추가합니다. AZ 장애 대비 count 2 이상 권장."""
        return change("add_sagemaker_endpoint", vpc=vpc, name=name, instance_type=instance_type, count=count, tier=tier)

    @tool
    def add_database(vpc: str, name: str, engine: str = "postgres", multi_az: bool = True, read_replica: bool = False,
                     instance_class: str = "db.r6g.large") -> dict:
        """data 계층에 RDS(mysql, postgres, mariadb 등)를 추가합니다. multi_az는 동기식 대기, read_replica는 별도 AZ 읽기 복제본."""
        return change("add_database", vpc=vpc, name=name, engine=engine, multi_az=multi_az, read_replica=read_replica,
                      instance_class=instance_class)

    @tool
    def add_aurora(vpc: str, name: str, engine: str = "aurora-postgresql", readers: int = 1) -> dict:
        """data 계층에 Aurora 클러스터(writer 1 + reader N, 서로 다른 AZ)를 추가합니다."""
        return change("add_aurora", vpc=vpc, name=name, engine=engine, readers=readers)

    @tool
    def add_cache(vpc: str, name: str, replicas: int = 1) -> dict:
        """data 계층에 ElastiCache Redis(primary + replica, 자동 장애 조치)를 추가합니다."""
        return change("add_cache", vpc=vpc, name=name, replicas=replicas)

    @tool
    def add_kafka(vpc: str, name: str, brokers_per_az: int = 1) -> dict:
        """data 계층에 Amazon MSK(Kafka) 클러스터를 추가합니다."""
        return change("add_kafka", vpc=vpc, name=name, brokers_per_az=brokers_per_az)

    @tool
    def add_opensearch(vpc: str, name: str, data_per_az: int = 1, standby: bool = False) -> dict:
        """data 계층에 OpenSearch 도메인(전용 마스터, AZ 분산)을 추가합니다. standby는 AZ 3개일 때 Multi-AZ with Standby."""
        return change("add_opensearch", vpc=vpc, name=name, data_per_az=data_per_az, standby=standby)

    @tool
    def add_file_system(vpc: str, name: str, tier: str = "app") -> dict:
        """EFS 파일 시스템과 AZ별 마운트 타깃을 추가합니다."""
        return change("add_file_system", vpc=vpc, name=name, tier=tier)

    @tool
    def add_lambda(name: str, vpc: str = "", tier: str = "app", runtime: str = "python3.12") -> dict:
        """Lambda 함수를 추가합니다. vpc를 비우면 VPC에 연결하지 않은 함수(구성도 밖, 목록에 표시)."""
        return change("add_lambda", name=name, vpc=vpc or None, tier=tier, runtime=runtime)

    @tool
    def add_vpc_endpoints(vpc: str, services: list[str] | None = None) -> dict:
        """VPC 엔드포인트를 추가합니다. s3·dynamodb는 게이트웨이, 그 외(ssm, ecr.dkr, sts, logs 등)는 인터페이스."""
        return change("add_vpc_endpoints", vpc=vpc, services=services or ["s3", "ssm"])

    @tool
    def connect_vpcs(vpc_a: str, vpc_b: str) -> dict:
        """두 VPC를 피어링으로 연결하고 양쪽 라우트를 추가합니다."""
        return change("connect_vpcs", a=vpc_a, b=vpc_b)

    @tool
    def add_vpn(vpc: str, customer_ip: str = "203.0.113.10", name: str = "on-premises") -> dict:
        """가상 프라이빗 게이트웨이와 사내 데이터 센터 간 Site-to-Site VPN(터널 2개)을 추가합니다."""
        return change("add_vpn", vpc=vpc, customer_ip=customer_ip, name=name)

    @tool
    def describe_design() -> dict:
        """현재 설계의 VPC·계층·리소스 개수를 요약합니다."""
        return d().describe()

    @tool
    def review_design() -> dict:
        """현재 설계를 신뢰성·보안·운영 규칙으로 검토하고 권장 조치를 돌려줍니다."""
        return d().review()

    return [aws_identity, collect_aws_resources, find_collected_resources, review_collected_resources,
            list_standard_architectures, apply_standard_architecture, start_from_current, new_design, add_vpc, add_subnet_tier, add_load_balancer, add_ec2_service,
            add_ecs_service, add_eks_cluster, add_gpu_node_group, add_fsx_lustre, add_sagemaker_endpoint, add_database, add_aurora, add_cache, add_kafka, add_opensearch, add_file_system,
            add_lambda, add_vpc_endpoints, connect_vpcs, add_vpn, describe_design, review_design]


def bedrock_model(key, session=None, record=None, streaming=True):
    """A Strands BedrockModel (Converse) that reports each call it serves, so the chat can say which model answered."""
    from strands.models.bedrock import BedrockModel
    if key not in MODELS:
        raise ValueError(f"지원하지 않는 모델입니다: {key} (사용 가능: {', '.join(MODELS)})")

    class Recorded(BedrockModel):
        async def stream(self, *args, **kwargs):
            if record:
                record(key)
            async for event in super().stream(*args, **kwargs):
                yield event

    return Recorded(model_id=MODELS[key][1], boto_session=session or boto3.Session(region_name="us-east-1"),
                    temperature=0.2, max_tokens=4096, streaming=streaming)


def progress_handler(emit):
    """A Strands callback handler that turns agent events into chat progress: text deltas, tool start and finish."""
    names = {}

    def handle(**event):
        if "data" in event:
            emit({"type": "text", "text": event["data"]})
        start = ((event.get("event") or {}).get("contentBlockStart") or {}).get("start", {}).get("toolUse")
        if start:
            names[start["toolUseId"]] = start["name"]
            emit({"type": "tool", "name": start["name"], "status": "start"})
        message = event.get("message")
        if message and message.get("role") == "user":
            for block in message.get("content", []):
                if "toolResult" in block:
                    result = block["toolResult"]
                    emit({"type": "tool", "name": names.get(result["toolUseId"], ""), "status": result.get("status", "success")})

    return handle


class Chat:
    """One conversation: a Strands agent over a workspace. The preferred model falls back on failure (e.g. model access)."""

    def __init__(self, workspace, model=DEFAULT_MODEL, fallback=FALLBACK_MODEL, model_factory=bedrock_model):
        from strands import Agent
        from strands.models.routing import ModelRouter
        from strands.tools.executors import SequentialToolExecutor
        self.workspace = workspace
        self.model = model
        keys = [model] + ([fallback] if fallback and fallback != model else [])
        self.calls = []
        self.emit = None
        self.models = {key: model_factory(key, record=self.record) for key in keys}
        engine = self.models[model] if len(keys) == 1 else ModelRouter([self.models[k] for k in keys])
        # Drawing steps depend on each other (a service needs its load balancer), so tools run in the order requested.
        self.agent = Agent(model=engine, tools=build_tools(workspace), system_prompt=SYSTEM_PROMPT, callback_handler=None,
                           tool_executor=SequentialToolExecutor())
        self.lock = threading.Lock()

    def record(self, key):
        self.calls.append(key)
        if self.emit:
            self.emit({"type": "model", "model": key, "model_label": MODELS[key][0], "fallback": key != self.model})

    def ask(self, message, on_event=None):
        """One turn. on_event, if given, receives progress events while the agent works (streamed to the browser)."""
        from strands.handlers.callback_handler import null_callback_handler
        with self.lock:
            self.workspace.changes, self.calls[:] = [], []
            start = len(self.agent.messages)
            self.emit = on_event
            self.agent.callback_handler = progress_handler(on_event) if on_event else null_callback_handler
            self.workspace.on_publish = (lambda identifier: on_event({"type": "snapshot", "id": identifier})) if on_event else None
            try:
                result = self.agent(message + self.workspace.context_note())
            finally:
                self.emit = self.workspace.on_publish = None
                self.agent.callback_handler = null_callback_handler
            text = str(result).strip()
            used = [b["toolUse"]["name"] for m in self.agent.messages[start:] for b in m.get("content", []) if "toolUse" in b]
            # The last model call produced the reply; earlier entries show any fallback (e.g. no model access yet).
            answered = self.calls[-1] if self.calls else self.model
            return {"reply": text, "snapshots": list(self.workspace.changes), "tools_used": used, "model": answered,
                    "model_label": MODELS[answered][0], "fallback": answered != self.model,
                    "usage": dict(getattr(result.metrics, "accumulated_usage", {}) or {})}
