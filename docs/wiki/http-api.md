---
title: HTTP API 레퍼런스
summary: create_app이 조립하는 모든 HTTP 엔드포인트(페이지·정적 파일·session·snapshots·documents·agent·aws 라우터)의 요청 모델, 응답, 상태 코드, 역할, 감사 기록.
sources: [src/diagram_bot/server.py, src/diagram_bot/routes/__init__.py, src/diagram_bot/routes/session.py, src/diagram_bot/routes/snapshots.py, src/diagram_bot/routes/documents.py, src/diagram_bot/routes/agent.py, src/diagram_bot/routes/aws.py, src/diagram_bot/auth.py, src/diagram_bot/egress.py, tests/test_server.py, tests/test_team.py, tests/test_agent.py]
related: [team-mode-security, storage, web-app, chat-agent, browser-egress, aws-access-and-sso, views, cloudformation, porting-guide]
updated: 2026-10-19
---
# HTTP API 레퍼런스

> `server.create_app()`이 `routes.Context` 하나 위에 라우터 다섯 개(`session`, `snapshots`, `documents`, `agent`, `aws`)와 페이지·편집기·정적 파일을 묶는다. 모든 응답 본문은 JSON이고, 오류는 `{"detail": "사용자에게 보일 한국어 메시지"}` 형태다.

## 핵심
- 공통 규칙은 `server.create_app`의 `protect_mutations` 미들웨어에 있다(자세한 보안 의미는 [[team-mode-security]]).
  - **변경 요청**(`POST`/`PUT`/`DELETE`/`PATCH`)은 `X-Diagram-Bot` 헤더에 페이지 토큰이 있어야 한다. 토큰은 프로세스마다 `secrets.token_urlsafe(32)`로 한 번 만들고 `GET /api/bootstrap`이 돌려준다. 틀리면 403 `앱 세션이 만료되었습니다. 새로고침하세요.` 예외는 `POST /api/login`뿐.
  - `Origin` 헤더가 있고 그 netloc이 `Host`와 다르면 403 `허용되지 않은 요청 출처`.
  - 변경 요청 본문이 15 MiB(`server.MAX_BODY`)를 넘으면 413 `파일은 최대 15 MiB입니다.` `Content-Length`로 먼저 거부하고, 없으면 상한까지만 읽는다.
  - `errors.InputError`(사용자가 고칠 입력: 스냅샷 형식, 통합할 수 없는 스냅샷 조합)는 `install_error_handlers`가 422로 바꾼다.
  - 팀 모드(`context.auth`가 있음)에서 `PUBLIC`(`/login`, `/api/login`, `/auth/oidc/start`, `/auth/oidc/callback`) 밖의 경로는 세션 쿠키가 필요하다. 없으면 `/api/*`는 401, 그 밖(페이지·`/static`·`/editor` 포함)은 303 → `/login`.
  - 역할 검사는 `auth.allowed(role, method, path)`: `GET`/`HEAD`와 `/api/logout`은 모두 허용, `/api/collections`·`/api/sso`로 시작하는 변경은 `admin`만, 나머지 변경은 `editor`·`admin`. 거부는 403과 감사 기록(`result="denied"`).
- 로컬 모드(팀 모드 아님)에서는 `request.state.user = "local"`이고 `routes.role()`은 `admin`을 돌려준다.
- 예외 처리기(`routes.install_error_handlers`): `store.NotFound` → 404, `store.Conflict` → 409, `errors.InputError` → 422(draw.io XML·규격 입력 오류 포함), 메시지는 예외 문자열 그대로. pydantic 검증 실패는 FastAPI 기본 422.
- `FastAPI(docs_url=None, redoc_url=None)`이라 `/docs`·`/redoc`는 없다.

## 감사 기록
- 미들웨어는 응답이 나온 뒤 모든 변경 요청을 `audit.write(user, "METHOD /path", target=쿼리 문자열 또는 None, result=상태 코드, client=주소)`로 남긴다.
- 제외: `/api/login`, `/api/logout`(각 핸들러가 직접 기록), `/api/relay/*`(AWS 요청 하나마다 오므로 그 요청을 일으킨 수집·채팅 턴이 기록된다).
- 미들웨어가 401·403(토큰·Origin)·413으로 먼저 끊은 요청은 기록되지 않는다. 역할 거부만 `denied`로 기록된다.
- 핸들러가 따로 남기는 항목: `login`(`ok`/`failed`/`throttled`, OIDC는 `method="oidc"`), `logout`, `agent snapshot`, `collection`, `sso login started`, `sso login <status>`, `sso role selected`. 형식은 [[team-mode-security]].

## 페이지와 정적 파일 (`server.py`)

| 메서드·경로 | 응답 | 비고 |
| --- | --- | --- |
| `GET /` | `static/app.html` | `Cache-Control: no-store`, `X-Frame-Options: DENY` |
| `GET /api/bootstrap` | `{token, editor_ready, editor_version, profiles, version, team, egress, user: {name, role}}` | `profiles`는 `boto3.Session().available_profiles`(실패 시 `[]`), `editor_ready`는 `<editor>/js/app.min.js` 존재 여부 |
| `GET /editor/js/app.min.js` | draw.io `app.min.js` + `\n` + `static/editor-bridge.js` | 없으면 503 `diagram-bot setup-editor를 먼저 실행하세요.`, `Cache-Control: public, max-age=3600` |
| `/static/*` | 패키지 `diagram_bot/static` (`StaticFiles`) | `app.js`, `app.css`, `js/*.js` |
| `/editor/*` | 고정 버전 draw.io 디렉터리 (`StaticFiles(html=True)`) | `create_app` 시점에 디렉터리가 있을 때만 마운트 |

`/editor/js/app.min.js` 라우트가 `/editor` 마운트보다 먼저 등록되어 편집기 스크립트 대신 브리지를 덧붙인 판이 나간다([[web-app]]).

## session 라우터 (`routes/session.py`)

| 메서드·경로 | 요청 | 응답·상태 |
| --- | --- | --- |
| `GET /login` | — | 팀 모드가 아니면 303 → `/`. 로그인 HTML(`oidc`가 있으면 SSO 버튼, 로컬 사용자가 없으면 폼 숨김) |
| `GET /auth/oidc/start` | — | OIDC 미설정 404, 제공자 연결 실패 502(로그인 페이지), 성공 303 → 인가 URL + `diagram_bot_oidc` 쿠키 |
| `GET /auth/oidc/callback` | `code`, `state`, `error` 쿼리 | 실패 403(로그인 페이지, 감사 `failed`), 성공 200 HTML(meta refresh → `/`) + 세션 쿠키 |
| `POST /api/login` | `LoginRequest{name: 1–64자, password: 1–256자}` | 팀 모드 아님 404, 차단 429, 실패 401, 성공 200 `{"name"}` + 세션 쿠키 |
| `POST /api/logout` | — | `{"ok": true}`, 쿠키 삭제. 페이지 토큰 필요 |
| `GET /api/audit?limit=200` | `limit`은 1–1000으로 자름 | 최신 항목부터 목록. 팀 모드에서 `admin`이 아니면 403 |

## snapshots 라우터 (`routes/snapshots.py`)

| 메서드·경로 | 요청 | 응답·상태 |
| --- | --- | --- |
| `GET /api/snapshots` | — | `[{id, metadata, resource_count}]`, `finished_at` 내림차순 |
| `GET /api/snapshots/{id}` | — | 스냅샷 전체(`metadata`, `coverage`, `raw`, `graph`). 없거나 ID 형식이 틀리면 404 |
| `GET /api/snapshots/{id}/findings` | — | `{summary, findings}` (`findings.review`) |
| `GET /api/snapshots/{id}/cloudformation` | `part: int` (합친 스냅샷이면 필수) | `application/json` 첨부, 파일 이름 `diagram-bot-cfn[-<sample>]-<account>-<region>.json`. 합친 스냅샷에 `part`가 없거나 범위 밖이면 422 |
| `GET /api/snapshots/{id}/diff/{other}` | — | `diff(other, id)`: `other`가 이전, `id`가 이후 |
| `POST /api/snapshots` | 스냅샷 JSON(`dict`) | 201 `{"id"}`. 형식 오류(`ValueError`·`KeyError`·`TypeError`·`AttributeError`)는 422 |
| `POST /api/snapshots/combine` | `CombineRequest{ids: list[str], title: str \| None}` | 201 `{"id"}`. `combine_snapshots`의 `ValueError`는 422, 없는 ID는 404 |

가져온 스냅샷의 `graph`는 버리고 `raw`에서 다시 만든다([[storage]]).

## documents 라우터 (`routes/documents.py`)

문서 네 엔드포인트 모두 보기 쿼리를 받는다: `view`(`overview`·`detail`·`focus`), `vpc`, `resource`, `references: bool`, `routes`(상세 보기의 로드 밸런서·대상 그룹 노드 ID), `spec`(`default`·`clean`·`enterprise`·`enterprise-clean`·`mxcell` 프리셋 이름만; 파일 경로·XML은 422, `drawio_spec.request_spec`, [[drawio-format-and-alignment]]). `views.view_graph`가 `ValueError`를 내면 422. 정해진 `(kind, vpc, resource, references, routes)`가 문서 키가 된다.

| 메서드·경로 | 본문 | 동작 |
| --- | --- | --- |
| `GET /api/snapshots/{id}/document` | (`reset: bool`, `spec: str?` 쿼리) | `spec`이 있으면 그 규격의 내보내기 미리보기(`revision: null`, `preview: true`, 저장 불가: PUT은 422). 아니면 저장본이 있고 `reset`이 없으면 저장본, 아니면 새로 생성(`revision`은 저장본의 것 또는 0, `saved_at: null`) |
| `PUT /api/snapshots/{id}/document` | `DocumentRequest{xml: ≤10 MiB, revision: ≥0}` | `drawio.validate_document` 실패(압축 페이지 포함) 422, 리비전 불일치 409, 같은 XML이면 변경 없음 |
| `POST /api/snapshots/{id}/document/relayout` | `RevisionRequest{revision}` (`spec: str?` 쿼리) | 다시 생성하고 `carry_annotations`로 사용자 메모를 옮김, 이전 편집본은 `자동 재배치 전`으로 보관 |
| `POST /api/snapshots/{id}/document/restore` | `RestoreRequest{revision, from_revision}` | 보관된 리비전을 복원(`v<n> 복원 전`으로 현재본 보관), 없으면 404 |
| `POST /api/xml/align` | `XmlAlignRequest{xml: ≤10 MiB, mode: Literal["preserve", "rebuild"], spec: 프리셋 이름 \| 객체 \| None, reparent, route_edges, normalize_styles}` | 모든 페이지를 정렬해 `{xml, before, after, pages: [{name, status, before, after, kept_wrappers}]}` 반환. 나빠지는 페이지는 `status: "reverted"`로 원래 배치 유지([[drawio-format-and-alignment]]) |
| `POST /api/xml/import` | `XmlImportRequest{xml: ≤10 MiB, title: ≤200자?, region: REGION 형식, save=True}` | 모든 페이지를 `Snapshot`으로 가져와 저장하고 `{id, snapshot}` 반환. `snapshot.metadata.import`에 셀 ID → 리소스 ID 맵, 페이지, 미대응 셀, 합성한 것 |
| `POST /api/xml/adopt` | `XmlAdoptRequest{xml: ≤10 MiB(XML 본문만, 경로 불가), name: ≤60자, title: ≤200자?, region: REGION 형식, make_default=False, save=True}` | 회사 `drawio.xml`에서 `DrawioSpec` 추출, 정렬, 가져오기 결과를 이 요청에만 돌려줌: `{id, name, default, spec, aligned_xml, standardized_xml, before, after}`. `make_default=true`는 admin만(아니면 403): 프로세스 기본 규격 설정 + `Design` 표준 등록, 기본 표준 이름과 겹치면 422 |

XML 입력은 모두 `drawio_spec.read_document`를 지난다(크기, defusedxml, 압축 페이지 해제, 셀 10,000개, 좌표 유한성, 부모 순환·깊이). 실패는 422. 감사 로그는 미들웨어가 변경 요청마다 한 줄 쓴다(라우트가 따로 쓰지 않음).

응답(`describe`)은 `{xml, revision, saved_at, view, counts, visible, routes, history: [{revision, saved_at, reason}]}`(미리보기는 `preview: true`도). `routes`에서는 `path`를 뺀다. 리비전·이력 규칙은 [[storage]].

## agent 라우터 (`routes/agent.py`)

| 메서드·경로 | 요청 | 응답·상태 |
| --- | --- | --- |
| `GET /api/agent` | — | `{ready, models: [{key, label, model_id}], default, fallback}`. `ready`는 `strands` import 가능 여부 |
| `POST /api/agent/chat` | `ChatRequest{message: 1–4000자, model: ≤64자?, snapshot: ≤64자?}` | `Chat.ask` 결과. 모르는 모델 422, strands 미설치 503, 모델·도구 실패 502(`chat_error`) |
| `POST /api/agent/chat/stream` | 같은 `ChatRequest` | `application/x-ndjson` 스트림 (아래) |
| `POST /api/agent/reset` | — | 사용자 대화 삭제, `{"reset": true}` |

- 대화는 `context.chats[user]`에 사용자별로 하나. 모델을 바꾸면 새로 만든다. `snapshot`은 매 턴 `workspace.current_snapshot`이 된다.
- 팀 모드에서 수집 도구는 `admin`만(`allow_collection`). 설계 도구는 `allowed()`에 따라 `editor`부터.
- 스트림 한 줄은 JSON 객체 하나다. `type`별로:
  - `text` `{text}` — 모델 텍스트 조각
  - `tool` `{name, status}` — `status`는 `start`, `success`, `error`
  - `model` `{model, model_label, fallback}` — 모델 호출마다
  - `snapshot` `{id}` — 도구가 스냅샷을 저장한 직후
  - 마지막 줄은 `done` `{reply, snapshots, tools_used, model, model_label, fallback, usage}` 또는 `error` `{detail}`.
- 422·503은 스트림 시작 전에 일반 JSON 오류로 온다. 턴 실패는 HTTP 200 스트림의 `error` 줄이다.
- 결과 의미와 모델 대체는 [[chat-agent]].

## aws 라우터 (`routes/aws.py`)

| 메서드·경로 | 요청 | 응답·상태 |
| --- | --- | --- |
| `GET /api/identity` | `profile` 쿼리 | `{account, arn, profile}` (STS `GetCallerIdentity`). 팀 모드 `admin` 외 403, 실패 422 |
| `POST /api/collections` | `CollectionRequest` | 202 작업 `{id, status: "queued", started_at}`. 이미 진행 중이면 409 |
| `GET /api/collections/{id}` | — | 작업. 없으면 404(재시작하면 초기화) |
| `GET /api/sso/defaults` | — | `[{name, start_url, region}]` (`AwsAccess.start_urls`) |
| `POST /api/sso/login` | `SsoStartRequest{start_url: 10–512자, region}` | `{id, status: "pending", user_code, verification_uri, verification_uri_complete, expires_in, interval}`. `SsoError` 422 |
| `GET /api/sso/login/{id}` | — | `{id, status, interval[, accounts, expires_in]}`. `SsoError` 404, AWS 오류 502 |
| `GET /api/sso/login/{id}/roles` | `account_id` 쿼리 | `{"roles": [...]}`. `SsoError` 409, AWS 오류 502 |
| `POST /api/sso/login/{id}/use` | `SsoUseRequest{account_id: 12자리, role}` | `{account, role}`. 그 사용자의 채팅 대화를 버린다. 미승인 422 |
| `POST /api/sso/login/{id}/logout` | — | `{"logged_out": ...}` |
| `GET /api/relay/next` | `tab` 쿼리(`^[\w-]*$`, ≤64) | `{"requests": [{id, url, method, headers, body}]}` (비동기 롱 폴링, 기본 20초; 열린 폴이 탭의 심장 박동) |
| `POST /api/relay/{id}` | `RelayAnswer{status: 0–599, headers, body: ≤12 MiB base64?, error: ≤1000자?}` | `{"ok": true}`. 끝났거나 남의 요청이면 404 |

- `CollectionRequest`: `profile`(≤128), `region`(패턴 `^[a-z]{2}(?:-[a-z0-9]+)+-\d+$`), `expected_account`(12자리, 필수), `role_arn`(IAM 역할 ARN 패턴), `external_id`(2–1224자), `sso_login`(`^[\w-]+$`), `sso_role`.
- 수집 작업은 기본 `ThreadPoolExecutor(max_workers=1)`에서 돈다. 작업 상태는 `queued` → `running` → 스냅샷의 `metadata.status`(예: `complete`, `partial`) 또는 `failed`(`error`), 끝나면 `snapshot_id`, `finished_at`. 끝날 때 `collection` 감사 항목(`target`, `snapshot`, `via`)이 남는다.
- `/api/relay/*`는 `--aws-egress browser`일 때만 동작하고 아니면 404. 브라우저 쪽 반복은 `static/js/relay.js`([[web-app]], [[browser-egress]]).

## 다른 FastAPI 앱에 붙이기
`routes/__init__.py` docstring: `routes.Context(snapshots, documents, aws, audit, ...)`를 만들고 필요한 라우터만 `app.include_router(snapshots.router(context))`처럼 붙인다. 조건은 두 가지다. 자체 인증 미들웨어가 `request.state.user`를 넣어야 하고(팀 모드라면 `request.state.role`도), `install_error_handlers(app)`를 호출해야 한다. `aws.router(context)`가 돌려주는 라우터의 `executor`는 앱 종료 때 직접 `shutdown`한다(`create_app`의 `lifespan` 참고). 페이지 토큰·Origin·본문 제한은 `create_app`의 미들웨어에 있으므로 붙이는 쪽이 따로 구현해야 한다.

## 바꿀 때 주의
- 새 변경 엔드포인트는 `allowed()`의 경로 접두사 규칙에 자동으로 걸린다. `admin` 전용이면 `/api/collections`·`/api/sso` 아래에 두거나 핸들러에서 `role(request)`를 검사한다(`/api/identity`, `/api/audit` 예).
- 응답 형태를 바꾸면 `static/app.js`와 `tests/test_server.py`, `tests/test_team.py`, `tests/test_agent.py`(스트림 이벤트 순서)를 같이 고친다.

## 관련
- [[team-mode-security]] — 미들웨어·쿠키·역할·감사의 보안 근거.
- [[storage]] — 스냅샷·문서 저장과 리비전 충돌.
- [[web-app]] — 이 API를 부르는 프런트엔드.
- [[chat-agent]] — 채팅 턴과 스트림 이벤트를 만드는 쪽.
- [[browser-egress]] — `/api/relay/*`의 서버 쪽 동작.
- [[aws-access-and-sso]] — `/api/identity`·`/api/sso/*`·수집 세션 뒤의 `AwsAccess`.
- [[views]] — 문서 엔드포인트의 보기 쿼리 의미.
- [[cloudformation]] — `/cloudformation` 응답 템플릿의 내용.
- [[porting-guide]] — 라우터를 다른 FastAPI 앱에 붙이는 이식 순서.
