# Status

Last Updated: 2026-10-19

## Active Plan

- 2026-10-19 review + hardening (PROGRESS_LOG top entry): the company XML feature fixed and raised (every page, cell map, one
  classifier, quality guard, no server file reads, adopt scoped), codebase audit fixes, `layout/`·`iac/`·`samples/` packages and `js/` modules
  behind golden hashes; 476 tests, verify_demo 35/35, verify_team 11/11.
- 2026-10-18 company draw.io spec & XML alignment (`drawio_spec.py`, `xml_align.py`, `tests/test_xml_align.py`, `docs/wiki/drawio-format-and-alignment.md`, `docs/HUMAN_GUIDE.md`): custom company draw.io specs (`DrawioSpec`, `spec_from_xml`, `"clean"`/`"mxcell"` presets), rule-based in-place alignment & orthogonal routing of existing `drawio.xml` (`align_drawio_xml`), and `drawio.xml` → `Snapshot` import (`import_drawio_xml`), exposed via `api.py`, `/api/xml/align`, `/api/xml/import`, and CLI `align-xml`/`import-xml`.
- 2026-10-18 company auth structure: `--aws-egress browser` (server signs, browser sends from the PC; docs/wiki/browser-egress.md),
  server split into store / aws_access / routes + `api.py` for porting (docs/wiki/porting-guide.md), LLM wiki docs/wiki + llms.txt.
- 2026-10-18 remaining work finished (PROGRESS_LOG top entry): CI green again (`.[test]`), chat streaming, brownfield designs
  (`start_from_current`, `add_subnet_tier`; 29 chat tools), CloudFormation export (`export-cfn`), collection of Elastic IPs,
  TGW route tables, CloudFront, WAFv2 and Direct Connect (4 new low rules), route filter for dense views, optional sample
  items; SAMPLE_VERSION 6, gpu-training in us-east-2.
- 2026-10-18 earlier: 11 samples aligned with AWS best practice (docs/ARCHITECTURE.md) incl. three AI/GPU; AI/ML collection
  and review pillar; multi-account combine; AWS console-style UI with sign-in profile and docked assistant. Live (owner's
  account, us-east-1): 34/34 calls complete; SSO via the profile menu collected 47 resources.
- 2026-10-17: chat design agent (Strands + Bedrock), team mode (v0.5.0), review/diff gates (v0.4.0); benchmark in
  docs/engineering/DEVOPS_AGENT_BENCHMARK.md, draw.io internals in docs/DRAWIO.md.
- All manual verification is done. No [auto] backlog is approved.

## Current Baseline (v0.5.0)

- Collection: direct SSO/SDK, read-only EC2/ELBv2/RDS/Auto Scaling/EKS/ECS/MSK/ElastiCache/OpenSearch/EFS/Redshift/
  Lambda/FSx/SageMaker calls plus VPC peering, Site-to-Site VPN, Direct Connect, CloudFront, WAFv2, Elastic IPs, incl. internet and
  NAT gateways, route tables, VPC endpoints and Transit Gateway attachments and route tables; coverage rows name the
  IAM action needed when denied or partial.
- Diagrams: AWS Architecture Icons style (Cloud > Region > VPC > AZ > route-classified subnets),
  IGW/NAT, Auto Scaling and ECS service frames, VPN to customer gateways; labels carry each node's HA role
  (primary/standby/read replica, writer/reader, shard primary/replica, OpenSearch data/master/standby per AZ,
  MSK broker IDs, Redshift primary/secondary). Samples in examples/samples (11 patterns incl. three AI/GPU and multi-account; basis docs/ARCHITECTURE.md).
- Graph: relation IDs from source/relation/target + port/AZ qualifiers; independent of API
  array order and target health. Same instance on two ports = two relations. Legacy snapshots
  get IDs on read in their stored order, so saved `relationIndex` documents still resolve.
- Views (`views.py`): overview (VPC cards + TGW/peering/VPN network to customer gateways; single unconnected VPC opens in detail), detail (one VPC), focus
  (one resource, direct relations, required boundaries). Each reports visible / collected /
  reference / hidden counts. SG/ENI/EBS references are an option; registered targets and
  unqueried references always appear in the detail view.
- Layout (`layout.py`, `diagram-bot-layered/2`): deterministic bands and AZ columns with explicit
  channel/gutter routes; `drawio.py` only serializes. Engine recorded in each document.
  ELK layered evaluated and not adopted: docs/engineering/LAYOUT_EVALUATION.md.
- Documents: one per snapshot/view/VPC/resource/reference option; detail views keep the old
  storage key. Saves and explicit relayouts archive the superseded revision (last 20);
  relayout keeps user notes; archived revisions can be restored.
- CLI `topology.drawio` is multi-page: overview + one detail page per VPC.
- Sample: 3 VPCs (one empty), 2 AZs, multi-subnet ALB, NLB, 3 target groups, one instance on
  two ports, IP target sharing an instance IP, unqueried instance, long Korean names,
  partial DescribeInstances.

## Live Workload Verification (2026-10-17, temporary stack, us-east-1)

- With the user's approval a CloudFormation stack (VPC 2 AZ public/app/data, IGW, ALB + target group, ASG of
  2 × t3.micro, RDS PostgreSQL db.t4g.micro Single-AZ) was created, collected and deleted.
- 16/16 checks vs CloudFormation physical IDs: route classes (public/isolated), ALB in both public subnets, TG
  targets = ASG instances placed in app subnets, ASG members, RDS in one data subnet, IGW; findings REL-RDS-SINGLE-AZ
  and REL-TG-UNHEALTHY (no web server running) are correct. Detail diagram reviewed (ASG frame across AZs).
- Chat over the same account: first answer missed the ALB (search by VPC name); `find_collected_resources` gained a
  `vpc` scope and results carry the VPC name; rerun answered correctly.

## Cross-Account Role Verification (2026-10-17)

- `deploy/readonly-role.cfn.json` deployed as a temporary stack (role trusted by the collector user, External ID),
  then deleted. `collect --role-arn … --external-id … --region us-east-1,us-west-2`: both targets complete, exit 0,
  metadata records the role; a wrong External ID is refused (AccessDenied, exit 1).
- Found and fixed: terminated EC2 instances (still listed ~1 h after the test stack's deletion) were drawn as
  unplaced resources and inventoried; they are now skipped like deleted VPN connections.
- SSO: in-app device-code sign-in (`sso.py`, `/api/sso/*`, collection dialog) implemented; start URL auto-discovered
  from Identity Center. Live: an unapproved code expired after 10 min and was audited; an approved code led to account +
  `CloudOpsReadOnly` auto-selection and a complete us-east-1 collection identical to the key-based one (node IDs equal).
  Found and fixed: the account/role selection appeared before roles loaded, so a collection could start without a role.
- GPT-6: other regions briefly answered "Your account is currently being verified"; once that cleared (Nova works in
  us-east-2/us-west-2), GPT-6 Luna was still "not available for this account" in every region. Not a timing issue:
  the account needs AWS (Sales/Support) to enable OpenAI models.

## Chat Agent Verification (2026-10-17, local macOS, owner's test account)

- Offline: designer steps/errors, 7 standards (8 since 2026-10-18), tool schemas, scripted-model chat incl. GPT-6 denial → Nova fallback,
  moto collection tool, chat API + audit, `.env` loader, design layout readability (`make check`, 118 tests).
- Live Bedrock us-east-1 in Chromium: 3-AZ three-tier design (10 tool calls, 8 snapshots, review high 0), advice turn
  without tools, live collection + SG review via chat (6 high, matches CLI). CLI `chat -m` with an EKS standard.
- GPT-6 Luna: agreement AVAILABLE but every API (Converse, Chat Completions, Responses) returns "not available for
  this account"; answers came from Nova 2 Lite and are labelled as fallback. Nova once invented an S3 storage tip in
  free-form advice (prompt tightened); diagrams and findings are code-generated.

## Live AWS Verification (2026-10-17, local macOS, owner's test account, us-east-1)

- `diagram-bot collect` with `.env` keys: 30/30 calls complete in ≈6 s, exit 0. Counts equal the
  AWS CLI: 1 VPC, 6 subnets, 1 IGW, 1 route table, 17 SGs, 21 Lambda (none VPC-attached), 1 cache
  subnet group, 0 for every other service. Six SEC-SG-OPEN findings match the SG rules.
- Web app: collection through the dialog, overview (VPC card + Lambda card), detail (IGW + six AZ
  columns, all subnets public), inventory 47, review 6, coverage 30, `.drawio`/PNG/CSV exports;
  no page errors or external requests. `review --fail-on high` → 3, `diff --fail-on-change` → 4/0.
- Found and fixed: VPC-less Lambda missing from graph/inventory; shell `AWS_PROFILE` breaking
  `make check` (ProfileNotFound).

## Verification (2026-10-17, cloud container)

- Gate, team-mode Chromium run, layout tests, 27 browser checks and evidence files: docs/archive/STATUS-verification-2026-10.md.
  Current gate: `make check` = ruff + 238 pytest cases + CLI round trip; `verify_demo.py` 35 checks (incl. streamed chat, route
  filter, CloudFormation download, edge/DX samples); `verify_team.py` ok (2026-10-18). CI (GitHub Actions) green from 077c6ee.

## Harness

- overnight-harness v1.8.0 installed for Claude in this cloud container on 2026-10-17
  (`make overnight-where` resolves it; `harness-init --check` OK). Codex CLI is not present
  here; the repo's default engine remains codex. Since 2026-10-18 `.claude/settings.json` registers the marketplace and
  enables the plugin, so a trusted cloud session should install it itself (not yet observed in a cloud session).

## Open Limits

- Live AWS collection verified on one account (owner's test account, us-east-1/us-west-2) with keys, in-app SSO and
  a cross-account role (2026-10-17). Real resources seen live: VPC, subnets, IGW, ALB/TG, ASG/EC2, RDS, SGs, Lambda;
  every other service only on stubs/moto.
- 100-resource views are structurally correct but dense; no large-environment claim.
- Team mode provides local accounts and OIDC SSO, roles, TLS and audit; SAML only via an auth proxy; no built-in scheduler (use cron/CI with `review`/`diff` exit codes),
  listener rules, NACLs or Direct Connect. Licensed Apache-2.0; NOTICE holder "men16922 and Diagram Bot contributors" (both owner decisions, 2026-10-17).
  EKS/ECS/MSK/ElastiCache/OpenSearch/EFS/Redshift/Lambda/VPN/FSx/SageMaker/HyperPod/placement-group and the new CloudFront/WAF/
  Direct Connect calls are verified on stubs only (Elastic IPs and TGW route tables also on moto);
  ElastiCache Serverless, MSK Serverless, EKS Fargate profiles and Aurora Global Database are not drawn.
- Browser verification needs a provisioned editor/Chromium and is separate from the offline gate.
- CloudFormation drafts were never deployed to AWS (cfn-lint + moto only; moto skips EKS/ElastiCache/Aurora/FSx/MSK/OpenSearch).
