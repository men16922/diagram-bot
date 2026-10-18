# Completed Summary

Last Updated: 2026-10-19

## Finished checklists moved from NEXT_PLAN — completed 2026-10-17/18

Moved here on 2026-10-19 so the rolling plan lists open work only.

### Manual follow-up

- [x] [manual] Validate AWS collection on one account/region — done 2026-10-17 with IAM user keys
  (STATUS.md "Live AWS Verification").
- [x] [manual] Live check with real workloads — done 2026-10-17 on a temporary CloudFormation stack
  (VPC, ALB, ASG × 2 t3.micro, RDS), created and deleted with the user's approval (STATUS.md).
- [x] [manual] Cross-account role collection — done 2026-10-17 with the repo's role template (temporary stack).
- [x] [manual] SSO collection from the app — done 2026-10-17: device code approved (owner signed in, agent confirmed the
  code and allowed access at the owner's request), account and CloudOpsReadOnly auto-selected, collection complete with
  the same 47 resources / node IDs as the key-based collection; audited as `via: sso:CloudOpsReadOnly`.
- [x] [manual] Visual review of docs/images/*.png and examples/demo/edited-topology.png — done 2026-10-17 by the agent
  (model review of all 8 screenshots + the edited PNG). All four issues it found were fixed on 2026-10-18 (PROGRESS_LOG):
  readable start zoom (was 23–31 % at 1440×900), toasts moved off the canvas, one colour per target group route,
  word-boundary cuts for long names.

- [x] Default chat model: Amazon Nova 2 Lite (owner's decision 2026-10-17). GPT-6 Luna stays selectable; it answers
  once AWS enables OpenAI models for the account (support-case draft prepared, owner sends it if wanted).

### Sample Best-Practice Alignment (owner request 2026-10-18)

- [x] docs/ARCHITECTURE.md: AWS sources, per-sample verdicts and a ranked change list.
- [x] Apply the change list (docs/ARCHITECTURE.md §4) — done 2026-10-18 (items 1–6; optional item 7 left out). Top: Redshift Multi-AZ
  subnet group (MUST), data-platform peering+VGW → TGW hub, second CGW/VPN in hybrid, missing `ecr.api`/SSM endpoints.
- [x] AI/GPU (docs/ARCHITECTURE.md §5): A1 `ai-platform`, A2 icons, A3 GPU review rules — done 2026-10-18.

### Remaining Work Finished (owner goal 2026-10-18: 잔여 작업 수행, 승인 없이)

- [x] CI fixed (`.[test]`); chat streaming; brownfield designs; CloudFormation export; collection of Elastic IPs,
  TGW route tables, CloudFront/WAF, Direct Connect; optional sample items (ARCHITECTURE.md §4 #7); route filter for dense
  views; focus routes tested (they already used gutters); overnight-harness via project settings. Details: PROGRESS_LOG.

## Remaining work, best-practice samples, AI/GPU, console UI — completed 2026-10-18

- 11 samples on AWS best practice (docs/ARCHITECTURE.md) incl. AI/GPU and multi-account; AI/ML collection and review pillar;
  network overview; console-style UI; chat streaming and brownfield designs (29 tools); CloudFormation export; Elastic IP,
  TGW route table, CloudFront/WAF and Direct Connect collection; route filter; CI repaired.
- Details: docs/PROGRESS_LOG.md (and its archive), CHANGELOG.md.

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
  Live AWS SSO collection not verified at the time (done later on 2026-10-17, see above).
- Plan (archived): docs/archive/TOPOLOGY_PLAN.md.

## Diagram Bot v0.2 demo

- AWS read-only collection and deterministic API-evidence graph.
- Hava-inspired AWS workspace with embedded native draw.io and resource details.
- Manual diagram edits, persisted view revisions, editable draw.io and PNG downloads.
- Snapshot import/history and JSON/CSV export.
- 24 pytest cases, browser acceptance flow and GitHub CI passed at `d27657e`.
- Documentation and synthetic examples are linked from README.md and docs/DEMO.md.
- Original product plan (archived): docs/archive/PLAN-2026-10-17.md.

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
- Plan (archived): docs/archive/2026-10-18-best-practice-samples.md.

## Network overview and AI/GPU strength — completed 2026-10-18

- Overview draws the TGW/peering/VPN network; single unconnected VPC opens in detail; stale notices cleared.
- AI/GPU: FSx/SageMaker/HyperPod/placement collection, AI/ML review pillar (17 rules), samples genai-rag and gpu-training,
  ai-platform with EFA/PG/FSx, designer tools. Evidence: `make check` 160 tests, verify_demo 29 checks.
