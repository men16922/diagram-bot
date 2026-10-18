# Lessons

- Run tests through `make check`, not bare `pytest`: the `.env`/shell AWS keys and `AWS_PROFILE` break the moto
  emulator tests (1 failed + 2 errors); the Makefile unexports them for the offline gate.
- A green `make check` does not mean green CI: the local `.venv` already had `strands-agents`, CI installed its own
  package list and failed 9 pushes in a row. CI installs `.[test]` now; check `gh run list` after a push.
