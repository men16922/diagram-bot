# Status

Last Updated: 2026-10-17

## Active Plan

- AWS topology refinement plan (docs/TOPOLOGY_PLAN.md): P1–P5 implemented and verified on the
  synthetic sample. Merged to main (fast-forward) on 2026-10-17 at the user's request.
- Remaining work is manual only: live SSO collection check and human visual review
  (docs/NEXT_PLAN.md). No [auto] backlog is approved.

## Current Baseline (v0.3)

- Collection: direct SSO/SDK, read-only EC2/ELBv2/RDS/Auto Scaling/EKS/MSK/ElastiCache/Lambda calls incl. internet and
  NAT gateways, route tables, VPC endpoints and Transit Gateway attachments; coverage rows name the
  IAM action needed when denied or partial.
- Diagrams: AWS Architecture Icons style (Cloud > Region > VPC > AZ > route-classified subnets),
  IGW/NAT/RDS standby, Auto Scaling frames; samples in examples/samples (6 reference patterns incl. EKS platform).
- Graph: relation IDs from source/relation/target + port/AZ qualifiers; independent of API
  array order and target health. Same instance on two ports = two relations. Legacy snapshots
  get IDs on read in their stored order, so saved `relationIndex` documents still resolve.
- Views (`views.py`): overview (account/region + VPC summary cards), detail (one VPC), focus
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

## Verification (2026-10-17, this container)

- `make check`: 48 pytest cases + five-artifact CLI round trip passed (GitHub Actions green on main 15665ad).
- Layout tests: no sibling overlap (icons + label boxes), nothing outside its container, no
  routed segment through another icon/label, identical rerun — all sample views and both VPCs
  of a 100-resource fixture. 100-resource layout of all pages ≈0.02 s (limit 5 s).
- Browser (`scripts/verify_demo.py`, Chromium, draw.io 32.4.1): 25 checks incl. overview →
  detail → focus, ALB relations/evidence, relation highlight, native keyboard move + note,
  save/reload, `.drawio` with moved geometry, PNG 1×/2×/3× and transparent, relayout keeps
  the note, restore, JSON/CSV, import. No page errors, no external requests.
- Downloaded `.drawio` reopened in draw.io renders the same edit as the exported PNG.
- Evidence: examples/demo/ (CLI report, edited-topology.drawio/png, verification.json),
  docs/images/{overview,workspace,focus}.png. All synthetic, labelled SAMPLE.

## Harness

- overnight-harness v1.8.0 installed for Claude in this cloud container on 2026-10-17
  (`make overnight-where` resolves it; `harness-init --check` OK). Codex CLI is not present
  here; the repo's default engine remains codex. Cloud containers need reinstalling per session.

## Open Limits

- Live AWS collection remains unverified. 2026-10-17 attempt from the cloud container: STS is
  reachable but the container's environment keys return `InvalidClientTokenId`; `diagram-bot
  collect` stopped with exit 1 and wrote no report (expected failure path). Needs a valid
  read-only key or SSO profile for the intended account.
- 100-resource views are structurally correct but dense; no large-environment claim.
- No shared-user hosting, scheduled collection, snapshot diff or listener rules. ECS, EFS and
  OpenSearch are not collected. EKS/MSK/ElastiCache/Lambda calls are verified on stubs only.
- Browser verification needs a provisioned editor/Chromium and is separate from the offline gate.
