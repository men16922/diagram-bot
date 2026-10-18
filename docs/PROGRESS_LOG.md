# Progress Log

Last Updated: 2026-10-18

## 2026-10-18 — Company auth structure, porting refactor, LLM wiki (owner: 인증 구조 회사것과 동일하게; 포팅하기 좋게 리팩토링; LLM Wiki)

- Browser egress (`serve --aws-egress browser`): Identity Center device code + GetRoleCredentials on the server; every AWS
  request built and signed by botocore and handed to the browser at before-send (`egress.py`, `/api/relay/*`). Measured with
  real Chromium: 18 collector services + Bedrock answer CORS for signed requests; Identity Center endpoints do not (stay on the
  server). Live with the server's own AWS egress blocked: collection 42/42 and a Bedrock chat turn, 46 requests from the browser.
- Refactor for porting: `store.py`, `aws_access.py`, `routes/*` (mountable routers), `api.py`, frontend `js/` modules; server.py
  760 → ~130 lines, behaviour unchanged (tests, verify_demo 35, verify_team, live egress re-run after the refactor).
- LLM wiki: 24 code-grounded pages + SCHEMA/log/index, `llms.txt`, `scripts/wiki_index.py`; tests check sources, links, index
  freshness and UI labels (found and fixed 4 missing relation labels).
- Also: CI test client on `httpx2` (deprecated fallback is an error). Verified: `make check` 238; CI green on 7698e86.

## 2026-10-18 — Remaining work finished (goal: 잔여 작업 수행, 승인 없이 최대한 완성도 있게)

- CI was red on every push since db76a70 (`strands` missing from its hand-written install list): it now installs `.[test]`,
  Actions on Node 24; clean 3.11/3.13 venvs pass. LESSONS: green `make check` ≠ green CI.
- Chat streaming: `POST /api/agent/chat/stream` (NDJSON text/tool/model/snapshot → done|error); the assistant shows tool
  progress and opens each design step. Live Nova 2 Lite: 8 tools, 5 snapshots over 11 s.
- Brownfield designs: `start_from_current` (open snapshot → last collection) adopts VPC tiers per AZ, NAT tables, LBs and
  TGs; `add_subnet_tier`; standards are extendable in their own VPCs; `chat --from`; never writes routes into uncollected
  tables. Live: three-tier + Redis, existing resources kept (29 tools).
- CloudFormation export (`export-cfn`, API, download dialog): designs, samples and collected snapshots; Ref/GetAtt only,
  parameters for AMIs/certs/roles/images, Secrets Manager passwords, TGW route tables; CloudFront/WAF rules/Direct Connect
  and other inexpressible items in `Metadata.DiagramBot.Unsupported`. cfn-lint clean for every sample; moto stacks for two.
- Collection: Elastic IPs, TGW route tables, CloudFront, WAFv2, Direct Connect (policy + role template); drawn at the edge /
  data-center box / labels; rules SEC-ALB-WAF, SEC-CF-ORIGIN-OPEN, COST-EIP-IDLE, REL-TGW-BLACKHOLE (all low).
- Samples (SAMPLE_VERSION 6): microservices 3 AZ, EKS /28 control-plane subnets + Aurora reader, central egress VPC;
  three-tier CloudFront+WAF, hybrid Direct Connect (VPN backup), data-platform TGW routing domains; gpu-training moved to
  us-east-2 (trn2 is not offered in ap-northeast-2, found by cfn-lint).
- Route filter for dense views (`routes=`, UI `경로 필터`): 100-resource VPC crossings 376 → 0; focus/detail/overview
  documents now tested for explicit routes clear of captions (fixed two caption crossings and a wrong empty-subnet note).
- Project `.claude/settings.json` registers the overnight-harness marketplace and plugin (not yet tried in a cloud session).
- Verified: `make check` 229 tests; verify_demo 35 checks, no page errors/external requests; all samples re-rendered and
  three-tier/hybrid/environments PNGs reviewed. Work ran as three parallel agents in worktrees plus the main session.

## 2026-10-18 — AWS console-style UI, overview at a glance (owner: 전체 구성이 늘 같다; Gemini처럼 우측 채팅; 로그인→프로필; AWS 스타일)

- Overview VPC cards now show services per tier (public / private / isolated) with icons and counts; isolated subnets count
  as their own tier; the unplaced card ignores references and drawn hubs.
- UI: dark top navigation (region, AI assistant, sign-in profile), white side navigation, breadcrumbs, page header,
  Cloudscape-like containers/buttons/tabs. Sign-in (SSO or profile/default chain via GET /api/identity, STS) shows
  `role @ account`; the profile menu holds collection and sign-out. AI assistant docks on the right and reflows content.
- Fixed: the page scrolled 85 px when draw.io focused its iframe on load.
- Verified: `make check` 178 tests (per-sample overview cards); verify_demo 31 checks (profile menu, sign-in dialog, assistant
  reflow, no overflow at 1280/980/640); verify_team ok; live: sign-in showed the IAM user @ account, and a free-form chat design
  (2-AZ VPC, ALB, ECS Fargate, PostgreSQL Multi-AZ) ran 6 tools and opened the design (review 0 findings).
- Live SSO through the new profile menu (owner approved device code): roles listed, CloudOpsReadOnly preselected, profile shows
  `CloudOpsReadOnly @ account (SSO)`, collection dialog shows the identity, us-east-1 collected (47 resources). verify_demo 32 checks
  now walk the same SSO UI against a stand-in Identity Center.
- Live SSO sign-out round trip: profile returns to 로그인 at once, POST .../logout 200, the server session is gone (404);
  verify_demo also asserts the logout request reaches the server.

## 2026-10-18 — Multi-account view, live check of the AI calls (goal: 잔여 작업 완료시까지)

- Multi-account: `combine.py` merges account × region snapshots; `merge_graphs` makes a RAM-shared Transit Gateway or a
  cross-account peering one node (owner's copy). Overview frames each account's VPC cards (AWS account group) with routes
  outside the frames. CLI `diagram-bot combine`, `POST /api/snapshots/combine`, app dialog `계정 통합 보기`; sample
  `multi-account` (Network account TGW + central egress + VPN, prod and staging accounts).
- Live (owner's account, us-east-1, `.env` keys, read-only): 34/34 calls complete, incl. DescribePlacementGroups,
  fsx DescribeFileSystems, sagemaker ListEndpoints/ListClusters (0 resources, so non-empty shapes rest on Stubber tests).
- Verified: `make check` 166 tests; verify_demo 30 checks (multi-account frames, shared TGW, combine dialog).

## 2026-10-18 — AI/GPU as a strength (owner: AI·GPU 쪽이 너무 약하다, 강점이 되어야 한다)

- Collection: FSx, SageMaker real-time endpoints (config instance type, model VpcConfig) and HyperPod clusters, placement
  groups; EFA interfaces, Capacity Block/ODCR and placement group read from DescribeInstances. Policy + CFN role updated.
- Drawing: FSx (type icons) and HyperPod in their subnet, SageMaker endpoints in the VPC row (else regional card); EC2
  labels/inspector show GPU · EFA and capacity · PG on separate lines so a 140 px label never cuts them.
- Review: new AI/ML pillar, 13 more rules with lens BP IDs (internet-open Jupyter/Ray/MLflow/vLLM, public GPU, P5 subnet
  IP headroom, Spot EFA, placement, Lustre AZ/scratch, Bedrock/SageMaker endpoint DNS/AZ, SageMaker VPC/AZ, HyperPod public).
- Samples: `genai-rag`, `gpu-training`; `ai-platform` gains EFA, placement group, FSx. Designer: training groups get a
  dedicated /20, EFA and a placement group; `add_fsx_lustre`, `add_sagemaker_endpoint`. ARCHITECTURE.md §5.5–5.8.
- Verified: `make check` 160 tests; verify_demo 29 checks; all 10 samples review low or clean; label crops reviewed.
