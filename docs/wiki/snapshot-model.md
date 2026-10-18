---
title: 스냅샷 데이터 모델
summary: 스냅샷 JSON 계약 — schema_version, metadata(수집·샘플·설계·통합별 필드), coverage 행, raw 키 전체, 그래프 노드·관계 필드와 검증 규칙.
sources: [src/diagram_bot/collector.py, src/diagram_bot/samples/, src/diagram_bot/designer.py, src/diagram_bot/combine.py, src/diagram_bot/store.py, src/diagram_bot/graph.py, src/diagram_bot/report.py, src/diagram_bot/demo.py]
related: [collection, graph, views, samples, storage, architecture]
updated: 2026-10-18
---
# 스냅샷 데이터 모델

> 스냅샷은 `{schema_version, metadata, coverage, raw}`이다. `raw`는 AWS API 응답 원본이고 `graph`는 언제든 `raw`에서 다시 만드는
> 파생 데이터다. 수집·샘플·채팅 설계·계정 통합 모두 같은 형식을 쓴다.

## 핵심
- `schema_version`은 항상 `1`. `store.validate_snapshot`이 다른 값을 거부한다(`지원하지 않는 스냅샷 버전입니다.`). CLI의 `render`와 `export-cfn`도 같은 검사를 한다(`cli.py`; `review`·`diff`는 검사하지 않는다).
- `validate_snapshot`이 확인하는 것: `metadata`의 `account_id`, `region`, `started_at`, `finished_at`, `status`가 문자열, `coverage`는 list, `raw`는 dict. 나머지 필드는 선택이다.
- `graph`는 저장 시점에 다시 만든다. `SnapshotStore.save`는 받은 `graph`를 무시하고 `graph.build_graph(snapshot)`로 덮어쓰며, `report.write_report`도 같다. 읽을 때는 `SnapshotStore.get`이 `graph.upgrade_graph`를 적용한다([[graph]]).
- 스냅샷은 저장 후 바뀌지 않는다. 사용자 편집은 draw.io 문서 쪽(`DocumentStore`)에만 남는다([[storage]]).

## metadata

수집(`collector.collect`)이 쓰는 필드:
- `account_id`, `region`, `profile`, `role_arn`, `partition`(호출자 ARN의 두 번째 필드), `started_at`, `finished_at`(ISO 8601 UTC, `collector.now`).
- `status`: coverage의 모든 행이 `complete`이면 `complete`, 아니면 `partial`.
- `demo`: 수집은 `False`, 샘플·데모는 `True`.
- `scope`: 수집한 리소스 유형 이름 목록(`"VPC"`, `"CloudFront"`, `"DirectConnect"` …). `limitations`: 해석 범위 문장 목록(보고서 `review.md`의 "해석 범위").

샘플(`samples.Sample.snapshot`)이 더하는 필드: `sample`(키, 예: `three-tier`), `sample_version`(= `SAMPLE_VERSION`), `title`, `practices`(권장 사항 문장 목록). `demo`는 `True`([[samples]]).

채팅 설계(`designer.Design.snapshot`)는 샘플 형식 위에 덮어쓴다: `design: True`, `sample`(표준 이름 또는 `"design"`), `title`, `steps`(적용한 단계 기록), `practices`, `limitations`, 브라운필드면 `based_on`(`account_id`, `region`, `collected_at`, `title` 중 있는 값). 설계안의 `account_id`는 빈 설계면 `000000000000`이고, 표준 아키텍처·브라운필드처럼 스냅샷에서 시작하면 그 스냅샷의 계정을 이어받는다(`Design.start_from`).

계정 통합(`combine.combine_snapshots`)의 필드:
- `combined: True`, `accounts`, `regions`(정렬 목록), `account_id`(계정이 하나면 그 ID, 아니면 `"N개 계정"`), `region`(같은 방식으로 `"N개 리전"`).
- `started_at`은 부분들의 최솟값, `finished_at`은 최댓값, `status`는 모두 `complete`일 때만 `complete`, `demo`는 모두 `demo`일 때만 참.
- `metadata.parts`: 부분 요약(`account_id`, `region`, `finished_at`, `title`). `scope`는 합집합.
- 최상위 `parts`에 부분 스냅샷 원본(그래프 제외)을 그대로 두고 `raw`는 `{}`다. 그래프는 부분별로 만들어 `graph.merge_graphs`로 합친다.
- 부분이 2개 미만이거나 같은 (계정, 리전)이 두 번 들어오면 `ValueError`.

## coverage 행

API 호출 하나(페이지 전체)가 한 행이다. `collector.query`와 `collect`의 `call`이 만든다.

| 필드 | 뜻 |
| --- | --- |
| `service`, `operation` | boto3 클라이언트 이름과 메서드 이름 (`ec2`, `describe_vpcs`) |
| `parameters` | 호출 인자 (예: `{"TargetGroupArn": …}`, `{"Scope": "CLOUDFRONT", "Limit": 100}`) |
| `status` | `complete` / `partial`(일부 페이지 성공 후 실패) / `unavailable`(첫 페이지부터 실패) / `not_discovered`(상위 목록이 불완전해 조회하지 못한 범위) |
| `error_code` | `ClientError`의 오류 코드, 그 밖의 예외는 클래스 이름. 잘림은 `AdditionalRoutesAvailable` |
| `permission` | 필요한 IAM 액션 (`collector.permission`) |
| `pages`, `items` | 받은 페이지 수와 행 수 (`describe_instances`는 평탄화한 인스턴스 수) |
| `started_at`, `finished_at` | 호출 시각 |
| `reason` | 사람이 읽는 설명 (`not_discovered`, 경로 1,000개 초과) |

- 통합 스냅샷의 coverage 행에는 `account_id`, `region`이 더 붙는다.
- 샘플·데모의 coverage는 `status: complete`, `pages: 1`, `items`만 가진 합성 행이다. 데모(`demo.demo_snapshot`)는 `describe_instances`를 일부러 `partial`로 둔다.
- UI 표시 이름: `static/js/labels.js`의 `statuses`(`조회 완료`, `부분 조회`, `조회 불가`, `미발견 범위`).

## raw 키

수집이 남기는 키(`collector.OPERATIONS`의 네 번째 값 + 2단계 상세 함수의 반환 키):
- 1단계 목록: `vpcs`, `subnets`, `instances`(Reservations를 평탄화), `load_balancers`, `target_groups`, `internet_gateways`, `nat_gateways`, `route_tables`, `db_instances`, `auto_scaling_groups`, `vpc_endpoints`, `transit_gateways`, `transit_gateway_attachments`, `db_clusters`, `cache_clusters`, `replication_groups`, `cache_subnet_groups`, `msk_clusters`, `lambda_functions`, `efs_file_systems`, `redshift_clusters`, `redshift_subnet_groups`, `vpc_peering_connections`, `vpn_gateways`, `vpn_connections`, `customer_gateways`, `security_groups`, `placement_groups`, `fsx_file_systems`, `elastic_ips`, `transit_gateway_route_tables`, `cloudfront_distributions`, `dx_connections`, `dx_virtual_interfaces`, `dx_gateways`.
- 2단계 상세: `target_health`(`{TargetGroupArn, Descriptions}` 목록), `eks_clusters`(각 클러스터에 `nodegroups`), `msk_nodes`(`ClusterArn` 추가), `opensearch_domains`, `opensearch_nodes`(`DomainName` 추가), `ecs_services`, `ecs_tasks`, `efs_mount_targets`, `sagemaker_endpoints`(`EndpointConfig`, `Models` 결합), `sagemaker_clusters`, `transit_gateway_route_table_associations`, `transit_gateway_route_table_propagations`, `transit_gateway_routes`(셋 다 `TransitGatewayRouteTableId` 추가), `waf_web_acls`(`Scope`, REGIONAL은 `ResourceArns` 추가), `dx_gateway_associations`.
- 중간 목록이라 저장 전에 지우는 키: `eks_cluster_names`, `opensearch_domain_names`, `ecs_cluster_arns`, `sagemaker_endpoint_names`, `sagemaker_cluster_names`.

샘플(`Sample.__init__`)은 처음부터 39개 키를 빈 목록으로 만든다(`target_health`, `msk_nodes`, `eks_clusters`, `opensearch_domains` 등 상세 키 포함, 수집의 1단계 이름 목록 키는 없음). 엣지·EIP·TGW 라우팅·Direct Connect 키(`elastic_ips`, `waf_web_acls`, `cloudfront_distributions`, `transit_gateway_route_tables`, `transit_gateway_route_table_associations`, `transit_gateway_route_table_propagations`, `transit_gateway_routes`, `dx_gateways`, `dx_gateway_associations`, `dx_connections`, `dx_virtual_interfaces`)는 해당 도우미가 `raw.setdefault`로 처음 쓸 때만 생긴다. 그래서 그 기능을 쓰지 않는 샘플에는 해당 coverage 행도 없다.

그래프 코드는 모든 키를 `raw.get(key, [])`로 읽으므로 키가 없는 오래된 스냅샷도 그대로 그린다.

## graph

`build_graph`의 반환값 `{nodes, edges}`. 노드는 `id`, 관계는 `(source, relation, target, id)` 순으로 정렬해 결정적이다.

노드 필드:
- `id`: `"{account}/{scope}/{kind}/{resource_id}"`. `scope`는 리전이고, `graph.GLOBAL_KINDS`(`cloudfront_distribution`, `dx_gateway`)이거나 `resource_id`에 `:global/`이 들어 있으면(CloudFront용 WAF ARN) `global`.
- `resource_id`, `kind`, `name`(Name 태그 → `DisplayName` → 이름 계열 필드 → `resource_id`), `account`, `region`(= scope), `attributes`(API 응답 원본 + 파생 값), `reference_only`(다른 응답에서 ID만 본 노드), `source`(근거 API 이름).
- 선택적 파생 필드: 서브넷의 `route_class`·`route_table`, DB의 `db_role`, `elastic_ips`, `tgw_route_table`(attachment), `route_tables`(TGW), `origins`·`waf`·`waf_acl`(CloudFront·ALB), `virtual_interfaces`(DX 연결), 속성 패널용 `properties`(`[라벨, 값]` 목록).

관계 필드:
- `id`(`rel-` + SHA-256 앞 20자, `graph.relation_id`), `source`, `target`, `relation`, `evidence`(예: `DescribeRouteTables.Routes[].NatGatewayId`), `qualifiers`(`port`, `availability_zone`, 경로성 관계는 `destination`), `details`(상태·터널 수 등 표시용), `inferred: False`(추정 관계는 만들지 않는다).

노드 종류·관계 이름·ID 규칙의 전체 목록은 [[graph]]에 있다.

## 바꿀 때 주의
- 형식을 바꾸면 `schema_version`과 `validate_snapshot`, `upgrade_graph`(저장된 그래프 호환), `cli.py`의 버전 검사(`render`, `export_cfn`)를 함께 고친다.
- `raw` 키를 추가하면 `Sample.__init__` 또는 `setdefault` 도우미, `Sample.snapshot`의 coverage 목록, 이 페이지의 목록을 함께 고친다([[howto-add-collected-service]]).
- 관련 테스트: `tests/test_collector.py`(raw·coverage 모양), `tests/test_server.py`(샘플 버전 교체), `tests/test_graph_report.py::test_combined_accounts_share_one_transit_gateway_and_one_peering`.

## 관련
- [[collection]] — coverage와 raw를 만드는 수집기.
- [[graph]] — raw에서 노드·관계를 만드는 규칙.
- [[samples]] — 같은 형식의 합성 스냅샷.
- [[storage]] — 스냅샷 파일 저장과 캐시.
