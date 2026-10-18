---
title: 변경 비교 (diff)
summary: diff.py가 두 스냅샷의 그래프를 노드 ID·관계 ID로 맞춰 리소스 추가·삭제, 설정 필드 변경, 관계 추가·삭제를 내는 방식과 CLI diff --fail-on-change, /api/snapshots/{id}/diff/{other}.
sources: [src/diagram_bot/diff.py, src/diagram_bot/graph.py, src/diagram_bot/cli.py, src/diagram_bot/routes/snapshots.py, src/diagram_bot/api.py, tests/test_diff.py]
related: [review-rules, multi-account, cli, graph, http-api]
updated: 2026-10-18
---
# 변경 비교 (diff)

> `src/diagram_bot/diff.py`의 `diff(before, after)`는 같은 계정·리전의 두 스냅샷을 그래프 수준에서 비교한다. 노드는 ID(계정/리전/종류/리소스 키)로, 관계는 안정적인 관계 ID로 맞추고, 조회 시점마다 바뀌는 상태 값은 비교하지 않는다.

## 핵심
- 입력은 스냅샷 딕셔너리 두 개. `with_graph()`가 `graph`가 없으면 `build_graph(snapshot)`로 만들고, 있으면 `upgrade_graph()`로 관계 ID가 없던 옛 그래프에 ID를 붙인다(제자리 수정).
- 노드 매칭 키는 노드 `id`(`{account}/{region 또는 global}/{kind}/{resource_id}`), 관계 매칭 키는 엣지 `id`(`graph.relation_id`: 출발·관계·도착·`qualifiers`의 sha256).
- `reference_only` 노드는 추가·삭제·변경 어디에도 나오지 않는다(참조에는 비교할 설정이 없다).
- 결과에 `comparable`(두 스냅샷의 `metadata.account_id`와 `region`이 같으면 `True`)이 붙는다. 다르면 모든 리소스가 추가·삭제로 보인다.

## 결과 형태
| 키 | 내용 |
| --- | --- |
| `before`, `after` | `meta()`: `account_id`, `region`, `finished_at`, `status` |
| `added`, `removed` | `{id, kind, name, resource_id}` 목록, 노드 ID 정렬 |
| `changed` | 양쪽에 있는 노드 중 설정이 다른 것: 위 네 필드 + `fields: [{field, before, after}]` |
| `relations_added`, `relations_removed` | `{id, relation, evidence, source, target, qualifiers}`. `source`·`target`은 노드 이름(없으면 노드 ID) |
| `summary` | 위 다섯 목록의 개수 |
| `comparable` | 같은 계정·리전 여부 |

## 설정 비교 규칙
- 비교 필드: 양쪽 `attributes` 키의 합집합에서 `VOLATILE`을 뺀 것. 값은 `canonical(value)` = `json.dumps(value, sort_keys=True, default=str)` 문자열로 비교하므로 딕셔너리 키 순서는 무시되지만 목록 순서는 차이로 본다.
- `VOLATILE`(비교 제외): `State`, `TargetHealth`, `runningCount`, `pendingCount`, `events`, `deployments`, `VgwTelemetry`, `VpnTunnels`, `LaunchTime`, `lastStatus`, `healthStatus`, `startedAt`, `createdAt`, `updatedAt`, `ClusterStatus`, `LifeCycleState`, `NumberOfMountTargets`, `SizeInBytes`, `Instances`, `DesiredCapacity`, `Status`, `currentServiceDeployment`, `NodeStatus`, `BrokerIds`, `Estimated`, `CurrentRole`, `Processing`, `UpgradeProcessing`, `AvailableIpAddressCount`.
- 그래프가 계산한 파생 값 `db_role`, `route_class`도 다르면 변경 필드로 추가된다.
- 표시 값 `value(node, field)`: 문자열은 그대로, 그 밖은 `canonical` 문자열. 200자를 넘으면 199자 + `…`로 자른다.
- 대상 헬스 변화는 관계 `details`에만 있고 관계 ID(`qualifiers`는 `port`, `availability_zone`, `destination`)에 들어가지 않으므로 관계 추가·삭제로 나오지 않는다(`test_diff_reports_resources_settings_and_relations_but_not_health`).

## Markdown 출력
`markdown(result)`는 `# 구성 변경 비교` 문서를 만든다.
- 머리: 이전·이후 계정/리전·시각(상태), 다섯 개수 한 줄.
- `comparable`이 거짓이면 "계정 또는 리전이 달라 모든 리소스가 추가·삭제로 표시됩니다." 인용문.
- 어느 쪽이든 `status`가 `partial`이면 "삭제로 보이는 리소스는 권한 문제로 조회되지 않았을 수 있습니다" 인용문.
- 절: `추가된 리소스`, `삭제된 리소스`, `설정이 바뀐 리소스`(필드별 `` `field`: before → after ``), `추가된 관계`, `삭제된 관계`(`source —relation→ target`). 빈 절은 생략.

## CLI와 API
- `diagram-bot diff BEFORE AFTER [--format markdown|json] [--fail-on-change]`: 두 JSON 파일을 읽어 `diff`를 출력한다. `--fail-on-change`이면 `summary` 값 중 하나라도 0이 아니면 종료 코드 4, 아니면 0(드리프트 감지용). `schema_version` 확인은 하지 않는다.
- `GET /api/snapshots/{identifier}/diff/{other}` (`routes/snapshots.py`의 `compare_snapshots`): `other`가 이전, `identifier`가 이후다(`diff(store.get(other), store.get(identifier))`). 경로 순서와 인자 순서가 반대인 점에 주의.
- 라이브러리: `diagram_bot.api.diff_snapshots`(= `diff.diff`).

## 바꿀 때 주의
- 노드 ID 형식이나 `relation_id`의 키 구성을 바꾸면 저장된 스냅샷과 새 스냅샷 사이 비교가 모두 추가·삭제로 바뀐다. `upgrade_graph`는 ID가 없는 엣지만 채운다.
- 조회 시점마다 변하는 새 수집 필드를 추가하면 `VOLATILE`에도 넣어야 드리프트 오탐이 없다(`test_identical_snapshots_have_no_changes_and_other_scope_is_flagged`는 같은 스냅샷만 확인하므로 이런 회귀를 잡지 못한다).
- 종료 코드 4는 `tests/test_diff.py::test_cli_diff_exit_code_detects_drift`가 고정한다.

## 관련
- [[review-rules]] — 한 스냅샷의 아키텍처 검토(변경 비교와 별개).
- [[multi-account]] — 합친 스냅샷의 `account_id`는 "N개 계정" 같은 표시 문자열이라 `comparable` 판정에 그대로 쓰인다.
- [[cli]] — 하위 명령과 종료 코드 전체.
- [[graph]] — 노드 ID와 `relation_id`.
- [[http-api]] — 변경 비교 엔드포인트를 포함한 API 전체.
