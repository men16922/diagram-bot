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
- [ ] Apply the change list (docs/ARCHITECTURE.md §4) — awaiting the owner's choice of items. Top: Redshift Multi-AZ
  subnet group (MUST), data-platform peering+VGW → TGW hub, second CGW/VPN in hybrid, missing `ecr.api`/SSM endpoints.
- [ ] AI/GPU (docs/ARCHITECTURE.md §5, Generative AI / ML / HPC lenses): A1 new `ai-platform` sample, A2 accelerated
  instance and Bedrock icons, A3 GPU review rules (training spans AZs, single-AZ inference, EFA SG) — awaiting approval.

## Candidate Follow-ups (not approved)

- Design → IaC (CloudFormation/CDK) export; brownfield design starting from a collected snapshot; chat streaming.

- Readability for dense views (≈100 resources): edge bundling or per-target-group filtering (route colours done).
- Placement edges in focus view use draw.io auto-routing; route them through gutters.
- A SessionStart hook to reinstall overnight-harness in cloud sessions automatically.

## Rules

- Only explicitly approved, deterministic offline work may be tagged [auto].
- Untagged roadmap entries and [manual]/[blocked] work are not consumed by the loop.
- Each future [auto] item needs one concrete done criterion and `make check` verification.
- Completed work belongs in docs/COMPLETED_SUMMARY.md, not this rolling plan.
