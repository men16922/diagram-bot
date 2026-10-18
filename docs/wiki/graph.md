---
title: 관계 그래프 (graph)
summary: raw API 데이터에서 노드·관계를 만드는 결정적 조인 규칙 — 노드 종류 45개와 관계 43개, 관계 ID 규칙, 참조 전용 노드, 서브넷 경로 분류, 고가용성 역할, 계정 통합 병합, 옛 그래프 업그레이드.
sources: [src/diagram_bot/graph.py, src/diagram_bot/static/js/labels.js, tests/test_graph_report.py]
related: [snapshot-model, collection, views, layout-and-drawio, howto-add-collected-service]
updated: 2026-10-19
---
# 관계 그래프 (graph)

> `graph.build_graph(snapshot)`은 `raw`의 API 필드에 적힌 ID·ARN·DNS 이름만으로 노드와 관계를 잇는다. 트래픽이나 IP로 인스턴스를
> 짐작하지 않으며(모듈 docstring: "No traffic inference or IP-to-instance guessing"), 모든 관계는 `evidence`에 근거 필드를 적는다.

## 핵심
- 입력이 같으면 결과도 같다: 노드는 `id`, 관계는 `(source, relation, target, id)` 순으로 정렬한다.
- 노드 필드·관계 필드 정의는 [[snapshot-model]]의 graph 절.
- 내부 도우미 두 개가 모든 것을 만든다.
  - `node(kind, resource_id, attrs, source, reference=False)`: 키가 없거나 `reference=False`이면 노드를 (다시) 쓴다. 이미 있는 키에 `reference=True`로 부르면 기존 노드를 그대로 둔다.
  - `link(source, kind, resource_id, relation, evidence, details, qualifiers)`: 대상이 없으면 `reference_only` 노드를 만든 뒤 관계를 넣는다. `resource_id`가 비면 아무것도 하지 않는다. 같은 ID의 관계는 한 번만 들어간다(`edges.setdefault`).
- `build_graph`의 처리 순서: `core_resources`(기본 5종 `vpcs`, `subnets`, `instances`, `load_balancers`, `target_groups`) → `target_registrations`(`ecs_tasks` → `target_health`) → `security_groups` → `endpoints_and_transit` → `gateways` → `route_tables` → `databases` → `scaling_groups` → `db_clusters` → `caches` → `kafka` → `lambdas` → `kubernetes` → `search_domains` → `file_systems` → `warehouses` → `connectivity` → `ai_infrastructure` → `elastic_ips` → `transit_routing` → `edge_services` → `direct_connect` → `classify_subnets`.

## 노드 종류

`labels.js`의 `names`에 표시 이름이 모두 있다(45개).

| 함수 | 종류 |
| --- | --- |
| `core_resources` / `target_registrations` / `build_graph` | `vpc`, `subnet`, `ec2`, `load_balancer`, `target_group`, 참조 전용 `network_interface`·`volume` / `registered_target` / `security_group` |
| `gateways` / `route_tables` / `databases` / `scaling_groups` | `internet_gateway`, `nat_gateway` / `route_table` / `db_instance`, `db_standby` / `auto_scaling_group` |
| `endpoints_and_transit` | `vpc_endpoint`, `transit_gateway`, `tgw_attachment` |
| `db_clusters` / `caches` / `kafka` | `db_cluster` / `cache_replication_group`, `cache_node` / `msk_cluster`, `msk_broker` |
| `lambdas` / `kubernetes` / `ecs_tasks` | `lambda_function` / `eks_cluster` / `ecs_service`, `ecs_task` |
| `search_domains` / `file_systems` / `warehouses` | `opensearch_domain`, `search_node` / `efs_file_system`, `efs_mount_target` / `redshift_cluster` |
| `ai_infrastructure` | `placement_group`, `fsx_file_system`, `sagemaker_endpoint`, `sagemaker_cluster` |
| `connectivity` | `vpc_peering`, `vpn_gateway`, `customer_gateway` |
| `elastic_ips` / `transit_routing` | `elastic_ip` / `tgw_route_table` |
| `edge_services` / `direct_connect` | `waf_web_acl`, `cloudfront_distribution` / `dx_connection`, `dx_gateway` |

`msk_broker`, `search_node`은 서브넷마다 하나씩 만드는 묶음 노드(`ClusterArn#subnet`, `ARN#subnet`)이고, `db_standby`(`id#standby`)와 Redshift Multi-AZ 보조(`id#secondary`)는 대기 AZ를 그리기 위한 노드다.

## 관계 이름

`labels.js`의 `relationNames`에 43개 관계의 표시 이름이 모두 있다(없으면 `app.js`가 `relationNames[r]||r`로 원래 키를 보여 준다). `tests/test_labels_and_wiki.py`가 샘플·데모·설계 그래프의 모든 종류·관계에 표시 이름이 있는지 확인한다.

- 배치·소속: `in_vpc`, `in_subnet`, `lambda_subnet`, `eks_subnet`, `service_subnet`, `endpoint_subnet`, `attachment_subnet`, `sagemaker_subnet`, `hyperpod_subnet`(HyperPod 두 번째 이후 서브넷), `in_placement_group`.
- 로드 밸런싱: `associated_load_balancer`(대상 그룹→LB), `registered_target`(대상 그룹→대상, `port`·`availability_zone` 한정자), `asg_target_group`, `ecs_target_group`.
- EC2 참조: `uses_security_group`, `attached_interface`, `attached_volume`.
- 라우팅: `associated_subnet`(라우팅 테이블→서브넷), `routes_to`(라우팅 테이블→NAT·TGW·피어링·IGW·VPCE·VGW, `destination` 한정자), `endpoint_route_table`.
- 묶음·역할: `asg_member`, `eks_nodegroup`(EKS→ASG), `service_task`, `cluster_member`, `cache_member_of`, `broker_of`, `node_of`, `mount_target_of`, `read_replica_of`, `standby_of`(RDS 대기, Redshift 보조), `orchestrated_by`(HyperPod→EKS).
- 네트워크 연결: `attached_transit_gateway`, `peer_vpc`, `vpn_connection`(VGW 또는 TGW→고객 게이트웨이), `eip_associated`.
- TGW 라우팅 도메인: `route_table_of`, `tgw_rt_association`, `tgw_rt_propagation`, `tgw_route`.
- 엣지·전용 회선: `waf_protects`(웹 ACL→LB 또는 배포), `cloudfront_origin`(배포→LB), `dx_virtual_interface`(VGW 또는 DX 게이트웨이→DX 연결), `dx_gateway_association`(DX 게이트웨이→VGW·TGW).

## 관계 ID (`relation_id`)
- 키 = JSON `[source, relation, target, qualifiers]`(`sort_keys`), ID = `"rel-" + sha256(key)[:20]`.
- `qualifiers`에는 `QUALIFIERS = ("port", "availability_zone")`가 값이 없어도 항상 들어가고, `destination`은 값이 있을 때만 들어간다(라우팅·VPN·VIF·오리진 관계). 그래서 경로가 없는 옛 관계의 ID 모양이 그대로다.
- 상태(TargetHealth 등)처럼 자주 바뀌는 값은 ID에 넣지 않는다. 다시 수집해도 같은 관계는 같은 ID다(`test_relation_ids_ignore_input_order_and_health`). 같은 인스턴스가 두 포트로 등록되면 관계가 둘이다(`test_same_instance_on_two_ports_is_two_relations`).
- draw.io 문서의 관계 셀은 이 ID를 `relationId` 속성으로 가진다([[layout-and-drawio]]).

## 참조 전용 노드
- 다른 응답에서 ID만 본 대상: 조회되지 않은 인스턴스(예: `i-demo-missing`), SG·ENI·EBS, 수집되지 않은 VPC 등. `reference_only: True`, `attributes: {}`(또는 아는 값), `source: None`.
- 일부러 참조로 두는 것: IP 주소가 없는 고객 게이트웨이(VPN 연결에서만 앎), 파트너 소유 hosted VIF의 `dx_connection`, TGW 라우팅 테이블 API에서만 아는 VPN·DX·피어링 attachment, 배포의 `WebACLId`로만 아는 웹 ACL.
- 수집된 노드를 나중에 다시 쓰면 앞서 붙인 파생 필드가 사라진다. 그래서 `transit_routing`은 모든 노드를 만든 뒤에 파생 값을 쓴다.

## 조인 규칙 (추정 금지의 예)
- 종료된 인스턴스(`State.Name == "terminated"`)는 노드를 만들지 않는다.
- 대상 유형 `instance`→`ec2`, `alb`→`load_balancer`, 그 밖은 `registered_target`(ID `"{TG ARN}#{Id}:{Port}:{AZ}"`). IP 대상은 ECS 서비스가 그 대상 그룹을 이름으로 가리키고 태스크 IP가 같을 때만 `ecs_task`로 잇는다. IP가 같은 EC2와 합치지 않는다(`test_ip_target_is_not_guessed_to_be_ec2`).
- IP 대상의 서브넷 배치(`place_ip_target`): 대상 그룹 VPC 안에서 CIDR이 IP를 포함하고 AZ가 맞는 서브넷이 **정확히 하나**일 때만. `AvailabilityZone == "all"`이면 VPC 밖 주소라 배치하지 않는다.
- RDS·ElastiCache·Redshift의 서브넷: 서브넷 그룹에서 해당 AZ의 서브넷이 하나일 때만 `in_subnet`. 세 곳 모두 `group_subnet(group, zone)` 하나를 쓴다(태그 사전은 `tag_map`, 서브넷 ID → 서브넷은 `subnet_index`).
- MSK 브로커는 `ListNodes`의 `ClientSubnet`, 없으면 클라이언트 서브넷에 균등 분배하고 `Estimated: True`. `ListNodes`가 일부 서브넷만 알려 주면(부분 응답, 브로커 교체 중) 빠진 서브넷은 브로커 0개, `Estimated: True`로 그린다. OpenSearch 노드도 `DescribeDomainNodes`가 없으면 같은 방식.
- VPC Lambda만 `in_vpc`; `VpcConfig.VpcId`가 없으면 노드만 있고 그림에는 안 들어간다.
- 피어링은 한쪽 VPC가 수집되어 있을 때만 만들고, 양쪽 다 있으면 두 번째에 `peer_vpc`.
- CloudFront 오리진은 LB `DNSName`(앞의 `dualstack.` 제거)과 정확히 같을 때만 `cloudfront_origin`. S3 엔드포인트(`S3_ORIGIN` 정규식), VPC 오리진, 그 밖의 도메인(LB 앞 CNAME 포함)은 `origins` 목록에만 남긴다.
- Elastic IP 소유자: NAT 주소 할당 ID → LB 주소 할당 ID → `InstanceId` → `NetworkInterfaceId` 순. 연결이 없으면 관계 없이 노드만(검토 `COST-EIP-IDLE` 대상).
- Direct Connect: 삭제·거부 상태는 건너뛴다. 퍼블릭 VIF는 AWS 쪽 게이트웨이가 없어 그리지 않는다. 다른 리전 게이트웨이와의 연결은 관계 대신 `properties`의 "다른 리전의 연결 게이트웨이"로 남긴다.

## 서브넷 경로 분류 (`classify_subnets`)
- `route_tables`가 있을 때만 동작한다. 서브넷의 명시 연결 테이블, 없으면 VPC 주 테이블(`Main`)을 쓰고, 주 테이블이면 `associated_subnet` 관계를 `details.implicit = True`로 추가한다.
- 활성 `0.0.0.0/0` 경로의 대상으로 `route_class`를 정한다: `igw-` → `public`, NAT → `nat`, TGW → `tgw`, `vgw-` → `vgw`, 없음 → `isolated`. `route_table`에 테이블 ID를 남긴다.
- 표시 이름은 `labels.js`의 `routeClasses`, 배치의 서브넷 색·층 순서는 [[layout-and-drawio]].

## 고가용성 역할 (라벨용 파생 값)
- `db_role`: 같은 리전 복제본 `read replica`, 다른 리전 원본(ARN) `cross-region replica`, DB 클러스터의 writer 멤버 `writer`, 나머지 멤버는 Aurora·DocumentDB·Neptune이면 `reader`, Multi-AZ DB 클러스터면 `readable standby`. `MultiAZ`와 `SecondaryAvailabilityZone`이 있으면 `db_standby` 노드.
- ElastiCache `CurrentRole`(복제 그룹 멤버 역할), 클러스터 모드면 `Shard`(노드 그룹 ID). OpenSearch `search_node`의 `Data`·`Master`·`Warm`·`Standby`. Redshift `Role`(`primary`/`secondary`, Multi-AZ가 아니면 없음). EFS 마운트 타깃 `OneZone`.
- 이 값을 라벨 문자열로 바꾸는 것은 `layout.resource_label`이다(`test_service_placement_and_high_availability_roles`).

## 계정 통합과 전역 리소스
- `GLOBAL_KINDS = {"cloudfront_distribution", "dx_gateway"}`와 `:global/` ARN(CloudFront용 WAF)은 ID의 리전 자리에 `global`을 쓴다. 같은 계정의 두 리전을 합쳐도 한 번만 나온다.
- `merge_graphs`: 부분 그래프의 노드를 합칠 때 수집된 노드가 참조 노드보다 우선한다. `SHARED_KINDS = {"transit_gateway", "vpc_peering"}`는 `(kind, resource_id)`가 같으면 하나로 합치며 소유 계정(`OwnerId == account`) 사본을 남기고, 관계의 끝점을 바꾼 뒤 ID를 다시 계산한다(`test_combined_accounts_share_one_transit_gateway_and_one_peering`).

## 옛 그래프 업그레이드 (`upgrade_graph`)
- `id`가 없는 관계에 `details.Target.Port`·`AvailabilityZone`(또는 `details.availability_zone`)로 `qualifiers`를 만들고 `relation_id`를 붙인다. 관계 순서는 그대로 둔다(옛 문서의 `relationIndex` 호환).
- `SnapshotStore.get`이 읽을 때마다 적용한다. 새 ID와 같은 값이 나와야 한다(`test_legacy_graph_upgrade_keeps_order_and_matches_new_ids`).

## 바꿀 때 주의
- 관계 이름이나 `QUALIFIERS`를 바꾸면 저장된 문서의 `relationId`가 끊긴다. `relation_id`의 키 모양은 바꾸지 않는다.
- 새 종류·관계는 `labels.js`와 [[views]]의 분류 집합에도 넣는다([[howto-add-collected-service]]).
- 관련 테스트: `tests/test_graph_report.py` 전체, 특히 `test_edge_elastic_ips_routing_domains_and_direct_connect_join_on_api_fields`(엣지·EIP·TGW·DX 조인과 `build_graph` 결정성).

## 관련
- [[snapshot-model]] — 노드·관계 필드 정의.
- [[views]] — 어떤 노드·관계를 어느 보기에 그리는지.
- [[layout-and-drawio]] — 라벨·아이콘·배치.
