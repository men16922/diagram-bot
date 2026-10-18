---
title: 저장소 (스냅샷·편집본·사내 표준)
summary: store.py의 SnapshotStore(검증·그래프 재생성·캐시·목록·샘플 시드), DocumentStore(보기 범위 키, 리비전 낙관적 잠금, 20개 이력, replace 의미), StandardStore(admin이 채택한 사내 표준), 데이터 디렉터리 위치, DB·S3로 바꿀 때 구현할 메서드.
sources: [src/diagram_bot/store.py, src/diagram_bot/server.py, src/diagram_bot/routes/documents.py, src/diagram_bot/routes/snapshots.py, src/diagram_bot/routes/standards.py, src/diagram_bot/routes/__init__.py, docs/OPERATIONS.md, tests/test_server.py, tests/test_standards.py]
related: [http-api, web-app, team-mode-security, chat-agent, snapshot-model, views, graph, drawio-format-and-alignment]
updated: 2026-10-19
---
# 저장소 (스냅샷·편집본·사내 표준)

> 데이터는 디렉터리 하나 아래의 JSON 파일이다. 스냅샷은 저장 뒤 바뀌지 않고, draw.io 편집본은 스냅샷 × 보기 범위마다 리비전을 갖는다. 낙관적 잠금으로 다른 창의 저장을 덮어쓰지 않고, 밀려난 리비전은 이력에 보관한다.

## 핵심
- 위치: `store.data_path()` = `$XDG_DATA_HOME/diagram-bot`, 없으면 `~/.local/share/diagram-bot`. `serve --data-dir`가 `create_app(directory=...)`로 바꾼다.
- 그 아래 `snapshots/<id>.json`, `documents/<sha256>.json`과 `documents/<sha256>.history.json`, `standards/<이름>.json`(아래 StandardStore), 그리고 `audit.log`(`create_app`이 `AuditLog(directory / "audit.log")`로 만든다, [[team-mode-security]]).
- 쓰기는 `atomic_json(path, value)` → `fileio.atomic_write`: 같은 디렉터리에 쓰는 사람마다 따로 만든 임시 파일(`tempfile.mkstemp`)에 쓰고 `os.replace`한다(`ensure_ascii=False`, `default=str`). 동시에 같은 파일을 써도 섞이지 않고, 파일은 소유자 전용(0600)으로 만들어진다. 팀 모드 사용자 파일도 같은 함수를 쓴다.
- `SnapshotStore.list()`는 읽을 수 없는 `*.json` 하나를 건너뛰고 로그로 남긴다(목록 전체가 500이 되지 않는다). 지워진 파일의 요약 캐시도 버린다. `get()`은 캐시에서 한 번 읽은 값을 돌려준다.
- 오류 타입 두 개가 HTTP로 이어진다: `NotFound(LookupError)` → 404, `Conflict(RuntimeError)` → 409(`routes.install_error_handlers`). 메시지는 사용자에게 그대로 보인다.

## SnapshotStore
- `save(snapshot, identifier=None)`:
  1. `validate_snapshot`: `schema_version == 1`, `metadata`의 `account_id`·`region`·`started_at`·`finished_at`·`status`가 문자열, `coverage`는 list, `raw`는 dict. 어기면 `ValueError`.
  2. `graph`를 `graph.build_graph(snapshot)`로 다시 만든다(가져온 스냅샷의 그래프는 믿지 않음).
  3. ID 기본값은 `uuid4().hex`, `snapshots/<id>.json`에 원자적으로 쓰고 ID를 돌려준다.
- `get(identifier)`: ID가 `[a-zA-Z0-9-]{1,64}`가 아니거나 파일이 없으면 `NotFound("스냅샷을 찾을 수 없습니다.")`. `(id, st_mtime_ns)` 키로 파싱 결과를 캐시하고 `graph.upgrade_graph`(관계 ID가 없던 옛 그래프 보정)를 적용한다. 새로 넣기 전에 같은 ID의 옛 항목과 가장 오래된 항목을 지워 캐시는 최대 8개다. 돌려준 객체는 공유되므로 호출자가 바꾸면 안 된다.
- `list()`: `snapshots/*.json` 전부의 `{id, metadata, resource_count(graph 노드 수)}`. 행도 `(stem, mtime)`으로 캐시하고 `metadata.finished_at` 내림차순으로 정렬한다.
- `seed(demo, samples, version)`: `demo.json`이 없을 때만 데모를 저장하고, 번들 샘플은 `sample-<key>.json`의 `metadata.sample_version`이 `version`(`samples.SAMPLE_VERSION`)과 다르거나 읽을 수 없으면 다시 만든다. `create_app`이 시작할 때 부른다.
- 스냅샷은 덮어쓰지 않는다. 수집·가져오기·통합·채팅 설계는 모두 새 ID로 저장된다(시드만 고정 ID `demo`, `sample-<key>`).

## DocumentStore
- 범위(`scope`)는 `(view kind, vpc, resource, references, routes)` 튜플이다. `routes/documents.view_scope`가 `views.view_graph`로 정규화해 만든다.
- `path(identifier, scope)`: 키 문자열의 SHA-256 hex가 파일 이름이다.
  - `detail` 보기: `f"{id}|{vpc}|{references}"` — 보기 개념이 생기기 전의 키를 그대로 써서 예전 편집본이 계속 붙는다(`test_legacy_snapshot_and_detail_document_stay_usable`).
  - 그 밖: `f"{id}|{view}|{vpc}|{resource}|{references}"`.
  - 경로 필터가 있으면 `|routes=<노드 ID>`를 덧붙여 별도 문서가 된다(`test_route_filtered_detail_is_its_own_document`).
- `read(identifier, scope)` → `(저장본 또는 None, 이력 목록)`. 저장본은 `{xml, revision, saved_at}`.
- 이력 항목은 `{revision, saved_at, reason, xml}`이고 `HISTORY = 20`개까지 최근 것만 남긴다. API 응답에는 `xml`을 빼고 나간다([[http-api]]).

## StandardStore
- admin이 `POST /api/xml/adopt`에 `make_default: true`로 채택한 사내 표준을 `standards/<이름>.json` 하나씩에 둔다(`routes.standards.remember`). 이름은 `designer.slug` 형식(`[a-z0-9-]{1,40}`)만 받고, 아니면 `NotFound`다.
- 기록: `name`, `title`, `description`(어시스턴트 목록 설명), `created_at`, `default`, `adopted_by`(채택한 사용자), `snapshot_id`(`save: true`면 저장된 스냅샷 ID), `spec`(`DrawioSpec.to_dict()`), `snapshot`(가져온 스냅샷 전체; `Design().apply_standard`가 이것을 복사해 시작한다). 스냅샷을 함께 두므로 표준은 스냅샷 파일과 따로 살아남는다.
- `save(record)`: 원자적 쓰기. `default`인 기록을 쓰면 다른 기록의 `default`를 먼저 끈다(기본 규격은 하나).
- `list()`: `created_at` 순. 읽을 수 없거나 `name`이 파일 이름과 다르거나 `spec`·`snapshot`이 객체가 아닌 파일은 로그만 남기고 건너뛴다.
- `delete(name)`: 파일을 지우고 기록을 돌려준다(읽을 수 없는 파일도 지운다). 없으면 `NotFound`(404).
- `create_app`이 시드 직후 `routes.standards.restore`로 모든 기록을 `designer.COMPANY_STANDARDS`·`drawio_spec.COMPANY_SPECS`에 다시 등록하고 `default` 기록의 규격을 프로세스 기본 규격으로 둔다. 규격은 `DrawioSpec(**spec)`으로 다시 검증되고, 실패한 기록은 건너뛴다.

## replace() 의미
`replace(identifier, scope, revision, xml=None, reason="편집 저장", source_revision=None, merge=None)`는 잠금 안에서 다음을 한다.
1. 저장본(없으면 `{"revision": 0}`)의 `revision`이 인자와 다르면 `Conflict("다른 창에서 편집본을 저장했습니다. 새로고침 후 다시 편집하세요.")`.
2. `source_revision`이 있으면(복원) 이력에서 그 리비전을 찾아 `xml`로 쓰고 이유를 `v<n> 복원 전`으로 바꾼다. 없으면 `NotFound("보관된 편집본을 찾을 수 없습니다.")`.
3. 그렇지 않고 `merge`가 없으며 XML이 저장본과 같으면 아무것도 바꾸지 않고 그대로 돌려준다(리비전 증가 없음).
4. `merge`가 있고 저장본에 XML이 있으면 `xml = merge(저장본 XML, 새 XML)`.
5. 저장본에 XML이 있으면 그것을 `reason`과 함께 이력에 보관하고, `{xml, revision: 이전 + 1, saved_at: now()}`를 쓴다.

세 엔드포인트의 사용:
- 저장 `PUT .../document`: `replace(..., document.revision, document.xml)` — 이유 `편집 저장`.
- 재배치 `POST .../relayout`: 새로 생성한 XML, `reason="자동 재배치 전"`, `merge=drawio.carry_annotations`(사용자가 더한 메모·도형·선을 옮김). merge가 있으므로 같은 XML이어도 새 리비전이 된다.
- 복원 `POST .../restore`: `source_revision=from_revision`.
- `GET .../document?reset=true`는 저장하지 않고 생성본만 돌려준다(리비전은 저장본의 것). 프런트엔드는 현재 이 플래그를 쓰지 않는다([[web-app]]의 `documentUrl`에 인자만 있음).

## DB·S3로 바꿀 때
모듈 docstring대로 웹 계층은 아래 메서드만 쓴다. 같은 시그니처의 클래스를 `routes.Context(snapshots=..., documents=...)`에 넣으면 된다([[http-api]]의 "다른 FastAPI 앱에 붙이기").
- 스냅샷: `save(snapshot, identifier=None) -> id`, `get(identifier) -> dict`(없으면 `NotFound`), `list() -> [{id, metadata, resource_count}]`. `create_app`을 그대로 쓰면 `seed(demo, samples, version)`도 필요하다.
- 문서: `read(identifier, scope) -> (document | None, history)`, `replace(identifier, scope, revision, xml=None, reason=..., source_revision=None, merge=None) -> (document, history)`.
- 지킬 불변식:
  - `save`는 검증하고 그래프를 다시 만든다(`validate_snapshot`, `build_graph`를 재사용).
  - `replace`의 리비전 비교와 쓰기는 원자적이어야 한다. DB면 `WHERE revision = :expected` 조건부 갱신, S3면 조건부 쓰기 같은 수단이 필요하다. 지금 구현은 프로세스 안 `threading.Lock`만 쓰므로 여러 프로세스가 같은 디렉터리를 쓰면 보호되지 않는다.
  - 범위 키 규칙(특히 `detail`의 옛 키)을 그대로 지켜야 기존 편집본이 이어진다.
  - 이력은 최근 20개, 동일 XML 저장은 무변경, 복원·재배치는 현재본을 먼저 보관.
- 사내 표준: `save(record)`, `list()`, `delete(name)`(없으면 `NotFound`). `routes.Context(standards=None)`이면 채택한 표준은 그 프로세스에만 있다.
- 감사 로그는 `AuditLog` 대신 `write(user, action, target=None, result="ok", client=None, **extra)`와 `tail(limit)`을 가진 객체로 바꿀 수 있다.

## 바꿀 때 주의
- 키 형식, `HISTORY`, `Conflict` 메시지를 바꾸면 `tests/test_server.py`의 `test_document_persistence_conflict_and_scope`, `test_views_are_isolated_documents`, `test_relayout_archives_edit_and_keeps_notes_then_restore`, `test_legacy_snapshot_and_detail_document_stay_usable`을 함께 본다.
- 샘플 구조를 바꾸면 `samples.SAMPLE_VERSION`을 올려야 기존 데이터 디렉터리의 샘플이 다시 만들어진다(`test_outdated_samples_are_regenerated`).
- 보관 정책·백업 안내는 `docs/OPERATIONS.md`의 "데이터 보관" 절.

## 관련
- [[http-api]] — 저장소를 부르는 snapshots·documents 엔드포인트.
- [[web-app]] — 리비전·409를 다루는 화면 흐름.
- [[team-mode-security]] — 같은 디렉터리의 `audit.log`.
- [[drawio-format-and-alignment]] — `standards/`에 저장되는 사내 규격과 템플릿 채택.
- [[chat-agent]] — 설계 단계마다 저장되는 스냅샷.
- [[snapshot-model]] — 저장되는 스냅샷 JSON의 형식.
- [[views]] — 문서 범위 키를 정하는 `view_graph`.
- [[graph]] — `save`가 다시 만드는 그래프와 `upgrade_graph`.
