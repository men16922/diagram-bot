"""Invariants every reference sample and every design keeps: addresses inside their subnet, unique IDs, the collector's
raw collections, and designer steps that apply completely or not at all."""
import ipaddress
import json
import re

import pytest
from fixtures import full_design

from diagram_bot.collector import OPERATIONS
from diagram_bot.designer import STANDARDS, Design, DesignError
from diagram_bot.graph import build_graph
from diagram_bot.samples import DETAILS, LISTINGS, OPTIONAL, SAMPLES, SUMMARIES, Sample


def parts(snapshot):
    return snapshot.get("parts") or [snapshot]


def addresses(raw):
    """(owner, subnet, private IP) of every instance, ECS task ENI and EFS mount target."""
    found = [(i["InstanceId"], i["SubnetId"], i["PrivateIpAddress"]) for i in raw["instances"]]
    for task in raw["ecs_tasks"]:
        details = {d["name"]: d["value"] for a in task["attachments"] for d in a["details"]}
        found.append((task["taskArn"], details["subnetId"], details["privateIPv4Address"]))
    found += [(m["MountTargetId"], m["SubnetId"], m["IpAddress"]) for m in raw["efs_mount_targets"]]
    return found


def assert_addresses_valid(raw):
    cidr = {s["SubnetId"]: ipaddress.ip_network(s["CidrBlock"]) for s in raw["subnets"]}
    seen = {}
    for owner, subnet, ip in addresses(raw):
        network, address = cidr[subnet], ipaddress.ip_address(ip)
        # Inside the subnet and not one of the five addresses AWS reserves.
        assert address in network and 4 <= int(address) - int(network.network_address) < network.num_addresses - 1, (owner, ip, network)
        assert seen.setdefault(ip, owner) == owner, f"{ip} used by {seen[ip]} and {owner}"


def assert_unique_ids(snapshot):
    for part in parts(snapshot):
        raw = part["raw"]
        for key, field in (("vpcs", "VpcId"), ("subnets", "SubnetId"), ("instances", "InstanceId"), ("auto_scaling_groups", "AutoScalingGroupName"),
                           ("db_instances", "DBInstanceIdentifier"), ("db_clusters", "DBClusterIdentifier"), ("eks_clusters", "name"),
                           ("load_balancers", "LoadBalancerArn"), ("target_groups", "TargetGroupArn"), ("route_tables", "RouteTableId"),
                           ("security_groups", "GroupId"), ("vpc_endpoints", "VpcEndpointId"), ("ecs_services", "serviceArn"),
                           ("ecs_tasks", "taskArn"), ("vpn_connections", "VpnConnectionId"), ("replication_groups", "ReplicationGroupId"),
                           ("msk_clusters", "ClusterArn"), ("lambda_functions", "FunctionName"), ("efs_file_systems", "FileSystemId")):
            ids = [item[field] for item in raw[key]]
            assert len(ids) == len(set(ids)), (key, sorted(i for i in ids if ids.count(i) > 1))
    ids = [n["id"] for n in build_graph(snapshot)["nodes"]]
    assert len(ids) == len(set(ids))


@pytest.mark.parametrize("key", SAMPLES)
def test_sample_addresses_sit_in_their_subnet_and_ids_are_unique(key):
    snapshot = SAMPLES[key]()
    for part in parts(snapshot):
        assert_addresses_valid(part["raw"])
    assert_unique_ids(snapshot)


def test_design_addresses_sit_in_their_subnet_and_ids_are_unique():
    d = full_design()
    d.add_ec2_service("shop", "batch", tier="web", per_az=3)  # shares the web subnets with an earlier service
    d.add_ecs_service("shop", "billing", 3)
    d.add_eks_cluster("shop", "jobs", nodes_per_az=2)
    assert_addresses_valid(d.snapshot()["raw"])
    assert_unique_ids(d.snapshot())


def test_sample_raw_and_coverage_follow_the_collector_operations():
    names = [name for *_, name in OPERATIONS]
    assert set(LISTINGS) <= set(names) and OPTIONAL <= set(names)
    raw = Sample("x", "x", []).raw
    # Every collection the collector keeps (listings become their detail collections), plus the detail calls.
    assert {LISTINGS.get(name, name) for name in names} - OPTIONAL <= set(raw)
    assert {"target_health", *(key for *_, key in DETAILS)} <= set(raw)
    assert not OPTIONAL & set(raw)
    calls = {(c["service"], c["operation"]) for c in SAMPLES["three-tier"]()["coverage"]}
    assert {(service, operation) for service, operation, _, name in OPERATIONS if name not in OPTIONAL} <= calls
    assert {(service, operation) for service, operation, _ in DETAILS} <= calls


def test_every_sample_has_one_summary_and_single_account_ones_are_standards():
    assert list(SUMMARIES) == list(SAMPLES)
    assert set(STANDARDS) == set(SAMPLES) - {"multi-account"} and all(STANDARDS[k] == SUMMARIES[k] for k in STANDARDS)


def test_snapshot_is_a_copy_and_counts_free_addresses_each_time():
    d = Design()
    d.add_vpc("shop", "10.20.0.0/16", 2, ["public", "app", "data"])
    first = d.snapshot()
    first["raw"]["vpcs"].clear()
    assert d.sample.raw["vpcs"]
    free = lambda: {s["SubnetId"]: s["AvailableIpAddressCount"] for s in d.snapshot()["raw"]["subnets"]}
    before = free()
    d.add_eks_cluster("shop", "jobs", nodes_per_az=3)
    after = free()
    assert after["subnet-shop-app-a"] == before["subnet-shop-app-a"] - 3


def test_brownfield_subnet_count_keeps_aws_value_minus_new_instances():
    snapshot = SAMPLES["three-tier"]()
    for subnet in snapshot["raw"]["subnets"]:
        subnet["AvailableIpAddressCount"] = 100
    d = Design()
    d.start_from_snapshot(snapshot)
    d.add_eks_cluster("prod-web", "jobs", nodes_per_az=2)
    counts = {s["SubnetId"]: s["AvailableIpAddressCount"] for s in d.snapshot()["raw"]["subnets"]}
    assert counts["subnet-app-a"] == 98 and counts["subnet-pub-a"] == 100


def state(d):
    snapshot = d.snapshot()
    text = json.dumps({"raw": snapshot["raw"], "steps": d.steps, "vpcs": d.vpcs, "lbs": d.load_balancers, "tgs": d.target_groups},
                      sort_keys=True, default=sorted)
    return re.sub(r"20\d\d-\d\d-\d\dT[0-9:.]+\+00:00", "", text)


def test_over_capacity_vpc_is_rejected_without_a_trace():
    d = Design()
    before = state(d)
    with pytest.raises(DesignError, match="/24 서브넷이 16개뿐"):
        d.add_vpc("big", "10.0.0.0/20", azs=6, tiers=("public", "web", "app", "data"))
    assert state(d) == before
    d.add_vpc("big", "10.0.0.0/20", azs=4, tiers=("public", "web", "app", "data"))
    assert [v["VpcId"] for v in d.sample.raw["vpcs"]] == ["vpc-big"]


def test_tasks_beyond_the_subnet_are_rejected_without_a_trace():
    s = Sample("small", "small", [])
    vpc = s.vpc("vpc-small", "10.9.0.0/16", "small")
    for i, az in enumerate("ab"):
        s.subnet(f"subnet-small-{az}", vpc, az, f"10.9.0.{16 * i}/28", f"app-{az}", "app")
    s.route_table("rtb-small", vpc, "small", main=True)
    d = Design()
    d.start_from_snapshot(s.snapshot())
    before = state(d)
    with pytest.raises(DesignError, match="주소가 부족"):
        d.add_ecs_service("small", "api", tasks_per_az=20)
    assert state(d) == before
    # A /28 holds 11 addresses: five tasks per subnet leave six, too few for ten instances.
    assert d.add_ecs_service("small", "api", tasks_per_az=5)["tasks"] == 10
    before = state(d)
    with pytest.raises(DesignError, match="주소가 부족"):
        d.add_ec2_service("small", "web", per_az=10)
    assert state(d) == before
    d.add_ec2_service("small", "web", per_az=6)
    assert_addresses_valid(d.snapshot()["raw"])


def test_a_step_failing_midway_leaves_the_design_unchanged(monkeypatch):
    d = Design()
    d.add_vpc("shop", "10.20.0.0/16", 2)
    d.add_load_balancer("shop", "alb")
    before = state(d)

    def broken(*args, **kwargs):
        raise KeyError("boom")
    monkeypatch.setattr(Sample, "auto_scaling_group", broken)
    with pytest.raises(DesignError, match="add_ec2_service.*바뀌지 않았습니다"):
        d.add_ec2_service("shop", "web", load_balancer="alb")
    assert state(d) == before
    monkeypatch.undo()
    assert d.add_ec2_service("shop", "web", load_balancer="alb")["instances"] == 2


@pytest.mark.parametrize("step", [
    lambda d: d.add_ec2_service("shop", "web"),
    lambda d: d.add_ecs_service("shop", "orders"),
    lambda d: d.add_eks_cluster("shop", "k8s"),
    lambda d: d.add_database("shop", "db"),
    lambda d: d.add_aurora("shop", "aurora"),
    lambda d: d.add_cache("shop", "cache"),
    lambda d: d.add_kafka("shop", "events"),
    lambda d: d.add_opensearch("shop", "logs"),
    lambda d: d.add_file_system("shop", "files"),
    lambda d: d.add_lambda("worker", vpc="shop"),
    lambda d: d.add_sagemaker_endpoint("shop", "ranker"),
    lambda d: d.add_fsx_lustre("shop", "scratch"),
    lambda d: d.add_vpn("shop", name="dc"),
    lambda d: d.connect_vpcs("shop", "other"),
])
def test_duplicate_names_are_rejected(step):
    d = Design()
    d.add_vpc("shop", "10.20.0.0/16", 2, ["public", "app", "data"])
    d.add_vpc("other", "10.30.0.0/16", 2, ["public", "app", "data"])
    step(d)
    before = state(d)
    with pytest.raises(DesignError, match="이미 (있는 이름|피어링된)"):
        step(d)
    assert state(d) == before


def test_existing_endpoints_are_kept_not_duplicated():
    d = Design()
    d.add_vpc("shop", "10.20.0.0/16", 2)
    d.add_vpc_endpoints("shop", ["s3", "ssm"])
    assert d.add_vpc_endpoints("shop", ["s3", "sts", "sts"]) == {"endpoints": ["sts"], "existing": ["s3"]}
    assert len(d.sample.raw["vpc_endpoints"]) == 3


def test_brownfield_vpcs_with_the_same_long_name_stay_apart():
    name = "production-shared-services-network-for-the-platform"
    s = Sample("brown", "brown", [])
    for n, cidr in ((1, "10.1.0.0/16"), (2, "10.2.0.0/16")):
        vpc = s.vpc(f"vpc-0000000000000{n}abc{n}", cidr, name)
        s.subnet(f"subnet-{n}", vpc, "a", cidr.replace("0.0/16", "1.0/24"), "app-a", "app")
    d = Design()
    keys = [v["name"] for v in d.start_from_snapshot(s.snapshot())["vpcs"]]
    assert len(set(keys)) == 2 and keys[1].endswith("-02abc2")
    assert {d.vpc(key)["id"] for key in keys} == {"vpc-00000000000001abc1", "vpc-00000000000002abc2"}
