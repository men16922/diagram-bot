---
title: 배치와 draw.io 문서
summary: layout.py의 결정적 계층 배치(diagram-bot-layered/2 — 띠, AZ 열, 거터·채널, 전체 구성 카드, CloudFront 엣지 열, 데이터 센터 상자)와 drawio.py의 XML 생성·검증·주석 보존, editor.py의 고정 draw.io 설치, 테스트가 지키는 불변식.
sources: [src/diagram_bot/layout.py, src/diagram_bot/drawio.py, src/diagram_bot/drawio_spec.py, src/diagram_bot/editor.py, src/diagram_bot/views.py, tests/test_drawio.py, tests/test_graph_report.py, docs/DRAWIO.md]
related: [drawio-format-and-alignment, views, graph, samples, web-app, storage, howto-add-collected-service]
updated: 2026-10-18
---
# 배치와 draw.io 문서

> 좌표는 모두 Python(`layout.layout_view`)이 결정적으로 계산하고, `drawio.py`는 그 결과를 압축하지 않은 네이티브 draw.io XML로
> 직렬화한다. draw.io의 자동 배치·자동 경로는 쓰지 않는다. 같은 입력과 옵션이면 같은 좌표·같은 XML이 나온다.

## 핵심
- 진입점: `drawio.layout_document(snapshot, view, vpc_id, resource, include_references, graph, routes)` = `layout_view(snapshot, graph, view_graph(...))`. `generate_document(..., spec=None)`가 XML을 만든다(회사별 포맷 커스터마이징과 기존 XML 정렬은 [[drawio-format-and-alignment]]).
- 엔진 표시: `layout.ENGINE, VERSION = "diagram-bot-layered", 2`. 범위 캡션 셀(`id="scope"`)에 `layout="diagram-bot-layered/2"`와 `view`, `vpc`, `focus`, `references`, `routes` 속성이 들어간다.
- 셀 ID: `layout.cell_id(value) = "r-" + sha256(value)[:24]`. 리소스 셀은 노드 ID, 관계 셀은 `"edge/" + 관계 ID`에서 만든다. 고정 ID 셀은 `0`, `1`, `heading`, `scope`, `cloud`, `region`, `references`, `onprem`(`drawio.GENERATED`).
- 배치 결과(`layout_view`의 반환): `engine`, `version`, `view`, `counts`, `width`, `height`, `shapes`(역할·부모·상대 좌표), `edges`(`path` 전체 경로, `points` 중간점, `constraints` exit/entry), `absolute`(셀별 절대 좌표), `labels`(라벨 상자).

## 공통 머리말
- `heading`: `AWS / {계정} / {리전} · {보기}` (샘플은 `SAMPLE · ` 접두, 설계안은 `DESIGN · {title} / {region}`), detail은 VPC 이름, focus는 리소스 이름, 경로 필터는 `· 경로 {이름}`.
- `scope`: 수집 상태(`조회 완료` 또는 `부분 조회: operation(error_code)…`), 완료 시각, `표시 N개 / 수집 N개 · 참조 N개 · 숨김 N개`, 경로 필터 설명, 서브넷 구분 기준(라우팅 테이블이 있으면 `0.0.0.0/0` 경로, 없으면 `MapPublicIpOnLaunch`·이름·Tier 태그).
- 컨테이너 계층은 AWS Architecture Icons 관례를 따른다: AWS Cloud(`cloud`) > Region(`region`) > VPC > AZ > 서브넷.

## detail 배치 (`layout.detail`)
VPC마다 위에서 아래로:
1. **연결 행**: `EDGE_KINDS` 순서(IGW, VGW, 피어링, TGW attachment, VPC 엔드포인트, EKS, 태스크를 수집하지 못한 ECS 서비스, Lambda, SageMaker 엔드포인트). VPC 폭을 넘으면 줄을 바꾼다.
2. **로드 밸런서 행**: 인터넷용을 왼쪽에. **대상 그룹 행**: 연결 LB 위치의 무게중심 순.
3. **채널**: 대상 그룹마다 자기 수평 줄, LB 서브넷 배치선의 줄.
4. **AZ 열**: AZ마다 열 하나, 열 사이와 양쪽 끝에 **거터**. 서브넷은 `tier_rank`(퍼블릭 → web → app → data/격리) 순으로 쌓고, 같은 층은 모든 열에서 같은 높이다. 서브넷 안 리소스는 등록 대상 그룹의 무게중심 순, ASG 멤버끼리 한 줄(`arrange`). 서브넷 색은 `is_public`(경로 분류 우선)과 `route_class`/`tier`(`spec.isolated_subnet_distinct=True`일 때 격리 서브넷 별도 색상).
5. **서브넷 미확인 띠**: VPC 소속만 확인된 리소스.
- ASG·ECS 서비스는 멤버가 한 층에 있을 때 AZ 열을 가로지르는 틀(`asg`, ECS는 `frame="ecs"`)로 그린다. 틀이 다른 리소스를 감싸게 되면 틀을 포기한다. 틀이 있으면 대상 그룹→틀 선 하나가 멤버별 `registered_target` 선을 대신한다(`suppressed`).
- 거터 폭은 지나가는 경로 수에 맞춘다(`max(28, 16 + 8 * n)`). 행마다 들어오는 경로 수만큼 위 여백(lane)을 둔다.
- VPC 밖 리전 리소스(참조·배치 미확인)는 Region 안 `references` 상자, `outside_vpc`인 것(고객 게이트웨이, DX 연결, `AvailabilityZone=all` IP 대상)은 AWS Cloud 오른쪽의 `onprem` 상자("Corporate data center")에 한 줄로 둔다.
- CloudFront 배포는 Region 왼쪽, AWS Cloud 안의 **엣지 열**(`edge_column`, 캡션 "엣지 · CloudFront (리전 밖)")에 둔다. Region의 x는 `edge_offset`이 엣지 열과 경로 통로 폭만큼 민다.
- 경로(`layout.route`): 모든 관계에 직교 경로를 명시한다. 대상 그룹은 자기 채널 줄로 줄기를 내리고(버스), 거터마다 출발 그룹별 세로 칸, 행마다 들어오는 관계별 lane을 쓴다. VPN·DX VIF는 AWS Cloud 오른쪽 통로를 거쳐 `onprem` 상자로, CloudFront는 Region 왼쪽 통로를 내려와 LB 행 위 lane으로 들어간다. 대상 그룹 경로 색은 `TG_STROKES` 다섯 가지를 순환한다.
- 경로 필터가 있으면 경로 밖 리소스·틀을 `muted`로 표시한다(XML에서 `opacity=30;textOpacity=45;`).

## overview 배치 (`layout.overview`)
- VPC 요약 카드(`summary`, 폭 320, 3열). 카드 제목은 VPC 이름·CIDR·AZ 수·서브넷 수, 계정 통합이면 계정, TGW attachment가 연결된 라우팅 테이블.
- 카드 안 **요약 아이콘**(`vpc_glance`): 층(퍼블릭·프라이빗·격리)별로 종류를 세어 `EC2 × 6` 아래 `ASG 2개`, `ALB × 2` 아래 `인터넷 · WAF`, `RDS × 2` 아래 `Multi-AZ`처럼 두 줄로 표시한다(`glance_label`, `GLANCE`, `GLANCE_KINDS`). 아이콘을 누르면 첫 리소스 속성이 열린다.
- 추가 카드: VPC에 붙지 않은 Lambda·SageMaker 엔드포인트("VPC 미연결 리전 서비스"), VPC가 확인되지 않은 수집 리소스("VPC 외 · 배치 미확인"), VPC가 없을 때 이유(`describe_vpcs`가 완료가 아니면 "권한·조회 실패일 수 있습니다").
- 허브 연결이 없고 통합 스냅샷이 아니면 카드 격자만 그린다. 아니면 `network()`: 연결된 VPC 카드 행 → 허브 띠(`HUB_ORDER` 순: TGW, VGW, 피어링, DX 게이트웨이) → 나머지 카드. 경로마다 카드 아래 변의 출발점, 카드 오른쪽 틈, 허브 위 lane을 따로 쓴다. DX 게이트웨이 연결(허브↔허브)은 허브 띠 아래 자기 lane. 고객 게이트웨이·DX 연결은 AWS Cloud 오른쪽 데이터 센터 상자(DX가 있으면 캡션 끝 `·DX`).
- 계정 통합이면 계정별 카드를 `account` 틀로 묶는다(행에 계정을 섞지 않음).
- CloudFront(`overview_edge`): 엣지 열의 아이콘에서 통로를 내려와 오리진 LB의 요약 아이콘 위에서 카드 윗변으로 들어간다. 아이콘 높이를 첫 경로 lane에 맞춘다(`align_edge`).

## 라벨과 아이콘
- `resource_label(node)`: 이름, 유형, 고가용성 역할 순. 예: RDS `primary`/`standby`/`Single-AZ`/`db_role`, ElastiCache `샤드 0001 · primary`, OpenSearch `data 2 · master 1`과 `standby`, Redshift `primary`/`secondary`, EC2 `GPU · EFA · EIP …`와 `Capacity Block`/`ODCR`/`PG …`, LB `AZ 2개 · EIP 2 · WAF`, DX 연결 `Direct Connect · 10Gbps`·위치·`VIF n`, TGW `라우팅 테이블 n`. 근거 값은 [[graph]]의 파생 필드.
- 라벨은 아래(폭 140, 4줄) 또는 오른쪽(폭 200, 3줄)에 두고 `fit_label`·`fit_name`으로 자른다. 전체 이름은 `tooltip`.
- `icon_variant`: LB 유형, DB·캐시 엔진, EFS 클래스, FSx 유형, 가속 인스턴스(`ACCELERATED_ICON`, trn·inf), Bedrock·SageMaker 엔드포인트, VPC 안 IP 대상.
- `drawio.resource_style`: 변형 → `RESOURCE_ICONS`(리소스 수준 아이콘) → `ICONS`(서비스 아이콘 타일) → 기본 둥근 사각형. 참조 노드는 `dashed=1;opacity=65;`, 강조는 굵은 글씨·노란 배경. WAF 보호 리소스에는 `badge` 자식 셀(`waf_badge`).
- 그룹 스타일 `STYLES`는 draw.io `aws4` 라이브러리의 그룹(`group_aws_cloud_alt`, `group_region`, `group_vpc2`, `group_auto_scaling_group`, `group_corporate_data_center`, `group_account`)을 쓴다.

## drawio.py
- `generate_document(snapshot, vpc_id, include_references, view, resource, routes, spec=None)`: 보기를 지정하면 한 페이지, 아니면 "전체 구성" + VPC마다 상세 페이지(VPC `resource_id` 정렬, 페이지 이름 = VPC ID). 루트는 `<mxfile host="Diagram Bot" compressed="false">`. `spec`으로 회사 규격(`"clean"`, `"mxcell"`, `DrawioSpec`)을 적용할 수 있다([[drawio-format-and-alignment]]).
- `page()`: 셀마다 `<object>`(또는 `spec.cell_wrapper="mxcell"`이면 `<mxCell>`)에 `label`과 `resourceId`, `tooltip`, `summary="1"`(카드) 등을 속성으로 단다. 관계 셀은 `relationId`(그래프 관계 ID), `relation`(`RELATIONS`의 한국어 이름, 없으면 원래 이름), `evidence`를 갖고, 스타일에 `exitX/exitY/entryX/entryY` 제약과 `orthogonalEdgeStyle`, 중간점 `Array as="points"`를 넣는다. 보조(SG·ENI·EBS 등) 관계는 회색 점선.
- `validate_document(xml)`: 10 MiB 이하, `defusedxml`로 파싱(외부 엔티티 차단), 루트는 `mxfile` 또는 `mxGraphModel`, `mxfile`이면 `diagram/mxGraphModel`이 있어야 함(압축 문서 거부).
- `carry_annotations(saved_xml, generated_xml)`: 재배치 때 저장본 첫 페이지에서 사용자가 더한 셀만 새 문서로 옮긴다. 생성 셀(고정 ID, `r-` 접두, `resourceId`·`relationId`·`relationIndex`·`summary` 속성)은 건너뛰고, 부모·끝점이 새 문서에 없는 셀도 건너뛴다. `routes/documents.py`의 재배치가 `DocumentStore.replace(merge=carry_annotations)`로 쓴다([[storage]]).

## editor.py (고정 draw.io)
- `VERSION = "32.4.1"`, `SHA256` 고정, `URL`은 GitHub `jgraph/drawio` 릴리스의 `draw.war`.
- 설치 위치 `editor_path()` = `$XDG_CACHE_HOME`(기본 `~/.cache`)`/diagram-bot/drawio/<VERSION>`. `.verified-sha256`가 같으면 다시 받지 않는다.
- `install_editor(archive, destination)`: 임시 디렉터리에 받아 SHA-256 확인 → `WEB-INF`·`META-INF` 제외 압축 해제(경로 탈출 검사) → 디렉터리 교체. 체크섬이 틀리면 기존 파일을 건드리지 않는다(`test_editor_checksum_failure_keeps_existing_files`).
- CLI `diagram-bot setup-editor [--archive draw.war]`. 설치 전이면 서버의 `/editor/js/app.min.js`가 503 "diagram-bot setup-editor를 먼저 실행하세요."를 돌려준다(설치되어 있으면 draw.io 스크립트 뒤에 `editor-bridge.js`를 붙여 보낸다).

## 테스트가 지키는 불변식 (`tests/test_drawio.py`)
- `assert_readable`: 셀 ID 중복 없음, 자식(라벨 상자 포함)은 부모 틀 안, 형제끼리 겹치지 않음(ASG·계정 틀은 예외), 경로는 직교이고 끝점이 아닌 리소스 아이콘·라벨을 지나지 않음.
- `assert_no_shared_segments`: 두 경로가 같은 선분을 공유하는 것은 끝점을 공유할 때뿐(대상 그룹 버스).
- `assert_routed`: 모든 관계가 명시 경로를 가짐(자동 경로 없음), 경로 양 끝이 exit/entry 제약 위치와 1px 안에서 일치하고 제약은 테두리 위. `assert_xml_routed`: XML의 모든 관계 셀에 `exitX`·`entryX`·`orthogonalEdgeStyle`과 중간점.
- `assert_captions_clear`: 경로가 컨테이너 캡션(그룹 아이콘·제목)을 지나지 않음. `assert_routes_avoid_cards`: 전체 구성 경로가 다른 요약 카드를 지나지 않음.
- 결정성: 같은 입력이면 `shapes`와 XML이 같다(`test_views_are_readable_and_reproducible`, 엣지 문서).
- 샘플 단위: `test_best_practice_samples_are_readable`(모든 샘플의 overview·VPC별 detail ± references·주요 종류 focus), `test_every_focus_document_routes_its_edges`(샘플·데모·설계안의 모든 노드 focus), `test_overview_draws_the_network_between_vpcs`(샘플별 전체 구성 관계 목록이 표와 정확히 같아야 함), `test_overview_cards_differ_per_sample_and_hide_drawn_groups`("VPC 외 · 배치 미확인" 카드 없음).
- 배치 정직성: 참조와 IP 대상은 배치를 지어내지 않음(`test_references_and_ip_targets_do_not_invent_placement`), focus는 숨긴 서브넷을 "없음"이라 하지 않음.
- 규모: 100개 리소스 5초 미만, 한 VPC 300 인스턴스·18 대상 그룹 5초 미만, 2,500개 리소스 계정 20초 미만·51페이지. 경로 필터: 대상 그룹 하나면 교차 0, LB 하나면 전체 교차의 1/10 이하(`test_route_filter_keeps_dense_views_readable`).
- 엣지·DX: CloudFront는 `cloud` 부모로 Region 왼쪽, WAF 배지는 보호 대상의 자식, DX 연결은 `onprem` 상자, CloudFront 경로는 Region 경계를 가로지름(`test_edge_and_direct_connect_views_are_readable`).

## 바꿀 때 주의
- 배치 규칙을 바꾸면 `VERSION`과 `docs/DRAWIO.md`를 함께 고치고, 샘플 그림을 다시 만든다([[samples]]).
- 새 종류는 `EDGE_KINDS`·`SUBNET_KINDS`·`outside_vpc`·overview의 `outside` 제외 목록·`GLANCE_KINDS` 중 어디에 속하는지 정해야 한다([[howto-add-collected-service]]).

## 관련
- [[drawio-format-and-alignment]] — 회사별 `DrawioSpec`과 기존 `drawio.xml` 인플레이스 정렬·스냅샷 이식.
- [[views]] — 배치 입력이 되는 보기 결과.
- [[web-app]] — 편집기 iframe·저장·PNG 내보내기.
- [[storage]] — 편집 문서 리비전과 재배치 병합.
