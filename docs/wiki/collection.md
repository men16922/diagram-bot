---
title: 읽기 전용 수집 (collector)
summary: collector.collect의 호출 목록(OPERATIONS)과 IAM 권한, 2단계 병렬 수집, 페이지 처리, 부분·조회 불가 기록, 계정 확인, 역할 맡기(ReadOnlyAccess 세션 정책), prepare 훅, 전역 API, 읽기 전용 정책 파일과의 관계.
sources: [src/diagram_bot/collector.py, src/diagram_bot/sessions.py, readonly-policy.json, deploy/readonly-role.cfn.json, src/diagram_bot/aws_access.py, src/diagram_bot/routes/aws.py, src/diagram_bot/api.py, tests/test_collector.py, tests/test_aws_emulator.py]
related: [snapshot-model, graph, aws-access-and-sso, browser-egress, howto-add-collected-service, cli]
updated: 2026-10-19
---
# 읽기 전용 수집 (collector)

> `collector.collect(region, …)`는 boto3로 Describe/List/Get/Search 호출만 해서 스냅샷 `{schema_version, metadata, coverage, raw}`를
> 만든다. 권한이 없거나 중간에 실패한 호출은 버리지 않고 coverage에 `partial`/`unavailable`로 남기며, 나머지는 계속 수집한다.

## 핵심
- 시그니처: `collect(region, profile=None, expected_account=None, session=None, role_arn=None, external_id=None, workers=8, prepare=None)`.
- 호출처: CLI `collect`(`cli.collect_all`, 계정·리전마다 한 번), 웹 `routes/aws.py`(백그라운드 작업, `prepare=aws.prepare_for(user)`), 라이브러리 `api.collect_snapshot`(결과에 `graph`를 붙여 반환).
- 클라이언트 설정: `retries={"mode": "adaptive", "total_max_attempts": 8}`, `connect_timeout=10`, `read_timeout=30`, `max_pool_connections=16`.
- 결과 모양과 필드 뜻은 [[snapshot-model]].

## 순서
1. `prepare(session or sdk_session(region, profile))` (`sessions.py`).
2. `role_arn`이 있으면 `assume(...)`로 새 세션을 만들고 다시 `prepare`를 적용한다.
3. `sts.get_caller_identity`로 계정을 확인한다. `expected_account`와 다르면 `ValueError("계정 불일치: 요청 …, 현재 인증 …")`를 던지고 **자원 호출을 하나도 하지 않는다**(`test_account_mismatch_stops_before_resource_calls`). 역할을 맡은 경우 맡은 뒤의 계정을 확인한다.
4. `OPERATIONS`에 나오는 서비스와 `wafv2`의 클라이언트, 전역 WAF용 `wafv2/global` 클라이언트를 만든다.
5. 1단계(목록) → 2단계(상세) → 중간 키 삭제 → `not_discovered` 행 추가 → 스냅샷 반환.

## 1단계: OPERATIONS

`(service, operation, 응답 키, raw 이름)`. 권한은 `collector.permission(service, operation)`이 만든다: `IAM_PREFIX`(`elbv2`→`elasticloadbalancing`, `opensearch`→`es`, `efs`→`elasticfilesystem`)로 접두사를 바꾸고, 메서드 이름을 TitleCase로 바꾼 뒤 `ACTIONS`(`DescribeDBInstances`, `DescribeDBClusters`, `ListWebACLs`, `ListResourcesForWebACL`)로 대소문자를 고친다.

| service · operation | 응답 키 | raw | IAM |
| --- | --- | --- | --- |
| `ec2` `describe_vpcs` | `Vpcs` | `vpcs` | `ec2:DescribeVpcs` |
| `ec2` `describe_subnets` | `Subnets` | `subnets` | `ec2:DescribeSubnets` |
| `ec2` `describe_instances` | `Reservations` | `instances` (평탄화) | `ec2:DescribeInstances` |
| `elbv2` `describe_load_balancers` | `LoadBalancers` | `load_balancers` | `elasticloadbalancing:DescribeLoadBalancers` |
| `elbv2` `describe_target_groups` | `TargetGroups` | `target_groups` | `elasticloadbalancing:DescribeTargetGroups` |
| `ec2` `describe_internet_gateways` | `InternetGateways` | `internet_gateways` | `ec2:DescribeInternetGateways` |
| `ec2` `describe_nat_gateways` | `NatGateways` | `nat_gateways` | `ec2:DescribeNatGateways` |
| `ec2` `describe_route_tables` | `RouteTables` | `route_tables` | `ec2:DescribeRouteTables` |
| `rds` `describe_db_instances` | `DBInstances` | `db_instances` | `rds:DescribeDBInstances` |
| `autoscaling` `describe_auto_scaling_groups` | `AutoScalingGroups` | `auto_scaling_groups` | `autoscaling:DescribeAutoScalingGroups` |
| `ec2` `describe_vpc_endpoints` | `VpcEndpoints` | `vpc_endpoints` | `ec2:DescribeVpcEndpoints` |
| `ec2` `describe_transit_gateways` | `TransitGateways` | `transit_gateways` | `ec2:DescribeTransitGateways` |
| `ec2` `describe_transit_gateway_vpc_attachments` | `TransitGatewayVpcAttachments` | `transit_gateway_attachments` | `ec2:DescribeTransitGatewayVpcAttachments` |
| `rds` `describe_db_clusters` | `DBClusters` | `db_clusters` | `rds:DescribeDBClusters` |
| `elasticache` `describe_cache_clusters` | `CacheClusters` | `cache_clusters` | `elasticache:DescribeCacheClusters` |
| `elasticache` `describe_replication_groups` | `ReplicationGroups` | `replication_groups` | `elasticache:DescribeReplicationGroups` |
| `elasticache` `describe_cache_subnet_groups` | `CacheSubnetGroups` | `cache_subnet_groups` | `elasticache:DescribeCacheSubnetGroups` |
| `kafka` `list_clusters_v2` | `ClusterInfoList` | `msk_clusters` | `kafka:ListClustersV2` |
| `lambda` `list_functions` | `Functions` | `lambda_functions` | `lambda:ListFunctions` |
| `eks` `list_clusters` | `clusters` | `eks_cluster_names` (중간) | `eks:ListClusters` |
| `opensearch` `list_domain_names` | `DomainNames` | `opensearch_domain_names` (중간) | `es:ListDomainNames` |
| `ecs` `list_clusters` | `clusterArns` | `ecs_cluster_arns` (중간) | `ecs:ListClusters` |
| `efs` `describe_file_systems` | `FileSystems` | `efs_file_systems` | `elasticfilesystem:DescribeFileSystems` |
| `redshift` `describe_clusters` | `Clusters` | `redshift_clusters` | `redshift:DescribeClusters` |
| `redshift` `describe_cluster_subnet_groups` | `ClusterSubnetGroups` | `redshift_subnet_groups` | `redshift:DescribeClusterSubnetGroups` |
| `ec2` `describe_vpc_peering_connections` | `VpcPeeringConnections` | `vpc_peering_connections` | `ec2:DescribeVpcPeeringConnections` |
| `ec2` `describe_vpn_gateways` | `VpnGateways` | `vpn_gateways` | `ec2:DescribeVpnGateways` |
| `ec2` `describe_vpn_connections` | `VpnConnections` | `vpn_connections` | `ec2:DescribeVpnConnections` |
| `ec2` `describe_customer_gateways` | `CustomerGateways` | `customer_gateways` | `ec2:DescribeCustomerGateways` |
| `ec2` `describe_security_groups` | `SecurityGroups` | `security_groups` | `ec2:DescribeSecurityGroups` |
| `ec2` `describe_placement_groups` | `PlacementGroups` | `placement_groups` | `ec2:DescribePlacementGroups` |
| `fsx` `describe_file_systems` | `FileSystems` | `fsx_file_systems` | `fsx:DescribeFileSystems` |
| `sagemaker` `list_endpoints` | `Endpoints` | `sagemaker_endpoint_names` (중간) | `sagemaker:ListEndpoints` |
| `sagemaker` `list_clusters` | `ClusterSummaries` | `sagemaker_cluster_names` (중간) | `sagemaker:ListClusters` |
| `ec2` `describe_addresses` | `Addresses` | `elastic_ips` | `ec2:DescribeAddresses` |
| `ec2` `describe_transit_gateway_route_tables` | `TransitGatewayRouteTables` | `transit_gateway_route_tables` | `ec2:DescribeTransitGatewayRouteTables` |
| `cloudfront` `list_distributions` | `DistributionList.Items` | `cloudfront_distributions` | `cloudfront:ListDistributions` |
| `directconnect` `describe_connections` | `connections` | `dx_connections` | `directconnect:DescribeConnections` |
| `directconnect` `describe_virtual_interfaces` | `virtualInterfaces` | `dx_virtual_interfaces` | `directconnect:DescribeVirtualInterfaces` |
| `directconnect` `describe_direct_connect_gateways` | `directConnectGateways` | `dx_gateways` | `directconnect:DescribeDirectConnectGateways` |

"(중간)" 키는 2단계 입력으로만 쓰고 반환 전에 `raw`에서 지운다.

## 2단계: 상세 호출

모듈 수준 함수 10개(`DETAILS` 튜플). 각 함수는 `(raw, call, log)`를 받아 1단계 목록을 읽고, `collect`의 `call(log, …)`로 호출해 자기 coverage 목록에 기록한 뒤 `{raw 키: 행}`을 돌려준다. 가짜 `call`로 따로 시험할 수 있다(`test_detail_steps_run_on_a_stub_call`).
- `target_health`: 대상 그룹마다 `elbv2.describe_target_health` → `target_health`.
- `eks`: 클러스터마다 `describe_cluster`, `list_nodegroups`, 노드 그룹마다 `describe_nodegroup` → `eks_clusters`(각 항목에 `nodegroups`).
- `msk`: `ClusterType`이 `PROVISIONED`인 클러스터만 `kafka.list_nodes` → `msk_nodes`.
- `opensearch`: 도메인 이름을 5개씩 `describe_domains`, `VPCOptions.SubnetIds`가 있는 도메인만 `describe_domain_nodes`.
- `ecs`: 클러스터마다 `list_services` → `describe_services`(10개씩), `list_tasks(desiredStatus="RUNNING")` → `describe_tasks`(100개씩).
- `efs`: 파일 시스템마다 `describe_mount_targets`.
- `sagemaker`: 엔드포인트마다 `describe_endpoint` → `describe_endpoint_config` → 모델 이름마다 `describe_model`을 합쳐 한 항목(`EndpointConfig`, `Models`). HyperPod는 `describe_cluster`. 응답 키 `None`은 응답 전체(메타데이터 제외)를 한 행으로 쓴다는 뜻이다.
- `transit_routes`: TGW 라우팅 테이블마다 `get_transit_gateway_route_table_associations`, `…_propagations`, `search_transit_gateway_routes`(필터 `state` = `active`, `blackhole`). `AdditionalRoutesAvailable`이면 해당 coverage 행을 `partial`, `error_code="AdditionalRoutesAvailable"`로 바꾼다(한 호출 최대 1,000개).
- `web_acls`: REGIONAL 범위 `list_web_acls(Scope="REGIONAL", Limit=100)` → ACL마다 `list_resources_for_web_acl(ResourceType="APPLICATION_LOAD_BALANCER")`. 이어서 `wafv2/global` 클라이언트로 `Scope="CLOUDFRONT"`.
- `direct_connect`: DX 게이트웨이마다 `describe_direct_connect_gateway_associations`.

2단계가 끝난 뒤 `describe_target_groups`가 `complete`가 아니면 `describe_target_health` 행을 `status: not_discovered`, `reason` 문장과 함께 덧붙인다(조회하지 못한 대상 그룹의 대상은 "없음"이 아니라 "미발견").

## 병렬 처리와 순서
- 1단계: 서비스마다 스레드 하나(`ThreadPoolExecutor(max_workers=workers)`), 한 서비스 안의 호출은 순서대로 실행한다. 서비스별 스로틀링이 서로 섞이지 않게 하려는 구조다.
- coverage는 완료 순서와 무관하게 `OPERATIONS` 순서로 합친다(`logs[index]`).
- 2단계: 상세 함수 10개를 같은 크기의 풀에서 동시에 실행하고, `DETAILS` 순서대로 `raw`와 coverage에 합친다.

## 페이지 처리와 실패 기록 (`query`)
- `client.can_paginate(operation)`이면 boto3 paginator, `MARKERS`에 있으면(`list_web_acls` → `NextMarker`) `marker_pages`로 직접 넘긴다. 빈 페이지는 마커가 있어도 끝으로 본다. 둘 다 아니면 한 번 호출한다.
- `extract(page, key)`: 점으로 이어진 키(`DistributionList.Items`)는 중첩 객체를 따라간다. 결과가 dict면 한 행으로 감싼다.
- 실패 시 이미 받은 페이지는 유지한다: 한 페이지 이상 받았으면 `partial`, 아니면 `unavailable`. `ClientError`는 응답의 오류 코드, `BotoCoreError`와 그 밖의 예외는 클래스 이름을 `error_code`로 둔다. 예외 하나가 다른 서비스 수집을 막지 않는다.
- 거부(`unavailable`)와 빈 성공(`complete`, `items: 0`)은 구분된다(`test_denied_is_distinct_from_empty_success`).

## 역할 맡기와 계정 확인 (`assume`)
- `sts.assume_role(RoleArn, RoleSessionName="diagram-bot-readonly", DurationSeconds=3600, PolicyArns=[{"arn": "arn:{partition}:iam::aws:policy/ReadOnlyAccess"}])`, `external_id`가 있으면 `ExternalId`. partition은 역할 ARN에서 읽는다.
- 세션 정책 때문에 역할 자체가 더 넓어도 이 세션은 읽기만 할 수 있다(`test_assume_role_caps_session_to_read_only_and_checks_the_assumed_account`).
- 맡은 자격 증명으로 새 세션(`sessions.credentials_session`)을 만들고 다시 `prepare`를 적용한다.

## prepare 훅 (브라우저 egress)
- `prepare(session)`은 수집에 쓰는 **모든** 세션(처음 세션과 맡은 역할의 세션)에 적용된다. 기본값은 항등 함수.
- 웹 앱은 `AwsAccess.prepare_for(user)`를 넘긴다. 브라우저 egress 모드면 `BrowserRelay.attach(session, user)`가 `before-send` 이벤트에 걸려 서명된 요청을 사용자 브라우저가 보내게 하고, 아니면 `None`이다([[browser-egress]], [[aws-access-and-sso]]).

## 전역 API
- CloudFront `list_distributions`는 전역 API라 수집 리전의 클라이언트로 호출한다.
- CloudFront용 WAF(`Scope=CLOUDFRONT`)는 파티션의 전역 WAF 리전에서만 응답한다: `GLOBAL_REGION = {"aws": "us-east-1", "aws-cn": "cn-northwest-1", "aws-us-gov": "us-gov-west-1"}`. 파티션은 호출자 ARN에서 읽는다.
- Direct Connect 게이트웨이는 계정 전역이다. 그래프는 이들을 `global` 범위 ID로 만들어 리전을 합쳐도 한 번만 보이게 한다([[graph]]).
- 이 사실은 `metadata.limitations`에도 문장으로 남는다.

## 읽기 전용 정책 파일
- `readonly-policy.json`(저장소 루트): 수집기가 부르는 IAM 액션 목록. 단일 계정에서 수집 사용자·역할에 붙인다.
- `deploy/readonly-role.cfn.json`: 멤버 계정용 `AWS::IAM::Role` 템플릿. 매개변수 `CollectorPrincipalArn`, `ExternalId`(NoEcho, 8자 이상), `RoleName`(기본 `DiagramBotReadOnly`), `MaxSessionDuration` 3600, 신뢰 정책에 `sts:ExternalId` 조건, 인라인 정책 `DiagramBotDescribeOnly`, 출력 `RoleArn`.
- 테스트가 세 곳을 묶는다(`test_deploy_template_grants_exactly_the_collector_actions`): 템플릿 액션 = 정책 액션 ∪ `{sts:GetCallerIdentity}`, 모든 `OPERATIONS` 권한 ⊆ 정책, 모든 액션 이름이 `Describe`/`List`/`Get`/`Search`로 시작. 또 `test_edge_routing_and_direct_connect_calls_keep_partial_and_denied_results`는 실제 coverage의 모든 `permission`이 정책에 있는지 확인한다(2단계 호출 포함).

## 바꿀 때 주의
- 호출을 추가하면 정책 파일 두 개를 같이 고친다. 순서는 [[howto-add-collected-service]].
- `test_collector.py`의 `Session.clients`에 서비스가 없으면 Stubber 테스트가 실패한다. 응답 키가 점 표기면 `EMPTY`에 빈 응답을 등록한다.
- 에뮬레이터(moto) 테스트는 moto가 구현하지 않은 호출을 `EMULATOR_GAPS`로 허용한다(`list_nodes`, `describe_domain_nodes`, `describe_placement_groups`, `describe_virtual_interfaces`, `describe_direct_connect_gateways`).
- 수집은 설정만 읽는다. 실제 통신 여부·트래픽은 판단하지 않는다(`metadata.limitations` = `LIMITATIONS`, 범위 목록은 `SCOPE`).

## 관련
- [[snapshot-model]] — coverage·raw 필드 정의.
- [[graph]] — raw를 노드·관계로 바꾸는 규칙.
- [[aws-access-and-sso]] — 수집에 쓰는 세션을 만드는 곳.
- [[browser-egress]] — `prepare` 훅으로 요청을 브라우저가 보내는 방식.
- [[howto-add-collected-service]] — 새 읽기 호출 추가 절차.
