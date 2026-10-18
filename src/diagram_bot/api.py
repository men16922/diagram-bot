"""Library entry points: the stable surface for embedding Diagram Bot in another backend, no web framework needed.

A snapshot is plain JSON (metadata, coverage, raw API responses, graph); every function below takes or returns one.
Typical use from a backend that already has AWS credentials (for example role credentials from Identity Center):

    session = boto3.Session(aws_access_key_id=..., aws_secret_access_key=..., aws_session_token=..., region_name=region)
    snapshot = collect_snapshot(session, region, expected_account=account_id)
    xml = render(snapshot, view="detail", vpc=vpc_id)          # draw.io document
    findings = review_snapshot(snapshot)                       # Well-Architected style findings
    template = cloudformation(snapshot)                        # CloudFormation draft

When the backend cannot reach AWS, its browser can send the requests: prepare=lambda s: relay.attach(s, user) with a
BrowserRelay (egress.py, docs/wiki/browser-egress.md).
"""
from .collector import collect
from .combine import combine_snapshots
from .designer import COMPANY_STANDARDS, STANDARDS, Design, DesignError, register_standard
from .diff import diff as diff_snapshots
from .drawio import carry_annotations, generate_document, layout_document, validate_document
from .drawio_spec import (
    DEFAULT_SPEC,
    ENTERPRISE_CLEAN_SPEC,
    MXCELL_SPEC,
    PRESETS,
    DrawioSpec,
    get_default_spec,
    resolve_spec,
    set_default_spec,
    spec_from_xml,
)
from .egress import BrowserRelay, RelayError
from .findings import review, summary
from .graph import build_graph
from .iac import template as cloudformation
from .iac import templates as cloudformation_parts
from .samples import SAMPLES
from .store import DocumentStore, SnapshotStore
from .views import view_graph
from .xml_align import adopt_company_xml, align_drawio_xml, align_report, import_drawio_xml, inspect_drawio_xml

adopt_xml = adopt_company_xml
align_xml = align_drawio_xml
import_xml = import_drawio_xml
inspect_xml = inspect_drawio_xml

__all__ = [
    "collect_snapshot", "render", "review_snapshot", "view", "diff_snapshots", "combine_snapshots", "cloudformation",
    "cloudformation_parts", "Design", "DesignError", "STANDARDS", "COMPANY_STANDARDS", "register_standard", "SAMPLES",
    "BrowserRelay", "RelayError", "SnapshotStore", "DocumentStore", "build_graph", "layout_document",
    "validate_document", "carry_annotations", "DrawioSpec", "DEFAULT_SPEC", "ENTERPRISE_CLEAN_SPEC", "MXCELL_SPEC",
    "PRESETS", "resolve_spec", "set_default_spec", "get_default_spec", "spec_from_xml",
    "adopt_company_xml", "align_drawio_xml", "align_report", "import_drawio_xml", "inspect_drawio_xml",
    "adopt_xml", "align_xml", "import_xml", "inspect_xml",
]


def collect_snapshot(session, region, expected_account=None, role_arn=None, external_id=None, prepare=None):
    """Read-only collection of one account × region with a boto3 session; the snapshot includes its graph.

    expected_account: refuse to collect if the credentials belong to another account. prepare(session): applied to every
    session the collection uses (browser egress: lambda s: relay.attach(s, user))."""
    snapshot = collect(region, expected_account=expected_account, session=session, role_arn=role_arn, external_id=external_id,
                       prepare=prepare)
    snapshot["graph"] = build_graph(snapshot)
    return snapshot


def with_graph(snapshot):
    return snapshot if snapshot.get("graph") else {**snapshot, "graph": build_graph(snapshot)}


def view(snapshot, view=None, vpc=None, resource=None, references=False, routes=None):
    """Which nodes and relations a view shows, with visible / collected / reference / hidden counts."""
    return view_graph(with_graph(snapshot)["graph"], view, vpc, resource, references, routes)


def render(snapshot, view=None, vpc=None, resource=None, references=False, routes=None, spec=None):
    """Uncompressed draw.io XML of one view: overview (default), detail (vpc=) or focus (resource=);
    routes= filters a detail view; spec= applies a DrawioSpec (preset name, dict, or DrawioSpec)."""
    return generate_document(with_graph(snapshot), vpc, references, view, resource, routes, spec=spec)


def review_snapshot(snapshot):
    """{"summary": counts per severity, "findings": [...]} for the snapshot's graph and collection coverage."""
    snapshot = with_graph(snapshot)
    findings = review(snapshot["graph"], snapshot.get("coverage", []))
    return {"summary": summary(findings), "findings": findings}
