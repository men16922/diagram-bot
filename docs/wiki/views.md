---
title: 보기 계약 (views)
summary: views.view_graph가 정하는 overview·detail·focus 보기의 노드·관계 선택, references 옵션, 경로 필터(routes=), 개수(visible·collected·references·hidden·filtered), 전체 구성의 네트워크·엣지 연결선, 포함 관계.
sources: [src/diagram_bot/views.py, src/diagram_bot/api.py, src/diagram_bot/routes/documents.py, src/diagram_bot/store.py, tests/test_graph_report.py]
related: [graph, layout-and-drawio, snapshot-model, http-api]
updated: 2026-10-18
---
# 보기 계약 (views)

> `views.view_graph(graph, view, vpc, resource, references, routes)`는 한 보기에 보일 노드 ID와 그릴 관계, 개수를 돌려준다.
> 스냅샷 그래프는 바꾸지 않는다(모듈 docstring). 배치는 이 결과만 보고 좌표를 계산한다([[layout-and-drawio]]).

## 핵심
- `VIEWS = ("overview", "detail", "focus")`. `view`가 없으면 `vpc`가 있을 때 `detail`, 아니면 `overview`.
- 반환값: `view`(`kind`, `vpc`, `resource`, `references`, `routes`), `nodes`(정렬된 ID), `edges`, `focus`, `routes`(필터 요약 또는 `None`), `counts`.
- 호출처: `drawio.layout_document`, `api.view`, `routes/documents.py`의 `view_scope`. 문서 저장 키도 보기 범위(`view, vpc, resource, references, routes`)로 정한다(`DocumentStore.path`).
- 오류는 `ValueError`(한국어 메시지): 지원하지 않는 보기, detail 밖의 경로 필터, 스냅샷에 없는 VPC·리소스, 이 VPC의 LB·대상 그룹이 아닌 경로 필터.

## 분류 집합
- `CORE = {associated_load_balancer, registered_target}`: 모든 보기에서 그리는 구성 관계.
- `AUXILIARY = {uses_security_group, attached_interface, attached_volume}`와 `AUXILIARY_KINDS = {security_group, network_interface, volume}`: references를 켰을 때만.
- `REGIONAL = {attached_transit_gateway, vpn_connection, dx_virtual_interface}`: VPC 상세에 함께 그리는 리전 리소스 연결. 그중 `REMOTE = {vpn_connection, dx_virtual_interface}`는 AWS 밖(데이터 센터 상자)으로 나간다. `SITE_KINDS = {customer_gateway, dx_connection}`.
- `EDGE = {cloudfront_origin}`: 리전 밖 엣지.
- `SUBNET_KINDS`: 서브넷 안에 그리는 종류(`ec2`, `nat_gateway`, `db_instance`, `db_standby`, `cache_node`, `msk_broker`, `registered_target`, `ecs_task`, `search_node`, `efs_mount_target`, `redshift_cluster`, `fsx_file_system`, `sagemaker_cluster`).
- `GROUPS = {auto_scaling_group: asg_member, ecs_service: service_task}`와 `GROUP_TARGETS = {asg_target_group, ecs_target_group}`: 멤버를 감싸는 틀과 그 틀→대상 그룹 관계.
- `UNDRAWN_KINDS`: 그래프·속성 패널에는 있지만 도형으로 그리지 않는 종류(`route_table`, `db_cluster`, `cache_replication_group`, `msk_cluster`, `opensearch_domain`, `efs_file_system`, `placement_group`, `elastic_ip`, `waf_web_acl`, `tgw_route_table`). 포커스 대상이면 예외로 보인다. EIP는 소유자 라벨, 웹 ACL은 WAF 배지, TGW 라우팅 테이블은 게이트웨이·VPC 카드에 나타난다.

## 포함 관계 (`containment`)
- `vpc_of`: `in_vpc` 관계의 source → VPC 노드.
- `subnet_of`: source가 `SUBNET_KINDS`인 `in_subnet` 관계만. 그래서 LB·EKS·Lambda처럼 여러 서브넷에 걸친 리소스는 서브넷 안에 놓이지 않는다.

## detail (VPC 상세)
1. 보이는 노드: VPC, `vpc_of`가 이 VPC인 노드, 이 VPC를 `peer_vpc`로 가리키는 피어링.
2. `CORE | REGIONAL` 관계의 대상을 두 번 따라가 추가한다(참조 전용인 등록 대상·LB도 보인다).
3. `references`면 `AUXILIARY` 대상 추가.
4. 이 VPC의 LB를 오리진으로 둔 CloudFront 배포(`EDGE` source) 추가.
5. 그릴 관계: `CORE | REGIONAL | EDGE | GROUP_TARGETS`(+ references면 `AUXILIARY`) 중 양 끝이 보이는 것. detail에서 `in_subnet`은 관계선이 아니라 포함으로 표현하므로 뺀다.
- `GROUP_TARGETS`는 후보다. 배치가 틀을 그릴 수 있으면 대상 그룹→ASG 선 하나가 인스턴스별 등록선을 대신한다([[layout-and-drawio]]).

## focus (선택 리소스)
- 포커스 노드에 닿는 관계 중 `CORE | REGIONAL | EDGE | GROUP_TARGETS | {in_subnet}`(+ references이거나 포커스가 SG·ENI·EBS면 `AUXILIARY`)만. 다른 서브넷 리소스의 `in_subnet`은 제외.
- 포커스가 서브넷이면 그 안의 리소스, `GROUPS` 종류면 멤버를 더한다. 보이는 노드의 VPC·서브넷 경계를 더한다.
- 최종 관계에서 `SUBNET_KINDS` source의 `in_subnet`은 포함으로 그리므로 뺀다. LB처럼 여러 서브넷에 놓인 리소스의 `in_subnet`은 배치 관계선으로 남는다(`test_view_contract_counts_and_scope`: ALB 포커스에 `in_subnet` 2개).

## overview (전체 구성)
- 보이는 노드: 모든 VPC + `overview_links`의 양 끝 + 수집된(`reference_only`가 아닌) CloudFront 배포.
- 그릴 관계: `overview_links` + `edge_links`. overview에서는 관계를 양 끝 가시성으로 거르지 않는다(배치가 카드에 연결한다).
- `overview_links(graph, nodes)`가 돌려주는 `(edge, from, to)`:
  - VPC → Transit Gateway: `attached_transit_gateway`의 source가 attachment이고, attachment의 `in_vpc`로 VPC를 찾는다.
  - VPC → 피어링 또는 VGW: 피어링·VGW의 `in_vpc`/`peer_vpc` 관계를 VPC 쪽에서 출발하도록 뒤집는다.
  - 허브 → 온프레미스·허브: `REMOTE`(VPN, DX VIF)와 `dx_gateway_association`(DX 게이트웨이 → TGW·VGW)는 그대로.
- `edge_links(graph, nodes, vpc_of)`: `(edge, 배포, 오리진 LB의 VPC, LB)`. 배치는 이 선을 VPC 카드의 LB 아이콘 위로 들인다.
- `HUB_ORDER = {transit_gateway: 0, vpn_gateway: 1, vpc_peering: 2, dx_gateway: 3}`: 허브 띠의 순서.

## 경로 필터 (`routes=`, `route_filter`)
- detail에서만 쓴다. 값은 이 VPC의 LB 또는 대상 그룹의 노드 ID나 `resource_id`.
- 대상 그룹을 고르면 그 그룹과 연결 LB, LB를 고르면 LB와 그 대상 그룹 전부가 경로다. 경로에는 등록 대상, `GROUP_TARGETS` source(ASG·ECS 서비스), 그 멤버가 더해진다.
- 다른 LB·대상 그룹과, 숨긴 경로 때문에만 그려지던 대상(IP 대상, 조회되지 않은 인스턴스, SG·ENI·EBS 참조)은 숨기고 센다. 이 VPC에 놓인 수집 리소스는 남긴다(배치가 경로 밖을 흐리게 표시).
- 요약 `routes`: `resource`, `kind`, `name`, `load_balancers`, `target_groups`, `path`(보이는 경로 노드), `hidden`(필터가 숨긴 노드 수), `hidden_relations`(생략한 관계 수).

## 개수 (`counts`)
- `collected`: `reference_only`가 아닌 노드 수. `references`: 참조 전용 노드 수.
- `visible`: 보이는 노드 수, `visible_references`: 그중 참조 전용 수.
- `hidden = 전체 노드 - visible`. 항상 `visible + hidden == collected + references == 전체`(`test_view_contract_counts_and_scope`).
- `filtered`: 경로 필터가 숨긴 수(필터가 없으면 0). `hidden - 필터 없는 hidden == filtered`(`test_route_filter_narrows_detail_to_one_path`).
- 배치는 이 값을 범위 문구 "표시 N개 / 수집 N개 · 참조 N개 · 숨김 N개"로 그린다.

## 바꿀 때 주의
- 분류 집합에 종류·관계를 넣지 않으면 새 리소스가 보기에서 빠지거나, 전체 구성에 "VPC 외 · 배치 미확인" 카드로 잘못 세어진다(`test_overview_cards_differ_per_sample_and_hide_drawn_groups`).
- 보기 범위 키(`DocumentStore.path`)의 모양을 바꾸면 저장된 편집본이 연결되지 않는다. detail은 옛 키 모양을 유지한다.
- 관련 테스트: `tests/test_graph_report.py::test_view_contract_counts_and_scope`, `::test_route_filter_narrows_detail_to_one_path`, `tests/test_drawio.py::test_overview_draws_the_network_between_vpcs`.

## 관련
- [[graph]] — 관계 이름과 의미.
- [[layout-and-drawio]] — 보기 결과를 좌표로 바꾸는 배치.
- [[http-api]] — 보기 매개변수를 받는 문서 API.
