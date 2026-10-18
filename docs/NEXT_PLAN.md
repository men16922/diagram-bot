# Next Plan

Last Updated: 2026-10-19

## Current Scope

No plan is in progress. The finished plans are archived (docs/archive/TOPOLOGY_PLAN.md,
docs/archive/2026-10-18-best-practice-samples.md, docs/archive/PLAN-2026-10-17.md); what they delivered is in
docs/STATUS.md and docs/COMPLETED_SUMMARY.md. What remains below needs credentials or human judgment.

## Overnight Seed

Actionable approved [auto] items: **0**. No unattended run has been requested.
Seed supply determines runtime: an empty queue stops normally instead of filling a time window.

## Candidate Follow-ups (not approved)

- Live check of the AI/ML calls with real resources: owner declined on 2026-10-18. The new CloudFront/WAF/Direct Connect
  calls are also stub-verified only; a live read-only collection on an account that has them would confirm the shapes.
- Deploy one CloudFormation draft (e.g. three-tier) to a scratch account to prove it end to end (costs; owner decision).
- Edge bundling for dense overviews (route filter covers detail views); CloudFront/WAF rule bodies (`GetWebACL`) if wanted.
- Confirm the plugin auto-install on the next cloud session.
- GPT-6 Luna: AWS must enable OpenAI models for the account; a support-case draft is prepared, the owner sends it if wanted.

## Rules

- Only explicitly approved, deterministic offline work may be tagged [auto].
- Untagged roadmap entries and [manual]/[blocked] work are not consumed by the loop.
- Each future [auto] item needs one concrete done criterion and `make check` verification.
- Completed work belongs in docs/COMPLETED_SUMMARY.md, not this rolling plan.
