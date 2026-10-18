# Decisions

Last Updated: 2026-10-19

- Product name is Diagram Bot; initial provider scope is AWS only.
- Collection uses direct SSO/service APIs, no Resource Explorer or DevOps Agent dependency (topology plan, finished and
  archived: docs/archive/TOPOLOGY_PLAN.md). Public DevOps Agent views are a UI reference only.
- Reuse FastAPI/buildless JavaScript/local storage; evaluate ELK without requiring Node at
  runtime. Keep existing CLI behavior and record which layout engine produced each document.
- Demo completeness is the current quality target; docs/PLAN.md lists the open product proposals.
- Graph relationships come from AWS IDs/ARNs and explicit API fields, not LLM inference.
- Chat design agent (2026-10-17, user request): Strands Agents on Bedrock Converse; the model only picks tools,
  `designer.py` decides CIDRs, AZ spread, routes, SGs and HA placement. Default Nova 2 Lite (owner's decision 2026-10-17
  while GPT-6 Luna is not enabled for the account); GPT-6 Luna / Claude Sonnet 4.5 selectable with ModelRouter
  fallback to Nova 2 Lite; the answering model is always shown. Tools run
  sequentially (steps depend on each other). No AWS write tools. Designs reuse the snapshot `raw` format, flagged
  `metadata.design`. GPT-6 Luna Marketplace agreement accepted on the owner's account with the user's approval.
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
- VPC endpoints and Transit Gateway attachments sit in the VPC entry row with the internet
  gateway; Transit Gateways are regional and drawn outside VPCs, linked from their attachment.
- Managed services: EKS control plane and VPC Lambda sit in the attachment row (multi-subnet
  ENIs); EKS managed node groups are drawn as their Auto Scaling group frames; MSK draws one broker
  icon per client subnet (brokers spread evenly by AWS); ElastiCache and RDS/Aurora nodes are placed
  only when exactly one subnet-group subnet matches their AZ. Cluster/replication-group objects
  are not drawn; they appear through their members in the inspector.
- Per-AZ detail comes from per-node APIs when granted (OpenSearch DescribeDomainNodes, MSK
  ListNodes, ECS DescribeTasks); without them the even spread is shown and marked estimated.
- IP targets are placed in a subnet only when exactly one subnet CIDR in the target group's VPC
  holds the address and the AZ matches (AZ=all stays outside). An IP target is drawn as an ECS task
  only when that task's service names the target group and the task ENI has the same address.
- ECS services with placed tasks are frames like Auto Scaling groups; services without collected
  tasks sit in the attachment row. VPC peering appears in both collected VPCs; VPN connections run
  from the VGW/TGW to customer gateways in the data center box beside the AWS Cloud.
- Architecture review reports only what API fields decide; a missing input yields no finding plus
  an OPS-COVERAGE row, never a guess. Reference samples must stay free of high/medium findings.
- Change review compares configuration, not observations (health, running counts, telemetry).
- Cross-account sessions always carry the ReadOnlyAccess session policy.
- Network exposure only in team mode (users file + TLS + allowed hosts); there is no option to serve
  unauthenticated on a non-loopback address. SSO is built-in OpenID Connect (code + PKCE, claims
  checked, ID token from the token endpoint over TLS); SAML goes through an authenticating proxy.
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
- Browser egress (2026-10-18, owner: 회사 구조와 동일하게): the company server cannot reach AWS (firewall) and Permission
  Sets deny calls from outside the company's addresses, so `serve --aws-egress browser` keeps Identity Center (device code,
  GetRoleCredentials; these endpoints answer no CORS) on the server, lets botocore build and sign every request, and hands
  the prepared request to the user's browser at `before-send` (`egress.py`). Chosen over a generic "sign this request"
  endpoint: the server signs only its own clients' requests, and the collector, chat tools and Bedrock run unchanged.
  Bedrock uses Converse without response streaming in this mode. Server egress stays the default.
- Company draw.io XML (2026-10-19, review of 6622915): request data never names server files. HTTP `spec` is a preset or
  an inline object (`request_spec`), adopt takes XML text; paths are for the CLI, library and `DIAGRAM_BOT_SPEC` only.
  `DrawioSpec` is a validated model. `/api/xml/adopt` answers per request for editors; only an admin's `make_default`
  changes the process default spec and registers a Design standard. Since 2026-10-19 (user request: the features on screen)
  those standards persist under `<data-dir>/standards/` and come back at startup; `spec` may also name one (a lookup, never a
  path), and only an admin deletes one (`DELETE /api/standards/{name}`). Documents the
  app edits keep the `object` wrapper and `resourceId`/`relationId` whatever the default exports (`for_editor`).
- Alignment never returns a page with more overlaps, out-of-bounds cells or edge-icon collisions than its input (the
  page is kept and reported `reverted`); a page without issues is untouched, so Diagram Bot's own output is a no-op.
  The aligner uses the `layout/` package's grid and tier rank; draw.io XML output stays uncompressed (compressed input is decoded).
- Import is label inference, not API evidence: snapshots carry `metadata.imported` and `metadata.import` (cell → resource
  map, unmapped cells, synthesized items). One classification table (`classify_cell`) serves spec extraction, inspect,
  align and import: shapes first, whole-word labels second; unknown shapes are annotations, never guessed EC2s, and no
  NAT gateway or out-of-VPC CIDR is invented.
