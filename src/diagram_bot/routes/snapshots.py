"""Snapshots: list, read, import, combine; review findings, change review and the CloudFormation draft."""
import re

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from ..diff import diff
from ..findings import review, summary


class CombineRequest(BaseModel):
    ids: list[str]
    title: str | None = None


def router(context):
    api = APIRouter()
    store = context.snapshots

    @api.get("/api/snapshots")
    def list_snapshots():
        return store.list()

    @api.get("/api/snapshots/{identifier}")
    def read_snapshot(identifier: str):
        return store.get(identifier)

    @api.get("/api/snapshots/{identifier}/findings")
    def read_findings(identifier: str):
        snapshot = store.get(identifier)
        findings = review(snapshot["graph"], snapshot["coverage"])
        return {"summary": summary(findings), "findings": findings}

    @api.get("/api/snapshots/{identifier}/cloudformation")
    def export_cloudformation(identifier: str, part: int | None = None):
        """CloudFormation draft of one account × region; a combined snapshot names its part by index."""
        from ..iac import dumps, template
        snapshot = store.get(identifier)
        parts = snapshot.get("parts") or []
        if parts and (part is None or not 0 <= part < len(parts)):
            raise HTTPException(422, f"여러 계정을 합친 스냅샷입니다. part=0~{len(parts) - 1}로 계정·리전을 고르세요.")
        source = parts[part] if parts else snapshot
        meta = source["metadata"]
        label = "-".join(["diagram-bot-cfn", *([meta["sample"]] if meta.get("sample") else []), meta["account_id"], meta["region"]])
        name = re.sub(r"[^A-Za-z0-9._-]", "-", label) + ".json"
        return Response(dumps(template(source)), media_type="application/json", headers={"Content-Disposition": f'attachment; filename="{name}"'})

    @api.get("/api/snapshots/{identifier}/diff/{other}")
    def compare_snapshots(identifier: str, other: str):
        """Changes from `other` (before) to `identifier` (after)."""
        return diff(store.get(other), store.get(identifier))

    @api.post("/api/snapshots", status_code=201)
    def import_snapshot(snapshot: dict):
        try:
            return {"id": store.save(snapshot)}
        except (ValueError, KeyError, TypeError, AttributeError) as exc:
            raise HTTPException(422, f"스냅샷 형식을 확인하세요: {exc}") from exc

    @api.post("/api/snapshots/combine", status_code=201)
    def combine(params: CombineRequest):
        """One snapshot from several account × region snapshots: the overview draws the network across accounts."""
        from ..combine import combine_snapshots
        try:
            return {"id": store.save(combine_snapshots([store.get(i) for i in params.ids], params.title))}
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    return api
