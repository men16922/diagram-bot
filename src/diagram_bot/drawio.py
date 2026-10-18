"""Serialize a computed layout into native, editable draw.io XML."""
from xml.etree.ElementTree import Element, SubElement, tostring

from .drawio_spec import ICONS, RESOURCE_ICONS, iter_cells, read_document, resolve_spec
from .graph import build_graph
from .layout import cell_id, layout_view
from .views import view_graph

__all__ = ["ICONS", "RESOURCE_ICONS", "carry_annotations", "cell_id", "generate_document", "layout_document", "validate_document"]
GENERATED = {"0", "1", "heading", "scope", "cloud", "region", "references", "onprem"}

RELATIONS = {
    "associated_load_balancer": "LB 연결", "registered_target": "대상 등록",
    "in_subnet": "서브넷 배치", "uses_security_group": "SG 연결",
    "attached_interface": "ENI 장착", "attached_volume": "EBS 장착",
    "asg_target_group": "Auto Scaling 대상 그룹", "attached_transit_gateway": "TGW 연결",
    "ecs_target_group": "ECS 서비스 대상 그룹", "vpn_connection": "Site-to-Site VPN",
    "dx_virtual_interface": "Direct Connect 가상 인터페이스", "dx_gateway_association": "Direct Connect 게이트웨이 연결",
    "cloudfront_origin": "CloudFront 오리진",
}
COMMON = "html=0;fontFamily=Arial;fontSize=12;fontColor=#232F3E;"
# Group styles of the AWS Architecture Icons (2023+) as shipped in draw.io's aws4 library.
AWS_GROUP = (COMMON + "outlineConnect=0;gradientColor=none;whiteSpace=wrap;fontStyle=1;container=1;pointerEvents=0;"
             "collapsible=0;recursiveResize=0;shape=mxgraph.aws4.group;verticalAlign=top;align=left;spacingLeft=30;")
PLAIN_GROUP = COMMON + "whiteSpace=wrap;container=1;collapsible=0;recursiveResize=0;verticalAlign=top;fontStyle=0;"
STYLES = {
    "title": COMMON + "text;align=left;fontSize=19;fontStyle=1;strokeColor=none;fillColor=none;",
    "caption": COMMON + "text;align=left;verticalAlign=top;whiteSpace=wrap;fontSize=10;fontColor=#5A6C86;strokeColor=none;fillColor=none;",
    "cloud": AWS_GROUP + "grIcon=mxgraph.aws4.group_aws_cloud_alt;strokeColor=#232F3E;fillColor=none;fontColor=#232F3E;dashed=0;",
    "region": AWS_GROUP + "grIcon=mxgraph.aws4.group_region;strokeColor=#00A4A6;fillColor=none;fontColor=#147EBA;dashed=1;",
    "summary": AWS_GROUP + "grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=#FFFFFF;fontStyle=0;spacingTop=4;dashed=0;",
    "vpc": AWS_GROUP + "grIcon=mxgraph.aws4.group_vpc2;strokeColor=#8C4FFF;fillColor=none;fontColor=#8C4FFF;dashed=0;",
    "az": PLAIN_GROUP + "fillColor=none;strokeColor=#147EBA;dashed=1;fontColor=#147EBA;align=center;spacingTop=6;",
    "public_subnet": AWS_GROUP + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#7AA116;fillColor=#F2F6E8;fontColor=#248814;dashed=0;fontSize=11;",
    "private_subnet": AWS_GROUP + "grIcon=mxgraph.aws4.group_security_group;grStroke=0;strokeColor=#00A4A6;fillColor=#E6F6F7;fontColor=#147EBA;dashed=0;fontSize=11;",
    "unplaced": PLAIN_GROUP + "fillColor=none;strokeColor=#5A6C86;dashed=1;fontColor=#5A6C86;align=left;spacingLeft=12;spacingTop=6;fontSize=11;",
    "references": PLAIN_GROUP + "fillColor=none;strokeColor=#5A6C86;dashed=1;fontColor=#5A6C86;align=left;spacingLeft=12;spacingTop=6;",
    # Auto Scaling group overlays AZ columns (not a container), dashed orange as in the icon set.
    # AWS account boundary in a combined (multi-account) overview; drawn behind its VPC cards, not a container.
    "account": (COMMON + "outlineConnect=0;gradientColor=none;whiteSpace=wrap;container=0;pointerEvents=0;shape=mxgraph.aws4.group;"
                "grIcon=mxgraph.aws4.group_account;strokeColor=#CD2264;fillColor=none;fontColor=#CD2264;dashed=0;fontStyle=1;"
                "verticalAlign=top;align=left;spacingLeft=30;"),
    "asg": (COMMON + "outlineConnect=0;gradientColor=none;whiteSpace=wrap;container=0;pointerEvents=0;shape=mxgraph.aws4.groupCenter;"
            "grIcon=mxgraph.aws4.group_auto_scaling_group;grStroke=1;strokeColor=#D86613;fillColor=none;fontColor=#D86613;"
            "dashed=1;align=left;spacingLeft=12;verticalAlign=bottom;spacingBottom=4;fontSize=11;"),
    # ECS service around its tasks: same overlay idea in the containers colour, without a group icon.
    "ecs_frame": (COMMON + "rounded=1;arcSize=4;whiteSpace=wrap;container=0;pointerEvents=0;strokeColor=#ED7100;fillColor=none;"
                  "fontColor=#C25E00;dashed=1;dashPattern=6 3;align=left;spacingLeft=12;verticalAlign=bottom;spacingBottom=4;fontSize=11;"),
    "onprem": AWS_GROUP + "grIcon=mxgraph.aws4.group_corporate_data_center;strokeColor=#7D8998;fillColor=none;fontColor=#5A6C86;dashed=0;",
    # AWS WAF marker in the corner of a protected resource icon (a child cell of that icon).
    "badge": COMMON + "shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.waf;fillColor=#DD344C;strokeColor=#FFFFFF;gradientColor=none;",
}
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;fontFamily=Arial;fontSize=9;fontColor=#536A82;strokeColor=#7C91A8;strokeWidth=1.5;endArrow=none;labelBackgroundColor=#FFFFFF;"


def validate_document(xml):
    """A document the app stores: the input gate without decompression (stored documents stay uncompressed)."""
    return read_document(xml, decode=False)


def resource_style(shape, spec=None):
    cfg = resolve_spec(spec)
    variant = shape.get("variant")
    custom = cfg.resource_styles.get(variant) or cfg.resource_styles.get(shape.get("kind"))
    res_icons = {**RESOURCE_ICONS, **cfg.resource_icons} if cfg.resource_icons else RESOURCE_ICONS
    svc_icons = {**ICONS, **cfg.service_icons} if cfg.service_icons else ICONS
    # A variant (engine, file system class, IP target) picks its own icon, else the kind's.
    icon = res_icons.get(variant) or (None if variant in svc_icons else res_icons.get(shape.get("kind")))
    shape_name, color = svc_icons.get(variant) or svc_icons.get(shape.get("kind"), ("", "#687B90"))
    common = COMMON if cfg.font_family == "Arial" and cfg.font_size == 12 and cfg.font_color == "#232F3E" else (
        f"html=0;fontFamily={cfg.font_family};fontSize={cfg.font_size};fontColor={cfg.font_color};"
    )
    style = common + "whiteSpace=wrap;"
    if shape.get("label_side") == "right":
        style += "labelWidth=170;labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=8;"
    else:
        style += f"labelWidth={shape.get('label_width', 140)};align=center;verticalLabelPosition=bottom;verticalAlign=top;spacingTop=8;"
    if shape.get("font_size"):
        style += f"fontSize={shape['font_size']};"
    if custom:
        style += custom if custom.endswith(";") else (custom + ";")
    elif shape.get("outside_vpc") and not icon and not shape_name:
        # Target registered with AvailabilityZone=all: an address outside the VPC (e.g. on premises).
        style += "shape=mxgraph.aws4.traditional_server;fillColor=#232F3D;strokeColor=none;gradientColor=none;"
    elif icon:
        style += f"shape=mxgraph.aws4.{icon[0]};fillColor={icon[1]};strokeColor=none;gradientColor=none;outlineConnect=0;"
    elif shape_name:
        style += f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{shape_name};fillColor={color};strokeColor=#FFFFFF;gradientColor=none;"
    else:
        style += "rounded=1;fillColor=#E8EFF6;strokeColor=#A7BFD4;"
    if shape.get("reference"):
        style += "dashed=1;opacity=65;"
    if shape.get("emphasis"):
        style += "fontStyle=1;labelBackgroundColor=#FFF0B3;"
    return style


def _subnet_role(shape, cfg):
    if shape.get("public"):
        return "public_subnet"
    if cfg.isolated_subnet_distinct and (shape.get("route_class") == "isolated" or shape.get("tier") == 3):
        return "isolated_subnet"
    return "private_subnet"


def _format_subnet_label(label, cfg):
    if cfg.subnet_label_style != "compact" or "\n" not in (label or ""):
        return label
    lines = [ln.strip() for ln in label.splitlines() if ln.strip()]
    if len(lines) >= 2:
        cidr = lines[1].split("·")[0].strip()
        if cidr and cidr not in lines[0]:
            return f"{lines[0]} ({cidr})"
        return lines[0]
    return label


def page(file, layout, name, spec=None):
    cfg = resolve_spec(spec)
    diagram = SubElement(file, "diagram", {"id": f"{layout['view']['kind']}-{cell_id(name)[2:10]}", "name": name})
    model = SubElement(diagram, "mxGraphModel", {"dx": "1200", "dy": "800", "grid": "1", "gridSize": str(cfg.grid_size),
                                                 "page": "0", "background": cfg.background})
    root = SubElement(model, "root")
    SubElement(root, "mxCell", {"id": "0"})
    SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    ids = {"1"}

    by_id = {s["id"]: s for s in layout["shapes"]}
    omitted = set()
    if not cfg.include_header:
        omitted.update(("heading", "scope"))
    if not cfg.use_cloud_box:
        omitted.add("cloud")
    if not cfg.use_region_box:
        omitted.add("region")
    if not cfg.use_az_box:
        omitted.update(s["id"] for s in layout["shapes"] if s["role"] == "az")

    placed = []
    for shape in layout["shapes"]:
        if shape["id"] in omitted:
            continue
        cur_p = shape["parent"]
        off_x, off_y = 0.0, 0.0
        while cur_p in omitted and cur_p in by_id:
            p_shape = by_id[cur_p]
            off_x += p_shape["x"]
            off_y += p_shape["y"]
            cur_p = p_shape["parent"]
        placed.append((shape, cur_p, shape["x"] + off_x, shape["y"] + off_y))

    top_boxes = [(nx, ny) for s, p, nx, ny in placed if p == "1" and s["id"] not in ("heading", "scope")]
    dx = (cfg.margin_x - min(x for x, _ in top_boxes)) if (not cfg.use_cloud_box and top_boxes) else 0.0
    dy = (cfg.margin_y - min(y for _, y in top_boxes)) if ((not cfg.include_header or not cfg.use_cloud_box or not cfg.use_region_box) and top_boxes) else 0.0
    attr_map = cfg.attribute_map

    for shape, parent_id, nx, ny in placed:
        sx = nx + (dx if parent_id == "1" else 0.0)
        sy = ny + (dy if parent_id == "1" else 0.0)
        role = _subnet_role(shape, cfg) if shape["role"] == "subnet" else shape["role"]
        label = _format_subnet_label(shape["label"], cfg) if shape["role"] == "subnet" else shape["label"]
        style = resource_style(shape, cfg) if shape["role"] == "resource" else cfg.role_style("ecs_frame" if shape.get("frame") == "ecs" else role)
        if shape["role"] == "summary" and not shape.get("resource"):
            style = cfg.role_style("references") + f"fontColor={cfg.font_color};spacingTop=12;spacingLeft=16;"
        if shape.get("empty"):
            style += "dashed=1;strokeColor=#9AA8BC;fontColor=#62748A;"
        if shape.get("emphasis") and shape["role"] != "resource":
            style += "strokeColor=#E0A100;strokeWidth=3;"
        if shape.get("muted"):
            # Off the route filter's path: kept in place for context, dimmed.
            style += "opacity=30;textOpacity=45;"
        extra_attrs = {}
        for key in ("resource", "tooltip", "layout", "view", "vpc", "focus", "references", "routes"):
            mapped = attr_map.get(key)
            if mapped and shape.get(key) not in (None, ""):
                extra_attrs[mapped] = str(shape[key])
        if shape["role"] == "summary" and attr_map:
            extra_attrs["summary"] = "1"
        if cfg.cell_wrapper == "mxCell":
            cell = SubElement(root, "mxCell", {"id": shape["id"], "value": label, "vertex": "1",
                                               "parent": parent_id, "style": style, **extra_attrs})
        else:
            obj = SubElement(root, cfg.cell_wrapper, {"id": shape["id"], "label": label, **extra_attrs})
            cell = SubElement(obj, "mxCell", {"vertex": "1", "parent": parent_id, "style": style})
        SubElement(cell, "mxGeometry", {"x": f"{sx:g}", "y": f"{sy:g}", "width": f"{shape['w']:g}",
                                        "height": f"{shape['h']:g}", "as": "geometry"})
        ids.add(shape["id"])
    edge_base = cfg.effective_edge_style()
    for edge in layout["edges"]:
        if edge["source"] not in ids or edge["target"] not in ids:
            continue
        edge_attrs = {}
        for key, val in (("relation_id", edge["relation_id"]),
                         ("relation", RELATIONS.get(edge["relation"], edge["relation"])),
                         ("evidence", edge["evidence"])):
            mapped = attr_map.get(key)
            if mapped and val not in (None, ""):
                edge_attrs[mapped] = str(val)
        style = edge["constraints"] + edge_base + ("dashed=1;strokeColor=#A0AEC0;" if edge.get("auxiliary") else "") \
            + (f"strokeColor={edge['stroke']};" if edge.get("stroke") else "")
        if cfg.cell_wrapper == "mxCell":
            cell = SubElement(root, "mxCell", {"id": edge["id"], "value": edge["label"], "edge": "1", "parent": "1",
                                               "source": edge["source"], "target": edge["target"], "style": style, **edge_attrs})
        else:
            obj = SubElement(root, cfg.cell_wrapper, {"id": edge["id"], "label": edge["label"], **edge_attrs})
            cell = SubElement(obj, "mxCell", {"edge": "1", "parent": "1", "source": edge["source"], "target": edge["target"], "style": style})
        labelled = edge["label"] and edge.get("label_offset")
        geometry = SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry", **({"x": "1"} if labelled else {})})
        if labelled:
            SubElement(geometry, "mxPoint", {"x": f"{edge['label_offset'][0]:g}", "y": f"{edge['label_offset'][1]:g}", "as": "offset"})
        if edge["points"]:
            points = SubElement(geometry, "Array", {"as": "points"})
            for x, y in edge["points"]:
                SubElement(points, "mxPoint", {"x": f"{x + dx:g}", "y": f"{y + dy:g}"})


def layout_document(snapshot, view=None, vpc_id=None, resource=None, include_references=False, graph=None, routes=None):
    graph = graph or build_graph(snapshot)
    return layout_view(snapshot, graph, view_graph(graph, view, vpc_id, resource, include_references, routes))


def generate_document(snapshot, vpc_id=None, include_references=False, view=None, resource=None, routes=None, spec=None):
    """One page for an explicit view; without a view, an overview page plus one detail page per VPC.

    `routes` narrows a detail page to one load balancer's or target group's routes (views.route_filter).
    `spec` customizes the draw.io XML envelope, cell wrapper, banner, subnet colors, fonts and edge styles."""
    cfg = resolve_spec(spec)
    graph = build_graph(snapshot)
    file = Element("mxfile", {"host": cfg.host, "version": "2", "compressed": "false"})
    if view or vpc_id or resource:
        layout = layout_document(snapshot, view, vpc_id, resource, include_references, graph, routes)
        focus = next((n["name"] for n in graph["nodes"] if n["id"] == layout["view"]["resource"]), None)
        name = {"overview": "전체 구성", "detail": vpc_id, "focus": focus}[layout["view"]["kind"]]
        page(file, layout, name or "AWS", spec=cfg)
    else:
        page(file, layout_document(snapshot, "overview", graph=graph), "전체 구성", spec=cfg)
        for vpc in sorted(n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc"):
            page(file, layout_document(snapshot, "detail", vpc, None, include_references, graph), vpc, spec=cfg)
    return tostring(file, encoding="unicode")


def carry_annotations(saved_xml, generated_xml):
    """Copy user-added cells (notes, shapes, user edges) from the first page of an edited document.

    Generated cells are recognised by their deterministic IDs or AWS attributes, so a relayout
    replaces them while user annotations keep their own geometry.
    """
    saved, generated = validate_document(saved_xml), validate_document(generated_xml)
    target = generated.find("diagram/mxGraphModel/root") if generated.tag == "mxfile" else generated.find("root")
    source = saved.find("diagram/mxGraphModel/root") if saved.tag == "mxfile" else saved.find("root")
    if source is None or target is None:
        return generated_xml
    present = {key for _, _, key, _ in iter_cells(target)}
    for cell, inner, key, _ in list(iter_cells(source)):
        # A cell without an id cannot be matched or referenced; draw.io assigns one when it saves, so skip it.
        if not key or key in present or key in GENERATED or key.startswith("r-") \
                or any(cell.get(a) is not None for a in ("resourceId", "relationId", "relationIndex", "summary")):
            continue
        if any(inner.get(a) is not None and inner.get(a) not in present for a in ("parent", "source", "target")):
            continue
        if cell is not inner and cell.get("id") is None:  # draw.io reads a wrapped cell's id from the wrapper
            cell.set("id", inner.attrib.pop("id"))
        target.append(cell)
        present.add(key)
    return tostring(generated, encoding="unicode")
