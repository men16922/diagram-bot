# Completed Summary

Last Updated: 2026-10-17

## Chat design agent and live verification — completed 2026-10-17

- Strands + Bedrock chat agent (24 tools, Nova 2 Lite default), designer, in-app SSO device-code sign-in, `--env-file`.
- Live: real account, temporary workload stack (16/16), cross-account role, SSO collection, browser chat; fixes for
  VPC-less Lambda, terminated instances, shell AWS_PROFILE in the gate, VPC-scoped search, SSO role race.
- Details: docs/archive/PROGRESS_LOG-2026-10.md, docs/STATUS.md, docs/engineering/DEVOPS_AGENT_BENCHMARK.md.

## v0.4–v0.5 (review, diff, multi-account, team mode, OIDC) — completed 2026-10-17

- See CHANGELOG.md and docs/archive/PROGRESS_LOG-2026-10.md.

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

## AWS best-practice samples and AI/GPU — completed 2026-10-18

- docs/ARCHITECTURE.md: official AWS sources, per-sample verdicts, AI/GPU (Generative AI, ML, HPC lenses).
- 8 samples (new ai-platform), GPU review rules, accelerated-instance/Bedrock icons, chat `add_gpu_node_group`,
  sample reseeding by version, wrapped attachment row, scripts/render_samples.py.
- Evidence: `make check` 143 tests; verify_demo 28 checks; all samples pass `review --fail-on medium`.

## Network overview and AI/GPU strength — completed 2026-10-18

- Overview draws the TGW/peering/VPN network; single unconnected VPC opens in detail; stale notices cleared.
- AI/GPU: FSx/SageMaker/HyperPod/placement collection, AI/ML review pillar (17 rules), samples genai-rag and gpu-training,
  ai-platform with EFA/PG/FSx, designer tools. Evidence: `make check` 160 tests, verify_demo 29 checks.
