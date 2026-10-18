"""Golden hashes of every generated output: draw.io documents (overview, each VPC's detail, one focus), CloudFormation
templates and review findings, for every reference sample and a few rule-heavy inputs. Collection timestamps are frozen.
Refactors must leave tests/golden.json as it is; a change there means an output changed. When that is intended,
`python tests/test_golden.py` rewrites it (run it with PYTHONHASHSEED values to confirm the hashes do not depend on it)."""
import hashlib
import json
from pathlib import Path

import pytest
from fixtures import edge_snapshot, full_design
from test_findings import gpu_snapshot

from diagram_bot import demo
from diagram_bot.drawio import generate_document
from diagram_bot.findings import review
from diagram_bot.graph import build_graph
from diagram_bot.iac import templates
from diagram_bot.samples import SAMPLES, builder

FROZEN = "2026-01-01T00:00:00.000001+00:00"
FOCUS = ("load_balancer", "transit_gateway", "ec2")


def degraded():
    """data-platform with single-AZ, unencrypted, public and failing resources, so most rules fire."""
    snapshot = SAMPLES["data-platform"]()
    raw = snapshot["raw"]
    next(d for d in raw["db_instances"] if d["DBInstanceIdentifier"] == "catalog-db").update(
        MultiAZ=False, SecondaryAvailabilityZone=None, StorageEncrypted=False, PubliclyAccessible=True)
    raw["replication_groups"][0].update(AutomaticFailover="disabled", TransitEncryptionEnabled=False)
    raw["opensearch_domains"][0]["ClusterConfig"].update(ZoneAwarenessEnabled=False, DedicatedMasterEnabled=False)
    raw["redshift_clusters"][0].update(MultiAZ="Disabled", Encrypted=False)
    raw["efs_file_systems"][0]["AvailabilityZoneName"] = "ap-northeast-2a"
    raw["vpn_connections"][0]["VgwTelemetry"][1]["Status"] = "DOWN"
    raw["ecs_services"][0]["runningCount"] = 1
    return snapshot


def exposed():
    """demo with world-open rules on several protocols and port ranges."""
    snapshot = demo.demo_snapshot()
    snapshot["raw"]["security_groups"][0]["IpPermissions"] += [
        {"IpProtocol": "tcp", "FromPort": 3000, "ToPort": 9000, "Ipv6Ranges": [{"CidrIpv6": "::/0"}]}, {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        {"IpProtocol": "udp", "FromPort": 0, "ToPort": 65535, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}, {"IpProtocol": "6", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}]
    return snapshot


CASES = {**SAMPLES, "demo": demo.demo_snapshot, "edge": edge_snapshot, "design": lambda: full_design().snapshot(), "degraded": degraded,
         "exposed": exposed, "gpu": lambda: gpu_snapshot(train_azs="ab", infer_azs="a", efa_self=False)}


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


def outputs(snapshot):
    graph = build_graph(snapshot)
    vpcs = sorted(n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc")
    candidates = sorted((FOCUS.index(n["kind"]), n["id"]) for n in graph["nodes"] if n["kind"] in FOCUS and not n["reference_only"])
    return {
        "overview": sha(generate_document(snapshot, view="overview")),
        "detail": sha("\n".join(generate_document(snapshot, vpc_id=v, view="detail") for v in vpcs)),
        "focus": sha(generate_document(snapshot, None, True, "focus", candidates[0][1])) if candidates else None,
        "iac": sha(json.dumps(templates(snapshot), ensure_ascii=False)),
        "findings": sha(json.dumps(review(graph, snapshot.get("coverage", [])), ensure_ascii=False)),
    }


GOLDEN = Path(__file__).with_name("golden.json")


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    monkeypatch.setattr(builder, "now", lambda: FROZEN)
    monkeypatch.setattr(demo, "now", lambda: FROZEN)


@pytest.mark.parametrize("key", CASES)
def test_generated_outputs_are_unchanged(key):
    assert outputs(CASES[key]()) == json.loads(GOLDEN.read_text())[key]


if __name__ == "__main__":
    builder.now = demo.now = lambda: FROZEN
    GOLDEN.write_text(json.dumps({key: outputs(case()) for key, case in CASES.items()}, indent=1) + "\n")
