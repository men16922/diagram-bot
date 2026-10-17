# AWS DevOps Agent 벤치마킹과 채팅 설계 에이전트

조사일: 2026-10-17. 목적은 AWS DevOps Agent의 기능을 정리하고, Diagram Bot이 무엇을 가져올지 정하는 것입니다.
대상은 Strands Agents와 Amazon Bedrock 기반의 "채팅으로 표준 인프라 아키텍처 그리기"입니다.

## AWS DevOps Agent 요약

- 2025년 12월 프리뷰, **2026-03-31 GA**. 에이전트 자체는 us-east-1에서 실행되며 여러 계정·리전의 애플리케이션을 볼 수 있습니다.
- **Agent Space:** 에이전트의 접근 범위입니다. 애플리케이션, 온콜 팀, 중앙 운영 단위로 만들고, 공간별 IAM 역할과 데이터를 분리합니다.
- **Topology:** 애플리케이션, 구성 서비스, AWS 리소스, 그리고 그 관계를 자동으로 찾아 계속 갱신합니다. 배포 이력도 함께 기록해 배포가 원인인지 판단할 때 씁니다.
- **Investigation:** 경보(CloudWatch, PagerDuty, Datadog, Dynatrace, ServiceNow 등)나 수동 요청으로 시작합니다.
  지표, 로그, 트레이스, 코드 변경, 배포를 엮어 가설을 여러 개 세우고 반증까지 확인한 뒤 근본 원인을 제시합니다. 조사 과정은 Investigation Journal에 기록됩니다.
- **Chat:** 조사 도중 후속 질문을 하고 조사 방향을 조정할 수 있습니다. AWS Support 케이스로 넘길 수도 있습니다.
- **Mitigation / Recommendation:** 완화 계획을 스펙 형태로 제시합니다(Kiro 등에 넘김). 과거 사건을 바탕으로 관측성, 인프라 구성, 배포 파이프라인 개선안도 냅니다.
- **확장:** 사용자 정의 에이전트 스킬(런북), 직접 연결하는 MCP 서버(Grafana, Prometheus 등), GA에서 추가된 멀티클라우드·온프레미스 지원이 있습니다.
- 웹 앱(IAM Identity Center), Slack 알림, 티켓 갱신을 지원합니다.

출처:
- [AWS News Blog (프리뷰 발표)](https://aws.amazon.com/blogs/aws/aws-devops-agent-helps-you-accelerate-incident-response-and-improve-system-reliability-preview)
- [AWS DevOps Blog (다중 에이전트 추론)](https://aws.amazon.com/jp/blogs/devops/how-aws-devops-agent-uses-multi-agent-reasoning-to-find-root-causes/)
- [Dynatrace 통합](https://www.dynatrace.com/news/blog/integration-with-aws-devops-agent-autonomous-investigations-powered-by-production-context/)
- [GA 정리](https://sabin.is-a.dev/posts/aws-devops-agent-ga.html)

성능 수치(MTTR 75% 단축 등)는 벤더가 발표한 것이라 여기서는 쓰지 않습니다.

## 기능 대응표

| DevOps Agent | Diagram Bot (이번 구현) | 비고 |
| --- | --- | --- |
| Topology 자동 발견 | `collect_aws_resources` 도구 → 기존 읽기 전용 수집기(boto3) → 관계 그래프·구성도 | API 필드 근거만 사용하고 통신 여부는 추정하지 않음 |
| Agent Space(범위·IAM) | `.env`/SSO/교차 계정 읽기 전용 역할, 팀 모드 역할(viewer/editor/admin), `AWS_ACCOUNT_ID` 계정 일치 확인 | 채팅 수집은 admin만 가능 |
| 채팅으로 조사·조정 | **채팅으로 설계·조회**: Strands 에이전트가 도구를 골라 구성도를 그림 | 조사 대상이 장애가 아니라 구성·설계 |
| Recommendation | `review_design` / `review_collected_resources` → 27개 결정적 규칙(신뢰성·보안·운영)과 권장 조치 | 모델이 아니라 코드가 판정 |
| Mitigation 스펙 | (없음) 설계안 draw.io/PNG/JSON 출력 | IaC 생성은 다음 후보 |
| Investigation Journal | 감사 로그(`agent snapshot`, 모든 변경 요청), 스냅샷 이력, 변경 비교 | |
| 경보 연동, 지표·로그·트레이스 상관 분석 | 범위 밖 | 데모 범위를 넘음 |

## 이번에 만든 것

**표준 인프라 아키텍처 설계 에이전트.** 사용자가 채팅으로 요청하면 에이전트가 함수(도구)를 호출하고, 그 결과로 구성도가 그려집니다.

- **프레임워크:** Strands Agents 1.61 (`strands-agents`), 도구는 `@tool` 함수입니다.
  설계 단계는 서로 의존하므로(서비스는 로드 밸런서가 있어야 함) `SequentialToolExecutor`로 요청 순서대로 실행합니다.
- **모델:** Amazon Bedrock Converse API (`strands.models.bedrock.BedrockModel`). `ModelRouter` + `FallbackStrategy`로 대체 모델을 둡니다.
  - 기본: **OpenAI GPT-6 Luna** (`global.openai.gpt-6-luna`)
  - 대체: **Amazon Nova 2 Lite** (`us.amazon.nova-2-lite-v1:0`)
  - 선택: Anthropic Claude Sonnet 4.5
  - 실제로 답한 모델은 응답마다 표시합니다(대체 모델을 썼는지 포함).
- **도구 24개**
  - 수집(읽기 전용, boto3): `aws_identity`, `collect_aws_resources`, `find_collected_resources`, `review_collected_resources`
  - 표준 아키텍처: `list_standard_architectures`, `apply_standard_architecture` (3계층, 마이크로서비스, NLB→ALB, 환경 분리, 하이브리드, EKS 플랫폼, 데이터 플랫폼)
  - 설계 단계: `new_design`, `add_vpc`(AZ·계층·NAT 전략), `add_load_balancer`, `add_ec2_service`, `add_ecs_service`, `add_eks_cluster`,
    `add_database`, `add_aurora`, `add_cache`, `add_kafka`, `add_opensearch`, `add_file_system`, `add_lambda`, `add_vpc_endpoints`,
    `connect_vpcs`, `add_vpn`, `describe_design`, `review_design`
- **표준을 지키는 방식:** 모델은 도구만 고릅니다.
  - CIDR 할당, AZ 분산, 라우트 테이블(퍼블릭 → IGW, 프라이빗 → AZ별 NAT, data는 격리), 계층 간 최소 허용 보안 그룹, Multi-AZ 대기 AZ 같은 세부 값은 코드(`designer.py`)가 정합니다.
  - 잘못된 요청(CIDR 겹침, 없는 VPC, 계층 누락)은 고칠 방법이 담긴 오류로 모델에 돌려줍니다.
- **렌더링:** 설계안은 수집 스냅샷과 같은 `raw` 형식으로 저장됩니다. 그래서 기존 그래프, 자동 배치, 검토, draw.io/PNG 출력, 변경 비교가 그대로 적용됩니다. 화면과 문서에는 `설계안`/`DESIGN`으로 표시해 실제 계정 데이터와 구분합니다.
- **접점**
  - 웹 앱 `채팅으로 설계` 패널: 응답 후 새 구성도를 바로 불러옵니다.
  - CLI `diagram-bot chat [--env-file .env] [-m 메시지]`
  - API `POST /api/agent/chat`

## 검증 (2026-10-17)

- 오프라인 게이트: 설계 단계, 오류, 표준 7종, 도구 스키마, 스크립트 모델로 대화 흐름과 GPT-6 거부 시 대체를 확인했습니다. moto로 수집 도구를, 채팅 API·감사 로그, `.env` 로더, 설계안 배치 가독성도 확인했습니다(`tests/test_agent.py`, `tests/test_drawio.py`).
- 실제 Bedrock(us-east-1), 브라우저 채팅
  - 3 AZ 3계층 쇼핑몰 설계: 도구 10회, 구성도 8회 갱신. 검토 결과 높음 0건.
  - 비용 절감 조언: 도구를 쓰지 않음.
  - 실제 계정 수집과 보안 검토: 수집 도구로 SG 개방 6건을 정확히 보고했습니다.
  - CLI 채팅으로 EKS 표준을 불러와 검토했습니다.
- **GPT-6 Luna:** 이 계정에서 Marketplace 약정(`create-foundation-model-agreement`)을 사용자 승인으로 수락했습니다. 약정 상태는 `AVAILABLE`이지만 Converse, Chat Completions, Responses 호출 모두 `not available for this account`로 거부됩니다.
  그래서 실제 응답은 Nova 2 Lite가 했습니다(대체 표시). 계정 단위 사용 가능 여부는 AWS 측에서 해결해야 하고, 풀리면 코드 변경 없이 GPT-6 Luna가 답합니다.
- Nova 2 Lite는 비용 조언에서 설계에 없는 S3 스토리지 항목을 지어낸 적이 있습니다. 시스템 프롬프트에 "설계에 없는 서비스·수치는 말하지 말 것"을 추가했습니다. 구성도와 검토 결과는 코드가 만들기 때문에 이 문제의 영향을 받지 않습니다.

## 다음 후보 (승인 전)

- 설계안 → IaC(CloudFormation/CDK) 출력. DevOps Agent의 mitigation 스펙에 해당합니다.
- 수집한 현행 구성을 설계의 출발점으로 불러와 개선안을 그리기(brownfield).
- 대화 기록 저장과 재개, 응답 스트리밍.
