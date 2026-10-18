"""The edge: CloudFront distributions in a column inside the AWS Cloud, left of the Region box. Their routes run down a
corridor of their own between the column and the Region, then along a lane above the target's row."""
from .base import BOTTOM_LABEL, ICON, LINE, MAX_LINES, cell_id
from .text import icon_label, icon_variant

EDGE_COLUMN = BOTTOM_LABEL + 10
EDGE_SLOT = ICON + 8 + MAX_LINES * LINE + 16


def edge_offset(distributions, routes):
    """Region x inside the cloud: past the edge column and one corridor lane per route."""
    return 20 + (EDGE_COLUMN + 12 + 6 * len(routes) + 12 if distributions else 0)


def waf_badge(canvas, shape, node):
    """AWS WAF marker in the corner of a resource icon protected by a web ACL (child cell: moves with the icon)."""
    if node.get("waf"):
        canvas.add(cell_id(node["id"] + "/waf"), shape["id"], ICON - 18, ICON - 18, 18, 18, "badge", "", resource=node.get("waf_acl"),
                   tooltip="AWS WAF · " + ", ".join(node["waf"]))


def edge_column(canvas, nodes, cloud, distributions):
    """CloudFront icons stacked in the edge column; returns their shapes."""
    if not distributions:
        return {}
    canvas.add(cell_id("edge/caption"), "cloud", 20, 44, EDGE_COLUMN, 20, "caption", "엣지 · CloudFront (리전 밖)")
    shapes = {}
    for i, n in enumerate(distributions):
        shapes[n["id"]] = canvas.add(cell_id(n["id"]), "cloud", 20 + (BOTTOM_LABEL - ICON) / 2, 72 + i * EDGE_SLOT, ICON, ICON, "resource",
                                     icon_label(n, "bottom"), resource=n["id"], kind=n["kind"], reference=n["reference_only"],
                                     label_side="bottom", tooltip=n["name"], variant=icon_variant(n))
        waf_badge(canvas, shapes[n["id"]], n)
    cloud["h"] = max(cloud["h"], 72 + len(distributions) * EDGE_SLOT + 4)
    return shapes


def align_edge(canvas, column, lanes):
    """Move each CloudFront icon level with its first route's lane (never above the column top or onto the icon before
    it), so a route leaves straight. `lanes` maps a distribution to its routes' lanes (absolute y) in route order;
    returns each route's exit fraction on the icon's right side."""
    cloud = next(s for s in canvas.shapes if s["id"] == "cloud")
    top = canvas.abs["cloud"][1] + 72
    exits = {}
    for key in sorted(column, key=lambda k: (min(lanes.get(k) or [float("inf")]), k)):
        shape, wanted = column[key], lanes.get(key) or []
        y = max(top, wanted[0] - ICON / 2) if wanted else top
        dy = y - canvas.abs[shape["id"]][1]
        shape["y"] += dy
        for cell in [shape] + [s for s in canvas.shapes if s["parent"] == shape["id"]]:
            canvas.abs[cell["id"]] = (canvas.abs[cell["id"]][0], canvas.abs[cell["id"]][1] + dy)
        top = y + EDGE_SLOT
        exits[key] = ([min(0.85, max(0.15, (wanted[0] - y) / ICON))] if len(wanted) == 1
                      else [(i + 1) / (len(wanted) + 1) for i in range(len(wanted))])
    cloud["h"] = max(cloud["h"], top - canvas.abs["cloud"][1] - 12)
    return exits


def edge_path(sx, exit_y, corridor_x, lane, end, end_y):
    """CloudFront route: right from the icon, down its corridor lane when the exit is not level with the lane, then
    along the lane to above the target and down into it."""
    if abs(exit_y - lane) < 0.5:
        return [(sx, lane), (end, lane), (end, end_y)]
    return [(sx, exit_y), (corridor_x, exit_y), (corridor_x, lane), (end, lane), (end, end_y)]
