# Lessons

- Run tests through `make check`, not bare `pytest`: the `.env`/shell AWS keys and `AWS_PROFILE` break the moto
  emulator tests (1 failed + 2 errors); the Makefile unexports them for the offline gate.
- A green `make check` does not mean green CI: the local `.venv` already had `strands-agents`, CI installed its own
  package list and failed 9 pushes in a row. CI installs `.[test]` now; check `gh run list` after a push.
- A green local `make check` on macOS did not catch Linux-only hangs or browser-step races: reproduce a red CI in `python:3.x-slim`
  containers (build the wheel, `pip install .[test]`, `pytest -o faulthandler_timeout=40`) instead of guessing, and never pipe a gate
  through `tail` before a commit (the exit status is lost; two commits went in with red lint that way).
