# Diagram Bot — AWS 구성도 워크스페이스

AWS 설정을 읽기 전용으로 수집해 **편집 가능한 인프라 아키텍처 구성도(draw.io)**, **아키텍처 검토 결과**, **변경 비교**를 만드는 로컬 웹 앱·CLI입니다. VPC 환경을 탐색하고, 리소스의 속성과 API 관계 근거를 확인하고, 편집본을 **`.drawio`·PNG**로 내려받을 수 있습니다. 여러 계정·리전을 읽기 전용 역할로 수집하고, 검토·드리프트 결과를 CI 종료 코드로 받을 수 있습니다. AWS 인증 없이 바로 시연할 수 있는 샘플이 포함되어 있습니다.

[운영 안내](docs/OPERATIONS.md) · [보안 정책](SECURITY.md) · [변경 내역](CHANGELOG.md)

혼자 쓸 때는 로컬(127.0.0.1)에서, 팀이 함께 쓸 때는 **팀 모드**(로컬 계정 또는 OpenID Connect SSO·역할·TLS·감사 로그)로 실행합니다.

![Diagram Bot AWS 워크스페이스](docs/images/workspace.png)

## 바로 실행

Python 3.11 이상, 데스크톱 Chrome/Chromium을 권장합니다.

```bash
git clone https://github.com/men16922/diagram-bot.git
cd diagram-bot
python -m venv .venv
source .venv/bin/activate
pip install -e .
diagram-bot setup-editor
diagram-bot serve
```

**http://127.0.0.1:8765**를 엽니다. 처음부터 합성 AWS 샘플이 표시됩니다. Windows에서는 가상환경 활성화 명령을 `.venv\Scripts\activate`로 바꿉니다.

`setup-editor`는 최초 한 번 공식 draw.io **32.4.1** 배포물(약 48 MB)을 다운로드하고 고정 SHA-256을 확인합니다. 이후 구성도 편집과 PNG 생성은 로컬에서 수행합니다. 오프라인 설치에는 동일 버전의 공식 `draw.war`를 준비해 `diagram-bot setup-editor --archive /path/to/draw.war`를 사용합니다. [배포물 출처와 라이선스](THIRD_PARTY_NOTICES.md)를 참고하세요.

[5분 시연 순서](docs/DEMO.md) · [AWS best practice 샘플 11종](examples/samples/README.md) · [사례 아키텍처 기준](docs/ARCHITECTURE.md) · [개발 계획과 구현 범위](docs/PLAN.md) · [GitHub 개발 안내](GITHUB.md)

## 시연 버전에서 할 수 있는 것

- **AWS 콘솔과 같은 화면 구성:** 상단 내비게이션(리전, AI 어시스턴트, 로그인·프로필 메뉴), 좌측 내비게이션(수집 이력, 전체 구성·VPC, 도구), 경로 표시와 페이지 헤더, 오른쪽에 붙는 AI 어시스턴트 패널.
- **AI/GPU 워크로드 (Well-Architected Generative AI·ML·HPC 렌즈):** GPU·Trainium·Inferentia 노드의 EFA 인터페이스·Capacity Block·용량 예약·클러스터 배치 그룹,
  FSx for Lustre, SageMaker 실시간 엔드포인트·HyperPod, Bedrock·SageMaker 인터페이스 엔드포인트를 수집해 그리고, AI/ML 검토 규칙 17개로 점검합니다
  (인터넷에 열린 Jupyter·Ray·MLflow·vLLM 포트, GPU 노드 퍼블릭 노출, 여러 AZ에 걸친 EFA 학습, 단일 AZ 추론, EFA 보안 그룹, Spot EFA, 배치 그룹 누락,
  GPU 서브넷 IP 부족, 학습 노드와 다른 AZ의 Lustre, Scratch Lustre, Bedrock 엔드포인트 AZ·프라이빗 DNS, VPC 밖·단일 AZ SageMaker 엔드포인트 등).
  AI 사례 3종(사설 EKS GPU 플랫폼, Bedrock RAG, GPU 분산 학습)과 채팅 설계 도구(GPU 노드 그룹·FSx·SageMaker 엔드포인트)를 제공합니다. 근거: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) 5절.
- **여러 계정 연결도:** 계정·리전별 수집을 `계정 통합 보기`(앱)나 `diagram-bot combine a/snapshot.json b/snapshot.json --output 경로`로 합치면 AWS 계정 프레임 안의 VPC 카드와 RAM으로 공유된 Transit Gateway·계정 간 피어링·VPN을 한 연결도로 그립니다. 각 VPC의 상세 구성과 검토도 그대로 동작합니다.
- **세 가지 보기:** VPC 카드와 Transit Gateway·피어링·VPN(온프레미스까지) 연결도의 `전체 구성`, 한 VPC의 AZ·서브넷·EC2·ALB·대상 그룹을 그린 `상세 구성`, 선택 리소스와 직접 관계·필요한 상위 경계만 그린 `선택 리소스`. 각 보기는 표시/수집/참조/숨김 수와 부분 조회 여부를 함께 보여줍니다.
- **인프라 아키텍처 표기:** AWS Cloud > Region > VPC > AZ > 서브넷(라우팅으로 퍼블릭·프라이빗·격리 구분)에 Internet/NAT Gateway, VPC 엔드포인트, Transit Gateway 연결, VPC 피어링, Site-to-Site VPN(VGW/TGW → 고객 게이트웨이, 터널 상태), EKS(컨트롤 플레인·노드 그룹), ECS 서비스·Fargate 태스크, MSK 브로커, OpenSearch 노드, ElastiCache, RDS·Aurora·DocumentDB·Neptune, EFS 마운트 타깃, Redshift, VPC Lambda, AZ를 가로지르는 Auto Scaling 그룹·ECS 서비스 프레임을 AWS Architecture Icons 스타일로 그립니다.
- **고가용성 표기:** 각 리소스가 어느 AZ·서브넷에서 어떤 역할인지 라벨에 표시합니다. RDS primary/standby/read replica, Multi-AZ DB 클러스터 readable standby, Aurora·DocumentDB writer/reader, ElastiCache 샤드별 primary/replica, OpenSearch AZ별 data·master 노드 수와 standby AZ, MSK AZ별 브로커 ID, Redshift Multi-AZ primary/secondary, EFS Regional/One Zone, Auto Scaling·EKS 노드 그룹 min/desired/max, ECS 실행/원하는 태스크 수.
- **결정적 자동 배치:** 로드밸런서 → 대상 그룹 → AZ 열·서브넷 순서의 계층 배치. 연결선은 채널과 AZ 사이 통로로만 지나가 다른 도형을 가로지르지 않습니다. 같은 입력은 항상 같은 좌표를 만듭니다([배치 엔진 평가](docs/engineering/LAYOUT_EVALUATION.md)).
- **아키텍처 검토:** 단일 AZ 구성, reader·replica 없는 데이터 계층, 다른 AZ NAT 의존, 꺼진 장애 조치, 퍼블릭 DB, 암호화 누락, IMDSv1, 인터넷에 열린 관리·DB 포트 등을 심각도·근거 API 필드·권장 조치와 함께 보여 줍니다(`검토 결과` 탭, `review.md`, `diagram-bot review --fail-on high`).
- **변경 비교:** 두 수집 시점 사이의 리소스 추가·삭제, 설정 변경, 관계 변경(`변경 비교` 탭, `diagram-bot diff --fail-on-change`). 헬스·실행 수 같은 상태 값은 비교하지 않습니다.
- 선택한 리소스의 속성·태그·관계와 API 근거, 포트·AZ 한정자, 구성도에서 직접 관계 강조.
- 실제 draw.io 도형 이동·스타일 수정·메모 편집. 편집본은 보기별로 저장되며, 덮어쓴 버전은 이력에 보관됩니다.
- `자동 배치 다시 실행`은 이전 편집본을 보관하고 메모를 유지합니다. `이전 편집본 복원` 지원.
- 이름·ID·태그·IP 검색, 리소스 종류별 목록, SG·ENI·EBS 참조 표시 전환.
- **경로 필터:** 리소스가 많은 상세 구성을 로드 밸런서·대상 그룹 하나의 경로로 좁힙니다. 다른 경로는 숨기고 숨김 수에 포함하며, 경로 밖 리소스는 흐리게 남깁니다(`경로 필터` 선택, 속성 패널 `이 경로만 보기`, API `GET /api/snapshots/{id}/document?view=detail&vpc=…&routes=<대상 그룹 ARN 또는 노드 ID>`).
- 현재 보기의 `.drawio`·PNG 다운로드. PNG는 1×/2×/3×와 흰색/투명 배경 지원.
- 전체 스냅샷 JSON·리소스 CSV 다운로드, JSON 가져오기, 수집 이력 선택.
- 기존 SDK/SSO 인증으로 읽기 전용 수집 시작, 완료·부분 조회·조회 불가와 필요한 IAM 권한 표시.

검색과 종류 선택은 리소스 목록 및 검색 결과를 필터링합니다. 구성도 범위는 보기 선택, 참조 표시와 경로 필터로 정합니다. 수동 편집은 구성도 표현에만 적용되며 원본 AWS 수집 데이터는 바뀌지 않습니다.

## 실제 AWS 연결

```bash
aws sso login --profile my-sso-profile
diagram-bot serve
```

화면 우측 상단 **로그인**(AWS 콘솔의 계정 메뉴와 같은 위치)에서 IAM Identity Center(SSO, device code)나 이 기기의 프로파일·기본 인증 체인으로 로그인하면 `역할 @ 계정`이 표시됩니다. 같은 메뉴의 **AWS 리소스 수집**에서 예상 계정 ID와 리전을 지정합니다. 로그인하지 않으면 boto3의 기본 인증 체인을 사용합니다. 기존 `AWS_CONFIG_FILE`·`AWS_PROFILE`·`credential_process`도 SDK가 처리합니다. STS의 실제 계정 ID와 예상 계정 ID가 다르면 리소스를 수집하지 않습니다. SSO 토큰과 자격 증명은 서버 메모리에만 두고 저장하지 않습니다.

### 서버가 AWS에 나갈 수 없는 환경 (브라우저 egress)

회사 서버의 방화벽이 AWS API를 막고, Permission Set이 회사 공인 IP 밖의 호출을 `aws:SourceIp`로 거부하는 환경을 위한 실행 방식입니다.

```bash
diagram-bot serve --aws-egress browser --sso-start-url https://d-1234567890.awsapps.com/start --sso-region us-east-1
```

```
 브라우저 (사용자 PC, 허용된 출구 IP)            서버 (방화벽 안)
 ① SSO 로그인 화면 ─────────────────────────▶ device code 시작·토큰 교환, GetRoleCredentials (Identity Center)
 ② 수집·채팅 요청 ──────────────────────────▶ boto3가 요청을 만들고 역할 자격 증명으로 SigV4 서명
 ③ GET /api/relay/next  ◀────── 서명된 요청 ── before-send에서 전송 대신 브라우저에 넘김
 ④ fetch → https://<서비스>.<리전>.amazonaws.com (CORS) — AWS 호출은 PC IP에서 나감
 ⑤ POST /api/relay/{id} ── AWS 응답 ─────────▶ botocore가 해석·재시도·페이지 넘김 (수집기·검토·채팅 코드는 그대로)
```

- 서버는 **자기 boto3 클라이언트가 만든 요청만** 서명합니다(임의 요청 서명 API 없음). 브라우저는 요청별 서명만 받고 비밀 키는 받지 않습니다.
- Identity Center 엔드포인트(sso-oidc, portal.sso)는 CORS를 허용하지 않아 로그인과 `GetRoleCredentials`는 서버에서 합니다(회사 샘플과 같은 구조).
- 수집에 쓰는 서비스 18종(EC2·ELB·RDS·Auto Scaling·EKS·ECS·MSK·ElastiCache·OpenSearch·EFS·Redshift·Lambda·FSx·SageMaker·CloudFront·WAFv2·Direct Connect·STS)과
  Bedrock Runtime은 서명된 브라우저 요청에 CORS로 응답합니다(2026-10-18 실제 Chromium으로 확인). 채팅의 Bedrock 호출은 이 모드에서 비스트리밍(Converse)입니다.
- 브라우저가 앱을 열고 있지 않으면 AWS 호출은 즉시 `브라우저 중계가 연결되어 있지 않습니다`로 실패합니다.
- 회사 네트워크에서 확인: 서버의 AWS 통신을 막고(`HTTPS_PROXY=http://127.0.0.1:9 NO_PROXY=127.0.0.1,localhost`) 앱을 띄운 뒤
  `python scripts/verify_browser_egress.py --url http://127.0.0.1:8796` — 브라우저가 호출한 AWS 호스트와 수집·채팅 결과를 보고합니다.

수집기는 `sts:GetCallerIdentity`와 [읽기 전용 정책 예시](readonly-policy.json)의 읽기 API(EC2·ELBv2·RDS·Auto Scaling·EKS·ECS·MSK·ElastiCache·OpenSearch·EFS·Redshift·Lambda·FSx·SageMaker·CloudFront·WAFv2·Direct Connect)만 호출합니다. 일부 권한이 없으면 해당 범위만 `조회 불가`로 표시하고 나머지는 그립니다. 정책을 자동 적용하지 않으며 키나 토큰을 앱·산출물에 저장하지 않습니다.

| 범위 | API 근거 |
| --- | --- |
| VPC·서브넷·가용 영역 | DescribeVpcs, DescribeSubnets |
| EC2와 VPC·서브넷 | DescribeInstances.VpcId/SubnetId |
| EC2의 보안 그룹·ENI·EBS 참조 | DescribeInstances의 리소스 ID |
| ELBv2의 VPC·여러 서브넷·보안 그룹 | DescribeLoadBalancers |
| 대상 그룹과 로드밸런서 | DescribeTargetGroups.LoadBalancerArns |
| 등록 대상과 상태 | DescribeTargetHealth |
| 인터넷 경로·NAT | DescribeInternetGateways.Attachments, DescribeNatGateways.SubnetId |
| 서브넷 유형(퍼블릭/프라이빗/격리) | DescribeRouteTables의 연결·`0.0.0.0/0` 경로 (명시 연결 없으면 기본 테이블) |
| RDS와 Multi-AZ 대기 | DescribeDBInstances의 AZ·SecondaryAvailabilityZone·DBSubnetGroup |
| Auto Scaling 그룹 | DescribeAutoScalingGroups의 Instances·TargetGroupARNs |
| VPC 엔드포인트 (Gateway·Interface) | DescribeVpcEndpoints의 RouteTableIds·SubnetIds |
| Transit Gateway와 VPC 연결 | DescribeTransitGateways, DescribeTransitGatewayVpcAttachments |
| Aurora writer/reader | rds DescribeDBClusters.DBClusterMembers |
| ElastiCache 노드 (primary/replica) | DescribeCacheClusters·ReplicationGroups·CacheSubnetGroups (AZ와 서브넷 그룹으로 배치) |
| MSK 브로커 | kafka ListClustersV2의 ClientSubnets, ListNodes의 브로커별 ClientSubnet (없으면 균등 분산으로 표시) |
| EKS 컨트롤 플레인·관리형 노드 그룹 | eks DescribeCluster·DescribeNodegroup (노드 그룹 → Auto Scaling 그룹) |
| VPC 연결 Lambda | lambda ListFunctions.VpcConfig |
| RDS 읽기 전용 복제본·Multi-AZ DB 클러스터 | DescribeDBInstances.ReadReplicaSourceDBInstanceIdentifier, DescribeDBClusters.DBClusterMembers |
| ElastiCache 클러스터 모드 샤드 | DescribeReplicationGroups.NodeGroups[].NodeGroupMembers |
| OpenSearch 도메인 노드 (data·master·standby) | es ListDomainNames·DescribeDomains.VPCOptions, DescribeDomainNodes의 AZ·NodeStatus |
| ECS 서비스·태스크 | ecs ListClusters·ListServices·DescribeServices, ListTasks·DescribeTasks의 ENI 서브넷·IP |
| ECS 태스크 = 대상 그룹 IP 대상 | 서비스의 loadBalancers[].targetGroupArn과 태스크 ENI IP가 같은 대상 |
| EFS 마운트 타깃 | elasticfilesystem DescribeFileSystems·DescribeMountTargets.SubnetId |
| Redshift (Multi-AZ primary/secondary) | redshift DescribeClusters.AvailabilityZone·MultiAZSecondary, DescribeClusterSubnetGroups |
| VPC 피어링 | DescribeVpcPeeringConnections, 라우팅 테이블의 VpcPeeringConnectionId 경로 |
| Site-to-Site VPN | DescribeVpnGateways·DescribeVpnConnections(VgwTelemetry)·DescribeCustomerGateways |
| 보안 그룹 규칙 | DescribeSecurityGroups.IpPermissions (검토 결과·속성 패널) |
| GPU 노드의 EFA·용량·배치 | DescribeInstances의 NetworkInterfaces[].InterfaceType·InstanceLifecycle·CapacityReservationId·Placement.GroupName, DescribePlacementGroups.Strategy |
| FSx (Lustre·ONTAP·OpenZFS·Windows) | fsx DescribeFileSystems.SubnetIds·LustreConfiguration |
| SageMaker 실시간 엔드포인트 | sagemaker ListEndpoints·DescribeEndpoint·DescribeEndpointConfig(인스턴스 유형)·DescribeModel.VpcConfig(서브넷) |
| SageMaker HyperPod | sagemaker ListClusters·DescribeCluster(InstanceGroups·VpcConfig·Orchestrator) |
| Elastic IP (NAT·NLB·EC2·ENI에 표시) | DescribeAddresses의 AllocationId = DescribeNatGateways.NatGatewayAddresses[]·DescribeLoadBalancers.LoadBalancerAddresses[], InstanceId·NetworkInterfaceId |
| TGW 라우팅 테이블 (라우팅 도메인) | DescribeTransitGatewayRouteTables, GetTransitGatewayRouteTableAssociations·Propagations, SearchTransitGatewayRoutes(state = active·blackhole, 최대 1,000개) |
| CloudFront와 오리진 | cloudfront ListDistributions(전역), 오리진 DomainName = DescribeLoadBalancers.DNSName일 때만 ALB에 연결 (S3·사용자 지정 오리진은 속성) |
| AWS WAF 웹 ACL | wafv2 ListWebACLs(REGIONAL은 수집 리전, CLOUDFRONT는 us-east-1)·ListResourcesForWebACL(ALB), ListDistributions.WebACLId |
| Direct Connect | directconnect DescribeConnections·DescribeVirtualInterfaces(VGW·DX 게이트웨이)·DescribeDirectConnectGateways·DescribeDirectConnectGatewayAssociations |
| 교차 계정 수집 | sts:AssumeRole (+ ReadOnlyAccess 세션 정책), GetCallerIdentity로 계정 확인 |
| IP 대상의 서브넷 | 대상 그룹 VPC 안에서 IP를 포함하는 서브넷 CIDR이 하나이고 AZ가 일치할 때만 |

ALB는 여러 서브넷에 걸쳐도 VPC 수준에 한 번만 그리며, 서브넷 연결은 속성 패널과 선택 리소스 보기에서 확인합니다. 대상 그룹은 논리 자원으로 서브넷 밖에 둡니다. 관계 ID는 출발·대상·관계 종류와 포트·AZ로 정해지므로 조회 순서나 상태가 바뀌어도 유지됩니다. ENI·EBS는 **참조만** 표시합니다. 보안 그룹은 규칙을 조회해 검토와 속성 패널에 쓰지만 구성도에는 참조 선택 시에만 그립니다. IP·Lambda 대상은 별도 등록 대상으로 남기고 EC2와 추측으로 합치지 않습니다. IP 대상은 서브넷 CIDR·AZ가 하나로 일치할 때만 서브넷에 두고, ECS 서비스가 그 대상 그룹을 지정하고 태스크 ENI IP가 같을 때만 태스크로 표시합니다.
CloudFront는 리전 상자 밖 AWS Cloud 안의 엣지 열에 두고 오리진 ALB(전체 구성은 그 VPC 카드)로 잇습니다. WAF 웹 ACL이 연결된 CloudFront·ALB 아이콘에는 WAF 표식을 붙입니다.
Direct Connect 연결은 VPN 고객 게이트웨이와 같은 데이터 센터 상자에 두고 가상 인터페이스로 VGW·DX 게이트웨이에, DX 게이트웨이는 연결된 TGW·VGW에 잇습니다.
Elastic IP는 붙은 리소스의 라벨(`EIP 203.0.113.10`, NLB는 `EIP 2`)에, TGW 라우팅 테이블은 TGW 라벨·VPC 카드(`TGW 라우팅 테이블 · 이름`)·속성 패널에 표시합니다. 그래프 관계에는 API 근거와 `inferred: false`가 기록됩니다. 보안 그룹 허용 규칙과 실제 통신을 혼동하지 않습니다.

SDK 페이지네이션·재시도·타임아웃을 적용합니다. `partial`은 일부 페이지 실패, `unavailable`은 한 페이지도 조회하지 못한 상태이며 리소스가 없다는 뜻이 아닙니다. 앞서 조회한 데이터는 보존합니다. 수집 시작·종료 시각과 범위를 기록합니다. 서비스별로 병렬 조회하며 adaptive 재시도를 씁니다. 여러 API를 조회한 결과이므로 원자적 스냅샷은 아닙니다.

## 채팅으로 아키텍처 설계 (Strands Agents + Amazon Bedrock)

표준 인프라 아키텍처를 말로 요청하면 에이전트가 함수(도구)를 호출해 구성도를 그립니다. 실제 계정은 같은 채팅에서 읽기 전용으로 수집·검토합니다.
모델은 구성도를 직접 그리지 않고 도구만 고릅니다. CIDR, AZ 분산, 라우팅, 보안 그룹, Multi-AZ 같은 세부 값은 코드가 모범 사례에 맞춰 정합니다.

```bash
pip install -e '.[agent]'                       # strands-agents
diagram-bot serve --env-file .env               # 앱 상단 「AI 어시스턴트」 (오른쪽에 붙는 패널)
diagram-bot chat --env-file .env                # 터미널 대화형
diagram-bot chat --env-file .env -m "서울 리전에 3 AZ 3계층 웹 서비스를 설계하고 검토해줘"
```

- `.env`에는 `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `AWS_REGION`을 넣고, 선택으로 `AWS_ACCOUNT_ID`(수집 계정 확인)를 넣습니다. `.env`는 Git 추적에서 제외되어 있습니다. 키가 있으면 셸의 `AWS_PROFILE`보다 우선합니다.
- 모델은 Bedrock Converse로 호출합니다. 기본은 Amazon Nova 2 Lite입니다. OpenAI GPT-6 Luna와 Claude Sonnet 4.5도 고를 수 있고, 고른 모델을 쓸 수 없으면 Nova 2 Lite로 자동 전환합니다. 응답마다 실제로 답한 모델이 표시됩니다. 계정에서 Bedrock 모델 액세스가 필요합니다.
- 도구: 표준 아키텍처 10종 불러오기(AI 플랫폼·RAG·분산 학습 포함), VPC(AZ·계층·NAT 전략), ALB/NLB, EC2 Auto Scaling, ECS Fargate, EKS·GPU 노드 그룹(학습: 전용 서브넷·EFA·배치 그룹), FSx for Lustre, SageMaker 엔드포인트, RDS/Aurora, Redis, MSK, OpenSearch, EFS, Lambda, VPC 엔드포인트, 피어링, VPN, 검토. 수집 도구는 읽기 전용입니다(STS 확인, 리전 수집, 리소스 찾기, 검토).
- **현재 구성에서 시작(brownfield):** "지금 보고 있는 구성에 Redis를 추가해줘"처럼 요청하면 에이전트가 앱에서 열어 둔 스냅샷(또는 마지막 수집 결과)을
  설계의 출발점으로 불러와(`start_from_current`) 기존 VPC·서브넷·로드 밸런서에 요소를 더합니다. 없는 계층은 `add_subnet_tier`로 빈 CIDR에 만듭니다.
  결과는 `based_on`이 붙은 새 설계안이며 원본 스냅샷과 실제 계정은 바뀌지 않습니다. 터미널에서는 `diagram-bot chat --from snapshot.json`.
  라우트 테이블이 수집되지 않은 VPC에는 라우트를 지어내지 않고, 게이트웨이 엔드포인트·피어링·VPN 단계가 이유를 알려 줍니다.
- 표준 아키텍처를 불러온 뒤에도 그 VPC 이름으로 `add_*` 단계를 이어 쓸 수 있습니다.
- 응답은 스트리밍됩니다(`POST /api/agent/chat/stream`, NDJSON). 도구가 도는 동안 진행 상황이 보이고, 설계 단계마다 저장된 구성도가 바로 캔버스에 열립니다.
  한 번에 결과만 받으려면 `POST /api/agent/chat`을 씁니다.
- 설계안은 스냅샷으로 저장되어 구성도, 검토, `.drawio`/PNG 다운로드, 변경 비교에 그대로 쓰입니다. 화면에는 `설계안`으로 표시되며 실제 계정 데이터가 아닙니다.
- 벤치마킹(AWS DevOps Agent)과 설계 근거는 [docs/engineering/DEVOPS_AGENT_BENCHMARK.md](docs/engineering/DEVOPS_AGENT_BENCHMARK.md), draw.io 연동과 자동 배치는 [docs/DRAWIO.md](docs/DRAWIO.md)에 있습니다.

## 설계 → CloudFormation 내보내기 (IaC 초안)

채팅 설계안·샘플·수집 스냅샷을 같은 구성을 다시 만드는 **CloudFormation 템플릿 초안(JSON)**으로 내보냅니다. 앱에서는 `다운로드` → `IaC 초안 · CloudFormation`,
CLI에서는 `export-cfn`, API는 `GET /api/snapshots/{id}/cloudformation`입니다. 템플릿을 배포하지는 않습니다(검토용 초안).

```bash
diagram-bot export-cfn reports/current/snapshot.json --output reports/current/template.json
diagram-bot export-cfn reports/chat/01-design/snapshot.json > design.cfn.json   # --output이 없으면 표준 출력
diagram-bot export-cfn reports/org/snapshot.json --output cfn/landing.json      # 여러 계정 통합본은 계정·리전마다 landing-<계정>-<리전>.json
```

- 템플릿이 만드는 리소스끼리는 `Ref`/`Fn::GetAtt`로 잇고, 수집된 물리 ID(vpc-…, subnet-…, sg-…, ARN)를 본문에 쓰지 않습니다. AZ 이름은 스냅샷 값을 기본으로 한 매개변수입니다.
- 지원: VPC(보조 CIDR)·서브넷·IGW·NAT(+EIP)·라우팅 테이블·경로·연결(기본 테이블은 명시 연결로)·VGW 전파, 보안 그룹·규칙, VPC 엔드포인트,
  Transit Gateway·VPC 연결(공유 TGW는 ID 매개변수), 피어링(양쪽 VPC가 있을 때), VPN·고객 게이트웨이, ALB/NLB·대상 그룹·리스너, Auto Scaling 그룹·시작 템플릿,
  독립 EC2(EFA·배치 그룹·용량 예약), RDS(Multi-AZ·읽기 복제본)·Aurora·DocumentDB·Multi-AZ DB 클러스터, ElastiCache(클러스터 모드 포함), MSK(프로비저닝),
  OpenSearch, EFS·마운트 타깃, FSx for Lustre, Redshift, EKS 클러스터·관리형 노드 그룹(EFA·배치 그룹 시작 템플릿), ECS Fargate 클러스터·서비스·작업 정의 골격.
- 조회하지 않는 값은 추측하지 않고 **매개변수**로 둡니다: AMI(아키텍처별), ACM 인증서, EKS·ECS IAM 역할, 컨테이너 이미지, 경로 조건(리스너 규칙은 수집 범위 밖).
  DB 비밀번호는 템플릿에 없고 Secrets Manager가 관리합니다(`ManageMasterUserPassword`). 데이터 리소스에는 스냅샷·보존 삭제 정책을 둡니다.
- 표현할 수 없는 것은 만들지 않고 `Metadata.DiagramBot.Unsupported`에 ID와 이유를 남기며, 추정한 값은 `Metadata.DiagramBot.Assumptions`에 적습니다.
  예: Lambda(코드 패키지), SageMaker 엔드포인트·HyperPod, Capacity Block·Spot·Karpenter 인스턴스, VPC 안 IP 대상(EKS 파드 등), 다른 계정과의 피어링, 기본 VPC,
  Gateway Load Balancer, FSx Windows·ONTAP·OpenZFS, Neptune, MSK Serverless.
- 리스너 포트·프로토콜은 대상 그룹과 로드 밸런서 보안 그룹 포트에서 추정하고, 규칙이 수집되지 않은 보안 그룹은 인바운드 규칙 없는 그룹으로 만듭니다(둘 다 Assumptions에 기록).
  배포 전에 매개변수·Unsupported·Assumptions를 검토하세요.

## 로컬 데이터와 CLI 보고서

편집기는 기본적으로 `~/.cache/diagram-bot/drawio/32.4.1`, 스냅샷과 편집본은 `~/.local/share/diagram-bot`에 저장합니다. `XDG_CACHE_HOME`·`XDG_DATA_HOME` 또는 `serve --data-dir reports/workspace`로 위치를 바꿀 수 있습니다. 서버는 `127.0.0.1`에만 바인딩합니다.

기존 단독 HTML 보고서도 생성할 수 있습니다.

```bash
diagram-bot demo --output reports/demo
diagram-bot demo --sample three-tier --output reports/three-tier   # three-tier | microservices | nlb-alb | environments | hybrid | eks-platform | data-platform
diagram-bot collect --profile my-sso-profile --region ap-northeast-2 \
  --expected-account 123456789012 --output reports/current
diagram-bot render reports/current/snapshot.json --output reports/rerendered
diagram-bot review reports/current/snapshot.json --fail-on high          # 높음 결과가 있으면 종료 코드 3
diagram-bot diff reports/previous/snapshot.json reports/current/snapshot.json --fail-on-change   # 변경이 있으면 4
# 여러 계정·리전: --role-arn을 여러 번, --region a,b (역할 세션은 ReadOnlyAccess로 제한)
```

여러 계정 배포용 읽기 전용 역할 템플릿과 운영 절차는 [운영 안내](docs/OPERATIONS.md)에 있습니다.

| CLI 산출물 | 내용 |
| --- | --- |
| `topology.drawio` | 자동 생성된 편집 가능한 구성도 (전체 구성 + VPC별 상세 페이지) |
| `index.html` | 서버 없이 여는 조회용 HTML·속성·필터·SVG 다운로드 |
| `snapshot.json` | 원본 응답·그래프·API별 조회 상태·수집 시각 |
| `inventory.csv` | 동일 스냅샷의 리소스 내역·참조 여부·속성 |
| `review.md` | 조회 결과, 아키텍처 검토 결과(심각도·근거·권장 조치), 해석 범위 |

**PNG 다운로드는 `serve` 웹 앱에서 제공합니다.** CLI의 HTML 뷰어와 draw.io 편집기는 별도 화면입니다. 부분 수집이어도 보고서를 생성하며 `collect` 종료 코드는 정상 0, 부분 조회 2, 인증·계정 불일치·파일 오류 1입니다(여러 대상이면 가장 나쁜 결과). 실제 수집 데이터는 `reports/` 등 Git 추적 제외 경로에 보관하세요.

## 검증과 현재 한계

```bash
pip install -e '.[test,browser]' build
pytest -q
python -m build
python -m playwright install chromium
# 별도 터미널에서 새 데이터 디렉터리로 앱을 실행한 뒤:
# diagram-bot serve --data-dir reports/browser-check
python scripts/verify_demo.py
```

자동 테스트 **90개**(AWS API 에뮬레이터 통합 테스트·팀 모드·SSO 포함)와 Chromium 시연 검증을 통과했습니다. 테스트는 관계 ID 안정성, 보기별 범위, 도형 겹침·컨테이너 밖 잘림·도형을 가로지르는 연결선이 없는지, 100개 리소스 배치 시간(약 0.02초, 합성 데이터)을 확인합니다. 브라우저 검증은 보기 전환, 관계 근거·강조, 키보드 이동과 메모 편집, 저장/재열기, 편집본이 반영된 `.drawio`·PNG(1×/2×/3×)·JSON·CSV, 자동 재배치·복원, JSON 가져오기를 확인합니다. 화면·PNG·검증 JSON은 `artifacts/browser/`에 생성됩니다. CI에서도 Python 3.11/3.12/3.13 패키지 테스트와 Chromium 검증을 실행합니다.

SAML 직접 연동(인증 프록시로 연결), 동시 편집 병합, 수집 작업의 재시작 복구, 내장 예약 수집(cron·CI에서 `collect`·`review`·`diff` 종료 코드 사용), 리스너 규칙·NACL·실제 트래픽 분석은 포함하지 않습니다. CloudFront·WAF·Direct Connect·Elastic IP·TGW 라우팅 테이블 수집은 모의 응답(Stubber)·에뮬레이터로만 검증했습니다. 실제 AWS 계정 수집은 한 계정·리전(IAM 사용자 키)에서 CLI와 웹 앱으로 검증했고, SSO 프로파일·교차 계정 역할·실제 워크로드가 있는 계정은 아직 검증하지 않았습니다. 수집 로직은 API별 모의 응답 테스트와 AWS API 에뮬레이터(moto)에 구성한 환경 전체를 수집하는 통합 테스트로 검증했습니다.

## 라이선스

[Apache License 2.0](LICENSE). 고지는 [NOTICE](NOTICE)에 있습니다. `setup-editor`로 설치하는 draw.io와 AWS Architecture Icons 도형의 조건은 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)를 보세요.
