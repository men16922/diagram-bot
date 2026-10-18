---
title: 여러 계정 통합 보기
summary: combine.py의 combine_snapshots가 계정 × 리전 스냅샷을 parts로 묶는 방식, graph.merge_graphs의 공유 TGW·계정 간 피어링 합치기와 전역 ID, 전체 구성의 계정 프레임, CLI combine과 /api/snapshots/combine, 계정별 CloudFormation.
sources: [src/diagram_bot/combine.py, src/diagram_bot/graph.py, src/diagram_bot/layout/, src/diagram_bot/drawio.py, src/diagram_bot/findings.py, src/diagram_bot/iac/, src/diagram_bot/cli.py, src/diagram_bot/routes/snapshots.py, src/diagram_bot/samples/, src/diagram_bot/static/app.html, tests/test_graph_report.py, tests/test_collector.py, tests/test_server.py, tests/test_iac.py, tests/test_drawio.py]
related: [cloudformation, review-rules, change-review, cli, graph, collection, snapshot-model]
updated: 2026-10-18
---
# 여러 계정 통합 보기

> 수집은 항상 계정 × 리전 하나 단위다. `combine_snapshots`는 그 스냅샷들을 바꾸지 않고 `parts`로 담은 새 스냅샷을 만들고, 그래프는 `build_graph`가 parts마다 만든 뒤 `merge_graphs`로 합친다. 계정 사이 연결은 양쪽 API가 같은 리소스 ID를 보고할 때만 잇는다.

## 핵심
- `src/diagram_bot/combine.py`의 `combine_snapshots(snapshots, title=None)`:
  - 입력 스냅샷에 `parts`가 있으면(이미 합친 것) 그 parts를 펼쳐 넣고, 아니면 `graph`를 뺀 스냅샷 전체를 part 하나로 넣는다.
  - part가 2개 미만이면 `ValueError("계정·리전 스냅샷을 두 개 이상 고르세요.")`, 같은 (계정, 리전)이 두 번이면 `ValueError("... 두 번 들어 있습니다 ...")`.
- 결과 스냅샷: `schema_version: 1`, `raw: {}`, `parts: [...]`(원본 그대로), `coverage`(각 part의 행에 `account_id`·`region`을 덧붙인 것), `metadata`.
- `metadata` 필드: `combined: True`, `accounts`·`regions`(정렬), `account_id`(계정이 하나면 그 ID, 아니면 `"N개 계정"`), `region`(같은 방식 `"N개 리전"`), `partition`(첫 part), `started_at`(최소)·`finished_at`(최대), `status`(모든 part가 `complete`면 `complete`, 아니면 `partial`), `demo`(모두 demo일 때만), `title`(기본 `계정 통합 · <계정들>`), `parts`(part별 `account_id`·`region`·`finished_at`·`title`), `scope`(합집합), `limitations` 3줄(수집 시점 차이, 같은 리소스 ID일 때만 연결, 실제 통신 미확인).

## 그래프 합치기 (`graph.merge_graphs`)
- `build_graph(snapshot)`은 `parts`가 있으면 `merge_graphs([build_graph(part) for part in parts])`를 돌려준다. 노드 ID에 이미 계정·리전이 들어 있어 대부분은 그냥 합쳐진다.
- 같은 노드 ID가 여러 그래프에 있으면 먼저 온 것을 쓰되, 기존 것이 `reference_only`이고 새 것이 수집된 노드면 바꿔 넣는다.
- `SHARED_KINDS = {"transit_gateway", "vpc_peering"}`: 같은 (`kind`, `resource_id`)를 가진 노드 사본들 중 하나만 남긴다. 우선순위는 `attributes.OwnerId == account`(소유 계정의 사본) → 수집된 노드(`reference_only` 아님) → 노드 ID 순. 나머지 사본 ID는 별칭으로 바꿔 엣지의 `source`·`target`을 남는 노드로 돌리고, 관계 ID를 `relation_id`로 다시 계산해 중복 엣지를 없앤다.
  - RAM으로 공유된 TGW: 각 계정의 attachment가 소유 계정(예: Network 계정)의 TGW 노드 하나에 붙는다(`test_combined_accounts_share_one_transit_gateway_and_one_peering`, `multi-account` 샘플).
  - 계정 간 피어링: 양쪽 계정이 같은 `pcx-` ID를 보고하면 피어링 노드 하나가 두 VPC에 `in_vpc`로 연결된다.
- 전역 ID: `build_graph`의 `node()`는 `GLOBAL_KINDS = {"cloudfront_distribution", "dx_gateway"}`이거나 리소스 ID에 `:global/`이 들어간 경우(CloudFront 범위 WAFv2 웹 ACL ARN) 노드 ID의 리전 자리를 `global`로 쓴다. 그래서 같은 계정의 두 리전을 합쳐도 CloudFront 배포·DX 게이트웨이는 하나씩만 남는다(`test_edge_elastic_ips_routing_domains_and_direct_connect_join_on_api_fields`).
- 엣지 정렬: (`source`, `relation`, `target`, `id`), 노드 정렬: `id`.

## 그림 (전체 구성)
- `layout.cloud_label`: 합친 스냅샷이면 `AWS Cloud · 계정 N개 (계정·리전별 수집 통합)`.
- 전체 구성에서 VPC 카드는 계정별로 모아 정렬하고(`vpcs.sort` 키에 계정), 카드 라벨에 `계정 <ID>`(리전이 둘 이상이면 `· 리전`)를 붙인다.
- `layout.network(..., account_of=...)`가 계정마다 카드 묶음 뒤에 `account` 스타일 프레임(`AWS 계정 · <ID>`, `drawio.py`의 `mxgraph.aws4.group_account` 도형, 컨테이너 아님)을 그린다. 한 행에 계정이 섞이지 않는다.
- VPC가 하나도 없는 합친 스냅샷(모든 part에서 `DescribeVpcs`가 거부되었거나 Lambda만 있는 계정)은 `network()` 대신 일반 카드 격자로 그린다(`test_combined_overview_without_vpcs`).
- VPC 상세·초점 보기는 그 VPC(또는 리소스)의 계정·리전으로 `metadata`를 바꿔 그린다(일반 단일 계정 보기와 같음).

## 검토와 비교
- 검토는 합친 그래프 그대로 돌아간다. `coverage` 행의 `account_id`·`region` 덕분에 `findings.waf_collected`가 ALB의 계정·리전별 WAF 수집 완료 여부를 따로 판정한다([[review-rules]]).
- `diff`의 `comparable`은 `metadata.account_id`·`region` 문자열 비교라서 합친 스냅샷끼리는 같은 계정 수·리전 수 표시가 같아야 참이 된다([[change-review]]).

## 사용 경로
- CLI: `diagram-bot combine A.json B.json [...] --output DIR [--title 이름]` → `write_report`로 보고서(`index.html`, `snapshot.json`, `topology.drawio`, `inventory.csv`, `review.md`). `test_cli_combine_writes_one_report_for_several_accounts`.
- `collect`에 `--role-arn`을 여러 개(또는 리전 여러 개) 주면 `<계정>-<리전>` 하위 디렉터리마다 `snapshot.json`이 생기고, 이것을 `combine`에 넘긴다([[cli]]).
- API: `POST /api/snapshots/combine` 본문 `{"ids": [...], "title": "..."}` → 201 `{"id": ...}`. `ValueError`는 422. 앱 왼쪽의 `계정 통합 보기` 버튼이 대화 상자(`combine-dialog`)로 이 API를 부른다.
- 샘플: `samples.multi_account`(`multi-account`)는 Network 계정이 TGW를 소유하고 워크로드 계정에 공유하는 SRA 형태를 part 3개로 합친 것이다.

## CloudFormation
- 합친 스냅샷은 템플릿 하나로 만들 수 없다. `iac.template()`은 `parts`가 있으면 `ValueError`, `iac.templates()`는 part마다 `(f"{account}-{region}", template)`을 돌려준다.
- 다른 계정 소유 TGW는 그 part의 템플릿에서 매개변수(기본값 = TGW ID)로 참조되고, TGW 라우팅 테이블은 소유 계정 템플릿에서만 만든다([[cloudformation]]).
- CLI `export-cfn`은 합친 스냅샷에 `--output`을 요구하고 `<이름>-<계정>-<리전>.json`으로 나눠 쓴다. API는 `?part=<인덱스>`가 필요하다(없거나 범위 밖이면 422).

## 바꿀 때 주의
- 노드 ID의 계정·리전 구성은 합치기의 전제다. 다른 계정 소유 리소스를 새로 공유 대상으로 다루려면 `SHARED_KINDS`에 넣고, 소유 계정 판별에 쓸 `OwnerId` 속성이 있는지 확인한다.
- 전역 서비스를 새로 수집하면 `GLOBAL_KINDS`에 넣어야 리전마다 중복 노드가 생기지 않는다.
- 관련 테스트: `tests/test_graph_report.py`(공유 TGW·피어링, 전역 노드), `tests/test_server.py::test_combine_account_snapshots_into_one_network_view`, `tests/test_collector.py::test_cli_combine_writes_one_report_for_several_accounts`, `tests/test_iac.py::test_shared_transit_gateway_is_a_parameter_and_combined_snapshots_need_a_part`.

## 관련
- [[cloudformation]] — part별 템플릿과 공유 TGW 매개변수.
- [[review-rules]] — 합친 coverage로 계정·리전별 판정.
- [[change-review]] — 합친 스냅샷의 `comparable`.
- [[cli]] — `collect`·`combine`·`export-cfn` 옵션.
- [[graph]] — `build_graph`와 노드 ID 형식.
- [[collection]] — 계정 × 리전 단위 수집과 역할 전환.
- [[snapshot-model]] — `parts`·`metadata` 필드.
