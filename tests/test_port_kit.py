"""The porting kit: port_check (offline acceptance per Level), scripts/port_copy.py (closure copy) and docs/porting cards."""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from diagram_bot import port_check

ROOT = Path(__file__).resolve().parents[1]


def run_check(level, pythonpath):
    """port_check in a fresh interpreter (it scrubs the AWS environment of its own process)."""
    env = {**os.environ, "PYTHONPATH": str(pythonpath)}
    result = subprocess.run([sys.executable, "-m", "diagram_bot.port_check", "--level", str(level), "--json"], capture_output=True, text=True,
                            env=env, cwd=pythonpath, timeout=120)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    return json.loads(result.stdout)


def test_port_check_levels_cover_the_brief():
    names = {level: {name for lv, name, _ in port_check.CHECKS if lv == level} for level in port_check.LEVELS}
    assert {"render-sample", "inspect", "align-preserve", "spec-from-xml", "import-roundtrip"} <= names[0]
    assert {"design-standard", "design-step", "review", "iac-template", "diff", "combine"} <= names[1]
    assert {"session-factory", "collect-offline", "aws-access", "browser-relay", "stores"} <= names[2]
    assert {"routers-snapshots", "document-revision", "xml-align", "roles"} <= names[3]
    assert {"build-tools"} <= names[4]


def test_port_check_passes_on_the_repository():
    report = run_check(4, ROOT / "src")
    assert report["ok"] and {c["status"] for c in report["checks"]} == {"PASS"}, report["checks"]
    assert Path(report["package"]) == ROOT / "src" / "diagram_bot"


def test_port_check_reports_a_failure_and_exits_non_zero(monkeypatch, capsys):
    monkeypatch.setattr(port_check, "CHECKS", [(0, "broken", lambda: 1 / 0), (0, "optional", lambda: (_ for _ in ()).throw(port_check.Skip("x")))])
    monkeypatch.setattr(port_check, "offline_environment", lambda: None)
    assert port_check.main(["--level", "0"]) == 1
    out = capsys.readouterr().out
    assert "FAIL L0 broken: ZeroDivisionError" in out and "SKIP L0 optional: x" in out and "1 failed" in out


@pytest.mark.parametrize("level", [9, -1])
def test_port_check_refuses_unknown_levels(level):
    with pytest.raises(SystemExit):
        port_check.main(["--level", str(level)])
