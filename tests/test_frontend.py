"""Static checks for the buildless frontend (no Node in the gate): module imports resolve to real exports, every
literal element ID exists in app.html, the modules are served, and the standalone viewer names every node kind."""
import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from diagram_bot.server import create_app

ROOT = Path(__file__).resolve().parent.parent
STATIC = ROOT / "src/diagram_bot/static"
MODULES = sorted([STATIC / "app.js", *(STATIC / "js").glob("*.js")])
IMPORT = re.compile(r"^import\s*(?:\{([^}]*)\}\s*from\s*)?'([^']+)';", re.M)


def object_keys(text, name):
    body = re.search(rf"const {name}\s*=\s*\{{(.*?)\}};", text, re.S).group(1)
    return set(re.findall(r"(\w+):'", body))


def exported(text):
    names = set(re.findall(r"^export\s+(?:async\s+)?(?:function|const|let|class)\s+([\w$]+)", text, re.M))
    for group in re.findall(r"^export\s+(?:const|let)\s*\{([^}]*)\}\s*=", text, re.M) + re.findall(r"^export\s*\{([^}]*)\}", text, re.M):
        names |= {part.split(":")[-1].split(" as ")[-1].strip() for part in group.split(",") if part.strip()}
    return names


def test_every_module_import_resolves_to_an_export():
    for module in MODULES:
        for names, source in IMPORT.findall(module.read_text(encoding="utf-8")):
            target = (module.parent / source).resolve()
            assert target.is_file() and target.is_relative_to(STATIC), f"{module.name}: {source}"
            wanted = {part.split(" as ")[0].strip() for part in names.split(",") if part.strip()}
            missing = wanted - exported(target.read_text(encoding="utf-8"))
            assert not missing, f"{module.name} imports {sorted(missing)} from {source}"


def test_literal_element_ids_exist_in_app_html():
    html = (STATIC / "app.html").read_text(encoding="utf-8")
    ids = set(re.findall(r'\bid="([^"]+)"', html))
    for module in MODULES:
        used = set(re.findall(r"\$\('([\w-]+)'\)", module.read_text(encoding="utf-8")))
        assert used <= ids, f"{module.name}: {sorted(used - ids)}"


@pytest.mark.parametrize("module", MODULES, ids=lambda m: m.name)
def test_modules_are_served(tmp_path, module):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor")) as client:
        response = client.get("/static/" + module.relative_to(STATIC).as_posix())
    assert response.status_code == 200
    assert "javascript" in response.headers["content-type"]


def test_viewer_names_every_kind():
    labels = object_keys((STATIC / "js/labels.js").read_text(encoding="utf-8"), "names")
    viewer = object_keys((ROOT / "src/diagram_bot/viewer.html").read_text(encoding="utf-8"), "names")
    assert labels - viewer == set()
