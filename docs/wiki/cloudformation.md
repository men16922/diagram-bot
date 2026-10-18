---
title: CloudFormation 초안 내보내기
summary: iac/ 패키지의 Template이 스냅샷 raw를 CloudFormation JSON 초안으로 바꾸는 단계 순서, Ref/GetAtt 규칙, 매개변수, DeletionPolicy, Metadata.DiagramBot의 Unsupported·Assumptions 전체 목록, export-cfn·API·테스트.
sources: [src/diagram_bot/iac/, src/diagram_bot/cli.py, src/diagram_bot/routes/snapshots.py, src/diagram_bot/api.py, tests/test_iac.py, tests/test_aws_emulator.py, tests/test_golden.py]
related: [multi-account, review-rules, cli, snapshot-model, http-api]
updated: 2026-10-18
---
# CloudFormation 초안 내보내기

> `src/diagram_bot/iac/` 패키지는 스냅샷(설계·샘플·수집)의 `raw`(API 응답 모양 데이터)가 말하는 것만 CloudFormation 리소스로 다시 만든다. API가 돌려주지 않는 값(AMI, 이미지, IAM 역할, 인증서, 리스너 규칙, 비밀번호)은 매개변수나 가정으로, 충실히 표현할 수 없는 것은 `Metadata.DiagramBot.Unsupported`에 이유와 함께 남긴다. 검토용 초안이며 앱이 배포하지 않는다.

## 핵심
- 진입점: `template(snapshot)`(계정 × 리전 하나; `parts`가 있으면 `ValueError`), `templates(snapshot)`(`[(f"{account}-{region}", template)]`, 합친 스냅샷은 part마다), `dumps(value)`(`ensure_ascii=False`, 들여쓰기 2, 끝 줄바꿈).
- 출력: `AWSTemplateFormatVersion: 2010-09-09`, ASCII `Description`, `Metadata.DiagramBot`(`Generator: "diagram-bot export-cfn"`, `Source`: 메타데이터의 `title`·`account_id`·`region`·`sample`·`finished_at`·`status`와 `design` 불리언, `Unsupported`, `Assumptions`), `Parameters`, `Resources`, `Outputs`. 매개변수·출력이 없으면 그 키를 뺀다.
- 만든 리소스에는 수집된 물리 ID가 들어가지 않는다. 템플릿 안 리소스끼리는 `Ref`/`Fn::GetAtt`로만 잇는다(`test_every_sample_exports_a_self_contained_template`의 `assert_self_contained`).
- `GONE` 상태(`deleted`·`deleting`·`terminated`·`shutting-down`·`failed`·`rejected`·`expired`) 리소스는 조용히 건너뛴다.

## Template 클래스
- 파일: `Template`(`iac/__init__.py`)은 믹스인 `Network`, `Balancing`, `Compute`, `Data`, `Base`를 합친 클래스이고 상태는 `self` 하나다. `base.py`(`__init__` 상태, `logical`·`unique`·`add`·`ref`·`param`·`skip`·`assume`·`tags`·`zone`·`image`·`security_group(s)`, `clean`·`ascii_text`·`GONE`·`GRAVITON`), `network.py`(`network`~`placement_groups`, `vpn`·`tgw_routing`·`elastic_ips`, SG 규칙 도우미 `default_egress`·`rules`), `balancing.py`(`load_balancers`·리스너·`targets`), `compute.py`(`auto_scaling`·`standalone_instances`, `kubernetes`·`containers`), `data.py`(`databases`~`warehouse`). `build`·`not_expressible`·`template`·`templates`·`dumps`·`PSEUDO`·`STATEFUL`은 `__init__.py`에 있고, 예전 `iac.py`의 공개 이름을 모두 다시 내보낸다.
- 상태: `resources`, `parameters`, `outputs`, `ids`(물리 ID·ARN·이름 → 논리 ID), `unsupported`, `assumptions`, `attachments`(게이트웨이 → VPC attachment 논리 ID, 경로의 `DependsOn`에 씀), `listeners`, `default_vpcs`.
- `logical(prefix, *labels)`: 접두사 + 읽을 수 있는 첫 레이블의 CamelCase, 200자 제한, 충돌하면 숫자 접미사. 예: `SecurityGroupProdDb`, `DbProdOrdersDb`.
- `unique(base)`: 파생 ID(`…Attachment`, `…Eip`, `…LaunchTemplate`, `…TaskDefinition`, `…Association`, `…Image` 파라미터)는 이름을 이어 붙인 뒤 이 함수를 거친다. 리소스·파라미터가 이미 그 ID를 쓰면 `base2`, `base3`… 이름이 `edge`와 `edge-attachment`인 IGW 두 개가 서로를 덮어쓰지 않는다.
- `add(logical, kind, props, physical=None, depends=None)`: `None`·`[]`·`{}` 속성을 빼고(`clean()`이 모든 깊이에서 `None` 제거) 등록, `physical`을 `ids`에 기록.
- `ref(physical)`: 템플릿이 만든 것만 `{"Ref": logical}`, 아니면 `None`. 호출부는 `None`이면 `skip()`으로 미지원 처리한다. 서브넷 목록은 `subnet_refs()`가 하나라도 없으면 `None`.
- 합성 물리 키: 배치 그룹 `placement:<이름>`, ASG `asg:<이름>`, DB 클러스터 `cluster:<id>`, 캐시 `cache:<id>`, EKS `eks:<이름>`, 서브넷 그룹 `<family>-subnets:<이름>`.
- `skip(kind, identifier, reason, name=None)` → `Unsupported`에 `{Type, Id, Reason[, Name]}`(중복 제거). `assume(text)` → `Assumptions`(중복 제거).

## build() 단계 순서
단계 목록은 믹스인에 흩어지지 않고 `Template.build`의 튜플 하나에 있다.
`network` → `gateways` → `nat_gateways` → `security_group_resources` → `route_tables` → `endpoints` → `security_group_rules` → `placement_groups` → `load_balancers` → `auto_scaling` → `standalone_instances` → `targets` → `databases` → `caches` → `kafka` → `search` → `file_systems` → `warehouse` → `kubernetes` → `containers` → `vpn` → `tgw_routing` → `elastic_ips` → `not_expressible`, 마지막으로 DeletionPolicy 적용. 뒤 단계가 앞 단계의 `ids`를 참조하므로 순서가 곧 의존 관계다(예: 보안 그룹 규칙은 모든 그룹이 만들어진 뒤, 대상 등록은 리스너가 생긴 뒤).

## Ref·GetAtt 규칙
- `Fn::GetAtt`: VPC `DefaultSecurityGroup`(기본 SG 참조, EFS 마운트 타깃 기본 SG), EIP `AllocationId`(NAT·인스턴스 EIP), 시작 템플릿 `LatestVersionNumber`, LB `DNSName`(출력).
- VPC 엔드포인트 서비스 이름은 `com.amazonaws.<region>.` 접두사면 `{"Fn::Sub": "com.amazonaws.${AWS::Region}.<short>"}`, 파트너 PrivateLink는 원래 이름.
- 출력: VPC마다 `<VPC논리ID>Id`, LB마다 `<LB논리ID>DnsName`.
- 연결은 됐지만 규칙이 수집되지 않은 SG는 `security_group()`이 같은 VPC에 빈 스텁 SG(`Diagram Bot stub (rules not collected)`)로 만든다.
- 경로: IGW·VGW 경로는 attachment에, TGW 경로는 그 VPC의 TGW attachment에 `DependsOn`. ECS 서비스는 대상 그룹의 리스너에, ALB 대상(NLB → ALB)은 ALB 리스너에 의존.

## 매개변수
| 이름 | 형식 | 언제 |
| --- | --- | --- |
| `ImageIdX8664`, `ImageIdArm64` | `AWS::EC2::Image::Id` | EC2·시작 템플릿. `Architecture` 또는 Graviton 정규식 `GRAVITON`으로 고름 |
| `AvailabilityZone<접미사>` (예: `AvailabilityZoneA`) | `AWS::EC2::AvailabilityZone::Name` | 스냅샷 리전으로 시작하는 AZ 이름. 기본값 = 원래 AZ |
| `DBMasterUsername` | String, 기본 `dbadmin`, 패턴 제한 | 수집된 `MasterUsername`이 없을 때만 (수집 값 우선) |
| `DBAllocatedStorage` | String, 기본 `100` | RDS `AllocatedStorage` 없을 때 |
| `KeyName` | `AWS::EC2::KeyPair::KeyName` | 인스턴스에 `KeyName`이 있을 때 |
| `<LB논리ID>CertificateArn` | String, ACM ARN 패턴 | HTTPS·TLS 리스너 |
| `<TG논리ID>PathPattern` | String, 기본 `/<대상그룹이름>/*` | ALB의 두 번째 이후 대상 그룹 리스너 규칙 |
| `CapacityReservation<ID>` | String, 기본 = 예약 ID | `CapacityReservationId`가 있는 인스턴스 |
| `TransitGatewayId<이름>` | String, 기본 = TGW ID | 다른 계정 소유(RAM 공유) TGW |
| `OpenSearchVolumeSize` | Number, 기본 100 | `EBSOptions` 없을 때 |
| `RedshiftDatabaseName` | String, 기본 `dev` | Redshift |
| `EksClusterRoleArn`, `EksNodeRoleArn`, `EcsTaskExecutionRoleArn` | String, `ROLE_PATTERN` | EKS·ECS (IAM은 수집 범위 밖) |
| `<ECS서비스논리ID>Image` | String, 기본값 없음 | ECS 서비스 컨테이너 이미지 (추측하지 않음) |

DB 비밀번호는 `ManageMasterUserPassword`(Redshift는 `ManageMasterPassword`)로 Secrets Manager가 관리하고 템플릿에 두지 않는다.

## DeletionPolicy
- `STATEFUL`: RDS `DBInstance`·`DBCluster`, DocDB `DBCluster`, ElastiCache `ReplicationGroup`·`CacheCluster`, Redshift `Cluster` → `Snapshot`; DocDB `DBInstance` → `Delete`; EFS·FSx 파일 시스템, OpenSearch 도메인, MSK 클러스터 → `Retain`.
- RDS `DBInstance` 중 `DBClusterIdentifier`나 `SourceDBInstanceIdentifier`가 있는 것(클러스터 멤버·읽기 복제본)은 `Delete`.
- `DeletionPolicy`와 `UpdateReplacePolicy`를 같은 값으로 둔다. Snapshot·Retain이 하나라도 있으면 그 사실을 가정에 적는다.

## Unsupported 목록과 이유
- 네트워크: `VPC`(기본 VPC — 리전마다 AWS가 만들며 그 안 리소스도 제외), `Subnet`·`SecurityGroup`·`RouteTable`(소속 VPC가 템플릿에 없음), `SecurityGroupReference`(템플릿 밖 VPC의 SG가 연결됨), `SecurityGroupRule`(스냅샷에 없는 SG 참조), `NatGateway`(서브넷 없음), `TransitGatewayVpcAttachment`(VPC·서브넷·TGW 중 없는 것, 다른 계정 연결일 수 있음), `VpcPeeringConnection`(상대 VPC가 다른 계정·리전이라 `PeerRoleArn` 없이 못 만듦), `VpcEndpoint`(VPC 없음, Gateway Load Balancer 엔드포인트, 인터페이스 서브넷 없음).
- 경로 `Route`: 대상 엔드포인트가 스냅샷에 없음, blackhole 경로, 이 VPC의 TGW attachment 없음, 대상이 템플릿에 없음, 지원하지 않는 대상(`EgressOnlyInternetGatewayId`, `NetworkInterfaceId`, `InstanceId`, `CarrierGatewayId`, `LocalGatewayId`, `CoreNetworkArn`, `VpcEndpointId`).
- 부하 분산: `LoadBalancer`(GWLB 또는 서브넷 없음), `TargetGroup`(VPC 없음 또는 Lambda 대상), `Listener`(NLB에 같은 포트 대상 그룹이 둘 이상), `RegisteredTarget`(대상 인스턴스를 만들지 않음, 대상 ALB 없음, VPC 안 IP 대상 — 배포 후 주소가 바뀌고 컨트롤러가 등록).
- 컴퓨팅: `AutoScalingGroup`(서브넷 없음 또는 구성원이 없어 유형 불명), `EC2Instance`(ASG가 스냅샷에 없음, 노드 그룹 밖 EKS 노드(Karpenter 등), Capacity Block, Spot, 서브넷 없음).
- 데이터: `DBCluster`(Neptune, 서브넷 그룹·인스턴스 클래스 불명, 서브넷 그룹의 서브넷 없음), `DBInstance`(서브넷 그룹 서브넷 없음, 원본이 다른 리전·템플릿 밖인 읽기 복제본), `ElastiCacheReplicationGroup`(노드·서브넷 그룹 없음, 서버리스일 수 있음), `ElastiCacheCluster`, `MSKCluster`(Serverless 또는 서브넷 없음), `OpenSearchDomain`, `EFSMountTarget`, `FSxFileSystem`(Lustre만 지원: Windows·ONTAP·OpenZFS는 디렉터리·SVM 설정 필요), `RedshiftCluster`.
- 컨테이너: `EKSCluster`, `EKSNodegroup`(서브넷 없음), `ECSService`(Fargate awsvpc 서비스만 지원).
- 하이브리드·라우팅: `VpnConnection`(CGW나 VGW·TGW 없음), `TransitGatewayRouteTable`(TGW 없음 또는 다른 계정 소유), `TransitGatewayRouteTableAssociation`·`Propagation`(VPN·Direct Connect attachment는 CloudFormation이 ID를 돌려주지 않아 참조 불가), `TransitGatewayRoute`(정적 경로 대상 연결 없음).
- 주소: `ElasticIp`(어디에도 연결되지 않음 — 요금만 발생, `COST-EIP-IDLE`; 또는 템플릿 밖 ENI·인스턴스에 연결).
- 표현 불가(`not_expressible`): `CloudFrontDistribution`(전역 서비스, 리전 템플릿과 따로, 캐시 동작 미수집), `WafWebAcl`(규칙 본문 `GetWebACL` 미수집), `DirectConnectConnection`(물리 회선은 DX 위치에서 주문), `DirectConnectVirtualInterface`·`DirectConnectGateway`(CloudFormation 리소스 없음), `LambdaFunction`(코드 패키지·실행 역할 미조회), `SageMakerEndpoint`(모델 아티팩트·이미지·역할 미조회), `SageMakerHyperPodCluster`(수명 주기 스크립트·역할 미조회).

## 주요 가정 (Assumptions)
- VPC DNS 지원·호스트 이름을 켠다(DescribeVpcs가 돌려주지 않음). VPC 기본 SG는 관리하지 않는다. 라우팅 테이블을 수집하지 않았으면 기본 라우팅 테이블만 쓴다. main 라우팅 테이블을 암묵적으로 쓰던 서브넷은 명시적으로 연결한다.
- 퍼블릭 NAT마다, 인스턴스 EIP마다 새 EIP를 할당한다(수집된 공인 IP를 이어받지 않음). NLB 고정 EIP도 새로 할당된다.
- 기본 전체 허용 아웃바운드를 없애야 하면 `127.0.0.1/32` 자리표시 규칙을 둔다. 규칙이 수집되지 않은 SG는 인바운드 없이 스텁으로 만든다.
- 리스너·리스너 규칙은 대상 그룹과 SG 포트에서 추정한다: ALB는 SG의 첫 단일 포트 TCP 규칙(`FromPort == ToPort`) 포트, 없으면 인터넷 쪽이거나 HTTPS 대상 그룹이면 443, 아니면 첫 대상 그룹 포트; 443이면 HTTPS. NLB는 대상 그룹마다 그 포트·프로토콜로 리스너 하나.
- ASG는 가장 많은 인스턴스 유형으로 시작 템플릿을 만든다. 중지된 인스턴스도 실행 상태로 만든다. IAM 인스턴스 프로필은 붙이지 않는다.
- 글로벌 DB·리전 간 복제는 독립 클러스터로. Aurora·DocDB 클러스터 인스턴스는 writer를 먼저 만들고 나머지가 의존한다. Multi-AZ DB 클러스터는 인스턴스를 스스로 만든다.
- OpenSearch 액세스 정책은 두지 않는다. FSx KMS 키는 AWS 관리형 키. VPN 사전 공유 키·내부 CIDR은 AWS가 생성. 실행 중 태스크가 없는 ECS 서비스는 Fargate 최소 256/512. EKS 노드 그룹은 클러스터 SG를 쓰고 EFA·배치 그룹이 있을 때만 시작 템플릿(AMI 없음).
- 다른 계정 소유 TGW는 매개변수로 참조하고 RAM 공유·연결 수락은 소유 계정 몫. TGW 라우팅 테이블이 없으면 기본 연결·전파. TGW 기본 라우팅 테이블은 게이트웨이가 만들어 참조하지 않는다.

## TGW 라우팅 테이블과 Elastic IP
- `tgw_routing()`: 기본 연결·전파 테이블이 아닌 테이블만 `TransitGatewayRouteTable`로 만들고, 템플릿에 있는 attachment의 연결·전파(`disassociated`·`disabled` 제외)와 `Type == "static"` 경로(blackhole이면 `Blackhole: true`, 아니면 대상 attachment 하나)를 만든다. 전파 경로는 전파 설정에서 생기므로 따로 쓰지 않는다.
- `elastic_ips()`: NAT·NLB가 가진 할당은 소유자와 함께 다시 할당되므로 건너뛰고, 템플릿이 만든 인스턴스에 붙은 주소는 새 `AWS::EC2::EIP` + `EIPAssociation`, 나머지는 Unsupported.

## 사용 경로
- CLI `diagram-bot export-cfn SNAPSHOT [--output PATH]`: `schema_version`이 1이 아니면 실패. `--output`이 없으면 표준 출력, 합친 스냅샷이면 `--output` 필수(없으면 `parser.error`)이고 `<stem>-<계정>-<리전><suffix 또는 .json>`으로 나눠 쓴다. 표준 오류에 리소스·매개변수·미지원·가정 개수를 요약한다.
- API `GET /api/snapshots/{identifier}/cloudformation[?part=N]`: JSON 첨부 파일 `diagram-bot-cfn[-<sample>]-<account>-<region>.json`. 합친 스냅샷은 `part`가 없거나 범위 밖이면 422.
- 라이브러리: `api.cloudformation`(= `template`), `api.cloudformation_parts`(= `templates`).

## 테스트 (`tests/test_iac.py`)
- `assert_self_contained`: 모든 `Ref`·`GetAtt`·`Sub`·`DependsOn` 대상 존재, 모든 매개변수 사용, `None` 없음, 논리 ID 영숫자, `ID_FIELDS`의 물리 ID 유출 없음, Unsupported 항목마다 `Type`·`Id`·`Reason`.
- 샘플별 기대: three-tier(서브넷 12, NAT·EIP 3, 리스너 2, Unsupported는 CloudFront·WAF뿐), eks-platform·gpu-training·genai-rag·data-platform의 미지원 목록, 설계 스냅샷(`Design`)의 모든 리소스 종류, moto 에뮬레이터 수집 스냅샷(수집 값이 매개변수보다 우선).
- `test_cfn_lint_accepts_every_template`: `cfnlint.api`가 있으면(moto[cloudformation] extra) 모든 템플릿에 E·W 규칙 문제가 없어야 한다. 없으면 skip.
- `test_emulated_stack_creates_the_resources[three-tier|microservices]`: moto에서 `create_stack`이 `CREATE_COMPLETE`가 되고 VPC·서브넷·LB·RDS·ASG·ECS 서비스 수가 템플릿과 일치. SG 인그레스 중 `SourcePrefixListId` 규칙은 moto 한계로 에뮬레이터 스택에서만 뺀다.
- `test_routing_domains_edge_and_dedicated_links_are_drafted_or_listed`, `test_api_downloads_the_template_per_account`, `test_cli_exports_files_and_stdout`.
- `tests/test_golden.py`: 모든 샘플과 데모·엣지·설계안의 `templates()` 출력(키 순서 포함) SHA-256을 `tests/golden.json`에 고정한다. 리팩터링은 이 값을 바꾸지 않아야 한다.

## 바꿀 때 주의
- 새 리소스 종류는 대상(서브넷·VPC·SG)이 템플릿에 없으면 반드시 `skip()`하고, 물리 ID를 속성 값으로 쓰지 않는다. 새 raw 키의 ID 필드는 `ID_FIELDS`에 넣어 유출 검사를 받게 한다.
- 단계 순서를 바꾸면 `ids` 조회가 실패해 조용히 Unsupported가 늘 수 있다. 샘플별 개수 테스트가 이를 잡는다.

## 관련
- [[multi-account]] — 합친 스냅샷의 part별 템플릿과 공유 TGW.
- [[review-rules]] — `COST-EIP-IDLE` 등 같은 입력을 보는 검토 규칙.
- [[cli]] — `export-cfn` 옵션과 종료 코드.
- [[snapshot-model]] — 템플릿 입력인 `raw` 키.
- [[http-api]] — `/cloudformation` 다운로드 엔드포인트.
