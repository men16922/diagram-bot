import copy
import json
import subprocess
import sys

import pytest

from diagram_bot.demo import demo_snapshot
from diagram_bot.findings import markdown, review, summary
from diagram_bot.graph import build_graph
from diagram_bot.samples import SAMPLES


def findings(snapshot):
    return review(build_graph(snapshot), snapshot["coverage"])


def rules(snapshot):
    return {(f["rule"], f["resource_name"]) for f in findings(snapshot)}


def test_demo_findings_name_resource_evidence_and_fix():
    result = findings(demo_snapshot())
    assert {(f["rule"], f["resource_name"]) for f in result} == {
        ("SEC-SG-OPEN", "demo-web-sg"), ("REL-TG-AZ", "demo-api"), ("REL-TG-AZ", "demo-ip-targets"),
        ("REL-TG-UNHEALTHY", "demo-web"), ("SEC-IMDSV1", "staging-api"), ("SEC-EC2-PUBLIC-IP", "staging-api"),
        ("OPS-COVERAGE", "수집 범위")}
    assert [f["severity"] for f in result] == sorted((f["severity"] for f in result), key=("high", "medium", "low").index)
    assert all(f["evidence"] and f["recommendation"] and f["id"].startswith("f-") for f in result)
    assert summary(result) == {"high": 1, "medium": 4, "low": 2}
    assert result == findings(demo_snapshot())  # deterministic


@pytest.mark.parametrize("key", sorted(SAMPLES))
def test_reference_samples_have_no_high_or_medium_findings(key):
    assert {f["severity"] for f in findings(SAMPLES[key]())} <= {"low"}


def test_single_az_and_unencrypted_resources_are_reported():
    snapshot = SAMPLES["data-platform"]()
    raw = snapshot["raw"]
    db = next(d for d in raw["db_instances"] if d["DBInstanceIdentifier"] == "catalog-db")
    db.update(MultiAZ=False, SecondaryAvailabilityZone=None, StorageEncrypted=False, PubliclyAccessible=True)
    raw["replication_groups"][0].update(AutomaticFailover="disabled", TransitEncryptionEnabled=False)
    raw["opensearch_domains"][0]["ClusterConfig"].update(ZoneAwarenessEnabled=False, DedicatedMasterEnabled=False)
    raw["redshift_clusters"][0].update(MultiAZ="Disabled", Encrypted=False)
    raw["redshift_clusters"][0].pop("MultiAZSecondary")
    raw["efs_file_systems"][0]["AvailabilityZoneName"] = "ap-northeast-2a"
    raw["vpn_connections"][0]["VgwTelemetry"][1]["Status"] = "DOWN"
    raw["ecs_services"][0]["runningCount"] = 1
    found = rules(snapshot)
    for expected in [("REL-RDS-SINGLE-AZ", "catalog-db"), ("SEC-RDS-PUBLIC", "catalog-db"), ("SEC-ENCRYPTION", "catalog-db"),
                     ("REL-CACHE-FAILOVER", "session-cache"), ("SEC-TRANSIT", "session-cache"), ("REL-SEARCH-AZ", "logs-search"),
                     ("REL-SEARCH-MASTER", "logs-search"), ("REL-REDSHIFT-AZ", "warehouse"), ("SEC-ENCRYPTION", "warehouse"),
                     ("REL-EFS-ONE-ZONE", "shared-assets"), ("REL-VPN-TUNNEL", "corp-dc-cgw"), ("REL-ECS-RUNNING", "data-api")]:
        assert expected in found, expected
    # A read replica is not a single-AZ primary.
    assert ("REL-RDS-SINGLE-AZ", "catalog-db-ro") not in found


def test_security_group_exposure_rules():
    snapshot = demo_snapshot()
    groups = snapshot["raw"]["security_groups"]
    groups[0]["IpPermissions"] += [{"IpProtocol": "tcp", "FromPort": 3000, "ToPort": 3400, "Ipv6Ranges": [{"CidrIpv6": "::/0"}]},
                                   {"IpProtocol": "-1", "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
                                   {"IpProtocol": "tcp", "FromPort": 5432, "ToPort": 5432, "IpRanges": [{"CidrIp": "10.0.0.0/8"}]}]
    titles = {f["title"] for f in findings(snapshot) if f["rule"] == "SEC-SG-OPEN" and f["resource_name"] == "demo-alb-sg"}
    # 3000-3400 covers MySQL 3306 and RDP 3389; private CIDRs and HTTPS stay quiet.
    assert titles == {"인터넷(0.0.0.0/0)에 MySQL 3306 허용", "인터넷(0.0.0.0/0)에 RDP 3389 허용", "인터넷(0.0.0.0/0)에 모든 포트 허용"}


def test_missing_input_reports_nothing_instead_of_guessing():
    snapshot = copy.deepcopy(demo_snapshot())
    snapshot["raw"] = {"vpcs": snapshot["raw"]["vpcs"]}
    snapshot["coverage"] = []
    assert findings(snapshot) == []
    assert "해당 항목 없음" in "\n".join(markdown([]))


def test_cli_review_exit_code_gates_on_severity(tmp_path):
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(demo_snapshot()), encoding="utf-8")
    run = lambda *args: subprocess.run([sys.executable, "-m", "diagram_bot.cli", "review", str(path), *args], capture_output=True, text=True)
    high = run("--fail-on", "high", "--format", "json")
    assert high.returncode == 3 and json.loads(high.stdout)["summary"]["high"] == 1
    assert run().returncode == 0 and "SEC-SG-OPEN" in run().stdout
    clean = tmp_path / "clean.json"
    clean.write_text(json.dumps(SAMPLES["three-tier"]()), encoding="utf-8")
    assert subprocess.run([sys.executable, "-m", "diagram_bot.cli", "review", str(clean), "--fail-on", "low"]).returncode == 0
