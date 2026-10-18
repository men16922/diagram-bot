---
title: 샘플과 데모 스냅샷
summary: samples.Sample 빌더 도우미, 번들 샘플 11개(무엇을 보여 주는지·리전), SAMPLE_VERSION과 서버의 샘플 재생성(SnapshotStore.seed), demo.py, scripts/render_samples.py, examples/samples.
sources: [src/diagram_bot/samples.py, src/diagram_bot/demo.py, src/diagram_bot/store.py, src/diagram_bot/server.py, src/diagram_bot/designer.py, src/diagram_bot/cli.py, scripts/render_samples.py, examples/samples/README.md, tests/test_drawio.py, tests/test_server.py]
related: [snapshot-model, layout-and-drawio, chat-agent, storage, howto-add-collected-service, cli]
updated: 2026-10-18
---
# 샘플과 데모 스냅샷

> 샘플은 AWS 권장 구성을 **수집기와 같은 raw 형식**으로 만든 합성 스냅샷이다(모듈 docstring: "Never live data"). 그래서 실제 수집과
> 똑같이 그려지고, 검토·CloudFormation 내보내기·채팅 설계의 출발점(표준 아키텍처)으로도 쓰인다.

## 핵심
- 레지스트리: `samples.SAMPLES`(키 → 빌더 함수) 11개. 기본 계정 `111122223333`, 기본 리전 `ap-northeast-2`(`ACCOUNT`, `REGION`).
- `SAMPLE_VERSION = 6`. 샘플 구성을 바꾸면 올린다. 서버는 저장된 샘플의 `metadata.sample_version`이 다르면 다시 만든다.
- 샘플 metadata: `demo: True`, `sample`, `sample_version`, `title`, `practices`, 공통 `limitations`(합성 샘플, 리스너 규칙·Route 53·Shield·Network Firewall은 범위 밖, 설정 관계). 필드 정의는 [[snapshot-model]].
- `designer.STANDARDS`에 있는 10개(`multi-account` 제외)는 채팅 설계의 `apply_standard`로 불러올 수 있다([[chat-agent]]).

## Sample 빌더
`Sample(key, title, practices, region=REGION, account=ACCOUNT)`가 `raw`를 채우고 `snapshot()`이 스냅샷을 돌려준다. ID는 결정적이다(`digest`, `uuid`는 SHA-256 기반).
- 네트워크: `vpc`, `subnet(key, vpc, az, cidr, name, tier)`(`MapPublicIpOnLaunch`는 tier가 public일 때), `internet_gateway`, `nat_gateway`, `route_table(key, vpc, name, subnets, gateway, nat, main)`, `route(table, destination, **target)`, `network(vpc, prefix, azs, public, private, nat_per_az)`(IGW + AZ별 NAT + 층별 라우팅 테이블 + 격리용 주 테이블), `gateway_endpoint`, `interface_endpoint`, `security_group(key, vpc, name, ingress, egress)`.
- 컴퓨팅·로드 밸런싱: `instance(key, subnet, name, kind, ip, sg, efa, placement, lifecycle, **tags)`, `load_balancer`, `target_group(name, vpc, protocol, port, targets, lbs, target_type, health)`, `auto_scaling_group`, `address(subnet, offset)`, `az_of(subnet)`.
- 데이터: `database(identifier, vpc, subnets, primary, engine, size, standby, replica)`, `aurora`(DocumentDB도 `engine="docdb"`), `redis`, `redis_cluster`, `kafka`, `opensearch`, `efs`, `redshift`.
- 컨테이너·서버리스: `eks`, `ecs_service`, `function`.
- 연결: `transit_gateway(key, name, owner)`, `tgw_attachment`, `peering`, `vpn(key, customer, ip, name, vgw, tgw, vpc, tunnels_up, vgw_name)`.
- AI/ML: `placement_group`, `fsx_lustre`, `sagemaker_endpoint`, `hyperpod`.
- 엣지·EIP·TGW 라우팅·DX: `elastic_ip(key, ip, name, nat, balancer, subnet)`(NAT 주소 또는 NLB 서브넷 주소에도 할당 ID를 넣음), `web_acl(name, scope, resources)`, `cloudfront(key, alias, origins, web_acl)`(LB ARN이면 `DNSName`을 만들어 오리진으로, 아니면 S3 버킷), `tgw_route_table(key, gateway, name, associate, propagate, routes)`(대상 없는 경로 = 정적 blackhole), `dx_gateway(name, tgw, vgw, prefixes)`, `direct_connect(key, name, location, gateway, vlan, bandwidth)`(전용 연결 + transit VIF, BGP up).
- 이 마지막 묶음은 `raw.setdefault`로 키를 만든다. 쓰지 않는 샘플에는 키도, coverage 행도 없다.
- `snapshot()`: 서브넷의 `AvailableIpAddressCount`를 CIDR − 5 − 인스턴스 수로 채우고, 고정 목록의 coverage 행(+ 대상 그룹별 `describe_target_health`, + 존재하는 선택 키의 엣지·EIP·TGW·DX 행)을 모두 `complete`로 만든다.

## 번들 샘플 11개
- `three-tier` (ap-northeast-2): 3 AZ 3계층 웹 — 인터넷 ALB → 웹 ASG → 내부 ALB → 앱 ASG → RDS Multi-AZ와 읽기 복제본, AZ별 NAT, SSM·S3 엔드포인트, 앞단 CloudFront(ALB + S3 오리진)와 CloudFront용 WAF, NAT별 Elastic IP.
- `microservices` (ap-northeast-2): ALB 하나에 대상 그룹 4개 — EC2 ASG 서비스 2개(`catalog`, `orders`)와 ECS Fargate 서비스 2개(`cart-svc`, `payments-svc`, 태스크 IP 대상), RDS Multi-AZ, ECR API·Docker·Logs 인터페이스와 S3 게이트웨이 엔드포인트.
- `nlb-alb` (ap-northeast-2): 고정 IP 진입점 NLB → ALB(`alb` 대상 유형), AZ별 NLB Elastic IP, ALB의 REGIONAL WAF 웹 ACL.
- `environments` (ap-northeast-2): 운영(3 AZ)·스테이징(2 AZ)·공용 서비스·중앙 egress VPC 4개를 Transit Gateway 하나로 연결, 워크로드 VPC는 NAT 없이 `0.0.0.0/0 → TGW`.
- `hybrid` (ap-northeast-2): 온프레미스 IP 대상(`AvailabilityZone=all`)이 섞인 대상 그룹, 위치 2곳의 Direct Connect 전용 연결 → DX 게이트웨이 → TGW, 백업 Site-to-Site VPN 2개.
- `eks-platform` (ap-northeast-2): EKS(전용 /28 클러스터 서브넷, 노드 그룹), Aurora writer/reader 3 AZ, ElastiCache Redis, MSK, VPC Lambda, 엔드포인트.
- `data-platform` (ap-northeast-2): 앱 VPC(ECS, OpenSearch Multi-AZ with Standby, RDS + 복제본, Redis 클러스터 모드, EFS)와 분석 VPC(Redshift Multi-AZ, DocumentDB), TGW 하나와 이중 VPN, TGW 라우팅 테이블 2개(VPC 둘이 쓰는 스포크 테이블, VPN attachment가 쓰는 사내망 테이블).
- `ai-platform` (ap-northeast-2): 인터넷 경로 없는 사설 EKS — GPU 추론 노드 그룹 3 AZ, EFA 학습 노드 그룹 단일 AZ·배치 그룹, FSx for Lustre, Bedrock 인터페이스 엔드포인트, Aurora pgvector, EFS, TGW 연결.
- `genai-rag` (ap-northeast-2): ECS Fargate 채팅 API·수집 워커, Bedrock 엔드포인트, Aurora pgvector, 시맨틱 캐시 Redis, VPC 안 SageMaker 리랭커 엔드포인트.
- `gpu-training` (**us-east-2**): p5 Capacity Block과 trn2 배치 그룹 + ODCR, EFA 자기 참조 보안 그룹, FSx for Lustre, HyperPod(Slurm). 리전 선택 이유(서울에 trn2 없음)가 `practices`에 있다.
- `multi-account` (ap-northeast-2, 계정 3개): Network 계정이 TGW를 소유·공유하고 중앙 egress와 VPN을 두며, 운영·스테이징 계정이 VPC를 연결한다. 계정별 `Sample` 세 개를 `combine_snapshots`로 합친 **통합 스냅샷**이다(`parts`, `raw: {}`).

전체 구성에 그려지는 관계 목록은 샘플마다 `test_overview_draws_the_network_between_vpcs`의 기대값으로 고정되어 있다(`three-tier`: `cloudfront_origin`, `hybrid`: TGW 연결 + DX 게이트웨이 연결 + VIF 2 + VPN 2 등).

## 데모 스냅샷 (`demo.demo_snapshot`)
- 문서화한 경계 사례를 모은 최소 스냅샷: VPC 3개(`vpc-demo`, `vpc-staging`, 빈 `vpc-empty`), 두 포트로 등록된 같은 인스턴스, 조회되지 않은 대상 인스턴스(`i-demo-missing`, 참조 전용), 다른 인스턴스와 IP가 같은 IP 대상, 긴 한국어 이름, NLB, 퍼블릭 IP·IMDSv1 인스턴스, SG 규칙.
- `status: partial`, `describe_instances` coverage가 `partial`(`UnauthorizedOperation`)이라 부분 조회 표시도 확인할 수 있다. 샘플과 달리 `sample`·`sample_version`이 없다.
- 그리기 테스트 대부분의 기본 입력이다(`tests/test_drawio.py`의 `VIEWS`).

## 서버의 샘플 재생성 (`SnapshotStore.seed`)
- `server.create_app`이 시작할 때 `context.snapshots.seed(demo_snapshot, SAMPLES, SAMPLE_VERSION)`을 부른다.
- 데모는 `demo.json`이 없을 때 한 번만 저장한다. 샘플은 `sample-{key}.json`을 읽어 `metadata.sample_version`이 현재 값과 다르거나(파일 없음·읽기 실패 포함) 하면 다시 만들어 저장한다(`test_server.py`: 옛 버전 샘플은 교체되고 데모는 그대로).
- 저장 ID가 `sample-{key}`로 고정이라, 버전이 바뀌어 다시 저장해도 스냅샷 ID는 같다. 저장 방식은 [[storage]].

## 산출물 만들기
- CLI: `diagram-bot demo [--sample KEY] [--output 경로]`가 `write_report`로 `snapshot.json`, `topology.drawio`(전체 구성 + VPC별 상세), `index.html`, `inventory.csv`, `review.md`를 만든다([[cli]]).
- `scripts/render_samples.py`: 새 데이터 디렉터리로 띄운 `diagram-bot serve`에 붙어 샘플마다 CLI 산출물을 다시 만들고, Playwright로 앱의 편집기에서 전체 구성과 VPC별 PNG를 내보낸 뒤 `examples/samples/README.md`의 샘플 절(리소스 개수 `COUNTS`, `practices`, 그림)을 다시 쓴다. 머리말은 손으로 쓴 부분이라 유지한다. AWS 자격 증명 환경 변수는 지우고 실행한다(`CLEAN_ENV`).
- `examples/samples/<key>/`: 위 산출물과 PNG(`overview.png`, VPC 이름별 PNG). README에는 VPC 상세 그림만 싣고, 통합 샘플(`multi-account`)만 `overview.png`를 맨 앞에 싣는다(`write_readme`).

## 바꿀 때 주의
- 샘플 구성을 바꾸면 `SAMPLE_VERSION`을 올리고 `scripts/render_samples.py`로 `examples/samples`를 다시 만든다. 올리지 않으면 이미 띄운 서버의 저장 샘플이 옛 그림으로 남는다.
- 샘플은 수집기가 실제로 읽는 필드만 쓴다. 새 필드를 샘플에만 넣으면 수집 데이터와 그림이 달라진다.
- 모든 샘플이 그리기 불변식을 통과해야 한다(`test_best_practice_samples_are_readable`, `test_every_focus_document_routes_its_edges`, [[layout-and-drawio]]). CloudFormation 템플릿 테스트(`tests/test_iac.py::test_every_sample_exports_a_self_contained_template`)도 모든 샘플을 돈다.

## 관련
- [[snapshot-model]] — 샘플이 따르는 스냅샷 형식.
- [[chat-agent]] — 표준 아키텍처로 샘플을 불러오는 설계 도구.
- [[howto-add-collected-service]] — 새 서비스를 샘플에 넣는 단계.
