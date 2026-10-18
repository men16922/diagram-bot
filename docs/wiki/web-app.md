---
title: 웹 앱 (프런트엔드)
summary: 빌드 없는 ES 모듈 프런트엔드(app.html, 진입 app.js, js/ 화면 모듈 11개와 http.js·relay.js·labels.js)의 화면 구조, state 객체와 주요 흐름, draw.io 임베드 메시지 규약, React로 옮길 때 그대로 쓸 모듈과 다시 짤 부분.
sources: [src/diagram_bot/static/app.html, src/diagram_bot/static/app.js, src/diagram_bot/static/js/state.js, src/diagram_bot/static/js/ui.js, src/diagram_bot/static/js/editor.js, src/diagram_bot/static/js/document.js, src/diagram_bot/static/js/workspace.js, src/diagram_bot/static/js/inspector.js, src/diagram_bot/static/js/review.js, src/diagram_bot/static/js/exports.js, src/diagram_bot/static/js/signin.js, src/diagram_bot/static/js/chat.js, src/diagram_bot/static/js/xml.js, src/diagram_bot/static/js/http.js, src/diagram_bot/static/js/relay.js, src/diagram_bot/static/js/labels.js, src/diagram_bot/static/editor-bridge.js, src/diagram_bot/editor.py, src/diagram_bot/server.py, scripts/verify_demo.py]
related: [http-api, storage, chat-agent, team-mode-security, browser-egress, views, porting-guide]
updated: 2026-10-19
---
# 웹 앱 (프런트엔드)

> `GET /`이 내보내는 `app.html` 한 장과 `<script type="module" src="/static/app.js">`가 전부다. 번들러·프레임워크 없이 브라우저가 ES 모듈을 직접 불러오고, 구성도 편집은 같은 출처의 iframe에 띄운 고정 버전 draw.io가 맡는다.

## 핵심
- 파일: `static/app.html`(마크업), `static/app.css`, `static/app.js`(진입 모듈: 셸 버튼 연결과 `start()`), `static/js/*.js`(화면 모듈, 아래 "ES 모듈"), `static/editor-bridge.js`(draw.io 안에서 도는 플러그인).
- 모듈 의존은 한 방향이다: `state.js`(상태·API 클라이언트) ← `ui.js`(DOM 도우미·`setControls`) ← 기능 모듈. 기능 모듈끼리는 필요한 함수만 가져온다. `tests/test_frontend.py`가 import 대상 export가 실제로 있는지, `$('id')`가 app.html에 있는지, 모든 모듈이 JavaScript로 서빙되는지를 Node 없이 확인한다.
- DOM은 `$(id)`로 찾고 `el(tag, text, className)`로 만든다. 모델 응답·리소스 속성은 `textContent`로만 넣고 `innerHTML`은 쓰지 않는다(채팅 Markdown도 `markdown()`·`inline()`이 DOM 노드로 만든다).
- 디버깅·브라우저 검증용으로 `window.diagramBot = {state}`를 노출한다(`scripts/verify_demo.py`가 읽는다).

## app.html 구조 (콘솔 셸)
- **상단 헤더** `.console-header`: 브랜드, 현재 리전(`#region-label`), AI 어시스턴트 버튼(`#chat-button`), 프로필 메뉴(`#profile-button` → `#profile-menu`: AWS 로그인, AWS 리소스 수집, AWS 로그아웃, 감사 로그(팀 모드 `admin`만 보임), 워크스페이스 로그아웃).
- **사이드 내비** `.side-nav`: 스냅샷 선택(`#snapshot-select`, "수집 이력"), 계정 요약, "전체 구성" 버튼과 VPC별 버튼(`#environments`), 도구(스냅샷 JSON 가져오기, 계정 통합 보기, draw.io XML).
- **본문** `main.content`: 경로 표시, 제목, 지표(`#metrics`), 작업 영역.
  - 탭: 구성도 / 리소스 목록 / 검토 결과 / 변경 비교, 오른쪽에 저장 상태·`편집본 저장`·`다운로드`.
  - 보기 바: 전체 구성 / 상세 구성 / 선택 리소스, 표시 범위 문구, 부분 조회 칩.
  - 필터 바: 검색, 종류 필터, `SG·ENI·EBS 참조 표시`, 경로 필터(`#route-filter`), `＋ 메모`, `자동 배치 다시 실행`, `이전 편집본 복원`.
  - 캔버스 `#canvas-area` 안 `iframe#editor`와 로딩 오버레이, 목록(`#inventory`), 검토(`#review`), 변경(`#changes`), 오른쪽 속성 패널(`aside#inspector`).
- **어시스턴트 패널** `aside#chat-panel`: 모델 선택, 대화 로그, 예시 요청 버튼, 입력 폼(최대 4000자).
- **대화상자** `<dialog>`: `#export-dialog`(규격 선택 `#export-spec`, draw.io·PNG·JSON·CSV·검토 CSV·CloudFormation), `#combine-dialog`, `#xml-dialog`(draw.io XML 가져오기·정렬·사내 표준 채택과 사내 표준 목록), `#audit-dialog`(감사 로그 표), `#coverage-dialog`, `#signin-dialog`(SSO와 로컬 프로파일), `#collection-dialog`.

## state 객체
`js/state.js`의 `state` 하나가 화면 상태 전부이고 모든 필드를 처음부터 선언한다: `token`, `role`, `team`, `snapshots`, `snapshot`, `snapshotId`, `review`, `view`(`overview`·`detail`·`focus`), `vpc`, `focus`, `references`, `routes`, `revision`, `xml`, `history`, `visible`, `ready`(편집기 init 받음), `loading`(문서 적재 중), `dirty`(저장 안 한 편집), `busy`(저장·재배치 중), `scrollY`, `mode`(탭), `selected`, `identity`, `sso`, `ssoDefaults`, `ssoTimer`, `chatReady`, `chatBusy`, `standards`(`GET /api/standards` 결과), `presets`(bootstrap의 규격 프리셋 이름), `xmlBusy`(XML 대화상자 작업 중). 같은 파일이 `api`·`request`·`streamApi`(페이지 토큰을 붙인 클라이언트)와 그래프 조회 도우미(`node`, `vpcNode`, `relationsOf`, `vpcOf`)를 내보낸다. 버튼 활성 상태는 모두 `setControls()`가 이 값과 역할(`viewer`는 편집·가져오기·XML 가져오기·정렬·채택 불가, `admin`만 로그인·수집과 `서버 기본 규격으로 지정` 체크, `viewer`는 채팅 불가)에서 계산한다. 서버 규칙(`auth.allowed`, adopt의 `make_default`)과 같다.

## 주요 흐름
- **시작** `start()`: `GET /api/bootstrap` → `state.token`·역할·팀 모드·규격 프리셋(`presets`)·egress 저장 → egress가 `browser`면 `startRelay(api)` 시작과 안내 문구 표시 → 팀 모드면 사용자 이름·역할과 로그아웃 버튼(`admin`이면 감사 로그 메뉴도) → 프로파일 목록 → `refreshSnapshots('demo')`, `loadSnapshot('demo')` → `editor_ready`가 아니면 `setup-editor` 안내로 멈춤, 맞으면 iframe `src`를 `/editor/index.html?embed=1&proto=json&configure=1&...&offline=1&local=1&stealth=1&lang=ko`로 설정.
- **스냅샷 적재** `loadSnapshot(id)`(`js/document.js`): 순번 토큰을 받아 더 새 적재가 시작되면 결과를 버린다(`loadView`도 같다). 편집 중이면 먼저 `flushEdits()`로 저장 → 스냅샷과 `findings`를 받아 `renderReview()` → VPC가 하나이고 TGW·피어링·VPN·DX 게이트웨이가 없으면 바로 상세 보기, 아니면 전체 구성 → `renderSummary()`(지표, VPC 버튼, 수집 상태 대화상자) → `loadView()`.
- **문서 적재** `loadView()`: `documentUrl()`로 보기 쿼리를 만들어 `GET .../document` → `applyDocument()`가 `xml`, `revision`, `history`, `visible`, `counts`를 반영 → 편집기가 준비됐으면 `sendDocument()`가 draw.io `load` 메시지를 보낸다(30초 응답 없으면 경고).
- **저장** `saveDocument()`: `viewer`면 저장하지 않는다(다운로드는 편집기의 현재 XML을 쓴다) → `requestDocument()`가 iframe에 `diagramBotDocument`를 요청(15초 제한) → `PUT .../document`에 `{xml, revision}` → 409면 서버 메시지(`다른 창에서 편집본을 저장했습니다…`)를 토스트로 보인다. 보기 전환·스냅샷 변경·채팅 전송 전에도 `dirty`면 자동 저장하고, `beforeunload`가 저장 안 한 편집을 경고한다.
- **재배치·복원** `replaceDocument(suffix, body)`: `/relayout`은 `{revision}`, `/restore`는 `{revision, from_revision: history 마지막}`을 POST한 뒤 다시 `sendDocument()`.
- **보기·포커스·경로 필터**: `switchView(view, {vpc, focus})`. 상세가 아니거나 다른 VPC로 가면 `state.routes`를 지운다. `renderRouteFilter()`는 현재 VPC의 로드 밸런서·대상 그룹을 옵션으로 채운다.
- **속성 패널** `showResource(id)`: 종류·이름·ID, 동작 버튼(상세 구성 보기, 선택 리소스 보기, 이 경로만 보기, 모든 경로 보기, 직접 관계 강조), 필드 목록, 태그, API 관계 버튼(`showRelation`), 원본 속성. 선택은 iframe에 `diagramBotSelect`로 전달된다.
- **검토·변경 탭**: `renderReview()`는 심각도 필터. `renderChanges()`는 같은 계정·리전의 더 이른 스냅샷을 기본으로 골라 `GET /api/snapshots/{id}/diff/{other}`를 그린다.
- **다운로드** (`js/exports.js`): `규격` 선택(`#export-spec`: `기본 · 현재 편집본`, 프리셋, 규격이 있는 사내 표준; 대화상자를 열 때마다 `/api/standards`로 다시 채운다). `기본`이 아니면 draw.io·PNG는 `documentUrl('', false, spec)`의 `GET .../document?spec=` 미리보기(`revision: null`을 확인)로 만든다. 새로 그린 것이라 수동 편집이 없다고 대화상자에 적고, 저장하지 않는다(PUT 없음). draw.io의 PNG `export`는 받은 XML을 편집기에 적재하므로 규격 PNG 뒤에는 보이던 문서를 `sendDocument()`로 되돌린다(저장 안 한 편집은 먼저 `flushEdits()`, viewer는 편집기 현재 XML). 파일 이름은 `v<리비전>` 대신 `spec-<이름>`. 기본 내보내기: draw.io(`saveDocument` 결과 XML, viewer는 편집기 현재 XML), PNG(draw.io `export` 메시지, `pngWaiter`로 45초 대기, 1000바이트 미만이면 실패 처리), JSON(스냅샷 그대로), CSV(노드 목록, `= + - @` 앞에 `'`), 검토 CSV, CloudFormation(`request()`로 `/cloudformation`: 401 이동과 오류 문구를 `api()`와 같이 처리, 합친 스냅샷은 `part`마다 한 파일). CSV 두 종류는 같은 CSV 작성기를 쓴다.
- **로그인 메뉴**: 로컬 프로파일은 `GET /api/identity?profile=`로 확인해 `state.identity = {via: 'profile', ...}`. SSO는 `POST /api/sso/login` → 사용자 코드와 승인 링크 표시 → `ssoPoll()`이 `interval`초마다 `GET /api/sso/login/{id}` → `authorized`면 계정 목록과 `ssoRoles()`(이름에 read only가 들어간 역할을 기본 선택) → `이 역할로 로그인`이 `POST .../use`의 응답을 기다려 성공했을 때만 로그인 상태로 바꾸고, 실패는 대화상자 안(`#sso-use-status`)에 보인다. SSO 폴링은 로그인 시도 하나에 묶여 새 시도가 시작되면 옛 폴링이 멈춘다. 화면 전환은 `ssoView('form'|'device'|'select')`.
- **수집 대화상자**: `POST /api/collections`(SSO면 `sso_login`·`sso_role`, 아니면 `profile`) 후 1.5초마다 작업을 폴링하고(새 수집이 시작되면 옛 폴링은 멈춘다), 끝나면 새 스냅샷을 연다.
- **계정 통합**: 수집 스냅샷(합친 것·설계안 제외)을 골라 `POST /api/snapshots/combine`.
- **draw.io XML** (`js/xml.js`, 사이드 내비 `도구 › draw.io XML`): `.drawio`·`.xml` 파일 하나를 고른다(브라우저에서 10 MiB 확인). 결과와 오류는 대화상자 안(`#xml-status`, `#xml-result`)에 보인다(토스트는 모달 배경 뒤에 가려진다). 한 번에 한 작업(`state.xmlBusy`).
  - `스냅샷으로 가져오기`: `POST /api/xml/import`(`save: true`) → `refreshSnapshots`·`loadSnapshot`으로 연다 → `snapshot.metadata.import`의 페이지(가져옴/전체), 대응 셀 수, 미대응 도형(페이지·라벨·종류), 합성 항목을 보인다. 스냅샷 목록에서는 `가져옴 · 제목`이다(`snapshotLabel`의 `metadata.imported`).
  - `자동 정렬`: `POST /api/xml/align` → `before`·`after`의 `overlaps`, `out_of_bounds`, `edge_collisions`, `unaligned_tiers`, `unrouted_edges` 개수 표(`labels.alignMetricNames`), 페이지별 정렬·원본 유지 수, `reverted` 페이지 목록(품질 보호). 바뀐 페이지가 없으면 입력을 그대로 두었다고 알린다. `정렬된 .drawio 다운로드`는 `<파일 이름>-aligned.drawio`.
  - `사내 표준으로 채택`: 이름(`[a-z0-9-]`, 40자)·제목과 `서버 기본 규격으로 지정 (관리자)`(`#adopt-default`, admin·로컬 모드만 켜짐) → `POST /api/xml/adopt`(`save: true`) → 저장된 스냅샷을 열고 목록을 다시 읽는다. 기본 규격으로 지정했으면 다운로드 규격 목록과 어시스턴트(`apply_standard_architecture <이름>`)에서 쓸 수 있다고, 아니면 이 요청에만 적용됐다고 알린다.
  - 사내 표준 목록(`renderStandards`): `GET /api/standards`의 `company` 항목마다 제목, `기본 규격` 배지, 이름·채택 시각·채택자, `열기`(그 스냅샷), admin만 `삭제`(`confirm` 뒤 `DELETE /api/standards/{name}`).
- **감사 로그** (`js/signin.js`): 팀 모드 `admin`의 프로필 메뉴 `감사 로그` → `GET /api/audit?limit=200` → `#audit-rows`(시간, 사용자, 작업, 대상(쿼리·스냅샷·주소), 결과; `denied`·`failed`·`throttled`·4xx·5xx는 경고 배지). 로컬 모드와 다른 역할에는 메뉴가 없다.
- **채팅** (`js/chat.js`): `openChat()`이 처음 한 번 `GET /api/agent`로 모델 목록을 채운다. 전송은 `streamApi('/api/agent/chat/stream', {message, model, snapshot: state.snapshotId}, onEvent)`. `tool`·`model`(대체 모델) 이벤트는 상태 문구, `snapshot`은 `show(id)`가 캔버스에 바로 연다(적재 중이면 가장 최근 것만 기다리고, 한 단계 적재가 실패해도 오류를 알린 뒤 다음 단계는 계속 연다), `text`는 응답 말풍선에 이어 붙인다. 끝나면 `done` 결과로 Markdown 말풍선과 메타(모델, 대체 여부, 도구 횟수, 구성도 갱신 횟수)를 그린다. `↺`는 `POST /api/agent/reset`.

## ES 모듈
- `app.js` — 진입 모듈. 보기·탭·도구 모음·가져오기·통합 대화상자를 연결하고 `start()`를 부른다.
- `js/state.js` — 위 `state`, API 클라이언트, 그래프 조회 도우미.
- `js/ui.js` — `$`/`el`, `toast`, `clearNotice`, `guard`(버튼 핸들러를 감싸 오류를 토스트로; `bind`가 이것을 쓴다), `download`, `setControls()`.
- `js/editor.js` — draw.io 임베드 규약(아래): `openEditor`, `post`, `sendDocument`, `requestDocument`, `exportPng`.
- `js/document.js` — `refreshSnapshots`, `loadSnapshot`, `loadView`, `switchView`, `flushEdits`(편집 중이면 저장), `saveDocument`, `replaceDocument`, `snapshotLabel`.
- `js/workspace.js` — `renderSummary`(지표·VPC 버튼·수집 상태), 경로 필터, `renderInventory`.
- `js/inspector.js` — `showResource`, `showRelation`, `showElement`, `clearInspector`. 필드 표는 한 줄에 필드 하나인 데이터 목록이고, 관계의 끝점이 그래프에 없으면 ID만 보인다.
- `js/review.js` — `renderReview`, `renderChanges`.
- `js/exports.js` — 다운로드 대화상자 핸들러와 규격 선택.
- `js/signin.js` — `renderProfile`, 로그인 대화상자(SSO·프로파일), 수집 대화상자, 감사 로그 대화상자.
- `js/chat.js` — 어시스턴트 패널과 `markdown()`.
- `js/xml.js` — draw.io XML 대화상자와 사내 표준 목록: `loadStandards`(→ `state.standards`, `exports.js`도 쓴다), `renderStandards`.
- `js/http.js` — `createClient(getToken, {onUnauthorized})` → `{api, request, streamApi}`. `request(path, options)`는 JSON 헤더와 `X-Diagram-Bot`을 붙이고, 401이면 `onUnauthorized()`(기본 `/login` 이동), 실패면 `errorMessage()`로 만든 `Error`(JSON `detail`, 422 검증 목록은 `필드: 메시지`, JSON이 아닌 응답은 `요청에 실패했습니다 (HTTP 상태)`). `api()`는 `request()` 결과를 JSON으로 읽는다. `streamApi(path, body, onEvent)`는 NDJSON을 줄 단위로 `onEvent`에 넘기고 마지막 줄이 `error`이거나 없으면 throw, 아니면 마지막 이벤트를 돌려준다.
- `js/relay.js` — `send(request)`(AWS API 주소만 `fetch`, `credentials: 'omit'`, 응답 본문 base64)와 `startRelay(api, {onRequests})`(탭 id를 붙여 `/api/relay/next?tab=` 롱 폴링 → 요청마다 백그라운드로 보내고 `POST /api/relay/{id}`, 답을 기다리지 않고 곧바로 다시 폴링(열린 폴이 심장 박동), 오류 시 3초 대기). 서버 쪽은 [[browser-egress]].
- `js/labels.js` — 순수 데이터: `names`(노드 종류), `relationNames`, `routeClasses`, `statuses`(수집 상태), `viewNames`, `severityNames`, `alignMetricNames`(정렬 지표), `pillarNames`. `viewer.html`(CLI 보고서)의 종류 이름은 이 목록을 모두 덮어야 한다(`tests/test_frontend.py`).

## draw.io 임베드 규약
- 편집기는 `editor.py`가 `setup-editor`로 설치한 draw.io `VERSION = "32.4.1"`(SHA-256 고정, `~/.cache/diagram-bot/drawio/<버전>`)이다. 서버가 `/editor`로 내보내고, `/editor/js/app.min.js`에 `editor-bridge.js`를 이어 붙여 원본을 고치지 않고 플러그인을 넣는다([[http-api]]).
- iframe 쿼리의 `offline=1`·`stealth=1` 때문에 편집기는 외부로 요청을 보내지 않는다. `scripts/verify_demo.py`가 앱 주소가 아닌 호스트로 간 요청이 하나도 없는지 확인한다.
- 양쪽 모두 `event.origin === location.origin`과 보낸 창(`parent` 또는 iframe의 `contentWindow`)을 확인하고, 메시지는 JSON 문자열이다.
- 앱 → 편집기 (draw.io 표준): `configure`(기본 글꼴, 비압축), `load`(`xml`, `autosave: 1`, `fit`, `border: 24` 등), `export`(`format: 'png'`, `scale`, `transparent`, `background`).
- 앱 → 편집기 (브리지): `diagramBotOverview`(사이드바·서식 창을 숨기고 읽기 쉬운 배율로 맞춤, 최소 0.6), `diagramBotDocument`(`requestId`), `diagramBotSelect`(`resourceId`, 선택적 `relations`), `diagramBotSelectRelation`(`relationId` 또는 `relationIndex`), `diagramBotNote`(노란 메모 도형 추가).
- 편집기 → 앱 (draw.io 표준): `configure`, `init`(→ `state.ready`), `load`(→ 오버레이 해제, `diagramBotOverview` 전송), `autosave`(→ `dirty`), `save`(→ `saveDocument`), `export`(PNG data URI).
- 편집기 → 앱 (브리지): `diagramBotSelection`(`resourceId`, `summary`, `relationId`, `relationIndex`, `label`; 다중 선택은 보내지 않음), `diagramBotDocument`(`requestId`, `xml`), `diagramBotError`, `diagramBotNotVisible`, `diagramBotHighlighted`(`count`), `diagramBotNoteAdded`.
- 브리지는 관계 선(`relationId`·`relationIndex` 속성이 있는 셀)을 끊을 수 없게 하고, `window.diagramBotGraph`를 노출한다.

## React로 옮길 때
- **그대로 가져갈 모듈**: `js/http.js`(`createClient(() => token)`), `js/relay.js`(로그인 뒤 한 번 `startRelay(api)`), `js/labels.js`. 셋 다 머리말에 프레임워크 없이 다른 프런트엔드에서 쓰도록 만들었다고 적혀 있고 `js/state.js`에 의존하지 않는다. `http.js`의 기본 `onUnauthorized`는 `location.href = '/login'`이므로 라우터를 쓰면 이 콜백을 넘긴다.
- **컴포넌트로 다시 짤 부분** (`js/` 화면 모듈; 모듈 경계가 컴포넌트 경계와 거의 같다):
  - `state`와 `setControls()` → 스토어(컨텍스트·리듀서 등)와 파생 값. 역할별 비활성 규칙을 그대로 옮긴다.
  - 셸: 헤더·프로필 메뉴, 사이드 내비(스냅샷 선택, VPC 목록), 지표, 탭, 보기 바, 필터 바.
  - 편집기 컴포넌트: iframe, `message` 리스너, `post()`, `pending` 맵(`requestDocument`), `pngWaiter`, 30초·15초·45초 시간 제한. 다른 페이지가 이 iframe을 쓰려면 같은 출처에서 `/editor`와 브리지 판 `app.min.js`를 내보내야 한다.
  - 문서 흐름: `documentUrl`, `loadView`, `saveDocument`(409 처리), `replaceDocument`, `dirty` 자동 저장과 `beforeunload`.
  - 속성 패널(`showResource`의 필드 표), 목록·검토·변경 탭, 다운로드(CSV 수식 이스케이프 포함).
  - 대화상자 일곱 개: 로그인(SSO device code 폴링 포함), 수집(작업 폴링), 통합, 수집 상태, 다운로드(규격 미리보기 포함), draw.io XML(가져오기·정렬·채택·표준 목록), 감사 로그.
  - 채팅 패널: `streamApi` 이벤트 처리, `show()`의 "가장 최근 스냅샷만 적재" 직렬화, `markdown()`(innerHTML 금지 규칙 유지).

## 바꿀 때 주의
- 메시지 이름(`diagramBot*`)이나 iframe 쿼리를 바꾸면 `editor-bridge.js`와 `js/editor.js`를 함께 고치고 `scripts/verify_demo.py` 브라우저 검증을 다시 돌린다.
- 규격 미리보기(`?spec=`)는 절대 `PUT`하지 않는다. 미리보기 XML을 편집기에 넣는 코드(PNG `export`)는 끝나면 보이던 문서를 되돌린다(`scripts/verify_demo.py` 14단계가 PUT 수·리비전·`state.xml`을 확인).
- 역할별 비활성 규칙을 바꾸면 서버 규칙(`auth.allowed`)과 `tests/test_frontend.py`, `scripts/verify_team.py`를 함께 고친다.
- 변경 요청은 반드시 `api()`/`streamApi()`를 거쳐 페이지 토큰을 붙인다. `fetch`를 직접 부르는 곳은 `http.js`와 `relay.js`(AWS로 보내는 요청)뿐이다.

## 관련
- [[http-api]] — 화면이 부르는 엔드포인트와 상태 코드.
- [[storage]] — 리비전·이력의 서버 쪽 규칙.
- [[chat-agent]] — 스트림 이벤트를 만드는 에이전트.
- [[team-mode-security]] — 역할과 페이지 토큰.
- [[browser-egress]] — `relay.js`가 받는 요청의 출처.
- [[views]] — 보기·포커스·경로 필터의 서버 쪽 계약.
- [[porting-guide]] — React + Vite 회사 스택으로 옮기는 전체 순서.
