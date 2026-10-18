> ▶ NEXT SESSION: company porting & draw.io spec/alignment done (2026-10-18): `drawio_spec.py` (custom company XML wrapper/styles + `spec_from_xml`), `xml_align.py` (`align_drawio_xml`, `import_drawio_xml`, `inspect_drawio_xml`), `--aws-egress browser`, modular `api.py`/routers, `docs/HUMAN_GUIDE.md` (human overview) + `docs/wiki/` (`llms.txt` agent wiki). No [auto] backlog.

# Diagram Bot — Agent Brief

Last Updated: 2026-10-18

## Baseline

Diagram Bot v0.5 (+ unreleased chat agent & company XML alignment/spec modules) is an AWS-only local application. FastAPI and buildless JavaScript embed
pinned draw.io 32.4.1; boto3 collects read-only AWS configuration using existing SDK/SSO credentials.
The demo supports overview/detail/focus views, a deterministic layered layout, custom company draw.io specs (`DrawioSpec`), in-place alignment/routing and snapshot import of existing `drawio.xml` files (`xml_align.py`), properties and
relation evidence, native editing with revision history, draw.io/PNG exports and JSON/CSV inventory, architecture
review/diff, team mode, a chat design agent (Strands + Bedrock; tools draw standard or brownfield designs, the model
never invents resources) and CloudFormation draft export. draw.io internals: docs/DRAWIO.md. Sample best-practice basis: docs/ARCHITECTURE.md. Product scope and future proposals are in docs/PLAN.md.

## Read Order

0. docs/wiki/index.md (for coding agents) and docs/HUMAN_GUIDE.md (for humans) — code-grounded wiki (architecture, data model, API, auth, porting, draw.io spec/alignment); keep it current (docs/wiki/SCHEMA.md).
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
- `python scripts/verify_demo.py [--chromium PATH]`: browser acceptance against a fresh `serve`.

## Boundaries

- Current objective is demo quality; do not silently expand into a production platform.
- Preserve API evidence, partial/unavailable states and original snapshots.
- Do not infer live communication from configuration, SG rules or matching IPs.
- User edits affect diagram presentation, never the captured AWS data.
- Overnight work is offline. Live AWS, downloads, deployment and push are outside the loop.
- Work requiring visual judgment or external credentials stays [manual].
- No new [auto] tasks have been approved by the user; initialization alone does not authorize
  an application-development backlog. Seed approved tasks before starting the loop.
