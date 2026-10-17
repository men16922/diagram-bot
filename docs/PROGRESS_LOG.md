# Progress Log

Last Updated: 2026-10-18

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

## 2026-10-18 — Overview becomes the network diagram (owner: 전체 구성이 의미 없어 보인다; goal: 잔여 작업 완료시까지)

- The overview drew VPC cards only (no relations). It now draws the network joining them: VPC → Transit Gateway (via its
  attachment), peering, VPN gateways, and VPN to customer gateways in a data center box; one lane per route, routes from
  upper rows drop through the gap right of their card. Unconnected snapshots keep the plain card grid.
- A snapshot with one VPC and no TGW/peering/VPN opens in the detail view.
- Fixed: the review step's "hidden in this view" notice survived snapshot/view switches (seen in verify_demo screenshots);
  notices now clear on a new snapshot or view.
- Verified: `make check` 152 tests (overview readable, no shared segments, no route through a card, for all samples and
  the peering/VGW design); `verify_demo.py` 28 checks incl. detail-first single VPC, TGW/CGW overview, no stale notice.

## 2026-10-18 — Samples aligned with AWS best practice + AI/GPU (goal: 구현 및 검증 완료시까지 수행)

- Plan docs/plans/2026-10-18-best-practice-samples.md executed with the recommended decisions (TGW hub, sample version,
  Aurora pgvector, optional items out).
- Samples: Redshift Multi-AZ subnet group (3 subnets, 4 nodes); data-platform TGW hub instead of peering+VGW; second
  CGW/VPN in hybrid and data-platform; `ecr.api`/`logs`/SSM endpoints; practices name what cannot be drawn.
- New `ai-platform` sample/standard: private VPC behind TGW, 11 interface endpoints (Bedrock), EKS system/g6e inference
  (3 AZ)/p5 EFA training (1 AZ), self-referencing EFA SG, Aurora pgvector, EFS.
- Review rules REL-GPU-TRAIN-AZ, REL-GPU-INFER-AZ, SEC-EFA-SG; single-AZ training is a low reminder. Icons for exact
  accelerated families (GPU icons print P3/P4d/G4dn, so p5/g6e keep the instance icon + "GPU"), Bedrock/SageMaker endpoints.
- Layout: VPC attachment row wraps at the AZ width (ai-platform was 3× wider than its AZs). Server regenerates stored
  samples on a new `SAMPLE_VERSION` (verified on reports/session-20261018). Chat tool `add_gpu_node_group`.
- `scripts/render_samples.py` regenerates examples/samples (PNG via the app, README sections; counts match the old README).
- Verified: `make check` lint + 143 tests + CLI round trip; `verify_demo.py` 28 checks, no page errors/external requests;
  `review --fail-on medium` exit 0 for all 8 samples; visual review of ai-platform, data-platform, hybrid, microservices.

## 2026-10-18 — Visual review follow-ups (user request: 잔여 작업 우선순위 수행)

- Detail views opened at 23–31 % at 1440×900 (fit whole diagram); the editor bridge now fits down to 60 % only,
  then fits the width from the top-left. `verify_demo.py` asserts the start zoom.
- Toasts moved to the top of the page (were over the canvas's data tier).
- Registration routes get one colour per target group, so trunks sharing a gutter are told apart.
- Truncated names end on a whole word before the ellipsis.
- STATUS no longer lists finished manual checks as open; LESSONS records the bare-`pytest` AWS-env trap.
- docs/ARCHITECTURE.md (owner request: 사례를 AWS Best Practice로): official sources per sample, verdicts, ranked
  change list. Real gaps: Redshift Multi-AZ subnet group (2 of the required 3 subnets), analytics VPC cannot reach
  on-premises over peering, single customer gateway, missing `ecr.api`/`ssmmessages` endpoints.
- Verified: `make check` (lint + 119 tests + CLI round trip); `verify_demo.py` 27 checks, no page errors or external
  requests; docs/images and examples/demo regenerated from that run.

## 2026-10-17 — Chat design agent (user request: DevOps Agent 벤치마킹, Strands·Bedrock 채팅 설계)

- Research: AWS DevOps Agent features mapped to Diagram Bot (docs/engineering/DEVOPS_AGENT_BENCHMARK.md).
- `designer.py` (intent-level standard design steps on the sample builder; region-configurable `Sample`),
  `agent.py` (Strands tools: design + read-only boto3 collection; ModelRouter GPT-6 Luna → Nova 2 Lite; sequential
  tools; answering model reported), `/api/agent/*`, chat panel, `diagram-bot chat`, `--env-file`.
- Bedrock: Nova 2 Lite / Claude tool use verified; GPT-6 Luna agreement accepted (user approved) but still denied.
- Live workload check on a temporary CloudFormation stack (created/deleted with approval): 16/16 checks; chat tool
  gained a VPC scope after the model missed an ALB when searching by VPC name.
- Cross-account role path verified live with the repo template (temporary, deleted); terminated instances no longer
  drawn or inventoried.
- In-app IAM Identity Center sign-in (device code) verified live end to end; role-selection race fixed.
- docs/DRAWIO.md (libraries, layout algorithm, routing, editor bridge, exports). Verified: `make check` 106 tests,
  live chat in Chromium (design, advice, collection) and CLI chat.

Older entries: docs/archive/PROGRESS_LOG-2026-10.md
