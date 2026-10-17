> ▶ NEXT SESSION: Read docs/STATUS.md, docs/NEXT_PLAN.md and docs/TOPOLOGY_PLAN.md. The new plan starts at P1; no overnight run has been requested.

# Diagram Bot — Agent Brief

Last Updated: 2026-10-17

## Baseline

Diagram Bot v0.2 is an AWS-only local demo application. FastAPI and buildless JavaScript embed
pinned draw.io 32.4.1; boto3 collects read-only AWS configuration using existing SDK/SSO credentials.
The demo supports VPC/AZ/subnet grouping, properties, native editing, saved views, draw.io/PNG
exports and JSON/CSV inventory. Product scope and future proposals are in docs/PLAN.md.

## Read Order

1. docs/STATUS.md — implemented behavior and evidence.
2. docs/NEXT_PLAN.md — authorized open work only.
3. docs/PROGRESS_LOG.md — recent changes.
4. docs/engineering/interp/INTERPRETATION.md — harness mapping and domain invariants.

## Commands

- `make check`: offline pytest + CLI report round-trip smoke.
- `make overnight-where`: resolve the installed plugin; behavior remains in the plugin.
- `make overnight-codex-once`: one iteration, only after approved [auto] work exists.
- `make overnight-stop`: stop after the current iteration.
- `diagram-bot serve`: manual demo; see docs/DEMO.md.

## Boundaries

- Current objective is demo quality; do not silently expand into a production platform.
- Preserve API evidence, partial/unavailable states and original snapshots.
- Do not infer live communication from configuration, SG rules or matching IPs.
- User edits affect diagram presentation, never the captured AWS data.
- Overnight work is offline. Live AWS, downloads, deployment and push are outside the loop.
- Work requiring visual judgment or external credentials stays [manual].
- No new [auto] tasks have been approved by the user; initialization alone does not authorize
  an application-development backlog. Seed approved tasks before starting the loop.
