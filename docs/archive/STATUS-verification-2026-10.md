# Status archive: container verification (2026-10-17)

Moved from docs/STATUS.md on 2026-10-17 (doc budget).

## Verification (2026-10-17, this container)

- `make check`: ruff lint + 118 pytest cases (incl. moto AWS-emulator end-to-end collection and team mode) + five-artifact CLI round trip passed.
- Team mode (`scripts/verify_team.py`, Chromium): TLS server on 0.0.0.0, refusal without credentials, login/throttle, viewer/admin roles, cookie flags, logout, OIDC SSO through a loopback test provider, audit log.
- Layout tests: no sibling overlap (icons + label boxes), nothing outside its container, no
  routed segment through another icon/label, identical rerun — all sample views and both VPCs
  of a 100-resource fixture. 100-resource layout of all pages ≈0.02 s (limit 5 s).
- Browser (`scripts/verify_demo.py`, Chromium, draw.io 32.4.1): 27 checks incl. overview →
  detail → focus, ALB relations/evidence, relation highlight, native keyboard move + note,
  save/reload, `.drawio` with moved geometry, PNG 1×/2×/3× and transparent, relayout keeps
  the note, restore, JSON/CSV, import. No page errors, no external requests.
- Downloaded `.drawio` reopened in draw.io renders the same edit as the exported PNG.
- Evidence: examples/demo/ (CLI report, edited-topology.drawio/png, verification.json),
  docs/images/{overview,workspace,focus}.png. All synthetic, labelled SAMPLE.

