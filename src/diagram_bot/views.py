"""View contract: which resources and relations a view shows. The snapshot graph is never mutated."""

VIEWS = ("overview", "detail", "focus")
# Relations that define the configured topology and are shown in every view.
CORE = {"associated_load_balancer", "registered_target"}
# Reference-only details from EC2/ELB responses; shown only when requested.
AUXILIARY = {"uses_security_group", "attached_interface", "attached_volume"}
AUXILIARY_KINDS = {"security_group", "network_interface", "volume"}
# Regional resources reached from a VPC, drawn with the VPC's detail view.
REGIONAL = {"attached_transit_gateway", "vpn_connection", "dx_virtual_interface"}
# Connections that leave AWS for the data center box: Site-to-Site VPN and Direct Connect virtual interfaces.
REMOTE = {"vpn_connection", "dx_virtual_interface"}
SITE_KINDS = {"customer_gateway", "dx_connection"}
# The edge in front of a load balancer: a CloudFront distribution drawn outside the Region box.
EDGE = {"cloudfront_origin"}
# Resources drawn inside the subnet that the API places them in.
SUBNET_KINDS = {"ec2", "nat_gateway", "db_instance", "db_standby", "cache_node", "msk_broker", "registered_target", "ecs_task",
                "search_node", "efs_mount_target", "redshift_cluster", "fsx_file_system", "sagemaker_cluster"}
# Groups framed around their members across AZ columns, and the relation to their target groups.
GROUPS = {"auto_scaling_group": "asg_member", "ecs_service": "service_task"}
GROUP_TARGETS = {"asg_target_group", "ecs_target_group"}
# Known to the graph and the inspector but not drawn as shapes (routing is shown on subnets).
# Logical groupings shown through their members (DB cluster, cache replication group, MSK cluster,
# OpenSearch domain, EFS file system).
# Elastic IPs appear on the labels of what they are attached to, web ACLs as a WAF badge, TGW route tables on the gateway
# and the VPC cards.
UNDRAWN_KINDS = {"route_table", "db_cluster", "cache_replication_group", "msk_cluster", "opensearch_domain", "efs_file_system",
                 "placement_group", "elastic_ip", "waf_web_acl", "tgw_route_table"}


def containment(graph):
    nodes = {n["id"]: n for n in graph["nodes"]}
    vpc_of = {e["source"]: e["target"] for e in graph["edges"] if e["relation"] == "in_vpc"}
    subnet_of = {e["source"]: e["target"] for e in graph["edges"]
                 if e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] in SUBNET_KINDS}
    return nodes, vpc_of, subnet_of


HUB_ORDER = {"transit_gateway": 0, "vpn_gateway": 1, "vpc_peering": 2, "dx_gateway": 3}


def overview_links(graph, nodes):
    """Connections the overview draws between VPC cards and network hubs, between hubs, then from hubs to on premises.

    Each link is (edge, from, to): VPC -> Transit Gateway (through its VPC attachment), VPC -> peering or
    virtual private gateway, Direct Connect gateway -> Transit Gateway / virtual private gateway (association), and
    Transit Gateway / virtual private gateway / Direct Connect gateway -> customer gateway (VPN) or Direct Connect
    connection (virtual interface)."""
    attachment_vpc = {e["source"]: e["target"] for e in graph["edges"]
                      if e["relation"] == "in_vpc" and nodes.get(e["source"], {}).get("kind") == "tgw_attachment"}
    links = []
    for e in graph["edges"]:
        source, target = nodes.get(e["source"]), nodes.get(e["target"])
        if not source or not target:
            continue
        if e["relation"] == "attached_transit_gateway" and e["source"] in attachment_vpc:
            links.append((e, attachment_vpc[e["source"]], e["target"]))
        elif e["relation"] in ("in_vpc", "peer_vpc") and source["kind"] in ("vpc_peering", "vpn_gateway") and target["kind"] == "vpc":
            links.append((e, e["target"], e["source"]))
        elif e["relation"] in REMOTE | {"dx_gateway_association"}:
            links.append((e, e["source"], e["target"]))
    return links


def edge_links(graph, nodes, vpc_of):
    """CloudFront -> the VPC whose load balancer is its origin: (edge, distribution, VPC, load balancer)."""
    return [(e, e["source"], vpc_of[e["target"]], e["target"]) for e in graph["edges"]
            if e["relation"] in EDGE and e["source"] in nodes and vpc_of.get(e["target"]) in nodes]


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
        # A peering between two collected VPCs is drawn in both VPCs' detail views.
        visible |= {e["source"] for e in edges if e["relation"] == "peer_vpc" and e["target"] == vpc_node["id"]}
        # Registered targets and associated load balancers stay visible even if only referenced.
        for _ in range(2):
            visible |= {e["target"] for e in edges if e["relation"] in CORE | REGIONAL and e["source"] in visible}
        if references:
            visible |= {e["target"] for e in edges if e["relation"] in AUXILIARY and e["source"] in visible}
        # CloudFront distributions whose origin is one of this VPC's load balancers, drawn at the edge.
        visible |= {e["source"] for e in edges if e["relation"] in EDGE and e["target"] in visible}
        # Target group -> Auto Scaling group is a candidate; the layout draws it instead of the per-instance
        # registrations when the group's members can be framed together.
        drawn = [e for e in edges if e["relation"] in CORE | REGIONAL | EDGE | GROUP_TARGETS | (AUXILIARY if references else set())]
    elif view == "focus":
        focus = next((n["id"] for n in nodes.values() if n["id"] == resource or n["resource_id"] == resource), None)
        if not focus:
            raise ValueError("선택한 리소스가 스냅샷에 없습니다.")
        allowed = CORE | REGIONAL | EDGE | GROUP_TARGETS | {"in_subnet"} | (AUXILIARY if references or nodes[focus]["kind"] in AUXILIARY_KINDS else set())
        drawn = [e for e in edges if focus in (e["source"], e["target"]) and e["relation"] in allowed
                 and not (e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] in SUBNET_KINDS and e["source"] != focus)]
        visible = {focus} | {e["source"] for e in drawn} | {e["target"] for e in drawn}
        if nodes[focus]["kind"] == "subnet":
            visible |= {k for k, v in subnet_of.items() if v == focus}
        if nodes[focus]["kind"] in GROUPS:
            visible |= {e["target"] for e in edges if e["source"] == focus and e["relation"] == GROUPS[nodes[focus]["kind"]]}
        # Boundaries needed to read the placement of the visible resources.
        visible |= {vpc_of[k] for k in list(visible) if k in vpc_of}
        visible |= {subnet_of[k] for k in list(visible) if k in subnet_of}
        drawn = [e for e in drawn if not (e["relation"] == "in_subnet" and nodes[e["source"]]["kind"] in SUBNET_KINDS)]
    else:
        # VPC summary cards plus the network that joins them: Transit Gateways, peering, VPN gateways and
        # the customer gateways on premises.
        # CloudFront distributions stand at the edge, joined to the VPC card of their load balancer origin.
        links = overview_links(graph, nodes)
        visible = {n["id"] for n in nodes.values() if n["kind"] == "vpc"} | {k for _, a, b in links for k in (a, b)} \
            | {n["id"] for n in nodes.values() if n["kind"] == "cloudfront_distribution" and not n["reference_only"]}
        drawn = [e for e, _, _ in links] + [e for e, *_ in edge_links(graph, nodes, vpc_of)]
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
