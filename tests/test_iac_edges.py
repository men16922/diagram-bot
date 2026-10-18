"""CloudFormation export edge cases: logical ID collisions and fields AWS may report empty (audit items 2.15, 2.16)."""
import copy

from test_iac import assert_self_contained

from diagram_bot.iac import template
from diagram_bot.samples import SAMPLES


def with_cluster_enabled_cache():
    for build in SAMPLES.values():
        snapshot = build()
        if any(g.get("ClusterEnabled") for g in snapshot.get("raw", {}).get("replication_groups", [])):
            return snapshot
    raise AssertionError("no sample has a cluster-mode cache")


def test_a_name_that_ends_like_a_derived_id_does_not_overwrite_a_resource():
    snapshot = SAMPLES["three-tier"]()
    raw = snapshot["raw"]
    [gateway] = raw["internet_gateways"]
    twin = copy.deepcopy(gateway)
    twin["InternetGatewayId"] = "igw-0twin"
    twin["Tags"] = [{"Key": "Name", "Value": "edge-attachment"}]
    gateway["Tags"] = [{"Key": "Name", "Value": "edge"}]
    raw["internet_gateways"] = [twin, gateway]  # "edge-attachment" first takes InternetGatewayEdgeAttachment
    result = template(snapshot)
    resources = result["Resources"]
    gateways = [k for k, v in resources.items() if v["Type"] == "AWS::EC2::InternetGateway"]
    attachments = [k for k, v in resources.items() if v["Type"] == "AWS::EC2::VPCGatewayAttachment"
                   and v["Properties"].get("InternetGatewayId")]
    assert len(gateways) == 2 and len(attachments) == 2
    assert {resources[k]["Properties"]["InternetGatewayId"]["Ref"] for k in attachments} == set(gateways)
    assert_self_contained(result, raw)


def test_null_kafka_version_and_unreported_node_groups_still_export():
    snapshot = SAMPLES["eks-platform"]()
    for cluster in snapshot["raw"]["msk_clusters"]:
        cluster["Provisioned"]["CurrentBrokerSoftwareInfo"] = {"KafkaVersion": None}
    result = template(snapshot)
    assert any(v["Type"] == "AWS::MSK::Cluster" for v in result["Resources"].values())
    snapshot = with_cluster_enabled_cache()
    for group in snapshot["raw"]["replication_groups"]:
        group["NodeGroups"] = []
    result = template(snapshot)
    caches = [v["Properties"] for v in result["Resources"].values() if v["Type"] == "AWS::ElastiCache::ReplicationGroup"]
    assert caches and all("NumNodeGroups" not in c for c in caches)
    assert_self_contained(result, snapshot["raw"])
