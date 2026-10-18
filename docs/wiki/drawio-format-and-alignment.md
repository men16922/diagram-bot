---
title: 회사 draw.io 규격(DrawioSpec)과 기존 XML 진단·정렬·가져오기
summary: drawio_spec.py의 검증된 회사 규격(DrawioSpec, spec_from_xml, request_spec)·입력 관문(read_document)·공용 분류표(classify_cell)와 xml_align.py의 진단(inspect), 품질 보호 정렬(align_report), 여러 페이지 가져오기(import), 템플릿 채택(adopt)과 그 HTTP·CLI 진입점.
sources: [src/diagram_bot/drawio_spec.py, src/diagram_bot/xml_align.py, src/diagram_bot/drawio.py, src/diagram_bot/layout/, src/diagram_bot/designer.py, src/diagram_bot/api.py, src/diagram_bot/routes/documents.py, src/diagram_bot/cli.py, tests/test_xml_align.py, tests/test_xml_robustness.py]
related: [porting-guide, layout-and-drawio, snapshot-model, http-api, cli, team-mode-security]
updated: 2026-10-19
---
# 회사 draw.io 규격(`DrawioSpec`)과 기존 XML 진단·정렬·가져오기

> 회사가 이미 가진 `drawio.xml`을 진단(`inspect_drawio_xml`)하고, 더 나빠지지 않는 범위에서 정렬하고(`align_report`/`align_drawio_xml`),
> 여러 페이지를 `Snapshot`으로 가져오고(`import_drawio_xml`), 회사 템플릿의 포맷을 `DrawioSpec`으로 뽑아 쓴다(`spec_from_xml`, `adopt_company_xml`).
> 라벨에서 읽은 내용은 추론이다. API 근거가 아니므로 가져온 스냅샷에는 `metadata.imported = true`가 붙는다.

## 핵심
- 입력은 모두 `drawio_spec.read_document` 한 곳을 지난다. 이 함수는 10 MiB 제한, defusedxml 파싱, 루트 태그 확인, 압축 페이지 해제, 셀 10,000개 제한을 맡는다. 실패는 `errors.InputError`이고, HTTP는 422(`routes.install_error_handlers`), CLI는 종료 코드 2와 한 줄 메시지(트레이스백 없음)로 답한다.
- 좌표가 `inf`·`nan`이거나 숫자가 아니면 `InputError`다. 절댓값이 `1e7`을 넘거나 너비·높이가 음수여도, 부모가 순환하거나 64단계보다 깊어도 마찬가지다(`xml_align._number`, `_compute_abs`는 반복문).
- 압축된 `<diagram>`(base64 → raw deflate → URL 인코딩)은 풀어서 읽는다. 해제 총량도 10 MiB로 제한한다. 출력은 항상 비압축이고 `compressed="false"`다. 저장하는 편집본(`drawio.validate_document`)만 압축 페이지를 거부한다.
- HTTP의 `spec`에는 프리셋 이름이나 인라인 JSON 객체만 넣을 수 있다(`request_spec`). 파일 경로와 XML은 CLI·라이브러리(`resolve_spec`)만 받는다. 요청 데이터로 서버 파일을 읽지 않는다.
- `DrawioSpec`은 생성할 때 모든 필드를 검증한다(`__post_init__`).
  - `cell_wrapper`는 `object`·`UserObject`·`mxCell` 중 하나이고, 대소문자를 구분하지 않고 정규화한다.
  - `attribute_map` 값은 XML 이름이어야 하고 draw.io 예약 이름(`id`, `parent`, `style`, `vertex`, `edge`, `source`, `target`, `label`, `value` 등)은 쓸 수 없다.
  - 숫자는 유한한 범위 값이다. 색·글꼴에는 `;`·`=`를 넣을 수 없다(스타일 키 주입 차단).
  - 모르는 키는 거부한다. 예전 파일의 `icon_size`·`slot_w`·`row_h`는 무시한다(렌더러가 읽은 적이 없다).
- 셀 분류표는 `drawio_spec.classify_cell` 하나다. 규격 추출, 진단, 정렬, 가져오기가 모두 이 표를 쓴다.
- 정렬은 입력보다 나쁜 결과를 내지 않는다. 문제가 없는 페이지는 그대로 두고, 겹침·범위 이탈·선-아이콘 충돌 중 하나라도 늘면 원래 배치를 돌려준다.

## 셀 분류 (`classify_cell`)
신호가 강한 것부터 본다.
1. Diagram Bot 고정 ID(`heading`, `scope`, `cloud`, `region`, `references`, `onprem`), `edgeLabel`·`text` 셀, WAF 표식, 요약 카드(`summary="1"`).
2. AWS 그룹 아이콘(`grIcon`, `GROUP_ROLES`)과 `resourceId`의 그래프 종류(`vpc`, `subnet`, `auto_scaling_group`).
3. aws4 `shape`/`resIcon`(`SHAPE_COLOR_KINDS`, `SHAPE_KINDS`, `SHAPE_ALIASES`). 대상 그룹과 로드 밸런서는 같은 타일이라 채움 색으로 가른다. 표에 없는 aws4 아이콘은 `kind=None` 리소스다.
4. 컨테이너(`container=1`, 자식이 있음, `swimlane`/`group`)의 라벨 단어(`CONTAINER_WORDS`, AZ·리전 코드). 맞는 것이 없으면 `group`이다.
5. 일반 도형의 라벨 단어(`LABEL_KINDS`). 단어 단위로만 맞추므로 "International"에서 `nat`, "Albert"에서 `alb`를 찾지 않는다. 맞는 것이 없으면 `annotation`이다. EC2로 추정하는 기본값은 없다.

- 서브넷 계층은 `subnet_tier`가 정한다. 퍼블릭·격리 색과 봇의 경로 문구를 먼저 보고, 그다음 첫 줄 이름을 `layout.tier_rank`로 판정한다(한국어 웹·앱·데이터는 영어로 바꿔서). 그래서 렌더러와 같은 순위가 나온다.
- 엣지의 부모가 엣지이거나 `relative="1"`인 꼭짓점은 `label`이다. 정렬·가져오기에서 건드리지 않는다.

## 진단 (`inspect_drawio_xml` / `api.inspect_xml`)
- 모든 페이지를 본다. `overlaps`, `out_of_bounds`, `unaligned_tiers`, `unrouted_edges`, `edge_collisions`는 셀 ID 쌍 또는 ID 목록이다(개수가 아니다). `clean`, `node_count`, `container_count`, `edge_count`, `roles`, `wrapper`, `spec`도 돌려준다.
- `pages`에는 페이지마다 `name`, `aws`(AWS 셀이 있는지; 표지·범례는 `false`), `node_count`, 문제 개수, `clean`이 들어 있다.
- 계층 행은 각 AZ 열에서 같은 계층의 k번째 서브넷이 같은 `y`에서 시작하면 정렬된 것으로 본다. `layout/`처럼 높이는 달라도 된다(빈 서브넷은 더 낮다).
- 엣지 중간점은 엣지 부모의 원점에 대한 상대 좌표다(draw.io 규칙). `orthogonalEdgeStyle`에 포트가 있으면 그 경로를 잰다. 직선 엣지는 중심에서 중심까지 그어 잰다(`_segment_hits`, 대각선도 정확). 경로를 draw.io가 정하는 엣지(직교, 포트·중간점 없음)는 충돌을 재지 않는다.
- 겹침·충돌 검사는 격자 색인(`xml_align._Grid`)을 쓴다. 도형 3,200개도 1초 안팎이다(예전에는 13.7초). 도형 하나당 상자 검사 횟수 상한을 테스트가 지킨다.

## 정렬 (`align_report` / `align_drawio_xml` / `api.align_xml`)
- `mode="preserve"`(기본): 페이지마다 아래 순서로 처리한다.
  1. 진단해서 `clean`이면 기하를 건드리지 않는다(`status: "clean"`).
  2. 아니면 사본을 만들고 `_align_page_in_place`를 실행한다. 이 함수는 평평한 셀을 감싸는 컨테이너로 옮기고, 리소스를 `layout.SLOT`/`ROW`/`ICON` 격자에 놓는다. AZ 열의 계층 행을 맞추고, 컨테이너를 아래에서 위로 키우고, 포트를 나눈 직교 경로를 만든다.
  3. 다시 진단해서 `GUARDED`(`overlaps`, `out_of_bounds`, `edge_collisions`) 중 하나라도 늘었으면 원래 페이지로 되돌린다(`status: "reverted"`). 아니면 `"aligned"`다.
  4. 규격(`spec` 또는 `normalize_styles`)은 그다음에 모든 페이지에 적용한다.
  - 결과: `align_report` → `{"xml", "pages": [{name, status, before, after, kept_wrappers}]}`. `align_drawio_xml`은 그중 `xml`이다.
- 중간점은 엣지 부모의 좌표계로 쓴다. 상단 배너를 지울 때(`include_header=False`)는 최상위(`parent="1"`) 엣지의 중간점만 옮긴다.
- `annotation`·`label`·`group`은 옮기지도 않고, 부모를 바꾸지도 않고, 측정하지도 않는다.
- 래퍼를 바꿀 때 사용자 속성은 지운다. `tooltip`, `link`, `placeholders`, `resourceId` 같은 속성이 있는 `object`/`UserObject`는 규격이 `mxCell`이어도 그대로 두고 `kept_wrappers`로 센다. 맨 `mxCell`의 사용자 속성은 새 래퍼로 옮긴다.
- Diagram Bot 자신의 출력(모든 샘플, 모든 페이지)에서는 아무것도 바꾸지 않는다.
- `mode="rebuild"`: 가져오기 → `generate_document`(규격이 없으면 입력에서 추출한 규격).

## 가져오기 (`import_drawio_xml` / `api.import_xml`)
- 모든 페이지를 읽는다. AWS 셀이 없는 페이지(표지, 범례)는 건너뛴다. 전체 구성 카드 안의 개수·아이콘(`glance`)은 리소스로 보지 않는다.
- 여러 페이지에 그려진 리소스는 한 번만 만든다.
  - 봇 셀은 `resourceId`로 맞추고, `vpc-`·`subnet-`·`i-`·`nat-`·`igw-`·`vpce-`·`fs-` ID를 그대로 쓴다.
  - 그 밖의 셀은 (종류, 이름, VPC)로 맞춘다. VPC는 CIDR, 서브넷은 (VPC, CIDR)로 맞춘다.
- AZ는 라벨이 그 리전의 실제 AZ를 가리킬 때만 라벨에서 읽는다(`ap-northeast-2a`, `AZ a`, `Zone B`, `AZ 2`, `REGION_AZS`). 아니면 리전의 AZ 목록을 열 순서대로 쓴다.
- 서브넷 CIDR은 라벨의 CIDR이 VPC 안에 있고 비어 있을 때만 쓰고, 아니면 VPC의 다음 빈 블록을 쓴다(`_Cidrs`). VPC 밖 CIDR은 만들지 않는다.
- `metadata.import`에는 다음이 들어 있다.
  - `cells`: draw.io 셀 ID → 스냅샷 리소스 ID(VPC·서브넷·인스턴스 ID, LB·TG ARN, DB 식별자 등).
  - `pages`: 페이지별 가져옴 여부.
  - `unmapped`: 만들 수 없는 종류(TGW, VPN, 고객 게이트웨이, OpenSearch 등)의 셀과 `kind=None` 아이콘. 추측하지 않고 보고만 한다.
  - `synthesized`: 그림에 없는데 만든 것. 기본 VPC, 서브넷 없는 그림의 기본 서브넷, 퍼블릭 서브넷용 IGW, 대상 그룹이 없을 때의 `tg-imported`.
- NAT 게이트웨이는 만들어 넣지 않는다. 프라이빗 서브넷은 그려진 NAT가 있을 때만 NAT 경로를 받는다.
- 왕복(봇 출력 → 가져오기): 모든 샘플에서 VPC·서브넷·EC2·LB·TG·NAT·IGW 개수와 VPC·서브넷·인스턴스 ID가 같다. 여러 노드로 그려지는 데이터 저장소(Aurora 멤버, ElastiCache 노드, EFS 탑재 대상)는 그려진 노드마다 하나씩 생긴다.

## 규격 추출과 템플릿 채택
- `spec_from_xml(xml)`이 모든 페이지에서 처음 본 값을 배우는 항목은 다음과 같다.
  - 래퍼, 배너(`heading`·`scope`), Cloud/Region/AZ 상자 유무, 서브넷 라벨 표기(`compact`/`full`).
  - 역할별 컨테이너 스타일, 종류별 리소스 스타일, 글꼴·색, 화살표.
  - `isolated_subnet_distinct`는 템플릿에 격리 서브넷이 따로 칠해져 있을 때만 참이다. 규격이 받지 않는 값(예: 새 draw.io의 `light-dark()` 색)은 배우지 않는다.
- `adopt_company_xml(xml_or_path, name=..., set_default=False, register=True, allow_path=True, save_spec=None)`은 진단 → 규격 추출 → 정렬 → 가져오기 → 표준 등록(`register`) → 회사 규격 렌더링(`standardized_xml`) 순서로 처리한다. 기본 표준 이름(`three-tier` 등)과 겹치면 `InputError`다.
- HTTP `POST /api/xml/adopt`는 요청마다 결과만 돌려준다(editor). `make_default: true`는 admin만 쓸 수 있고, 프로세스 기본 규격 설정과 `Design` 표준 등록을 함께 한다. 이 설정은 메모리에만 있어 재시작하면 사라진다.
- 앱이 저장·편집하는 문서는 기본 규격이 `mxCell`이거나 속성 이름을 바꿔도 `object` 래퍼와 `resourceId`/`relationId`를 유지한다(`DrawioSpec.for_editor`). 편집기 브리지가 이 속성으로 선택하기 때문이다. `GET document?spec=`은 저장할 수 없는 내보내기 미리보기다(`revision: null`, `preview: true`).

```python
from diagram_bot import api

report = api.inspect_xml(company_xml)                 # report["overlaps"]: [(id_a, id_b), ...], report["clean"]
result = api.align_report(company_xml, spec="clean")  # result["pages"][0]["status"]: "clean" | "aligned" | "reverted"
snapshot = api.import_xml(company_xml)                # snapshot["metadata"]["import"]["cells"]: {cell_id: resource_id}
spec = api.spec_from_xml(company_xml)
xml = api.render(snapshot, spec=spec)
```

## 진입점
- Python(`api.py`): `inspect_xml`, `align_xml`, `align_report`, `import_xml`, `adopt_xml`, `spec_from_xml`, `resolve_spec`, `set_default_spec`, `DrawioSpec`.
- HTTP(`routes/documents.py`, [[http-api]]):
  - `POST /api/xml/align` → `{xml, before, after, pages}`.
  - `POST /api/xml/import` → `{id, snapshot}`.
  - `POST /api/xml/adopt` → `{id, name, default, spec, aligned_xml, standardized_xml, before, after}`.
  - 세 엔드포인트 모두 editor 이상이고, `make_default`는 admin이다.
- CLI(`cli.py`, [[cli]]): `align-xml`(`--spec`은 프리셋·JSON·회사 `.drawio`; 되돌린 페이지와 남긴 래퍼를 표준 오류에 출력), `import-xml`, `adopt-template`. XML 입력 오류는 종료 코드 2다.

## 바꿀 때 주의
- 새 입력 경로는 반드시 `read_document`를 지나게 한다. `fromstring`을 직접 부르면 크기·압축·셀 제한과 422 매핑이 빠진다.
- 요청 데이터를 `resolve_spec`에 넘기지 않는다. 라우트는 `request_spec`만 쓴다. `adopt_company_xml`에는 `allow_path=False`를 넘긴다.
- 분류 규칙은 `classify_cell` 한 곳에서만 바꾼다. 서브넷 계층 단어는 `layout.tier_rank`에 있다. `test_subnet_tiers_match_layout_tier_rank_on_own_output`와 `test_align_on_own_output_is_a_no_op_or_an_improvement`가 렌더러와의 일치를 지킨다.
- 엣지 중간점을 읽거나 쓰는 코드는 엣지 부모의 원점을 더하거나 뺀다(`test_edge_inside_a_group_is_routed_in_its_parent_frame`).
- 정렬기 격자는 `layout.SLOT`, `ROW`, `ICON`, `LANE_TOP`, `SUBNET_HEADER`, `AZ_HEADER`, `VPC_HEADER`를 쓴다. 값을 따로 두지 않는다.
- 성능 테스트(`test_align_and_inspect_do_linear_work`)는 시간이 아니라 상자 검사 횟수를 센다. 새 쌍별 검사는 `_Grid`로 한다.
- `drawio.py`의 렌더러 출력은 바이트 단위로 고정되어 있다(golden). 스타일·아이콘 표는 `drawio_spec`에만 있다.

## 관련
- [[porting-guide]] — 회사 저장소로 옮길 때의 단계별 파일 목록.
- [[layout-and-drawio]] — `layout/` 패키지의 격자·계층·경로 규칙(정렬기가 따르는 기준).
- [[snapshot-model]] — 가져오기가 만드는 `Snapshot` 구조.
- [[http-api]] — `/api/xml/*`와 문서 `spec` 미리보기 계약.
- [[cli]] — `align-xml`, `import-xml`, `adopt-template` 옵션과 종료 코드.
- [[team-mode-security]] — 역할(viewer·editor·admin)과 감사 로그.
