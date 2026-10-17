# Decisions

Last Updated: 2026-10-17

- Product name is Diagram Bot; initial provider scope is AWS only.
- Current refinement scope is docs/TOPOLOGY_PLAN.md: direct SSO/service APIs, no Resource
  Explorer or DevOps Agent dependency. Public DevOps Agent views are a UI reference only.
- Reuse FastAPI/buildless JavaScript/local storage; evaluate ELK without requiring Node at
  runtime. Keep existing CLI behavior and record which layout engine produced each document.
- Demo completeness is the current quality target; docs/PLAN.md contains deferred product work.
- Graph relationships come from AWS IDs/ARNs and explicit API fields, not LLM inference.
- Relation IDs hash source/relation/target plus port/AZ qualifiers only; health and array order
  never change an ID. Legacy edges are upgraded in stored order.
- Layout: deterministic Python `diagram-bot-layered/2`; ELK layered rejected (AZ columns not
  preserved, JS runtime needed for CLI parity). Evidence: docs/engineering/LAYOUT_EVALUATION.md.
- Visual style follows AWS Architecture Icons group/resource conventions via draw.io aws4 shapes;
  lines stay arrowless (configuration, not traffic). Details: docs/engineering/LAYOUT_EVALUATION.md.
- Collection includes internet gateways, NAT gateways, route tables, RDS instances and Auto Scaling
  groups (read-only). Subnet type comes from the route table's 0.0.0.0/0 route; RDS placement only
  when exactly one subnet-group subnet matches the AZ; a target group attached to a framed Auto
  Scaling group is drawn as one edge instead of per-instance registrations.
- Views are overview/detail/focus; each saved separately. Detail views keep the v0.2 storage key.
- Automatic layout only on first generation or explicit relayout; superseded edits are archived
  and user-added notes carried over.
- draw.io is the native editor; PNG rendering uses the same current edited document.
- Original snapshots and presentation edits are separate stores.
- Overnight Harness behavior stays in the installed plugin; the repo owns config, permissions,
  docs, gate commands and Makefile wiring. Do not pin a machine-specific plugin cache version.
- Codex is the default overnight engine; do not override the user's model/effort routing.
- Offline pytest + CLI smoke is the commit gate. Browser visual quality and live AWS checks
  remain separate evidence; an offline green result does not establish them.
- Preserve plugin contract/prompt defaults. Initialization does not create new product backlog
  or run an unattended implementation/commit cycle.
