# Progress Log

Last Updated: 2026-10-19

## 2026-10-19 — Porting kit for GPT-6 Luna / GPT-6.1 Sol (goal: 서브에이전트로 카드별 작업, $100·세션 150K)

- `scripts/port_copy.py --level N` (import closure copy + MANIFEST), `python -m diagram_bot.port_check --level N` (offline), docs/porting
  (README, RULES, ORCHESTRATOR on Sol, cards T0–T6). Copy test runs port_check on each isolated copy; fixed 7 porting-guide mismatches.

## 2026-10-19 — Screen for draw.io XML, company standards, spec export and audit log (goal: API·CLI만이 아니라 실제 화면에서)

- Tools › draw.io XML dialog: import as a snapshot (import report; badge "XML 가져옴 · 라벨 추정"), auto-align (before/after metrics,
  aligned download), adopt as a company standard (admin/local may make it the server default) and the standards list (open, admin delete).
- Adopted default standards persist in `<data-dir>/standards/`; `GET /api/standards`, `DELETE /api/standards/{name}` (admin).
- Download dialog › 규격: preset or company spec export (fresh rendering, never saved). Profile menu › 감사 로그 for team-mode admins.
- Also: imported isolated subnets stay the data tier. Verified: gate 483 tests; verify_demo 40/40, verify_team 13/13 (viewer XML disabled).

## 2026-10-19 — Review of the company draw.io XML work, codebase hardening and refactor (goal: antigravity 최종 커밋 검수·피드백, 2단계 수준 상승, 소스 전반 점검·리팩토링)

- Review of 6622915 (antigravity): gate green but 19 reproduced defects (security: request `spec` read server files; edge waypoints in the
  wrong frame; adopt changed the server-wide default; 500s on hostile XML; first page only; label guessing invented EC2s). All fixed with
  regression tests (`tests/test_xml_robustness.py`); level 2: every page and compressed pages, cell → resource map, one classification table,
  quality guard (align never returns a worse page; a no-op on the bot's own output), grid index (3,200 shapes 13.7 s → 1.8 s).
- Codebase audit (bugs with reproducers, duplication, god modules, test gaps) → fixes in graph (MSK partial), server (body cap before read),
  auth (malformed hash, torn audit line, (client, name) throttle), store (corrupt file, cache race, atomic 0600 writes), SSO/chat
  (refreshing Bedrock credentials, 409 on expired SSO, no AWS I/O under locks), egress (async long-poll, tab id, re-queue reads only),
  collector (`RelayDisconnected` fails the job), designer (`@atomic`, duplicates, brownfield key collision), samples (IPs inside subnets,
  `SAMPLE_VERSION` 7), IaC (derived logical IDs unique), findings (UDP false positive), layout (zero-VPC combined overview, seed-dependent
  combined focus frame), frontend (stuck chat loading, stale loads, viewer exports, SSO use, error parsing).
- Refactor behind golden hashes (`tests/golden.json`, stable across PYTHONHASHSEED): `layout/`, `iac/`, `samples/` packages; findings,
  graph and collector split into named functions; `app.js` → 10 `js/` modules; shared `snapshot.py`, `sessions.py`, `errors.py`,
  `fileio.py`, `validation.py`, `version.py`, `expiry.py`. Wiki pages, index, `llms.txt`, DECISIONS (XML feature scope) updated.
- After the push CI went red twice, both test-side races that the local gate could not show: the browser-relay end-to-end test hung on
  Linux when its page thread's last `/api/relay/next` poll was still open as the TestClient closed (reproduced in python:3.11/3.13
  containers; fixed by joining the thread, 6/6 full runs green on 3.11-3.13), and `verify_demo.py` opened the combine dialog before the
  second imported account reached the snapshot list (it now waits on the app state). `ae4cb08`, `03641fe`; CI green.
- Verified: `make check` equivalent (ruff + 476 pytest + offline smoke), `scripts/verify_demo.py` 35/35 checks with no page errors,
  `scripts/verify_team.py` 11/11. Not run: `verify_browser_egress.py` (live AWS, outside the offline gate).

## 2026-10-18 — Company draw.io spec, existing XML auto-alignment & import, two-tier porting docs (goal: 모듈화, 회사 drawio 규격/자동 정렬, 사람용 High-level + 코딩 에이전트용 토큰 최적화 문서)

- Custom draw.io specification (`drawio_spec.py`): `DrawioSpec` dataclass + presets (`DEFAULT_SPEC`, `ENTERPRISE_CLEAN_SPEC` / `"clean"`, `MXCELL_SPEC` / `"mxcell"`) + `spec_from_xml(xml_text)` to auto-extract wrapper tags (`<mxCell>` vs `<object>`), header presence, font family, container styles, distinct isolated/data subnet styles, and edge arrow styles from any sample company `drawio.xml`. Wired through `layout.py`, `drawio.py`, `api.render(..., spec=...)`, and `GET /api/snapshots/{id}/document?spec=...`.
- Existing `drawio.xml` diagnostics, rule-based alignment, and snapshot import (`xml_align.py`):
  - `inspect_drawio_xml` (`api.inspect_xml`): reports sibling overlaps, out-of-bounds children, unaligned AZ subnet tiers, unrouted edges, edge-icon collisions, and extracted `DrawioSpec`.
  - `align_drawio_xml` (`api.align_xml`): `mode="preserve"` keeps all original cell IDs, labels, and wrapper structure while reparenting flat visual children, snapping resources into layered grids, synchronizing subnet tier rows across AZ columns, resizing containers bottom-up, and routing orthogonal edges around obstacles; `mode="rebuild"` imports and re-renders via `generate_document`.
  - `import_drawio_xml` (`api.import_xml`): parses an existing company `drawio.xml` into a valid Diagram Bot `Snapshot` (`raw` + `graph`) so it can immediately be extended with `Design.start_from_snapshot`, reviewed (`findings.review`), diffed, or exported to CloudFormation (`iac.template`).
  - Exposed via HTTP (`POST /api/xml/align`, `POST /api/xml/import`) and CLI (`diagram-bot align-xml`, `diagram-bot import-xml`).
- Two-tier documentation: created `docs/HUMAN_GUIDE.md` (high-level overview, Level 0~4 module map, 5 copy-paste prompts for company coding agents, 3-phase productionization roadmap) and `docs/wiki/drawio-format-and-alignment.md` + updated `docs/wiki/porting-guide.md`, `llms.txt`, and `docs/wiki/index.md` for token-efficient coding-agent execution.
- Verified: `make check` (ruff + 242 pytest cases + offline CLI round-trip smoke) and `python scripts/wiki_index.py --check`.

## 2026-10-18 — Company auth structure, porting refactor, LLM wiki (owner: 인증 구조 회사것과 동일하게; 포팅하기 좋게 리팩토링; LLM Wiki)

- Browser egress (`serve --aws-egress browser`): Identity Center device code + GetRoleCredentials on the server; every AWS
  request built and signed by botocore and handed to the browser at before-send (`egress.py`, `/api/relay/*`). Measured with
  real Chromium: 18 collector services + Bedrock answer CORS for signed requests; Identity Center endpoints do not (stay on the
  server). Live with the server's own AWS egress blocked: collection 42/42 and a Bedrock chat turn, 46 requests from the browser.
- Refactor for porting: `store.py`, `aws_access.py`, `routes/*` (mountable routers), `api.py`, frontend `js/` modules; server.py
  760 → ~130 lines, behaviour unchanged (tests, verify_demo 35, verify_team, live egress re-run after the refactor).
- LLM wiki: 24 code-grounded pages + SCHEMA/log/index, `llms.txt`, `scripts/wiki_index.py`; tests check sources, links, index
  freshness and UI labels (found and fixed 4 missing relation labels).
- Also: CI test client on `httpx2` (deprecated fallback is an error). Verified: `make check` 238; CI green on 7698e86.

## 2026-10-18 — Remaining work finished (goal: 잔여 작업 수행, 승인 없이 최대한 완성도 있게)

- CI was red on every push since db76a70 (`strands` missing from its hand-written install list): it now installs `.[test]`,
  Actions on Node 24; clean 3.11/3.13 venvs pass. LESSONS: green `make check` ≠ green CI.
- Chat streaming: `POST /api/agent/chat/stream` (NDJSON text/tool/model/snapshot → done|error); the assistant shows tool
  progress and opens each design step. Live Nova 2 Lite: 8 tools, 5 snapshots over 11 s.
- Brownfield designs: `start_from_current` (open snapshot → last collection) adopts VPC tiers per AZ, NAT tables, LBs and
  TGs; `add_subnet_tier`; standards are extendable in their own VPCs; `chat --from`; never writes routes into uncollected
  tables. Live: three-tier + Redis, existing resources kept (29 tools).
- CloudFormation export (`export-cfn`, API, download dialog): designs, samples and collected snapshots; Ref/GetAtt only,
  parameters for AMIs/certs/roles/images, Secrets Manager passwords, TGW route tables; CloudFront/WAF rules/Direct Connect
  and other inexpressible items in `Metadata.DiagramBot.Unsupported`. cfn-lint clean for every sample; moto stacks for two.
- Collection: Elastic IPs, TGW route tables, CloudFront, WAFv2, Direct Connect (policy + role template); drawn at the edge /
  data-center box / labels; rules SEC-ALB-WAF, SEC-CF-ORIGIN-OPEN, COST-EIP-IDLE, REL-TGW-BLACKHOLE (all low).
- Samples (SAMPLE_VERSION 6): microservices 3 AZ, EKS /28 control-plane subnets + Aurora reader, central egress VPC;
  three-tier CloudFront+WAF, hybrid Direct Connect (VPN backup), data-platform TGW routing domains; gpu-training moved to
  us-east-2 (trn2 is not offered in ap-northeast-2, found by cfn-lint).
- Route filter for dense views (`routes=`, UI `경로 필터`): 100-resource VPC crossings 376 → 0; focus/detail/overview
  documents now tested for explicit routes clear of captions (fixed two caption crossings and a wrong empty-subnet note).
- Project `.claude/settings.json` registers the overnight-harness marketplace and plugin (not yet tried in a cloud session).
- Verified: `make check` 229 tests; verify_demo 35 checks, no page errors/external requests; all samples re-rendered and
  three-tier/hybrid/environments PNGs reviewed. Work ran as three parallel agents in worktrees plus the main session.

## 2026-10-18 — AWS console-style UI, overview at a glance (owner: 전체 구성이 늘 같다; Gemini처럼 우측 채팅; 로그인→프로필; AWS 스타일)

- Overview VPC cards now show services per tier (public / private / isolated) with icons and counts; isolated subnets count
  as their own tier; the unplaced card ignores references and drawn hubs.
- UI: dark top navigation (region, AI assistant, sign-in profile), white side navigation, breadcrumbs, page header,
  Cloudscape-like containers/buttons/tabs. Sign-in (SSO or profile/default chain via GET /api/identity, STS) shows
  `role @ account`; the profile menu holds collection and sign-out. AI assistant docks on the right and reflows content.
- Fixed: the page scrolled 85 px when draw.io focused its iframe on load.
- Verified: `make check` 178 tests (per-sample overview cards); verify_demo 31 checks (profile menu, sign-in dialog, assistant
  reflow, no overflow at 1280/980/640); verify_team ok; live: sign-in showed the IAM user @ account, and a free-form chat design
  (2-AZ VPC, ALB, ECS Fargate, PostgreSQL Multi-AZ) ran 6 tools and opened the design (review 0 findings).
- Live SSO through the new profile menu (owner approved device code): roles listed, CloudOpsReadOnly preselected, profile shows
  `CloudOpsReadOnly @ account (SSO)`, collection dialog shows the identity, us-east-1 collected (47 resources). verify_demo 32 checks
  now walk the same SSO UI against a stand-in Identity Center.
- Live SSO sign-out round trip: profile returns to 로그인 at once, POST .../logout 200, the server session is gone (404);
  verify_demo also asserts the logout request reaches the server.

## 2026-10-18 — Multi-account view, live check of the AI calls (goal: 잔여 작업 완료시까지)

- Multi-account: `combine.py` merges account × region snapshots; `merge_graphs` makes a RAM-shared Transit Gateway or a
  cross-account peering one node (owner's copy). Overview frames each account's VPC cards (AWS account group) with routes
  outside the frames. CLI `diagram-bot combine`, `POST /api/snapshots/combine`, app dialog `계정 통합 보기`; sample
  `multi-account` (Network account TGW + central egress + VPN, prod and staging accounts).
- Live (owner's account, us-east-1, `.env` keys, read-only): 34/34 calls complete, incl. DescribePlacementGroups,
  fsx DescribeFileSystems, sagemaker ListEndpoints/ListClusters (0 resources, so non-empty shapes rest on Stubber tests).
- Verified: `make check` 166 tests; verify_demo 30 checks (multi-account frames, shared TGW, combine dialog).
