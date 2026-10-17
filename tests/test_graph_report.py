import copy
import csv
import json
import random
import re

from diagram_bot.demo import TG_WEB, demo_snapshot
from diagram_bot.graph import build_graph, upgrade_graph
from diagram_bot.report import write_report
from diagram_bot.views import view_graph


def registered(graph, target_id):
    return [e for e in graph["edges"] if e["relation"] == "registered_target" and e["target"].endswith(target_id)]


def test_explicit_relationships_and_multisubnet_lb():
    graph = build_graph(demo_snapshot())
    nodes = {n["id"]: n for n in graph["nodes"]}
    lb = next(n for n in nodes.values() if n["name"] == "demo-alb")
    placements = [e for e in graph["edges"] if e["source"] == lb["id"] and e["relation"] == "in_subnet"]
    assert sorted(e["qualifiers"]["availability_zone"] for e in placements) == ["ap-northeast-2a", "ap-northeast-2c"]
    assert all(e["source"] in nodes and e["target"] in nodes and not e["inferred"] for e in graph["edges"])
    assert all(n["reference_only"] for n in nodes.values() if n["kind"] in ("security_group", "volume", "network_interface"))
    assert not any(e["relation"] in ("communicates_with", "allows_traffic") for e in graph["edges"])
    assert build_graph(demo_snapshot())["edges"] == graph["edges"]
    assert len({e["id"] for e in graph["edges"]}) == len(graph["edges"])


def test_relation_ids_ignore_input_order_and_health():
    snapshot = demo_snapshot()
    baseline = {e["id"]: (e["source"], e["relation"], e["target"]) for e in build_graph(snapshot)["edges"]}
    shuffled = copy.deepcopy(snapshot)
    rng = random.Random(7)
    for rows in shuffled["raw"].values():
        rng.shuffle(rows)
    for result in shuffled["raw"]["target_health"]:
        rng.shuffle(result["Descriptions"])
        for description in result["Descriptions"]:
            description["TargetHealth"] = {"State": "draining"}
    graph = build_graph(shuffled)
    assert {e["id"]: (e["source"], e["relation"], e["target"]) for e in graph["edges"]} == baseline
    assert [e["id"] for e in graph["edges"]] == sorted(baseline, key=lambda k: (*baseline[k], k))


def test_same_instance_on_two_ports_is_two_relations():
    graph = build_graph(demo_snapshot())
    web = [e for e in registered(graph, "/ec2/i-demo-a") if e["source"].endswith(TG_WEB)]
    assert sorted(e["qualifiers"]["port"] for e in web) == [443, 8443]
    assert len({e["id"] for e in web}) == 2
    duplicated = demo_snapshot()
    duplicated["raw"]["target_health"][0]["Descriptions"].append(copy.deepcopy(duplicated["raw"]["target_health"][0]["Descriptions"][0]))
    assert len(build_graph(duplicated)["edges"]) == len(graph["edges"])


def test_legacy_graph_upgrade_keeps_order_and_matches_new_ids():
    graph = build_graph(demo_snapshot())
    legacy = copy.deepcopy(graph)
    for edge in legacy["edges"]:
        del edge["id"], edge["qualifiers"]
    legacy["edges"].reverse()
    upgraded = upgrade_graph(legacy)
    assert [e["id"] for e in upgraded["edges"]] == [e["id"] for e in reversed(graph["edges"])]


def test_ip_target_is_not_guessed_to_be_ec2():
    graph = build_graph(demo_snapshot())
    edge = registered(graph, "#10.20.11.25:9000:ap-northeast-2a")[0]
    target = next(n for n in graph["nodes"] if n["id"] == edge["target"])
    assert target["kind"] == "registered_target" and not target["reference_only"]
    batch = next(n for n in graph["nodes"] if n["resource_id"] == "i-demo-batch")
    assert batch["attributes"]["PrivateIpAddress"] == "10.20.11.25"
    assert not any(e["target"] == batch["id"] for e in graph["edges"] if e["relation"] == "registered_target")


def test_missing_instance_stays_reference():
    graph = build_graph(demo_snapshot())
    missing = next(n for n in graph["nodes"] if n["resource_id"] == "i-demo-missing")
    assert missing["reference_only"] and registered(graph, "/ec2/i-demo-missing")
    assert not any(e["relation"] == "in_subnet" and e["source"] == missing["id"] for e in graph["edges"])
    snapshot = demo_snapshot()
    snapshot["raw"]["instances"] = []
    assert all(n["reference_only"] for n in build_graph(snapshot)["nodes"] if n["kind"] == "ec2")


def test_view_contract_counts_and_scope():
    graph = build_graph(demo_snapshot())
    nodes = {n["id"]: n for n in graph["nodes"]}
    detail = view_graph(graph, "detail", "vpc-demo")
    kinds = {nodes[k]["resource_id"] for k in detail["nodes"]}
    assert {"i-demo-missing", "vpc-demo"} <= kinds and "i-staging" not in kinds and "sg-web" not in kinds
    assert "sg-web" in {nodes[k]["resource_id"] for k in view_graph(graph, "detail", "vpc-demo", references=True)["nodes"]}
    assert all(e["relation"] in ("registered_target", "associated_load_balancer") for e in detail["edges"])
    counts = detail["counts"]
    assert counts["visible"] + counts["hidden"] == len(graph["nodes"]) == counts["collected"] + counts["references"]
    focus = view_graph(graph, "focus", resource="i-demo-a")
    assert {e["relation"] for e in focus["edges"]} == {"registered_target"} and len(focus["edges"]) == 2
    assert {nodes[k]["kind"] for k in focus["nodes"]} == {"ec2", "target_group", "subnet", "vpc"}
    alb = view_graph(graph, "focus", resource=next(k for k, n in nodes.items() if n["name"] == "demo-alb"))
    assert sum(e["relation"] == "in_subnet" for e in alb["edges"]) == 2
    assert view_graph(graph)["view"]["kind"] == "overview"


def test_html_json_and_csv_share_data_and_escape_resource_tags(tmp_path):
    snapshot = demo_snapshot()
    malicious = '</script><script>window.injected=true</script>'
    snapshot["raw"]["vpcs"][0]["Tags"][0]["Value"] = malicious
    snapshot["raw"]["instances"][0]["Tags"][0]["Value"] = '=HYPERLINK("https://example.invalid")'
    write_report(snapshot, tmp_path)
    document = (tmp_path / "index.html").read_text()
    assert malicious not in document
    embedded = re.search(r'<script id="snapshot" type="application/json">(.*?)</script>', document, re.S)[1]
    data = json.loads(embedded)
    assert data == json.loads((tmp_path / "snapshot.json").read_text())
    with (tmp_path / "inventory.csv").open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    assert len(rows) == len(data["graph"]["nodes"])
    ec2 = next(r for r in rows if r["resource_id"] == "i-demo-a")
    assert ec2["name"].startswith("'=")
    assert all(r["collection_status"] == "partial" for r in rows)
    review = (tmp_path / "review.md").read_text()
    assert "UnauthorizedOperation" in review and "ec2:DescribeInstances" in review
