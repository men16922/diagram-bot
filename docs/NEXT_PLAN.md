# Next Plan

Last Updated: 2026-10-18

## Current Scope

The AWS topology refinement plan (docs/TOPOLOGY_PLAN.md) P1–P5 is implemented and verified on
synthetic data; details in docs/STATUS.md and docs/COMPLETED_SUMMARY.md. What remains needs
credentials or human judgment. The broader roadmap in docs/PLAN.md is not an unattended-work
authorization.

## Overnight Seed

Actionable approved [auto] items: **0**. No unattended run has been requested.
Seed supply determines runtime: an empty queue stops normally instead of filling a time window.

## Manual Follow-up

- [x] [manual] Validate AWS collection on one account/region — done 2026-10-17 with IAM user keys
  (STATUS.md "Live AWS Verification").
- [x] [manual] Live check with real workloads — done 2026-10-17 on a temporary CloudFormation stack
  (VPC, ALB, ASG × 2 t3.micro, RDS), created and deleted with the user's approval (STATUS.md).
- [x] [manual] Cross-account role collection — done 2026-10-17 with the repo's role template (temporary stack).
- [x] [manual] SSO collection from the app — done 2026-10-17: device code approved (owner signed in, agent confirmed the
  code and allowed access at the owner's request), account and CloudOpsReadOnly auto-selected, collection complete with
  the same 47 resources / node IDs as the key-based collection; audited as `via: sso:CloudOpsReadOnly`.
- [x] [manual] Visual review of docs/images/*.png and examples/demo/edited-topology.png — done 2026-10-17 by the agent
  (model review of all 8 screenshots + the edited PNG). All four issues it found were fixed on 2026-10-18 (PROGRESS_LOG):
  readable start zoom (was 23–31 % at 1440×900), toasts moved off the canvas, one colour per target group route,
  word-boundary cuts for long names.

- [x] Default chat model: Amazon Nova 2 Lite (owner's decision 2026-10-17). GPT-6 Luna stays selectable; it answers
  once AWS enables OpenAI models for the account (support-case draft prepared, owner sends it if wanted).

## Sample Best-Practice Alignment (owner request 2026-10-18)

- [x] docs/ARCHITECTURE.md: AWS sources, per-sample verdicts and a ranked change list.
- [x] Apply the change list (docs/ARCHITECTURE.md §4) — done 2026-10-18 (items 1–6; optional item 7 left out). Top: Redshift Multi-AZ
  subnet group (MUST), data-platform peering+VGW → TGW hub, second CGW/VPN in hybrid, missing `ecr.api`/SSM endpoints.
- [x] AI/GPU (docs/ARCHITECTURE.md §5): A1 `ai-platform`, A2 icons, A3 GPU review rules — done 2026-10-18.

## Remaining Work Finished (owner goal 2026-10-18: 잔여 작업 수행, 승인 없이)

- [x] CI fixed (`.[test]`); chat streaming; brownfield designs; CloudFormation export; collection of Elastic IPs,
  TGW route tables, CloudFront/WAF, Direct Connect; optional sample items (ARCHITECTURE.md §4 #7); route filter for dense
  views; focus routes tested (they already used gutters); overnight-harness via project settings. Details: PROGRESS_LOG.

## Candidate Follow-ups (not approved)

- Live check of the AI/ML calls with real resources: owner declined on 2026-10-18. The new CloudFront/WAF/Direct Connect
  calls are also stub-verified only; a live read-only collection on an account that has them would confirm the shapes.
- Deploy one CloudFormation draft (e.g. three-tier) to a scratch account to prove it end to end (costs; owner decision).
- Starlette `TestClient` deprecation: move tests to `httpx2` when Starlette drops `httpx` support.
- Edge bundling for dense overviews (route filter covers detail views); CloudFront/WAF rule bodies (`GetWebACL`) if wanted.
- Confirm the plugin auto-install on the next cloud session.

## Rules

- Only explicitly approved, deterministic offline work may be tagged [auto].
- Untagged roadmap entries and [manual]/[blocked] work are not consumed by the loop.
- Each future [auto] item needs one concrete done criterion and `make check` verification.
- Completed work belongs in docs/COMPLETED_SUMMARY.md, not this rolling plan.
