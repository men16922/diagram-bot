# Progress Log

Last Updated: 2026-10-17

## 2026-10-17 — More services and HA detail (user request: OpenSearch 등, primary/standby·Multi-AZ·replica)

- Collector: OpenSearch (ListDomainNames, DescribeDomains, DescribeDomainNodes), ECS (clusters,
  services, tasks), EFS (file systems, mount targets), Redshift (clusters, subnet groups), MSK
  ListNodes, VPC peering, VPN gateways/connections, customer gateways; IAM prefixes es/
  elasticfilesystem; policy example updated.
- Graph: OpenSearch nodes per AZ (data/master/standby), ECS tasks as target-group targets, IP
  targets placed by subnet CIDR, RDS read replicas and Multi-AZ DB cluster readable standbys,
  ElastiCache shards, Redshift Multi-AZ, EFS mount targets, peering, VPN, `0.0.0.0/0 → VGW`.
- Layout: four-line labels with HA role and size, ECS service frames, VPN routes to the data
  center box, overview cards listing every collected kind, frame captions with capacity.
- Samples: new data-platform (two VPCs); three-tier read replica; microservices ECS services;
  hybrid VPN over TGW. Verified: 51 tests, browser acceptance 26 checks.

## 2026-10-17 — Common services (user request: EKS·MSK·RDS·ElastiCache·NAT 등)

- Collector: rds DescribeDBClusters, ElastiCache cache clusters/replication groups/subnet groups,
  kafka ListClustersV2, lambda ListFunctions, eks ListClusters/DescribeCluster/ListNodegroups/
  DescribeNodegroup (per-cluster coverage rows); policy example updated.
- Graph/layout: Aurora writer/reader, Redis primary/replica, MSK brokers per client subnet, EKS
  control plane + node-group frames, VPC Lambda; attachment row split from the load balancer row.
- New sample eks-platform (EKS, ALB→pod IPs, Aurora, Redis, MSK, Lambda, endpoints).
- Verified: 48 tests incl. every eks-platform view, browser acceptance incl. EKS sample.

## 2026-10-17 — Recommended next steps (user request: 추천안대로 수행)

- Merged the infrastructure work to main (fast-forward 5938d03..77749d9); CI passed on main.
- Added VPC endpoints (gateway via route tables, interface via subnets) and Transit Gateway
  VPC attachments: collector (3 Describe calls), graph, entry-row placement, TGW in the
  region box, `0.0.0.0/0 → TGW` subnet class; samples gained S3/SSM/ECR endpoints and TGW hubs.
- Verified: 47 tests, browser acceptance, renders reviewed.

## 2026-10-17 — Infrastructure architecture (user request: 인프라 아키텍처 기반)

- Collector: DescribeInternetGateways, DescribeNatGateways, DescribeRouteTables, rds
  DescribeDBInstances, autoscaling DescribeAutoScalingGroups; readonly-policy.json updated.
- Graph: attachment/placement/routing/standby/membership relations with API evidence; subnet
  route class (public/nat/isolated) from explicit or main route table associations.
- Layout: IGW in the entry row, NAT/RDS in their subnets, Auto Scaling frames across AZ columns
  (one row per group inside a subnet), target group → Auto Scaling group edge replaces
  per-instance registrations when the frame encloses only members.
- Samples rebuilt with IGW, NAT per AZ (staging: one), route tables, RDS Multi-AZ, ASGs.
- Fixed during review: ASG node duplicated into the reference box (new unique-ID check),
  relation direction for TargetGroupARNs, misplaced route code, interleaved ASG frames.
- Verified: 47 tests, browser acceptance incl. infrastructure sample, renders reviewed.

## 2026-10-17 — AWS Architecture Icons styling (user request: 더 AWS best practice 형태로)

- Groups: AWS Cloud > Region > VPC > AZ > public/private subnet with official aws4 group styles;
  resource-level icons for EC2/ALB/NLB/GWLB/ENI/EBS; tiers aligned across AZ columns;
  AvailabilityZone=all targets in a Corporate data center group beside the cloud.
- Sources: AWS Architecture Icons deck excerpts (search) and draw.io aws4 library; AWS domains
  are blocked from this environment, so official pages were not read directly.
- Fixed: on-premises routes crossing the reference box (box moved beside the cloud).
- Verified: 47 tests, browser acceptance, all sample views rendered and reviewed.

## 2026-10-17 — AWS best-practice samples (user request)

- samples.py: three-tier (3 AZ, public + internal ALB), microservices (one ALB, four target groups,
  IP targets), nlb-alb (NLB → ALB static entry, TLS NLB), environments (prod/staging/shared VPCs),
  hybrid (on-premises IP targets). Seeded in the app; `diagram-bot demo --sample <key>`.
- Samples exposed two layout defects, fixed: second reference-box row routed through labels
  (box is now one row) and lane wrap in busy rows (lane space now sized per row).
- Layout: subnets ordered by tier (public → web → app → data), internet-facing LBs first,
  AvailabilityZone=all targets drawn with the AWS traditional-server icon.
- Verified: 47 tests incl. every sample view; browser acceptance; renders reviewed.

## 2026-10-17 — Edge overlap cleanup (user request: 선 겹침 정리)

- Router rewrite: per-source buses, per-group gutter slots, per-edge row lanes, right-margin
  routes for right-side outside targets, explicit ALB/NLB placement routes in focus view.
- Measured before: 6 shared segments (sample with references), 24 (100-resource view); after: 0.
- New test asserts orthogonal routes and no shared segment between unrelated routes
  (mutation-checked); `make check` 42 passed; browser acceptance passed; renders reviewed.

## 2026-10-17 — Topology plan P1–P5 implemented (cloud session)

- Installed overnight-harness v1.8.0 (Claude marketplace); `make overnight-where` resolves it.
- P1/P2 commit: relation IDs, views.py, layout.py, serializer-only drawio.py, rich sample,
  ELK experiment (scripts/experiments/elk) and evaluation doc.
- P3/P4 commit: view-aware document API, history/relayout/restore, UI view switcher, focus view,
  relation highlight, bridge relationId support; browser acceptance rewritten.
- P5: examples/demo and docs/images regenerated from the final code; docs updated.
- Verified: `make check` (42 tests + CLI round trip); Chromium acceptance 23 checks with no page
  errors/external requests; geometry checker mutation-tested (overlap, clip, edge crossing).
- Visual review of rendered views done by the implementer; human review remains [manual].
- Not verified: live AWS collection — container keys rejected (`InvalidClientTokenId`); the CLI
  failed cleanly with exit 1. Merged to main by fast-forward at the user's request.

## 2026-10-17 — Commit/push preparation

- User requested committing and pushing the topology plan and prepared repository state.
- Verified `make check`: 24 tests passed; five CLI report artifacts reproduced identically.
- Fetched origin/main and confirmed no divergence from the application baseline before commit.
- Runtime logs and sentinels remain ignored; no live AWS collection or overnight actor run.

## 2026-10-17 — AWS topology refinement plan

- Inspected collector, graph, native XML layout, editor bridge, UI and existing plan/status.
- Added docs/TOPOLOGY_PLAN.md with P1–P5, bounded demo scope and concrete acceptance criteria.
- Updated current-plan pointers and next work; preserved existing harness initialization state.
- Plan only: no application implementation, AWS calls, overnight actor run, commit or push.
- Documentation diff/links checked; application tests are not claimed for this planning pass.

## 2026-10-17 — Harness initialization checkpoint

- Status: initialization complete and checkpointed locally; changes remain uncommitted.
- Changed: Codex v1.8.0 harness state, command boundaries, offline gate and context docs;
  CODEX_HOME-aware discovery and Linux/macOS launch wiring. Plugin owns runner and bibles.
- Verified: `harness-init.sh --check` and `make overnight-where` resolved the installed plugin.
- Verified: `make check` passed 24 tests and reproduced five CLI report artifacts identically.
- Verified: `HARNESS_NOTIFY=0 OVERNIGHT_REPO_WRITE_PROBE=0 ENV_DOCTOR='make check'
  MAX_ITER=0 make overnight-codex-once` ended normally without invoking an actor.
- Verified: `make overnight-ledger-check` passed; installer re-run created 0/skipped 16 files
  and preserved existing state byte-for-byte. Config/rule syntax and doc budgets passed.
- Blockers: no initialization blocker. Full actor/gate/commit smoke and live AWS remain unverified;
  an empty-backlog required-contract experiment was removed in favor of plugin defaults.
- Next: docs/NEXT_PLAN.md contains manual acceptance work; approved [auto] backlog remains 0.

Evidence: scripts/overnight/logs/runner.log and events.jsonl (local, Git-ignored).
Checkpoint edits only reorganized documentation; application tests were not rerun in this pass.
