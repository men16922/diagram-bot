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

- [ ] [manual] Validate AWS collection with the intended SSO profile/account (one account, one
  region). Done: account/region recorded, main resources and relations matched against the
  console/API, coverage and any access failures recorded in STATUS.md.
- [ ] [manual] Human visual review of docs/images/*.png and examples/demo/edited-topology.png.
  Done: groups/edges judged readable by the presenter, or concrete layout issues listed here.
- [ ] [manual] Review and merge branch `claude/harness-plugin-install-yirqt3` into main.

## Candidate Follow-ups (not approved)

- Readability for dense views (≈100 resources): edge bundling or per-target-group filtering.
- Placement edges in focus view use draw.io auto-routing; route them through gutters.
- A SessionStart hook to reinstall overnight-harness in cloud sessions automatically.

## Rules

- Only explicitly approved, deterministic offline work may be tagged [auto].
- Untagged roadmap entries and [manual]/[blocked] work are not consumed by the loop.
- Each future [auto] item needs one concrete done criterion and `make check` verification.
- Completed work belongs in docs/COMPLETED_SUMMARY.md, not this rolling plan.
