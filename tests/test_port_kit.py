"""The porting kit: port_check (offline acceptance per Level), scripts/port_copy.py (closure copy) and docs/porting cards."""
import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from diagram_bot import port_check

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("port_copy", ROOT / "scripts" / "port_copy.py")
port_copy = importlib.util.module_from_spec(spec)
spec.loader.exec_module(port_copy)
# -I drops PYTHONPATH, the script directory and user site; the venv's editable-install .pth still adds a src directory,
# so every sys.path entry holding a diagram_bot package is removed before the copy goes first.
ISOLATED = ("import os, runpy, sys; sys.path[:] = [p for p in sys.path if not os.path.isdir(os.path.join(p, 'diagram_bot'))]; "
            "sys.path.insert(0, sys.argv[1]); sys.argv = ['port_check', '--level', sys.argv[2], '--json']; "
            "runpy.run_module('diagram_bot.port_check', run_name='__main__', alter_sys=True)")


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


def copy(level, dest, *extra):
    return port_copy.main(["--level", str(level), "--dest", str(dest), *extra])


@pytest.mark.parametrize("level", port_check.LEVELS)
def test_each_level_copy_passes_port_check_with_only_its_files(level, tmp_path, capsys):
    package, web = tmp_path / "company" / "diagram_bot", tmp_path / "web"
    assert copy(level, package, "--static-dest", str(web)) == 0
    printed = capsys.readouterr().out
    manifest = json.loads((package / "MANIFEST.json").read_text())
    assert manifest["level"] == level and set(manifest["files"]) == {p.relative_to(package).as_posix() for p in package.rglob("*") if p.is_file()} - {"MANIFEST.json"}
    assert sorted(manifest["static"]) == sorted(port_copy.static_files(level)) and ("viewer.html" in manifest["files"]) == ("report.py" in manifest["files"])
    assert "pip install 'boto3" in printed and ("fastapi" in printed) == (level >= 3)
    result = subprocess.run([sys.executable, "-I", "-c", ISOLATED, str(package.parent), str(level)], capture_output=True, text=True, timeout=120,
                            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, cwd=tmp_path)
    assert result.returncode == 0, result.stdout[-3000:] + result.stderr[-3000:]
    report = json.loads(result.stdout)
    assert Path(report["package"]) == package.resolve()
    loaded = {name: Path(file).resolve() for name, file in report["modules"].items() if file}
    assert all(package.resolve() in f.parents for f in loaded.values()), loaded  # nothing came from the repository
    assert {f.relative_to(package.resolve()).as_posix() for f in loaded.values()} <= set(manifest["files"])
    assert {c["status"] for c in report["checks"]} <= {"PASS", "SKIP"} and max(c["level"] for c in report["checks"]) == level


def test_closure_follows_lazy_and_package_imports():
    modules, external = port_copy.closure(3)
    assert {"routes.standards", "validation", "fileio", "layout.text", "samples.catalog"} <= set(modules)
    assert "agent" not in modules and "server" not in modules and "report" not in modules
    assert port_copy.closure(4)[1]["strands"] is True and external["fastapi"] is False  # strands only inside functions
    assert port_copy.requirements(4)[1] == ["strands-agents>=1.61,<2"]


def test_copy_refuses_a_manifest_from_another_commit(tmp_path, capsys):
    package = tmp_path / "diagram_bot"
    assert copy(0, package) == 0
    manifest = json.loads((package / "MANIFEST.json").read_text())
    (package / "MANIFEST.json").write_text(json.dumps({**manifest, "commit": "0" * 40}))
    assert copy(1, package) == 2 and "refusing" in capsys.readouterr().err
    assert copy(1, package, "--force") == 0 and json.loads((package / "MANIFEST.json").read_text())["level"] == 1
    assert copy(0, package) == 0 and "not in level 0" in capsys.readouterr().out  # same commit: re-copy allowed, stale files named


def test_copy_refuses_a_foreign_directory_and_dry_run_writes_nothing(tmp_path):
    foreign = tmp_path / "diagram_bot"
    foreign.mkdir()
    (foreign / "mine.py").write_text("")
    assert copy(0, foreign) == 2
    assert copy(2, tmp_path / "dry", "--dry-run", "--static-dest", str(tmp_path / "web")) == 0
    assert not (tmp_path / "dry").exists() and not (tmp_path / "web").exists()


def test_a_copy_missing_a_closure_file_fails(tmp_path):
    """The porting-guide §3 Level 3 list without routes/standards.py (routes/documents.py imports it) cannot pass."""
    package = tmp_path / "diagram_bot"
    assert copy(3, package) == 0
    (package / "routes" / "standards.py").unlink()
    result = subprocess.run([sys.executable, "-I", "-c", ISOLATED, str(tmp_path), "3"], capture_output=True, text=True, timeout=120,
                            env={k: v for k, v in os.environ.items() if k != "PYTHONPATH"}, cwd=tmp_path)
    assert result.returncode == 1 and "FAIL" in result.stdout and "routes.standards" in result.stdout


CARDS = sorted((ROOT / "docs" / "porting").glob("T[0-9]-*.md"))
SECTIONS = ("담당·예산", "목표", "읽을 것", "하지 말 것", "단계", "완료 조건", "멈춤 조건", "보고")


def section(text, name):
    match = re.search(rf"^## {re.escape(name)}\n(.*?)(?=^## |\Z)", text, re.S | re.M)
    assert match, name
    return match.group(1)


def test_cards_have_every_section_and_stay_short():
    assert [c.name.split("-")[0] for c in CARDS] == [f"T{i}" for i in range(7)]
    for card in CARDS:
        text = card.read_text()
        for name in SECTIONS:
            section(text, name)
        assert "150K" in section(text, "담당·예산"), card.name
        assert len(text) / 3 <= 1200, card.name  # hard cap, chars/3 estimate
    assert len((ROOT / "docs/porting/RULES.md").read_text()) / 3 <= 700
    assert len((ROOT / "docs/porting/ORCHESTRATOR.md").read_text()) / 3 <= 800


def test_card_reading_lists_name_existing_files_or_company_placeholders():
    for card in CARDS:
        listed = re.sub(r"```.*?```", "", section(card.read_text(), "읽을 것"), flags=re.S)  # the cheat sheet is not a reading list
        for token in re.findall(r"`([^`\n]+)`", listed):
            if re.fullmatch(r"[\w./-]+\.(md|py|js|json|html)", token):
                assert (ROOT / token).is_file(), f"{card.name}: {token}"
            elif "/" in token and not token.startswith("--"):
                assert token.startswith("<"), f"{card.name}: {token}"


def test_done_commands_use_existing_port_check_levels():
    for card in CARDS:
        levels = re.findall(r"port_check --level (\S+?)`", section(card.read_text(), "완료 조건"))
        assert levels, card.name
        assert all(lv == "<N>" or int(lv) in port_check.LEVELS for lv in levels), (card.name, levels)


def test_readme_and_orchestrator_list_every_card_with_only_luna_and_sol():
    readme, orchestrator = (ROOT / "docs/porting/README.md").read_text(), (ROOT / "docs/porting/ORCHESTRATOR.md").read_text()
    rows = {line.split("|")[1].strip(): line for line in readme.splitlines() if line.startswith("| ")}
    for card in CARDS:
        card_id = card.name.split("-")[0]
        assert card_id in rows and f"({card.name})" in readme and card_id in orchestrator, card.name
        owner = rows[card_id].split("|")[3].strip()
        assert owner in ("GPT-6 Luna", "GPT-6.1 Sol", "셸(ORCH)"), (card_id, owner)
        assert owner == "셸(ORCH)" or owner in card.read_text(), card.name
    assert rows["ORCH"].split("|")[3].strip() == "GPT-6.1 Sol"
    caps = [int(row.split("|")[5].split()[0]) for key, row in rows.items() if key not in ("카드", "---")]
    assert sum(caps) == 100, caps
    assert not any("Sonnet" in p.read_text() for p in (ROOT / "docs" / "porting").glob("*.md"))


def test_card_section_references_exist():
    """`page.md` §A, §B: each name starts a `## ` heading of that page (RULES.md reads it with sed '/^## A/,/^## /p')."""
    for card in CARDS:
        for path, rest in re.findall(r"^- `([\w./-]+\.md)` (§.*)$", section(card.read_text(), "읽을 것"), re.M):
            headings = [h[3:] for h in (ROOT / path).read_text().splitlines() if h.startswith("## ")]
            for ref in re.findall(r"§(.+?)(?=, §| \(~|$)", rest):
                assert any(h.startswith(ref) for h in headings), f"{card.name}: {path} §{ref}"
