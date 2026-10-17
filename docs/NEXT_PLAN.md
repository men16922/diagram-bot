# Next Plan

Last Updated: 2026-10-17

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
- [ ] [manual] SSO collection from the app (`SSO로 로그인`, device code; implemented and tested offline 2026-10-17).
  Done: owner approves the device code, CloudOpsReadOnly collection completes and matches the key-based one.
  Paused by the owner ("SSO 말고 다른것부터").
- [x] [manual] Visual review of docs/images/*.png and examples/demo/edited-topology.png — done 2026-10-17 by the agent
  (model review of all 8 screenshots + the edited PNG; presenter judgment still welcome). Grouping (Cloud > Region >
  VPC > AZ > tiered subnets), icons, HA labels and the overview cards read correctly. Concrete issues found:
  1. Three-AZ detail views (eks-platform, data-platform, workspace) open at the page-fit zoom of 30–41 %: labels are
     illegible and the diagram uses under half of the canvas width (fit by height). Fit to width / readable start zoom.
  2. edited-topology: four to five target-group routes share the left gutter of AZ a, 8 px apart over the whole
     height, and the demo-api → i-demo-missing route runs down the VPC margin into the reference box; at 1× it is
     hard to tell which route ends where (edge bundling or per-target-group colouring).
  3. Toasts ("직접 관계 2개를 구성도에서 강조했습니다", hidden-resource notice) cover the lower canvas, i.e. the data
     tier, for several seconds.
  4. Long names are truncated with "…" in the diagram (e.g. the batch-server label); full text is only in the tooltip
     and inspector — acceptable, but the cut makes the m6i.large line look like part of the name.

- [x] Default chat model: Amazon Nova 2 Lite (owner's decision 2026-10-17). GPT-6 Luna stays selectable; it answers
  once AWS enables OpenAI models for the account (support-case draft prepared, owner sends it if wanted).

## Candidate Follow-ups (not approved)

- Design → IaC (CloudFormation/CDK) export; brownfield design starting from a collected snapshot; chat streaming.

- Readability for dense views (≈100 resources): edge bundling or per-target-group filtering.
- Placement edges in focus view use draw.io auto-routing; route them through gutters.
- A SessionStart hook to reinstall overnight-harness in cloud sessions automatically.

## Rules

- Only explicitly approved, deterministic offline work may be tagged [auto].
- Untagged roadmap entries and [manual]/[blocked] work are not consumed by the loop.
- Each future [auto] item needs one concrete done criterion and `make check` verification.
- Completed work belongs in docs/COMPLETED_SUMMARY.md, not this rolling plan.
