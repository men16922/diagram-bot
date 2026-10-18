---
title: 회사 스택으로 옮기기 (React + Vite, FastAPI, 브라우저 egress, 회사 draw.io 규격)
summary: 표준 drawio.xml을 직접 고치던 회사 버전을 인플레이스 자동 정렬(align_xml)·회사 규격(DrawioSpec)·스냅샷 파이프라인으로 이식하는 코딩 에이전트용 토큰 최적화 플레이북(라이브러리 명세, Level 0~4 파일 복사 목록, 계약 치트시트 포함).
sources: [pyproject.toml, src/diagram_bot/api.py, src/diagram_bot/drawio_spec.py, src/diagram_bot/xml_align.py, src/diagram_bot/designer.py, src/diagram_bot/samples/, src/diagram_bot/graph.py, src/diagram_bot/views.py, src/diagram_bot/layout/, src/diagram_bot/drawio.py, src/diagram_bot/findings.py, src/diagram_bot/iac/, src/diagram_bot/routes/__init__.py, src/diagram_bot/server.py, src/diagram_bot/aws_access.py, src/diagram_bot/store.py, src/diagram_bot/egress.py, src/diagram_bot/agent.py, src/diagram_bot/static/js/http.js, src/diagram_bot/static/js/relay.js, src/diagram_bot/static/js/labels.js, src/diagram_bot/static/editor-bridge.js]
related: [architecture, drawio-format-and-alignment, snapshot-model, layout-and-drawio, chat-agent, browser-egress, aws-access-and-sso, storage, http-api, web-app, team-mode-security]
updated: 2026-10-19
---
# 회사 스택으로 옮기기 (Coding Agent Playbook)

> **코딩 에이전트 필독**: 이 문서는 토큰을 최소한으로 쓰면서 원하는 기능만 회사 저장소(React + Vite, FastAPI, Identity Center, 브라우저 egress, 자체 `drawio.xml` 포맷)로 정확히 이식하기 위한 실행 명세서다.
> 사람용 고수준 개요·명령 프롬프트·제품 고도화 로드맵은 [docs/HUMAN_GUIDE.md](../HUMAN_GUIDE.md)에 있다.

## 1. 기존 회사 방식(`drawio.xml` 직접 정렬)과 연결하는 3가지 경로

| 경로 | 상황 | 호출할 API (`src/diagram_bot/api.py`) | 상세 위키 |
| --- | --- | --- | --- |
| **경로 A. 기존 XML 인플레이스 자동 정렬** | 회사가 이미 만든 `drawio.xml`의 셀 ID·라벨·사용자 속성은 유지하고 도형 겹침·컨테이너 이탈·AZ 서브넷 계층 행·직교 배선을 교정하고 싶을 때. 정렬 결과가 입력보다 나빠지는 페이지는 원래대로 돌려준다(`align_report`의 `status`). 엣지 스타일은 직교 경로·포트로 다시 쓴다 | `api.align_xml(xml_text, spec=None, mode="preserve")`<br>`api.inspect_xml(xml_text)` | [[drawio-format-and-alignment]] |
| **경로 B. 회사 `drawio.xml` 포맷·규격 일치화** | Diagram Bot이 생성하는 다이어그램을 회사 draw.io 규격(순수 `<mxCell>` 래퍼, 상단 배너 제거, 회사 폰트/색상/화살표, 격리 서브넷 별도 색상 등)에 맞추고 싶을 때 | `spec = api.spec_from_xml(company_sample_xml)` (또는 `"clean"`, `"mxcell"`)<br>`api.render(snapshot, view="detail", vpc=vpc_id, spec=spec)` | [[drawio-format-and-alignment]] |
| **경로 C. 기존 회사 XML → 스냅샷 역변환 후 확장** | 회사 표준 `drawio.xml`을 Diagram Bot `Snapshot` JSON으로 가져와 `Design` 도구로 리소스 추가(브라운필드), 47개 규칙 검토, Diff, CloudFormation 템플릿 추출까지 쓰고 싶을 때 | `snap = api.import_xml(company_xml)`<br>`d = api.Design(); d.start_from_snapshot(snap)` | [[drawio-format-and-alignment]], [[snapshot-model]] |

## 2. 사용 라이브러리와 모듈별 의존성 매트릭스 (`pyproject.toml`)

핵심 그래프·배치·설계·검토·CloudFormation 모듈은 **외부 라이브러리 없이 Python 3.11+ 표준 라이브러리만**으로 동작한다.

| 구분 | 패키지 / 자산 | 버전 | 실제 import하는 모듈 (`src/diagram_bot/`) | 역할 및 대체 가능 여부 |
| --- | --- | --- | --- | --- |
| **무의존 코어** | Python 표준 라이브러리 (`ipaddress`, `hashlib`, `re`, `json`, `dataclasses`, `xml.etree.ElementTree`) | `>=3.11` | `graph.py`, `views.py`, `layout/`, `findings.py`, `iac/`, `diff.py`, `combine.py` | 그래프 조인, 보기 필터, 결정적 좌표·직교 경로 계산, 47개 검토 규칙, CloudFormation 생성 |
| **필수 1 (XML 보안)** | `defusedxml` | `>=0.7,<1` | `drawio_spec.py`(`read_document`), `xml_align.py`, `drawio.py` | 외부/사용자 XML 파싱 시 XXE·엔티티 폭탄 차단. `drawio_spec.py`만 쓰면(규격·분류·입력 관문) 유일한 외부 패키지 |
| **필수 2 (AWS 호출)** | `boto3` (`botocore`) | `>=1.35,<2` | `sessions.py`, `collector.py`, `aws_access.py`, `sso.py`, `egress.py`, `agent.py`. `xml_align.py`도 `samples` → `collector`를 거쳐 import 시점에 불러온다(호출은 안 함) | 읽기 전용 AWS 수집, Identity Center device code, `before-send` 훅 기반 브라우저 egress 서명 |
| **필수 3 (웹 서버)** | `fastapi`, `uvicorn` (`pydantic`, `starlette`) | `>=0.115,<1`, `>=0.30,<1` | `server.py`, `routes/*.py` | HTTP 라우터·요청 검증. **라이브러리 모드(`api.py`)로만 쓸 때는 설치 불필요** |
| **선택 (AI 에이전트)** | `strands-agents` (`pip install '.[agent]'`) | `>=1.61,<2` | `agent.py`, `routes/agent.py` | Bedrock 모델 라우팅(`ModelRouter` 폴백), `@tool` 스키마 생성, `SequentialToolExecutor`. 회사 자체 에이전트 루프가 있으면 `designer.Design` 메서드만 직접 도구로 등록 가능 |
| **프런트엔드** | 빌드 없는 순수 ES 모듈 | 외부 npm 0개 | `static/js/http.js`, `relay.js`, `labels.js`, `editor-bridge.js` | `fetch`·`postMessage` 표준 Web API만 사용. React + Vite 프로젝트에 그대로 복사 가능 |
| **외부 정적 자산** | 고정 draw.io 편집기 (`jgraph/drawio` `draw.war`) | `32.4.1` (SHA-256 고정) | `editor.py` (`diagram-bot setup-editor`) | 오프라인 내장 편집기. 사내 CDN/정적 경로에 한 번 풀어 두고 iframe으로 로드 |

## 3. 원하는 만큼만 가져가는 5단계(Level 0~4) 포팅 레시피

- 실행 키트: [docs/porting/README.md](../porting/README.md) — 카드 T0–T6·RULES·ORCHESTRATOR(GPT-6 Luna·GPT-6.1 Sol).
- 복사: `python scripts/port_copy.py --level N --dest <회사>/diagram_bot` — import를 따라 목록을 계산(아래 표는 요약, 정확한 목록은 `--dry-run`).
- 검증: `python -m diagram_bot.port_check --level N` (오프라인, AWS·네트워크 없음). 패키지 이름 `diagram_bot`은 유지한다(`report.py`가 패키지 자원을 이름으로 찾는다).

| 단계 | 목적 | 복사할 파일 (`src/diagram_bot/` 기준) | 필요한 pip 패키지 | 진입점 함수 / 클래스 |
| --- | --- | --- | --- | --- |
| **Level 0. 기존 `drawio.xml` 자동 정렬 + 회사 규격(`DrawioSpec`)** | 회사 기존 XML 생성기는 그대로 두고, 결과 XML의 겹침·컨테이너 크기·AZ 서브넷 정렬·직교 배선만 자동 교정하거나 회사 XML 포맷 추출 | `drawio_spec.py`, `errors.py`, `xml_align.py`, `drawio.py`, `layout/`, `views.py`, `graph.py`, `snapshot.py`, `designer.py`, `findings.py`, `combine.py`, `samples/`, `collector.py`, `sessions.py`, `egress.py` (`xml_align`의 import 범위) | `defusedxml`, `boto3`(import 시점에 로드) | `xml_align.align_drawio_xml`, `xml_align.inspect_drawio_xml`, `xml_align.import_drawio_xml`, `drawio_spec.spec_from_xml` |
| **Level 1. 다이어그램·설계·검토·CFN 코어 엔진** | 표준 아키텍처 생성 + 코드/도구 기반 설계(`Design`) + 회사 규격 XML 출력 + 주석 보존 + 47개 검토 규칙 + CloudFormation 내보내기 | Level 0 + `iac/`, `diff.py` | `defusedxml`, `boto3` | `designer.Design`, `drawio.generate_document(..., spec=...)`, `drawio.carry_annotations`, `findings.review`, `iac.template` |
| **Level 2. AWS 수집 + 브라우저 Egress + 통합 퍼사드** | Level 1 + 실제 계정 읽기 전용 수집 + 서버 서명·브라우저 전송(`--aws-egress browser`) + `api.py` 단일 진입점 | Level 1 + `collector.py`, `sessions.py`, `egress.py`, `aws_access.py`, `sso.py`, `expiry.py`, `store.py`, `fileio.py`, `validation.py`, `api.py` + 프런트 `static/js/relay.js` | `defusedxml`, `boto3` | `api.collect_snapshot`, `api.render`, `api.align_xml`, `api.import_xml`, `egress.BrowserRelay`, 프런트 `startRelay(api)` |
| **Level 3. FastAPI 라우터 장착** | Level 2 + 회사 FastAPI 앱에 `/diagram/*` 엔드포인트(`/api/xml/align`, `/api/xml/import` 포함)와 리비전 저장소 장착 | Level 2 + `auth.py`, `routes/__init__.py`, `routes/snapshots.py`, `routes/documents.py`, `routes/standards.py`, `routes/aws.py` | + `fastapi`(`pydantic` 포함). `uvicorn` 불필요, `httpx2`는 port_check L3 검사용 | `routes.Context`, `snapshots.router(ctx)`, `documents.router(ctx)`, `aws.router(ctx)` |
| **Level 4. AI 채팅 설계 에이전트 + 웹 UI 연동** | Level 3 + Strands/Bedrock 29개 도구 에이전트(표준·브라운필드·하위 모델 지원) + React 연동 모듈 | Level 3 + `agent.py`, `routes/agent.py` + 프런트 `static/js/http.js`, `static/js/labels.js`, `static/editor-bridge.js` | + `strands-agents` | `agent.Workspace`, `agent.Chat`, `routes.agent.router(ctx)` |

### 코드 예시 1 (Level 0) — 회사 기존 `drawio.xml` 자동 정렬 + 규격 추출
`api.py`는 Level 2부터 복사되므로 Level 0은 모듈을 직접 부른다(`api.align_xml` 등은 같은 함수의 별칭).
```python
from diagram_bot.designer import Design
from diagram_bot.drawio import generate_document
from diagram_bot.drawio_spec import spec_from_xml
from diagram_bot.xml_align import align_report, import_drawio_xml, inspect_drawio_xml

# 1) 기존 drawio.xml 정렬 (셀 ID·라벨·사용자 속성 유지). 입력보다 나빠지는 페이지는 'reverted'로 원본 유지
before_report = inspect_drawio_xml(company_xml)        # {'clean': False, 'overlaps': [('a', 'b')], 'out_of_bounds': [...], 'pages': [...], ...}
result = align_report(company_xml)                     # {'xml': ..., 'pages': [{'name', 'status': 'aligned'|'clean'|'reverted', ...}]}
after_report = inspect_drawio_xml(result["xml"])       # 문제 목록은 개수가 아니라 셀 ID 쌍·목록

# 2) 회사 샘플 XML에서 회사 draw.io 규격(래퍼·폰트·컨테이너색·격리 서브넷색·화살표) 추출
company_spec = spec_from_xml(company_xml)

# 3) 회사 XML → Snapshot → 브라운필드 설계 → 회사 규격으로 다시 그리기
snapshot = import_drawio_xml(company_xml, title="사내 표준 아키텍처")
design = Design()
design.start_from_snapshot(snapshot)
if "data" not in design.vpcs["corp-prod-vpc"]["tiers"]:  # data 계층이 없는 템플릿일 때만
    design.add_subnet_tier(vpc="corp-prod-vpc", tier="data")
design.add_cache(vpc="corp-prod-vpc", name="session-redis")
new_xml = generate_document(design.snapshot(), vpc_id="vpc-corp-prod-vpc", view="detail", spec=company_spec)
```

### 코드 예시 2 (Level 1) — `drawio.xml` 직접 조작을 `Design` + `generate_document`로 대체
```python
from diagram_bot.designer import Design
from diagram_bot.drawio import carry_annotations, generate_document
from diagram_bot.findings import review
from diagram_bot.iac import template as export_cfn

# 1) 표준 아키텍처 불러오기 + 코드 레벨에서 리소스 추가 (좌표 계산 불필요)
d = Design(title="사내 표준 3계층 + 캐시", region="ap-northeast-2")
d.apply_standard("three-tier")                         # 표준 템플릿 로드 (AWS raw 형식)
vpc = d.vpc_summary()[0]                                # {"name": "prod-web", "id": "vpc-...", ...}
d.add_cache(vpc=vpc["name"], name="session-redis")    # 원하는 리소스 추가 -> AZ별 서브넷·SG 자동 연결
snapshot = d.snapshot()

# 2) 회사 규격("clean", "mxcell", 또는 DrawioSpec)으로 결정적 배치 XML 생성
xml_detail = generate_document(snapshot, vpc_id=vpc["id"], view="detail", spec="clean")

# 3) 사용자가 draw.io에서 수동으로 추가한 메모·도형을 유지하며 재배치할 때
merged_xml = carry_annotations(saved_user_xml, xml_detail)

# 4) 같은 스냅샷에서 아키텍처 검토(47개 규칙)와 CloudFormation 템플릿 즉시 생성
findings = review(d.graph(), snapshot.get("coverage", []))
cfn_json = export_cfn(snapshot)
```

## 4. 백엔드 장착(Level 2~3)과 교체할 경계 3곳

**A. FastAPI 라우터 통째 장착 (가장 빠름)**
```python
from diagram_bot.auth import AuditLog
from diagram_bot.aws_access import AwsAccess
from diagram_bot.routes import Context, agent, aws, documents, install_error_handlers, snapshots
from diagram_bot.store import DocumentStore, SnapshotStore

context = Context(snapshots=SnapshotStore(DATA), documents=DocumentStore(DATA),
                  aws=AwsAccess("browser", sso_start=(START_URL, "us-east-1")),
                  audit=AuditLog(DATA / "audit.log"), token=PAGE_TOKEN)
install_error_handlers(app)                      # NotFound → 404, Conflict → 409
for module in (snapshots, documents, agent, aws):
    app.include_router(module.router(context), prefix="/diagram")
```
- **필수 전제:** 회사 인증 미들웨어가 `request.state.user`(문자열 ID, 팀 모드면 `request.state.role`도)를 채워야 한다.
- **권한 검사는 라우터가 아니라 미들웨어:** 회사 미들웨어가 변경 요청마다 `auth.allowed(role, method, path)`를 불러 거부 시 403을 내야 한다. `path`는 마운트 접두사(`/diagram`)를 뗀 값이다. 접두사가 붙은 채면 관리자 전용 경로(`/api/collections`, `/api/sso`, `/api/standards`)를 알아보지 못해 편집자가 수집을 시작할 수 있다. 라우터 자체는 `make_default`만 막는다.
- 이 앱의 보안 미들웨어(페이지 토큰 `X-Diagram-Bot`, Origin 확인, 15 MiB 제한)는 `server.create_app`에 있다([[team-mode-security]]).

**B. 라이브러리 퍼사드만 호출 (`api.py` — 회사 기존 API 라우터 유지)**
```python
from diagram_bot import api
snapshot = api.collect_snapshot(session, region, expected_account=account, prepare=lambda s: relay.attach(s, user))
xml = api.render(snapshot, view="detail", vpc=vpc_id, routes="traffic", spec="clean")
result = api.review_snapshot(snapshot); draft = api.cloudformation(snapshot)
```

**교체할 경계 3곳 (인터페이스 계약)**
1. **AWS 인증·경로 (`aws_access.AwsAccess`)**: 사내 Identity Center 로그인 토큰이 이미 있으면 `session(user, region)`이 그 자격 증명으로 `boto3.Session`을 만들고 `self.relay.attach(session, user)`만 거치게 바꾼다([[aws-access-and-sso]], [[browser-egress]]).
2. **저장소 (`store.SnapshotStore`, `DocumentStore`)**: JSON 파일 대신 사내 DB·S3에 저장하려면 `SnapshotStore`(`save`, `get`, `list`, `seed`), `DocumentStore`(`read`, `replace`), `StandardStore`(`save`, `list`, `delete`)의 메서드 시그니처만 그대로 구현한다([[storage]]).
3. **프런트엔드 (React + Vite)**:
   - **그대로 복사할 파일**: `static/js/http.js`(`createClient`), `static/js/relay.js`(`startRelay`), `static/js/labels.js`(한국어 표시 이름 사전), `static/editor-bridge.js`(draw.io iframe `postMessage` 브리지).
   - **React 최상단 훅 한 줄**: `useEffect(() => { if (boot.egress === 'browser') startRelay(api); }, [boot.egress]);`
   - **Vite 프록시**: `server: { proxy: { '/api': 'http://127.0.0.1:8765', '/editor': 'http://127.0.0.1:8765' } }`
   - **React로 새로 짤 화면**: `static/app.js`와 `static/js/` 화면 모듈의 상태·컴포넌트([[web-app]]).

## 5. 코딩 에이전트 실행 규칙 (GPT-6 Luna · GPT-6.1 Sol)

- 에이전트별 규칙·멈춤 조건·보고 형식은 [docs/porting/RULES.md](../porting/RULES.md)가 기준이다. 요약:
  1. 카드가 지정한 파일만 읽는다. 복사한 모듈 본문·전체 repo는 읽지 않는다(Level 0만 약 17만 토큰).
  2. 파일은 `scripts/port_copy.py`로 복사한다. 코드를 다시 쓰지 않는다.
  3. `layout/` 좌표 수식은 바꾸지 않는다. 회사 포맷은 `DrawioSpec`, 기존 XML 정렬은 `align_drawio_xml`.
  4. 요청 데이터 경계: HTTP `spec`은 `request_spec`(프리셋·객체·채택된 표준 이름), XML은 `read_document`. 파일 경로는 서버 설정·CLI에서만.
  5. 검증은 `python -m diagram_bot.port_check --level N` + 회사 테스트(출력은 꼬리만).
  6. 세션 문맥 150K 상한(272K 할증 회피), 카드마다 새 세션.

## 바꿀 때 주의
- 라우터는 `request.state.user`가 없으면 동작하지 않는다(SSO·중계·채팅이 사용자 단위).
- `POST /api/xml/adopt`의 `make_default`는 `request.state.role == "admin"`일 때만 허용된다. 회사 인증 미들웨어가 역할을 채우지 않으면 단일 사용자(admin)로 간주한다(`routes.role`).
- 브라우저 egress에서 탭을 닫으면 그 탭이 가져간 읽기 요청은 다른 열린 탭으로 넘어가고, 그 밖의 요청이나 열린 탭이 없을 때는 1초 안팎에 `RelayDisconnected`로 실패한다(폴 도중 끊김 감지, 아니면 `idle` 15초). 수집 작업은 이때 실패로 끝난다.
- 새로운 리소스 종류나 관계를 추가할 때는 [[howto-add-collected-service]]의 체크리스트(`labels.js` 한국어 이름 포함)를 따른다.

## 관련
- [[drawio-format-and-alignment]] · [[architecture]] · [[snapshot-model]] · [[layout-and-drawio]] · [[chat-agent]] · [[http-api]] · [[web-app]] · [[storage]] · [[team-mode-security]]
