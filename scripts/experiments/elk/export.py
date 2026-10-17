"""Export a detail view as an ELK hierarchical graph (evaluation only)."""
import json, sys
sys.path.insert(0, str(__import__('pathlib').Path(__file__).resolve().parents[3] / 'tests'))
from diagram_bot.demo import demo_snapshot
from diagram_bot.graph import build_graph
from diagram_bot.views import view_graph, containment
from fixtures import scale_snapshot

def export(snapshot, vpc):
    g = build_graph(snapshot); sel = view_graph(g, "detail", vpc)
    nodes, vpc_of, subnet_of = containment(g)
    vis = set(sel["nodes"])
    elk = {"id": "root", "layoutOptions": {"elk.algorithm": "layered", "elk.direction": "DOWN",
           "elk.hierarchyHandling": "INCLUDE_CHILDREN", "elk.edgeRouting": "ORTHOGONAL", "elk.spacing.nodeNode": "40"}, "children": [], "edges": []}
    groups = {}
    def group(key, parent):
        if key not in groups:
            groups[key] = {"id": key, "children": [], "layoutOptions": {"elk.padding": "[top=46,left=20,bottom=20,right=20]"}}
            parent["children"].append(groups[key])
        return groups[key]
    leaf = lambda k: {"id": k, "width": 52, "height": 52, "labels": [{"text": nodes[k]["name"], "width": 140, "height": 30}],
                      "layoutOptions": {"elk.nodeLabels.placement": "OUTSIDE H_CENTER V_BOTTOM"}}
    for k in sorted(vis):
        n = nodes[k]
        if n["kind"] == "vpc":
            group(k, elk)
    for k in sorted(vis):
        n = nodes[k]
        if n["kind"] == "subnet":
            az = group(vpc_of[k] + "/" + n["attributes"]["AvailabilityZone"], groups[vpc_of[k]])
            group(k, az)
    for k in sorted(vis):
        n = nodes[k]
        if n["kind"] in ("vpc", "subnet"): continue
        parent = groups.get(subnet_of.get(k)) or groups.get(vpc_of.get(k)) or elk
        parent["children"].append(leaf(k))
    elk["edges"] = [{"id": e["id"], "sources": [e["source"]], "targets": [e["target"]]} for e in sel["edges"]]
    return elk

json.dump({"demo": export(demo_snapshot(), "vpc-demo"), "scale": export(scale_snapshot(), "vpc-scale-0")}, open(sys.argv[1], "w"))
