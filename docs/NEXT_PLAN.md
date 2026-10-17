# Next Plan

Last Updated: 2026-10-17

## Current Scope

Current work is the AWS topology refinement plan in [TOPOLOGY_PLAN.md](TOPOLOGY_PLAN.md).
The user requested a concrete plan for direct SSO/API collection, graph layout and draw.io/PNG.
Planning is complete; P1–P5 implementation has not started. Start implementation with P1.
Completed demo/harness work is recorded in docs/COMPLETED_SUMMARY.md.
The broader roadmap in docs/PLAN.md is not an unattended-work authorization.

## Overnight Seed

Actionable approved [auto] items: **0**. The implementation sequence is documented, but no
unattended run or executable overnight backlog has been started by this planning task.
Seed supply determines runtime: an empty queue stops normally instead of filling a time window.
A run/review window has not been requested, so no duration estimate is assigned.

## Implementation Sequence

- [ ] P1: Stable relation IDs, view graph contract and edge-case fixtures.
- [ ] P2: Separate layout from XML generation; evaluate ELK and improve automatic layout.
- [ ] P3: Overview, detail and focused-resource views with relationship inspection.
- [ ] P4: Preserve view-specific edits and verify matching draw.io/PNG exports.
- [ ] P5: Offline/browser acceptance, sample artifacts and demo documentation.

Each step's scope and done criteria are in TOPOLOGY_PLAN.md. These entries are not overnight seeds.

## Manual Follow-up

- [ ] [manual] Validate AWS collection with the intended SSO profile/account when credentials
  are available. Done: record account/region, collection coverage and any access failures.
- [ ] [manual] Review P2/P4 visual evidence. Done: readable groups/edges, no clipped shapes,
  and matching edited draw.io/PNG outputs for the documented fixtures.

## Rules

- Only explicitly approved, deterministic offline work may be tagged [auto].
- Untagged roadmap entries and [manual]/[blocked] work are not consumed by the loop.
- Each future [auto] item needs one concrete done criterion and `make check` verification.
- Completed work belongs in docs/COMPLETED_SUMMARY.md, not this rolling plan.
