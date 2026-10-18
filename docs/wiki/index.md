---
title: 위키 색인
summary: Diagram Bot 위키의 모든 페이지와 한 줄 요약. 처음 읽는 사람과 LLM 에이전트의 시작점.
sources: []
related: [architecture, porting-guide, SCHEMA, log]
updated: 2026-10-18
---
# Diagram Bot 위키

> AWS 계정을 읽기 전용으로 수집해 draw.io 구성도·검토·변경 비교·CloudFormation 초안을 만드는 도구의 지식 베이스.
> 한 페이지 한 주제, 모든 사실은 코드 근거(`sources`)와 함께. 이 파일은 `python scripts/wiki_index.py`가 만든다.

처음이면 [[architecture]] → [[snapshot-model]] → 하려는 일의 페이지 순서로 읽는다. 회사 스택으로 옮기려면 [[porting-guide]].

## 시작

- [[architecture]] (architecture.md) — **전체 구조**: 수집 → 스냅샷 → 그래프 → 보기 → 배치 → draw.io의 데이터 흐름과, 라이브러리·저장소·AWS 접근·HTTP 라우터·프런트엔드로 나뉜 모듈 지도.
- [[porting-guide]] (porting-guide.md) — **회사 스택으로 옮기기 (React + Vite, FastAPI, 브라우저 egress, 회사 draw.io 규격)**: 표준 drawio.xml을 직접 고치던 회사 버전을 인플레이스 자동 정렬(align_xml)·회사 규격(DrawioSpec)·스냅샷 파이프라인으로 이식하는 코딩 에이전트용 토큰 최적화 플레이북(라이브러리 명세, Level 0~4 파일 복사 목록, 계약 치트시트 포함).
- [[glossary]] (glossary.md) — **용어**: 스냅샷·커버리지·참조 노드·보기·경로 클래스·설계안·브라운필드·브라우저 egress 등 이 저장소에서 쓰는 용어의 정확한 뜻.

## 데이터와 모델

- [[snapshot-model]] (snapshot-model.md) — **스냅샷 데이터 모델**: 스냅샷 JSON 계약 — schema_version, metadata(수집·샘플·설계·통합별 필드), coverage 행, raw 키 전체, 그래프 노드·관계 필드와 검증 규칙.
- [[collection]] (collection.md) — **읽기 전용 수집 (collector)**: collector.collect의 호출 목록(OPERATIONS)과 IAM 권한, 2단계 병렬 수집, 페이지 처리, 부분·조회 불가 기록, 계정 확인, 역할 맡기(ReadOnlyAccess 세션 정책), prepare 훅, 전역 API, 읽기 전용 정책 파일과의 관계.
- [[graph]] (graph.md) — **관계 그래프 (graph)**: raw API 데이터에서 노드·관계를 만드는 결정적 조인 규칙 — 노드 종류 45개와 관계 43개, 관계 ID 규칙, 참조 전용 노드, 서브넷 경로 분류, 고가용성 역할, 계정 통합 병합, 옛 그래프 업그레이드.
- [[views]] (views.md) — **보기 계약 (views)**: views.view_graph가 정하는 overview·detail·focus 보기의 노드·관계 선택, references 옵션, 경로 필터(routes=), 개수(visible·collected·references·hidden·filtered), 전체 구성의 네트워크·엣지 연결선, 포함 관계.
- [[layout-and-drawio]] (layout-and-drawio.md) — **배치와 draw.io 문서**: layout.py의 결정적 계층 배치(diagram-bot-layered/2 — 띠, AZ 열, 거터·채널, 전체 구성 카드, CloudFront 엣지 열, 데이터 센터 상자)와 drawio.py의 XML 생성·검증·주석 보존, editor.py의 고정 draw.io 설치, 테스트가 지키는 불변식.
- [[drawio-format-and-alignment]] (drawio-format-and-alignment.md) — **회사 draw.io 규격(DrawioSpec)과 기존 XML 자동 정렬·이식**: drawio_spec.py의 회사별 draw.io XML 포맷·컨테이너 계층·리소스 스타일 자동 추출(spec_from_xml, set_default_spec)과 xml_align.py의 회사 표준 템플릿 원클릭 표준화(adopt_company_xml), 인플레이스 정렬(align_drawio_xml), 스냅샷 역변환(import_drawio_xml).
- [[samples]] (samples.md) — **샘플과 데모 스냅샷**: samples.Sample 빌더 도우미, 번들 샘플 11개(무엇을 보여 주는지·리전), SAMPLE_VERSION과 서버의 샘플 재생성(SnapshotStore.seed), demo.py, scripts/render_samples.py, examples/samples.

## 분석과 출력

- [[review-rules]] (review-rules.md) — **아키텍처 검토 규칙**: findings.py의 review(graph, coverage)가 수집된 API 필드만으로 판정하는 47개 규칙(안정성·보안·운영·AI/ML·비용), 결과 필드, 수집 공백 처리, review --fail-on 게이트.
- [[change-review]] (change-review.md) — **변경 비교 (diff)**: diff.py가 두 스냅샷의 그래프를 노드 ID·관계 ID로 맞춰 리소스 추가·삭제, 설정 필드 변경, 관계 추가·삭제를 내는 방식과 CLI diff --fail-on-change, /api/snapshots/{id}/diff/{other}.
- [[multi-account]] (multi-account.md) — **여러 계정 통합 보기**: combine.py의 combine_snapshots가 계정 × 리전 스냅샷을 parts로 묶는 방식, graph.merge_graphs의 공유 TGW·계정 간 피어링 합치기와 전역 ID, 전체 구성의 계정 프레임, CLI combine과 /api/snapshots/combine, 계정별 CloudFormation.
- [[cloudformation]] (cloudformation.md) — **CloudFormation 초안 내보내기**: iac/ 패키지의 Template이 스냅샷 raw를 CloudFormation JSON 초안으로 바꾸는 단계 순서, Ref/GetAtt 규칙, 매개변수, DeletionPolicy, Metadata.DiagramBot의 Unsupported·Assumptions 전체 목록, export-cfn·API·테스트.

## AWS 접근과 인증

- [[aws-access-and-sso]] (aws-access-and-sso.md) — **AWS 접근과 Identity Center 로그인**: 사용자별 AWS 세션을 만드는 한 곳(aws_access.AwsAccess) — Identity Center device code 로그인, 역할 자격 증명, 프로파일·SDK 체인, AssumeRole, egress 경로 선택.
- [[browser-egress]] (browser-egress.md) — **브라우저 egress (서버 서명, 브라우저 전송)**: 서버가 AWS에 나갈 수 없고 Permission Set이 회사 IP만 허용하는 환경에서, botocore가 만든·서명한 요청을 before-send에서 사용자 브라우저로 넘겨 PC IP에서 보내는 방식.
- [[team-mode-security]] (team-mode-security.md) — **팀 모드와 보안 경계**: 앱 로그인(로컬 계정·OpenID Connect), 세션·역할, create_app 미들웨어(TrustedHost, 페이지 토큰, Origin, 본문 제한, 보안 헤더), 감사 로그, 비루프백 TLS 요구와 저장하지 않는 비밀.

## 앱과 API

- [[http-api]] (http-api.md) — **HTTP API 레퍼런스**: create_app이 조립하는 모든 HTTP 엔드포인트(페이지·정적 파일·session·snapshots·documents·agent·aws 라우터)의 요청 모델, 응답, 상태 코드, 역할, 감사 기록.
- [[web-app]] (web-app.md) — **웹 앱 (프런트엔드)**: 빌드 없는 ES 모듈 프런트엔드(app.html, 진입 app.js, js/ 화면 모듈 10개와 http.js·relay.js·labels.js)의 화면 구조, state 객체와 주요 흐름, draw.io 임베드 메시지 규약, React로 옮길 때 그대로 쓸 모듈과 다시 짤 부분.
- [[storage]] (storage.md) — **저장소 (스냅샷·편집본)**: store.py의 SnapshotStore(검증·그래프 재생성·캐시·목록·샘플 시드)와 DocumentStore(보기 범위 키, 리비전 낙관적 잠금, 20개 이력, replace 의미), 데이터 디렉터리 위치, DB·S3로 바꿀 때 구현할 메서드.
- [[chat-agent]] (chat-agent.md) — **채팅 설계 에이전트**: agent.py(모델 목록·대체, SYSTEM_PROMPT, Workspace, 도구 29개, Chat.ask와 진행 이벤트)와 designer.py(Design 상태, 표준 아키텍처, 기존 구성에서 시작하는 brownfield 설계, 스냅샷 메타데이터), CLI chat.
- [[cli]] (cli.md) — **CLI와 검증 스크립트**: diagram-bot CLI(cli.py)의 모든 하위 명령·옵션·종료 코드, --env-file 동작, Makefile 게이트(check·lint·test·smoke-local)와 scripts/의 검증·렌더링 스크립트.

## 작업 방법

- [[howto-add-collected-service]] (howto-add-collected-service.md) — **수집 서비스 추가 방법**: 새 AWS 읽기 호출을 수집기부터 정책·그래프·라벨·보기·배치·검토·CloudFormation·샘플·테스트·문서까지 잇는 순서. Direct Connect와 Elastic IP를 추가한 방식을 기준으로 한다.
- [[howto-add-review-rule]] (howto-add-review-rule.md) — **검토 규칙 추가하기**: findings.py에 새 검토 규칙을 넣는 순서 — 위치, 규칙 ID·심각도·영역 관례, 근거·권장 조치 문구, ARCHITECTURE.md 카탈로그, tests/test_findings.py 패턴, 샘플이 review --fail-on medium을 통과해야 하는 조건.
- [[howto-add-chat-tool]] (howto-add-chat-tool.md) — **채팅 도구 추가하기**: 설계 단계(Design 메서드)와 그 단계를 부르는 Strands 도구를 추가하는 절차. add_fsx_lustre·add_subnet_tier를 본보기로 DesignError 메시지, record(), @tool docstring, 도구 수 단언, SYSTEM_PROMPT, CloudFormation 범위, 테스트를 다룬다.

## 위키 관리

- [[SCHEMA]] (SCHEMA.md) — **위키 작성 규칙**: 이 위키 페이지의 형식, 근거 확인, 색인·변경 기록 갱신 규칙. 사람과 LLM 에이전트 모두 이 규칙으로 페이지를 쓰고 고친다.
- [[log]] (log.md) — **위키 변경 기록**: 위키 페이지를 추가·삭제·크게 고친 기록(최신이 위). 코드 변경 이력은 CHANGELOG.md.
