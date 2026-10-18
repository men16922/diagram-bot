"""draw.io documents of a snapshot view: read (generated on first use), save, relayout, restore, and XML align/import/adopt."""
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..drawio import carry_annotations, generate_document, validate_document
from ..drawio_spec import get_default_spec, request_spec
from ..validation import REGION
from ..views import view_graph
from . import role
from ..xml_align import adopt_company_xml, align_drawio_xml, import_drawio_xml, inspect_drawio_xml


class DocumentRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    revision: int = Field(ge=0)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=0)


class RestoreRequest(RevisionRequest):
    from_revision: int = Field(ge=0)


class XmlAlignRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    mode: Literal["preserve", "rebuild"] = "preserve"
    spec: str | dict | None = Field(default=None, description="preset name or inline spec object; never a path")
    reparent: bool = True
    route_edges: bool = True
    normalize_styles: bool = False


class XmlImportRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    title: str | None = Field(default=None, max_length=200)
    region: str = Field(default="ap-northeast-2", pattern=REGION, max_length=50)
    save: bool = True


class XmlAdoptRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    name: str = Field(default="company-standard", max_length=60)
    title: str | None = Field(default=None, max_length=200)
    region: str = Field(default="ap-northeast-2", pattern=REGION, max_length=50)
    make_default: bool = False  # admin only: server-wide default spec and a registered company standard
    save: bool = True


def view_scope(snapshot, view, vpc, resource, references, routes=None):
    """(view kind, vpc, resource, references, routes) as the document key, and the view selection."""
    try:
        selection = view_graph(snapshot["graph"], view, vpc, resource, references, routes)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    v = selection["view"]
    return (v["kind"], v["vpc"], v["resource"], v["references"], v["routes"]), selection


def describe(document, selection, history):
    routes = selection["routes"] and {k: v for k, v in selection["routes"].items() if k != "path"}
    return {**document, "view": selection["view"], "counts": selection["counts"], "visible": selection["nodes"], "routes": routes,
            "history": [{k: h[k] for k in ("revision", "saved_at", "reason")} for h in history]}


def generate(snapshot, scope, spec=None, editor=True):
    """XML of a view. Documents the app stores and edits keep the wrapper attributes the editor selects by (for_editor);
    an explicit spec preview is the export as the company spec writes it."""
    cfg = request_spec(spec) or get_default_spec()
    return generate_document(snapshot, scope[1], scope[3], scope[0], scope[2], scope[4], spec=cfg.for_editor() if editor else cfg)


def router(context):
    api = APIRouter()
    snapshots, documents = context.snapshots, context.documents

    @api.get("/api/snapshots/{identifier}/document")
    def read_document(identifier: str, view: str | None = None, vpc: str | None = None, resource: str | None = None,
                      references: bool = False, reset: bool = False, routes: str | None = None, spec: str | None = None):
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        saved, history = documents.read(identifier, scope)
        if saved and not reset and not spec:
            return describe(saved, selection, history)
        return describe({"xml": generate(snapshot, scope, spec=spec), "revision": saved["revision"] if saved else 0, "saved_at": None}, selection, history)

    @api.put("/api/snapshots/{identifier}/document")
    def save_document(identifier: str, document: DocumentRequest, view: str | None = None, vpc: str | None = None,
                      resource: str | None = None, references: bool = False, routes: str | None = None):
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        validate_document(document.xml)  # InputError: 422
        saved, history = documents.replace(identifier, scope, document.revision, document.xml)
        return describe(saved, selection, history)

    @api.post("/api/snapshots/{identifier}/document/relayout")
    def relayout_document(identifier: str, request: RevisionRequest, view: str | None = None, vpc: str | None = None,
                          resource: str | None = None, references: bool = False, routes: str | None = None, spec: str | None = None):
        """Explicit automatic layout: archive the current edit, regenerate, keep user-added notes."""
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        saved, history = documents.replace(identifier, scope, request.revision, generate(snapshot, scope, spec=spec), reason="자동 재배치 전",
                                           merge=carry_annotations)
        return describe(saved, selection, history)

    @api.post("/api/snapshots/{identifier}/document/restore")
    def restore_document(identifier: str, request: RestoreRequest, view: str | None = None, vpc: str | None = None,
                         resource: str | None = None, references: bool = False, routes: str | None = None):
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        saved, history = documents.replace(identifier, scope, request.revision, source_revision=request.from_revision)
        return describe(saved, selection, history)

    @api.post("/api/xml/align")
    def align_xml_endpoint(payload: XmlAlignRequest):
        """Auto-align, resize containers, and orthogonally route an arbitrary draw.io XML document (InputError: 422)."""
        before = inspect_drawio_xml(payload.xml)
        aligned = align_drawio_xml(payload.xml, mode=payload.mode, spec=request_spec(payload.spec), reparent=payload.reparent,
                                   route_edges=payload.route_edges, normalize_styles=payload.normalize_styles)
        after = inspect_drawio_xml(aligned)
        return {"xml": aligned, "before": before, "after": after}

    @api.post("/api/xml/import")
    def import_xml_endpoint(payload: XmlImportRequest):
        """Import an arbitrary AWS draw.io XML document as a Diagram Bot Snapshot (editor, like any snapshot import)."""
        snapshot = import_drawio_xml(payload.xml, title=payload.title, region=payload.region)
        return {"id": snapshots.save(snapshot) if payload.save else None, "snapshot": snapshot}

    @api.post("/api/xml/adopt")
    def adopt_xml_endpoint(payload: XmlAdoptRequest, request: Request):
        """Extract a company template's DrawioSpec, align it and import it, for this request only (editor).

        make_default (admin, like collection): the spec becomes this process's default and the template a Design standard."""
        if payload.make_default and role(request) != "admin":
            raise HTTPException(403, "서버 기본 규격 변경(make_default)은 admin 역할만 할 수 있습니다.")
        result = adopt_company_xml(payload.xml, name=payload.name, title=payload.title, region=payload.region,
                                   set_default=payload.make_default, register=payload.make_default, allow_path=False)
        identifier = snapshots.save(result["snapshot"]) if payload.save else None
        return {
            "id": identifier,
            "name": result["name"],
            "spec": result["spec"].to_dict(),
            "default": payload.make_default,
            "aligned_xml": result["aligned_xml"],
            "standardized_xml": result["standardized_xml"],
            "before": result["before"],
            "after": result["after"],
        }

    return api
