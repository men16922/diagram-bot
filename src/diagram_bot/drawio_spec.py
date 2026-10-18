"""Declarative draw.io document format and visual style specification (`DrawioSpec`).

Allows adapting Diagram Bot's output (or normalizing existing company `drawio.xml` files) to any enterprise
draw.io convention without modifying `layout.py` or `drawio.py`:
- Cell wrapper element: `<object>`, `<UserObject>`, or plain `<mxCell value="...">`
- Header/telemetry banner: include `heading`/`scope` text cells or omit them for a clean architecture canvas
- Container hierarchy: toggle outer `AWS Cloud`, `Region`, or `AZ` containers (`use_cloud_box`, `use_region_box`, `use_az_box`)
- Subnet label format: full multi-line route classification (`"full"`) or compact single-line `name (cidr)` (`"compact"`)
- Container, subnet & per-resource styles: distinct `isolated_subnet`, custom `styles` and `resource_styles`
- Edge conventions: directional arrowheads (`classic`, `block`, `none`), rounded vs sharp corners, stroke width/color
- Auto-extraction: `spec_from_xml(company_xml)` learns a complete `DrawioSpec` directly from a sample company `drawio.xml`
- Process-wide default: `set_default_spec(spec)` or `DIAGRAM_BOT_SPEC=<preset_or_xml_or_json_path>`.
"""
import base64
import binascii
from dataclasses import dataclass, field, replace
import json
import math
import os
from pathlib import Path
import re
from urllib.parse import unquote
from xml.etree.ElementTree import ParseError
import zlib

from defusedxml import DefusedXmlException
from defusedxml.ElementTree import fromstring

from .errors import InputError
from .layout import tier_rank

DEFAULT_ATTRIBUTE_MAP = {
    "resource": "resourceId",
    "tooltip": "tooltip",
    "layout": "layout",
    "view": "view",
    "vpc": "vpc",
    "focus": "focus",
    "references": "references",
    "routes": "routes",
    "relation_id": "relationId",
    "relation": "relation",
    "evidence": "evidence",
}

COMMON = "html=0;fontFamily=Arial;fontSize=12;fontColor=#232F3E;"
AWS_GROUP = (COMMON + "outlineConnect=0;gradientColor=none;whiteSpace=wrap;fontStyle=1;container=1;pointerEvents=0;"
             "collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;verticalAlign=top;align=left;spacingLeft=30;")
PLAIN_GROUP = COMMON + "whiteSpace=wrap;container=1;collapsible=0;recursiveResize=0;verticalAlign=top;fontStyle=0;"

DEFAULT_STYLES = {
    "title": COMMON + "text;align=left;fontSize=19;fontStyle=1;strokeColor=none;fillColor=none;",
    "caption": COMMON + "text;align=left;verticalAlign=top;whiteSpace=wrap;fontSize=10;fontColor=#5A6C86;strokeColor=none;fillColor=none;",
    "cloud": AWS_GROUP + "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;fontColor=#232F3E;dashed=0;",
    "region": AWS_GROUP + "grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;fillColor=none;fontColor=#147EBA;dashed=1;",
    "summary": AWS_GROUP + "grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=#FFFFFF;fontStyle=0;spacingTop=4;dashed=0;",
    "vpc": AWS_GROUP + "grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=none;fontColor=#8C4FFF;dashed=0;",
    "az": PLAIN_GROUP + "fillColor=none;strokeColor=#147EBA;dashed=1;fontColor=#147EBA;align=center;spacingTop=6;",
    "public_subnet": AWS_GROUP + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;dashed=0;fontSize=11;",
    "private_subnet": AWS_GROUP + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#00A4A6;fillColor=#E6F6F7;fontColor=#147EBA;dashed=0;fontSize=11;",
    "isolated_subnet": AWS_GROUP + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#3F51B5;fillColor=#ECEFF8;fontColor=#283593;dashed=0;fontSize=11;",
    "unplaced": PLAIN_GROUP + "fillColor=none;strokeColor=#5A6C86;dashed=1;fontColor=#5A6C86;align=left;spacingLeft=12;spacingTop=6;fontSize=11;",
    "references": PLAIN_GROUP + "fillColor=none;strokeColor=#5A6C86;dashed=1;fontColor=#5A6C86;align=left;spacingLeft=12;spacingTop=6;",
    "account": (COMMON + "outlineConnect=0;gradientColor=none;whiteSpace=wrap;container=0;pointerEvents=0;shape=mxgraph.aws4.group;"
                "grIcon=mxgraph.aws4.group_account;strokeColor=#CD2264;fillColor=none;fontColor=#CD2264;dashed=0;fontStyle=1;"
                "verticalAlign=top;align=left;spacingLeft=30;"),
    "asg": (COMMON + "outlineConnect=0;gradientColor=none;whiteSpace=wrap;container=0;pointerEvents=0;shape=mxgraph.aws4.groupCenter;"
            "grIcon=mxgraph.aws4.group_auto_scaling_group;grStroke=1;strokeColor=#D86613;fillColor=none;fontColor=#D86613;"
            "dashed=1;align=left;spacingLeft=12;verticalAlign=bottom;spacingBottom=4;fontSize=11;"),
    "ecs_frame": (COMMON + "rounded=1;arcSize=4;whiteSpace=wrap;container=0;pointerEvents=0;strokeColor=#ED7100;fillColor=none;"
                  "fontColor=#C25E00;dashed=1;dashPattern=6 3;align=left;spacingLeft=12;verticalAlign=bottom;spacingBottom=4;fontSize=11;"),
    "onprem": AWS_GROUP + "grIcon=mxgraph.aws4.group_corporate_data_center;strokeColor=#7D8998;fillColor=none;fontColor=#5A6C86;dashed=0;",
    "badge": COMMON + "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.waf;fillColor=#DD344C;strokeColor=#FFFFFF;gradientColor=none;",
}

DEFAULT_EDGE_STYLE = (
    "edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;fontFamily=Arial;fontSize=9;"
    "fontColor=#536A82;strokeColor=#7C91A8;strokeWidth=1.5;endArrow=none;labelBackgroundColor=#FFFFFF;"
)


MAX_BYTES = 10 * 1024 * 1024  # the whole document, and again for all decompressed pages together
MAX_CELLS = 10_000


def _parse(text, what="draw.io XML"):
    try:
        return fromstring(text)
    except (ParseError, DefusedXmlException) as exc:
        raise InputError(f"{what}을(를) 읽지 못했습니다: {exc}") from exc


def _inflate(diagram, budget):
    """A compressed page as draw.io writes it: base64, raw deflate, then URL-encoding; at most `budget` bytes inflated."""
    try:
        inflater = zlib.decompressobj(-15)
        raw = inflater.decompress(base64.b64decode((diagram.text or "").strip(), validate=True), budget + 1)
    except (binascii.Error, zlib.error, ValueError) as exc:
        raise InputError(f"압축된 페이지 {diagram.get('name') or ''}를 풀지 못했습니다: {exc}") from exc
    if len(raw) > budget or inflater.unconsumed_tail:
        raise InputError("압축을 푼 문서가 10 MiB를 넘습니다.")
    text = raw.decode("utf-8", "replace")
    model = _parse(text if text.lstrip().startswith("<") else unquote(text), "압축된 페이지")
    if model.tag != "mxGraphModel":
        raise InputError("압축된 페이지에 mxGraphModel이 없습니다.")
    diagram.text = None
    diagram.append(model)
    return len(raw)


def read_document(xml, *, decode=True):
    """The one input gate for draw.io XML (align, inspect, import, adopt, spec extraction, saved documents).

    Size limit, defusedxml parse, root tag, compressed pages decoded in place (or refused with decode=False, for documents
    the app stores uncompressed) and a cell limit. Every failure is an InputError (HTTP 422, CLI exit 2)."""
    if not isinstance(xml, str):
        raise InputError("draw.io XML 문자열이 필요합니다.")
    if len(xml.encode()) > MAX_BYTES:
        raise InputError("문서는 최대 10 MiB입니다.")
    root = _parse(xml)
    if root.tag not in {"mxfile", "mxGraphModel"}:
        raise InputError("draw.io XML 문서가 아닙니다.")
    if root.tag == "mxfile":
        budget = MAX_BYTES
        for diagram in root.findall("diagram"):
            if diagram.find("mxGraphModel") is None and (diagram.text or "").strip():
                if not decode:
                    raise InputError("압축하지 않은 draw.io XML이 필요합니다.")
                budget -= _inflate(diagram, budget)
        if root.get("compressed") == "true":
            root.set("compressed", "false")
        if not root.findall("diagram/mxGraphModel/root"):
            raise InputError("draw.io mxGraphModel/root를 찾을 수 없습니다.")
    elif root.find("root") is None:
        raise InputError("draw.io mxGraphModel/root를 찾을 수 없습니다.")
    if sum(len(page) for _, page in pages(root)) > MAX_CELLS:
        raise InputError(f"셀이 {MAX_CELLS:,}개를 넘는 문서는 처리하지 않습니다.")
    return root


def pages(root):
    """[(page name, <root> element)] of every uncompressed page of an mxfile, or the one page of an mxGraphModel."""
    if root.tag == "mxGraphModel":
        return [("", root.find("root"))] if root.find("root") is not None else []
    return [(d.get("name") or d.get("id") or f"page-{i + 1}", d.find("mxGraphModel/root"))
            for i, d in enumerate(root.findall("diagram")) if d.find("mxGraphModel/root") is not None]


def iter_cells(graph_root):
    """(wrapper, mxCell, id, label) for each cell of a page: a bare <mxCell>, or <object>/<UserObject> around one.

    The id and label live on the wrapper when there is one (draw.io's convention), else on the mxCell."""
    for el in graph_root:
        if el.tag == "mxCell":
            yield el, el, el.get("id"), el.get("value") or ""
        elif el.tag in ("object", "UserObject"):
            inner = el.find("mxCell")
            if inner is not None:
                yield el, inner, el.get("id") or inner.get("id"), el.get("label") or el.get("value") or inner.get("value") or ""


# Resource-level icons from the AWS Architecture Icons set (draw.io aws4 library).
RESOURCE_ICONS = {
    "ec2": ("instance2", "#ED7100"),
    "application": ("application_load_balancer", "#8C4FFF"),
    "network": ("network_load_balancer", "#8C4FFF"),
    "gateway": ("gateway_load_balancer", "#8C4FFF"),
    "network_interface": ("elastic_network_interface", "#8C4FFF"),
    "volume": ("volume", "#7AA116"),
    "internet_gateway": ("internet_gateway", "#8C4FFF"),
    "nat_gateway": ("nat_gateway", "#8C4FFF"),
    "db_instance": ("rds_instance", "#C925D1"),
    "db_standby": ("rds_instance_alt", "#C925D1"),
    "vpc_endpoint": ("endpoints", "#8C4FFF"),
    "lambda_function": ("lambda_function", "#ED7100"),
    "aurora": ("aurora_instance", "#C925D1"),
    "redis": ("elasticache_for_redis", "#C925D1"),
    "valkey": ("elasticache_for_valkey", "#C925D1"),
    "memcached": ("elasticache_for_memcached", "#C925D1"),
    "tgw_attachment": ("transit_gateway_attachment", "#8C4FFF"),
    "vpn_gateway": ("vpn_gateway", "#8C4FFF"),
    "customer_gateway": ("customer_gateway", "#8C4FFF"),
    "vpc_peering": ("peering", "#8C4FFF"),
    "ip_target": ("elastic_network_interface", "#8C4FFF"),
    "ecs_task": ("ecs_task", "#ED7100"),
    "ecs_service": ("ecs_service", "#ED7100"),
    "search_node": ("opensearch_service_data_node", "#8C4FFF"),
    "efs_standard": ("elastic_file_system_standard", "#7AA116"),
    "efs_one_zone": ("elastic_file_system_one_zone", "#7AA116"),
    "redshift_cluster": ("redshift_ra3", "#8C4FFF"),
    "p3": ("p3_instance", "#ED7100"),
    "p4d": ("p4d_instance", "#ED7100"),
    "p4de": ("ec2_p4de_instance", "#ED7100"),
    "g4dn": ("g4dn", "#ED7100"),
    "g4ad": ("g4ad_instance", "#ED7100"),
    "trainium": ("trainium_instance", "#ED7100"),
    "inferentia": ("inferentia", "#ED7100"),
    "dl1": ("ec2_dl1_instance", "#ED7100"),
    "elastic_ip": ("elastic_ip_address", "#8C4FFF"),
}
# Square resource-icon tiles for things without a resource-level icon.
ICONS = {
    "load_balancer": ("elastic_load_balancing", "#8C4FFF"),
    "target_group": ("elastic_load_balancing", "#6D5DB3"),
    "security_group": ("group_security_group", "#DD344C"),
    "transit_gateway": ("transit_gateway", "#8C4FFF"),
    "eks_cluster": ("eks", "#ED7100"),
    "msk_broker": ("managed_streaming_for_kafka", "#8C4FFF"),
    "documentdb": ("documentdb_with_mongodb_compatibility", "#C925D1"),
    "neptune": ("neptune", "#C925D1"),
    "bedrock": ("bedrock", "#01A88D"),
    "sagemaker": ("sagemaker", "#01A88D"),
    "sagemaker_endpoint": ("sagemaker", "#01A88D"),
    "sagemaker_cluster": ("sagemaker", "#01A88D"),
    "fsx_file_system": ("fsx", "#7AA116"),
    "fsx_lustre": ("fsx_for_lustre", "#7AA116"),
    "fsx_ontap": ("fsx_for_netapp_ontap", "#7AA116"),
    "fsx_openzfs": ("fsx_for_openzfs", "#7AA116"),
    "fsx_windows": ("fsx_for_windows_file_server", "#7AA116"),
    # Edge and dedicated connectivity: service icons, as AWS has no resource-level icon for them.
    "cloudfront_distribution": ("cloudfront", "#8C4FFF"),
    "waf_web_acl": ("waf", "#DD344C"),
    "dx_gateway": ("direct_connect", "#8C4FFF"),
    "dx_connection": ("direct_connect", "#8C4FFF"),
}

# draw.io aws4 shapes a company template may use for the same kinds (service icons, older names).
SHAPE_ALIASES = {
    "ec2": "ec2", "instance": "ec2", "instances": "ec2", "rds": "db_instance", "aurora": "aurora", "elasticache": "redis",
    "lambda": "lambda_function", "elastic_load_balancing": "load_balancer", "elastic_kubernetes_service": "eks_cluster",
    "ecs": "ecs_service", "elastic_container_service": "ecs_service", "fargate": "ecs_service", "endpoint": "vpc_endpoint",
    "opensearch_service": "search_node", "elastic_file_system": "efs_standard", "site_to_site_vpn": "vpn_gateway",
    "traditional_server": "registered_target",
}
# Kind of a resource cell by its aws4 shape (or resIcon), and by shape and fill where kinds share one icon
# (a target group is the load-balancing tile in its own colour).
SHAPE_KINDS = {**SHAPE_ALIASES, **{icon: kind for kind, (icon, _) in reversed(ICONS.items())},
               **{icon: kind for kind, (icon, _) in reversed(RESOURCE_ICONS.items())}}
SHAPE_COLOR_KINDS = {(icon, color.upper()): kind for table in (ICONS, RESOURCE_ICONS) for kind, (icon, color) in table.items()}
GROUP_ROLES = {"group_aws_cloud": "cloud", "group_aws_cloud_alt": "cloud", "group_region": "region", "group_account": "account",
               "group_vpc": "vpc", "group_vpc2": "vpc", "group_auto_scaling_group": "asg", "group_on_premise": "onprem",
               "group_corporate_data_center": "onprem", "group_security_group": "subnet", "group_subnet": "subnet"}
# Diagram Bot's own fixed cell ids (drawio.GENERATED) and the graph kinds its resourceId attributes carry.
FIXED_IDS = {"heading": "title", "scope": "caption", "cloud": "cloud", "region": "region", "references": "references", "onprem": "onprem"}
ID_ROLES = {"vpc": "vpc", "subnet": "subnet", "auto_scaling_group": "asg"}
REGION_CODE = re.compile(r"\b(?:ap|us|eu|sa|ca|me|af|il|mx)-[a-z]+-\d\b(?![a-z])")
AZ_CODE = re.compile(r"\b((?:ap|us|eu|sa|ca|me|af|il|mx)-[a-z]+-\d)([a-f])\b")
# Label words: the weaker signal, used only when the style names no AWS shape. Whole words, never substrings.
CONTAINER_WORDS = (
    ("references", r"vpc 밖|vpc 외|배치 미확인"),
    ("unplaced", r"서브넷 미확인"),
    ("onprem", r"corporate data ?cent(er|re)|\bon-?prem(ise|ises)?\b|\bdata ?cent(er|re)\b|데이터 ?센터|\bidc\b"),
    ("cloud", r"\baws cloud\b"),
    ("subnet", r"\bsubnets?\b|서브넷|^(public|private|isolated|pub|priv|dmz|web|app|db|data)\b"),
    ("asg", r"\bauto ?scaling\b|\basg\b"),
    ("account", r"\baccount\b|계정"),
    ("vpc", r"\bvpc\b"),
    ("az", r"\baz\s*[-_]?\s*([a-f]|\d)\b|availability zone|가용 ?영역|\bzone\b"),
    ("region", r"\bregion\b|리전"),
)
LABEL_KINDS = (
    ("application", r"\b(alb|application load balancer)\b"), ("network", r"\b(nlb|network load balancer)\b"),
    ("gateway", r"\b(gwlb|gateway load balancer)\b"), ("load_balancer", r"\b(elb|load balancer)\b"),
    ("target_group", r"\b(target group|tg)\b|대상 그룹"), ("nat_gateway", r"\bnat( gateway)?\b"),
    ("internet_gateway", r"\b(igw|internet gateway)\b"), ("tgw_attachment", r"\btgw attach(ment)?\b"),
    ("transit_gateway", r"\b(tgw|transit gateway)\b"), ("aurora", r"\baurora\b"),
    ("db_instance", r"\b(rds|postgres(ql)?|mysql|mariadb|oracle db|sql server)\b"),
    ("redis", r"\b(redis|elasticache|valkey|memcached)\b"), ("eks_cluster", r"\beks\b"),
    ("ecs_service", r"\b(ecs|fargate)\b"), ("lambda_function", r"\blambda\b"), ("search_node", r"\bopensearch\b"),
    ("msk_broker", r"\b(msk|kafka)\b"), ("efs_standard", r"\befs\b"), ("fsx_lustre", r"\b(fsx|lustre)\b"),
    ("sagemaker_endpoint", r"\bsagemaker\b"), ("bedrock", r"\bbedrock\b"), ("vpc_endpoint", r"\b(vpce|vpc endpoint)\b"),
    ("cloudfront_distribution", r"\bcloudfront\b"), ("waf_web_acl", r"\bwaf\b"), ("dx_connection", r"\b(direct connect|dx)\b"),
    ("customer_gateway", r"\b(cgw|customer gateway)\b"), ("vpn_gateway", r"\b(vgw|vpn gateway|vpn)\b"),
    ("ec2", r"\b(ec2|instance)\b"),
)
TIER_WORDS = {"웹": "web", "앱": "app", "데이터": "data", "프라이빗": "private"}  # Korean names layout.tier_rank does not read


def subnet_tier(label, style):
    """Tier of a drawn subnet, ranked like layout.tier_rank (public 0, web 1, app 2, data 3).

    The style's public/isolated colours and the bot's route captions decide first, then the name's words."""
    _, kv = parse_style(style)
    fill, stroke, text = kv.get("fillColor", "").upper(), kv.get("strokeColor", "").upper(), (label or "").lower()
    if fill in ("#F2F6E8", "#E8F5E9") or stroke == "#7AA116" or "→ igw" in text or re.search(r"\b(public|pub|dmz|ingress)\b|퍼블릭", text):
        return 0
    isolated = fill in ("#ECEFF8", "#EDE7F6") or stroke == "#3F51B5" or "인터넷 경로 없음" in text or re.search(r"\bisolated\b|격리", text)
    words = text.splitlines()[0] if text else ""
    for korean, english in TIER_WORDS.items():
        words = words.replace(korean, f" {english} ")
    return tier_rank({"name": words, "attributes": {}, "route_class": "isolated" if isolated else "private"})


def classify_cell(label, style, attrs=None, *, cell_id=None, container=False, small=False):
    """(role, kind, tier) of a draw.io vertex: one table for spec extraction, inspect, align and import.

    Signals, strongest first: Diagram Bot's fixed ids and resourceId kinds, the style's aws4 group icon (grIcon), its aws4
    shape or resIcon, and only then label words, matched as whole words. A shape nothing recognises is an annotation
    (role "annotation"), never a guessed resource; an unrecognised container is a plain "group"."""
    attrs = attrs or {}
    flags, kv = parse_style(style)
    text = " ".join((label or "").lower().split())
    gr = kv.get("grIcon", "").removeprefix("mxgraph.aws4.")
    shape = kv.get("shape", "").removeprefix("mxgraph.aws4.")
    icon = kv.get("resIcon", "").removeprefix("mxgraph.aws4.") or (shape if kv.get("shape", "").startswith("mxgraph.aws4.") else "")
    graph_kind = (attrs.get("resourceId") or "").split("/")[2] if (attrs.get("resourceId") or "").count("/") >= 3 else ""
    container = container or kv.get("container") == "1" or "swimlane" in flags or "group" in flags

    if cell_id in FIXED_IDS:
        return FIXED_IDS[cell_id], FIXED_IDS[cell_id], None
    if "edgeLabel" in flags:
        return "label", "label", None
    if "text" in flags or kv.get("shape") == "text":
        return ("title", "title", None) if _fraction_of(kv.get("fontSize")) >= 18 else ("caption", "caption", None)
    if icon == "waf" and small:  # the WAF marker in a protected icon's corner
        return "badge", "waf_web_acl", None
    if attrs.get("summary") == "1":
        return "summary", "vpc", None
    role = GROUP_ROLES.get(gr) or ID_ROLES.get(graph_kind)
    if role == "subnet":
        return "subnet", "subnet", subnet_tier(label, style)
    if role:
        return role, {"asg": "auto_scaling_group"}.get(role, role), None
    if kv.get("container") == "0" and kv.get("dashed") == "1" and kv.get("strokeColor", "").upper() == "#ED7100":
        return "ecs_frame", "ecs_service", None
    if icon and not icon.startswith("group"):
        kind = SHAPE_COLOR_KINDS.get((icon, kv.get("fillColor", "").upper())) or SHAPE_KINDS.get(icon)
        return "resource", kind or graph_kind or None, None
    if graph_kind:
        return "resource", graph_kind, None
    if container:
        first = text.split(" (")[0]
        for role, pattern in CONTAINER_WORDS:
            if re.search(pattern, first if role == "subnet" else text):
                if role == "subnet":
                    return "subnet", "subnet", subnet_tier(label, style)
                return role, {"asg": "auto_scaling_group"}.get(role, role), None
        if AZ_CODE.search(text):
            return "az", "az", None
        if REGION_CODE.search(text):
            return "region", "region", None
        return "group", "group", None
    for kind, pattern in LABEL_KINDS:
        if re.search(pattern, text):
            return "resource", kind, None
    return "annotation", "annotation", None


def _fraction_of(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return number if math.isfinite(number) else 0.0


def parse_style(style_str):
    """Parse a draw.io semicolon-separated style string into `(flags_list, key_value_dict)`."""
    flags, kv = [], {}
    for part in (style_str or "").split(";"):
        part = part.strip()
        if not part:
            continue
        if "=" in part:
            k, _, v = part.partition("=")
            kv[k.strip()] = v.strip()
        else:
            flags.append(part)
    return flags, kv


def format_style(flags, kv):
    """Serialize `(flags_list, key_value_dict)` back into a draw.io style string."""
    parts = list(flags) + [f"{k}={v}" for k, v in kv.items() if v is not None]
    return ";".join(parts) + (";" if parts else "")


def patch_style(style_str, **overrides):
    """Return `style_str` with `overrides` keys updated (or removed if value is None)."""
    flags, kv = parse_style(style_str)
    for k, v in overrides.items():
        if v is None:
            kv.pop(k, None)
        else:
            kv[k] = str(v)
    return format_style(flags, kv)


WRAPPERS = {"object": "object", "userobject": "UserObject", "mxcell": "mxCell"}
# Attributes draw.io itself reads on a cell or its wrapper; a mapped custom attribute must not shadow them.
RESERVED_ATTRIBUTES = {"id", "parent", "style", "vertex", "edge", "source", "target", "label", "value", "placeholders",
                       "connectable", "collapsed", "visible", "as"}
ARROWS = {"none", "classic", "classicThin", "block", "blockThin", "open", "openThin", "oval", "diamond", "diamondThin",
          "dash", "cross", "async", "openAsync", "circle", "circlePlus", "halfCircle"}
XML_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._-]{0,63}$")
KIND_NAME = re.compile(r"^[a-z][a-z0-9_]{0,47}$")
COLOR = re.compile(r"^(#[0-9A-Fa-f]{3,8}|none)$")
FONT = re.compile(r"^[\w .,'-]{1,64}$")
SPEC_TEXT = re.compile(r"^[^\x00-\x1f;=<>]{0,120}$")
NUMBERS = {"margin_x": (0, 2000), "margin_y": (0, 2000), "font_size": (4, 96), "grid_size": (1, 100), "icon_size": (8, 256),
           "slot_w": (40, 1000), "row_h": (40, 1000)}


def _check(ok, message):
    if not ok:
        raise InputError(f"draw.io 규격(spec) 오류: {message}")


def _check_style(value, where):
    _check(isinstance(value, str) and len(value) <= 65536 and not re.search(r"[\x00-\x1f]", value),
           f"{where}는 64 KiB 이하의 draw.io 스타일 문자열이어야 합니다.")


def _learned(pattern, value, default):
    """A value read from a template only when it is one the spec accepts (newer draw.io writes e.g. light-dark() colours)."""
    return value if isinstance(value, str) and pattern.match(value) else default


@dataclass(frozen=True)
class DrawioSpec:
    """Declarative specification for draw.io XML serialization, container hierarchy, and style normalization.

    Every field is validated on construction (InputError): request data reaches XML tag, attribute and style names."""
    name: str = "default"
    host: str = "Diagram Bot"
    cell_wrapper: str = "object"          # "object" | "UserObject" | "mxCell" (case-insensitive on input)
    include_header: bool = True           # False omits top heading/scope banner and shifts diagram to margin_y
    use_cloud_box: bool = True            # False omits outer AWS Cloud container (reparents children upward)
    use_region_box: bool = True           # False omits Region container (reparents VPCs upward)
    use_az_box: bool = True               # False omits AZ dashed containers (reparents subnets directly into VPC)
    subnet_label_style: str = "full"      # "full" | "compact" (single-line "name (cidr)")
    margin_x: int = 24
    margin_y: int = 24
    isolated_subnet_distinct: bool = False
    font_family: str = "Arial"
    font_size: int = 12
    font_color: str = "#232F3E"
    background: str = "#ffffff"
    grid_size: int = 10
    icon_size: int = 52
    slot_w: float = 150.0
    row_h: float = 144.0
    edge_style: str = DEFAULT_EDGE_STYLE
    edge_end_arrow: str = "none"          # ARROWS
    edge_end_fill: bool = True
    edge_rounded: bool = True
    attribute_map: dict = field(default_factory=lambda: dict(DEFAULT_ATTRIBUTE_MAP))
    styles: dict = field(default_factory=lambda: dict(DEFAULT_STYLES))
    resource_styles: dict = field(default_factory=dict)
    resource_icons: dict = field(default_factory=dict)
    service_icons: dict = field(default_factory=dict)

    def __post_init__(self):
        _check(isinstance(self.name, str) and SPEC_TEXT.match(self.name), "name은 120자 이하의 문자열이어야 합니다.")
        _check(isinstance(self.host, str) and SPEC_TEXT.match(self.host), "host는 120자 이하의 문자열이어야 합니다.")
        wrapper = WRAPPERS.get(self.cell_wrapper.lower()) if isinstance(self.cell_wrapper, str) else None
        _check(wrapper, f"cell_wrapper는 {', '.join(WRAPPERS.values())} 중 하나여야 합니다.")
        object.__setattr__(self, "cell_wrapper", wrapper)
        for key in ("include_header", "use_cloud_box", "use_region_box", "use_az_box", "isolated_subnet_distinct",
                    "edge_end_fill", "edge_rounded"):
            _check(isinstance(getattr(self, key), bool), f"{key}는 true 또는 false여야 합니다.")
        _check(self.subnet_label_style in ("full", "compact"), "subnet_label_style은 full 또는 compact여야 합니다.")
        for key, (low, high) in NUMBERS.items():
            value = getattr(self, key)
            _check(isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and low <= value <= high,
                   f"{key}는 {low}~{high} 사이의 숫자여야 합니다.")
        _check(isinstance(self.font_family, str) and FONT.match(self.font_family), "font_family는 글꼴 이름이어야 합니다(; = 금지).")
        for key in ("font_color", "background"):
            _check(isinstance(getattr(self, key), str) and COLOR.match(getattr(self, key)), f"{key}는 #RRGGBB 색이어야 합니다.")
        _check(self.edge_end_arrow in ARROWS, f"edge_end_arrow는 {', '.join(sorted(ARROWS))} 중 하나여야 합니다.")
        _check_style(self.edge_style, "edge_style")
        _check(isinstance(self.attribute_map, dict) and len(self.attribute_map) <= 32, "attribute_map은 객체여야 합니다.")
        for key, name in self.attribute_map.items():
            _check(key in DEFAULT_ATTRIBUTE_MAP, f"attribute_map 키는 {', '.join(DEFAULT_ATTRIBUTE_MAP)} 중 하나여야 합니다.")
            _check(isinstance(name, str) and XML_NAME.match(name) and name.lower() not in RESERVED_ATTRIBUTES,
                   f"attribute_map.{key}={name!r}: XML 속성 이름이어야 하고 draw.io 예약 이름은 쓸 수 없습니다.")
        _check(isinstance(self.styles, dict) and set(self.styles) <= set(DEFAULT_STYLES),
               f"styles 키는 {', '.join(DEFAULT_STYLES)} 중 하나여야 합니다.")
        for key, value in self.styles.items():
            _check_style(value, f"styles.{key}")
        for table in ("resource_styles", "resource_icons", "service_icons"):
            values = getattr(self, table)
            _check(isinstance(values, dict) and len(values) <= 200, f"{table}는 객체여야 합니다.")
            for key, value in values.items():
                _check(isinstance(key, str) and KIND_NAME.match(key), f"{table}의 키 {key!r}는 리소스 종류 이름이어야 합니다.")
                if table == "resource_styles":
                    _check_style(value, f"resource_styles.{key}")
                else:
                    _check(isinstance(value, (list, tuple)) and len(value) == 2 and isinstance(value[0], str)
                           and KIND_NAME.match(value[0]) and isinstance(value[1], str) and COLOR.match(value[1]),
                           f"{table}.{key}는 [aws4 아이콘 이름, #색] 이어야 합니다.")
            if table != "resource_styles":
                object.__setattr__(self, table, {k: tuple(v) for k, v in values.items()})

    def role_style(self, role):
        base = self.styles.get(role) or DEFAULT_STYLES.get(role, DEFAULT_STYLES["references"])
        if self.font_family != "Arial":
            base = patch_style(base, fontFamily=self.font_family)
        return base

    def effective_edge_style(self):
        style = self.edge_style
        if self.font_family != "Arial":
            style = patch_style(style, fontFamily=self.font_family)
        if self.edge_rounded is not True:
            style = patch_style(style, rounded="1" if self.edge_rounded else "0")
        if self.edge_end_arrow != "none":
            style = patch_style(style, endArrow=self.edge_end_arrow, endFill="1" if self.edge_end_fill else "0")
        return style

    def for_editor(self):
        """This spec for documents the app's editor opens: the editor bridge selects cells by their resourceId/relationId
        wrapper attributes, so a company spec that exports bare <mxCell> or renamed attributes keeps them in the app."""
        wrapper = "object" if self.cell_wrapper == "mxCell" else self.cell_wrapper
        if wrapper == self.cell_wrapper and self.attribute_map == DEFAULT_ATTRIBUTE_MAP:
            return self
        return replace(self, cell_wrapper=wrapper, attribute_map=dict(DEFAULT_ATTRIBUTE_MAP))

    def to_dict(self):
        data = {name: getattr(self, name) for name in self.__dataclass_fields__}
        data.update({key: dict(data[key]) for key in ("attribute_map", "styles", "resource_styles")})
        data.update({key: {k: list(v) for k, v in data[key].items()} for key in ("resource_icons", "service_icons")})
        return data

    @classmethod
    def from_dict(cls, data):
        """A spec from JSON: unspecified fields come from the `base` preset (default "default"); unknown keys are refused."""
        if isinstance(data, cls):
            return data
        _check(isinstance(data, dict), "spec는 프리셋 이름 또는 JSON 객체여야 합니다.")
        unknown = set(data) - set(cls.__dataclass_fields__) - {"base"}
        _check(not unknown, f"알 수 없는 키: {', '.join(sorted(map(str, unknown)))}")
        base = PRESETS.get(data.get("base", "default"))
        _check(base is not None, f"base는 {', '.join(PRESETS)} 중 하나여야 합니다.")
        values = {name: getattr(base, name) for name in cls.__dataclass_fields__}
        values.update({k: v for k, v in data.items() if k != "base"})
        for key in ("styles", "resource_styles"):
            _check(isinstance(data.get(key) or {}, dict), f"{key}는 객체여야 합니다.")
            values[key] = {**getattr(base, key), **(data.get(key) or {})}
        if "attribute_map" in data:
            _check(data["attribute_map"] is None or isinstance(data["attribute_map"], dict), "attribute_map은 객체여야 합니다.")
            values["attribute_map"] = {**base.attribute_map, **data["attribute_map"]} if data["attribute_map"] else {}
        for key in ("resource_icons", "service_icons"):
            values[key] = data.get(key) or getattr(base, key)
        return cls(**values)


DEFAULT_SPEC = DrawioSpec()

ENTERPRISE_CLEAN_SPEC = DrawioSpec(
    name="enterprise-clean",
    host="Enterprise Architecture",
    cell_wrapper="object",
    include_header=False,
    isolated_subnet_distinct=True,
    edge_end_arrow="block",
    edge_end_fill=True,
)

MXCELL_SPEC = DrawioSpec(
    name="mxcell",
    host="draw.io",
    cell_wrapper="mxCell",
    include_header=False,
    isolated_subnet_distinct=True,
    edge_end_arrow="classic",
    edge_end_fill=True,
    attribute_map={},
)

PRESETS = {
    "default": DEFAULT_SPEC,
    "clean": ENTERPRISE_CLEAN_SPEC,
    "enterprise": ENTERPRISE_CLEAN_SPEC,
    "enterprise-clean": ENTERPRISE_CLEAN_SPEC,
    "mxcell": MXCELL_SPEC,
}

_ACTIVE_DEFAULT_SPEC: DrawioSpec = DEFAULT_SPEC


def set_default_spec(spec=None) -> DrawioSpec:
    """Set the process-wide default `DrawioSpec` used whenever `spec=None` is passed to rendering APIs."""
    global _ACTIVE_DEFAULT_SPEC
    _ACTIVE_DEFAULT_SPEC = DEFAULT_SPEC if spec is None else resolve_spec(spec)
    return _ACTIVE_DEFAULT_SPEC


def get_default_spec() -> DrawioSpec:
    """Return the active default `DrawioSpec` (honoring `DIAGRAM_BOT_SPEC` env var if set)."""
    env_spec = os.environ.get("DIAGRAM_BOT_SPEC", "").strip()
    if env_spec and _ACTIVE_DEFAULT_SPEC is DEFAULT_SPEC:
        return resolve_spec(env_spec)
    return _ACTIVE_DEFAULT_SPEC


def resolve_spec(spec=None) -> DrawioSpec:
    """Trusted callers (library, CLI, DIAGRAM_BOT_SPEC): `None`, a preset name, a `.drawio`/`.json` file path, raw XML text,
    a `dict`, or a `DrawioSpec`. Request data goes through `request_spec`, which never touches the filesystem."""
    if spec is None:
        return get_default_spec()
    if isinstance(spec, DrawioSpec):
        return spec
    if isinstance(spec, Path):
        spec = str(spec)
    if isinstance(spec, str):
        s = spec.strip()
        if s in PRESETS:
            return PRESETS[s]
        if s.startswith("<"):
            return spec_from_xml(s)
        path = Path(s)
        if path.is_file():
            content = path.read_text(encoding="utf-8")
            if path.suffix.lower() == ".json" or content.lstrip().startswith("{"):
                try:
                    return DrawioSpec.from_dict(json.loads(content))
                except json.JSONDecodeError as exc:
                    raise InputError(f"규격 JSON을 읽지 못했습니다 ({path.name}): {exc}") from exc
            return spec_from_xml(content, name=re.sub(r"[^\w.-]+", "-", path.stem)[:60] or "company-extracted")
        raise InputError(f"알 수 없는 draw.io 규격 프리셋 또는 파일입니다: {spec} (선택: {', '.join(PRESETS)})")
    if isinstance(spec, dict):
        return DrawioSpec.from_dict(spec)
    raise InputError(f"지원하지 않는 spec 형식입니다: {type(spec).__name__}")


def request_spec(spec):
    """A spec from request data: None, a preset name or an inline JSON object. Anything else (a path, XML) is refused."""
    if spec is None or isinstance(spec, DrawioSpec):
        return spec
    if isinstance(spec, str):
        if spec.strip() in PRESETS:
            return PRESETS[spec.strip()]
        raise InputError(f"spec은 프리셋 이름({', '.join(PRESETS)}) 또는 JSON 객체여야 합니다.")
    return DrawioSpec.from_dict(spec)


LEARNED_ROLES = {"cloud", "region", "account", "vpc", "az", "asg", "onprem", "references", "unplaced", "summary", "ecs_frame"}


def spec_from_xml(xml_text, *, name="company-extracted") -> DrawioSpec:
    """Extract a complete `DrawioSpec` automatically from an existing company `drawio.xml` file."""
    root = read_document(xml_text)
    host = _learned(SPEC_TEXT, root.get("host"), "draw.io") if root.tag == "mxfile" else "draw.io"
    model = root.find("diagram/mxGraphModel") if root.tag == "mxfile" else root
    graph_root = model.find("root")

    background = _learned(COLOR, model.get("background"), "#ffffff")
    grid = model.get("gridSize") or ""
    grid_size = int(grid) if grid.isdigit() and 1 <= int(grid) <= 100 else 10
    page_roots = [page for _, page in pages(root)]
    wrappers = {el.tag for page in page_roots for el in page if el.tag in ("object", "UserObject")}
    cell_wrapper = "UserObject" if "UserObject" in wrappers else ("object" if "object" in wrappers else "mxCell")

    ids = {el.get("id") for el in graph_root}
    include_header = "heading" in ids and "scope" in ids

    styles = dict(DEFAULT_STYLES)
    resource_styles = {}
    fonts, font_colors = [], []
    edge_arrows, edge_rounded, edge_styles = [], [], []
    seen_roles = set()
    subnet_labels = []

    # Every page, first occurrence wins; roles and kinds come from the same table inspect, align and import use.
    for page in page_roots:
        cells = [cell for cell in iter_cells(page) if cell[2] not in ("0", "1", None)]
        parents = {inner.get("parent") for _, inner, _, _ in cells}
        for el, inner, cid, label in cells:
            style_str = inner.get("style") or ""
            if not style_str or len(style_str) > 65536 or re.search(r"[\x00-\x1f]", style_str):
                continue
            _, kv = parse_style(style_str)
            if FONT.match(kv.get("fontFamily", "")):
                fonts.append(kv["fontFamily"])
            if COLOR.match(kv.get("fontColor", "")):
                font_colors.append(kv["fontColor"])
            if inner.get("vertex") == "1":
                geo = inner.find("mxGeometry")
                small = geo is not None and all(re.fullmatch(r"\d{1,2}(\.\d+)?", geo.get(k) or "x") and float(geo.get(k)) <= 28
                                                for k in ("width", "height"))
                role, kind, tier = classify_cell(label, style_str, el.attrib, cell_id=cid, container=cid in parents, small=small)
                if role == "subnet":
                    role = "public_subnet" if tier == 0 else "isolated_subnet" if tier == 3 else "private_subnet"
                    if label:
                        subnet_labels.append(label)
                if role in LEARNED_ROLES or role.endswith("_subnet"):
                    if role not in seen_roles:
                        styles[role] = style_str
                    seen_roles.add(role)
                elif role == "resource" and kind and KIND_NAME.match(kind):
                    resource_styles.setdefault(kind, style_str)
            elif inner.get("edge") == "1":
                edge_styles.append(style_str)
                if kv.get("endArrow") in ARROWS:
                    edge_arrows.append(kv["endArrow"])
                if "rounded" in kv:
                    edge_rounded.append(kv["rounded"] != "0")

    has_vpc = "vpc" in seen_roles
    use_cloud_box = ("cloud" in seen_roles) if has_vpc else True
    use_region_box = ("region" in seen_roles) if has_vpc else True
    has_subnet = bool(seen_roles & {"public_subnet", "private_subnet", "isolated_subnet"})
    use_az_box = ("az" in seen_roles) if has_subnet else True

    subnet_label_style = "full"
    if subnet_labels and all("\n" not in lbl and "0.0.0.0/0" not in lbl for lbl in subnet_labels):
        subnet_label_style = "compact"

    font_family = fonts[0] if fonts else "Arial"
    font_color = font_colors[0] if font_colors else "#232F3E"
    edge_end_arrow = edge_arrows[0] if edge_arrows else "none"
    rounded = edge_rounded[0] if edge_rounded else True
    edge_style = DEFAULT_EDGE_STYLE
    if edge_styles:
        _, ekv = parse_style(edge_styles[0])
        for k in ("strokeColor", "strokeWidth", "fontSize", "fontColor", "jettySize"):
            if k in ekv:
                edge_style = patch_style(edge_style, **{k: ekv[k]})

    return DrawioSpec(
        name=name,
        host=host,
        cell_wrapper=cell_wrapper,
        include_header=include_header,
        use_cloud_box=use_cloud_box,
        use_region_box=use_region_box,
        use_az_box=use_az_box,
        subnet_label_style=subnet_label_style,
        # Learned only from what the template shows: a distinct isolated style needs an isolated subnet drawn differently.
        isolated_subnet_distinct="isolated_subnet" in seen_roles and styles["isolated_subnet"] != styles["private_subnet"],
        font_family=font_family,
        font_color=font_color,
        background=background,
        grid_size=grid_size,
        edge_style=edge_style,
        edge_end_arrow=edge_end_arrow,
        edge_rounded=rounded,
        attribute_map={} if cell_wrapper == "mxCell" else dict(DEFAULT_ATTRIBUTE_MAP),
        styles=styles,
        resource_styles=resource_styles,
    )
