---
title: 회사 스택으로 옮기기 (React + Vite, FastAPI, 브라우저 egress)
summary: Identity Center device code + 서버 서명 + 브라우저 전송 구조의 회사 서비스에 Diagram Bot을 이식하는 순서 — 백엔드 장착 두 가지 방법, 바꿔 끼울 경계 3곳, React로 옮길 모듈, 확인 목록.
sources: [src/diagram_bot/api.py, src/diagram_bot/routes/__init__.py, src/diagram_bot/server.py, src/diagram_bot/aws_access.py, src/diagram_bot/store.py, src/diagram_bot/egress.py, src/diagram_bot/static/js/http.js, src/diagram_bot/static/js/relay.js, src/diagram_bot/static/js/labels.js]
related: [architecture, browser-egress, aws-access-and-sso, storage, http-api, web-app, team-mode-security]
updated: 2026-10-18
---
# 회사 스택으로 옮기기

> 회사 구조(프런트 React + Vite, 백엔드 Python, Identity Center 시작 URL + device code, 백엔드가 서명하고 브라우저가 PC IP에서 AWS 호출)는
> 이 저장소의 `--aws-egress browser` 모드와 같은 구조다. 백엔드는 **라이브러리 또는 라우터로 장착**하고, 경계 세 곳(인증·저장소·화면)만 바꾼다.

## 1. 백엔드 장착 — 두 가지 중 하나

**A. 라우터 장착 (회사 백엔드가 FastAPI일 때, 가장 빠름)**
```python
from diagram_bot.routes import Context, install_error_handlers
from diagram_bot.routes import snapshots, documents, aws, agent
from diagram_bot.store import SnapshotStore, DocumentStore
from diagram_bot.aws_access import AwsAccess
from diagram_bot.auth import AuditLog

context = Context(snapshots=SnapshotStore(DATA), documents=DocumentStore(DATA),
                  aws=AwsAccess("browser", sso_start=(START_URL, "us-east-1")),
                  audit=AuditLog(DATA / "audit.log"), token=PAGE_TOKEN)
install_error_handlers(app)                      # NotFound → 404, Conflict → 409
for module in (snapshots, documents, agent):
    app.include_router(module.router(context), prefix="/diagram")
app.include_router(aws.router(context), prefix="/diagram")
```
- 회사 인증 미들웨어가 `request.state.user`(팀 모드면 `request.state.role`도)를 넣어야 한다. 라우터는 이 값으로 SSO 흐름·중계 요청을 사용자에게 묶는다.
- 이 앱의 미들웨어(페이지 토큰 `X-Diagram-Bot`, Origin 확인, 15 MiB 제한, 감사 로그)는 `server.create_app` 안에 있다. 회사 미들웨어가 CSRF·감사를 맡지 않으면 같은 규칙을 옮긴다([[team-mode-security]]).
- prefix를 붙이면 프런트의 API 경로(`/api/...`)도 같은 prefix로 바꾼다.

**B. 라이브러리만 사용 (회사 API 설계를 유지할 때)**
```python
from diagram_bot import api
snapshot = api.collect_snapshot(session, region, expected_account=account, prepare=lambda s: relay.attach(s, user))
xml = api.render(snapshot, view="detail", vpc=vpc_id)
result = api.review_snapshot(snapshot); draft = api.cloudformation(snapshot)
```
- 의존성은 `boto3`, `defusedxml`(채팅은 `strands-agents`). 스냅샷 JSON은 회사 저장소에 그대로 넣으면 된다([[snapshot-model]]).

## 2. 바꿔 끼울 경계 세 곳

| 경계 | 이 저장소 | 회사에서 |
| --- | --- | --- |
| AWS 인증·경로 | `aws_access.AwsAccess` (`sso.SsoLogins` + `egress.BrowserRelay`) | 이미 있는 Identity Center 로그인을 쓰려면 `session()`/`collection_session()`이 회사 토큰으로 만든 `boto3.Session`을 돌려주게 바꾼다. 서명 위치·브라우저 전송은 `BrowserRelay`를 그대로 쓴다 ([[aws-access-and-sso]]) |
| 저장소 | `store.SnapshotStore`, `DocumentStore` (JSON 파일) | 같은 메서드(`save/get/list/seed`, `read/replace`)를 DB·S3로 구현 ([[storage]]) |
| 화면 | 빌드 없는 ES 모듈(`static/`) | React 컴포넌트. 아래 3절 |

## 3. 프런트엔드 (React + Vite)

**그대로 가져오는 모듈** (프레임워크 무관, `src/diagram_bot/static/js/`)
- `http.js` — `createClient(getToken, {onUnauthorized})` → `{api, streamApi}`. 채팅 NDJSON 스트림 포함.
- `relay.js` — `startRelay(api, {onRequests})`, `send(request)`. 브라우저 egress의 브라우저 쪽 전부.
- `labels.js` — 노드 종류·관계·서브넷 경로 클래스·조회 상태·보기 이름(한국어).

```jsx
// 앱 최상단에서 한 번: 로그인 후 서버가 서명한 AWS 요청을 이 PC에서 보낸다.
useEffect(() => { if (boot.egress === 'browser') startRelay(api); }, [boot.egress]);
```
```js
// vite.config.js — 개발 중 API를 백엔드로 (같은 출처처럼 보이게 해 Origin 확인·쿠키를 유지)
export default { server: { proxy: { '/api': 'http://127.0.0.1:8765', '/editor': 'http://127.0.0.1:8765' } } };
```

**다시 만드는 부분** — `app.js`의 화면 로직([[web-app]]): 스냅샷 선택, 보기(전체·상세·선택 리소스, 경로 필터), 속성 패널, 검토·변경 탭, 다운로드, 로그인·수집 대화상자, 채팅 패널.
- **draw.io 편집기:** 고정 버전(draw.io 32.4.1, `diagram-bot setup-editor`)을 사내에서 정적 호스팅하고 iframe + `editor-bridge.js` 메시지 규약을 그대로 쓴다.
  처음에는 편집 없이 `GET /api/snapshots/{id}/document`의 XML을 보여 주거나 PNG·`.drawio` 다운로드만 제공해도 된다.
- 쓰기 요청은 `X-Diagram-Bot` 페이지 토큰(`/api/bootstrap`의 `token`)을 보낸다. 회사가 다른 CSRF 방식을 쓰면 미들웨어와 `http.js`를 함께 바꾼다.

## 4. 확인 목록
1. **권한:** Permission Set이 `readonly-policy.json`의 읽기 액션을 포함하는지. 빠지면 해당 범위가 `조회 불가`와 필요한 IAM 액션으로 표시된다([[collection]]).
2. **CORS·경로:** 회사 PC에서 `scripts/verify_browser_egress.py`로 실제 호출 확인(서버 AWS 통신 차단 상태) — [[browser-egress]].
3. **Identity Center:** 서버에서 `oidc.<region>.amazonaws.com`, `portal.sso.<region>.amazonaws.com`에 나갈 수 있어야 한다(회사 샘플도 서버가 `GetRoleCredentials` 호출).
4. **Bedrock(채팅을 쓸 때):** 역할에 `bedrock:InvokeModel`과 모델 액세스. 브라우저 egress에서는 비스트리밍 Converse.
5. **규모:** 가장 큰 계정에서 수집 시간·화면 가독성 확인(검증은 리소스 ~100개 규모까지, [[views]]의 경로 필터 참고).
6. **테스트 이식:** `tests/test_egress.py`(중계 = 직접 수집), `tests/test_api.py`(라이브러리), `tests/test_server.py`·`test_sso.py`(라우터).

## 바꿀 때 주의
- 라우터는 `request.state.user`가 없으면 동작하지 않는다(SSO·중계·채팅이 사용자 단위).
- 브라우저 egress에서 탭을 닫으면 새 AWS 요청은 30초(`idle`) 뒤부터 즉시 `RelayError`, 이미 넘긴 요청은 최대 90초(`timeout`) 뒤 실패한다. 수집은 그 범위를 `조회 불가`로 남기거나 실패로 끝난다.

## 관련
- [[architecture]] · [[http-api]] · [[web-app]] · [[storage]] · [[team-mode-security]]
