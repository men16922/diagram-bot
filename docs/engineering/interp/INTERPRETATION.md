# Engineering Interpretation — Diagram Bot

Last Updated: 2026-10-17

General engineering bibles live in the plugin's `templates/docs/engineering/`.
`make overnight-where` shows that root. This file maps them to repository mechanisms.

## HARNESS — Verification and permissions

- Config: `.claude/harness-config.json` (historical cross-engine path); engine `codex`.
- Gate `make check`; smoke `make smoke-local`; dependencies provisioned before an unattended run.
- Codex boundary: `.codex/rules/overnight.rules`; template boundaries for other engines remain
  under scripts/overnight. Commands are supplemental guardrails, not a complete security sandbox.
- Gate targets are explicit; live AWS, download, serve, deployment and push are excluded.
- Maturity: local demo with automated regression evidence, not production infrastructure tooling.

## LOOP — Execution and state

- Runner: `$(HARNESS_ROOT)/templates/scripts/overnight/run.sh`, resolved by the Makefile.
- Default engine: Codex. Model/effort variables stay blank to inherit CLI settings.
- Repo-local state: scripts/overnight/{logs,STOP,DONE}; these are Git-ignored.
- Backlog: docs/NEXT_PLAN.md. Only approved [auto] items may become work contracts.
- Preserve plugin contract/prompt defaults; prepare approved [auto] tasks before starting.
- `make overnight-once` runs a bounded iteration; `make overnight-stop` requests graceful stop.
- Repository-local Makefile state supports CODEX_HOME and Linux without caffeinate.

## VERIFICATION — Three layers

- Mechanical: `make check` = 24 existing pytest cases plus CLI demo/render round-trip verification.
  It checks pagination/failures, graph evidence, XML validity, persistence/conflicts, imports,
  endpoint boundaries and consistency of five generated report artifacts. No AWS access or
  editor download. The CLI smoke uses a temporary directory and removes its own outputs.
- Semantic: plugin default critic policy/prompt; keep it in the plugin. Review domain invariants:
  unknown is not empty; API relations are not observed traffic; IP targets are not guessed EC2;
  multi-subnet ALBs are not duplicated; user diagram edits do not rewrite AWS snapshots.
- Creative/manual: Hava-like readability, native layout usability, PNG appearance, real SSO access.
  docs/DEMO.md and scripts/verify_demo.py guide separate provisioned browser checks.
- A zero-iteration startup smoke verifies wiring/termination only, not an actor-generated commit.

## AGENTIC — Execution structure

One repo and one engine lane. No multi-agent orchestration is configured by initialization.
The plugin may use its own read-only reviewer on a future authorized run.

## CONTEXT — Read path and budgets

- docs/AGENT_BRIEF.md → STATUS.md → NEXT_PLAN.md → PROGRESS_LOG.md.
- Brief ≤60 lines; status/plan/log ≤120; lessons ≤40. Archive under docs/archive if needed.
- Product proposal: docs/PLAN.md. Demo instructions: docs/DEMO.md. Neither substitutes for
  explicit [auto] work authorization in the rolling plan.
- docs/LESSONS.md starts empty for facts learned during actual loop work.

## PROMPT — Ownership

- Runtime coding prompt and default critic: installed plugin templates/scripts/overnight/.
- No repo-local PROMPT.md override is needed for initialization.
- Diagram generation is ordinary deterministic code; the product does not call an LLM.
