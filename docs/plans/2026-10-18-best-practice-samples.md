# 구현 계획: 사례 AWS Best Practice 정렬 + AI/GPU 사례

Created: 2026-10-18 · 근거: [docs/ARCHITECTURE.md](../ARCHITECTURE.md) §4(기존 사례 변경안 1–7), §5.6(AI/GPU 변경안 A1–A3)
상태: **완료** (2026-10-18) — 0절 결정은 권장안 채택. 계획과 달라진 점: GPU 아이콘은 정확한 계열에만(아이콘에 계열명이 찍혀 있음), 엔드포인트를 줄이는 대신 VPC 연결 행 줄바꿈을 구현(위험 1의 대응을 레이아웃 소폭 수정으로 변경), `scripts/render_samples.py` 추가.

## 목표와 완료 기준

- 기존 7개 사례가 ARCHITECTURE.md 3절의 MUST·HIGH 항목을 모두 충족하고, 그릴 수 없는 권고는 practices 문구로 명시한다.
- 8번째 사례 `ai-platform`을 샘플·채팅 표준 설계로 추가한다.
- GPU 인스턴스·Bedrock 엔드포인트를 전용 AWS 아이콘으로 그리고, GPU 배치 검토 규칙 3개를 추가한다.
- 전체 완료 기준: `make check` 통과, `scripts/verify_demo.py` 통과(사례 검사 추가 포함), **모든 사례의 검토 결과가 low 이하**
  (`tests/test_findings.py::test_samples_are_clean` 유지), examples/samples·docs/images 재생성, 문서의 사례 수 7 → 8.

## 범위 밖

- 수집 범위 확장(CloudFront·WAF·EIP·TGW 라우팅 테이블·Direct Connect·FSx·SageMaker·S3·다중 계정 구성도).
  해당 권고는 practices 문구로만 남긴다(ARCHITECTURE.md 1절).
- 레이아웃 엔진 변경. 새 사례가 기존 가독성 테스트를 통과하지 못하면 사례 구성을 조정하고, 엔진 수정은 별도 계획으로 한다.
- 실 AWS 배포·검증(오프라인 합성 샘플만).

## 0. 착수 전 결정 (소유자)

| # | 결정 | 권장안 | 이유 |
| --- | --- | --- | --- |
| D1 | data-platform의 VPC 간·온프레미스 연결 | **TGW 허브**: 두 VPC와 VPN을 TGW에 연결하고 피어링·VGW 제거 | 피어링으로는 분석 VPC의 VPN을 데이터 플랫폼 VPC가 쓸 수 없음(MUST). 피어링·VGW 렌더링 테스트는 `tests/fixtures.py` 합성 스냅샷으로 옮겨 유지 |
| D2 | 기존 데이터 디렉터리의 샘플 갱신 | **샘플 버전 도입**: 메타데이터에 `sample_version`, 서버 시작 시 버전이 다르면 덮어씀 | 지금은 `sample-*.json`이 없을 때만 만들어 기존 사용자는 옛 샘플을 계속 봄(`server.py:149`). 편집본은 새 그래프에 `carry_annotations`로 메모만 옮겨짐 |
| D3 | ai-platform 벡터 저장소 | **Aurora PostgreSQL(pgvector)** | Bedrock Knowledge Bases는 VPC 안 OpenSearch 도메인을 지원하지 않음. Aurora는 수집·도형화 가능 |
| D4 | 선택 항목(§4 7번: microservices 3 AZ, eks Aurora reader 추가·/28 클러스터 서브넷, environments egress VPC) | **이번 범위에서 제외** | OPT 강도. 레이아웃 폭·테스트 영향 대비 효과가 작음 |

## 1단계 — 샘플 빌더·서버 기반 작업

변경 파일: `src/diagram_bot/samples.py`, `src/diagram_bot/server.py`, `src/diagram_bot/designer.py`

1. **보안 그룹 규칙 확장** (`Sample.security_group`)
   - 인그레스 튜플에 프로토콜 `-1`(전체)과 자기 참조(`"self"` → 자신의 GroupId)를 허용.
   - `egress` 매개변수 추가(기본은 지금처럼 0.0.0.0/0 전체). EFA SG는 자기 자신 + VPC CIDR만.
   - 수집 API 형식(`IpPermissions`, `UserIdGroupPairs`, `IpProtocol: "-1"`)을 그대로 따름. `findings.py`의 SG 파서가 `-1`을 이미
     처리하는지 확인하고, 아니면 함께 수정.
2. **샘플 버전** (D2)
   - `samples.py`에 `SAMPLE_VERSION = 2`, `Sample.snapshot()` 메타데이터에 `sample_version` 기록.
   - `server.py`: 저장된 `sample-<key>.json`의 `metadata.sample_version`이 다르면 다시 만든다. `demo.json`은 그대로.
   - 테스트: 옛 버전 파일이 있는 데이터 디렉터리로 앱을 만들면 새 버전으로 교체되는지(`tests/test_server.py`).
3. **NAT 없는 VPC의 게이트웨이 엔드포인트** (`Design.add_vpc_endpoints`)
   - 지금은 NAT 경로가 있는 프라이빗 라우팅 테이블이 없으면 S3 게이트웨이 엔드포인트를 거부한다. 계층별 라우팅 테이블이
     있으면(NAT 유무와 무관) 허용하도록 바꾼다. ai-platform(IGW·NAT 없음)과 채팅 설계에 필요.
   - 테스트: `nat="none"` VPC에 `s3` 엔드포인트 추가 성공.

완료 기준: `make check` 통과, 기존 샘플 출력 변화 없음(버전 메타데이터 제외).

## 2단계 — 기존 사례 수정 (§4 1–5)

변경 파일: `src/diagram_bot/samples.py`, 필요 시 `tests/fixtures.py`, `tests/test_graph_report.py`, `scripts/verify_demo.py`

| 작업 | 사례 | 구체 변경 | 확인 |
| --- | --- | --- | --- |
| 2.1 | data-platform | 분석 VPC에 `warehouse-c` 서브넷 추가(10.71.12.0/24), Redshift 서브넷 그룹 3개, `nodes=4`(AZ당 2) | Redshift primary/secondary 역할 라벨 유지, `REL-REDSHIFT-AZ` 없음 |
| 2.2 | data-platform (D1) | `transit_gateway("tgw-dp…")`, 두 VPC의 TGW attachment(데이터 플랫폼은 app 서브넷 3개, 분석은 warehouse 3개), VPN을 `tgw=`로 종단. 프라이빗 라우팅 테이블에 상대 VPC CIDR과 192.168.0.0/16 → TGW. 피어링·VGW 제거 | 상세 보기에서 TGW attachment·VPN·CGW가 보임, 공유 선분 검사 통과 |
| 2.3 | data-platform, hybrid | 두 번째 CGW(`198.51.100.20`, 다른 ISP 가정)와 두 번째 VPN 연결, 같은 TGW. 라벨에 두 연결 모두 `2/2 UP` | `REL-VPN-TUNNEL` 없음, 온프레미스 상자에 CGW 2개 |
| 2.4 | microservices, eks-platform | `ecr.api` 인터페이스 엔드포인트 추가, microservices에 `logs` 추가 | 엔드포인트 행 폭이 VPC 폭을 넘지 않는지(가독성 테스트) |
| 2.5 | three-tier | `ssmmessages`·`ec2messages` 인터페이스 엔드포인트 추가 | 같음 |
| 2.6 | 피어링·VGW 회귀 보호 (D1) | `tests/fixtures.py`에 피어링 + VGW VPN을 쓰는 작은 합성 스냅샷 추가. `test_graph_report.py`의 피어링·VPN 단언을 이 픽스처로 옮기고, data-platform 단언은 TGW 기준으로 교체 | 피어링·VGW 렌더링 테스트가 계속 존재 |
| 2.7 | 브라우저 검증 | `verify_demo.py` 9번 검사(data platform)의 "VPN in the peered VPC"를 TGW VPN으로 바꾸고, CGW 2개 확인 추가 | 27개 검사 유지 |

각 작업 후 `make check`. 2.2는 변경 폭이 커서 별도 커밋.

## 3단계 — practices 문구 (§4 6)

- 각 사례 `practices` 끝에 "도형 밖 권고" 항목 1–3개를 추가한다(ARCHITECTURE.md 3절의 P·X 항목). 예:
  - three-tier: "엣지: CloudFront(정적 콘텐츠 S3)·AWS WAF·Route 53 헬스 체크 (수집 범위 밖)"
  - environments: "권장: 환경별 계정 분리, Network 계정의 TGW를 RAM으로 공유, 운영·스테이징 TGW 라우팅 테이블 분리 (수집 범위 밖)",
    "스테이징 NAT 1개는 비운영 비용 절충"
  - hybrid: "운영 주 연결은 Direct Connect, VPN은 백업 (수집 범위 밖)"
  - nlb-alb: "고정 IP는 AZ별 Elastic IP (수집 범위 밖)"
- practices는 샘플 메타데이터·`examples/samples/README.md`·채팅 `apply_standard` 응답에 그대로 쓰인다. 별도 코드 변경 없음.
- 문구 끝에 ARCHITECTURE.md 근거 ID(REL02-BP01 등)를 괄호로 단다.

## 4단계 — 가속 인스턴스·Bedrock 아이콘 (A2)

변경 파일: `src/diagram_bot/layout.py`(`icon_variant`), `src/diagram_bot/drawio.py`(`RESOURCE_ICONS`/`ICONS`), `tests/test_drawio.py`

1. 고정판 draw.io 32.4.1의 `Sidebar-AWS4.js`에서 정확한 도형 이름을 확인한다. 확인된 후보: `mxgraph.aws4.p4d_instance`,
   `mxgraph.aws4.trainium_instance`, resIcon `mxgraph.aws4.bedrock`. G·Inferentia·DL 계열(`G4dn Instance`, `Inferentia`,
   `DL1 Instance`)은 도형 이름을 같은 방식으로 확인.
2. `icon_variant`: EC2의 `InstanceType` 계열로 변형 결정 — `p*` → GPU(p4d 아이콘), `g*` → G 계열, `trn*` → Trainium,
   `inf*` → Inferentia, `dl*` → DL1. 그 외는 지금처럼 일반 인스턴스.
3. VPC 엔드포인트: `ServiceName`이 `.bedrock`, `.bedrock-runtime`, `.bedrock-agent*`로 끝나면 Bedrock 타일, `sagemaker.*`는
   SageMaker 타일. 라벨(서비스명·서브넷 수)은 그대로.
4. 테스트: 인스턴스 유형별 스타일에 기대 도형 이름이 들어가는지, 일반 EC2·일반 엔드포인트 스타일이 바뀌지 않는지.

완료 기준: 기존 7개 사례의 출력 XML에서 아이콘 변화 없음(가속 인스턴스가 없으므로), `make check` 통과.

## 5단계 — `ai-platform` 사례 (A1)

변경 파일: `src/diagram_bot/samples.py`(`ai_platform()` + `SAMPLES`), `src/diagram_bot/designer.py`(`STANDARDS`)

구성(ARCHITECTURE.md 5.5절):

| 요소 | 값 |
| --- | --- |
| VPC | `vpc-0a1b2c3d4e5f60009`, 10.80.0.0/16, `ai-platform`, IGW·NAT 없음 |
| 서브넷 | `app-a/b/c` 10.80.0.0/19 · 10.80.32.0/19 · 10.80.64.0/19 (Tier app), `data-a/b/c` 10.80.200–202.0/24 (Tier data), `train-a` 10.80.96.0/20 (Tier app, AZ a만) |
| 라우팅 | `rtb-ai-app`(app·train, 0.0.0.0/0 → TGW = 중앙 egress), `rtb-ai-data`(로컬만 = 격리). S3 게이트웨이 엔드포인트는 두 테이블 모두 |
| TGW | `tgw-ai…` + attachment(app 서브넷 3개). 구성도에서 서브넷이 "TGW 경로 프라이빗"으로 분류됨 |
| 인터페이스 엔드포인트 | app 서브넷 3개에 `bedrock-runtime`, `bedrock-agent-runtime`, `ecr.api`, `ecr.dkr`, `sts`, `eks`, `eks-auth`, `ec2`, `elasticloadbalancing`, `logs`, `ssm` (11개; `sagemaker.runtime`은 practices 문구) |
| EKS | `ai-eks`, 비공개 엔드포인트. 노드 그룹 `system`(m7g.large × 3 AZ, min 3), `gpu-inference`(g6e.2xlarge × 3 AZ, min 3·max 12, taint는 문구), `gpu-train`(p5.48xlarge × 2, `train-a`만, min 0·max 8) |
| 보안 그룹 | `sg-ai-nodes`(VPC CIDR 443), `sg-train-efa`(자기 참조 전체 허용, 이그레스 자신 + VPC CIDR), `sg-ai-db`(5432 ← sg-ai-nodes), `sg-ai-endpoints`(443 ← sg-ai-nodes) |
| 진입 | 내부 ALB `ai-inference-alb`(app 3개) → 대상 그룹 `inference-pods`(IP 대상, AZ별 파드 IP 3개, `/healthz`) |
| 벡터 저장소 (D3) | Aurora PostgreSQL `ai-vectors` writer a + reader b, data 서브넷 |
| 모델 캐시 | EFS `ai-model-cache`, 마운트 타깃 app 3개 |

practices 문구(도형 밖 권고 포함): 사설 연결(GENSEC01-BP02), 3 AZ 추론·벡터 DB(GENREL02-BP01), 학습 단일 AZ와 체크포인트
복구(HPCREL01-BP02, GENREL06-BP01), 학습 배치는 클러스터 배치 그룹 **또는** Capacity Block, FSx for Lustre(AZ a, S3 연동),
Bedrock 호출 로깅·Guardrails·엔드포인트 정책, Karpenter·KEDA·device plugin은 Kubernetes 객체라 그리지 않음.

검증 포인트(테스트로 고정):
- 가독성·공유 선분 검사(`tests/test_drawio.py`의 사례 매개변수화 테스트)에 자동 포함. 엔드포인트 11개 행이 VPC 폭 안에 들어가는지,
  `train-a`만 있는 AZ a 열의 높이가 다른 열과 어긋나도 겹침이 없는지 확인. 넘치면 엔드포인트를 핵심 6개로 줄이고 나머지는 문구로.
- 검토 결과 low 이하(6단계 규칙 포함).
- 아이콘: gpu-inference는 G 계열, gpu-train은 GPU(p) 아이콘, Bedrock 엔드포인트 2개는 Bedrock 타일.
- `test_server.py`의 샘플 수 단언은 `SAMPLES` 기준이라 자동 반영.

## 6단계 — GPU 검토 규칙 (A3)

변경 파일: `src/diagram_bot/findings.py`, `tests/test_findings.py`

| 규칙 | 심각도 | 판정 (수집 필드만) | 권고 문구 근거 |
| --- | --- | --- | --- |
| `REL-GPU-TRAIN-AZ` | medium | EFA 학습 계열(`p4d/p5/p5e/p5en/p6*`, `trn1/trn2`) 인스턴스로 이뤄진 ASG·노드 그룹의 인스턴스가 2개 이상 AZ | EFA는 AZ를 넘지 못함 (EC2 EFA, EKS AI/ML Networking) |
| `REL-GPU-INFER-AZ` | medium | `g*`/`inf*` 계열 ASG가 한 AZ에만 있음 | GENREL02-BP01, MLCOST06-BP03 |
| `SEC-EFA-SG` | medium | EFA 학습 계열 인스턴스의 SG 어느 것에도 자기 참조 전체 허용 규칙이 없음 | EKS AI/ML Networking, HyperPod |

기존 규칙과의 관계:
- `REL-ASG-AZ`(ASG가 한 AZ의 서브넷만 사용, medium)는 EFA 학습 계열 ASG에 대해서는 **낮춰서** low
  "강결합 학습은 의도적 단일 AZ — 체크포인트·재시작 경로 확인"으로 낸다(HPCREL01-BP02). 그렇지 않으면 ai-platform이 medium을 받아
  사례 청결 테스트가 실패한다. `REL-GPU-INFER-AZ`와 `REL-ASG-AZ`가 같은 ASG에 중복되지 않게 G·Inf 계열은 새 규칙만 낸다.
- 계열 판정은 `InstanceType` 접두사 표(코드 상수 하나)로. 표에 없는 유형은 기존 규칙 그대로.

테스트: 규칙마다 위반·정상 합성 스냅샷 한 쌍, ai-platform에서 `REL-ASG-AZ`가 low로 바뀌는지, 기존 사례 결과 불변.

## 7단계 — 채팅 설계 에이전트

변경 파일: `src/diagram_bot/designer.py`, `src/diagram_bot/agent.py`, `tests/test_agent.py`

1. `STANDARDS["ai-platform"] = "AI 플랫폼 (사설 EKS GPU 추론·학습, Bedrock 엔드포인트, Aurora pgvector, EFS)"`.
   `apply_standard`는 `SAMPLES`를 쓰므로 자동 동작. `test_agent.py`의 표준 반복 테스트에 자동 포함.
2. `Design.add_gpu_node_group(vpc, cluster, name, instance_type, per_az=1, training=False, minimum=0, maximum=None)`:
   - `training=True`면 app 계층의 첫 AZ 서브넷에만 배치하고 EFA SG(자기 참조)를 만든다. 아니면 계층의 모든 AZ.
   - EKS 클러스터에 노드 그룹 추가(기존 `add_eks_cluster`가 만든 클러스터에 붙임). 클러스터가 없으면 `DesignError`.
   - 가속 계열이 아닌 유형은 거부(`DesignError`: "GPU/가속 인스턴스 유형을 지정하세요").
3. agent 도구 `add_gpu_node_group` 추가(도구 수 24 → 25), 시스템 프롬프트에 "학습은 단일 AZ, 추론은 여러 AZ" 한 줄.
4. 테스트: 스크립트 모델로 "사설 GPU 추론 플랫폼" 대화 → 도구 호출 순서·검토 결과 low 이하, 잘못된 유형 거부 메시지.

## 8단계 — 문서·산출물

1. `examples/samples/<key>/` 재생성(`diagram-bot demo --sample <key> --output examples/samples/<key>`, 8개). PNG 렌더링 경로는
   기존 생성 방식을 따름(착수 시 확인). `examples/samples/README.md`에 ai-platform 절과 변경된 사례 수치 갱신.
2. `scripts/verify_demo.py`에 ai-platform 검사 1개 추가(GPU 아이콘, Bedrock 엔드포인트, 학습 노드 그룹 단일 AZ) → 28개.
   docs/images에 `ai-platform.png` 추가 여부는 README 배치와 함께 결정.
3. 사례 수 7 → 8: `README.md`(2곳), `CHANGELOG.md`(Unreleased), `docs/STATUS.md`, `docs/AGENT_BRIEF.md`.
   도구 수 24 → 25: README·CHANGELOG·STATUS.
4. `docs/ARCHITECTURE.md`: 각 사례 표의 "현재 샘플" 열을 반영 결과로 갱신하고 §4·§5.6 상태를 "완료"로.
5. `docs/PROGRESS_LOG.md`·`NEXT_PLAN.md`·`COMPLETED_SUMMARY.md` 체크포인트.

## 9단계 — 최종 검증

- `make check` (lint + pytest + CLI 왕복). 예상 테스트 수는 119 + 약 12.
- `diagram-bot review --fail-on medium`을 사례 8개 스냅샷에 실행 → 모두 종료 코드 0.
- 새 데이터 디렉터리로 `serve` 후 `verify_demo.py` 28개 검사, 페이지 오류·외부 요청 0.
- 사례 8개 상세 보기를 1440×900에서 캡처해 모델 시각 검토(아이콘·라벨·겹침). 기존 데이터 디렉터리(`reports/session-20261018`)로
  서버를 다시 띄워 샘플이 버전 2로 교체되는지 확인(D2).

## 순서와 커밋

| 순서 | 커밋 | 의존 |
| --- | --- | --- |
| 1 | 1단계 빌더·서버 기반 | — |
| 2 | 2.1·2.4·2.5 (작은 사례 수정) | 1 |
| 3 | 2.2·2.3·2.6·2.7 (TGW 허브·CGW 이중화·회귀 픽스처) | 1 |
| 4 | 3단계 practices 문구 | 2, 3 |
| 5 | 4단계 아이콘 | — (1과 병행 가능) |
| 6 | 6단계 검토 규칙 | 1 |
| 7 | 5단계 ai-platform | 1, 5, 6 |
| 8 | 7단계 채팅 설계 | 7 |
| 9 | 8·9단계 산출물·문서·검증 | 전부 |

각 커밋 전 `make check`. 9번 전에는 사례 출력이 바뀌므로 docs/images·examples 재생성은 마지막에 한 번만.

## 위험과 대응

| 위험 | 대응 |
| --- | --- |
| 엔드포인트 행(ai-platform 11개 + 엔드포인트 늘어난 사례)이 넓어져 가독성 테스트 실패 | 행 줄바꿈 대신 엔드포인트 수를 줄이고 문구로 이동. 레이아웃 수정은 범위 밖 |
| `train-a`만 있는 AZ 열에서 서브넷 높이 불균형으로 겹침·경로 공유 | 5단계 검증에서 먼저 확인. 실패 시 train 서브넷을 app 계층과 같은 행으로 두지 않고 별도 계층(Tier `train`)으로 정렬 가능한지 검토 후 결정 |
| `REL-ASG-AZ` 하향 조정이 실제 계정의 잘못된 단일 AZ GPU 그룹을 가림 | 하향은 EFA 학습 계열에만, 문구에 "의도적인지 확인" 명시. 추론 계열은 새 medium 규칙으로 더 엄격 |
| 기존 사용자의 샘플 편집본이 새 그래프와 어긋남 (D2) | `carry_annotations`로 메모 유지, 위치 편집은 자동 배치로 재생성됨을 CHANGELOG에 명시 |
| 피어링·VGW 경로가 샘플에서 사라져 회귀를 놓침 (D1) | 2.6 픽스처로 렌더링·뷰 테스트 유지 |
