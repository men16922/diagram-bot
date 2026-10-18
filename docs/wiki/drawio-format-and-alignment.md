---
title: 회사 draw.io 규격(DrawioSpec)과 기존 XML 자동 정렬·이식
summary: drawio_spec.py의 회사별 draw.io XML 포맷·컨테이너 계층·리소스 스타일 자동 추출(spec_from_xml, set_default_spec)과 xml_align.py의 회사 표준 템플릿 원클릭 표준화(adopt_company_xml), 인플레이스 정렬(align_drawio_xml), 스냅샷 역변환(import_drawio_xml).
sources: [src/diagram_bot/drawio_spec.py, src/diagram_bot/xml_align.py, src/diagram_bot/drawio.py, src/diagram_bot/layout/, src/diagram_bot/designer.py, src/diagram_bot/api.py, src/diagram_bot/routes/documents.py, src/diagram_bot/cli.py, tests/test_xml_align.py]
related: [porting-guide, layout-and-drawio, snapshot-model, http-api, cli]
updated: 2026-10-18
---
# 회사 draw.io 규격(`DrawioSpec`)과 기존 XML 자동 정렬·이식

> 회사마다 원하는 표준 아키텍처 템플릿(`drawio.xml`)의 포맷이 다르다:
> - `<mxCell value="…">` vs `<object label="…">` 래퍼
> - 외곽 `AWS Cloud`·`Region`·`AZ` 컨테이너 상자 유무 (`use_cloud_box`, `use_region_box`, `use_az_box`)
> - 서브넷 라벨 표기법(한 줄 `public-a (10.20.1.0/24)` vs 여러 줄 라우팅 설명)
> - 리소스 종류별 아이콘·색상·폰트(`resource_styles`), 격리(Data) 서브넷 구분색, 화살표 모양(`endArrow`)
>
> `adopt_company_xml`(`api.adopt_xml` / CLI `adopt-template` / HTTP `POST /api/xml/adopt`)에 회사 표준 `drawio.xml` 파일 하나만 넘기면 **회사 포맷 규격 추출 + 전역 렌더러 표준화 + 기존 XML 자동 정렬 + 사내 표준 아키텍처(`Design.apply_standard`) 등록**이 한 번에 끝난다.

## 1. 원클릭 회사 템플릿 표준화 (`adopt_company_xml` / `api.adopt_xml`)

회사에서 기존에 쓰던 표준 아키텍처 `drawio.xml` 파일을 받아 그 파일 기준으로 전체 엔진의 포맷·레이아웃·표준 템플릿을 개편하는 가장 빠른 방법이다.

```python
from diagram_bot import api

# 1) 회사 표준 drawio.xml 파일(또는 XML 문자열) 하나로 전체 포맷 개편 + 사내 표준 아키텍처 등록
result = api.adopt_xml(
    "company_standard_3tier.drawio",   # 파일 경로 또는 XML 문자열
    name="company-3tier",              # Design.apply_standard("company-3tier")로 부를 이름
    set_default=True,                  # 이후 모든 api.render / generate_document 호출이 회사 포맷으로 출력됨!
    save_spec="config/company_spec.json",
)
# result["aligned_xml"]      : 원본 셀 ID·라벨을 유지한 채 겹침·컨테이너·AZ 계층·직교 배선만 교정한 XML
# result["standardized_xml"] : 추출한 회사 규격(DrawioSpec)으로 새로 렌더링한 표준 XML
# result["spec"]             : 추출된 DrawioSpec 인스턴스
# result["snapshot"]         : 변환된 Snapshot (raw + graph)

# 2) 등록된 회사 표준 템플릿을 불러와 코드/AI 도구로 리소스 추가 -> 결과도 자동으로 회사 포맷!
d = api.Design()
d.apply_standard("company-3tier")
d.add_subnet_tier(vpc="corp-prod-vpc", tier="data")
d.add_cache(vpc="corp-prod-vpc", name="corp-redis")
new_company_xml = api.render(d.snapshot(), view="detail", vpc="vpc-corp-prod-vpc")
```

환경 변수 `DIAGRAM_BOT_SPEC=/path/to/company_standard.drawio` (또는 저장된 `company_spec.json`)를 설정하면 코드 수정 없이 서버·CLI·채팅 전체가 해당 파일의 규격을 기본값으로 사용한다.

## 2. `drawio_spec.py` — 회사 맞춤형 draw.io 포맷·스타일 규격

### 프리셋 3종 (`PRESETS`, `resolve_spec`)
| 프리셋 이름 (`spec=`) | 상수 | 특징 |
| --- | --- | --- |
| `"default"` (또는 `None`) | `DEFAULT_SPEC` | Diagram Bot 기본값: `<object label="…"><mxCell/></object>` 래퍼, 상단 제목(`heading`)·범위(`scope`) 배너 표시, `Arial`, 프라이빗·격리 서브넷 동일 청록색 |
| `"clean"` / `"enterprise"` | `ENTERPRISE_CLEAN_SPEC` | 사내 문서·발표용: 상단 `heading`·`scope` 배너 생략(다이어그램이 `y=24`에서 바로 시작), 격리(Data) 서브넷 별도 색상(`#ECEFF8` / `#3F51B5`), `endArrow=block` |
| `"mxcell"` | `MXCELL_SPEC` | 순수 `<mxCell id="…" value="…">` 래퍼(외부 `<object>` 미사용) + 배너 생략 + 격리 서브넷 구분. `<mxCell>`만 파싱하는 사내 기존 코드와 100% 호환 |

### `spec_from_xml(xml_text)`이 회사 XML에서 자동 학습하는 항목
- **래퍼 및 배너**: `cell_wrapper` (`"mxCell"` \| `"object"` \| `"UserObject"`), `include_header` (`heading`/`scope` 유무), `host`, `background`, `grid_size`
- **컨테이너 계층 구조**:
  - `use_cloud_box`: 회사 XML에 `AWS Cloud` 상자가 없으면 `False` (렌더링 시 생략하고 자식 좌표를 좌상단 여백 `24px`로 자동 이동)
  - `use_region_box`: 회사 XML에 `Region` 상자가 없으면 `False` (`VPC`를 상위 컨테이너로 자동 승격)
  - `use_az_box`: 회사 XML에 `AZ` 점선 상자가 없으면 `False` (서브넷을 `VPC` 바로 아래에 배치)
- **서브넷 라벨 표기**: `subnet_label_style` — 회사 XML의 서브넷 라벨이 한 줄(`public-subnet-a (10.20.1.0/24)`)이면 `"compact"`, 라우팅 설명을 포함한 여러 줄이면 `"full"`
- **컨테이너·서브넷·리소스별 스타일**:
  - `styles`: 컨테이너 역할별(`vpc`, `az`, `public_subnet`, `private_subnet`, `isolated_subnet` 등) 스타일 문자열
  - `resource_styles`: 리소스 종류별(`application`, `ec2`, `db_instance`, `nat_gateway` 등) 회사 아이콘·색상 스타일 문자열
  - `font_family`, `font_color`, `icon_size`, `edge_style`, `edge_end_arrow`, `edge_rounded`

## 3. `xml_align.py` — 기존 회사 `drawio.xml` 진단·자동 정렬·스냅샷 변환

### A. 진단 (`inspect_drawio_xml` / `api.inspect_xml`)
기존 `drawio.xml`의 레이아웃·배선 문제를 정량 진단한다:
- `overlaps`: 같은 부모 안에서 겹치는 도형 쌍
- `out_of_bounds`: 부모 컨테이너 영역 밖으로 삐져나온 자식 셀
- `unaligned_tiers`: 같은 VPC 내 AZ 열 사이에서 같은 계층(Public/Web/App/Data) 서브넷의 `y`·`height`가 어긋난 경우
- `unrouted_edges`: `orthogonalEdgeStyle` 또는 `exitX`/`entryX`가 없는 연결선
- `edge_collisions`: 연결선 선분이 끝점이 아닌 리소스 아이콘을 관통하는 경우
- `clean`: 위 5가지 문제가 모두 `[]`이면 `True`

### B. 규칙 기반 자동 정렬 (`align_drawio_xml` / `api.align_xml`)
- **`mode="preserve"` (기본, 인플레이스 정렬)**:
  1. `parent="1"`로 평평하게 놓인 셀의 중심점이 컨테이너(VPC·AZ·서브넷) 안에 있으면 가장 안쪽 컨테이너의 자식으로 편입하고 상대 좌표로 변환
  2. 서브넷·컨테이너 내부 리소스를 계층 순서(`_BAND_ORDER`: 게이트웨이/엔드포인트 → LB → 대상 그룹 → 컴퓨트/DB)와 격자로 겹침 없이 정렬
  3. 같은 VPC 안의 AZ 열들을 좌우 나란히 맞추고, 같은 계층 서브넷의 `y` 시작점과 `height`를 모든 AZ 열에서 동일하게 동기화
  4. 모든 컨테이너(서브넷 → AZ → VPC → Region → Cloud)를 자식 크기에 맞춰 아래에서 위로(`bottom-up`) 자동 리사이즈
  5. 모든 연결선에 포트 분산 `exitX`/`exitY`/`entryX`/`entryY`와 장애물(아이콘) 회피 직교 중간점(`Array as="points"`)을 부여
- **`mode="rebuild"`**: `import_drawio_xml`로 스냅샷을 만든 뒤 추출된 회사 `DrawioSpec`과 `generate_document`로 재배치한다.

## 4. HTTP API 및 CLI 진입점

| 진입점 | 사용법 |
| --- | --- |
| Python (`api.py`) | `api.adopt_xml(xml_or_path, name=..., set_default=True)`, `api.set_default_spec(spec)`, `api.spec_from_xml(xml)`, `api.align_xml(xml, mode="preserve"\|"rebuild")`, `api.import_xml(xml)`, `api.inspect_xml(xml)` |
| HTTP (`routes/documents.py`) | `POST /api/xml/adopt` (`{xml, name, set_default}` → `{id, name, spec, aligned_xml, standardized_xml, before, after}`), `POST /api/xml/align`, `POST /api/xml/import`, `GET /api/snapshots/{id}/document?spec=...` |
| CLI (`cli.py`) | `diagram-bot adopt-template company.drawio -o reports/company [--name corp-std] [--save-spec spec.json]`<br>`diagram-bot align-xml input.drawio -o aligned.drawio [--mode preserve\|rebuild] [--spec ...]`<br>`diagram-bot import-xml input.drawio -o snapshot.json [--render out.drawio]` |

## 관련
- [[porting-guide]] — 회사 스택 단계별 이식 가이드.
- [[layout-and-drawio]] — `layout.py`와 `drawio.py`의 기본 배치·렌더링 원리.
- [[snapshot-model]] — `import_drawio_xml`이 만드는 `Snapshot` 구조.
- [[http-api]] — `/api/xml/adopt`, `/api/xml/align`, `/api/xml/import`, 문서 `spec` 쿼리 레퍼런스.
- [[cli]] — `adopt-template`, `align-xml`, `import-xml` 명령 옵션.
