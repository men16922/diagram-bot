---
title: 전체 구조
summary: 수집 → 스냅샷 → 그래프 → 보기 → 배치 → draw.io의 데이터 흐름과, 라이브러리·저장소·AWS 접근·HTTP 라우터·프런트엔드로 나뉜 모듈 지도.
sources: [src/diagram_bot/api.py, src/diagram_bot/server.py, src/diagram_bot/routes/__init__.py, src/diagram_bot/store.py, src/diagram_bot/aws_access.py, src/diagram_bot/sessions.py, src/diagram_bot/snapshot.py, src/diagram_bot/egress.py]
related: [snapshot-model, collection, graph, views, layout-and-drawio, http-api, web-app, aws-access-and-sso, browser-egress, porting-guide]
updated: 2026-10-19
---
# 전체 구조

> Diagram Bot은 AWS 계정을 **읽기 전용**으로 수집해 스냅샷(JSON)으로 남기고, 그 스냅샷에서 관계 그래프·보기·결정적 배치를 계산해
> draw.io 문서로 그린다. 검토·변경 비교·CloudFormation 초안·채팅 설계는 모두 같은 스냅샷 위에서 동작한다.

## 데이터 흐름

```
 AWS API ──(boto3, 읽기 전용)──▶ collector.collect ──▶ snapshot {metadata, coverage, raw}
                                                          │ graph.build_graph
                                                          ▼
                                                 graph {nodes, edges}
            ┌───────────────┬───────────────┬────────────┼───────────────┬──────────────────┐
            ▼               ▼               ▼            ▼               ▼                  ▼
     views.view_graph  findings.review  diff.diff   combine.combine  iac.template   designer.Design
     (보기·개수)        (검토 규칙)      (변경 비교)  (계정 통합)       (CFN 초안)      (설계안 = 같은 raw 형식)
            │
            ▼
     layout.layout (결정적 배치) ──▶ drawio.generate_document ──▶ draw.io XML ──▶ 웹 편집기 / PNG / .drawio
```

- **스냅샷이 유일한 계약이다.** 원본 API 응답(`raw`)과 호출 결과(`coverage`)를 보존하고, 그래프는 언제든 `raw`에서 다시 만든다([[snapshot-model]]).
- **설계안도 스냅샷이다.** `designer.Design`은 수집기와 같은 `raw` 형식을 만들어서, 그리기·검토·내보내기가 실제 계정과 똑같이 동작한다([[chat-agent]]).
- **관계는 설정 근거다.** ID·ARN과 API 필드로만 연결하며, 실제 통신을 추정하지 않는다([[graph]]).

## 계층과 모듈

| 계층 | 모듈 | 역할 | 바꿔 끼우는 지점 |
| --- | --- | --- | --- |
| 라이브러리 | `api.py` | 웹 없이 쓰는 공개 함수: `collect_snapshot`, `render`, `view`, `review_snapshot`, `diff_snapshots`, `combine_snapshots`, `cloudformation`, `Design`, `BrowserRelay` | 회사 백엔드가 직접 import |
| 수집 | `collector.py` | 서비스별 읽기 호출, 페이지·부분 실패 처리, `prepare` 훅 | — |
| 모델 | `snapshot.py`, `graph.py`, `views.py` | 스냅샷 봉투, 노드·관계 생성, 보기별 선택과 개수 | — |
| 출력 | `layout.py`, `drawio.py`, `report.py` | 배치, draw.io XML, 단독 HTML 보고서 | — |
| 분석 | `findings.py`, `diff.py`, `combine.py`, `iac/` | 검토, 변경 비교, 계정 통합, CloudFormation | — |
| 설계·채팅 | `designer.py`, `agent.py` | 표준·브라운필드 설계, Strands + Bedrock 에이전트 | 모델 목록(`MODELS`) |
| 저장소 | `store.py` | `SnapshotStore`, `DocumentStore`(리비전·이력) | **DB·S3로 교체** ([[storage]]) |
| AWS 접근 | `aws_access.py`, `sessions.py`, `sso.py`, `egress.py` | Identity Center 로그인, 역할 세션, 서버/브라우저 egress | **회사 인증으로 교체** ([[aws-access-and-sso]]) |
| HTTP | `routes/*.py`, `server.py` | 도메인별 `APIRouter`, 미들웨어·조립 | **회사 FastAPI에 라우터 장착** ([[http-api]]) |
| 팀 모드 | `auth.py`, `oidc.py` | 앱 계정·역할·OIDC·감사 로그 | 회사 SSO 미들웨어 ([[team-mode-security]]) |
| 프런트엔드 | `static/app.js`, `static/js/*.js`, `editor-bridge.js` | 콘솔형 화면, API 클라이언트, 브라우저 중계, draw.io 연동 | **React 컴포넌트로 이식** ([[web-app]]) |
| 실행 | `cli.py`, `editor.py` | 명령줄, draw.io 편집기 설치 | — |

`server.create_app()`은 `routes.Context`(저장소, AWS 접근, 감사 로그, 인증, 페이지 토큰)를 만들고 미들웨어와 라우터 다섯 개(`session`, `snapshots`,
`documents`, `agent`, `aws`)를 붙일 뿐이다. 다른 FastAPI 앱은 같은 `Context`로 필요한 라우터만 `include_router`하면 된다.

## 실행 형태

- **로컬(기본):** `diagram-bot serve` — 127.0.0.1만, 사용자 한 명, 서버가 AWS를 직접 호출.
- **팀 모드:** `--users`/`--oidc-config` + TLS + `--allowed-host` — 계정·역할·감사 로그([[team-mode-security]]).
- **브라우저 egress:** `--aws-egress browser` — 서버는 서명만, AWS 호출은 사용자 PC에서([[browser-egress]]).
- **CLI·CI:** `collect`, `review --fail-on`, `diff --fail-on-change`, `export-cfn`([[cli]]).

## 바꿀 때 주의
- 스냅샷 형식(`snapshot.SCHEMA_VERSION`, 봉투는 `snapshot.make_snapshot`)을 바꾸면 저장된 스냅샷 호환(`graph.upgrade_graph`, `store.validate_snapshot`)과 [[snapshot-model]]을 함께 고친다.
- 검증 묶음: `make check`(오프라인 게이트), `scripts/verify_demo.py`(브라우저 35개 확인), `scripts/verify_team.py`, 실제 AWS는 `scripts/verify_browser_egress.py`([[cli]]).

## 관련
- [[porting-guide]] — 회사 스택(React + Vite, FastAPI, 브라우저 egress)으로 옮기는 순서.
- [[glossary]] — 용어.
