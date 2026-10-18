# Progress Log archive (2026-10)

Moved from docs/PROGRESS_LOG.md on 2026-10-17, 2026-10-18 and 2026-10-19 (doc budget). Newest first.

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


## 2026-10-17 — First live AWS verification (goal: 검증 완료시까지 수행)

- Owner's test account, us-east-1, keys from git-ignored `.env`: CLI collection 30/30 complete,
  counts equal to the AWS CLI; web app collection, views, tabs and exports driven in Chromium.
- Fixed: Lambda functions without VpcConfig were dropped from the graph, so inventory/CSV
  (documented as the full collection) lost them; now nodes without a VPC edge, shown on a separate
  overview card and never in detail views. Regression test added.
- Fixed: `make check` failed with ProfileNotFound when the developer shell exports `AWS_PROFILE`;
  the Makefile now unexports profile and key variables for the offline gate.
- Verified: `make check` (lint + 91 tests + CLI round trip), also with real keys in the environment.

## 2026-10-17 — v0.5.0 team mode and AWS-emulator verification

- Team mode (`auth.py`): PBKDF2 accounts, throttled login, Secure/HttpOnly/Strict session cookie,
  viewer/editor/admin enforced server-side, HSTS, Host allow-list first, JSON Lines audit log,
  `user add|remove|list`; non-loopback serve refused without users + TLS + allowed hosts.
- moto end-to-end test over real boto3 serialization (20+ resource types, AssumeRole via STS).
  It surfaced two real issues, both fixed: an unexpected exception from one API aborted the whole
  collection; the shared-segment check rejected a target group's own bus (frame + registration).
- OIDC SSO (`oidc.py`): discovery, code + PKCE, state cookie, nonce/iss/aud/exp checks, userinfo
  subject match, group→role mapping; unit tests with a PKCE-checking fake provider; Chromium run
  through a loopback provider in `scripts/verify_team.py`.
- Verified: lint + 90 tests; `scripts/verify_team.py` in Chromium over TLS; CI runs both.

## 2026-10-17 — Enterprise readiness (goal: 엔터프라이즈 상용 제품 수준까지 작업·검증)

- Architecture review (`findings.py`): 27 deterministic reliability/security/operations rules from
  API fields only; security group rules collected; review tab, review.md section, `review --fail-on`.
- Change review (`diff.py`): resources/settings/relations between snapshots, volatile state ignored;
  comparison tab, API, `diff --fail-on-change`.
- Collector: AssumeRole with ExternalId capped by the ReadOnlyAccess session policy; CLI over
  several roles × regions; per-service parallel calls with adaptive retries; mtime caches in server.
- Quality: ruff in `make check` and CI, CI runs review/diff CLIs, scale tests (300 instances in one
  VPC readable; 2,500-resource account < 20 s), CloudFormation role template kept equal to the
  policy by test, version 0.4.0, CHANGELOG, SECURITY, OPERATIONS.
- Verified: `make check` (lint + 73 tests + CLI round trip); browser acceptance 27 checks incl.
  review tab and snapshot comparison, no page errors or external requests.

## 2026-10-17 — More services and HA detail (user request: OpenSearch 등, primary/standby·Multi-AZ·replica)

- Collector: OpenSearch (ListDomainNames, DescribeDomains, DescribeDomainNodes), ECS (clusters,
  services, tasks), EFS (file systems, mount targets), Redshift (clusters, subnet groups), MSK
  ListNodes, VPC peering, VPN gateways/connections, customer gateways; IAM prefixes es/
  elasticfilesystem; policy example updated.
- Graph: OpenSearch nodes per AZ (data/master/standby), ECS tasks as target-group targets, IP
  targets placed by subnet CIDR, RDS read replicas and Multi-AZ DB cluster readable standbys,
  ElastiCache shards, Redshift Multi-AZ, EFS mount targets, peering, VPN, `0.0.0.0/0 → VGW`.
- Layout: four-line labels with HA role and size, ECS service frames, VPN routes to the data
  center box, overview cards listing every collected kind, frame captions with capacity.
- Samples: new data-platform (two VPCs); three-tier read replica; microservices ECS services;
  hybrid VPN over TGW. Verified: 51 tests, browser acceptance 26 checks (v0.3 round).

## 2026-10-17 — Common services (user request: EKS·MSK·RDS·ElastiCache·NAT 등)

- Collector: rds DescribeDBClusters, ElastiCache cache clusters/replication groups/subnet groups,
  kafka ListClustersV2, lambda ListFunctions, eks ListClusters/DescribeCluster/ListNodegroups/
  DescribeNodegroup (per-cluster coverage rows); policy example updated.
- Graph/layout: Aurora writer/reader, Redis primary/replica, MSK brokers per client subnet, EKS
  control plane + node-group frames, VPC Lambda; attachment row split from the load balancer row.
- New sample eks-platform (EKS, ALB→pod IPs, Aurora, Redis, MSK, Lambda, endpoints).
- Verified: 48 tests incl. every eks-platform view, browser acceptance incl. EKS sample.

## 2026-10-17 — Recommended next steps (user request: 추천안대로 수행)

- Merged the infrastructure work to main (fast-forward 5938d03..77749d9); CI passed on main.
- Added VPC endpoints (gateway via route tables, interface via subnets) and Transit Gateway
  VPC attachments: collector (3 Describe calls), graph, entry-row placement, TGW in the
  region box, `0.0.0.0/0 → TGW` subnet class; samples gained S3/SSM/ECR endpoints and TGW hubs.
- Verified: 47 tests, browser acceptance, renders reviewed.

## 2026-10-17 — Infrastructure architecture (user request: 인프라 아키텍처 기반)

- Collector: DescribeInternetGateways, DescribeNatGateways, DescribeRouteTables, rds
  DescribeDBInstances, autoscaling DescribeAutoScalingGroups; readonly-policy.json updated.
- Graph: attachment/placement/routing/standby/membership relations with API evidence; subnet
  route class (public/nat/isolated) from explicit or main route table associations.
- Layout: IGW in the entry row, NAT/RDS in their subnets, Auto Scaling frames across AZ columns
  (one row per group inside a subnet), target group → Auto Scaling group edge replaces
  per-instance registrations when the frame encloses only members.
- Samples rebuilt with IGW, NAT per AZ (staging: one), route tables, RDS Multi-AZ, ASGs.
- Fixed during review: ASG node duplicated into the reference box (new unique-ID check),
  relation direction for TargetGroupARNs, misplaced route code, interleaved ASG frames.
- Verified: 47 tests, browser acceptance incl. infrastructure sample, renders reviewed.

## 2026-10-17 — AWS Architecture Icons styling (user request: 더 AWS best practice 형태로)

- Groups: AWS Cloud > Region > VPC > AZ > public/private subnet with official aws4 group styles;
  resource-level icons for EC2/ALB/NLB/GWLB/ENI/EBS; tiers aligned across AZ columns;
  AvailabilityZone=all targets in a Corporate data center group beside the cloud.
- Sources: AWS Architecture Icons deck excerpts (search) and draw.io aws4 library; AWS domains
  are blocked from this environment, so official pages were not read directly.
- Fixed: on-premises routes crossing the reference box (box moved beside the cloud).
- Verified: 47 tests, browser acceptance, all sample views rendered and reviewed.

## 2026-10-17 — AWS best-practice samples (user request)

- samples.py: three-tier (3 AZ, public + internal ALB), microservices (one ALB, four target groups,
  IP targets), nlb-alb (NLB → ALB static entry, TLS NLB), environments (prod/staging/shared VPCs),
  hybrid (on-premises IP targets). Seeded in the app; `diagram-bot demo --sample <key>`.
- Samples exposed two layout defects, fixed: second reference-box row routed through labels
  (box is now one row) and lane wrap in busy rows (lane space now sized per row).
- Layout: subnets ordered by tier (public → web → app → data), internet-facing LBs first,
  AvailabilityZone=all targets drawn with the AWS traditional-server icon.
- Verified: 47 tests incl. every sample view; browser acceptance; renders reviewed.

## 2026-10-17 — Edge overlap cleanup (user request: 선 겹침 정리)

- Router rewrite: per-source buses, per-group gutter slots, per-edge row lanes, right-margin
  routes for right-side outside targets, explicit ALB/NLB placement routes in focus view.
- Measured before: 6 shared segments (sample with references), 24 (100-resource view); after: 0.
- New test asserts orthogonal routes and no shared segment between unrelated routes
  (mutation-checked); `make check` 42 passed; browser acceptance passed; renders reviewed.

## 2026-10-17 — Topology plan P1–P5 implemented (cloud session)

- Installed overnight-harness v1.8.0 (Claude marketplace); `make overnight-where` resolves it.
- P1/P2 commit: relation IDs, views.py, layout.py, serializer-only drawio.py, rich sample,
  ELK experiment (scripts/experiments/elk) and evaluation doc.
- P3/P4 commit: view-aware document API, history/relayout/restore, UI view switcher, focus view,
  relation highlight, bridge relationId support; browser acceptance rewritten.
- P5: examples/demo and docs/images regenerated from the final code; docs updated.
- Verified: `make check` (42 tests + CLI round trip); Chromium acceptance 23 checks with no page
  errors/external requests; geometry checker mutation-tested (overlap, clip, edge crossing).
- Visual review of rendered views done by the implementer; human review remains [manual].
- Not verified: live AWS collection — container keys rejected (`InvalidClientTokenId`); the CLI
  failed cleanly with exit 1. Merged to main by fast-forward at the user's request.

## 2026-10-17 — Commit/push preparation

- User requested committing and pushing the topology plan and prepared repository state.
- Verified `make check`: 24 tests passed; five CLI report artifacts reproduced identically.
- Fetched origin/main and confirmed no divergence from the application baseline before commit.
- Runtime logs and sentinels remain ignored; no live AWS collection or overnight actor run.

## 2026-10-17 — AWS topology refinement plan

- Inspected collector, graph, native XML layout, editor bridge, UI and existing plan/status.
- Added docs/TOPOLOGY_PLAN.md with P1–P5, bounded demo scope and concrete acceptance criteria.
- Updated current-plan pointers and next work; preserved existing harness initialization state.
- Plan only: no application implementation, AWS calls, overnight actor run, commit or push.
- Documentation diff/links checked; application tests are not claimed for this planning pass.

## 2026-10-17 — Harness initialization checkpoint

- Status: initialization complete and checkpointed locally; changes remain uncommitted.
- Changed: Codex v1.8.0 harness state, command boundaries, offline gate and context docs;
  CODEX_HOME-aware discovery and Linux/macOS launch wiring. Plugin owns runner and bibles.
- Verified: `harness-init.sh --check` and `make overnight-where` resolved the installed plugin.
- Verified: `make check` passed 24 tests and reproduced five CLI report artifacts identically.
- Verified: `HARNESS_NOTIFY=0 OVERNIGHT_REPO_WRITE_PROBE=0 ENV_DOCTOR='make check'
  MAX_ITER=0 make overnight-codex-once` ended normally without invoking an actor.
- Verified: `make overnight-ledger-check` passed; installer re-run created 0/skipped 16 files
  and preserved existing state byte-for-byte. Config/rule syntax and doc budgets passed.
- Blockers: no initialization blocker. Full actor/gate/commit smoke and live AWS remain unverified;
  an empty-backlog required-contract experiment was removed in favor of plugin defaults.
- Next: docs/NEXT_PLAN.md contains manual acceptance work; approved [auto] backlog remains 0.

Evidence: scripts/overnight/logs/runner.log and events.jsonl (local, Git-ignored).
Checkpoint edits only reorganized documentation; application tests were not rerun in this pass.
