"""The UI names every node kind and relation the graph produces, and the wiki stays navigable and grounded in code."""
import json
import re
import subprocess
import sys
from pathlib import Path

from diagram_bot.demo import demo_snapshot
from diagram_bot.graph import build_graph
from diagram_bot.samples import SAMPLES
from fixtures import full_design

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "docs" / "wiki"


def labels(name):
    text = (ROOT / "src/diagram_bot/static/js/labels.js").read_text(encoding="utf-8")
    body = re.search(rf"export const {name} = \{{(.*?)\}};", text, re.S).group(1)
    return set(re.findall(r"(\w+):'", body))


def test_every_kind_and_relation_has_a_display_name():
    graphs = [build_graph(build()) for build in SAMPLES.values()] + [build_graph(demo_snapshot()), build_graph(full_design().snapshot())]
    kinds = {n["kind"] for g in graphs for n in g["nodes"]}
    relations = {e["relation"] for g in graphs for e in g["edges"]}
    assert kinds - labels("names") == set()
    assert relations - labels("relationNames") == set()


def pages():
    return {p.stem: p.read_text(encoding="utf-8") for p in WIKI.glob("*.md")}


def test_wiki_pages_have_front_matter_real_sources_and_working_links():
    found = pages()
    assert len(found) >= 20
    for name, text in found.items():
        head = re.match(r"^---\n(.*?)\n---\n", text, re.S)
        assert head, name
        fields = dict(line.split(":", 1) for line in head.group(1).splitlines())
        assert {"title", "summary", "sources", "related", "updated"} <= set(fields), name
        for source in re.findall(r"[\w./-]+", fields["sources"].strip(" []")):
            assert (ROOT / source).exists(), f"{name}: 근거 파일 없음 {source}"
        prose = re.sub(r"`[^`\n]*`", "", re.sub(r"```.*?```", "", text, flags=re.S))  # examples in code are not links
        linked = set(re.findall(r"\[\[([\w-]+)\]\]", prose)) | {r.strip() for r in fields["related"].strip(" []").split(",") if r.strip()}
        assert linked <= set(found), f"{name}: 없는 페이지 링크 {sorted(linked - set(found))}"


def test_wiki_index_and_llms_txt_are_current():
    result = subprocess.run([sys.executable, str(ROOT / "scripts/wiki_index.py"), "--check"], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.dumps(sorted(pages())).count("index") == 1
