"""draw.io documents of a snapshot view: read (generated on first use), save, relayout and restore with revisions."""
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from ..drawio import carry_annotations, generate_document, validate_document
from ..views import view_graph


class DocumentRequest(BaseModel):
    xml: str = Field(max_length=10 * 1024 * 1024)
    revision: int = Field(ge=0)


class RevisionRequest(BaseModel):
    revision: int = Field(ge=0)


class RestoreRequest(RevisionRequest):
    from_revision: int = Field(ge=0)


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


def generate(snapshot, scope):
    return generate_document(snapshot, scope[1], scope[3], scope[0], scope[2], scope[4])


def router(context):
    api = APIRouter()
    snapshots, documents = context.snapshots, context.documents

    @api.get("/api/snapshots/{identifier}/document")
    def read_document(identifier: str, view: str | None = None, vpc: str | None = None, resource: str | None = None,
                      references: bool = False, reset: bool = False, routes: str | None = None):
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        saved, history = documents.read(identifier, scope)
        if saved and not reset:
            return describe(saved, selection, history)
        return describe({"xml": generate(snapshot, scope), "revision": saved["revision"] if saved else 0, "saved_at": None}, selection, history)

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
                          resource: str | None = None, references: bool = False, routes: str | None = None):
        """Explicit automatic layout: archive the current edit, regenerate, keep user-added notes."""
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        saved, history = documents.replace(identifier, scope, request.revision, generate(snapshot, scope), reason="자동 재배치 전",
                                           merge=carry_annotations)
        return describe(saved, selection, history)

    @api.post("/api/snapshots/{identifier}/document/restore")
    def restore_document(identifier: str, request: RestoreRequest, view: str | None = None, vpc: str | None = None,
                         resource: str | None = None, references: bool = False, routes: str | None = None):
        snapshot = snapshots.get(identifier)
        scope, selection = view_scope(snapshot, view, vpc, resource, references, routes)
        saved, history = documents.replace(identifier, scope, request.revision, source_revision=request.from_revision)
        return describe(saved, selection, history)

    return api
