# Changelog

형식은 [Keep a Changelog](https://keepachangelog.com/ko/1.1.0/)를, 버전은 [Semantic Versioning](https://semver.org/lang/ko/)을 따릅니다.

## [Unreleased]

### Added
- **현재 구성에서 설계 시작(brownfield):** 채팅 도구 `start_from_current`(앱에서 연 스냅샷 → 마지막 수집 순)와 `add_subnet_tier`(도구 29개).
  설계가 수집 스냅샷을 복사해 시작하고 기존 VPC(계층·AZ·NAT 라우트 테이블)·로드 밸런서·대상 그룹을 인식합니다. 결과 메타데이터 `based_on`.
  표준 아키텍처도 같은 방식으로 불러와 기존 VPC에 단계를 더할 수 있습니다. `diagram-bot chat --from`, 채팅 요청에 열린 스냅샷 전달(`snapshot`).
  실시간 Nova 2 Lite: three-tier에서 시작해 Redis 추가·검토(도구 3회, 기존 리소스 유지).
- **채팅 응답 스트리밍:** `POST /api/agent/chat/stream`(NDJSON: `text`·`tool`·`model`·`snapshot`, 마지막 줄 `done` 또는 `error`).
  AI 어시스턴트가 도구 진행을 보여 주고 설계 단계마다 저장된 구성도를 바로 엽니다. 실시간 Nova 2 Lite 턴(도구 8회, 11초)에서 단계별 수신 확인.
- **채팅으로 아키텍처 설계:** Strands Agents + Amazon Bedrock(Converse) 에이전트. 웹 앱 `채팅으로 설계` 패널, `diagram-bot chat`, `POST /api/agent/chat`.
  도구 24개(표준 아키텍처 7종, VPC·LB·EC2·ECS·EKS·RDS·Aurora·Redis·MSK·OpenSearch·EFS·Lambda·엔드포인트·피어링·VPN, 검토)와
  boto3 읽기 전용 수집 도구(STS, 리전 수집, 리소스 찾기, 검토). 기본 Amazon Nova 2 Lite(GPT-6 Luna·Claude Sonnet 4.5 선택 가능, 실패 시 Nova로 자동 전환)이며 실제 답한 모델을 표시.
- 설계안 스냅샷(`metadata.design`): `설계안`/`DESIGN` 표시, 검토·draw.io/PNG·변경 비교 지원.
- **앱에서 IAM Identity Center(SSO) 로그인:** 수집 화면의 `SSO로 로그인`(device code 방식). 승인 링크·코드만 화면에 보이고
  토큰·자격 증명은 서버 메모리에만 둡니다. 계정·역할 선택 후 그 역할로 수집하고, 채팅 에이전트도 같은 역할로 조회합니다(admin 전용, 감사 로그).
- `--env-file` (`collect`, `serve`, `chat`): `.env`의 AWS 키·리전을 읽고, 키가 있으면 셸 프로파일보다 우선.
- 문서: `docs/DRAWIO.md`(draw.io 라이브러리·자동 배치·편집기 연동), `docs/engineering/DEVOPS_AGENT_BENCHMARK.md`.
- **AWS Best Practice 사례 정렬:** `docs/ARCHITECTURE.md`(사례별 AWS 공식 출처·판정·AI/GPU Well-Architected).
  샘플 8종 — 신규 `ai-platform`(IGW·NAT 없는 사설 VPC, Bedrock 등 인터페이스 엔드포인트, EKS GPU 추론 3 AZ·EFA 학습 단일 AZ,
  Aurora pgvector, EFS). 각 사례의 practices에 그릴 수 없는 권고(CloudFront·WAF·Direct Connect·계정 분리 등)를 명시.
- 검토 규칙: `REL-GPU-TRAIN-AZ`(EFA 학습 노드가 여러 AZ), `REL-GPU-INFER-AZ`(GPU 추론 노드가 한 AZ), `SEC-EFA-SG`(EFA 보안 그룹 자기 참조 규칙 없음).
  단일 AZ EFA 학습 그룹은 `REL-ASG-AZ` low(의도적 단일 AZ)로 표시.
- 가속 인스턴스 아이콘(P3·P4d·P4de·G4dn·G4ad·Trainium·Inferentia·DL1), 그 밖의 GPU 계열은 `GPU` 라벨. Bedrock·SageMaker 엔드포인트 아이콘.
- 채팅 도구 `add_gpu_node_group`·`add_fsx_lustre`·`add_sagemaker_endpoint`(도구 27개), 표준 설계 `ai-platform`·`genai-rag`·`gpu-training`.
- **AI/GPU 수집·표기:** FSx(`fsx:DescribeFileSystems`), SageMaker 실시간 엔드포인트·HyperPod(`sagemaker:List*/Describe*`), 배치 그룹(`ec2:DescribePlacementGroups`)
  수집. EC2 라벨·속성 패널에 EFA·Capacity Block·ODCR·배치 그룹. 검토 탭에 `AI/ML` 영역과 규칙 13개 추가(`SEC-AI-PORT-OPEN`, `SEC-GPU-PUBLIC`,
  `REL-GPU-SUBNET-IP`, `REL-GPU-SPOT-EFA`, `OPS-GPU-PLACEMENT`, `REL-FSX-TRAIN-AZ`, `OPS-FSX-SCRATCH`, `SEC-AI-ENDPOINT-DNS`, `REL-AI-ENDPOINT-AZ`,
  `OPS-GPU-S3-ENDPOINT`, `SEC-SAGEMAKER-VPC`, `REL-SAGEMAKER-AZ`, `SEC-HYPERPOD-PUBLIC`). 샘플 10종(`genai-rag`, `gpu-training` 추가, `SAMPLE_VERSION` 3).
  읽기 전용 정책·CloudFormation 역할에 새 권한 추가.
- `scripts/render_samples.py`: examples/samples 산출물·PNG·README 재생성.

### Changed
- CI는 테스트 의존성을 `.[test]`에서 설치합니다(직접 적은 목록에 `strands-agents`가 빠져 2026-10-17 이후 실패). Actions를 Node 24 버전으로 올림.
- 샘플: data-platform은 피어링·VGW 대신 Transit Gateway 허브(두 VPC + VPN)와 Redshift Multi-AZ 서브넷 그룹 3개·AZ당 2노드,
  hybrid·data-platform은 고객 게이트웨이 2개(VPN 2개), microservices·eks-platform은 `ecr.api`(microservices는 `logs`도),
  three-tier는 `ssmmessages`·`ec2messages` 엔드포인트 추가.
- 저장된 샘플은 `SAMPLE_VERSION`이 바뀌면 서버 시작 시 다시 만듭니다(그 샘플의 위치 편집은 자동 배치로 다시 생성, 메모는 유지).
- VPC 상단의 연결 행(게이트웨이·엔드포인트·EKS)이 AZ 폭을 넘으면 줄을 바꿉니다(VPC가 넓어지지 않음).
- **AWS 콘솔형 화면:** 상단 내비게이션(리전·AI 어시스턴트·로그인 프로필), 좌측 내비게이션, 경로 표시·페이지 헤더·컨테이너·알약형 버튼.
  `AWS에서 새로 수집`은 우측 상단 **로그인 → 프로필 메뉴**로 이동(SSO 또는 프로파일·기본 인증 체인, `GET /api/identity`로 STS 확인, `역할 @ 계정` 표시).
  `채팅으로 설계`는 오른쪽에 붙는 **AI 어시스턴트** 패널(본문이 함께 줄어듦, Enter 전송).
- **전체 구성 VPC 카드:** 계층(퍼블릭·프라이빗·격리)별 서비스 아이콘과 개수(EC2 × 6 · ASG 2개, Aurora × 3 · Multi-AZ 등). 아이콘을 누르면 대표 리소스 속성.
- **여러 계정 연결도:** `diagram-bot combine`, `POST /api/snapshots/combine`, 앱의 `계정 통합 보기`로 계정·리전별 스냅샷을 합칩니다.
  RAM 공유 Transit Gateway와 계정 간 피어링은 소유 계정의 노드 하나로 이어지고, 전체 구성은 AWS 계정 프레임으로 VPC 카드를 묶습니다.
  샘플 `multi-account`(Network 계정 TGW·중앙 egress·VPN, 운영·스테이징 계정).
- **전체 구성 = 네트워크 연결도:** VPC 카드와 Transit Gateway·피어링·VPN 게이트웨이, VPN으로 이어진 온프레미스 고객 게이트웨이를 연결선으로 그립니다
  (예전에는 카드만 나열). VPC가 하나이고 이런 연결이 없는 스냅샷은 상세 구성부터 엽니다. 저장된 전체 구성 편집본은 `자동 배치 다시 실행`으로 새 배치를 받습니다.
- 상세 보기는 최소 60 % 줌으로 열리고(너비 맞춤), 알림은 화면 위쪽에 표시. 대상 그룹마다 등록 경로 색을 구분.

### Fixed
- VPC에 연결되지 않은 Lambda 함수가 리소스 목록·CSV에서 빠지던 문제. 전체 구성에 `VPC 미연결 리전 서비스` 카드로 표시하고 상세 구성에는 그리지 않습니다.
- 종료(terminated)된 EC2 인스턴스가 약 1시간 동안 `배치 미확인` 리소스로 그려지고 리소스 목록에 남던 문제.
- 채팅 에이전트의 리소스 찾기가 VPC 이름으로 범위를 좁히지 못하던 문제(`vpc` 범위 추가).
- 이전 화면의 안내 알림(예: `현재 보기에서 숨겨진 리소스입니다`)이 스냅샷·보기를 바꾼 뒤에도 남던 문제.
- 개발자 셸에 `AWS_PROFILE`이 설정되어 있으면 `make check`가 `ProfileNotFound`로 실패하던 문제.

## [0.5.0] - 2026-10-17

### Added
- **팀 모드:** `serve --host 0.0.0.0 --users users.json --certfile … --keyfile … --allowed-host …`. 루프백이 아닌 주소는 사용자 파일·TLS·허용 호스트가
  모두 있어야 열립니다. 로그인(PBKDF2-SHA256 600,000회, 실패 5회/15분 제한), HttpOnly·Secure·SameSite=Strict 세션 쿠키(8시간), HSTS,
  역할(viewer 읽기 / editor 편집·가져오기 / admin 수집·감사 로그), `diagram-bot user add|remove|list`.
- **SSO:** `--oidc-config`로 OpenID Connect 로그인(Authorization Code + PKCE, state·nonce·issuer·aud·만료 확인, 그룹→역할 매핑).
- **감사 로그:** 로그인·로그아웃·모든 변경 요청·수집 결과를 `audit.log`(JSON Lines)에 남기고 admin은 `GET /api/audit`로 봅니다. 로컬 모드도 기록합니다.
- AWS API 에뮬레이터(moto) 통합 테스트: 실제 boto3 직렬화로 20여 종 리소스를 만들고 병렬 수집·그래프·검토·보고서까지 확인합니다.
- `scripts/verify_team.py`: TLS 서버·로그인·역할·감사 로그 브라우저 검증(CI 포함).
- Apache License 2.0(`LICENSE`, `NOTICE`, 패키지 메타데이터).

### Fixed
- 한 API가 예상하지 못한 예외를 내도 그 API만 `조회 불가`로 기록하고 나머지 서비스 수집을 계속합니다.

## [0.4.0] - 2026-10-17

### Added
- **아키텍처 검토:** 수집한 설정만으로 안정성·보안 항목을 판정합니다(단일 AZ 로드밸런서·대상·Auto Scaling·RDS, reader 없는 DB 클러스터,
  ElastiCache 자동 장애 조치, 다른 AZ NAT 의존, OpenSearch 영역 인식·전용 마스터, MSK·Redshift·EFS 가용성, VPN 터널, ECS 실행 수,
  퍼블릭 DB, 저장·전송 암호화, IMDSv1, 퍼블릭 IP, 인터넷에 열린 관리·데이터 포트, 공개 EKS 엔드포인트, 수집 누락).
  앱의 `검토 결과` 탭과 검토 결과 CSV 내보내기, `review.md`, `diagram-bot review --fail-on <심각도>`(CI 게이트, 종료 코드 3).
- **변경 비교:** 두 스냅샷 사이의 리소스 추가·삭제, 설정 변경, 관계 변경. 상태 값(헬스·실행 수 등)은 제외합니다.
  앱의 `변경 비교` 탭, `GET /api/snapshots/{after}/diff/{before}`, `diagram-bot diff --fail-on-change`(드리프트 감지, 종료 코드 4).
- **교차 계정 수집:** `--role-arn`·`--external-id`(앱 수집 대화상자에도 있음). 맡은 역할 세션은 ReadOnlyAccess 세션 정책으로 제한됩니다.
  여러 `--role-arn`과 여러 리전(`--region a,b`)을 한 번에 수집해 `<계정>-<리전>` 디렉터리로 저장합니다.
- 보안 그룹 규칙 수집(`ec2:DescribeSecurityGroups`). 속성 패널에 인바운드 규칙을 보여 줍니다.
- 구성 요소: OpenSearch, ECS 서비스·태스크, EFS, Redshift, DocumentDB·Neptune, VPC 피어링, Site-to-Site VPN, 리소스별 고가용성 역할 표시.
- `deploy/readonly-role.cfn.json`: 멤버 계정용 읽기 전용 수집 역할 CloudFormation 템플릿(StackSet 배포용).
- `diagram-bot --version`, `/api/bootstrap`의 `version`.

### Changed
- 서비스별 병렬 수집과 adaptive 재시도로 대규모 계정 수집 시간을 줄였습니다. 같은 서비스의 호출은 순서대로 실행됩니다.
- 스냅샷 목록과 본문을 파일 수정 시각 기준으로 캐시합니다.
- IP 대상은 서브넷 CIDR과 AZ가 하나로 일치할 때 그 서브넷에 배치합니다.
- `make check`에 ruff 린트를 추가했고 CI도 린트·검토·비교 CLI를 실행합니다.

### Security
- 맡은 역할 세션에 ReadOnlyAccess 세션 정책을 적용합니다. 수집 정책 예시와 배포 템플릿은 테스트로 수집기 호출 목록과 일치하는지 확인합니다.

## [0.3.0] - 2026-10-17

- 인프라 아키텍처 표기(IGW·NAT·라우팅·RDS·Auto Scaling·엔드포인트·Transit Gateway·EKS·MSK·ElastiCache·Lambda),
  전체/상세/선택 보기, 결정적 배치 엔진, 편집 이력·복원, 브라우저 인수 검증.
