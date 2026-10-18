---
title: 웹 앱 (프런트엔드)
summary: 빌드 없는 ES 모듈 프런트엔드(app.html, app.js, js/http.js·relay.js·labels.js)의 화면 구조, state 객체와 주요 흐름, draw.io 임베드 메시지 규약, React로 옮길 때 그대로 쓸 모듈과 다시 짤 부분.
sources: [src/diagram_bot/static/app.html, src/diagram_bot/static/app.js, src/diagram_bot/static/js/http.js, src/diagram_bot/static/js/relay.js, src/diagram_bot/static/js/labels.js, src/diagram_bot/static/editor-bridge.js, src/diagram_bot/editor.py, src/diagram_bot/server.py]
related: [http-api, storage, chat-agent, team-mode-security, browser-egress, views, porting-guide]
updated: 2026-10-18
---
# 웹 앱 (프런트엔드)

> `GET /`이 내보내는 `app.html` 한 장과 `<script type="module" src="/static/app.js">`가 전부다. 번들러·프레임워크 없이 브라우저가 ES 모듈을 직접 불러오고, 구성도 편집은 같은 출처의 iframe에 띄운 고정 버전 draw.io가 맡는다.

## 핵심
- 파일: `static/app.html`(마크업), `static/app.css`, `static/app.js`(화면 로직 전부), `static/js/http.js`, `static/js/relay.js`, `static/js/labels.js`, `static/editor-bridge.js`(draw.io 안에서 도는 플러그인).
- DOM은 `$(id)`로 찾고 `el(tag, text, className)`로 만든다. 모델 응답·리소스 속성은 `textContent`로만 넣고 `innerHTML`은 쓰지 않는다(채팅 Markdown도 `markdown()`·`inline()`이 DOM 노드로 만든다).
- 디버깅·브라우저 검증용으로 `window.diagramBot = {state}`를 노출한다(`scripts/verify_demo.py`가 읽는다).

## app.html 구조 (콘솔 셸)
- **상단 헤더** `.console-header`: 브랜드, 현재 리전(`#region-label`), AI 어시스턴트 버튼(`#chat-button`), 프로필 메뉴(`#profile-button` → `#profile-menu`: AWS 로그인, AWS 리소스 수집, AWS 로그아웃, 워크스페이스 로그아웃).
- **사이드 내비** `.side-nav`: 스냅샷 선택(`#snapshot-select`, "수집 이력"), 계정 요약, "전체 구성" 버튼과 VPC별 버튼(`#environments`), 도구(스냅샷 JSON 가져오기, 계정 통합 보기).
- **본문** `main.content`: 경로 표시, 제목, 지표(`#metrics`), 작업 영역.
  - 탭: 구성도 / 리소스 목록 / 검토 결과 / 변경 비교, 오른쪽에 저장 상태·`편집본 저장`·`다운로드`.
  - 보기 바: 전체 구성 / 상세 구성 / 선택 리소스, 표시 범위 문구, 부분 조회 칩.
  - 필터 바: 검색, 종류 필터, `SG·ENI·EBS 참조 표시`, 경로 필터(`#route-filter`), `＋ 메모`, `자동 배치 다시 실행`, `이전 편집본 복원`.
  - 캔버스 `#canvas-area` 안 `iframe#editor`와 로딩 오버레이, 목록(`#inventory`), 검토(`#review`), 변경(`#changes`), 오른쪽 속성 패널(`aside#inspector`).
- **어시스턴트 패널** `aside#chat-panel`: 모델 선택, 대화 로그, 예시 요청 버튼, 입력 폼(최대 4000자).
- **대화상자** `<dialog>`: `#export-dialog`(draw.io·PNG·JSON·CSV·검토 CSV·CloudFormation), `#combine-dialog`, `#coverage-dialog`, `#signin-dialog`(SSO와 로컬 프로파일), `#collection-dialog`.

## state 객체
`app.js` 맨 위의 `state` 하나가 화면 상태 전부다. 처음 필드: `token`, `snapshot`, `snapshotId`, `view`(`overview`·`detail`·`focus`), `vpc`, `focus`, `references`, `routes`, `revision`, `xml`, `history`, `visible`, `counts`, `ready`(편집기 init 받음), `loading`(문서 적재 중), `dirty`(저장 안 한 편집), `busy`(저장·재배치 중), `mode`(탭), `selected`. 실행 중 추가되는 필드: `role`, `team`, `egress`, `snapshots`, `review`, `identity`, `sso`, `ssoDefaults`, `ssoTimer`, `chatReady`, `chatBusy`, `relayed`, `scrollY`. 버튼 활성 상태는 모두 `setControls()`가 이 값과 역할(`viewer`는 편집·가져오기 불가, `admin`만 로그인·수집, `viewer`는 채팅 불가)에서 계산한다.

## 주요 흐름
- **시작** `start()`: `GET /api/bootstrap` → `state.token`·역할·팀 모드·egress 저장 → egress가 `browser`면 `startRelay(api)` 시작과 안내 문구 표시 → 팀 모드면 사용자 이름·역할과 로그아웃 버튼 → 프로파일 목록 → `refreshSnapshots('demo')`, `loadSnapshot('demo')` → `editor_ready`가 아니면 `setup-editor` 안내로 멈춤, 맞으면 iframe `src`를 `/editor/index.html?embed=1&proto=json&configure=1&...&offline=1&local=1&stealth=1&lang=ko`로 설정.
- **스냅샷 적재** `loadSnapshot(id)`: 편집 중이면 먼저 저장 → 스냅샷과 `findings`를 받아 `renderReview()` → VPC가 하나이고 TGW·피어링·VPN·DX 게이트웨이가 없으면 바로 상세 보기, 아니면 전체 구성 → `renderSummary()`(지표, VPC 버튼, 수집 상태 대화상자) → `loadView()`.
- **문서 적재** `loadView()`: `documentUrl()`로 보기 쿼리를 만들어 `GET .../document` → `applyDocument()`가 `xml`, `revision`, `history`, `visible`, `counts`를 반영 → 편집기가 준비됐으면 `sendDocument()`가 draw.io `load` 메시지를 보낸다(30초 응답 없으면 경고).
- **저장** `saveDocument()`: `viewer`면 아무것도 안 함 → `requestDocument()`가 iframe에 `diagramBotDocument`를 요청(15초 제한) → `PUT .../document`에 `{xml, revision}` → 409면 서버 메시지(`다른 창에서 편집본을 저장했습니다…`)를 토스트로 보인다. 보기 전환·스냅샷 변경·채팅 전송 전에도 `dirty`면 자동 저장하고, `beforeunload`가 저장 안 한 편집을 경고한다.
- **재배치·복원** `replaceDocument(suffix, body)`: `/relayout`은 `{revision}`, `/restore`는 `{revision, from_revision: history 마지막}`을 POST한 뒤 다시 `sendDocument()`.
- **보기·포커스·경로 필터**: `switchView(view, {vpc, focus})`. 상세가 아니거나 다른 VPC로 가면 `state.routes`를 지운다. `renderRouteFilter()`는 현재 VPC의 로드 밸런서·대상 그룹을 옵션으로 채운다.
- **속성 패널** `showResource(id)`: 종류·이름·ID, 동작 버튼(상세 구성 보기, 선택 리소스 보기, 이 경로만 보기, 모든 경로 보기, 직접 관계 강조), 필드 목록, 태그, API 관계 버튼(`showRelation`), 원본 속성. 선택은 iframe에 `diagramBotSelect`로 전달된다.
- **검토·변경 탭**: `renderReview()`는 심각도 필터. `renderChanges()`는 같은 계정·리전의 더 이른 스냅샷을 기본으로 골라 `GET /api/snapshots/{id}/diff/{other}`를 그린다.
- **다운로드**: draw.io(`saveDocument` 결과 XML), PNG(draw.io `export` 메시지, `pngWaiter`로 45초 대기, 1000바이트 미만이면 실패 처리), JSON(스냅샷 그대로), CSV(노드 목록, `= + - @` 앞에 `'`), 검토 CSV, CloudFormation(`fetch`로 `/cloudformation`, 합친 스냅샷은 `part`마다 한 파일).
- **로그인 메뉴**: 로컬 프로파일은 `GET /api/identity?profile=`로 확인해 `state.identity = {via: 'profile', ...}`. SSO는 `POST /api/sso/login` → 사용자 코드와 승인 링크 표시 → `ssoPoll()`이 `interval`초마다 `GET /api/sso/login/{id}` → `authorized`면 계정 목록과 `ssoRoles()`(이름에 read only가 들어간 역할을 기본 선택) → `이 역할로 로그인`이 `POST .../use`. 화면 전환은 `ssoView('form'|'device'|'select')`.
- **수집 대화상자**: `POST /api/collections`(SSO면 `sso_login`·`sso_role`, 아니면 `profile`) 후 1.5초마다 작업을 폴링하고, 끝나면 새 스냅샷을 연다.
- **계정 통합**: 수집 스냅샷(합친 것·설계안 제외)을 골라 `POST /api/snapshots/combine`.
- **채팅**: `openChat()`이 처음 한 번 `GET /api/agent`로 모델 목록을 채운다. 전송은 `streamApi('/api/agent/chat/stream', {message, model, snapshot: state.snapshotId}, onEvent)`. `tool`·`model`(대체 모델) 이벤트는 상태 문구, `snapshot`은 `show(id)`가 캔버스에 바로 연다(적재 중이면 가장 최근 것만 기다림), `text`는 응답 말풍선에 이어 붙인다. 끝나면 `done` 결과로 Markdown 말풍선과 메타(모델, 대체 여부, 도구 횟수, 구성도 갱신 횟수)를 그린다. `↺`는 `POST /api/agent/reset`.

## ES 모듈
- `js/http.js` — `createClient(getToken, {onUnauthorized})` → `{api, streamApi}`. `api(path, options)`는 JSON 헤더와 `X-Diagram-Bot`을 붙이고, 401이면 `onUnauthorized()`(기본 `/login` 이동), 실패면 `detail`로 `Error`. `streamApi(path, body, onEvent)`는 NDJSON을 줄 단위로 `onEvent`에 넘기고 마지막 줄이 `error`이거나 없으면 throw, 아니면 마지막 이벤트를 돌려준다.
- `js/relay.js` — `send(request)`(AWS API 주소만 `fetch`, `credentials: 'omit'`, 응답 본문 base64)와 `startRelay(api, {onRequests})`(`/api/relay/next` 롱 폴링 → 요청마다 `POST /api/relay/{id}`, 오류 시 3초 대기). 서버 쪽은 [[browser-egress]].
- `js/labels.js` — 순수 데이터: `names`(노드 종류), `relationNames`, `routeClasses`, `statuses`(수집 상태), `viewNames`.

## draw.io 임베드 규약
- 편집기는 `editor.py`가 `setup-editor`로 설치한 draw.io `VERSION = "32.4.1"`(SHA-256 고정, `~/.cache/diagram-bot/drawio/<버전>`)이다. 서버가 `/editor`로 내보내고, `/editor/js/app.min.js`에 `editor-bridge.js`를 이어 붙여 원본을 고치지 않고 플러그인을 넣는다([[http-api]]).
- 양쪽 모두 `event.origin === location.origin`과 보낸 창(`parent` 또는 iframe의 `contentWindow`)을 확인하고, 메시지는 JSON 문자열이다.
- 앱 → 편집기 (draw.io 표준): `configure`(기본 글꼴, 비압축), `load`(`xml`, `autosave: 1`, `fit`, `border: 24` 등), `export`(`format: 'png'`, `scale`, `transparent`, `background`).
- 앱 → 편집기 (브리지): `diagramBotOverview`(사이드바·서식 창을 숨기고 읽기 쉬운 배율로 맞춤, 최소 0.6), `diagramBotDocument`(`requestId`), `diagramBotSelect`(`resourceId`, 선택적 `relations`), `diagramBotSelectRelation`(`relationId` 또는 `relationIndex`), `diagramBotNote`(노란 메모 도형 추가).
- 편집기 → 앱 (draw.io 표준): `configure`, `init`(→ `state.ready`), `load`(→ 오버레이 해제, `diagramBotOverview` 전송), `autosave`(→ `dirty`), `save`(→ `saveDocument`), `export`(PNG data URI).
- 편집기 → 앱 (브리지): `diagramBotSelection`(`resourceId`, `summary`, `relationId`, `relationIndex`, `label`; 다중 선택은 보내지 않음), `diagramBotDocument`(`requestId`, `xml`), `diagramBotError`, `diagramBotNotVisible`, `diagramBotHighlighted`(`count`), `diagramBotNoteAdded`.
- 브리지는 관계 선(`relationId`·`relationIndex` 속성이 있는 셀)을 끊을 수 없게 하고, `window.diagramBotGraph`를 노출한다.

## React로 옮길 때
- **그대로 가져갈 모듈**: `js/http.js`(`createClient(() => token)`), `js/relay.js`(로그인 뒤 한 번 `startRelay(api)`), `js/labels.js`. 셋 다 머리말에 프레임워크 없이 다른 프런트엔드에서 쓰도록 만들었다고 적혀 있고 `app.js`의 `state`에 의존하지 않는다. `http.js`의 기본 `onUnauthorized`는 `location.href = '/login'`이므로 라우터를 쓰면 이 콜백을 넘긴다.
- **컴포넌트로 다시 짤 부분** (`app.js`):
  - `state`와 `setControls()` → 스토어(컨텍스트·리듀서 등)와 파생 값. 역할별 비활성 규칙을 그대로 옮긴다.
  - 셸: 헤더·프로필 메뉴, 사이드 내비(스냅샷 선택, VPC 목록), 지표, 탭, 보기 바, 필터 바.
  - 편집기 컴포넌트: iframe, `message` 리스너, `post()`, `pending` 맵(`requestDocument`), `pngWaiter`, 30초·15초·45초 시간 제한. 다른 페이지가 이 iframe을 쓰려면 같은 출처에서 `/editor`와 브리지 판 `app.min.js`를 내보내야 한다.
  - 문서 흐름: `documentUrl`, `loadView`, `saveDocument`(409 처리), `replaceDocument`, `dirty` 자동 저장과 `beforeunload`.
  - 속성 패널(`showResource`의 필드 표), 목록·검토·변경 탭, 다운로드(CSV 수식 이스케이프 포함).
  - 대화상자 다섯 개: 로그인(SSO device code 폴링 포함), 수집(작업 폴링), 통합, 수집 상태, 다운로드.
  - 채팅 패널: `streamApi` 이벤트 처리, `show()`의 "가장 최근 스냅샷만 적재" 직렬화, `markdown()`(innerHTML 금지 규칙 유지).

## 바꿀 때 주의
- 메시지 이름(`diagramBot*`)이나 iframe 쿼리를 바꾸면 `editor-bridge.js`와 `app.js`를 함께 고치고 `scripts/verify_demo.py` 브라우저 검증을 다시 돌린다.
- 변경 요청은 반드시 `api()`/`streamApi()`를 거쳐 페이지 토큰을 붙인다. 직접 `fetch`하는 곳은 CloudFormation 다운로드(GET)뿐이다.

## 관련
- [[http-api]] — 화면이 부르는 엔드포인트와 상태 코드.
- [[storage]] — 리비전·이력의 서버 쪽 규칙.
- [[chat-agent]] — 스트림 이벤트를 만드는 에이전트.
- [[team-mode-security]] — 역할과 페이지 토큰.
- [[browser-egress]] — `relay.js`가 받는 요청의 출처.
- [[views]] — 보기·포커스·경로 필터의 서버 쪽 계약.
- [[porting-guide]] — React + Vite 회사 스택으로 옮기는 전체 순서.
