"""draw.io documents of a snapshot view: read (generated on first use), save, relayout, restore, and XML align/import/adopt."""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from ..drawio import carry_annotations, generate_document, validate_document
from ..drawio_spec import request_spec
from ..views import view_graph
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
    mode: str = "preserve"
    spec: str | dict | None = Field(default=None, description="preset name or inline spec object; never a path")
    reparent: bool = True
    route_edges: bool = True
    normalize_styles: bool = False


class XmlImportRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    title: str | None = None
    region: str = "ap-northeast-2"
    save: bool = True


class XmlAdoptRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    name: str = "company-standard"
    title: str | None = None
    region: str = "ap-northeast-2"
    set_default: bool = True
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


def generate(snapshot, scope, spec=None):
    return generate_document(snapshot, scope[1], scope[3], scope[0], scope[2], scope[4], spec=request_spec(spec))


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
        try:
            validate_document(document.xml)
        except Exception as exc:
            raise HTTPException(422, "유효한 압축 해제 draw.io XML이 아닙니다.") from exc
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
        """Auto-align, resize containers, and orthogonally route an arbitrary draw.io XML document."""
        try:
            before = inspect_drawio_xml(payload.xml)
            aligned = align_drawio_xml(
                payload.xml,
                mode=payload.mode,
                spec=request_spec(payload.spec),
                reparent=payload.reparent,
                route_edges=payload.route_edges,
                normalize_styles=payload.normalize_styles,
            )
            after = inspect_drawio_xml(aligned)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"xml": aligned, "before": before, "after": after}

    @api.post("/api/xml/import")
    def import_xml_endpoint(payload: XmlImportRequest, request: Request):
        """Import an arbitrary AWS draw.io XML document as a Diagram Bot Snapshot."""
        try:
            snapshot = import_drawio_xml(payload.xml, title=payload.title, region=payload.region)
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        identifier = snapshots.save(snapshot) if payload.save else None
        if identifier and context.audit:
            context.audit.write(getattr(request.state, "user", "local"), "xml import", target=identifier)
        return {"id": identifier, "snapshot": snapshot}

    @api.post("/api/xml/adopt")
    def adopt_xml_endpoint(payload: XmlAdoptRequest, request: Request):
        """Adopt a company draw.io XML template as both the default DrawioSpec and a registered Design standard."""
        try:
            result = adopt_company_xml(
                payload.xml,
                name=payload.name,
                title=payload.title,
                region=payload.region,
                set_default=payload.set_default,
            )
        except (ValueError, TypeError) as exc:
            raise HTTPException(422, str(exc)) from exc
        identifier = snapshots.save(result["snapshot"]) if payload.save else None
        if identifier and context.audit:
            context.audit.write(getattr(request.state, "user", "local"), "xml adopt", target=identifier, standard=result["name"])
        return {
            "id": identifier,
            "name": result["name"],
            "spec": result["spec"].to_dict(),
            "aligned_xml": result["aligned_xml"],
            "standardized_xml": result["standardized_xml"],
            "before": result["before"],
            "after": result["after"],
        }

    return api
