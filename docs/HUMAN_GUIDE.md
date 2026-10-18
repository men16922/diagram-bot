# 사람용 통합 가이드 (High-Level Overview & 지시서)

> **이 문서는 사람(개발자·테크리드·기획자)이 전체 구조를 5분 만에 파악하고, 회사 코딩 에이전트에게 바로 작업을 지시할 수 있도록 만든 고수준(High-Level) 가이드입니다.**
>
> 세부 함수 시그니처, 파일별 의존성, 코드 근거는 코딩 에이전트가 토큰을 아끼며 읽을 수 있도록 [`llms.txt`](../llms.txt)와 [`docs/wiki/`](wiki/index.md)에 최적화되어 있습니다.

---

## 1. 한눈에 보는 핵심 해결책: 회사 기존 버전 vs 이 저장소

현재 회사 버전은 **표준 아키텍처 `drawio.xml`을 가지고 코드 레벨에서 직접 좌표를 계산·정렬**하고 있습니다. 이 방식은 리소스가 조금만 늘어나거나 바뀔 때 도형이 겹치고, 컨테이너(VPC·AZ·서브넷) 밖으로 삐져나가며, 화살표가 아이콘이나 제목을 뚫고 지나가는 문제가 생깁니다. 또한 회사마다 원하는 draw.io XML 포맷(래퍼 태그, 상단 배너 유무, 폰트, 색상, 화살표 모양)이 다릅니다.

이 저장소는 회사의 기존 작업을 버리지 않고 **원클릭 통합 개편(`adopt_xml`)을 포함한 4가지 방식**으로 즉시 연결해 문제를 해결합니다:

```
[방식 0. 회사 표준 drawio.xml 파일 하나로 전체 포맷·레이아웃·표준 템플릿 완전 개편 (원클릭)]
  회사 표준 템플릿 (company_standard.drawio)
       │
       ▼  api.adopt_xml("company_standard.drawio", name="company-std", set_default=True)
          (또는 CLI: diagram-bot adopt-template company_standard.drawio -o reports/company;
           HTTP는 요청마다 결과만, 서버 기본값 변경은 admin의 make_default=true;
           웹 앱은 도구 › draw.io XML › 사내 표준으로 채택)
  ① 회사 XML(모든 페이지)의 포맷(래퍼 태그, Cloud/Region/AZ 상자 유무, 서브넷 라벨 표기, 컨테이너·리소스별 스타일·폰트·화살표) 추출 → 기본값으로 설정
     (라이브러리·CLI는 그 프로세스에만; 서버에서 admin이 기본 규격으로 지정한 표준은 데이터 디렉터리에 저장되어 재시작 뒤에도 유지)
  ② 원본 XML의 도형 겹침·컨테이너 경계·AZ 서브넷 계층·직교 배선을 자동 교정한 aligned.drawio 생성!
  ③ 회사 템플릿을 사내 표준 아키텍처("company-std")로 자동 등록 → Design.apply_standard("company-std")로 불러와
     리소스를 추가·변경해도 항상 회사 포맷 + 자동 레이아웃으로 출력!

[방식 A. 기존 회사 XML 생성기 유지 + 자동 정렬 필터만 장착]
  회사 기존 코드가 만든 어수선한 drawio.xml
       │
       ▼  api.align_xml(xml, mode="preserve")
  셀 ID·라벨·사용자 속성은 유지하면서(엣지 스타일은 직교 경로·포트로 다시 씀)
  도형 겹침 제거 + AZ 서브넷 계층 행 맞춤 + 컨테이너 크기 조절 + 직교 화살표 배선.
  정렬하면 더 나빠지는 페이지는 원래대로 돌려주고 align_report의 status로 알려 줌

[방식 B. 회사 draw.io 규격(DrawioSpec) 자동 추출 및 적용]
  회사 표준 샘플 drawio.xml 1개
       │
       ▼  spec = api.spec_from_xml(sample_xml)  (또는 환경변수 DIAGRAM_BOT_SPEC=company.drawio)
  회사 래퍼(<mxCell> vs <object>)·Cloud/Region/AZ 상자 유무·폰트·컨테이너/리소스 색상·화살표 규격 자동 추출
       │
       ▼  api.render(snapshot, ..., spec=spec)
  Diagram Bot의 모든 결과물이 회사 draw.io 규격과 똑같은 포맷으로 출력!

[방식 C. 회사 표준 drawio.xml → 스냅샷 역변환 및 고도화]
  회사 표준 drawio.xml
       │
       ▼  snapshot = api.import_xml(company_xml)
  Diagram Bot 표준 Snapshot(JSON)으로 즉시 변환
       ├──▶ Design.start_from_snapshot(snapshot) : 코드/AI 채팅으로 리소스 추가 (좌표 계산 불필요)
       ├──▶ api.review_snapshot(snapshot)        : 47개 AWS 모범사례·보안 규칙 자동 점검
       ├──▶ api.diff_snapshots(before, after)    : 아키텍처 변경점 비교
       └──▶ api.cloudformation(snapshot)         : 배포 가능한 CloudFormation(JSON) 템플릿 초안 생성
```

**웹 앱에서 바로 쓰기 (코드 없이):** 방식 0·A·C는 API·CLI만이 아니라 화면에도 있습니다. `diagram-bot serve`로 연 앱의 사이드 내비 `도구 › draw.io XML`에서
회사 `.drawio` 파일 하나를 골라 `스냅샷으로 가져오기`(방식 C), `자동 정렬`(방식 A, 전후 지표와 정렬본 다운로드), `사내 표준으로 채택`(방식 0, 관리자는
서버 기본 규격으로 지정)을 누르면 됩니다. 채택한 사내 표준은 같은 대화상자 목록에서 열거나 지울 수 있고, `다운로드 › 규격`에서 프리셋·사내 표준 규격으로
.drawio·PNG를 내보내며(방식 B), AI 어시스턴트에서 "corp-web 표준으로 설계해줘"처럼 불러옵니다. 팀 모드 관리자는 프로필 메뉴 `감사 로그`로 누가 무엇을 했는지 봅니다.

---

## 2. 원하는 만큼만 가져가는 5단계 모듈 지도 (Level 0 ~ Level 4)

**원하는 단계(Level) 하나만 골라** 코딩 에이전트에게 지시합니다. 단계별 복사 파일·필요 패키지·진입점: [porting-guide](wiki/porting-guide.md) 2·3절.

| 단계 | 무엇을 가져가나? | 어떤 상황에 추천하나? |
| --- | --- | --- |
| **Level 0** | **기존 `drawio.xml` 진단·정렬기 + 회사 템플릿 표준화(`adopt_xml`, `DrawioSpec`)** | 회사 표준 `drawio.xml` 파일 기준으로 포맷을 통일하고, **깨지는 정렬·컨테이너 크기·화살표 배선을 고치고 싶을 때** |
| **Level 1** | **다이어그램·설계(`Design`)·검토(`review`)·CloudFormation(`iac`) 코어 엔진** | XML 직접 조작 대신 `d.apply_standard("company-std")`, `d.add_load_balancer(...)`, `d.add_cache(...)` 같은 **의도 단위 코드로 아키텍처를 조립**하고, 보안 점검과 CFN 내보내기까지 쓰고 싶을 때 |
| **Level 2** | **실제 AWS 계정 읽기 전용 수집 + 브라우저 Egress (`BrowserRelay`)** | 사내 방화벽·IP 제한(`aws:SourceIp`) 환경에서 **서버가 서명하고 사용자 브라우저가 AWS API를 호출**해 실제 계정을 구성도로 그리고 싶을 때 |
| **Level 3** | **FastAPI 백엔드 라우터 + 리비전 저장소** (`/api/snapshots`, `/api/xml/adopt`, `/api/xml/align`, `/api/xml/import` 등) | 회사의 기존 FastAPI 서버에 `/diagram/*` API 엔드포인트들을 통째로 마운트하고 싶을 때 |
| **Level 4** | **AI 채팅 설계 에이전트 + React 프런트엔드 브리지** | **GPT-6 Luna나 Claude 5.5 Haiku 같은 하위/경량 모델**로도 환각 없이 표준 아키텍처 설계·브라운필드 수정이 되는 AI 어시스턴트를 붙이고 싶을 때 |

---

## 3. 내일 회사 코딩 에이전트에게 바로 복붙할 작업 지시서 (프롬프트 5종)

내일 회사에서 이 저장소를 압축 해제(또는 클론)한 뒤, 회사 코딩 에이전트(Claude Haiku, GPT-6 Luna, Cursor, Windsurf, Codex 등)에게 **아래 프롬프트 중 원하는 시나리오 하나를 그대로 복사해서 전달**하세요.

### 시나리오 ①: 회사 기존 `drawio.xml` 정렬 문제를 즉시 해결하고 싶을 때 (Level 0)
```text
우리 회사 레포의 drawio.xml 정렬/배선 문제를 해결하기 위해 다운로드한 diagram-bot 레포의 Level 0 모듈을 이식해줘.
1. 먼저 diagram-bot/docs/wiki/porting-guide.md 와 diagram-bot/docs/wiki/drawio-format-and-alignment.md 두 문서만 읽어. (전체 코드 스캔 금지)
2. porting-guide.md의 Level 0 표에 적힌 파일들만 우리 프로젝트 패키지로 복사해.
3. 우리 회사 코드에서 drawio.xml을 최종 반환하기 직전에 `align_report(xml_text, mode="preserve")`를 통과하도록 연결하고, 결과의 `xml`을 쓰고 `pages[*].status`(clean·aligned·reverted)를 로그로 남겨줘. (셀 ID·라벨·사용자 속성은 유지, 나빠지는 페이지는 원래대로 돌아옴)
4. diagram-bot/tests/test_xml_align.py 와 tests/test_xml_robustness.py 를 참고해서 우리 회사 샘플 drawio.xml로 정렬 전후(inspect_drawio_xml) 문제 수가 늘지 않는지 검증해줘. 우리 파일의 구조에 따라 clean=True가 안 나올 수 있으니 남은 문제 목록을 보고해줘.
```

### 시나리오 ②: 우리 회사 표준 아키텍처 `drawio.xml` 파일을 기준으로 전체 포맷·레이아웃·표준 템플릿을 완전 개편할 때 (가장 추천)
```text
우리 회사의 표준 아키텍처 템플릿 XML 파일(<여기에 회사 .drawio 파일 경로 입력>)을 기준으로 diagram-bot이 우리 회사 포맷과 레이아웃 규칙으로 동작하도록 개편해줘.
1. diagram-bot/docs/wiki/drawio-format-and-alignment.md 를 읽어.
2. 앱 시작 시점(또는 모듈 초기화 시점, 서버 설정 코드에서만; 요청 데이터의 경로를 넘기지 말 것)에 우리 회사의 표준 XML 파일을 `api.adopt_xml("<회사_템플릿.drawio>", name="company-standard", set_default=True, save_spec="config/company_spec.json")`으로 호출하도록 연결해줘. 기본 표준 이름(three-tier 등)은 쓸 수 없어.
   - 이렇게 하면 우리 회사 XML의 래퍼(<mxCell> vs <object>), 외곽 Cloud/Region/AZ 상자 사용 여부, 서브넷 한 줄 라벨 표기법, 컨테이너·리소스별 색상/폰트/화살표가 자동으로 기본 규격(`DrawioSpec`)으로 고정되고,
   - 우리 회사 템플릿이 `"company-standard"`라는 이름의 표준 아키텍처로 등록되어 `Design.apply_standard("company-standard")` 및 `add_*` 메서드로 리소스를 추가해도 우리 회사 포맷과 자동 레이아웃으로 렌더링돼. 앱 편집기에서 여는 문서는 선택에 필요한 `object` 래퍼와 `resourceId`를 유지해.
   - 템플릿의 라벨로 리소스를 추론하므로, 가져온 스냅샷의 `metadata.import.unmapped`·`synthesized`를 확인해줘.
3. `tests/test_xml_align.py::test_adopt_company_xml_overhauls_format_and_registers_standard` 를 참고해서 우리 회사 XML 파일로 테스트를 작성하고 통과하는지 확인해줘.
```

### 시나리오 ③: 회사 표준 `drawio.xml`을 가져와 코드/AI로 리소스를 추가하고 검토·CFN까지 뽑고 싶을 때 (Level 1)
```text
우리 회사의 정적 표준 아키텍처 drawio.xml 파일들을 diagram-bot의 Snapshot 파이프라인(Level 1)으로 이식해줘.
1. diagram-bot/docs/wiki/porting-guide.md 와 diagram-bot/docs/wiki/drawio-format-and-alignment.md 를 읽어.
2. Level 1 모듈들을 우리 백엔드에 복사해.
3. 우리 회사의 기존 표준 drawio.xml을 `api.import_xml(xml_text)`로 가져와서 Snapshot JSON으로 변환하거나, 자주 쓰는 표준 패턴은 `samples/` 패키지의 `Sample` 빌더 형식으로 `designer.STANDARDS`에 등록해줘.
4. 이제 XML 좌표를 직접 건드리지 말고 `Design.start_from_snapshot(snapshot)`과 `add_*` 메서드로 리소스를 추가한 뒤, 회사 규격(`spec`)으로 렌더링하고 `review_snapshot`(아키텍처 점검)과 `cloudformation`(IaC 템플릿)까지 출력되도록 연결해줘.
```

### 시나리오 ④: 회사 React + Vite 프런트엔드와 FastAPI 백엔드(브라우저 Egress 포함)에 붙일 때 (Level 2~3)
```text
diagram-bot의 AWS 수집·브라우저 Egress·다이어그램 편집기 기능을 우리 회사 FastAPI + React(Vite) 프로젝트에 포팅해줘.
1. diagram-bot/llms.txt 를 먼저 보고, docs/wiki/porting-guide.md, docs/wiki/browser-egress.md, docs/wiki/http-api.md 만 필요한 순서대로 읽어.
2. 백엔드에는 Level 3 모듈을 복사하고, `routes.Context`를 생성해 `snapshots`, `documents`, `aws` 라우터를 마운트해줘. 우리 사내 인증 미들웨어가 `request.state.user`를 세팅하도록 연결해.
3. 프런트엔드(React + Vite)에는 `static/js/http.js`·`relay.js`·`labels.js`와 `static/editor-bridge.js` 4개 파일을 그대로 복사하고, 최상단 컴포넌트 마운트 시 `startRelay(api)`가 돌도록 훅을 추가해줘.
4. pytest 로 `test_api.py`, `test_xml_align.py`, `test_egress.py` 가 통과하는지 확인해줘.
```

### 시나리오 ⑤: GPT-6 Luna / Claude 5.5 Haiku 같은 하위 모델로 AI 아키텍처 설계 챗봇을 붙일 때 (Level 4)
```text
diagram-bot의 AI 아키텍처 설계 에이전트(agent.py, designer.py)를 우리 프로젝트에 연결해줘.
1. diagram-bot/docs/wiki/chat-agent.md 와 docs/wiki/porting-guide.md 를 읽어.
2. 모델이 직접 XML이나 좌표를 생성하지 않고 반드시 `designer.Design`의 29개 도구만 순차 호출(`SequentialToolExecutor`)하도록 구현된 `agent.py` 구조를 그대로 사용해줘.
3. 기본 모델 또는 폴백 모델에 우리 사내에서 쓰는 경량 모델(예: GPT-6 Luna, Claude 5.5 Haiku, Nova 2 Lite)을 설정하고, `tests/test_agent.py` 로 표준 설계·브라운필드 설계가 정상 동작하는지 검증해줘.
```

---

## 4. 왜 하위 모델(GPT-6 Luna, Claude 5.5 Haiku)도 완벽하게 포팅할 수 있나?

1. **토큰 최적화 2계층 지식 구조 (`llms.txt` + `docs/wiki/*.md`)**:
   - 코딩 에이전트가 수만 줄의 코드를 처음부터 다 읽으면 컨텍스트가 넘치고 길을 잃습니다.
   - 이 저장소는 루트의 [`llms.txt`](../llms.txt)(약 60줄)가 **전체 지도 역할**을 하고, 각 위키 페이지 상단에 `sources: [정확한 코드 경로]`와 입출력 계약이 적혀 있어 에이전트가 **딱 필요한 문서 1~2개와 대상 코드 파일만 열어 작업**합니다.
2. **단일 진입점 퍼사드 ([`src/diagram_bot/api.py`](../src/diagram_bot/api.py))**:
   - 내부 모듈이 어떻게 나뉘어 있든 외부에서는 `from diagram_bot import api` 하나로 생성·정렬·변환·검토·비교·CFN 내보내기를 모두 호출할 수 있습니다.
3. **좌표/XML과 비즈니스 로직의 완전한 분리**:
   - 에이전트(또는 하위 LLM)는 `x, y, width, height`를 계산할 필요가 전혀 없습니다.
   - 기존 XML은 `api.align_xml(xml)`이 알아서 정렬하고, 새 설계는 `Design.add_*` 메서드가 알아서 모범사례(Multi-AZ, 서브넷 분리, 보안 그룹 최소 권한)를 채운 뒤 `layout/` 패키지가 결정적 좌표를 계산합니다.

---

## 5. 실제 사용 가능한 상용 제품(Production) 고도화 로드맵

포팅 직후 실제 사내 제품으로 출시하기 위한 3단계 고도화 계획입니다:

### Phase 1. 즉시 적용 (1~2일): 회사 `drawio.xml` 품질 표준화
- **목표**: 현재 회사에서 생성·관리 중인 모든 표준 아키텍처 `drawio.xml`의 정렬 불량·선 겹침 해결 및 포맷 통일.
- **실행**:
  1. 회사 대표 `drawio.xml` 1개에서 `api.spec_from_xml()`로 사내 표준 `DrawioSpec` 추출 및 고정.
  2. 기존 XML 생성 파이프라인 끝에 `api.align_xml(xml, spec=COMPANY_SPEC, mode="preserve")` 적용.
  3. CLI(`diagram-bot align-xml <입력> -o <출력>`; 정렬 전후 겹침·범위 이탈·미배선·선 충돌 개수와 되돌린 페이지를 표준 오류에 출력)로 사내 기존 템플릿을 일괄 정렬·검증.

### Phase 2. 사내 플랫폼 결합 (1~2주): 인증·영구 저장소·웹 편집기 내재화
- **목표**: 여러 명의 사내 아키텍트·엔지니어가 동시에 접속해 실제 AWS 계정을 수집하고 다이어그램을 편집·공유하는 웹 서비스화.
- **실행**:
  1. **사내 SSO + 브라우저 Egress 연결** ([aws-access-and-sso](wiki/aws-access-and-sso.md), [browser-egress](wiki/browser-egress.md)): 사내 방화벽 뒤의 백엔드 서버는 boto3로 요청을 SigV4 서명만 하고, 사용자 PC 브라우저(`relay.js`)가 사내 공인 IP로 AWS API를 호출해 응답을 중계.
  2. **DB / S3 저장소 교체** ([storage](wiki/storage.md) "DB·S3로 바꿀 때"): 로컬 디스크의 `SnapshotStore`·`DocumentStore`·`StandardStore`를 같은 메서드의 사내 PostgreSQL/DynamoDB + S3 구현으로 교체 (리비전 낙관적 락 `409 Conflict`와 `carry_annotations` 주석 보존은 유지).
  3. **오프라인 draw.io 사내 호스팅** ([layout-and-drawio](wiki/layout-and-drawio.md)): `diagram-bot setup-editor`가 검증한 draw.io `32.4.1` 정적 에셋과 `editor-bridge.js`를 사내 CDN/Nginx에 올려 외부 인터넷 연결 없이 편집·고해상도 PNG(1×/2×/3×) 내보내기 제공.

### Phase 3. 엔터프라이즈 지능화 (2~4주): 사내 표준 카탈로그 & CI/CD 아키텍처 게이트
- **목표**: 단순 그리기를 넘어 **설계 자동화 → 사내 보안 규정 검증 → IaC 코드 생성 → 운영 드리프트 감시**의 전체 수명주기 완성.
- **실행**:
  1. **사내 표준 아키텍처 카탈로그화**: 사내 표준 `drawio.xml`들을 웹 앱 `도구 › draw.io XML`(관리자 채택은 서버에 저장)이나 `api.import_xml`로 불러오거나 `samples/` 패키지에 사내 표준 패턴(예: 사내 금융망 3-Tier, 사내 프라이빗 EKS 표준 등)으로 등록해 AI 채팅에서 `"사내 금융망 표준 불러와서 Redis 추가해줘"` 한 줄로 즉시 설계.
  2. **사내 보안·컴플라이언스 규칙 추가** ([howto-add-review-rule](wiki/howto-add-review-rule.md)): `findings.py`의 47개 AWS Well-Architected 규칙 위에 사내 전용 규칙(예: 필수 비용 태그 누락, 사내 보안 프록시 미경유, 금지된 인스턴스 패밀리 등)을 추가해 다이어그램 화면에서 즉시 빨간색/주황색 배지로 시각화.
  3. **배포 파이프라인(CI/CD) 아키텍처 게이트** ([cli](wiki/cli.md)): 인프라 변경 전후에 `diagram-bot review --fail-on high`(종료 코드 3)와 `diagram-bot diff --fail-on-change`(종료 코드 4)를 실행해 단일 장애점(SPOF)이나 보안 위반이 생겼을 때 PR 단계에서 자동 알림·차단.
