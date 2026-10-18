"""Regenerate docs/wiki/index.md and the root llms.txt from the wiki pages' front matter (title, summary).

    python scripts/wiki_index.py          # write both files
    python scripts/wiki_index.py --check  # exit 1 if either is out of date (tests/test_labels_and_wiki.py runs this check)
"""
import argparse
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
WIKI = ROOT / "docs" / "wiki"
GROUPS = [
    ("시작", ["architecture", "porting-guide", "glossary"]),
    ("데이터와 모델", ["snapshot-model", "collection", "graph", "views", "layout-and-drawio", "samples"]),
    ("분석과 출력", ["review-rules", "change-review", "multi-account", "cloudformation"]),
    ("AWS 접근과 인증", ["aws-access-and-sso", "browser-egress", "team-mode-security"]),
    ("앱과 API", ["http-api", "web-app", "storage", "chat-agent", "cli"]),
    ("작업 방법", ["howto-add-collected-service", "howto-add-review-rule", "howto-add-chat-tool"]),
    ("위키 관리", ["SCHEMA", "log"]),
]


def front_matter(path):
    text = path.read_text(encoding="utf-8")
    match = re.match(r"^---\n(.*?)\n---\n", text, re.S)
    if not match:
        raise ValueError(f"{path.name}: front matter가 없습니다")
    fields = {}
    for line in match.group(1).splitlines():
        key, _, value = line.partition(":")
        fields[key.strip()] = value.strip()
    return fields


def pages():
    return {p.stem: front_matter(p) for p in sorted(WIKI.glob("*.md")) if p.stem != "index"}


def render():
    found = pages()
    listed = [name for _, names in GROUPS for name in names]
    missing = sorted(set(found) - set(listed))
    if missing or set(listed) - set(found):
        raise ValueError(f"GROUPS와 페이지가 다릅니다: 목록에 없음 {missing}, 파일 없음 {sorted(set(listed) - set(found))}")
    index = ["---", "title: 위키 색인", "summary: Diagram Bot 위키의 모든 페이지와 한 줄 요약. 처음 읽는 사람과 LLM 에이전트의 시작점.",
             "sources: []", "related: [architecture, porting-guide, SCHEMA, log]", "updated: 2026-10-18", "---",
             "# Diagram Bot 위키", "",
             "> AWS 계정을 읽기 전용으로 수집해 draw.io 구성도·검토·변경 비교·CloudFormation 초안을 만드는 도구의 지식 베이스.",
             "> 한 페이지 한 주제, 모든 사실은 코드 근거(`sources`)와 함께. 이 파일은 `python scripts/wiki_index.py`가 만든다.", "",
             "처음이면 [[architecture]] → [[snapshot-model]] → 하려는 일의 페이지 순서로 읽는다. 회사 스택으로 옮기려면 [[porting-guide]].", ""]
    llms = ["# Diagram Bot", "",
            "> Read-only AWS collector that turns an account into a snapshot (raw API responses + relation graph) and renders",
            "> draw.io architecture diagrams, Well-Architected style review findings, change review, multi-account views,",
            "> CloudFormation drafts and chat-driven designs. Python (boto3, FastAPI), buildless ES-module frontend, Strands +",
            "> Amazon Bedrock agent. Supports browser egress: the server signs AWS requests and the user's browser sends them.", "",
            "The wiki (Korean, one topic per page, code paths as sources) is the primary reference for agents and porting.", ""]
    for group, names in GROUPS:
        index += [f"## {group}", ""]
        llms += [f"## {group}", ""]
        for name in names:
            meta = found[name]
            index.append(f"- [[{name}]] ({name}.md) — **{meta['title']}**: {meta['summary']}")
            llms.append(f"- [{meta['title']}](docs/wiki/{name}.md): {meta['summary']}")
        index.append("")
        llms.append("")
    llms += ["## Optional", "", "- [README](README.md): 설치·실행·기능 요약", "- [CHANGELOG](CHANGELOG.md): 변경 이력",
             "- [ARCHITECTURE](docs/ARCHITECTURE.md): 샘플의 AWS 모범 사례 근거", "- [STATUS](docs/STATUS.md): 구현 상태와 검증 근거", ""]
    return "\n".join(index), "\n".join(llms)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    index, llms = render()
    targets = {WIKI / "index.md": index, ROOT / "llms.txt": llms}
    if args.check:
        stale = [str(p.relative_to(ROOT)) for p, text in targets.items() if not p.exists() or p.read_text(encoding="utf-8") != text]
        if stale:
            print(f"오래된 파일: {', '.join(stale)} — python scripts/wiki_index.py 를 실행하세요.", file=sys.stderr)
            return 1
        return 0
    for path, text in targets.items():
        path.write_text(text, encoding="utf-8")
        print(f"wrote {path.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
