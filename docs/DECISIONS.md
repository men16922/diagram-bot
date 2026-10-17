# Decisions

Last Updated: 2026-10-17

- Product name is Diagram Bot; initial provider scope is AWS only.
- Current refinement scope is docs/TOPOLOGY_PLAN.md: direct SSO/service APIs, no Resource
  Explorer or DevOps Agent dependency. Public DevOps Agent views are a UI reference only.
- Reuse FastAPI/buildless JavaScript/local storage; evaluate ELK without requiring Node at
  runtime. Keep existing CLI behavior and record which layout engine produced each document.
- Demo completeness is the current quality target; docs/PLAN.md contains deferred product work.
- Graph relationships come from AWS IDs/ARNs and explicit API fields, not LLM inference.
- draw.io is the native editor; PNG rendering uses the same current edited document.
- Original snapshots and presentation edits are separate stores.
- Overnight Harness behavior stays in the installed plugin; the repo owns config, permissions,
  docs, gate commands and Makefile wiring. Do not pin a machine-specific plugin cache version.
- Codex is the default overnight engine; do not override the user's model/effort routing.
- Offline pytest + CLI smoke is the commit gate. Browser visual quality and live AWS checks
  remain separate evidence; an offline green result does not establish them.
- Preserve plugin contract/prompt defaults. Initialization does not create new product backlog
  or run an unattended implementation/commit cycle.
