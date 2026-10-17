# Diagram Bot: installed test dependencies required; no installation or AWS calls in the gate.
.DEFAULT_GOAL := check
PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
export AWS_EC2_METADATA_DISABLED := true
export AWS_CONFIG_FILE := /dev/null
export AWS_SHARED_CREDENTIALS_FILE := /dev/null

.PHONY: check lint test smoke-local
check: lint test smoke-local

lint:
	$(PYTHON) -m ruff check src tests scripts

test:
	$(PYTHON) -m pytest -q

smoke-local:
	$(PYTHON) scripts/verify_offline.py

# External notifications are opt-in; initialization does not configure recipients.
export HARNESS_NOTIFY ?= 0

# ===== overnight harness targets (append to your Makefile) =====
# The overnight runner + helpers are the Single Source of Truth in the overnight-harness
# PLUGIN; this repo does NOT vendor them. These targets resolve the installed plugin at
# runtime and invoke its runner against THIS repo. Per-repo STATE stays here:
#   scripts/overnight/overnight-settings.json  — Claude permission boundary
#   scripts/overnight/opencode.json            — opencode permission boundary
#   .codex/rules/overnight.rules               — Codex command rules
#   scripts/overnight/PROMPT.md                — optional per-repo prompt override (else plugin default)
#   scripts/overnight/{logs,STOP,DONE}         — runtime state
#   docs/LESSONS.md                            — the actor's memory surface (committed)
#
# The loop's commit gate is $GATE_CMD (default `make check`). Define a `check` target that proves
# correctness OFFLINE + DETERMINISTICALLY and allow-list it in scripts/overnight/overnight-settings.json.
#
# Select the engine with ENGINE=claude|codex|opencode|agy|kiro. This repository defaults to Codex.
ENGINE ?= codex

# Model routing (Claude engine; defaults refreshed in 1.7.0). Actor and critic both run on
# Opus 5.5. Its effort default is medium (one level below Opus 5), so both are pinned to high.
# Blank = the CLI's own default. These are per-repo policy, so edit them here rather than in
# the plugin.
CLAUDE_MODEL ?= claude-opus-5-5
CLAUDE_EFFORT ?= high
CLAUDE_CRITIC_MODEL ?= claude-opus-5-5
OVERNIGHT_CRITIC_MODEL ?=
CLAUDE_CRITIC_EFFORT ?= high
CLAUDE_FALLBACK_MODEL ?=
export CLAUDE_MODEL CLAUDE_EFFORT CLAUDE_CRITIC_MODEL OVERNIGHT_CRITIC_MODEL CLAUDE_CRITIC_EFFORT CLAUDE_FALLBACK_MODEL

# Codex routing is opt-in; preserve installed model/effort settings when blank.
# Astra preset: make overnight-codex-once CODEX_MODEL=gpt-6-astra
CODEX_MODEL ?=
CODEX_EFFORT ?=
CODEX_CRITIC_EFFORT ?=
export CODEX_MODEL CODEX_EFFORT CODEX_CRITIC_EFFORT

# HARNESS_ROOT resolution (env override → per-repo pin → highest installed version). This mirrors
# the plugin's bin/harness-locate.sh; override ad hoc with `make overnight HARNESS_ROOT=/path`.
HARNESS_ROOT ?= $(shell \
  if [ -n "$$OVERNIGHT_HARNESS_ROOT" ] && [ -d "$$OVERNIGHT_HARNESS_ROOT/templates/scripts/overnight" ]; then \
    echo "$$OVERNIGHT_HARNESS_ROOT"; \
  elif [ -n "$$OVERNIGHT_HARNESS_ROOT" ] && [ -d "$$OVERNIGHT_HARNESS_ROOT/plugins/overnight-harness/templates/scripts/overnight" ]; then \
    echo "$$OVERNIGHT_HARNESS_ROOT/plugins/overnight-harness"; \
  elif [ -f .claude/harness-config.json ] && grep -q '"harness_root"' .claude/harness-config.json; then \
    pin="$$(sed -n 's/.*"harness_root"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' .claude/harness-config.json | head -1)"; \
    if [ -d "$$pin/templates/scripts/overnight" ]; then echo "$$pin"; \
    elif [ -d "$$pin/plugins/overnight-harness/templates/scripts/overnight" ]; then echo "$$pin/plugins/overnight-harness"; fi; \
  else \
    { \
      ls -d $$HOME/.claude/plugins/cache/overnight-harness/overnight-harness/*/ 2>/dev/null; \
      find "$${CODEX_HOME:-$$HOME/.codex}/plugins/cache" -path '*/overnight-harness/*' -type d 2>/dev/null; \
      [ -d $$HOME/.gemini/antigravity-cli/plugins/overnight-harness ] && echo $$HOME/.gemini/antigravity-cli/plugins/overnight-harness; \
      [ -d $$HOME/.cache/opencode/node_modules/opencode-overnight-harness ] && echo $$HOME/.cache/opencode/node_modules/opencode-overnight-harness; \
    } | while read d; do [ -d "$$d/templates/scripts/overnight" ] && echo "$$d"; done | sort -V | tail -1; \
  fi)

# OVN_SRC = runner + helpers (in the plugin); OVN = per-repo state (in this repo).
# NB: no inline comments on these := lines — make would fold the gap into the value.
OVN_SRC := $(HARNESS_ROOT:%/=%)/templates/scripts/overnight
OVN := scripts/overnight

_harness-guard:
	@test -x "$(OVN_SRC)/run.sh" || { \
	  echo "overnight-harness not found (resolved HARNESS_ROOT='$(HARNESS_ROOT)')."; \
	  echo "Install the plugin, or pass HARNESS_ROOT=/path/to/plugin, or re-run /harness-init."; \
	  exit 1; }

overnight: _harness-guard           ## run the unattended loop (caffeinate keeps macOS awake)
	@mkdir -p $(OVN)/logs
	@if command -v caffeinate >/dev/null 2>&1; then \
	  OVERNIGHT_ENGINE=$(ENGINE) nohup caffeinate -dimsu "$(OVN_SRC)/run.sh" > $(OVN)/logs/launcher.log 2>&1 & \
	else \
	  OVERNIGHT_ENGINE=$(ENGINE) nohup "$(OVN_SRC)/run.sh" > $(OVN)/logs/launcher.log 2>&1 & \
	fi
overnight-watch: overnight          ## start the loop and tail its log
	@sleep 1; tail -f $(OVN)/logs/runner.log
overnight-once: _harness-guard      ## single iteration (smoke test the loop)
	OVERNIGHT_ENGINE=$(ENGINE) $(OVN_SRC)/run.sh --once
overnight-claude-once: _harness-guard
	OVERNIGHT_ENGINE=claude $(OVN_SRC)/run.sh --once
overnight-codex-once: _harness-guard
	OVERNIGHT_ENGINE=codex $(OVN_SRC)/run.sh --once
overnight-opencode-once: _harness-guard
	OVERNIGHT_ENGINE=opencode $(OVN_SRC)/run.sh --once
overnight-agy-once: _harness-guard
	OVERNIGHT_ENGINE=agy $(OVN_SRC)/run.sh --once
overnight-kiro-once: _harness-guard
	OVERNIGHT_ENGINE=kiro $(OVN_SRC)/run.sh --once
overnight-stop:                     ## graceful stop after the current iteration
	@touch $(OVN)/STOP && echo "STOP created — loop will exit after current iteration"
overnight-clean:                    ## clear STOP/DONE sentinels before the next run
	@rm -f $(OVN)/STOP $(OVN)/DONE && echo "cleared STOP/DONE"
overnight-status: _harness-guard    ## aggregate iteration status across lanes
	@bash $(OVN_SRC)/status.sh
overnight-logs:                     ## tail the runner log
	@mkdir -p $(OVN)/logs; touch $(OVN)/logs/runner.log; tail -f $(OVN)/logs/runner.log
overnight-dashboard: _harness-guard ## tmux dashboard (falls back to status.sh)
	@bash $(OVN_SRC)/dashboard.sh
overnight-ledger-check: _harness-guard ## validate event history before trusting status/report
	@python3 $(OVN_SRC)/lib/ledger.py check $(OVN)/logs/events.jsonl
overnight-ledger-state: _harness-guard ## project deterministic per-mission state as JSON
	@python3 $(OVN_SRC)/lib/ledger.py project $(OVN)/logs/events.jsonl
overnight-trajectory: _harness-guard ## render causal node/edge trajectory; optional MISSION and FORMAT=json
	@python3 $(OVN_SRC)/lib/trajectory.py $(OVN)/logs/events.jsonl \
	  $(if $(MISSION),--mission "$(MISSION)",) --format "$(or $(FORMAT),text)"
overnight-resume: _harness-guard       ## resume MISSION with DECISION=approve|reject
	@test -x "$(OVN_SRC)/resume.sh" || { echo "installed overnight-harness does not provide resume.sh"; exit 1; }
	@test -n "$(MISSION)" || { echo "MISSION=<mission-id> is required"; exit 2; }
	@test "$(DECISION)" = "approve" -o "$(DECISION)" = "reject" || { echo "DECISION=approve|reject is required"; exit 2; }
	@HARNESS_REPO_ROOT="$(CURDIR)" bash $(OVN_SRC)/resume.sh "$(MISSION)" --"$(DECISION)"
overnight-provenance-compare: _harness-guard ## compare two manifests; optional LEFT_RESULT/RIGHT_RESULT
	@test -n "$(LEFT)" -a -n "$(RIGHT)" || { echo "LEFT=<manifest> and RIGHT=<manifest> are required"; exit 2; }
	@python3 $(OVN_SRC)/lib/provenance.py compare --left "$(LEFT)" --right "$(RIGHT)" \
	  --left-result "$(LEFT_RESULT)" --right-result "$(RIGHT_RESULT)"
overnight-where:                    ## print the resolved plugin location (debug)
	@echo "HARNESS_ROOT = $(HARNESS_ROOT)"; echo "runner       = $(OVN_SRC)/run.sh"

.PHONY: overnight overnight-watch overnight-once overnight-claude-once overnight-codex-once overnight-opencode-once overnight-agy-once overnight-kiro-once overnight-stop overnight-clean overnight-status overnight-logs overnight-dashboard overnight-ledger-check overnight-ledger-state overnight-trajectory overnight-resume overnight-provenance-compare overnight-where _harness-guard
# ===== end overnight harness targets =====
