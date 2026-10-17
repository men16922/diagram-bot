# Progress Log

Last Updated: 2026-10-18

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

Older entries: docs/archive/PROGRESS_LOG-2026-10.md
