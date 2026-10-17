"""Serialize a computed layout into native, editable draw.io XML."""
from xml.etree.ElementTree import Element, SubElement, tostring

from defusedxml.ElementTree import fromstring

from .graph import build_graph
from .layout import cell_id, layout_view
from .views import view_graph

__all__ = ["cell_id", "generate_document", "layout_document", "validate_document"]

RELATIONS = {
    "associated_load_balancer": "LB 연결", "registered_target": "대상 등록",
    "in_subnet": "서브넷 배치", "uses_security_group": "SG 연결",
    "attached_interface": "ENI 장착", "attached_volume": "EBS 장착",
}
ICONS = {
    "ec2": ("ec2", "#ED7100"),
    "load_balancer": ("elastic_load_balancing", "#8C4FFF"),
    "target_group": ("elastic_load_balancing", "#6D5DB3"),
    "security_group": ("group_security_group", "#DD344C"),
    "network_interface": ("elastic_network_interface", "#8C4FFF"),
    "volume": ("elastic_block_store", "#7AA116"),
}
COMMON = "html=0;fontFamily=Arial;fontSize=12;fontColor=#24364B;"
GROUP = COMMON + "swimlane;startSize=38;horizontal=1;rounded=1;arcSize=10;collapsible=0;whiteSpace=wrap;fillColor=#F1F7FC;swimlaneFillColor=#FFFFFF;strokeColor=#A7BFD4;fontStyle=1;align=left;spacingLeft=16;"
STYLES = {
    "title": COMMON + "text;align=left;fontSize=19;fontStyle=1;strokeColor=none;fillColor=none;",
    "caption": COMMON + "text;align=left;verticalAlign=top;whiteSpace=wrap;fontSize=10;fontColor=#62748A;strokeColor=none;fillColor=none;",
    "account": GROUP + "fillColor=#EEF2FA;strokeColor=#9BAACB;",
    "summary": COMMON + "rounded=1;arcSize=6;whiteSpace=wrap;align=left;verticalAlign=top;spacingLeft=16;spacingTop=12;fillColor=#FFFFFF;strokeColor=#4875EF;strokeWidth=1.5;fontSize=12;",
    "vpc": GROUP,
    "az": GROUP + "startSize=32;dashed=1;fillColor=#F4F3FA;strokeColor=#CCC4E2;",
    "subnet": GROUP + "startSize=46;fillColor=#EAF6F2;strokeColor=#8BBCA9;fontSize=10;",
    "unplaced": GROUP + "startSize=46;dashed=1;fillColor=#FFF6E7;strokeColor=#D5B47A;fontSize=10;",
    "references": GROUP + "startSize=46;dashed=1;fillColor=#FFF6E7;strokeColor=#D5B47A;",
}
EDGE = "edgeStyle=orthogonalEdgeStyle;rounded=1;html=0;fontFamily=Arial;fontSize=9;fontColor=#536A82;strokeColor=#7C91A8;strokeWidth=1.5;endArrow=none;labelBackgroundColor=#FFFFFF;"


def validate_document(xml):
    if len(xml.encode()) > 10 * 1024 * 1024:
        raise ValueError("문서는 최대 10 MiB입니다.")
    root = fromstring(xml)
    if root.tag not in {"mxfile", "mxGraphModel"}:
        raise ValueError("draw.io XML 문서가 아닙니다.")
    # Keep an inspectable XML document, not compressed/unbounded input.
    if root.tag == "mxfile" and not root.findall("diagram/mxGraphModel"):
        raise ValueError("압축하지 않은 draw.io XML이 필요합니다.")
    return root


def resource_style(shape):
    shape_name, color = ICONS.get(shape.get("kind"), ("", "#687B90"))
    style = COMMON + "whiteSpace=wrap;"
    if shape.get("label_side") == "right":
        style += "labelWidth=170;labelPosition=right;verticalLabelPosition=middle;align=left;verticalAlign=middle;spacingLeft=8;"
    else:
        style += "labelWidth=140;align=center;verticalLabelPosition=bottom;verticalAlign=top;spacingTop=8;"
    if shape_name:
        style += f"shape=mxgraph.aws4.resourceIcon;resIcon=mxgraph.aws4.{shape_name};fillColor={color};strokeColor=#FFFFFF;gradientColor=none;"
    else:
        style += "rounded=1;fillColor=#E8EFF6;strokeColor=#A7BFD4;"
    if shape.get("reference"):
        style += "dashed=1;opacity=65;"
    if shape.get("emphasis"):
        style += "fontStyle=1;labelBackgroundColor=#FFF0B3;"
    return style


def page(file, layout, name):
    diagram = SubElement(file, "diagram", {"id": f"{layout['view']['kind']}-{cell_id(name)[2:10]}", "name": name})
    model = SubElement(diagram, "mxGraphModel", {"dx": "1200", "dy": "800", "grid": "1", "gridSize": "10", "page": "0", "background": "#ffffff"})
    root = SubElement(model, "root")
    SubElement(root, "mxCell", {"id": "0"})
    SubElement(root, "mxCell", {"id": "1", "parent": "0"})
    ids = {"1"}
    for shape in layout["shapes"]:
        attrs = {"id": shape["id"], "label": shape["label"]}
        for key, attr in (("resource", "resourceId"), ("tooltip", "tooltip"), ("layout", "layout"), ("view", "view"),
                          ("vpc", "vpc"), ("focus", "focus"), ("references", "references")):
            if shape.get(key) not in (None, ""):
                attrs[attr] = str(shape[key])
        if shape["role"] == "summary":
            attrs["summary"] = "1"
        style = resource_style(shape) if shape["role"] == "resource" else STYLES[shape["role"]]
        if shape.get("empty"):
            style += "dashed=1;strokeColor=#9AA8BC;fontColor=#62748A;"
        if shape.get("emphasis") and shape["role"] != "resource":
            style += "strokeColor=#E0A100;strokeWidth=3;"
        obj = SubElement(root, "object", attrs)
        cell = SubElement(obj, "mxCell", {"vertex": "1", "parent": shape["parent"], "style": style})
        SubElement(cell, "mxGeometry", {"x": f"{shape['x']:g}", "y": f"{shape['y']:g}", "width": f"{shape['w']:g}",
                                        "height": f"{shape['h']:g}", "as": "geometry"})
        ids.add(shape["id"])
    for edge in layout["edges"]:
        if edge["source"] not in ids or edge["target"] not in ids:
            continue
        obj = SubElement(root, "object", {"id": edge["id"], "label": edge["label"], "relationId": edge["relation_id"],
                                          "relation": RELATIONS.get(edge["relation"], edge["relation"]), "evidence": edge["evidence"]})
        style = edge["constraints"] + EDGE + ("dashed=1;strokeColor=#A0AEC0;" if edge.get("auxiliary") else "")
        cell = SubElement(obj, "mxCell", {"edge": "1", "parent": "1", "source": edge["source"], "target": edge["target"], "style": style})
        geometry = SubElement(cell, "mxGeometry", {"relative": "1", "as": "geometry", **({"x": f"{edge['label_at']:g}"} if edge["label"] and edge["points"] else {})})
        if edge["points"]:
            points = SubElement(geometry, "Array", {"as": "points"})
            for x, y in edge["points"]:
                SubElement(points, "mxPoint", {"x": f"{x:g}", "y": f"{y:g}"})


def layout_document(snapshot, view=None, vpc_id=None, resource=None, include_references=False, graph=None):
    graph = graph or build_graph(snapshot)
    return layout_view(snapshot, graph, view_graph(graph, view, vpc_id, resource, include_references))


def generate_document(snapshot, vpc_id=None, include_references=False, view=None, resource=None):
    """One page for an explicit view; without a view, an overview page plus one detail page per VPC."""
    graph = build_graph(snapshot)
    file = Element("mxfile", {"host": "Diagram Bot", "version": "2", "compressed": "false"})
    if view or vpc_id or resource:
        layout = layout_document(snapshot, view, vpc_id, resource, include_references, graph)
        name = {"overview": "전체 구성", "detail": vpc_id, "focus": layout["view"]["resource"]}[layout["view"]["kind"]]
        page(file, layout, name or "AWS")
    else:
        page(file, layout_document(snapshot, "overview", graph=graph), "전체 구성")
        for vpc in sorted(n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc"):
            page(file, layout_document(snapshot, "detail", vpc, None, include_references, graph), vpc)
    return tostring(file, encoding="unicode")
