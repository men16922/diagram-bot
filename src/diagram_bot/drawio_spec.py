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
from dataclasses import dataclass, field
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


def _detect_container_role(label, style_str):
    _, kv = parse_style(style_str)
    gr = kv.get("grIcon", "")
    text = (label or "").lower()
    if "group_aws_cloud" in gr or "aws cloud" in text:
        return "cloud"
    if "group_region" in gr or (
        re.search(r"\b(ap|us|eu|sa|ca|me|af)-[a-z]+-\d\b", text) and "az" not in text and not re.search(r"\d[a-f]\b", text)
    ):
        return "region"
    if "group_account" in gr or "account" in text or "계정" in text:
        return "account"
    if "group_vpc" in gr or ("vpc" in text and "subnet" not in text and "엔드포인트" not in text and kv.get("container") == "1"):
        return "vpc"
    if "group_auto_scaling_group" in gr or "auto scaling" in text or "asg" in text:
        return "asg"
    if "group_corporate_data_center" in gr or "data center" in text or "on-prem" in text or "데이터 센터" in text:
        return "onprem"
    if "group_security_group" in gr or (
        kv.get("container") == "1" and any(w in text for w in ("subnet", "서브넷", "public-", "private-", "isolated-", "app-", "db-", "web-", "data-"))
    ):
        if any(w in text for w in ("public", "퍼블릭", "pub")):
            return "public_subnet"
        if any(w in text for w in ("isolated", "data", "db", "rds", "격리", "데이터")):
            return "isolated_subnet"
        return "private_subnet"
    if kv.get("container") == "1" and (re.search(r"\b[a-z]{2}-[a-z]+-\d[a-f]\b", text) or "az" in text or "가용 영역" in text):
        return "az"
    return None


def _detect_resource_kind(label, style_str):
    _, kv = parse_style(style_str)
    res_icon = kv.get("resIcon", "").replace("mxgraph.aws4.", "")
    shape = kv.get("shape", "").replace("mxgraph.aws4.", "")
    icon_key = res_icon or shape
    text = (label or "").lower()
    known = {
        "instance2": "ec2",
        "application_load_balancer": "application",
        "network_load_balancer": "network",
        "gateway_load_balancer": "gateway",
        "rds_instance": "db_instance",
        "rds_instance_alt": "db_standby",
        "aurora_instance": "aurora",
        "nat_gateway": "nat_gateway",
        "internet_gateway": "internet_gateway",
        "endpoints": "vpc_endpoint",
        "lambda_function": "lambda_function",
        "elasticache_for_redis": "redis",
        "elasticache_for_valkey": "valkey",
        "elasticache_for_memcached": "memcached",
        "transit_gateway": "transit_gateway",
        "transit_gateway_attachment": "tgw_attachment",
        "eks": "eks_cluster",
        "ecs_service": "ecs_service",
        "ecs_task": "ecs_task",
        "elastic_file_system_standard": "efs_standard",
        "cloudfront": "cloudfront_distribution",
        "waf": "waf_web_acl",
    }
    if icon_key in known:
        return known[icon_key]
    if "alb" in text:
        return "application"
    if "nlb" in text:
        return "network"
    if "rds" in text or "postgres" in text or "mysql" in text:
        return "db_instance"
    if "nat" in text:
        return "nat_gateway"
    if "ec2" in text or "was" in text or "web-" in text:
        return "ec2"
    return None


def spec_from_xml(xml_text, *, name="company-extracted") -> DrawioSpec:
    """Extract a complete `DrawioSpec` automatically from an existing company `drawio.xml` file."""
    root = read_document(xml_text)
    host = _learned(SPEC_TEXT, root.get("host"), "draw.io") if root.tag == "mxfile" else "draw.io"
    model = root.find("diagram/mxGraphModel") if root.tag == "mxfile" else root
    graph_root = model.find("root")

    background = _learned(COLOR, model.get("background"), "#ffffff")
    grid = model.get("gridSize") or ""
    grid_size = int(grid) if grid.isdigit() and 1 <= int(grid) <= 100 else 10
    wrappers = {el.tag for el in graph_root if el.tag in ("object", "UserObject")}
    cell_wrapper = "UserObject" if "UserObject" in wrappers else ("object" if "object" in wrappers else "mxCell")

    ids = {el.get("id") for el in graph_root}
    include_header = "heading" in ids and "scope" in ids

    styles = dict(DEFAULT_STYLES)
    resource_styles = {}
    fonts, font_colors, icon_sizes = [], [], []
    edge_arrows, edge_rounded, edge_styles = [], [], []
    seen_roles = set()
    subnet_labels = []

    for el in graph_root:
        inner = el if el.tag == "mxCell" else el.find("mxCell")
        if inner is None:
            continue
        label = el.get("label") or el.get("value") or inner.get("value") or ""
        style_str = inner.get("style") or ""
        if not style_str:
            continue
        _, kv = parse_style(style_str)
        if FONT.match(kv.get("fontFamily", "")):
            fonts.append(kv["fontFamily"])
        if COLOR.match(kv.get("fontColor", "")):
            font_colors.append(kv["fontColor"])
        if len(style_str) > 65536 or re.search(r"[\x00-\x1f]", style_str):
            continue
        if inner.get("vertex") == "1":
            role = _detect_container_role(label, style_str)
            if role:
                styles[role] = style_str
                seen_roles.add(role)
                if role in ("public_subnet", "private_subnet", "isolated_subnet") and label:
                    subnet_labels.append(label)
            else:
                rkind = _detect_resource_kind(label, style_str)
                if rkind:
                    resource_styles.setdefault(rkind, style_str)
                    geo = inner.find("mxGeometry")
                    width = (geo.get("width") or "") if geo is not None else ""
                    if re.fullmatch(r"\d{2}(\.\d+)?", width) and 28 < float(width) <= 96:
                        icon_sizes.append(int(float(width)))
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
    icon_size = icon_sizes[0] if icon_sizes else 52
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
        isolated_subnet_distinct=("isolated_subnet" in seen_roles) or (styles.get("isolated_subnet") != styles.get("private_subnet")),
        font_family=font_family,
        font_color=font_color,
        background=background,
        grid_size=grid_size,
        icon_size=icon_size,
        edge_style=edge_style,
        edge_end_arrow=edge_end_arrow,
        edge_rounded=rounded,
        attribute_map={} if cell_wrapper == "mxCell" else dict(DEFAULT_ATTRIBUTE_MAP),
        styles=styles,
        resource_styles=resource_styles,
    )
