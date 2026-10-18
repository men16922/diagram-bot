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
from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
from defusedxml.ElementTree import fromstring

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


@dataclass(frozen=True)
class DrawioSpec:
    """Declarative specification for draw.io XML serialization, container hierarchy, and style normalization."""
    name: str = "default"
    host: str = "Diagram Bot"
    cell_wrapper: str = "object"          # "object" | "UserObject" | "mxCell"
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
    edge_end_arrow: str = "none"          # "none" | "classic" | "block" | "open"
    edge_end_fill: bool = True
    edge_rounded: bool = True
    attribute_map: dict = field(default_factory=lambda: dict(DEFAULT_ATTRIBUTE_MAP))
    styles: dict = field(default_factory=lambda: dict(DEFAULT_STYLES))
    resource_styles: dict = field(default_factory=dict)
    resource_icons: dict = field(default_factory=dict)
    service_icons: dict = field(default_factory=dict)

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
        return {
            "name": self.name,
            "host": self.host,
            "cell_wrapper": self.cell_wrapper,
            "include_header": self.include_header,
            "use_cloud_box": self.use_cloud_box,
            "use_region_box": self.use_region_box,
            "use_az_box": self.use_az_box,
            "subnet_label_style": self.subnet_label_style,
            "margin_x": self.margin_x,
            "margin_y": self.margin_y,
            "isolated_subnet_distinct": self.isolated_subnet_distinct,
            "font_family": self.font_family,
            "font_size": self.font_size,
            "font_color": self.font_color,
            "background": self.background,
            "grid_size": self.grid_size,
            "icon_size": self.icon_size,
            "slot_w": self.slot_w,
            "row_h": self.row_h,
            "edge_style": self.edge_style,
            "edge_end_arrow": self.edge_end_arrow,
            "edge_end_fill": self.edge_end_fill,
            "edge_rounded": self.edge_rounded,
            "attribute_map": dict(self.attribute_map),
            "styles": dict(self.styles),
            "resource_styles": dict(self.resource_styles),
            "resource_icons": {k: list(v) for k, v in self.resource_icons.items()},
            "service_icons": {k: list(v) for k, v in self.service_icons.items()},
        }

    @classmethod
    def from_dict(cls, data):
        if isinstance(data, cls):
            return data
        base_name = (data or {}).get("base", "default")
        base = PRESETS.get(base_name, DEFAULT_SPEC)
        merged_styles = {**base.styles, **(data.get("styles") or {})}
        merged_res_styles = {**base.resource_styles, **(data.get("resource_styles") or {})}
        merged_attrs = (
            {**base.attribute_map, **(data.get("attribute_map") or {})}
            if "attribute_map" not in data or data["attribute_map"] is not None
            else {}
        )
        if data.get("attribute_map") == {}:
            merged_attrs = {}
        return cls(
            name=data.get("name", base.name),
            host=data.get("host", base.host),
            cell_wrapper=data.get("cell_wrapper", base.cell_wrapper),
            include_header=data.get("include_header", base.include_header),
            use_cloud_box=bool(data.get("use_cloud_box", base.use_cloud_box)),
            use_region_box=bool(data.get("use_region_box", base.use_region_box)),
            use_az_box=bool(data.get("use_az_box", base.use_az_box)),
            subnet_label_style=data.get("subnet_label_style", base.subnet_label_style),
            margin_x=int(data.get("margin_x", base.margin_x)),
            margin_y=int(data.get("margin_y", base.margin_y)),
            isolated_subnet_distinct=bool(data.get("isolated_subnet_distinct", base.isolated_subnet_distinct)),
            font_family=data.get("font_family", base.font_family),
            font_size=int(data.get("font_size", base.font_size)),
            font_color=data.get("font_color", base.font_color),
            background=data.get("background", base.background),
            grid_size=int(data.get("grid_size", base.grid_size)),
            icon_size=int(data.get("icon_size", base.icon_size)),
            slot_w=float(data.get("slot_w", base.slot_w)),
            row_h=float(data.get("row_h", base.row_h)),
            edge_style=data.get("edge_style", base.edge_style),
            edge_end_arrow=data.get("edge_end_arrow", base.edge_end_arrow),
            edge_end_fill=bool(data.get("edge_end_fill", base.edge_end_fill)),
            edge_rounded=bool(data.get("edge_rounded", base.edge_rounded)),
            attribute_map=merged_attrs,
            styles=merged_styles,
            resource_styles=merged_res_styles,
            resource_icons={k: tuple(v) for k, v in (data.get("resource_icons") or base.resource_icons).items()},
            service_icons={k: tuple(v) for k, v in (data.get("service_icons") or base.service_icons).items()},
        )


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
    """Resolve `None`, a preset name, a `.drawio`/`.json` file path, raw XML text, a `dict`, or a `DrawioSpec`."""
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
                return DrawioSpec.from_dict(json.loads(content))
            return spec_from_xml(content, name=path.stem)
        raise ValueError(f"알 수 없는 draw.io 규격 프리셋 또는 파일입니다: {spec} (선택: {', '.join(PRESETS)})")
    if isinstance(spec, dict):
        return DrawioSpec.from_dict(spec)
    raise TypeError(f"지원하지 않는 spec 형식입니다: {type(spec)!r}")


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
    root = fromstring(xml_text)
    host = root.get("host", "draw.io") if root.tag == "mxfile" else "draw.io"
    model = root.find(".//mxGraphModel") if root.tag == "mxfile" else root
    graph_root = model.find("root") if model is not None else None
    if graph_root is None:
        raise ValueError("draw.io mxGraphModel/root를 찾을 수 없습니다.")

    background = model.get("background", "#ffffff") or "#ffffff"
    grid_size = int(model.get("gridSize", "10") or 10)
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
        if kv.get("fontFamily"):
            fonts.append(kv["fontFamily"])
        if kv.get("fontColor"):
            font_colors.append(kv["fontColor"])
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
                    if geo is not None and geo.get("width"):
                        w = int(float(geo.get("width")))
                        if 28 < w <= 96:
                            icon_sizes.append(w)
        elif inner.get("edge") == "1":
            edge_styles.append(style_str)
            if "endArrow" in kv:
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
