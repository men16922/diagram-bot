# Status

Last Updated: 2026-10-17

## Active Plan

- AWS topology refinement plan: docs/TOPOLOGY_PLAN.md; P1–P5 are not yet implemented.
- Reuse direct SSO + EC2/ELBv2 collection. No Resource Explorer or DevOps Agent dependency.
- Next implementation starts with graph/view contracts, then layout, views and export acceptance.
- This planning pass changed documentation only; no application tests or live AWS calls were run.

## Current Baseline

- Application: v0.2; last pushed baseline `d27657e` on main.
- AWS VPC/Subnet/EC2/ELBv2/TargetGroup collection and explicit API relations implemented.
- Native draw.io editing, persisted views, resource inspection, PNG and data exports implemented.
- Existing verification: 24 pytest cases; Chromium editing/export flow; CI Python 3.11/3.12/3.13
  and browser jobs passed at the application baseline.
- Browser evidence: examples/demo/verification.json, topology.drawio, topology.png;
  docs/images/workspace.png. These describe synthetic data, not a live AWS account.

## Harness Initialization — Complete

- Overnight Harness v1.8.0 installed in Codex; per-repo state scaffolded.
- Engine: codex; model and reasoning effort inherit the installed CLI configuration.
- Gate: `make check`; smoke: `make smoke-local`.
- Runner/helpers/prompts/bibles remain plugin-owned; no copied executable runner.
- Makefile resolver honors CODEX_HOME and follows installed versions without an absolute pin.
- Linux/macOS background launch supported by the repo-local Makefile state.
- No approved [auto] tasks yet; plugin defaults are preserved for contract/prompt routing.
- User requested commit/push of the configuration and topology plan; no application changes made.
- `harness-init --check`, plugin resolution, `make check`, MAX_ITER=0 startup/shutdown,
  event ledger validation and config/doc checks passed. Full actor smoke is not claimed.
- Installer re-run preserved existing files (0 created, 16 skipped).
- Checkpoint recorded on 2026-10-17; current next work is P1 in docs/NEXT_PLAN.md.
- Pre-commit verification: `make check` passed 24 tests and the five-artifact CLI round trip.

## Open Limits

- Actual AWS credentials, permissions, networking and SSO refresh remain unverified.
- No shared-user hosting, scheduled collection, snapshot diff, RDS/EKS or production-scale claim.
- Browser verification requires a provisioned editor/Chromium and is separate from the offline gate.
- Full actor execution and commit verification require an approved [auto] task and Codex access;
  zero-iteration startup/shutdown verification does not prove model invocation or commit capability.
