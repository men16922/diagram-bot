"""Company standards an admin adopts survive a restart, are listed for every role, are deleted by an admin only, and their
names resolve as `spec` values without reading any file."""
import json

import pytest
from fastapi.testclient import TestClient

from diagram_bot import designer, drawio_spec
from diagram_bot.auth import hash_password
from diagram_bot.drawio_spec import DEFAULT_SPEC, get_default_spec, request_spec, set_default_spec
from diagram_bot.errors import InputError
from diagram_bot.server import create_app
from test_xml_robustness import MXCELL_TEMPLATE


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """Standards and the default spec are process-wide; each test starts and ends without any."""
    monkeypatch.setattr(designer, "COMPANY_STANDARDS", {})
    monkeypatch.setattr(drawio_spec, "COMPANY_SPECS", {})
    set_default_spec(None)
    yield
    set_default_spec(None)


def local(directory):
    client = TestClient(create_app(directory))
    client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
    return client


def restart(directory):
    """A new process: nothing in memory, only what the data directory holds."""
    designer.COMPANY_STANDARDS.clear()
    drawio_spec.COMPANY_SPECS.clear()
    set_default_spec(None)
    return local(directory)


def company(client):
    return {row["name"]: row for row in client.get("/api/standards").json() if row["company"]}


def test_an_adopted_default_standard_survives_a_restart(tmp_path):
    data = tmp_path / "data"
    client = local(data)
    res = client.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": "corp-web", "title": "사내 웹 표준", "make_default": True})
    assert res.status_code == 200 and res.json()["default"] is True
    sid, spec = res.json()["id"], res.json()["spec"]
    stored = json.loads((data / "standards" / "corp-web.json").read_text(encoding="utf-8"))
    assert stored["name"] == "corp-web" and stored["title"] == "사내 웹 표준" and stored["created_at"] and stored["default"] is True
    assert stored["adopted_by"] == "local" and stored["snapshot_id"] == sid and stored["spec"] == spec and stored["snapshot"]["raw"]["vpcs"]

    client = restart(data)
    row = company(client)["corp-web"]
    assert row["title"] == "사내 웹 표준" and row["default"] is True and row["snapshot_id"] == sid and row["spec"] is True
    assert row["created_at"] == stored["created_at"] and row["adopted_by"] == "local"
    builtin = {r["name"]: r for r in client.get("/api/standards").json() if not r["company"]}
    assert builtin["three-tier"]["snapshot_id"] == "sample-three-tier" and client.get("/api/snapshots/sample-three-tier").status_code == 200
    # The default spec, the Design standard and the spec name are back.
    assert get_default_spec().to_dict() == spec and get_default_spec().cell_wrapper == "mxCell"
    assert designer.Design().apply_standard("corp-web")["standard"] == "corp-web"
    assert request_spec("corp-web").to_dict() == spec
    preview = client.get("/api/snapshots/sample-three-tier/document", params={"spec": "corp-web"}).json()
    assert preview["preview"] is True and preview["revision"] is None and "<object" not in preview["xml"]
    # Documents the app edits keep the wrapper the editor selects by, whatever the default exports.
    assert "resourceId=" in client.get("/api/snapshots/sample-three-tier/document").json()["xml"]


def test_a_new_default_takes_the_flag_and_names_never_read_files(tmp_path):
    data = tmp_path / "data"
    client = local(data)
    for name in ("corp-a", "corp-b"):
        assert client.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": name, "make_default": True}).status_code == 200
    assert {n: r["default"] for n, r in company(client).items()} == {"corp-a": False, "corp-b": True}
    client = restart(data)
    assert {n: r["default"] for n, r in company(client).items()} == {"corp-a": False, "corp-b": True}
    # A preset or built-in name cannot be adopted; a file path is still never a spec.
    for name in ("clean", "three-tier"):
        assert client.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": name, "make_default": True}).status_code == 422
    (tmp_path / "corp-c").write_text(json.dumps({"cell_wrapper": "mxCell"}), encoding="utf-8")
    for value in (str(tmp_path / "corp-c"), "corp-c", "../standards/corp-b.json"):
        with pytest.raises(InputError):
            request_spec(value)
    assert client.get("/api/snapshots/demo/document", params={"spec": "corp-c"}).status_code == 422
    # A standard file that no longer validates is skipped at startup, not fatal.
    (data / "standards" / "broken.json").write_text("{", encoding="utf-8")
    assert set(company(restart(data))) == {"corp-a", "corp-b"}


def test_roles_and_deletion_in_team_mode(tmp_path):
    data = tmp_path / "data"
    users = {name: {"name": name, "password_hash": hash_password("pw-" + name), "role": name} for name in ("viewer", "editor", "admin")}
    app = create_app(data, users=users)

    def login(name):
        c = TestClient(app)
        assert c.post("/api/login", json={"name": name, "password": "pw-" + name}).status_code == 200
        c.headers["X-Diagram-Bot"] = c.get("/api/bootstrap").json()["token"]
        return c
    viewer, editor, admin = login("viewer"), login("editor"), login("admin")
    assert editor.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": "corp-web", "make_default": True}).status_code == 403
    assert admin.post("/api/xml/adopt", json={"xml": MXCELL_TEMPLATE, "name": "corp-web", "make_default": True}).status_code == 200
    for client in (viewer, editor, admin):
        assert company(client)["corp-web"]["adopted_by"] == "admin"
    for client in (viewer, editor):
        assert client.delete("/api/standards/corp-web").status_code == 403
    audit = [json.loads(line) for line in (data / "audit.log").read_text().splitlines()]
    assert {(e["user"], e["result"]) for e in audit if e["action"] == "DELETE /api/standards/corp-web"} == {("viewer", "denied"), ("editor", "denied")}
    assert "corp-web" in designer.COMPANY_STANDARDS and get_default_spec().cell_wrapper == "mxCell"

    assert admin.delete("/api/standards/three-tier").status_code == 422
    assert admin.delete("/api/standards/nothing-here").status_code == 404
    res = admin.delete("/api/standards/corp-web")
    assert res.status_code == 200 and res.json() == {"deleted": "corp-web", "default_cleared": True}
    assert company(viewer) == {} and not (data / "standards" / "corp-web.json").exists()
    assert get_default_spec() is DEFAULT_SPEC and "corp-web" not in designer.COMPANY_STANDARDS
    assert viewer.get("/api/snapshots/demo/document", params={"spec": "corp-web"}).status_code == 422
    assert admin.delete("/api/standards/corp-web").status_code == 404
