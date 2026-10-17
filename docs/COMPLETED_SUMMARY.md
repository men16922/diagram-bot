# Completed Summary

Last Updated: 2026-10-17

## AWS topology refinement P1–P5 (v0.3) — completed 2026-10-17

- P1: stable relation IDs (port/AZ qualifiers, order/health independent), legacy upgrade,
  view contract with counts, edge-case sample (multi-subnet ALB, NLB, multi-port, IP target,
  unqueried reference, long Korean names, empty VPC, partial coverage).
- P2: layout separated from XML; deterministic banded layout with channel/gutter routing;
  ELK layered evaluated and rejected with measurements (docs/engineering/LAYOUT_EVALUATION.md).
- P3: overview/detail/focus views, VPC summary cards, relation highlight and inspector with
  qualifiers, visible/hidden/partial indicators, IAM permission hints.
- P4: per-view documents, revision history, relayout that keeps notes, restore, multi-page
  CLI draw.io; `.drawio`/PNG parity verified after a native move and note.
- P5: browser acceptance (23 checks), regenerated synthetic artifacts, docs.
- Verification: 42 pytest cases, CLI round trip, Chromium acceptance, draw.io reopen render.
  Live AWS SSO collection not verified.

## Diagram Bot v0.2 demo

- AWS read-only collection and deterministic API-evidence graph.
- Hava-inspired AWS workspace with embedded native draw.io and resource details.
- Manual diagram edits, persisted view revisions, editable draw.io and PNG downloads.
- Snapshot import/history and JSON/CSV export.
- 24 pytest cases, browser acceptance flow and GitHub CI passed at `d27657e`.
- Documentation and synthetic examples are linked from README.md and docs/DEMO.md.

## Harness setup — completed 2026-10-17

- 2026-10-17 cloud session: overnight-harness v1.8.0 installed for Claude from the
  men16922/claude-overnight-harness marketplace; plugin resolution and init check passed.


- Codex plugin v1.8.0 installed; local state scaffolded and mapped to this repository.
- `make check` runs existing tests plus synthetic CLI report round-trip checks.
- Context docs distinguish completed demo work from the future product roadmap.
- Verification: initialization check and plugin resolution passed; 24 tests and five-artifact
  report round-trip passed; MAX_ITER=0 startup/shutdown and ledger validation passed.
- Installer re-run: 0 files created, 16 skipped; existing state preserved byte-for-byte.
- Checkpoint docs reflect completed initialization, remaining manual acceptance and no approved
  [auto] backlog. Full actor execution/commit and actual AWS access are outside verified evidence.
