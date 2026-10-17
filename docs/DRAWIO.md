# draw.io 연동과 자동 배치

Diagram Bot이 draw.io를 어떻게 쓰는지 정리한 문서입니다. 사용하는 라이브러리, 구성도 XML 생성 방법, 자동 정렬 방식, 편집기 연동, 저장·출력 방법을 다룹니다.
코드 기준은 `src/diagram_bot/{layout,drawio,editor,views,graph}.py`와 `src/diagram_bot/static/{app.js,editor-bridge.js}`입니다.

## 한눈에 보기

```
AWS API(boto3) ─┐
샘플·채팅 설계 ──┴─▶ snapshot.raw ─▶ graph.py ─▶ views.py ─▶ layout.py ─▶ drawio.py ─▶ draw.io XML
                    (API 응답 원본)   (노드·관계)   (보기별 범위)  (좌표 계산)    (XML 직렬화)     │
                                                                                          ▼
                            브라우저: 고정 버전 draw.io 편집기(iframe) + editor-bridge.js ─▶ 편집·저장·PNG
```

- **draw.io 자동 배치 기능은 쓰지 않습니다.** 좌표는 모두 Python(`layout.py`)이 결정적으로 계산하고, draw.io는 그 결과를 보여 주고 편집하는 데만 씁니다.
- 생성한 문서는 **압축하지 않은 네이티브 draw.io XML**(`mxfile` > `diagram` > `mxGraphModel`)입니다. draw.io 데스크톱·웹에서 그대로 열고 고칠 수 있습니다.
- 입력이 같고 옵션이 같으면 좌표도 항상 같습니다. 새로 수집해도 구성이 같으면 같은 그림이 나옵니다.

## 사용하는 라이브러리

| 구분 | 무엇 | 어디서 | 비고 |
| --- | --- | --- | --- |
| 편집기 | **draw.io (jgraph/drawio) 32.4.1** 웹 배포물 `draw.war` | `diagram-bot setup-editor`가 GitHub 릴리스에서 내려받음 (`editor.py`) | SHA-256 고정 검증, `~/.cache/diagram-bot/drawio/32.4.1`에 압축 해제, 저장소에 포함하지 않음, Apache-2.0 |
| 편집기 확장 | `editor-bridge.js` (자체 코드) | 서버가 `/editor/js/app.min.js` 뒤에 붙여 제공 | draw.io 원본은 수정하지 않고 플러그인 API(`Draw.loadPlugin`)만 사용 |
| 아이콘 | draw.io 내장 **AWS Architecture Icons** (`mxgraph.aws4.*`) | draw.io 배포물 안의 셰이프 라이브러리 | 별도 다운로드 없음 |
| XML 생성 | Python 표준 `xml.etree.ElementTree` | — | 생성 전용 |
| XML 검증 | **defusedxml** | PyPI | 업로드·저장 XML을 안전하게 파싱 (XXE 등 차단) |
| 레이아웃 | 자체 구현 `diagram-bot-layered/2` | `layout.py` | 외부 레이아웃 엔진 없음 |

외부 레이아웃 엔진(ELK, `elkjs`)도 평가했지만 채택하지 않았습니다. 이유는 [engineering/LAYOUT_EVALUATION.md](engineering/LAYOUT_EVALUATION.md)에 있습니다.
요약하면 ELK는 AZ를 나란히 놓인 열로 유지하지 못했고, CLI에 Node 런타임이 필요해지는 문제도 있었습니다.
mxGraph·maxGraph 같은 JavaScript 라이브러리를 서버에서 쓰지도 않습니다. XML은 Python이 직접 만듭니다.

## 1. 그래프와 보기 범위 (`graph.py`, `views.py`)

- `graph.py`는 API 응답 필드만으로 노드(VPC, 서브넷, EC2, LB, RDS …)와 관계(`in_vpc`, `in_subnet`, `registered_target`, `associated_load_balancer` …)를 만듭니다. 각 관계에는 근거 API 필드가 붙습니다. IP나 이름이 같다고 해서 관계를 추측하지 않습니다.
- `views.py`는 보기마다 무엇을 그릴지 정합니다.
  - **전체 구성(overview):** 계정 › 리전 › VPC 요약 카드입니다. VPC에 속하지 않은 리소스는 별도 카드에 표시합니다(VPC 미연결 Lambda, 배치 미확인 리소스).
  - **상세 구성(detail):** VPC 하나와 그 안의 모든 리소스, 등록 대상, 리전 리소스(TGW, VPN)를 그립니다.
  - **선택 리소스(focus):** 리소스 하나와 직접 관계, 그리고 위치를 읽는 데 필요한 상위 경계만 그립니다.
  - SG·ENI·EBS 같은 참조 리소스는 옵션을 켤 때만 그립니다(`AUXILIARY`).

## 2. 자동 정렬 알고리즘 (`layout.py`)

### 컨테이너 중첩

```
AWS Cloud(계정) › Region › VPC › AZ 열 › 서브넷 › 리소스 아이콘
```

### VPC 안의 띠(band) — 위에서 아래로

1. **연결 행:** IGW, VPN 게이트웨이, 피어링, TGW 연결, VPC 엔드포인트, EKS 컨트롤 플레인, VPC Lambda, ECS 서비스입니다(`EDGE_KINDS` 순서).
2. **로드 밸런서 행:** 서브넷 여러 개에 걸친 ALB·NLB도 VPC 수준에 한 번만 그립니다.
3. **대상 그룹 행:** 논리 리소스라서 서브넷 안에 넣지 않습니다.
4. **배선 채널:** 대상 그룹에서 내려오는 선 전용 띠입니다.
5. **AZ 열:** AZ마다 열을 하나씩 두고 나란히 배치합니다. 각 열 안에는 서브넷을 계층 순서(퍼블릭 → web → app → data, `tier_rank`)로 위에서 아래로 쌓습니다. 계층은 라우트 테이블 분류(IGW/NAT/TGW/VGW/격리)와 `Tier` 태그·이름으로 판단합니다.
6. **배치 미확인 영역:** VPC에는 속하지만 서브넷을 확인하지 못한 리소스입니다.

VPC 밖의 참조 리소스(조회되지 않은 대상, 외부 IP, 고객 게이트웨이)는 VPC 오른쪽의 별도 상자에 둡니다.

### 정렬 규칙 (결정적)

- 로드 밸런서는 이름순입니다.
- 대상 그룹은 연결된 LB 위치의 평균(barycenter) 순입니다.
- 인스턴스는 대상 그룹 barycenter 순으로 놓고, 같으면 이름·ID 순입니다.
- 서브넷 안에서 같은 Auto Scaling 그룹 멤버는 같은 행을 씁니다(`arrange`). 그래서 AZ 열을 가로지르는 ASG 점선 프레임이 다른 그룹 인스턴스를 감싸지 않습니다.
- ECS 서비스는 태스크 주위에 주황 점선 프레임을 그리고, ASG는 주황 점선 오버레이로 표시합니다. 둘 다 컨테이너가 아닌 오버레이라서 AZ 배치를 깨지 않습니다.
- 셀 ID는 리소스 ID의 SHA-256(`cell_id`)으로 만들어 실행할 때마다 같습니다. 덕분에 사용자 편집본과 새 생성본을 맞춰 볼 수 있습니다.

### 배선(라우팅)

draw.io 자동 경로 대신 꺾은선 좌표를 직접 지정합니다(`edgeStyle=orthogonalEdgeStyle` + 명시적 waypoint).

- 대상 그룹마다 채널 안에 자기 행으로 내려가는 trunk를 하나 둡니다.
- AZ 열 사이 gutter에서는 출발 그룹마다 세로 슬롯을 따로 둡니다. 더 깊이 꺾는 선이 더 바깥쪽을 쓰고, 간격은 8 px입니다.
- 아이콘 행에 들어오는 선마다 진입 lane을 따로 둡니다. 멀리 가는 선이 더 위를 쓰고, 간격은 4 px입니다.
- 출발점이나 도착점이 같을 때만 선분을 공유합니다(대상 그룹 버스). 그 밖에는 겹치지 않습니다.
- 같은 인스턴스가 포트 두 개로 등록되면 선을 두 개로 나누고 진입부에 포트 라벨을 붙입니다.
- SG·ENI·EBS 참조선은 아이콘 왼쪽에서 출발하고 회색 점선으로 그립니다.

### 라벨

- 아이콘 아래 최대 4줄입니다. 이름, 종류, 크기, HA 역할(primary/standby/replica, writer/reader, 샤드, 브로커 ID 등)을 표시합니다.
- 너비는 글자 폭으로 추정합니다(한글 1em, 라틴 0.62em, `text_width`). 넘치면 말줄임하고, 전체 이름은 툴팁과 속성 패널에 남깁니다.
- 숫자로 끝나는 이름(노드, 샤드)은 뒤쪽을 살려서 줄입니다(`fit_name`). 그래야 `…-0001-002`처럼 구분이 됩니다.

### 검증 (`tests/test_drawio.py`)

다음은 `make check`에서 자동으로 확인합니다.
- 형제 도형이 겹치지 않습니다(아이콘과 추정 라벨 상자 포함).
- 자식 도형이 컨테이너 밖으로 나가지 않습니다.
- 선분이 다른 아이콘이나 라벨을 지나지 않습니다.
- 모든 선이 직교 경로이고, 관련 없는 선끼리 선분을 공유하지 않습니다.
- 다시 실행해도 결과가 같습니다.

샘플 7종, 채팅 설계안, 리소스 100개짜리 픽스처로 확인합니다. 리소스 100개 기준 모든 페이지 배치는 약 0.02초입니다.

## 3. XML 직렬화 (`drawio.py`)

- `layout.py`가 만든 도형과 선 목록을 `mxCell`로 바꾸기만 합니다. 계산은 하지 않습니다.
- 그룹 스타일은 AWS 아이콘 세트의 그룹 셰이프를 씁니다. AWS Cloud, Region, VPC, 퍼블릭/프라이빗 서브넷, Auto Scaling 그룹, 기업 데이터 센터 등이 있습니다(`mxgraph.aws4.group*`).
- 리소스 아이콘은 리소스 단위 아이콘(`RESOURCE_ICONS`)을 우선하고, 없으면 서비스 아이콘 타일(`ICONS`)을 씁니다. 엔진·종류별로 변형이 있습니다. 예: Aurora와 RDS, Redis/Valkey/Memcached, ALB/NLB/GWLB, EFS Standard/One Zone.
- 각 셀에는 `resourceId`, `relationId`, `summary` 같은 사용자 속성을 넣습니다(`<object>` 래퍼). 이 속성이 편집기의 선택·강조와 속성 패널을 연결합니다.
- 맨 위 `heading`·`scope` 셀에는 계정·리전·수집 시각·집계·해석 범위를 적고, 레이아웃 엔진 이름과 버전을 `layout` 속성에 기록합니다. 샘플은 `SAMPLE ·`, 채팅 설계안은 `DESIGN ·` 접두어가 붙습니다.
- CLI의 `topology.drawio`는 페이지가 여러 개입니다. 전체 구성 페이지 하나와 VPC별 상세 페이지가 들어갑니다.
- `validate_document`는 크기(10 MiB)를 제한하고 압축되지 않은 `mxfile`만 허용합니다. 파싱은 defusedxml로 합니다.

## 4. 편집기 연동 (`app.js`, `editor-bridge.js`, `server.py`)

- 앱은 로컬에 설치된 draw.io를 iframe으로 띄웁니다. URL은 `/editor/index.html?embed=1&proto=json&offline=1&local=1&stealth=1&ui=min&lang=ko …`입니다.
  `offline`·`stealth`라서 **외부 네트워크 요청이 없습니다.** 브라우저 검증에서도 외부 요청이 0건인지 확인합니다.
- 통신은 draw.io **embed 프로토콜**(`postMessage`, JSON)로 합니다. `load`(XML 열기), `autosave`, `export`(PNG) 같은 표준 메시지를 씁니다.
- `editor-bridge.js`는 같은 origin의 부모 창 메시지만 받습니다. 다음을 추가합니다.
  - 선택한 셀의 `resourceId`/`relationId`를 앱에 전달 → 속성 패널
  - `diagramBotSelect`: 리소스와 직접 관계선 강조
  - `diagramBotDocument`: 현재 편집 XML 받기(저장용)
  - `diagramBotNote`: 노란 검토 메모 삽입
  - 관계선을 끊지 못하게 막기(`isCellDisconnectable`). 설정 관계를 그림에서 실수로 바꾸지 않도록 하기 위해서입니다.

## 5. 저장, 재배치, 출력

- 편집본은 보기 조합마다 따로 저장합니다(스냅샷 × 보기 × VPC × 리소스 × 참조 옵션). 저장할 때 리비전 번호로 충돌을 확인하고, 이전 리비전은 최근 20개까지 보관합니다.
- **자동 배치 다시 실행**은 새로 계산한 레이아웃에 사용자 메모를 옮깁니다(`carry_annotations`). 생성 셀이 아닌 사용자 추가 셀을 보존하고, 이전 편집본은 이력에 남깁니다.
- 출력 형식은 다음과 같습니다.
  - `.drawio`: 편집본 XML 그대로입니다. 이동한 좌표와 메모가 포함됩니다.
  - PNG: draw.io `export`(1×/2×/3×, 흰 배경 또는 투명)로 만듭니다. 같은 편집본에서 만들기 때문에 `.drawio`와 그림이 같습니다.
  - JSON·CSV: 구성도가 아니라 수집 데이터 전체입니다. 메모는 포함하지 않습니다.

## 6. 새 리소스 종류를 추가하려면

1. `collector.py`: 읽기 전용 API를 `OPERATIONS`에 추가합니다. IAM 정책 예시와 CloudFormation 역할도 갱신합니다.
2. `graph.py`: 노드와 관계를 API 필드 근거와 함께 만듭니다.
3. `views.py`: 어떤 보기에 나올지 정합니다(서브넷 배치면 `SUBNET_KINDS`, 연결 행이면 `layout.EDGE_KINDS`).
4. `drawio.py`: 아이콘을 `RESOURCE_ICONS` 또는 `ICONS`에 등록합니다.
5. `layout.py`: 라벨(`resource_label`)에 HA 역할과 크기를 넣습니다.
6. `samples.py`·`designer.py`: 샘플과 채팅 설계 단계를 추가하고, `tests/test_drawio.py`의 겹침·경계·경로 검사를 통과하는지 확인합니다.

## 한계

- 리소스가 100개 안팎이면 구조는 맞지만 선이 빽빽합니다. 큰 환경의 가독성은 아직 보장하지 않습니다.
- 선택 리소스 보기의 배치 관계선 일부는 draw.io 자동 경로를 씁니다.
- 라벨 너비는 추정치라서 글꼴이 다른 환경에서는 조금 어긋날 수 있습니다.
