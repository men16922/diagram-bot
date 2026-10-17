# Lessons

- Run tests through `make check`, not bare `pytest`: the `.env`/shell AWS keys and `AWS_PROFILE` break the moto
  emulator tests (1 failed + 2 errors); the Makefile unexports them for the offline gate.
