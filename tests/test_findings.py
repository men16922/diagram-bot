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
                     ("REL-EFS-ONE-ZONE", "shared-assets"), ("REL-VPN-TUNNEL", "corp-dc-cgw-1"), ("REL-ECS-RUNNING", "data-api")]:
        assert expected in found, expected
    # A read replica is not a single-AZ primary.
    assert ("REL-RDS-SINGLE-AZ", "catalog-db-ro") not in found


def test_edge_address_and_routing_rules_need_their_inputs():
    """WAF, CloudFront origin exposure, idle Elastic IPs and TGW blackhole routes are judged only from collected fields."""
    from fixtures import edge_snapshot
    snapshot = edge_snapshot()
    found = {(f["rule"], f["resource_name"]): f for f in findings(snapshot)}
    edge_rules = {key for key in found if key[0] in ("SEC-ALB-WAF", "SEC-CF-ORIGIN-OPEN", "COST-EIP-IDLE", "REL-TGW-BLACKHOLE")}
    # shop-alb: internet-facing, fronted by CloudFront without a web ACL. web-alb: CloudFront with WAF in front, but its
    # security group still admits 0.0.0.0/0, so the edge can be bypassed (configuration only, no reachability test).
    assert edge_rules == {("SEC-ALB-WAF", "shop-alb"), ("SEC-CF-ORIGIN-OPEN", "web-alb"), ("COST-EIP-IDLE", "203.0.113.82"),
                          ("REL-TGW-BLACKHOLE", "edge-spokes")}
    assert all(found[key]["severity"] == "low" for key in edge_rules) and found[("COST-EIP-IDLE", "203.0.113.82")]["pillar"] == "cost"
    assert "10.99.0.0/16 (static)" in found[("REL-TGW-BLACKHOLE", "edge-spokes")]["evidence"]
    assert "shop.example.org" in found[("SEC-ALB-WAF", "shop-alb")]["evidence"]
    assert "sg-edge-alb: tcp 443-443 ← 0.0.0.0/0" in found[("SEC-CF-ORIGIN-OPEN", "web-alb")]["evidence"]
    # Without a complete regional WAF listing for the ALB's account and region, nothing is said about WAF.
    for coverage in (
            [c for c in snapshot["coverage"] if c["service"] != "wafv2"],
            [{**c, "status": "unavailable"} if c["service"] == "wafv2" and c["parameters"]["Scope"] == "REGIONAL" else c for c in snapshot["coverage"]],
            [{**c, "region": "us-east-1"} if c["service"] == "wafv2" else c for c in snapshot["coverage"]]):
        assert not any(f["rule"] == "SEC-ALB-WAF" for f in review(build_graph(snapshot), coverage))
    # An ALB origin whose security groups were not all collected is not judged.
    snapshot["raw"]["security_groups"] = [g for g in snapshot["raw"]["security_groups"] if g["GroupId"] != "sg-edge-alb"]
    assert not any(f["rule"] == "SEC-CF-ORIGIN-OPEN" for f in findings(snapshot))


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


def gpu_snapshot(train_azs="a", infer_azs="ab", efa_self=True):
    """A small VPC with an EFA training group and a GPU inference group, built like the samples."""
    from diagram_bot.samples import Sample
    s = Sample("gpu", "GPU", [])
    vpc = s.vpc("vpc-gpu", "10.90.0.0/16", "gpu")
    subnets = {az: s.subnet(f"subnet-gpu-{az}", vpc, az, f"10.90.{i}.0/24", f"gpu-{az}", "app") for i, az in enumerate("abc")}
    s.route_table("rtb-gpu", vpc, "gpu", list(subnets.values()))
    s.gateway_endpoint("vpce-gpu-s3", vpc, "s3", ["rtb-gpu"])
    s.security_group("sg-efa", vpc, "efa", [(None, "self")] if efa_self else [(443, "10.90.0.0/16")], egress=("self",))
    s.security_group("sg-infer", vpc, "infer", [(8080, "10.90.0.0/16")])
    s.placement_group("train-pg")
    train = [s.instance(f"i-train{az}", subnets[az], f"train-{az}", "p5.48xlarge", sg="sg-efa", efa=True, placement="train-pg") for az in train_azs]
    infer = [s.instance(f"i-infer{az}", subnets[az], f"infer-{az}", "g6e.2xlarge", sg="sg-infer") for az in infer_azs]
    s.auto_scaling_group("gpu-train", train, [subnets[az] for az in train_azs], minimum=0)
    s.auto_scaling_group("gpu-infer", infer, [subnets[az] for az in infer_azs])
    return s.snapshot()


def test_gpu_placement_rules():
    # Best practice: single-AZ EFA training (low reminder only), multi-AZ inference, self-referencing EFA group.
    good = findings(gpu_snapshot())
    assert [(f["rule"], f["severity"], f["resource_name"]) for f in good if f["rule"] != "OPS-COVERAGE"] == [
        ("REL-ASG-AZ", "low", "gpu-train")]
    found = rules(gpu_snapshot(train_azs="ab", infer_azs="a", efa_self=False))
    assert {("REL-GPU-TRAIN-AZ", "gpu-train"), ("REL-GPU-INFER-AZ", "gpu-infer"), ("SEC-EFA-SG", "gpu-train")} <= found
    # The inference group is not reported twice (generic single-AZ rule).
    assert ("REL-ASG-AZ", "gpu-infer") not in found


def test_ai_review_pack_flags_each_ml_anti_pattern():
    """Every AI/ML rule fires on its anti-pattern and names the lens practice to follow."""
    from diagram_bot.samples import Sample
    s = Sample("ai-bad", "AI anti-patterns", [])
    vpc = s.vpc("vpc-aibad", "10.91.0.0/16", "ai-bad")
    a = s.subnet("subnet-aibad-a", vpc, "a", "10.91.0.0/24", "gpu-a", "app")
    b = s.subnet("subnet-aibad-b", vpc, "b", "10.91.1.0/24", "gpu-b", "app")
    pub = s.subnet("subnet-aibad-pub", vpc, "a", "10.91.9.0/24", "public-a", "public")
    igw = s.internet_gateway("igw-aibad", vpc, "igw")
    s.route_table("rtb-aibad-pub", vpc, "public", [pub], gateway=igw)
    s.security_group("sg-notebook", vpc, "notebook", [(8888, "0.0.0.0/0")])
    infer = s.instance("i-aibadinfer", a, "infer", "g5.xlarge", sg="sg-notebook")
    s.raw["instances"][-1]["PublicIpAddress"] = "203.0.113.50"
    trainers = [s.instance(f"i-aibadtrain{n}", a, f"train-{n}", "p5.48xlarge", efa=True, lifecycle="spot",
                           **{"eks:cluster-name": "ml"}) for n in range(2)]
    s.auto_scaling_group("ml-train", trainers, [a], minimum=0, maximum=10)
    s.auto_scaling_group("ml-infer", [infer], [a, b])
    s.interface_endpoint("vpce-aibad-bedrock", vpc, "bedrock-runtime", [b])
    s.raw["vpc_endpoints"][-1]["PrivateDnsEnabled"] = False
    s.instance("i-aibadclient", b, "client", "m7g.large")
    s.fsx_lustre("fs-0aibad000000001", "scratch", vpc, b, deployment="SCRATCH_2")
    s.sagemaker_endpoint("llm", "ml.g5.2xlarge", 1)
    s.hyperpod("hp", [pub], [("workers", "ml.p5.48xlarge", 4)])
    found = {(f["rule"], f["resource_name"]): f for f in findings(s.snapshot())}
    expected = [("SEC-AI-PORT-OPEN", "notebook"), ("SEC-GPU-PUBLIC", "ml-infer"), ("REL-GPU-SPOT-EFA", "ml-train"),
                ("OPS-GPU-PLACEMENT", "ml-train"), ("REL-GPU-SUBNET-IP", "ml-train"), ("SEC-AI-ENDPOINT-DNS", "bedrock-runtime-endpoint"),
                ("REL-AI-ENDPOINT-AZ", "bedrock-runtime-endpoint"), ("OPS-FSX-SCRATCH", "scratch"), ("REL-FSX-TRAIN-AZ", "ml-train"),
                ("OPS-GPU-S3-ENDPOINT", "ai-bad"), ("SEC-SAGEMAKER-VPC", "llm"), ("REL-SAGEMAKER-AZ", "llm"), ("SEC-HYPERPOD-PUBLIC", "hp")]
    for key in expected:
        assert key in found, key
        assert found[key]["pillar"] == "ai" and found[key]["evidence"] and found[key]["recommendation"]
    # A GPU instance with a public IP is reported once, as the AI rule, not also as the generic low finding.
    assert ("SEC-EC2-PUBLIC-IP", "infer") not in found
