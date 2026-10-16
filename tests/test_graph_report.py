import csv
import json
import re

from ta_bot.demo import demo_snapshot
from ta_bot.graph import build_graph
from ta_bot.report import write_report


def test_explicit_relationships_and_multisubnet_lb():
    graph = build_graph(demo_snapshot())
    nodes = {n["id"]: n for n in graph["nodes"]}
    lb = next(n for n in nodes.values() if n["kind"] == "load_balancer")
    assert len([e for e in graph["edges"] if e["source"] == lb["id"] and e["relation"] == "in_subnet"]) == 2
    assert all(e["source"] in nodes and e["target"] in nodes and not e["inferred"] for e in graph["edges"])
    assert all(n["reference_only"] for n in nodes.values() if n["kind"] in ("security_group", "volume", "network_interface"))
    assert not any(e["relation"] in ("communicates_with", "allows_traffic") for e in graph["edges"])
    assert build_graph(demo_snapshot())["edges"] == graph["edges"]


def test_ip_target_is_not_guessed_to_be_ec2():
    snapshot = demo_snapshot()
    snapshot["raw"]["target_groups"][0]["TargetType"] = "ip"
    snapshot["raw"]["instances"][0]["PrivateIpAddress"] = "10.20.1.10"
    snapshot["raw"]["target_health"][0]["Descriptions"] = [{"Target": {"Id": "10.20.1.10", "Port": 443}, "TargetHealth": {"State": "healthy"}}]
    graph = build_graph(snapshot)
    edge = next(e for e in graph["edges"] if e["relation"] == "registered_target")
    target = next(n for n in graph["nodes"] if n["id"] == edge["target"])
    assert target["kind"] == "registered_target" and not target["reference_only"]


def test_missing_instance_stays_reference():
    snapshot = demo_snapshot()
    snapshot["raw"]["instances"] = []
    graph = build_graph(snapshot)
    assert all(n["reference_only"] for n in graph["nodes"] if n["kind"] == "ec2")
    assert not any(e["relation"] == "in_subnet" and "/ec2/" in e["source"] for e in graph["edges"])


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
    assert "UnauthorizedOperation" in (tmp_path / "review.md").read_text()
