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
- [ ] [manual] Live check on an account with real workloads (EC2/ALB/RDS etc.), via an SSO
  profile and, if used, a cross-account role. Done: resources/relations matched against the
  console, findings reviewed, results in STATUS.md.
- [ ] [manual] Human visual review of docs/images/*.png and examples/demo/edited-topology.png.
  Done: groups/edges judged readable by the presenter, or concrete layout issues listed here.

- [ ] [manual] GPT-6 Luna access: agreement accepted 2026-10-17 but invocations are still denied for this account.
  Done: `diagram-bot chat -m "hi"` answers with "OpenAI GPT-6 Luna" (no fallback label), or AWS confirms ineligibility.

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
