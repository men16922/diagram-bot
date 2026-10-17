"""View contract: which resources and relations a view shows. The snapshot graph is never mutated."""

VIEWS = ("overview", "detail", "focus")
# Relations that define the configured topology and are shown in every view.
CORE = {"associated_load_balancer", "registered_target"}
# Reference-only details from EC2/ELB responses; shown only when requested.
AUXILIARY = {"uses_security_group", "attached_interface", "attached_volume"}
AUXILIARY_KINDS = {"security_group", "network_interface", "volume"}
# Regional resources reached from a VPC, drawn with the VPC's detail view.
REGIONAL = {"attached_transit_gateway"}
# Resources drawn inside the subnet that the API places them in.
SUBNET_KINDS = {"ec2", "nat_gateway", "db_instance", "db_standby"}
# Known to the graph and the inspector but not drawn as shapes (routing is shown on subnets).
UNDRAWN_KINDS = {"route_table"}


def containment(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    vpc_of = {e["source"]: e["target"] for e in graph["edges"] if e["relation"] == "in_vpc"}
    subnet_of = {e["source"]: e["target"] for e in graph["edges"]
                 if e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] in SUBNET_KINDS}
    return nodes, vpc_of, subnet_of


def view_graph(graph, view=None, vpc=None, resource=None, references=False):
    """Return visible node IDs, drawn edges and counts for one view."""
    view = view or ("detail" if vpc else "overview")
    if view not in VIEWS:
        raise ValueError("지원하지 않는 보기입니다.")
    nodes, vpc_of, subnet_of = containment(graph)
    edges = graph["edges"]
    focus = None
    if view == "detail":
        vpc_node = next((n for n in nodes.values() if n["kind"] == "vpc" and n["resource_id"] == vpc), None)
        if not vpc_node:
            raise ValueError("선택한 VPC가 스냅샷에 없습니다.")
        visible = {vpc_node["id"]} | {k for k, v in vpc_of.items() if v == vpc_node["id"]}
        # Registered targets and associated load balancers stay visible even if only referenced.
        for _ in range(2):
            visible |= {e["target"] for e in edges if e["relation"] in CORE | REGIONAL and e["source"] in visible}
        if references:
            visible |= {e["target"] for e in edges if e["relation"] in AUXILIARY and e["source"] in visible}
        # Target group -> Auto Scaling group is a candidate; the layout draws it instead of the per-instance
        # registrations when the group's members can be framed together.
        drawn = [e for e in edges if e["relation"] in CORE | REGIONAL | {"asg_target_group"} | (AUXILIARY if references else set())]
    elif view == "focus":
        focus = next((n["id"] for n in nodes.values() if n["id"] == resource or n["resource_id"] == resource), None)
        if not focus:
            raise ValueError("선택한 리소스가 스냅샷에 없습니다.")
        allowed = CORE | REGIONAL | {"in_subnet", "asg_target_group"} | (AUXILIARY if references or nodes[focus]["kind"] in AUXILIARY_KINDS else set())
        drawn = [e for e in edges if focus in (e["source"], e["target"]) and e["relation"] in allowed
                 and not (e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] in SUBNET_KINDS and e["source"] != focus)]
        visible = {focus} | {e["source"] for e in drawn} | {e["target"] for e in drawn}
        if nodes[focus]["kind"] == "subnet":
            visible |= {k for k, v in subnet_of.items() if v == focus}
        if nodes[focus]["kind"] == "auto_scaling_group":
            visible |= {e["target"] for e in edges if e["source"] == focus and e["relation"] == "asg_member"}
        # Boundaries needed to read the placement of the visible resources.
        visible |= {vpc_of[k] for k in list(visible) if k in vpc_of}
        visible |= {subnet_of[k] for k in list(visible) if k in subnet_of}
        drawn = [e for e in drawn if not (e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] in SUBNET_KINDS)]
    else:
        visible = {n["id"] for n in nodes.values() if n["kind"] == "vpc"}
        drawn = []
    visible -= {k for k in visible if nodes[k]["kind"] in UNDRAWN_KINDS and k != focus}
    if view != "overview":
        drawn = [e for e in drawn if e["source"] in visible and e["target"] in visible
                 and not (e["relation"] == "in_subnet" and view == "detail")]
    collected = [n for n in nodes.values() if not n["reference_only"]]
    shown = [nodes[k] for k in visible]
    return {"view": {"kind": view, "vpc": vpc if view == "detail" else None,
                     "resource": focus, "references": bool(references)},
            "nodes": sorted(visible), "edges": drawn, "focus": focus,
            "counts": {"collected": len(collected), "references": len(nodes) - len(collected),
                       "visible": len(shown), "visible_references": sum(n["reference_only"] for n in shown),
                       "hidden": len(nodes) - len(shown)}}
