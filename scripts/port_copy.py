"""Copy one porting Level of diagram_bot into another repository: the modules that Level imports, nothing else.

    python scripts/port_copy.py --level N --dest <company>/path/diagram_bot [--static-dest <dir>] [--dry-run] [--force]

The closure is computed statically: from the Level's entry modules (docs/wiki/porting-guide.md §3) follow every
relative import (module level and lazy) by AST, with the __init__ files of each package. port_check.py is added so the
copy can check itself (python -m diagram_bot.port_check --level N). MANIFEST.json records each file's sha256, the Level
and the source commit; pip requirements come from pyproject.toml for the closure's third-party imports.
"""
import argparse
import ast
import hashlib
import json
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = ROOT / "src" / "diagram_bot"
# Entry modules each Level adds (porting-guide §3, "진입점"); a Level includes every lower one.
ENTRIES = {
    0: ["xml_align", "drawio_spec"],
    1: ["designer", "drawio", "findings", "iac", "diff", "combine"],
    2: ["collector", "sessions", "egress", "aws_access", "sso", "expiry", "store", "api"],
    3: ["auth", "routes", "routes.snapshots", "routes.documents", "routes.aws"],
    4: ["agent", "routes.agent"],
}
STATIC = {2: ["js/relay.js"], 4: ["js/http.js", "js/labels.js", "editor-bridge.js"]}  # under src/diagram_bot/static
CHECK = "port_check"  # copied, not followed: its checks import their Level's modules lazily
# Third-party top-level module -> distribution in pyproject.toml.
DISTRIBUTIONS = {"boto3": "boto3", "botocore": "boto3", "defusedxml": "defusedxml", "fastapi": "fastapi", "pydantic": "fastapi",
                 "starlette": "fastapi", "strands": "strands-agents"}
CHECK_EXTRAS = {3: ["httpx2"]}  # what port_check itself needs from the test extra from this Level on (Starlette TestClient)


def module_file(name):
    path = PACKAGE.joinpath(*name.split("."))
    return path / "__init__.py" if path.is_dir() else path.with_suffix(".py")


def is_module(name):
    return module_file(name).is_file()


def imports(name):
    """(relative modules, {third-party top-level: lazy?}) imported anywhere in one module."""
    path = module_file(name)
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = name.split(".") if path.name == "__init__.py" else name.split(".")[:-1]
    lazy = {id(n) for f in ast.walk(tree) if isinstance(f, (ast.FunctionDef, ast.AsyncFunctionDef)) for n in ast.walk(f)}
    local, external = set(), {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level:
            base = package[:len(package) - node.level + 1]
            target = base + (node.module.split(".") if node.module else [])
            if target:
                local.add(".".join(target))
            local |= {".".join(target + [a.name]) for a in node.names if is_module(".".join(target + [a.name]))}
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for top in ([a.name.split(".")[0] for a in node.names] if isinstance(node, ast.Import) else [node.module.split(".")[0]]):
                if top not in sys.stdlib_module_names and top != "diagram_bot":
                    external[top] = external.get(top, True) and id(node) in lazy
    return local, external


def closure(level):
    """(sorted module names, {third-party: lazy only?}) of a Level."""
    seen, external = set(), {}
    todo = [m for lv in range(level + 1) for m in ENTRIES[lv]]
    while todo:
        name = todo.pop()
        parts = name.split(".")
        for parent in (".".join(parts[:i]) for i in range(1, len(parts))):  # importing a.b runs a/__init__.py
            todo.append(parent)
        if name in seen:
            continue
        if not is_module(name):
            raise SystemExit(f"{name}: no such module under {PACKAGE}")
        seen.add(name)
        local, ext = imports(name)
        todo += local
        for top, lazy in ext.items():
            external[top] = external.get(top, True) and lazy
    return sorted(seen | {CHECK}), external


def files(level):
    """Package-relative source files of a Level: modules, the package __init__ and viewer.html when report.py is in."""
    modules = closure(level)[0]
    paths = {"__init__.py", *(module_file(m).relative_to(PACKAGE).as_posix() for m in modules)}
    if "report" in modules:
        paths.add("viewer.html")
    return sorted(paths)


def static_files(level):
    return [f for lv in range(level + 1) for f in STATIC.get(lv, [])]


def requirements(level):
    """(required, optional) pip specifiers: optional when a package is imported only inside functions (strands)."""
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    specs = project["dependencies"] + [s for group in project["optional-dependencies"].values() for s in group]
    spec = lambda dist: next(s for s in specs if s.split(">")[0].split("<")[0].split("=")[0].split("[")[0].strip() == dist)
    external = closure(level)[1]
    unknown = sorted(set(external) - set(DISTRIBUTIONS))
    if unknown:
        raise SystemExit(f"third-party imports without a pyproject mapping: {unknown}")
    required = {DISTRIBUTIONS[t] for t, lazy in external.items() if not lazy}
    optional = {DISTRIBUTIONS[t] for t, lazy in external.items() if lazy} - required
    check = {d for lv, dists in CHECK_EXTRAS.items() if level >= lv for d in dists}
    return [spec(d) for d in sorted(required)], [spec(d) for d in sorted(optional)], [spec(d) for d in sorted(check)]


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def source_commit():
    """(commit, dirty); "unknown" when there is no git or no checkout (a zip download)."""
    def git(*args):
        try:
            return subprocess.run(["git", "-C", str(ROOT), *args], capture_output=True, text=True).stdout.strip()
        except OSError:
            return ""
    commit = git("rev-parse", "HEAD") or "unknown"
    return commit, commit != "unknown" and bool(git("status", "--porcelain", "--", "src/diagram_bot"))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--level", type=int, choices=sorted(ENTRIES), required=True)
    parser.add_argument("--dest", type=Path, required=True, help="the company's diagram_bot package directory (keep the name)")
    parser.add_argument("--static-dest", type=Path, help="where the frontend JS goes (Level 2: relay.js; Level 4: http.js, labels.js, editor-bridge.js)")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true", help="overwrite a copy made from another commit")
    args = parser.parse_args(argv)
    commit, dirty = source_commit()
    sources, statics = files(args.level), static_files(args.level)
    chars = sum(len((PACKAGE / f).read_text(encoding="utf-8")) for f in sources)
    required, optional, check = requirements(args.level)

    manifest_path = args.dest / "MANIFEST.json"
    if manifest_path.exists():
        previous = json.loads(manifest_path.read_text(encoding="utf-8"))
        if previous.get("commit") != commit and not args.force:
            print(f"refusing: {manifest_path} is from commit {previous.get('commit')}, this is {commit} (--force overwrites)", file=sys.stderr)
            return 2
        stale = sorted(set(previous.get("files", {})) - set(sources))
        if stale:
            print(f"note: files of the previous copy not in level {args.level}: {', '.join(stale)}")
    elif args.dest.exists() and any(args.dest.iterdir()) and not args.force:
        print(f"refusing: {args.dest} has files but no MANIFEST.json (--force overwrites)", file=sys.stderr)
        return 2

    print(f"level {args.level}: {len(sources)} files, {chars:,} chars (~{chars // 3:,} tokens, chars/3 estimate), commit {commit[:12]}{' (dirty)' if dirty else ''}")
    for f in sources:
        print(f"  {f}")
    for f in statics:
        print(f"  static/{f} -> {args.static_dest / f if args.static_dest else '(skipped: no --static-dest)'}")
    print("pip install " + " ".join(f"'{s}'" for s in required))
    if optional:
        print("optional (imported lazily): pip install " + " ".join(f"'{s}'" for s in optional))
    if check:
        print("port_check from this level also needs: pip install " + " ".join(f"'{s}'" for s in check))
    if args.dry_run:
        return 0

    for f in sources:
        target = args.dest / f
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(PACKAGE / f, target)
    copied_static = {}
    if args.static_dest:
        for f in statics:
            target = args.static_dest / f
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(PACKAGE / "static" / f, target)
            copied_static[f] = sha256(target)
    manifest = {"source": "diagram-bot", "commit": commit, "dirty": dirty, "level": args.level,
                "files": {f: sha256(args.dest / f) for f in sources}, "static": copied_static,
                "requirements": required, "optional": optional, "check_requirements": check}
    manifest_path.write_text(json.dumps(manifest, indent=1) + "\n", encoding="utf-8")
    print(f"wrote {manifest_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
