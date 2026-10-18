---
title: 채팅 설계 에이전트
summary: agent.py(모델 목록·대체, SYSTEM_PROMPT, Workspace, 도구 29개, Chat.ask와 진행 이벤트)와 designer.py(Design 상태, 표준 아키텍처, 기존 구성에서 시작하는 brownfield 설계, 스냅샷 메타데이터), CLI chat.
sources: [src/diagram_bot/agent.py, src/diagram_bot/designer.py, src/diagram_bot/routes/agent.py, src/diagram_bot/cli.py, tests/test_agent.py]
related: [http-api, howto-add-chat-tool, web-app, storage, team-mode-security, browser-egress, aws-access-and-sso, snapshot-model, cli]
updated: 2026-10-19
---
# 채팅 설계 에이전트

> Strands Agents의 `Agent`가 Amazon Bedrock 모델로 도구만 고른다. 리소스·관계·검토 결과는 모두 코드(`designer.Design`, 수집기, `findings`)가 만들고, 설계 단계마다 새 스냅샷이 저장되어 앱이 바로 그린다.

## 핵심
- 모델 목록 `agent.MODELS`(키 → (표시 이름, Bedrock 모델 ID)):
  - `nova-2-lite` → `us.amazon.nova-2-lite-v1:0`
  - `gpt-6-luna` → `global.openai.gpt-6-luna`
  - `claude-sonnet-4.5` → `us.anthropic.claude-sonnet-4-5-20250929-v1:0`
- `DEFAULT_MODEL = FALLBACK_MODEL = "nova-2-lite"`. 주석: 소유자 결정(2026-10-17), GPT-6 Luna는 계정에서 아직 쓸 수 없고 다른 모델이 거부되면 Nova 2 Lite로 넘어간다.
- `bedrock_model(key, session=None, record=None, streaming=True)`: `BedrockModel`을 감싼 `Recorded`가 `stream()` 호출마다 `record(key)`를 부른다. `temperature=0.2`, `max_tokens=4096`, 세션 기본값 `sdk_session("us-east-1")`(`sessions.py`).
- 설치는 선택 의존성이다(`pip install -e '.[agent]'`). 없으면 `/api/agent`의 `ready`가 `false`이고 채팅 요청은 503([[http-api]]).

## SYSTEM_PROMPT 원칙
- 구성도는 도구로만 그리고, 도구 결과에 있는 것만 말한다. 조언은 `describe_design`·`review_design` 결과의 리소스로 한정하고, 확인하지 않은 가격·수치는 말하지 않는다.
- 새 설계 순서: `new_design` → `add_vpc` → 로드 밸런서·서비스·데이터. 표준이 맞으면 `list_standard_architectures` → `apply_standard_architecture`.
- 기본값은 Well-Architected: AZ 3개(최소 2), 퍼블릭에는 LB·NAT만, 앱·데이터는 프라이빗, DB Multi-AZ, AZ별 NAT, S3 게이트웨이·SSM 인터페이스 엔드포인트. 비용 절감 요청이면 NAT 1개 등으로 조정하고 영향을 알린다.
- AI/GPU 워크로드 규칙(추론 다중 AZ, EFA 학습은 한 AZ 전용 서브넷·배치 그룹, 같은 AZ의 FSx for Lustre, Bedrock·SageMaker 인터페이스 엔드포인트, SageMaker 엔드포인트 2대 이상)과 표준 `ai-platform`·`genai-rag`·`gpu-training` 우선 고려.
- "지금 구성에 추가" 같은 요청은 `start_from_current` 후 결과의 VPC·LB 이름으로 `add_*`. 메시지 끝 `[현재 화면]` 메모가 "이 구성"의 대상이다. 이미 그 스냅샷에서 시작했으면 다시 부르지 않는다.
- 실제 계정은 `aws_identity`·`collect_aws_resources`·`find_collected_resources`·`review_collected_resources`로 읽기만 한다(쓰기 도구는 없다). 도구를 쓴다고 말만 하지 말고 바로 호출, 오류는 읽고 고쳐 재시도하거나 사용자에게 확인, 답은 간결한 한국어.

## Workspace (대화 하나의 상태)
- `Workspace(save, session_factory=None, region=None, allow_collection=True, default_account=None, load=None)`.
- 필드: `design`(`Design`), `collected`(마지막 수집 스냅샷), `current_snapshot`(앱에서 열린 스냅샷 ID, 매 턴 라우터가 설정), `changes`(이번 턴에 저장한 스냅샷 ID), `on_publish`(스트리밍 중 저장 알림), `model_session`(브라우저 egress용: `aws.model_session(user, "us-east-1")`가 만드는, 처음 쓸 때 가져오고 만료 전에 갱신하는 자격 증명 세션. 사용자가 고른 역할을 따르고 채팅을 만들 때는 AWS를 부르지 않는다), `lock`.
- 리전 기본값: `AWS_REGION` → `AWS_DEFAULT_REGION` → `us-east-1`. 수집 예상 계정: `default_account()`(서버에서는 사용자가 고른 SSO 계정 또는 `AWS_ACCOUNT_ID`).
- `publish(snapshot)`은 `save` 후 ID를 `changes`에 넣고 `on_publish`를 부른다. `publish_design(result)`는 `design.snapshot()`을 저장하고 결과에 `snapshot_id`를 붙인다.
- `context_note()`: 열린 스냅샷(없으면 마지막 수집)을 `\n\n[현재 화면] 설계안|수집한 구성 '<이름>' (<리전>, VPC n개)`로 요약한다. 진행 중인 설계가 그 스냅샷에서 시작했으면 `· 진행 중인 설계가 이 스냅샷에서 시작함`을 덧붙인다.

## 도구 29개 (`build_tools`)
설계 도구 대부분은 내부 `change(method, **kwargs)`로 `w.lock` 안에서 `Design` 메서드를 부르고 `publish_design`한다.
- `aws_identity` — STS로 계정 ID·ARN 확인.
- `collect_aws_resources` — 한 리전을 읽기 전용 수집, 종류별 개수·미완료 호출·검토 요약·`snapshot_id`. `allow_collection`이 거짓이면 `PermissionError`.
- `find_collected_resources` — 마지막 수집에서 `kind`·`search`·`vpc`로 찾기(`limit` 1–100).
- `review_collected_resources` — 마지막 수집의 검토 결과 상위 20개.
- `list_standard_architectures` — `STANDARDS` 사전.
- `apply_standard_architecture` — 표준 하나로 설계를 교체.
- `start_from_current` — 열린 스냅샷 또는 마지막 수집을 설계 출발점으로(기존 설계는 지움).
- `new_design` — 빈 설계 시작(`Design.reset`).
- `add_vpc` — VPC, AZ별 계층 서브넷, IGW, NAT(`per_az`·`single`·`none`), 라우트 테이블.
- `add_subnet_tier` — 빠진 계층을 AZ마다 /24로 추가.
- `add_load_balancer` — ALB/NLB, internet-facing은 public, internal은 app(없으면 web).
- `add_ec2_service` — AZ별 인스턴스, ASG, LB 대상 그룹.
- `add_ecs_service` — ECS Fargate 서비스, IP 대상 그룹.
- `add_eks_cluster` — 비공개 엔드포인트 EKS와 관리형 노드 그룹.
- `add_gpu_node_group` — 기존 EKS에 GPU/가속 노드 그룹, `training=true`면 한 AZ·EFA·배치 그룹.
- `add_fsx_lustre` — FSx for Lustre, 학습 서브넷이 있으면 거기.
- `add_sagemaker_endpoint` — VPC 안 SageMaker 실시간 엔드포인트.
- `add_database` — RDS(Multi-AZ, 읽기 복제본).
- `add_aurora` — Aurora writer 1 + reader N.
- `add_cache` — ElastiCache Redis primary + replica.
- `add_kafka` — MSK 클러스터.
- `add_opensearch` — OpenSearch 도메인(standby 선택).
- `add_file_system` — EFS와 AZ별 마운트 타깃.
- `add_lambda` — Lambda(VPC 연결 선택).
- `add_vpc_endpoints` — s3·dynamodb는 게이트웨이, 나머지는 인터페이스.
- `connect_vpcs` — VPC 피어링과 양쪽 라우트.
- `add_vpn` — VGW와 Site-to-Site VPN, 라우트 `192.168.0.0/16`.
- `describe_design` — VPC·계층·리소스 개수 요약.
- `review_design` — 설계 검토 결과 상위 20개.

도구 수는 `tests/test_agent.py`의 `test_start_from_current_uses_the_open_snapshot_then_the_last_collection`이 `len(tools) == 29`로 고정한다.

## Chat과 한 턴 (`Chat.ask`)
- `Chat(workspace, model, fallback, model_factory)`: 선호 모델과 (다르면) 대체 모델을 `model_factory(key, record=self.record)`로 만든다. 둘이면 Strands `ModelRouter([선호, 대체])`, 하나면 그 모델. Strands의 기본 `FallbackStrategy`가 실패한 호출 뒤 다음 후보로 넘긴다(`.venv`의 `strands.models.routing` docstring).
- 도구는 `SequentialToolExecutor`로 요청 순서대로 실행한다. 서비스가 로드 밸런서에 의존하는 식이라 병렬 실행하지 않는다.
- `record(key)`: 모델 호출마다 `calls`에 추가하고, 스트리밍 중이면 `{"type": "model", "model", "model_label", "fallback": key != self.model}`을 낸다.
- `ask(message, on_event=None)`: 잠금 → `changes`·`calls` 초기화 → `on_event`가 있으면 `progress_handler`(텍스트 `text`, 도구 시작·결과 `tool`)와 `on_publish`(`snapshot`)를 연결 → `agent(message + context_note())` → 훅 해제.
- 결과: `{reply, snapshots, tools_used, model, model_label, fallback, usage}`. `model`은 마지막 모델 호출(`calls[-1]`), `tools_used`는 이번 턴 메시지의 `toolUse` 이름, `usage`는 `result.metrics.accumulated_usage`.
- 도구 안의 `DesignError`·`ValueError`는 Strands가 오류 도구 결과로 모델에 돌려준다(`test_chat_draws_through_tools_and_falls_back_when_the_preferred_model_is_denied`).

## 서버에서의 생성 (`routes.agent`)
- 대화는 사용자별(`context.chats`), 모델이 바뀌면 새로 만든다. `Workspace`의 `save`는 스냅샷을 저장하고 `agent snapshot` 감사 항목을 남긴다. `session_factory`는 `AwsAccess.session(user, region)`(고른 Identity Center 역할, 없으면 SDK 체인), `load`는 `SnapshotStore.get`.
- SSO 로그인이 만료되면 채팅 생성·턴 모두 409와 다시 로그인하라는 메시지다(채팅은 유지된다).
- `default_chat(workspace, model)`: 브라우저 egress면(`aws.relay`가 있어 `workspace.model_session`이 설정됨) `bedrock_model(..., session=사용자 세션(us-east-1), streaming=False)`로 만든다. 중계는 응답 전체를 나르므로 Bedrock 응답 스트리밍은 끄지만, 도구 진행 이벤트는 그대로 스트리밍된다. 아니면 `Chat(workspace, model)`.
- SSO 역할을 새로 고르면(`POST /api/sso/login/{id}/use`) 그 사용자의 대화가 버려진다. 스트림 형식은 [[http-api]], 화면 처리는 [[web-app]].

## designer.py — Design 상태
- `Design(title, region)` → `reset()`: 리전 형식 검사, `sample = Sample("design", title, [], region, account="000000000000")`, `vpcs`, `steps`, `standard`, `target_groups`, `load_balancers`, `base`.
- `vpcs[slug]` = `{name, key, id, cidr, azs(AZ 문자열 예: "abc"), tiers{계층: [AZ별 서브넷 ID]}, private_tables, main_table, sgs}`. 계층은 `TIERS = ("public", "web", "app", "data")`.
- `load_balancers[slug]` = `{arn, vpc, sg, kind}`, `target_groups[slug]`.
- 결과는 수집기와 같은 API 모양 `raw`라 그래프·배치·검토·CloudFormation 파이프라인을 그대로 탄다. AWS는 호출하지 않는다.
- `DesignError(ValueError)`: 모델과 사용자에게 보이는 메시지. 이름은 `slug()`로 소문자·숫자·하이픈 40자.
- `routing_tables(vpc)`: NAT 프라이빗 테이블, 없으면 메인 테이블. 둘 다 없으면(라우트 테이블을 수집하지 못한 VPC) `DesignError`로 거부한다. 본 적 없는 테이블에 라우트를 쓰지 않기 위해서다. `add_vpc_endpoints`(s3·dynamodb가 있을 때), `connect_vpcs`, `add_vpn`이 쓴다.
- `add_subnet_tier(vpc, tier)`: VPC CIDR의 빈 /24를 AZ마다 하나. public은 기존 IGW(없으면 새로) 테이블, web·app은 같은 AZ의 NAT 테이블에 연결, data는 자체 테이블(격리).

## 표준과 brownfield
- `STANDARDS` 10개: `three-tier`, `microservices`, `nlb-alb`, `environments`, `hybrid`, `eks-platform`, `data-platform`, `ai-platform`, `genai-rag`, `gpu-training`. `apply_standard(name)`은 `SAMPLES[name]()`(또는 `register_standard`로 등록한 사내 표준 빌더)을 `start_from`으로 불러오고 `standard`를 기록한다. `STANDARDS`는 `samples.SUMMARIES`에서 도출한다.
- 모든 단계는 `@atomic`이라 실패한 단계는 설계를 바꾸지 않는다. 같은 이름의 ASG·EKS·DB·캐시·MSK 등은 `DesignError`로 거부한다.
- `Workspace.start_from_snapshot(snapshot_id)`: 인자 → `current_snapshot` → `collected` 순으로 고르고 없으면 `ValueError`.
- `Design.start_from_snapshot(snapshot)`: 합친 스냅샷(`metadata.accounts`)은 거부, `raw`를 깊은 복사, `base = {account_id, region, collected_at, title}`, 제목 `<원래 제목·계정> 기반 설계`. 원본 스냅샷은 바뀌지 않는다.
- `adopt()`: `raw`에서 단계 상태를 다시 만든다. 서브넷 계층은 route class `public` → `Tier` 태그 → 이름에 `web` → 이름에 data/db/rds/database/cache/isolated 또는 route class `isolated`이면 `data` → 나머지 `app`. AZ마다 계층별 서브넷 하나. VPC 키는 `Name` 태그 slug(영숫자가 없으면 VPC ID, 겹치면 `-<VPC ID 끝 6자>`). NAT 라우트가 있는 web·app 테이블이 `private_tables`.

## 스냅샷 메타데이터 (`Design.snapshot`)
`metadata`에 `design: true`, `sample`(표준 이름 또는 `design`), `title`, `steps`(`record(step, **params)`가 쌓은 단계, 빈 값 제외), `practices`, `based_on`(brownfield일 때), `limitations`(설계안·가정 안내)를 넣는다. 앱은 `metadata.design`으로 `설계 ·` 라벨과 "실제 계정 아님" 문구를 단다.

## CLI `chat`
- `diagram-bot chat [--model] [--output reports/chat] [-m 메시지 ...] [--from snapshot.json]`와 공통 `--env-file`.
- 각 저장은 `report.write_report`로 `<output>/<NN>-design` 또는 `<NN>-<계정>-<리전>` 보고서가 된다. 도구 완료·오류는 stderr에 찍힌다.
- `--from`: JSON을 읽어 `graph`를 다시 만들고 `workspace.collected`로 둔다. CLI의 `Workspace`에는 `load`가 없어서 `start_from_current`는 이 스냅샷에서 시작한다.

## 바꿀 때 주의
- 도구를 더하거나 빼면 `build_tools`의 반환 목록, `len(tools) == 29` 단언, `SYSTEM_PROMPT`를 함께 본다. 절차는 [[howto-add-chat-tool]].
- 결과 사전 키를 바꾸면 `routes/agent.py`, `static/app.js`, `tests/test_agent.py`의 스트림 테스트를 고친다.
- 팀 모드 수집 제한(`allow_collection`)과 보안 설명은 [[team-mode-security]], `SECURITY.md`의 채팅 에이전트 행.

## 관련
- [[howto-add-chat-tool]] — 설계 단계와 도구를 추가하는 절차.
- [[http-api]] — `/api/agent*` 엔드포인트와 NDJSON 이벤트.
- [[web-app]] — 채팅 패널과 스냅샷 즉시 표시.
- [[storage]] — 설계 스냅샷이 저장되는 곳.
- [[browser-egress]] — 브라우저 egress에서 Bedrock 호출 경로.
- [[aws-access-and-sso]] — 수집 도구가 쓰는 사용자 세션.
- [[snapshot-model]] — 설계 스냅샷의 `metadata` 필드 계약.
- [[cli]] — `chat` 외 하위 명령.
