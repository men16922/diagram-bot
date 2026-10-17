# 사례 아키텍처 기준 (AWS Best Practice)

Last Updated: 2026-10-18

샘플 7종(`src/diagram_bot/samples.py`)은 데모 데이터이자 채팅 에이전트의 표준 설계(`designer.py` `STANDARDS`)다.
이 문서는 각 사례가 따라야 할 AWS 공식 권고를 출처와 함께 정리하고, 현재 샘플과의 차이와 변경안을 기록한다.
변경안은 제안이며, 샘플 수정은 소유자 승인 후 진행한다.

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
| 수집·도형화 가능 | VPC, 서브넷, 라우팅 테이블, IGW, NAT, VPC 엔드포인트, TGW·attachment, 피어링, VGW·CGW·Site-to-Site VPN, ELB(ALB/NLB/GWLB)·대상 그룹, EC2·ASG, ECS, EKS·노드 그룹, Lambda, RDS·Aurora·DocumentDB, ElastiCache, MSK, OpenSearch, EFS, Redshift, 보안 그룹 |
| 수집 안 함 (도형 불가) | CloudFront, AWS WAF, Shield, Route 53(Resolver 포함), Global Accelerator, Network Firewall, Direct Connect, Elastic IP, TGW 라우팅 테이블, NACL, 리스너 규칙, Organizations(계정·OU), S3, DynamoDB |
| 구조상 제약 | 스냅샷은 계정 × 리전 하나 단위라 한 구성도에 여러 계정을 그릴 수 없다 |

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
| SSM 엔드포인트 | `ssm` · `ssmmessages` · `ec2messages` | REC | `ssm`만 | **S** 두 엔드포인트 추가 |
| 엣지 | CloudFront(+S3 정적 콘텐츠) · WAF · Route 53 | REC | 없음 | **P** / X |

### 3.2 microservices — ALB 경로 라우팅 (EC2 ASG + ECS Fargate)

출처: Implementing Microservices on AWS, ALB target groups, ECS outbound networking, ECR interface endpoints.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 서비스별 대상 그룹 | 마이크로서비스마다 대상 그룹 | REC | 4개 | 충족 |
| Fargate 배치 | 프라이빗 서브넷, 퍼블릭 IP 없음, AZ마다 NAT | REC | 충족 | 충족 |
| ECR 엔드포인트 | `ecr.api` + `ecr.dkr` + S3 게이트웨이 (NAT 없으면 필수), 로그는 `logs` | MUST(NAT 없을 때)/REC | `ecr.dkr` + S3 | **S** `ecr.api`·`logs` 추가 |
| AZ | 2 AZ 이상 (참조 다이어그램 3 AZ) | HIGH/OPT | 2 AZ | **S**(선택) 3 AZ |
| 엣지 | ALB 앞 WAF·CloudFront | REC | 없음 | **P** / X |

### 3.3 nlb-alb — 고정 IP 진입점 (NLB → ALB)

출처: NLB "Use an ALB as a target", ALB-type target group 블로그(2021-09), REL02-BP01.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 같은 VPC·계정 | NLB와 ALB는 같은 VPC | MUST | 충족 | 충족 |
| 리스너 | `alb` 대상 그룹은 TCP 리스너, 포트 = ALB 리스너 포트 | MUST | TCP 443 → ALB 443 | 충족 |
| AZ 일치 | NLB와 ALB에 같은 AZ | REC | NLB는 퍼블릭 a/c, ALB는 프라이빗 a/c | 충족 |
| 고정 IP | AZ마다 Elastic IP | REC | EIP 미표시 | **X** (EIP 수집 시 라벨) / P |
| WAF | ALB에 WAF | REC | 없음 | **P** |
| 대안 | Global Accelerator(애니캐스트 고정 IP 2개) | OPT | — | P |

### 3.4 environments — 환경 분리 (운영 · 스테이징 · 공용)

출처: Organizing Your AWS Environment Using Multiple Accounts(2025-04), Multi-VPC 백서(TGW, centralized egress),
SRA Network account, TGW isolated VPCs with shared services.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 계정 분리 | 환경마다 계정, TGW는 Network 계정에서 RAM 공유 | REC(강함) | 한 계정 | **X** (다중 계정 구성도) / P |
| TGW 격리 | 운영·스테이징 별도 라우팅 테이블, 공용만 양쪽 전파, 스포크 간 blackhole | REC | 모든 VPC가 같은 10.0.0.0/8 경로 | **P** (TGW 라우팅 테이블 미수집) |
| TGW attachment | AZ마다 서브넷 하나 | REC | 운영 3 AZ, 스테이징 2 AZ, 공용 2 AZ | 충족 |
| 운영 NAT | AZ마다 | REC | AZ마다 | 충족 |
| 스테이징 NAT 1개 | 비운영 비용 절충 (REL10은 운영 대상) | — | NAT 1개 | **P** "비용 절충" 명시 |
| 중앙 egress / 검사 VPC | NAT 집중 egress VPC, 검사 VPC(TGW appliance mode) 또는 TGW Network Firewall attachment | OPT | 없음 | **S**(선택) egress VPC는 그릴 수 있음, 방화벽은 X |

### 3.5 hybrid — 하이브리드 (TGW + Site-to-Site VPN)

출처: REL02-BP02, Redundant Site-to-Site VPN connections, Customer gateway devices, Multi-VPC 백서(VPN),
Direct Connect Resiliency Recommendations, ALB target groups(IP 대상).

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| VPN 종단 | TGW (ECMP 지원, 터널당 1.25 Gbps) | REC | TGW | 충족 |
| 이중화 | 두 번째 CGW(가능하면 다른 ISP)로 두 번째 VPN, 같은 대역 BGP 광고, 장비 이중화 | HIGH | CGW 1, VPN 1(터널 2) | **S** CGW·VPN 하나 더 |
| 터널 | CGW에서 두 터널 모두 사용 | MUST(실무) | 터널 2개 UP | 충족 |
| 주 연결 | 운영은 Direct Connect, VPN은 백업 | REC | VPN만 | **X** / P |
| IP 대상 | RFC1918/RFC6598만, DX·VPN 경유 온프레미스 허용 | MUST | 192.168.20.0/24 | 충족 |
| DNS | Route 53 Resolver 엔드포인트 | OPT | 없음 | P |

### 3.6 eks-platform — EKS 플랫폼

출처: EKS Best Practices(VPC and Subnet), EKS private clusters, EKS ALB ingress, MSK best practices,
Aurora high availability, ElastiCache Multi-AZ, Lambda VPC.

| 항목 | AWS 권고 | 강도 | 현재 샘플 | 조치 |
| --- | --- | --- | --- | --- |
| 엔드포인트 접근 | 일반 권장은 public+private(CIDR 제한), 격리 필요 시 private | REC | private | 충족(의도 명시) |
| 노드 | 세 AZ 프라이빗 서브넷, AZ마다 NAT | REC | 충족 | 충족 |
| 클러스터 서브넷 | 전용 /28 서브넷 | OPT | 노드 서브넷 공용 | **S**(선택) |
| LB 서브넷 | ALB는 퍼블릭(`kubernetes.io/role/elb` 태그), 2 AZ 이상, Fargate면 IP 대상 | REC | 3 AZ, IP 대상 | 충족 (태그 **S**) |
| VPC 엔드포인트 | NAT가 없으면 `ec2`·`ecr.api`·`ecr.dkr`·`s3`·`elasticloadbalancing`·`logs`·`sts`(+`eks-auth`) | MUST(NAT 없을 때) | NAT 있음, `ecr.dkr`·`sts`·`s3` | **S** `ecr.api` 추가 (NAT가 있어 나머지는 선택) |
| MSK | 3 AZ, 브로커 수는 AZ 배수, RF ≥ 3, minISR ≤ RF−1 | REC | 3 AZ × 1 | 충족 (RF는 P) |
| Aurora | 다른 AZ에 리플리카 1개 이상 | REC | writer a, reader b | **S**(선택) c에 reader 추가 |
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
| Redshift Multi-AZ | RA3, 암호화, AZ당 2노드 이상, 서브넷 그룹 3개 이상 서브넷 | MUST | 서브넷 2개, 노드 2 | **S** 서브넷 3개, 노드 4 |
| DocumentDB | 다른 AZ에 복제본 | REC | writer a, reader b | 충족 (3 AZ는 선택) |
| EFS | AZ마다 마운트 타깃 | REC | 3 AZ | 충족 |
| VPC 간 연결 | 피어링은 비전이적: 피어 VPC의 VPN으로 온프레미스에 갈 수 없음. VPC 10개 미만이면 피어링 가능 | MUST(동작) | 분석 VPC에만 VGW VPN, 데이터 플랫폼 VPC는 피어링으로는 온프레미스 불가 | **S** TGW로 두 VPC + VPN 연결 |
| VPN | VGW는 시작용, ECMP 필요 시 TGW. CGW 이중화 | REC / HIGH | VGW, CGW 1 | **S** TGW 종단 + CGW 2 |

## 4. 변경안 (승인 대기)

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
| 7 | 선택 | microservices 3 AZ, eks Aurora reader 추가·/28 클러스터 서브넷, environments egress VPC | OPT |

수집 범위 확장 후보(별도 승인 필요): CloudFront·WAF(엣지), Elastic IP(NLB 고정 IP), TGW 라우팅 테이블(환경 격리),
Direct Connect, 다중 계정 구성도(계정 그룹).

## 5. 출처

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
