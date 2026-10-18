"""Standard architectures: the built-in ones and the company standards an admin adopted (POST /api/xml/adopt with
make_default). Adopted standards live in a StandardStore, so a restart brings back each Design standard
(apply_standard_architecture), its spec name (`?spec=<name>`) and the default spec."""
import copy
import logging

from fastapi import APIRouter, HTTPException, Request

from .. import designer, drawio_spec
from ..collector import now
from ..designer import STANDARDS, register_standard
from ..drawio_spec import DrawioSpec, register_spec, set_default_spec
from ..errors import InputError
from . import role


def activate(record):
    """Register a stored standard in this process: Design standard, spec name and, when it is the default, the default spec."""
    spec = DrawioSpec(**record["spec"])  # the stored to_dict(); validated again on construction
    snapshot = record["snapshot"]
    register_standard(record["name"], lambda: copy.deepcopy(snapshot), description=record.get("description"))
    register_spec(record["name"], spec)
    if record.get("default"):
        set_default_spec(spec)
    return spec


def restore(standards):
    """At startup: every stored standard; one that no longer validates is skipped and logged."""
    for record in standards.list():
        try:
            activate(record)
        except (InputError, LookupError, TypeError, ValueError):
            logging.getLogger(__name__).warning("Skipping invalid company standard %s", record.get("name"), exc_info=True)


def remember(standards, result, title, snapshot_id, user):
    """Persist an admin's make_default adoption (adopt_company_xml already registered it and set the default spec)."""
    name = result["name"]
    register_spec(name, result["spec"])
    return standards.save({"name": name, "title": title or name, "description": title or f"사내 표준 아키텍처 ({name})",
                           "created_at": now(), "default": True, "adopted_by": user, "snapshot_id": snapshot_id,
                           "spec": result["spec"].to_dict(), "snapshot": result["snapshot"]})


def router(context):
    api = APIRouter()
    standards = context.standards

    @api.get("/api/standards")
    def list_standards():
        """Built-in standards (each opens its sample snapshot) and the company standards registered in this process."""
        stored = {record["name"]: record for record in standards.list()} if standards else {}
        rows = [{"name": name, "title": name, "description": summary, "created_at": None, "default": False,
                 "snapshot_id": f"sample-{name}", "company": False, "spec": False, "adopted_by": None} for name, summary in STANDARDS.items()]
        for name, (description, _) in designer.COMPANY_STANDARDS.items():
            record = stored.get(name, {})
            rows.append({"name": name, "title": record.get("title", name), "description": description, "created_at": record.get("created_at"),
                         "default": bool(record.get("default")), "snapshot_id": record.get("snapshot_id"), "company": True,
                         "spec": name in drawio_spec.COMPANY_SPECS, "adopted_by": record.get("adopted_by")})
        return rows

    @api.delete("/api/standards/{name}")
    def delete_standard(name: str, request: Request):
        """admin (auth.allowed): unregister an adopted standard; deleting the default restores the built-in default spec."""
        if role(request) != "admin":
            raise HTTPException(403, "사내 표준 삭제는 admin 역할만 할 수 있습니다.")
        if name in STANDARDS:
            raise HTTPException(422, "기본 표준 아키텍처는 삭제할 수 없습니다.")
        if not standards:
            raise HTTPException(404, "사내 표준을 찾을 수 없습니다.")
        record = standards.delete(name)  # NotFound: 404
        designer.COMPANY_STANDARDS.pop(name, None)
        drawio_spec.COMPANY_SPECS.pop(name, None)
        if record.get("default"):
            set_default_spec(None)
        return {"deleted": name, "default_cleared": bool(record.get("default"))}

    return api
