# 사례 아키텍처 기준 (AWS Best Practice)

Last Updated: 2026-10-18

샘플 11종(`src/diagram_bot/samples.py`)은 데모 데이터이자 채팅 에이전트의 표준 설계(`designer.py` `STANDARDS`)다.
이 문서는 각 사례가 따라야 할 AWS 공식 권고를 출처와 함께 정리하고, 현재 샘플과의 차이와 변경안을 기록한다.
변경안은 2026-10-18 소유자 지시로 반영했다(4절·5.9절의 반영 현황).

## 1. 범위와 판정 기준

**권고 강도** (출처 문구 기준)

| 표기 | 의미 |
| --- | --- |
| MUST | 서비스 문서의 필수 조건 (구성 자체가 불가하거나 기능이 동작하지 않음) |
| HIGH | Well-Architected 모범 사례 중 "Level of risk if not established: High" |
| REC | "we recommend" / "should" |
| OPT | "consider" / 선택지로 제시 |

**그릴 수 있는 것만 샘플에 넣는다.** 구성도는 수집한 API 응답만 그리므로(`collector.py`), 수집하지 않는 서비스는
도형으로 넣을 수 없다. 이런 권고는 샘플의 `practices` 문구(설명)로만 남기거나, 수집 범위 확장 후보로 둔다.

| 구분 | 서비스 |
| --- | --- |
| 수집·도형화 가능 | VPC, 서브넷, 라우팅 테이블, IGW, NAT, VPC 엔드포인트, TGW·attachment, 피어링, VGW·CGW·Site-to-Site VPN, ELB(ALB/NLB/GWLB)·대상 그룹, EC2·ASG, ECS, EKS·노드 그룹, Lambda, RDS·Aurora·DocumentDB, ElastiCache, MSK, OpenSearch, EFS, Redshift, 보안 그룹, **AI/ML: FSx(Lustre 등), SageMaker 실시간 엔드포인트, SageMaker HyperPod, 클러스터 배치 그룹, EC2의 EFA 인터페이스·Capacity Block·용량 예약**, **엣지·연결(2026-10-18, 4.1절): CloudFront(리전 밖 엣지 열), AWS WAF 웹 ACL(WAF 표식), Elastic IP(붙은 리소스 라벨), TGW 라우팅 테이블(라우팅 도메인), Direct Connect 연결·VIF·DX 게이트웨이** |
| 수집 안 함 (도형 불가) | Shield, Route 53(Resolver 포함), Global Accelerator, Network Firewall, NACL, 리스너 규칙, Organizations(계정·OU), S3, DynamoDB, Bedrock 설정(Guardrails·호출 로깅·Knowledge Base), SageMaker 학습 작업·노트북, Kubernetes 객체(Karpenter·KEDA·device plugin) |
| 여러 계정 | 수집은 계정 × 리전 단위. `diagram-bot combine`·앱의 `계정 통합 보기`로 합치면 전체 구성에 AWS 계정 프레임과 공유 TGW·계정 간 피어링을 그린다 (`multi-account` 사례) |

## 2. 공통 원칙 (모든 사례)

| # | 원칙 | 강도 | 출처 |
| --- | --- | --- | --- |
| C1 | 운영 워크로드는 2개 이상 AZ, 참조 구성은 3 AZ. 모든 컴퓨팅(EC2·Fargate·VPC Lambda)을 AZ에 분산 | HIGH | REL10-BP01 |
| C2 | RDS·Redshift·ElastiCache 등 데이터 저장소는 Multi-AZ 복제 | HIGH | REL10-BP01 |
| C3 | 민감도에 따른 네트워크 계층: 퍼블릭 서브넷에는 로드 밸런서·NAT만, 앱은 프라이빗, 데이터는 별도 프라이빗(인터넷 경로 없음) | HIGH | SEC05-BP01 |
| C4 | 프라이빗 서브넷의 외부 통신은 AZ마다 NAT 게이트웨이 | REC | VPC 예제, ECS outbound |
| C5 | 관리형 서비스 접근은 VPC 엔드포인트(S3는 게이트웨이), 관리 접근은 SSM(배스천 없음) | REC | SEC05-BP01, Systems Manager |
| C6 | 인터넷 진입점 앞에 CloudFront·WAF·Shield, DNS는 Route 53 헬스 체크 | HIGH(진입점 고가용성)/REC | REL02-BP01, SRA |
| C7 | 온프레미스 연결은 연결·장비·ISP 이중화와 BGP | HIGH | REL02-BP02 |
| C8 | 워크로드·환경은 계정으로 분리, 네트워크는 Network 계정의 TGW를 RAM으로 공유 | REC | Organizing Your AWS Environment, SRA |
| C9 | 다이어그램 표기: 그룹 구조(Cloud > Region > VPC > AZ > Subnet) 먼저, ASG처럼 여러 그룹을 가로지르는 그룹 허용, 아이콘 크기 고정, 라벨 12pt Arial, 이름은 단어 중간에서 끊지 않음, 직각 화살표 | 지침 | AWS Architecture Icons (release 24) |

현재 레이아웃은 C9를 따른다(DRAWIO.md). C6·C8은 수집 범위 밖이라 도형으로는 표현할 수 없다(1절).

## 3. 사례별 판정

각 표의 "조치"는 **S**(샘플 수정으로 반영 가능), **P**(practices 문구로만 반영), **X**(수집 범위 확장 필요)로 표시한다.

### 3.1 three-tier — 3계층 웹 (3 AZ)

출처: REL10-BP01, SEC05-BP01, REL02-BP01, VPC 예제(private subnets + NAT), Systems Manager VPC 엔드포인트.
(Web Application Hosting 백서는 "historical reference only"라 기준으로 쓰지 않는다.)

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| AZ | 3 AZ, ELB도 세 AZ | HIGH | 3 AZ, 퍼블릭·내부 ALB 모두 3 AZ | 충족 |
| 계층 | ALB만 퍼블릭, 웹·앱 프라이빗, 데이터 별도 격리 | HIGH | 충족 | 충족 |
| NAT | AZ마다 | REC | AZ마다 | 충족 |
| DB | Multi-AZ + 읽기 복제본 | HIGH | MySQL Multi-AZ + 3번째 AZ 복제본 | 충족 |
| SSM 엔드포인트 | `ssm` · `ssmmessages` · `ec2messages` | REC | 세 엔드포인트 | 충족 (2026-10-18 반영) |
| 엣지 | CloudFront(+S3 정적 콘텐츠) · WAF · Route 53 | REC | CloudFront(+WAF, S3 오리진), ALB SG는 CloudFront 접두사 목록만, NAT별 EIP | 충족 (2026-10-18, 4.1절; Route 53은 문구) |

### 3.2 microservices — ALB 경로 라우팅 (EC2 ASG + ECS Fargate)

출처: Implementing Microservices on AWS, ALB target groups, ECS outbound networking, ECR interface endpoints.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 서비스별 대상 그룹 | 마이크로서비스마다 대상 그룹 | REC | 4개 | 충족 |
| Fargate 배치 | 프라이빗 서브넷, 퍼블릭 IP 없음, AZ마다 NAT | REC | 충족 | 충족 |
| ECR 엔드포인트 | `ecr.api` + `ecr.dkr` + S3 게이트웨이 (NAT 없으면 필수), 로그는 `logs` | MUST(NAT 없을 때)/REC | `ecr.api`·`ecr.dkr`·`logs` + S3 | 충족 (반영) |
| AZ | 2 AZ 이상 (참조 다이어그램 3 AZ) | HIGH/OPT | 3 AZ (ASG·Fargate 태스크·NAT·RDS 서브넷 그룹) | 충족 (3 AZ 반영, §4 #7) |
| 엣지 | ALB 앞 WAF·CloudFront | REC | 없음 | **P** / X |

### 3.3 nlb-alb — 고정 IP 진입점 (NLB → ALB)

출처: NLB "Use an ALB as a target", ALB-type target group 블로그(2021-09), REL02-BP01.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 같은 VPC·계정 | NLB와 ALB는 같은 VPC | MUST | 충족 | 충족 |
| 리스너 | `alb` 대상 그룹은 TCP 리스너, 포트 = ALB 리스너 포트 | MUST | TCP 443 → ALB 443 | 충족 |
| AZ 일치 | NLB와 ALB에 같은 AZ | REC | NLB는 퍼블릭 a/c, ALB는 프라이빗 a/c | 충족 |
| 고정 IP | AZ마다 Elastic IP | REC | NLB AZ별 EIP (`EIP 2` 라벨) | 충족 (2026-10-18) |
| WAF | ALB에 WAF | REC | ALB에 리전 WAF 웹 ACL | 충족 (2026-10-18) |
| 대안 | Global Accelerator(애니캐스트 고정 IP 2개) | OPT | — | P |

### 3.4 environments — 환경 분리 (운영 · 스테이징 · 공용)

출처: Organizing Your AWS Environment Using Multiple Accounts(2025-04), Multi-VPC 백서(TGW, centralized egress),
SRA Network account, TGW isolated VPCs with shared services.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 계정 분리 | 환경마다 계정, TGW는 Network 계정에서 RAM 공유 | REC(강함) | 한 계정 (VPC 분리 예시) | 별도 사례 `multi-account`가 계정 분리·RAM 공유 TGW·중앙 egress를 그림 (2026-10-18) |
| TGW 격리 | 운영·스테이징 별도 라우팅 테이블, 공용만 양쪽 전파, 스포크 간 blackhole | REC | 모든 VPC가 같은 10.0.0.0/8 경로 | **P** (TGW 라우팅 테이블 미수집) |
| TGW attachment | AZ마다 서브넷 하나 | REC | 운영 3 AZ, 스테이징 2 AZ, 공용 2 AZ, egress 3 AZ(전용 /28) | 충족 |
| NAT | AZ마다 | REC | egress VPC에 AZ마다 (운영·스테이징 VPC에는 없음) | 충족 |
| 스테이징 NAT 1개 | 비운영 비용 절충 (REL10은 운영 대상) | — | 중앙 egress로 대체 (NAT를 환경끼리 공유) | 해당 없음 (§4 #7) |
| 중앙 egress / 검사 VPC | NAT 집중 egress VPC, 검사 VPC(TGW appliance mode) 또는 TGW Network Firewall attachment | OPT | `egress-vpc`: AZ별 NAT + 전용 /28 attachment 서브넷, 환경 VPC는 0.0.0.0/0 → TGW, 퍼블릭 경로에 10.0.0.0/8 → TGW 복귀 | egress VPC 충족 (반영, §4 #7), 방화벽은 X / P |

### 3.5 hybrid — 하이브리드 (TGW + Site-to-Site VPN)

출처: REL02-BP02, Redundant Site-to-Site VPN connections, Customer gateway devices, Multi-VPC 백서(VPN),
Direct Connect Resiliency Recommendations, ALB target groups(IP 대상).

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| VPN 종단 | TGW (ECMP 지원, 터널당 1.25 Gbps) | REC | TGW | 충족 |
| 이중화 | 두 번째 CGW(가능하면 다른 ISP)로 두 번째 VPN, 같은 대역 BGP 광고, 장비 이중화 | HIGH | CGW 2, VPN 2(각 터널 2) | 충족 (반영) |
| 터널 | CGW에서 두 터널 모두 사용 | MUST(실무) | 터널 2개 UP | 충족 |
| 주 연결 | 운영은 Direct Connect, VPN은 백업 | REC | 위치 2곳 DX 연결 2개 · transit VIF → DX 게이트웨이 → TGW, VPN 2개는 백업 | 충족 (2026-10-18) |
| IP 대상 | RFC1918/RFC6598만, DX·VPN 경유 온프레미스 허용 | MUST | 192.168.20.0/24 | 충족 |
| DNS | Route 53 Resolver 엔드포인트 | OPT | 없음 | P |

### 3.6 eks-platform — EKS 플랫폼

출처: EKS Best Practices(VPC and Subnet), EKS private clusters, EKS ALB ingress, MSK best practices,
Aurora high availability, ElastiCache Multi-AZ, Lambda VPC.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 엔드포인트 접근 | 일반 권장은 public+private(CIDR 제한), 격리 필요 시 private | REC | private | 충족(의도 명시) |
| 노드 | 세 AZ 프라이빗 서브넷, AZ마다 NAT | REC | 충족 | 충족 |
| 클러스터 서브넷 | 전용 /28 서브넷 | OPT | `eks-cluster-a/b/c` /28 (컨트롤 플레인 ENI), 노드는 /20 | 충족 (반영, §4 #7) |
| LB 서브넷 | ALB는 퍼블릭(`kubernetes.io/role/elb` 태그), 2 AZ 이상, Fargate면 IP 대상 | REC | 3 AZ, IP 대상, `elb`·`internal-elb` 태그 | 충족 (태그 반영) |
| VPC 엔드포인트 | NAT가 없으면 `ec2`·`ecr.api`·`ecr.dkr`·`s3`·`elasticloadbalancing`·`logs`·`sts`(+`eks-auth`) | MUST(NAT 없을 때) | NAT 있음, `ecr.api`·`ecr.dkr`·`sts`·`s3` | 충족 (반영; NAT가 있어 나머지는 선택) |
| MSK | 3 AZ, 브로커 수는 AZ 배수, RF ≥ 3, minISR ≤ RF−1 | REC | 3 AZ × 1 | 충족 (RF는 P) |
| Aurora | 다른 AZ에 리플리카 1개 이상 | REC | writer a, reader b·c | 충족 (c reader 반영, §4 #7) |
| ElastiCache | Multi-AZ 자동 장애 조치 | REC | primary b, replica c | 충족 |
| Lambda | 여러 AZ 서브넷 | HIGH | 3 AZ | 충족 |
| 엣지 | 인터넷 ALB에 WAF | REC | 없음 | P |

### 3.7 data-platform — 데이터 플랫폼

출처: OpenSearch Multi-AZ, RDS Multi-AZ, Redshift Multi-AZ, DocumentDB replication, EFS mount targets,
Multi-VPC 백서(VPC peering, VPN).

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| OpenSearch | Multi-AZ with Standby: 3 AZ, 전용 마스터 3, 데이터 노드 3의 배수, 복제본 2 이상 | REC | 충족 | 충족 |
| RDS | Multi-AZ 인스턴스(대기 1) 또는 Multi-AZ DB 클러스터(읽기 가능 대기 2, 3 AZ) | HIGH | 인스턴스 + 복제본 | 충족 (클러스터는 선택) |
| Redshift Multi-AZ | RA3, 암호화, AZ당 2노드 이상, 서브넷 그룹 3개 이상 서브넷 | MUST | 서브넷 3개, 노드 4 | 충족 (반영) |
| DocumentDB | 다른 AZ에 복제본 | REC | writer a, reader b | 충족 (3 AZ는 선택) |
| EFS | AZ마다 마운트 타깃 | REC | 3 AZ | 충족 |
| VPC 간 연결 | 피어링은 비전이적: 피어 VPC의 VPN으로 온프레미스에 갈 수 없음. VPC 10개 미만이면 피어링 가능 | MUST(동작) | TGW 허브에 두 VPC와 VPN 연결 | 충족 (반영) |
| VPN | VGW는 시작용, ECMP 필요 시 TGW. CGW 이중화 | REC / HIGH | TGW 종단, CGW 2 | 충족 (반영) |

## 4. 변경안

**반영 현황 (2026-10-18):** 1–7 완료. 7(OPT)은 같은 날 후속으로 반영(`SAMPLE_VERSION` 4, 세 사례 모두 `review --fail-on medium` 종료 코드 0). 구현 기록: [docs/plans/2026-10-18-best-practice-samples.md](plans/2026-10-18-best-practice-samples.md).

샘플 빌더가 이미 지원하는 요소만으로 반영할 수 있는 항목부터 정리한다. 각 항목의 완료 기준은
`make check` 통과(가독성·경로 테스트 포함)와 `review`에서 새 high 발견이 없는 것.

| 순위 | 사례 | 변경 | 근거 강도 |
| --- | --- | --- | --- |
| 1 | data-platform | Redshift 서브넷 그룹 3개 서브넷·4노드 | MUST |
| 2 | data-platform | 피어링 + VGW → TGW 허브(두 VPC + VPN attachment), CGW 2 | MUST(동작)/HIGH |
| 3 | hybrid | 두 번째 CGW·VPN (TGW ECMP) | HIGH |
| 4 | microservices, eks-platform | `ecr.api` 엔드포인트, microservices에 `logs` | MUST(NAT 없을 때)/REC |
| 5 | three-tier | `ssmmessages`·`ec2messages` 엔드포인트 | REC |
| 6 | 전 사례 | practices 문구에 그릴 수 없는 권고(CloudFront·WAF·Route 53·DX·계정 분리·TGW 라우팅 테이블) 명시 | — |
| 7 | 선택 | microservices 3 AZ, eks Aurora reader 추가·/28 클러스터 서브넷, environments egress VPC (완료) | OPT |

수집 범위 확장 후보였던 CloudFront·WAF(엣지), Elastic IP(NLB 고정 IP), TGW 라우팅 테이블(환경 격리), Direct Connect는
소유자 승인으로 2026-10-18 수집·도형화했다(4.1절). 다중 계정 구성도는 계정 통합 보기로 구현했다(2026-10-18).

### 4.1 수집 범위 확장: 엣지·고정 IP·라우팅 도메인·전용 연결 (2026-10-18)

| 대상 | 수집 API | 구성도 | 사례 반영 |
| --- | --- | --- | --- |
| Elastic IP | `ec2:DescribeAddresses` (NAT·NLB는 AllocationId, EC2는 InstanceId, 그 밖은 ENI로 연결) | 붙은 리소스 라벨(`EIP 203.0.113.101`, NLB `EIP 2`), 별도 도형 없음 | three-tier NAT 3개, nlb-alb NLB AZ별 |
| TGW 라우팅 테이블 | `DescribeTransitGatewayRouteTables`, `GetTransitGatewayRouteTableAssociations`·`Propagations`, `SearchTransitGatewayRoutes`(state active·blackhole) | TGW 라벨(`라우팅 테이블 n`), VPC 카드(`TGW 라우팅 테이블 · 이름`), 속성 패널(연결·전파·경로·blackhole). 선으로 그리지 않음 | data-platform: `dp-spokes`(두 VPC, VPC·사내망 학습) / `dp-onprem`(VPN, VPC만 학습) |
| CloudFront | `cloudfront:ListDistributions`(전역) — 오리진 DomainName이 수집한 ELB의 DNSName과 같을 때만 연결, S3·사용자 지정(CNAME 포함)은 속성 | 리전 상자 밖 AWS Cloud 안의 엣지 열 → ALB(상세)·VPC 카드(전체) | three-tier: ALB + S3 정적 오리진 |
| AWS WAF | `wafv2:ListWebACLs`(REGIONAL: 수집 리전, CLOUDFRONT: us-east-1), `ListResourcesForWebACL`(ALB), `ListDistributions.WebACLId` | 보호 대상 아이콘 모서리의 WAF 표식(누르면 웹 ACL 속성), ALB 라벨·카드에 `WAF` | three-tier CloudFront ACL, nlb-alb ALB ACL |
| Direct Connect | `DescribeConnections`·`DescribeVirtualInterfaces`·`DescribeDirectConnectGateways`·`DescribeDirectConnectGatewayAssociations` | 연결은 VPN 고객 게이트웨이와 같은 데이터 센터 상자, VIF는 VGW·DX 게이트웨이까지, DX 게이트웨이 → TGW·VGW는 허브 줄 아래 전용 차선. 다른 리전 게이트웨이와의 연결은 속성으로만 | hybrid: 위치 2곳 전용 연결 2개, transit VIF → DX 게이트웨이 → TGW (VPN은 백업) |

검토 규칙 (모두 설정 근거만, 실제 접근·트래픽은 확인하지 않음):

| 규칙 | 심각도 | 판정 | 근거 |
| --- | --- | --- | --- |
| SEC-ALB-WAF | low | 인터넷 ALB에 리전 WAF 웹 ACL이 없고, 앞단 CloudFront에도 WebACLId가 없음. 그 계정·리전의 REGIONAL `ListWebACLs`·`ListResourcesForWebACL`이 모두 완료일 때만 | C6 (REC → low) |
| SEC-CF-ORIGIN-OPEN | low | CloudFront 오리진으로 연결된 ALB의 보안 그룹(모두 수집된 경우만)이 0.0.0.0/0·::/0 허용 → CloudFront·WAF 우회 가능한 설정 | CloudFront origin-facing 접두사 목록·VPC 오리진 권고 |
| COST-EIP-IDLE | low (비용) | AssociationId·InstanceId·NetworkInterfaceId가 없고 어느 리소스와도 연결되지 않은 Elastic IP | 퍼블릭 IPv4 시간당 과금 |
| REL-TGW-BLACKHOLE | low | 라우팅 테이블에 state = blackhole 경로 | 스포크 간 격리(REC)일 수 있어 정보 수준, 삭제된 attachment의 잔여 경로면 정리 |

3.1 three-tier `엣지`, 3.3 nlb-alb `고정 IP`·`WAF`, 3.5 hybrid `주 연결`은 이 확장으로 충족한다. 3.4 environments의 TGW 격리는 이제 그릴 수 있지만
그 사례에는 아직 반영하지 않았다(라우팅 도메인 예시는 data-platform).

AI/GPU 워크로드 관련 변경안(신규 AI 사례, GPU 아이콘, 검토 규칙)은 5.5–5.9절에 따로 둔다.

## 5. AI/GPU 워크로드 (Well-Architected)

기준 렌즈: **Generative AI Lens**(2025-11-19, Bedrock·SageMaker AI·Amazon Q 대상, 6개 기둥 × 수명 주기),
**Machine Learning Lens**(2025-11-19, BP ID가 `MLSEC04-BP01` 형식으로 바뀌어 예전 `mlsec-08.html` 링크는 쓰지 않음),
GPU 클러스터의 AZ 판단에는 **HPC Lens**. 강도는 각 렌즈의 "Level of risk exposed if not established"(HIGH/MEDIUM)를 따르고,
플랫폼의 물리적 제약은 MUST로 적는다.

### 5.1 구성도에 드러나는 모범 사례

| BP | 위험도 | 구성도 요소 | 요지 |
| --- | --- | --- | --- |
| GENSEC01-BP02 FM과 앱 사이 사설 통신 | HIGH | `bedrock-runtime`·SageMaker 인터페이스 엔드포인트, 프라이빗 서브넷, 사설 경로의 벡터 저장소 | 사설 통신이 없으면 엔드포인트를 공용 인터넷으로 호출하게 됨. RAG의 벡터 DB 접근도 사설 경로로 |
| GENSEC01-BP01 FM 엔드포인트 최소 권한 | HIGH | 엔드포인트 정책, VPC 조건 | 특정 VPC에서 온 요청만 허용하는 조건 고려 |
| GENSEC01-BP03 FM에서 데이터 저장소로의 최소 권한 | HIGH | VPC 안의 벡터 저장소, 보안 그룹 | 벡터 저장소를 보안 VPC에 두고 SG로 노드·데이터 소스 간 통신 제한 |
| GENSEC01-BP04 접근 모니터링 | HIGH | `logs` 엔드포인트 (설정은 문구) | Bedrock 모델 호출 로깅은 기본 꺼짐, `bedrock-runtime` 경유 호출만 기록 |
| GENSEC02-BP01 Guardrails | HIGH | 문구 | 자체 호스팅 모델에도 SDK로 적용 가능 |
| GENREL02-BP01 이중화된 네트워크 연결 | MEDIUM | 2 AZ 이상, 다중 AZ 엔드포인트, 다중 AZ 벡터 DB, TGW | 앱·벡터 DB·에이전트를 최소 두 AZ에 |
| GENREL01-BP01 처리량을 사용률에 맞춤 | MEDIUM | 로드 밸런서 뒤 ASG / 노드 그룹 | 자체 호스팅 엔드포인트는 LB 뒤 Auto Scaling, Bedrock은 Provisioned Throughput 또는 교차 리전 추론(둘은 함께 쓸 수 없음) |
| GENREL05-BP01 추론 부하 분산 | MEDIUM | 다중 AZ 추론, 오토 스케일링 | 다중 AZ 추론 엔드포인트 + 오토 스케일링 |
| GENREL06-BP01 분산 학습 내결함성 | HIGH | 체크포인트 저장소(FSx for Lustre·S3, 문구) | 노드 장애·Spot 중단을 체크포인트로 복구 |
| MLSEC03-BP02 안전한 데이터·모델링 환경 | HIGH | 인터넷 경로 없는 VPC, PrivateLink, 사설 ECR | "ML 환경의 무제한 인터넷 접근"이 안티 패턴 |
| MLSEC04-BP01 거버넌스 환경 | HIGH | 문구(개발·학습·운영 계정 분리) | SG·NACL과 PrivateLink |
| MLREL05-BP01 모델 엔드포인트 자동 확장 | HIGH | ASG·노드 그룹 min/max | — |
| MLCOST06-BP03 엔드포인트 사용량 모니터링·적정 규모 | HIGH | 서로 다른 AZ의 서브넷 2개 이상, FSx와 학습 노드는 같은 AZ | — |
| MLPERF04-BP01 학습·추론 인스턴스 최적화 | MEDIUM | 학습·추론 노드 그룹 분리 | 학습과 추론에 같은 인스턴스 유형을 쓰는 것이 안티 패턴 |
| MLSUS05-BP02 효율적인 실리콘 · GENSUS01-BP01 | MEDIUM | 인스턴스 유형(Inf2·Trn2), ASG min 0 | 자체 호스팅 모델은 미사용 시 0까지 축소 |

### 5.2 GPU 인프라의 물리적 제약 — AZ 긴장 관계

- **EFA 트래픽은 AZ·VPC를 넘을 수 없고 라우팅되지 않는다** (EC2 EFA, MUST). 강결합 학습 노드는 한 AZ에 둔다.
- **클러스터 배치 그룹은 여러 AZ에 걸칠 수 없다.** 한 인스턴스 유형·한 번의 시작 요청, 그룹 안 ODCR 권장.
- **Capacity Blocks는 배치 그룹을 지원하지 않는다.** 대신 EC2 UltraCluster 안에 자동으로 가깝게 배치된다.
  학습 노드 그룹은 배치 그룹과 Capacity Block 중 **하나만** 쓴다. Capacity Block은 오토 스케일링을 지원하지 않는다.
- **HPCREL01-BP02**: 강결합 워크로드는 단일 AZ·배치 그룹에 두고, FSx for Lustre·S3의 중앙 데이터와 IaC로 다른 AZ에서
  다시 띄워 복구한다. 공유 스토리지는 같은 AZ에(HPCREL01-BP01).
- **EFA 보안 그룹**: 모든 EFA 인스턴스가 같은 SG를 쓰고 자기 자신을 참조하는 전체 허용 규칙이 있어야 한다. 없으면
  EFA 트래픽이 "조용히" 실패한다(EKS AI/ML Networking, HyperPod 사전 조건). 아웃바운드 0.0.0.0/0은 피한다.
- **IP 계획**: EKS에서 P5 한 대가 81개 IP를 쓰고 서브넷 CIDR은 나중에 바꿀 수 없다(HyperPod). /19 서브넷, IPv6 또는
  `100.64.0.0/10` 보조 CIDR + 접두사 위임 권장(EKS IP 최적화). 대형 GPU 노드의 warm ENI가 서브넷 IP를 고갈시킬 수 있다.
- **Spot**: ML Lens는 관리형 Spot 학습으로 최대 90% 절감을 권하지만, EFA 동일 AZ 배치는 "상관된 중단 위험"이 있어
  EKS 가이드는 RI·ODCR·Savings Plans·Capacity Blocks를 권한다. 소규모·비강결합 학습에만 Spot.

**정리**: 추론·데이터·엔드포인트는 3 AZ(GENREL02-BP01, MLCOST06-BP03), 강결합 학습은 의도적으로 단일 AZ(EFA 제약,
HPCREL01-BP02). 학습의 복구 수단은 다중 AZ 배치가 아니라 체크포인트(FSx/S3)와 다른 AZ 재시작이다.

### 5.3 EKS AI/ML 모범 사례 (EKS Best Practices Guide — AI/ML)

- 컴퓨팅: P·Trn은 Capacity Blocks, G는 ODCR. 예약 용량은 관리형 노드 그룹, 변동 수요는 Karpenter. GPU 노드 taint,
  Node Monitoring Agent + Node Auto Repair. Bottlerocket 가속 AMI에는 NVIDIA device plugin 포함(AL2023 가속 AMI는 미포함).
  추론 확장은 요청 수·토큰 처리량 기반 KEDA.
- 스토리지: 다중 노드 학습·서빙은 S3 연동 FSx for Lustre(같은 AZ, GPU가 여러 AZ면 AZ마다 하나), 여러 AZ에서 모델을
  공유하는 실시간 추론은 EFS, 단일 인스턴스는 Mountpoint for S3. S3는 SSE-KMS + Bucket Key.
- 사설 클러스터 필수 엔드포인트: `ec2` · `ecr.api` · `ecr.dkr` · `s3` · `elasticloadbalancing` · `logs` · `sts`(IRSA) ·
  `eks-auth`(Pod Identity) · `eks` · `oidc-eks`. 엔드포인트 SG는 노드 서브넷 CIDR 또는 노드 SG에서 443 허용.
- 참조 구현: AWS Solutions Guidance *Scalable Model Inference and Agentic AI on Amazon EKS*(공식) — 3 AZ와 AZ별 VPC
  엔드포인트, 애드온용 관리형 노드 그룹 + GPU·Graviton·Inferentia용 Karpenter, WAF 뒤 ELB, RAG용 OpenSearch.
  *AI on EKS*(awslabs)는 AWS 팀 프로젝트지만 공식 문서는 아니다.

### 5.4 Bedrock·SageMaker 네트워크

- Bedrock 인터페이스 엔드포인트: `bedrock`(제어) · `bedrock-runtime` · `bedrock-agent` · `bedrock-agent-runtime`
  (+ `bedrock-mantle`, FIPS 변형). 기본 엔드포인트 정책은 전체 허용이라 `InvokeModel`·`InvokeModelWithResponseStream`으로 제한.
- 교차 리전 추론은 AWS 네트워크 안에서만 이동. 지리 프로필은 데이터가 해당 지리권에 머문다. 글로벌 프로필은 SCP 조정 필요.
- **Knowledge Bases는 VPC 안의 OpenSearch 도메인(관리형)을 지원하지 않는다**(공용 접근 필요). 사설 RAG 저장소는
  OpenSearch Serverless(인터페이스 엔드포인트 `aoss-data`) 또는 Aurora PostgreSQL(pgvector, 같은 계정).
  VPC 안 OpenSearch 도메인은 자체 구현 RAG에서만 쓴다.
- SageMaker: `sagemaker.api` · `sagemaker.runtime`. 런타임 엔드포인트는 클라이언트가 있는 AZ마다 있어야 사설 DNS가 동작.
- HyperPod(EKS 오케스트레이션): EKS와 같은 VPC의 프라이빗 서브넷, 기본은 단일 AZ(다중 AZ는 인스턴스 그룹을 AZ별로 분리),
  인터넷이 없으면 S3 엔드포인트 필수, FSx는 노드와 같은 AZ.

### 5.5 사례: `ai-platform` (사설 EKS GPU 플랫폼)

수집 가능한 요소만으로 그릴 수 있는 사설 AI 플랫폼. 근거는 괄호 안.

| 요소 | 구성 | 근거 |
| --- | --- | --- |
| VPC | 10.80.0.0/16, IGW·NAT 없음, 사내·공용 망은 TGW attachment로 진입 | MLSEC03-BP02, GENSEC01-BP02 |
| 서브넷 | `app-a/b/c` /19(노드·엔드포인트), `data-a/b/c` /24(Aurora·EFS), `train-a` /20 **AZ a만** | EKS IP 최적화, HyperPod IP 계획 |
| 라우팅 | 계층별 라우팅 테이블, TGW 경로, S3 게이트웨이 엔드포인트 | EKS 사설 클러스터, HyperPod |
| 인터페이스 엔드포인트 (3 AZ) | `bedrock-runtime`, `bedrock-agent-runtime`, `sagemaker.runtime`(선택), `ecr.api`, `ecr.dkr`, `sts`, `eks`, `eks-auth`, `ec2`, `elasticloadbalancing`, `logs`, `ssm` | GENSEC01-BP02, GENREL02-BP01, EKS 사설 클러스터 |
| EKS | 비공개 엔드포인트. 노드 그룹 `system`(m7g.large, 3 AZ), `gpu-inference`(g6e.2xlarge, min 2, 3 AZ, taint), `gpu-train`(p5.48xlarge, `train-a`만, min 0) | Solutions Guidance, MLCOST06-BP03, GENREL05-BP01, EFA 제약 |
| EFA SG | `sg-train-efa`: 자기 참조 전체 허용, 0.0.0.0/0 아웃바운드 없음 | EKS AI/ML Networking, HyperPod |
| 학습 배치·데이터 | 학습 노드 EFA 인터페이스 + 클러스터 배치 그룹 `ai-train-pg`, 같은 AZ의 FSx for Lustre PERSISTENT_2(체크포인트) | EC2 placement strategies, GENREL06-BP01, HPCREL01-BP01 |
| 진입 | 내부 ALB(3 AZ) → 추론 파드 IP 대상 | GENREL01-BP01, MLREL05-BP01 |
| 벡터 저장소 | Aurora PostgreSQL(pgvector) writer + 다른 AZ reader, 노드 SG만 허용 | GENSEC01-BP03, GENREL02-BP01, Bedrock KB 지원 |
| 모델 캐시 | EFS, 3 AZ 마운트 타깃 | EKS AI/ML Storage |

문구로만 남길 것(수집 안 함): FSx의 S3 데이터 리포지토리 연동, S3 버킷(모델·데이터셋·호출 로그, SSE-KMS, `aws:SourceVpce`), Bedrock 호출 로깅·Guardrails·엔드포인트
정책·교차 리전 추론 또는 Provisioned Throughput, HyperPod, Karpenter·KEDA·device plugin(Karpenter 노드는 ASG 없는 EC2로 보임),
공개 진입 변형의 WAF·CloudFront·API Gateway.

### 5.6 사례: `genai-rag` (Bedrock RAG 애플리케이션)

| 요소 | 구성 | 근거 |
| --- | --- | --- |
| 진입·앱 | 인터넷 ALB(퍼블릭) → ECS Fargate `chat-api` 3 AZ, 수집용 `ingest-worker`·VPC Lambda `document-ingest` | REL10-BP01, SEC05-BP01 |
| 모델 호출 | `bedrock-runtime`·`bedrock-agent-runtime`·`ecr.api`·`ecr.dkr`·`logs`·`secretsmanager`·`sts` 인터페이스 엔드포인트 3 AZ, S3 게이트웨이 | GENSEC01-BP02, GENREL02-BP01 |
| 벡터 저장소 | Aurora PostgreSQL(pgvector) writer a + reader b·c, 앱 태스크 SG만 5432 | GENSEC01-BP03, GENREL02-BP01, GENPERF04·GENCOST04(벡터 저장소 선택) |
| 캐시 | ElastiCache Redis primary b / replica c (시맨틱 캐시·세션) | REL10-BP01 |
| 자체 호스팅 모델 | VPC 안 SageMaker 실시간 엔드포인트 `rag-reranker` ml.g5.xlarge × 2, 서브넷 2 AZ | MLSEC03-BP02, MLCOST06-BP03 |
| 문구 | Guardrails, 모델 호출 로깅, 엔드포인트 정책 InvokeModel 한정, 교차 리전 추론 또는 Provisioned Throughput, WAF·CloudFront | GENSEC02-BP01, GENSEC01-BP04·BP01, GENREL01-BP01, REL02-BP01 |

### 5.7 사례: `gpu-training` (GPU 분산 학습 클러스터)

| 요소 | 구성 | 근거 |
| --- | --- | --- |
| 리전 | us-east-2(오하이오). p5와 trn2를 함께 제공하는 리전이어야 한다(서울 리전에는 trn2 없음; CloudFormation 내보내기의 cfn-lint 지역별 인스턴스 목록으로 확인, 2026-10-18) | EC2 인스턴스 유형 리전 가용성 |
| 서브넷 | `compute-a` /20 (AZ a만, 학습 노드·FSx·HyperPod), `head-a/b` /24 (헤드·로그인 노드), 퍼블릭 a/b (NAT) | HyperPod IP 계획, HPCREL01-BP02 |
| p5 큐 | p5.48xlarge × 4, EFA, **Capacity Block**(UltraCluster 자동 배치, 배치 그룹 없음) | EC2 Capacity Blocks |
| trn2 큐 | trn2.48xlarge × 2, EFA, **클러스터 배치 그룹** `trn2-pg` + 용량 예약(ODCR) | EC2 placement strategies, MLSUS05-BP02 |
| EFA SG | 자기 참조 전체 허용, 헤드 노드에서만 22, 이그레스는 자신·VPC | EKS AI/ML Networking |
| 데이터 | FSx for Lustre PERSISTENT_2 18.75 TiB, 학습 노드와 같은 서브넷 | GENREL06-BP01, HPCREL01-BP01, MLCOST06-BP03 |
| 관리형 대안 | SageMaker HyperPod(Slurm) `hp-slurm`: controller ml.m5.2xlarge × 1, workers ml.p5.48xlarge × 8 | GENPERF03-BP01 |
| 접근·사설 경로 | 헤드·로그인 노드 프라이빗, SSM·SSM Messages·EC2 Messages·Logs·ECR·STS 엔드포인트, S3 게이트웨이 | MLSEC03-BP02 |

### 5.8 AI/ML 검토 규칙 (검토 탭 `AI/ML` 영역)

모두 수집한 API 필드만으로 판정하고, 권장 조치에 렌즈 BP를 적는다. 입력이 없으면(권한 거부 등) 판정하지 않는다.

| 규칙 | 심각도 | 판정 근거 (API 필드) | 렌즈·문서 |
| --- | --- | --- | --- |
| SEC-AI-PORT-OPEN | high | SG가 0.0.0.0/0에 Jupyter 8888·TensorBoard 6006·Ray 8265/10001·MLflow 5000·Gradio 7860·Streamlit 8501·Ollama 11434·vLLM/Triton 8000/8001 허용 | MLSEC03-BP02 |
| SEC-GPU-PUBLIC | high | 가속 인스턴스(p·g·trn·inf·dl)에 퍼블릭 IP 또는 IGW 경로 서브넷 | MLSEC03-BP02, GENSEC01-BP02 |
| SEC-HYPERPOD-PUBLIC | high | HyperPod `VpcConfig.Subnets`가 IGW 경로 서브넷 | GENSEC01-BP02, HyperPod 사전 조건 |
| REL-GPU-TRAIN-AZ | medium | EFA 학습 그룹이 여러 AZ | EC2 EFA(AZ 불가), EKS AI/ML Networking |
| REL-GPU-INFER-AZ | medium | G·Inferentia 추론 그룹이 한 AZ | GENREL02-BP01, MLCOST06-BP03 |
| SEC-EFA-SG | medium | EFA 노드 SG에 자기 참조 `IpProtocol -1` 없음 | EKS AI/ML Networking, HyperPod |
| REL-GPU-SPOT-EFA | medium | EFA 노드 `InstanceLifecycle = spot` | EKS AI/ML Networking (상관 중단) |
| REL-GPU-SUBNET-IP | medium | p5 그룹의 (MaxSize − 현재) × 81(EKS)/32 > `AvailableIpAddressCount` 합 | HyperPod 사전 조건, EKS IP 최적화 |
| REL-FSX-TRAIN-AZ | medium | EFA 학습 노드 AZ에 같은 VPC의 Lustre가 없음 | HPCREL01-BP01, MLCOST06-BP03 |
| SEC-AI-ENDPOINT-DNS | medium | bedrock*/sagemaker.* 인터페이스 엔드포인트 `PrivateDnsEnabled = false` | GENSEC01-BP02 |
| REL-AI-ENDPOINT-AZ | medium | bedrock*/sagemaker.* 엔드포인트 서브넷 AZ ⊉ VPC 컴퓨팅(EC2·ECS 태스크) AZ | GENREL02-BP01, SageMaker 런타임 엔드포인트 |
| SEC-SAGEMAKER-VPC | medium | SageMaker 엔드포인트 모델에 `VpcConfig` 없음 | MLSEC03-BP02 |
| REL-SAGEMAKER-AZ | medium | 엔드포인트 인스턴스 < 2 또는 서브넷 AZ < 2 | MLCOST06-BP03, MLREL05-BP01 |
| OPS-GPU-PLACEMENT | low | EFA 노드가 cluster 전략 배치 그룹·Capacity Block 밖 | EC2 placement strategies, Capacity Blocks |
| OPS-FSX-SCRATCH | low | Lustre `DeploymentType = SCRATCH_*` | GENREL06-BP01 |
| OPS-GPU-S3-ENDPOINT | low | 가속 인스턴스 VPC에 S3 게이트웨이 엔드포인트 없음 | MLSEC03-BP02 |
| REL-ASG-AZ (학습) | low | 단일 AZ EFA 학습 그룹은 의도적 단일 AZ 알림(일반 ASG는 medium) | HPCREL01-BP02 |

### 5.9 AI/GPU 변경안 이력

**반영 현황 (2026-10-18):** A1–A3 완료. 이어서 소유자 요청("AI/GPU가 강점이 되어야")으로 수집(FSx·SageMaker 엔드포인트·HyperPod·배치 그룹·EFA/Capacity Block), 사례(genai-rag·gpu-training, ai-platform 보강), 검토 규칙 13개(5.8절), 채팅 도구(add_fsx_lustre·add_sagemaker_endpoint, 학습 그룹의 전용 서브넷·EFA·배치 그룹)를 추가했다. A2는 아이콘에 계열 이름이 찍혀 있어(P3·P4d·G4dn) 정확히 같은 계열에만 전용 아이콘을 쓰고, p5·g6e 등은 일반 인스턴스 아이콘 + `GPU` 라벨로 바꿨다. 추가로 VPC 상단 연결 행이 AZ 폭에서 줄바꿈된다(엔드포인트 12개인 ai-platform이 AZ 영역의 세 배로 넓어지던 문제).

| 순위 | 변경 | 비고 |
| --- | --- | --- |
| A1 | `ai-platform` 샘플·표준 설계 추가 (5.5절) | 샘플 빌더가 지원하는 요소만 사용. 채팅 에이전트 표준 설계 8번째 |
| A2 | 가속 인스턴스 아이콘: p·g·trn·inf·dl 계열 EC2를 AWS 아이콘(`p4d_instance`, `trainium_instance` 등)으로, `bedrock*` 엔드포인트에 Bedrock 아이콘 | 고정판 draw.io 32.4.1 AWS4 라이브러리에 아이콘이 있음을 확인 |
| A3 | 검토 규칙: 학습 노드 그룹(p·trn 계열)이 여러 AZ에 걸치면 경고(EFA), 추론 GPU 노드 그룹이 한 AZ뿐이면 경고(GENREL02-BP01), EFA SG에 자기 참조 규칙이 없으면 경고, GPU 노드 VPC에 `bedrock-runtime` 엔드포인트 없이 NAT만 있으면 정보 | 모두 수집한 API 필드(인스턴스 유형·서브넷·SG 규칙·엔드포인트)만으로 판정 가능 |

## 6. 출처

모두 2026-10-18에 내용을 확인한 페이지다(† 표시는 AWS가 "historical"로 표시).

- Well-Architected: [REL10-BP01](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_fault_isolation_multiaz_region_system.html) ·
  [REL02-BP01](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_planning_network_topology_ha_conn_users.html) ·
  [REL02-BP02](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_planning_network_topology_ha_conn_private_networks.html) ·
  [SEC05-BP01](https://docs.aws.amazon.com/wellarchitected/latest/security-pillar/sec_network_protection_create_layers.html)
- VPC: [Security best practices](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-security-best-practices.html) ·
  [Example: private subnets and NAT](https://docs.aws.amazon.com/vpc/latest/userguide/vpc-example-private-subnets-nat.html) ·
  [TGW: how it works / isolated VPCs with shared services](https://docs.aws.amazon.com/vpc/latest/tgw/transit-gateway-isolated-shared.html)
- 백서: [Building a Scalable and Secure Multi-VPC AWS Network Infrastructure](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/welcome.html)
  ([TGW](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/transit-gateway.html),
  [peering](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/vpc-peering.html),
  [VPN](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/vpn.html),
  [centralized egress](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/centralized-egress-to-internet.html),
  [centralized inspection](https://docs.aws.amazon.com/whitepapers/latest/building-scalable-secure-multi-vpc-network-infrastructure/centralized-network-security-for-vpc-to-vpc-and-on-premises-to-vpc-traffic.html)) ·
  [Organizing Your AWS Environment Using Multiple Accounts](https://docs.aws.amazon.com/whitepapers/latest/organizing-your-aws-environment/organizing-your-aws-environment.html) ·
  [Implementing Microservices on AWS](https://docs.aws.amazon.com/whitepapers/latest/microservices-on-aws/microservices-on-aws.html) ·
  [Hybrid Connectivity: Reliability](https://docs.aws.amazon.com/whitepapers/latest/hybrid-connectivity/reliability.html)† ·
  [Web Application Hosting](https://docs.aws.amazon.com/whitepapers/latest/web-application-hosting-best-practices/welcome.html)†
- Prescriptive Guidance: [AWS SRA — Network account](https://docs.aws.amazon.com/prescriptive-guidance/latest/security-reference-architecture/network.html)
- 연결: [Redundant Site-to-Site VPN](https://docs.aws.amazon.com/vpn/latest/s2svpn/vpn-redundant-connection.html) ·
  [Customer gateway devices](https://docs.aws.amazon.com/vpn/latest/s2svpn/your-cgw.html) ·
  [Direct Connect resiliency](https://aws.amazon.com/directconnect/resiliency-recommendation/) ·
  [Network Firewall deployment models](https://aws.amazon.com/blogs/networking-and-content-delivery/deployment-models-for-aws-network-firewall/)
- ELB: [ALB as NLB target](https://docs.aws.amazon.com/elasticloadbalancing/latest/network/application-load-balancer-target.html) ·
  [ALB-type target group 블로그](https://aws.amazon.com/blogs/networking-and-content-delivery/application-load-balancer-type-target-group-for-network-load-balancer/) ·
  [ALB target groups](https://docs.aws.amazon.com/elasticloadbalancing/latest/application/load-balancer-target-groups.html)
- 컨테이너: [ECS outbound networking](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/networking-outbound.html) ·
  [ECR interface endpoints](https://docs.aws.amazon.com/AmazonECR/latest/userguide/vpc-endpoints.html) ·
  [EKS subnets](https://docs.aws.amazon.com/eks/latest/best-practices/subnets.html) ·
  [EKS private clusters](https://docs.aws.amazon.com/eks/latest/userguide/private-clusters.html) ·
  [EKS ALB ingress](https://docs.aws.amazon.com/eks/latest/userguide/alb-ingress.html) ·
  [Systems Manager VPC endpoints](https://docs.aws.amazon.com/systems-manager/latest/userguide/setup-create-vpc.html) ·
  [Lambda VPC](https://docs.aws.amazon.com/lambda/latest/dg/configuration-vpc.html)
- 데이터: [MSK best practices](https://docs.aws.amazon.com/msk/latest/developerguide/bestpractices.html) ·
  [OpenSearch Multi-AZ](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/managedomains-multiaz.html) ·
  [ElastiCache auto failover](https://docs.aws.amazon.com/AmazonElastiCache/latest/dg/AutoFailover.html) ·
  [RDS Multi-AZ](https://docs.aws.amazon.com/AmazonRDS/latest/UserGuide/Concepts.MultiAZ.html) ·
  [Aurora HA](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/Concepts.AuroraHighAvailability.html) ·
  [Redshift Multi-AZ](https://docs.aws.amazon.com/redshift/latest/mgmt/managing-cluster-multi-az.html) ·
  [DocumentDB replication](https://docs.aws.amazon.com/documentdb/latest/developerguide/replication.html) ·
  [EFS mount targets](https://docs.aws.amazon.com/efs/latest/ug/accessing-fs.html)
- 표기: [AWS Architecture Icons](https://aws.amazon.com/architecture/icons/) (release 24 PPTx 지침)
- AI/ML 렌즈: [Generative AI Lens](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/generative-ai-lens.html)
  ([GENSEC01-BP02](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gensec01-bp02.html),
  [GENSEC01-BP03](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gensec01-bp03.html),
  [GENSEC01-BP04](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/gensec01-bp04.html),
  [GENREL01-BP01](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel01-bp01.html),
  [GENREL02-BP01](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel02-bp01.html),
  [GENREL06-BP01](https://docs.aws.amazon.com/wellarchitected/latest/generative-ai-lens/genrel06-bp01.html)) ·
  [Machine Learning Lens](https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/machine-learning-lens.html)
  ([MLSEC03-BP02](https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/mlsec03-bp02.html),
  [MLSEC04-BP01](https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/mlsec04-bp01.html),
  [MLCOST06-BP03](https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/mlcost06-bp03.html),
  [MLPERF04-BP01](https://docs.aws.amazon.com/wellarchitected/latest/machine-learning-lens/mlperf04-bp01.html)) ·
  [HPC Lens — tightly coupled](https://docs.aws.amazon.com/wellarchitected/latest/high-performance-computing-lens/tightly-coupled-scenarios.html)
- GPU 인프라: [EFA](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/efa.html) ·
  [Placement strategies](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/placement-strategies.html) ·
  [Capacity Blocks for ML](https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/ec2-capacity-blocks.html) ·
  [HyperPod prerequisites](https://docs.aws.amazon.com/sagemaker/latest/dg/sagemaker-hyperpod-prerequisites.html)
- EKS AI/ML: [AI/ML 개요](https://docs.aws.amazon.com/eks/latest/best-practices/aiml.html) ·
  [Networking](https://docs.aws.amazon.com/eks/latest/best-practices/aiml-networking.html) ·
  [Compute](https://docs.aws.amazon.com/eks/latest/best-practices/aiml-compute.html) ·
  [Storage](https://docs.aws.amazon.com/eks/latest/best-practices/aiml-storage.html) ·
  [IP 최적화](https://docs.aws.amazon.com/eks/latest/best-practices/ip-opt.html) ·
  [Guidance: Scalable Model Inference and Agentic AI on EKS](https://docs.aws.amazon.com/solutions/scalable-model-inference-and-agentic-ai-on-amazon-eks/) ·
  [AI on EKS](https://awslabs.github.io/ai-on-eks/) (awslabs, 비공식)
- Bedrock·SageMaker: [Bedrock interface endpoints](https://docs.aws.amazon.com/bedrock/latest/userguide/vpc-interface-endpoints.html) ·
  [Bedrock VPC 사용](https://docs.aws.amazon.com/bedrock/latest/userguide/usingVPC.html) ·
  [Knowledge Base 설정](https://docs.aws.amazon.com/bedrock/latest/userguide/knowledge-base-setup.html) ·
  [Cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html) ·
  [Model invocation logging](https://docs.aws.amazon.com/bedrock/latest/userguide/model-invocation-logging.html) ·
  [OpenSearch Serverless VPC endpoints](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/serverless-vpc.html) ·
  [SageMaker interface endpoints](https://docs.aws.amazon.com/sagemaker/latest/dg/interface-vpc-endpoint.html) ·
  [RAG options (Prescriptive Guidance)](https://docs.aws.amazon.com/prescriptive-guidance/latest/retrieval-augmented-generation-options/introduction.html)
