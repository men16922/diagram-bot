"""Explicit orthogonal routes of a detail view, and the edge cells every view emits.

Each target group drops one trunk to its own channel row; every gutter gives each source group
its own vertical slot (deeper turns further out); every icon row gives each entering edge its
own lane, the edge going further sitting higher, so parallel routes never coincide."""
from ..views import AUXILIARY, EDGE, GROUP_TARGETS, REGIONAL, REMOTE
from .base import ICON, LANE_TOP, SUBNET_HEADER, cell_id
from .edge_column import EDGE_COLUMN, align_edge, edge_path

OUTSIDE_BOXES = {"references", "onprem"}
# Registration routes of different target groups share gutters 8 px apart; one colour per target
# group (in channel order) tells them apart. Chosen to stay clear of the subnet, VPC and ASG colours.
TG_STROKES = ("#1F5FAD", "#B4387A", "#8A6D00", "#2E7D32", "#7A4E2D")


def tg_stroke(tg_order, tg):
    return TG_STROKES[tg_order[tg] % len(TG_STROKES)] if tg in tg_order else None


def target_on_right(canvas, target, geometry):
    """Outside-VPC targets placed in the right half of the reference box use the right margin."""
    return geometry["last"] > 0 and canvas.abs[target["id"]][0] > (geometry["gutters"][0][0] + geometry["gutters"][-1][1]) / 2


def edge_entry(e, path, constraints, label=""):
    return {"id": cell_id("edge/" + e["id"]), "relation_id": e["id"], "relation": e["relation"],
            "source": cell_id(e["source"]), "target": cell_id(e["target"]), "evidence": e["evidence"],
            "points": path[1:-1], "path": path, "constraints": constraints, "label": label,
            "auxiliary": e["relation"] not in {"registered_target", "associated_load_balancer", *GROUP_TARGETS, *REGIONAL, *EDGE}}


def edge_between(e, source, target, path, constraints):
    """An overview route drawn between two shapes standing for the relation's ends (a VPC card for its attachment)."""
    return {**edge_entry(e, path, constraints), "source": cell_id(source), "target": cell_id(target), "auxiliary": False}


def route(canvas, drawn_edges, vpc_of, placed, lanes, tg_order, subnet_at, frames_at):
    """Explicit orthogonal routes. Only edges sharing a source (a bus) or a target may share a segment."""
    position = lambda key: canvas.abs[placed[key]["id"]]
    shapes = {shape["id"]: shape for shape in canvas.shapes}
    specs, plain = {}, []
    for e in sorted(drawn_edges, key=lambda e: e["id"]):
        spec = edge_spec(canvas, e, vpc_of, placed, lanes, subnet_at, frames_at, shapes)
        if spec:
            specs[e["id"]] = spec
        else:
            plain.append(e)
    gutter_slots(specs, placed, position)
    edge_specs = corridor_slots(canvas, specs, shapes, position)
    entering = entry_points(specs)
    row_lanes(specs, placed, position)
    align_cloudfront(canvas, placed, edge_specs)
    frame_entries(specs)
    for e in plain:
        canvas.edges.append(edge_entry(e, [], "", {"in_subnet": "서브넷 배치"}.get(e["relation"], "")))
    for spec in sorted(specs.values(), key=lambda s: s["edge"]["id"]):
        canvas.edges.append(EMIT[spec["kind"]](spec, position, tg_order, entering))


def edge_spec(canvas, e, vpc_of, placed, lanes, subnet_at, frames_at, shapes):
    """The route kind of one edge and the facts it is routed by; None draws it as a plain edge."""
    geometry = lanes.get(vpc_of.get(e["source"]))
    ends = e["source"] in placed and e["target"] in placed
    if e["relation"] in REMOTE and ends and placed[e["target"]]["parent"] == "onprem":
        # Site-to-Site VPN or Direct Connect VIF: from the gateway's row lane to a corridor right of the AWS Cloud,
        # then into the data center box.
        return {"edge": e, "kind": "remote", "geometry": None, "turn_left": False}
    if e["relation"] in EDGE and ends and placed[e["source"]]["parent"] == "cloud":
        # CloudFront: down a corridor left of the Region, then along the lane above the load balancer row.
        return {"edge": e, "kind": "edge", "geometry": None, "turn_left": False}
    if e["relation"] in GROUP_TARGETS and e["target"] in placed and e["source"] in frames_at and lanes.get(vpc_of.get(e["target"])):
        # Routed on the target group's trunk like its registrations; the cell runs group -> target group.
        cell, g = frames_at[e["source"]]
        x, y = canvas.abs[cell]
        return {"edge": e, "kind": "frame", "geometry": lanes[vpc_of[e["target"]]], "gutter": g,
                "group": ("bus", e["target"]), "depth": y, "far": x, "frame": (x, y, shapes[cell]["h"])}
    if e["relation"] == "in_subnet" and geometry and e["source"] in placed and e["id"] in geometry["placements"] and e["target"] in subnet_at:
        cell, g = subnet_at[e["target"]]
        x, y = canvas.abs[cell]
        return {"edge": e, "kind": "place", "geometry": geometry, "gutter": g, "group": ("place", e["id"]),
                "depth": y, "far": x, "subnet": (x, y, shapes[cell]["h"])}
    if not (geometry and ends):
        return None
    target = placed[e["target"]]
    tx, ty = canvas.abs[target["id"]]
    same = vpc_of.get(e["target"]) == vpc_of.get(e["source"]) and "column" in target
    outside = target["parent"] in OUTSIDE_BOXES
    spec = {"edge": e, "geometry": geometry, "depth": ty, "far": tx}
    if e["relation"] == "registered_target" and (same or outside):
        g = target["column"] if same else (geometry["last"] if target["parent"] == "onprem" or target_on_right(canvas, target, geometry) else 0)
        return {**spec, "kind": "bus", "gutter": g, "group": ("bus", e["source"])}
    if e["relation"] in AUXILIARY | REGIONAL and outside and "column" in placed[e["source"]]:
        return {**spec, "kind": "aux", "gutter": placed[e["source"]]["column"], "group": ("aux", e["id"])}
    if e["relation"] == "associated_load_balancer" and e["target"] in geometry["lb_mid"] \
            and placed[e["target"]]["parent"] == placed[e["source"]]["parent"]:
        return {**spec, "kind": "lb"}
    return None


def gutter_slots(specs, placed, position):
    """Gutter slots per (VPC, gutter, source group). Placement routes also descend the left margin
    from the lane above their load balancer to the channel; that leg is a shallow slot in gutter 0."""
    groups = {}
    for spec in specs.values():
        if spec["kind"] in ("lb", "remote", "edge"):
            continue
        groups.setdefault((id(spec["geometry"]), spec["gutter"]), {}).setdefault(spec["group"], []).append(spec)
        if spec["kind"] == "place":
            channel = spec["geometry"]["placements"][spec["edge"]["id"]]
            leg = {"geometry": spec["geometry"], "depth": channel, "far": position(spec["edge"]["source"])[0], "leg_of": spec}
            groups.setdefault((id(spec["geometry"]), 0), {}).setdefault(("leg", spec["edge"]["id"]), []).append(leg)
    for (_, g), members in groups.items():
        geometry = next(iter(members.values()))[0]["geometry"]
        left, right = geometry["gutters"][g]
        turn_left = g == geometry["last"] and g > 0 or len(geometry["gutters"]) == 1
        ordered = sorted(members.items(), key=lambda item: (-max(m["depth"] for m in item[1]),
                                                            max(m["far"] for m in item[1]) * (-1 if turn_left else 1), item[0]))
        for i, (_, group) in enumerate(ordered):
            for member in group:
                x = right - 8 - 8 * i if turn_left else left + 8 + 8 * i
                if "leg_of" in member:
                    member["leg_of"]["leg_x"] = x
                else:
                    # Routes to the box beside the cloud leave the right margin towards the right.
                    member["x"], member["turn_left"] = x, turn_left and placed[member["edge"]["target"]]["parent"] != "onprem" \
                        if member["edge"]["target"] in placed else turn_left


def corridor_slots(canvas, specs, shapes, position):
    """VPN and Direct Connect routes: one lane each in the corridor right of the AWS Cloud. CloudFront routes: one
    corridor lane each, leaving their distribution's right side from separate points; returns those specs."""
    cloud_right = canvas.abs["cloud"][0] + shapes["cloud"]["w"]
    for i, spec in enumerate(sorted((s for s in specs.values() if s["kind"] == "remote"), key=lambda s: s["edge"]["id"])):
        spec["x"] = cloud_right + 14 + 8 * i
    edge_specs = sorted((s for s in specs.values() if s["kind"] == "edge"), key=lambda s: (position(s["edge"]["source"])[1], s["edge"]["id"]))
    corridor = canvas.abs["cloud"][0] + 20 + EDGE_COLUMN + 12
    for i, spec in enumerate(edge_specs):
        spec["x"] = corridor + 6 * i
    return edge_specs


def entry_points(specs):
    """Entry points along each target's top, ordered so routes from further out enter further out; returns them per target."""
    entering = {}
    for spec in specs.values():
        if spec["kind"] in ("bus", "aux", "remote", "edge"):
            entering.setdefault(spec["edge"]["target"], []).append(spec)
    for members in entering.values():
        members.sort(key=lambda s: (s["x"] if not s["turn_left"] else -s["x"], s["edge"]["id"]))
        for i, spec in enumerate(members):
            fraction = (i + 1) / (len(members) + 1)
            spec["entry"] = 1 - fraction if spec["turn_left"] else fraction
    return entering


def row_lanes(specs, placed, position):
    """Exit points per source, then one lane per route entering or leaving each icon row."""
    rows, leaving = {}, {}
    for spec in specs.values():
        if spec["kind"] in ("bus", "aux", "remote", "edge"):
            tx, ty = position(spec["edge"]["target"])
            spec["end"] = tx + spec["entry"] * ICON
            rows.setdefault((placed[spec["edge"]["target"]]["parent"], ty), []).append(("in", spec, spec["end"], placed[spec["edge"]["target"]]))
        if spec["kind"] in ("aux", "place", "remote"):
            leaving.setdefault((spec["kind"], spec["edge"]["source"]), []).append(spec)
    for (kind, source), members in leaving.items():
        members.sort(key=lambda s: s["edge"]["id"])
        sx, sy = position(source)
        for i, spec in enumerate(members):
            # VPN routes leave from the top right, clear of the centred entries of routes into the same icon.
            spec["exit"] = 0.88 - 0.08 * i if kind == "remote" else (i + 1) / (len(members) + 1)
            # Reference routes leave from the left side; placement and VPN routes from the top.
            spec["stub"] = sx - 14 - 6 * i if kind == "aux" else sx + spec["exit"] * ICON
            rows.setdefault((placed[source]["parent"], sy), []).append(("out", spec, spec["stub"], placed[source]))
    for (_, top), members in rows.items():
        members.sort(key=lambda m: (m[1].get("turn_left", False) and m[0] == "in",
                                    m[2] if m[1].get("turn_left") and m[0] == "in" else -m[2], m[1]["edge"]["id"]))
        lane_top = members[0][3].get("lane_top", LANE_TOP)
        for i, (direction, spec, _, _) in enumerate(members):
            spec["lane_in" if direction == "in" else "lane_out"] = top - lane_top + 4 + 4 * (i % ((lane_top - 8) // 4))


def align_cloudfront(canvas, placed, edge_specs):
    """CloudFront icons move level with the lane their route takes above the load balancer row."""
    wanted = {}
    for spec in sorted(edge_specs, key=lambda s: (s["lane_in"], s["edge"]["id"])):
        wanted.setdefault(spec["edge"]["source"], []).append(spec["lane_in"])
    exits = align_edge(canvas, {k: s for k, s in placed.items() if s["parent"] == "cloud"}, wanted) if edge_specs else {}
    for spec in edge_specs:
        source = spec["edge"]["source"]
        spec["exit"] = exits[source][wanted[source].index(spec["lane_in"])]


def frame_entries(specs):
    """Group edges leave their frame's left side from separate points."""
    framed = {}
    for spec in specs.values():
        if spec["kind"] == "frame":
            framed.setdefault(spec["edge"]["source"], []).append(spec)
    for members in framed.values():
        for i, spec in enumerate(sorted(members, key=lambda s: (s["x"], s["edge"]["id"]))):
            spec["entry"] = (i + 1) / (len(members) + 1)


def frame_route(spec, position, tg_order, entering):
    e, geometry = spec["edge"], spec["geometry"]
    x, y, h = spec["frame"]
    gx, gy = position(e["target"])
    channel = geometry["channel"] + 7 * tg_order.get(e["target"], 0)
    side_y = y + spec["entry"] * h
    path = [(x, side_y), (spec["x"], side_y), (spec["x"], channel), (gx + ICON / 2, channel), (gx + ICON / 2, gy + ICON)]
    return {**edge_entry(e, path, f"exitX=0;exitY={spec['entry']:.3f};entryX=0.5;entryY=1;"), "stroke": tg_stroke(tg_order, e["target"])}


def remote_route(spec, position, tg_order, entering):
    e = spec["edge"]
    sx, sy = position(e["source"])
    tx, ty = position(e["target"])
    path = [(spec["stub"], sy), (spec["stub"], spec["lane_out"]), (spec["x"], spec["lane_out"]), (spec["x"], spec["lane_in"]),
            (spec["end"], spec["lane_in"]), (spec["end"], ty)]
    return edge_entry(e, path, f"exitX={spec['exit']:.3f};exitY=0;entryX={spec['entry']:.3f};entryY=0;")


def cloudfront_route(spec, position, tg_order, entering):
    e = spec["edge"]
    sx, sy = position(e["source"])
    path = edge_path(sx + ICON, sy + spec["exit"] * ICON, spec["x"], spec["lane_in"], spec["end"], position(e["target"])[1])
    return edge_entry(e, path, f"exitX=1;exitY={spec['exit']:.3f};entryX={spec['entry']:.3f};entryY=0;")


def placement_route(spec, position, tg_order, entering):
    e = spec["edge"]
    sx, sy = position(e["source"])
    x, y, h = spec["subnet"]
    channel = spec["geometry"]["placements"][e["id"]]
    entry_y = y + SUBNET_HEADER / 2
    path = [(spec["stub"], sy), (spec["stub"], spec["lane_out"]), (spec["leg_x"], spec["lane_out"]), (spec["leg_x"], channel),
            (spec["x"], channel), (spec["x"], entry_y), (x, entry_y)]
    return edge_entry(e, path, f"exitX={spec['exit']:.3f};exitY=0;entryX=0;entryY={SUBNET_HEADER / 2 / h:.3f};")


def balancer_route(spec, position, tg_order, entering):
    e = spec["edge"]
    sx, sy = position(e["source"])
    tx, ty = position(e["target"])
    middle = spec["geometry"]["lb_mid"][e["target"]]
    path = [(sx + ICON / 2, sy), (sx + ICON / 2, middle), (tx + ICON / 2, middle), (tx + ICON / 2, ty + ICON)]
    return edge_entry(e, path, "exitX=0.5;exitY=0;entryX=0.5;entryY=1;")


def target_route(spec, position, tg_order, entering):
    """Registration (bus: the target group's trunk) or reference (aux: from the source's left side) into a target's top."""
    e, end = spec["edge"], spec["end"]
    sx, sy = position(e["source"])
    tx, ty = position(e["target"])
    if spec["kind"] == "bus":
        channel = spec["geometry"]["channel"] + 7 * tg_order.get(e["source"], 0)
        path = [(sx + ICON / 2, sy + ICON), (sx + ICON / 2, channel), (spec["x"], channel), (spec["x"], spec["lane_in"]),
                (end, spec["lane_in"]), (end, ty)]
        constraints = f"exitX=0.5;exitY=1;entryX={spec['entry']:.3f};entryY=0;"
    else:
        exit_y = sy + spec["exit"] * ICON
        path = [(sx, exit_y), (spec["stub"], exit_y), (spec["stub"], spec["lane_out"]), (spec["x"], spec["lane_out"]),
                (spec["x"], spec["lane_in"]), (end, spec["lane_in"]), (end, ty)]
        constraints = f"exitX=0;exitY={spec['exit']:.3f};entryX={spec['entry']:.3f};entryY=0;"
    ports = [x for x in entering[e["target"]] if x["edge"]["source"] == e["source"]]
    label = f":{e['qualifiers']['port']}" if len(ports) > 1 and e["qualifiers"].get("port") is not None else ""
    entry = edge_entry(e, path, constraints, label)
    entry["label_offset"] = (-16 if spec["entry"] < 0.5 else 18, -9)
    if spec["kind"] == "bus":
        entry["stroke"] = tg_stroke(tg_order, e["source"])
    return entry


EMIT = {"frame": frame_route, "remote": remote_route, "edge": cloudfront_route, "place": placement_route, "lb": balancer_route,
        "bus": target_route, "aux": target_route}
