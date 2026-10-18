import pytest
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
    assert all(n["reference_only"] for n in nodes.values() if n["kind"] in ("volume", "network_interface"))
    # Security groups are collected for their rules (review) but still drawn only as references.
    assert {n["source"] for n in nodes.values() if n["kind"] == "security_group"} == {"DescribeSecurityGroups"}
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


def test_route_filter_narrows_detail_to_one_path():
    """A detail view filtered to one target group or load balancer: only its routes, others hidden and counted."""
    from diagram_bot.demo import ALB
    graph = build_graph(demo_snapshot())
    nodes = {n["id"]: n for n in graph["nodes"]}
    full = view_graph(graph, "detail", "vpc-demo")
    web = view_graph(graph, "detail", "vpc-demo", routes=TG_WEB)
    tg = next(k for k, n in nodes.items() if n["resource_id"] == TG_WEB)
    assert web["view"]["routes"] == tg and web["routes"]["kind"] == "target_group" and web["routes"]["target_groups"] == 1
    assert web["edges"] and all(e["source"] == tg for e in web["edges"])
    assert {e["id"] for e in web["edges"]} < {e["id"] for e in full["edges"]}
    # Honest counts: everything not shown is in the hidden count, and the filter says how much of it is its own doing.
    counts = web["counts"]
    assert counts["visible"] + counts["hidden"] == len(graph["nodes"]) == len(web["nodes"]) + counts["hidden"]
    assert counts["hidden"] - full["counts"]["hidden"] == counts["filtered"] == web["routes"]["hidden"] > 0
    assert web["routes"]["hidden_relations"] == len(full["edges"]) - len(web["edges"])
    # Only other load balancing (load balancers, target groups, their IP and unqueried targets) is hidden; placed resources stay.
    gone = set(full["nodes"]) - set(web["nodes"])
    assert all(nodes[k]["kind"] in ("load_balancer", "target_group", "registered_target") or nodes[k]["reference_only"] for k in gone)
    assert {k for k in full["nodes"] if nodes[k]["kind"] in ("ec2", "subnet") and not nodes[k]["reference_only"]} <= set(web["nodes"])
    assert set(web["routes"]["path"]) <= set(web["nodes"]) and tg in web["routes"]["path"]
    # A node ID addresses the same filter; a load balancer brings all of its target groups.
    assert view_graph(graph, "detail", "vpc-demo", routes=tg)["nodes"] == web["nodes"]
    alb = view_graph(graph, "detail", "vpc-demo", routes=ALB)
    groups = [g for g in demo_snapshot()["raw"]["target_groups"] if ALB in g["LoadBalancerArns"]]
    assert alb["routes"]["target_groups"] == len(groups) and {e["id"] for e in web["edges"]} <= {e["id"] for e in alb["edges"]}
    for kwargs in ({"view": "focus", "resource": "i-demo-a"}, {"view": "detail", "vpc": "vpc-staging"}, {"view": "overview"}):
        with pytest.raises(ValueError):
            view_graph(graph, routes=TG_WEB, **kwargs)
    with pytest.raises(ValueError, match="로드 밸런서나 대상 그룹"):
        view_graph(graph, "detail", "vpc-demo", routes="i-demo-a")


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


def test_service_placement_and_high_availability_roles():
    from diagram_bot.samples import SAMPLES
    snapshot = SAMPLES["data-platform"]()
    graph = build_graph(snapshot)
    nodes = {n["id"]: n for n in graph["nodes"]}
    by_kind = lambda kind: [n for n in graph["nodes"] if n["kind"] == kind]
    # OpenSearch: one node per subnet with the per-AZ counts from DescribeDomainNodes; AZ c is the standby.
    search = {n["attributes"]["AvailabilityZone"][-1]: n["attributes"] for n in by_kind("search_node")}
    assert {az: (a["Data"], a["Master"], a["Standby"]) for az, a in search.items()} == {
        "a": (2, 1, False), "b": (2, 1, False), "c": (2, 1, True)}
    # ECS: the target group's IP targets are the service's tasks, not separate addresses.
    tasks = by_kind("ecs_task")
    registered = [e for e in graph["edges"] if e["relation"] == "registered_target" and nodes[e["target"]]["kind"] == "ecs_task"]
    assert len(tasks) == 5 and len(registered) == 3 and not by_kind("registered_target")
    assert sum(e["relation"] == "service_task" for e in graph["edges"]) == 5
    # RDS Multi-AZ primary/standby plus a read replica; Redis cluster mode shards; Redshift Multi-AZ.
    roles = {n["attributes"]["DBInstanceIdentifier"]: n.get("db_role") for n in by_kind("db_instance")}
    assert roles["catalog-db-ro"] == "read replica" and roles["profiles-docdb-1"] == "writer" and roles["profiles-docdb-2"] == "reader"
    assert any(e["relation"] == "read_replica_of" for e in graph["edges"]) and by_kind("db_standby")
    assert sorted((n["attributes"]["Shard"], n["attributes"]["CurrentRole"]) for n in by_kind("cache_node")) == [
        ("0001", "primary"), ("0001", "replica"), ("0002", "primary"), ("0002", "replica")]
    assert sorted(n["attributes"]["Role"] for n in by_kind("redshift_cluster")) == ["primary", "secondary"]
    # EFS mount target per AZ; one Transit Gateway joins both VPCs and terminates two VPNs (two customer gateways).
    assert len(by_kind("efs_mount_target")) == 3
    assert not by_kind("vpc_peering") and not by_kind("vpn_gateway")
    # VPN attachments are known only by reference, from the TGW route table APIs.
    assert {n["attributes"]["VpcId"] for n in by_kind("tgw_attachment") if not n["reference_only"]} == {"vpc-0a1b2c3d4e5f60007", "vpc-0a1b2c3d4e5f60008"}
    vpns = [e for e in graph["edges"] if e["relation"] == "vpn_connection"]
    assert sorted(nodes[e["target"]]["attributes"]["IpAddress"] for e in vpns) == ["198.51.100.20", "203.0.113.20"]
    assert all(e["details"]["Tunnels"] == "2/2 UP" for e in vpns)
    for e in vpns:
        assert e["target"] in view_graph(graph, "detail", "vpc-0a1b2c3d4e5f60008")["nodes"]


def test_peering_and_virtual_private_gateway_vpn():
    """Peering and a VGW-terminated VPN (the chat designer's connect_vpcs/add_vpn) stay covered without a sample."""
    from fixtures import full_design
    snapshot = full_design().snapshot()
    graph = build_graph(snapshot)
    nodes = {n["id"]: n for n in graph["nodes"]}
    vpcs = {n["name"]: n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc"}
    peering = next(n["id"] for n in graph["nodes"] if n["kind"] == "vpc_peering")
    for vpc in vpcs.values():
        assert peering in view_graph(graph, "detail", vpc)["nodes"]
    vpn = next(e for e in graph["edges"] if e["relation"] == "vpn_connection")
    assert vpn["details"]["Tunnels"] == "2/2 UP" and nodes[vpn["target"]]["attributes"]["IpAddress"] == "198.51.100.7"
    assert vpn["target"] in view_graph(graph, "detail", vpcs["analytics"])["nodes"]
    # A default route to the virtual private gateway classifies the subnet as private (VGW).
    table = next(t for t in snapshot["raw"]["route_tables"] if t["RouteTableId"] == "rtb-analytics-main")
    vgw = snapshot["raw"]["vpn_gateways"][0]["VpnGatewayId"]
    table["Routes"].append({"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": vgw, "State": "active"})
    classes = {n.get("route_class") for n in build_graph(snapshot)["nodes"] if n["kind"] == "subnet" and n["attributes"]["VpcId"] == vpcs["analytics"]}
    assert classes == {"vgw"}


def test_msk_brokers_follow_list_nodes():
    from diagram_bot.samples import SAMPLES
    graph = build_graph(SAMPLES["eks-platform"]())
    brokers = sorted((n["attributes"]["BrokerIds"], n["attributes"]["Estimated"]) for n in graph["nodes"] if n["kind"] == "msk_broker")
    assert brokers == [([1], False), ([2], False), ([3], False)]
    # A partial ListNodes answer (broker replacement) must not fail the whole graph: the unnamed subnet is estimated.
    snapshot = SAMPLES["eks-platform"]()
    snapshot["raw"]["msk_nodes"] = snapshot["raw"]["msk_nodes"][:2]
    brokers = sorted((n["attributes"]["BrokerIds"], n["attributes"]["Brokers"], n["attributes"]["Estimated"])
                     for n in build_graph(snapshot)["nodes"] if n["kind"] == "msk_broker")
    assert brokers == [([], 0, True), ([1], 1, False), ([2], 1, False)]


def test_optional_best_practice_items():
    """docs/ARCHITECTURE.md §4 #7: microservices in 3 AZs, EKS /28 cluster subnets and a third Aurora AZ, central egress VPC."""
    import ipaddress

    from diagram_bot.samples import SAMPLES
    zones = lambda raw: {s["SubnetId"]: s["AvailabilityZone"] for s in raw["subnets"]}
    raw = SAMPLES["microservices"]()["raw"]
    assert all(len({zones(raw)[s] for s in g["VPCZoneIdentifier"].split(",")}) == 3 for g in raw["auto_scaling_groups"])
    assert len({t["availabilityZone"] for t in raw["ecs_tasks"]}) == 3 and len(raw["nat_gateways"]) == 3
    raw = SAMPLES["eks-platform"]()["raw"]
    cidr = {s["SubnetId"]: s["CidrBlock"] for s in raw["subnets"]}
    cluster = raw["eks_clusters"][0]["resourcesVpcConfig"]["subnetIds"]
    assert [ipaddress.ip_network(cidr[s]).prefixlen for s in cluster] == [28] * 3 and len({zones(raw)[s] for s in cluster}) == 3
    assert not set(cluster) & {s for g in raw["auto_scaling_groups"] for s in g["VPCZoneIdentifier"].split(",")}
    assert sorted(d["AvailabilityZone"][-1] for d in raw["db_instances"] if d.get("DBClusterIdentifier") == "platform-aurora") == ["a", "b", "c"]
    snapshot = SAMPLES["environments"]()
    raw, graph = snapshot["raw"], build_graph(snapshot)
    names = {v["VpcId"]: next(t["Value"] for t in v["Tags"] if t["Key"] == "Name") for v in raw["vpcs"]}
    # NAT gateways only in the egress VPC (one per AZ); the environments send 0.0.0.0/0 to the Transit Gateway.
    assert sorted((names[n["VpcId"]], n["SubnetId"][-1]) for n in raw["nat_gateways"]) == [("egress-vpc", az) for az in "abc"]
    classes = {}
    for n in graph["nodes"]:
        if n["kind"] == "subnet":
            classes.setdefault(names[n["attributes"]["VpcId"]], set()).add(n.get("route_class"))
    assert classes == {"prod-vpc": {"public", "tgw"}, "staging-vpc": {"public", "tgw"}, "egress-vpc": {"public", "nat"},
                       "shared-services": {"isolated"}}
    returns = next(t for t in raw["route_tables"] if t["RouteTableId"] == "rtb-egress-public")["Routes"]
    assert {"DestinationCidrBlock": "10.0.0.0/8", "TransitGatewayId": "tgw-corp0000000004", "State": "active"} in returns
    assert sorted(names[a["VpcId"]] for a in raw["transit_gateway_attachments"]) == ["egress-vpc", "prod-vpc", "shared-services", "staging-vpc"]


def test_lambda_outside_vpc_is_inventoried_but_not_drawn(tmp_path):
    # Live account check (2026-10-17): functions without VpcConfig were collected but missing from the inventory.
    from diagram_bot.drawio import layout_document
    snapshot = demo_snapshot()
    snapshot["raw"]["lambda_functions"] = [{"FunctionName": "edge-handler", "Runtime": "python3.12",
                                            "VpcConfig": {"SubnetIds": [], "SecurityGroupIds": [], "VpcId": ""}},
                                           {"FunctionName": "cron-job", "Runtime": "nodejs22.x"}]
    graph = build_graph(snapshot)
    functions = [n for n in graph["nodes"] if n["kind"] == "lambda_function"]
    assert sorted(n["name"] for n in functions) == ["cron-job", "edge-handler"]
    assert not any(e["source"] in {n["id"] for n in functions} for e in graph["edges"])
    for vpc in (n["resource_id"] for n in graph["nodes"] if n["kind"] == "vpc"):
        assert not {n["id"] for n in functions} & set(view_graph(graph, "detail", vpc)["nodes"])
    labels = [s["label"] for s in layout_document(snapshot, "overview")["shapes"]]
    assert any(label.startswith("VPC 미연결 리전 서비스\nLambda 2개") for label in labels)
    assert not any("배치 미확인" in label and "리소스 2개" in label for label in labels)
    assert layout_document(snapshot, "focus", resource=functions[0]["id"])["shapes"]
    write_report(snapshot, tmp_path)
    with (tmp_path / "inventory.csv").open(encoding="utf-8-sig", newline="") as f:
        assert {"cron-job", "edge-handler"} <= {r["resource_id"] for r in csv.DictReader(f)}


def test_terminated_instances_are_not_drawn_or_inventoried():
    # Live check (2026-10-17): instances of a deleted stack stayed in DescribeInstances as "terminated" without a subnet
    # and showed up as unplaced resources.
    snapshot = demo_snapshot()
    gone = copy.deepcopy(snapshot["raw"]["instances"][0])
    gone.update(InstanceId="i-gone0000000000001", State={"Code": 48, "Name": "terminated"})
    gone.pop("SubnetId", None)
    gone.pop("VpcId", None)
    snapshot["raw"]["instances"].append(gone)
    graph = build_graph(snapshot)
    assert not any(n["resource_id"] == "i-gone0000000000001" for n in graph["nodes"])
    assert any(n["resource_id"] == snapshot["raw"]["instances"][0]["InstanceId"] for n in graph["nodes"])


def test_ai_platform_follows_the_ai_lenses():
    """Private VPC, multi-AZ inference and endpoints, single-AZ EFA training (docs/ARCHITECTURE.md §5.5)."""
    from diagram_bot.findings import review
    from diagram_bot.samples import SAMPLES
    snapshot = SAMPLES["ai-platform"]()
    raw, graph = snapshot["raw"], build_graph(snapshot)
    assert not raw["internet_gateways"] and not raw["nat_gateways"]
    assert {n.get("route_class") for n in graph["nodes"] if n["kind"] == "subnet"} == {"tgw", "isolated"}
    zones = {s["SubnetId"]: s["AvailabilityZone"] for s in raw["subnets"]}
    groups = {g["AutoScalingGroupName"]: {zones[s] for s in g["VPCZoneIdentifier"].split(",")} for g in raw["auto_scaling_groups"]}
    assert len(groups["eks-ai-gpu-inference-3c4d"]) == 3 and len(groups["eks-ai-gpu-train-5e6f"]) == 1
    services = {e["ServiceName"].split(f".{snapshot['metadata']['region']}.", 1)[1] for e in raw["vpc_endpoints"]}
    assert {"bedrock-runtime", "bedrock-agent-runtime", "ecr.api", "ecr.dkr", "sts", "logs", "s3"} <= services
    assert all(len({zones[s] for s in e.get("SubnetIds", [])}) == 3 for e in raw["vpc_endpoints"] if e["VpcEndpointType"] == "Interface")
    efa = next(g for g in raw["security_groups"] if g["GroupId"] == "sg-train-efa")
    assert {"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": "sg-train-efa"}]} in efa["IpPermissions"]
    assert all(r.get("IpRanges", [{}])[0].get("CidrIp") != "0.0.0.0/0" for r in efa["IpPermissionsEgress"])
    assert [(f["rule"], f["severity"]) for f in review(graph, snapshot["coverage"])] == [("REL-ASG-AZ", "low")]


def test_combined_accounts_share_one_transit_gateway_and_one_peering():
    """A RAM-shared Transit Gateway and a cross-account peering become one node each, owned by the owner account."""
    from diagram_bot.combine import combine_snapshots
    from diagram_bot.samples import SAMPLES, Sample
    snapshot = SAMPLES["multi-account"]()
    graph = build_graph(snapshot)
    gateways = [n for n in graph["nodes"] if n["kind"] == "transit_gateway"]
    assert [(n["account"], n["reference_only"]) for n in gateways] == [("111111111111", False)]
    attached = {n["account"] for e in graph["edges"] if e["relation"] == "attached_transit_gateway"
                for n in graph["nodes"] if n["id"] == e["source"]}
    assert attached == {"111111111111", "222222222222", "333333333333"}
    # Cross-account peering: each side reports the same pcx; the combined graph has one, linked to both VPCs.
    sides = []
    for account, vpc, cidr in (("444444444444", "vpc-0aaaa000000000044", "10.44.0.0/16"), ("555555555555", "vpc-0bbbb000000000055", "10.55.0.0/16")):
        s = Sample("peer", "peer", [], account=account)
        s.vpc(vpc, cidr, f"vpc-{account[:2]}")
        s.raw["vpc_peering_connections"].append({"VpcPeeringConnectionId": "pcx-0cross0000000045", "Status": {"Code": "active"},
                                                  "RequesterVpcInfo": {"VpcId": "vpc-0aaaa000000000044", "OwnerId": "444444444444", "CidrBlock": "10.44.0.0/16"},
                                                  "AccepterVpcInfo": {"VpcId": "vpc-0bbbb000000000055", "OwnerId": "555555555555", "CidrBlock": "10.55.0.0/16"}})
        sides.append(s.snapshot())
    peered = build_graph(combine_snapshots(sides))
    peerings = [n for n in peered["nodes"] if n["kind"] == "vpc_peering"]
    assert len(peerings) == 1
    assert sorted(e["target"].split("/")[0] for e in peered["edges"] if e["source"] == peerings[0]["id"] and e["relation"] == "in_vpc") == [
        "444444444444", "555555555555"]
    with pytest.raises(ValueError, match="두 개 이상"):
        combine_snapshots(sides[:1])
    with pytest.raises(ValueError, match="두 번"):
        combine_snapshots([sides[0], sides[0]])


def test_edge_elastic_ips_routing_domains_and_direct_connect_join_on_api_fields():
    """Every new relation comes from a named API field: Elastic IP owners, CloudFront origins by DNS name only, WAF
    associations, TGW route table associations/propagations/routes and Direct Connect VIFs and gateway associations."""
    from diagram_bot.combine import combine_snapshots
    from fixtures import edge_snapshot
    snapshot = edge_snapshot()
    graph = build_graph(snapshot)
    nodes = {n["id"]: n for n in graph["nodes"]}
    named = lambda name: next(n for n in graph["nodes"] if n["name"] == name)
    out = lambda node, relation: sorted(nodes[e["target"]]["name"] for e in graph["edges"] if e["source"] == node["id"] and e["relation"] == relation)
    # Elastic IPs: NAT by allocation ID, instance by InstanceId; the idle one joins nothing.
    assert out(named("nat-a-eip"), "eip_associated") == ["edge-nat-a"] and out(named("203.0.113.81"), "eip_associated") == ["web-a"]
    assert out(named("203.0.113.82"), "eip_associated") == [] and named("edge-nat-a")["elastic_ips"] == ["203.0.113.80"]
    # CloudFront: the ALB origin is matched by DNS name, S3 by its endpoint; a CNAME in front of an ALB stays unresolved.
    web, shop = named("www.example.org"), named("shop.example.org")
    assert web["id"].split("/")[1] == "global" and web["region"] == "global"
    assert out(web, "cloudfront_origin") == ["web-alb"] and out(shop, "cloudfront_origin") == ["shop-alb"]
    assert web["origins"] == [["ELB", "web-alb"], ["S3", "edge-static"]]
    assert shop["origins"] == [["ELB", "shop-alb"], ["custom", "origin.shop.example.org"]]
    origin = next(e for e in graph["edges"] if e["relation"] == "cloudfront_origin" and e["source"] == web["id"])
    assert origin["evidence"] == "ListDistributions.Origins.Items[].DomainName = DescribeLoadBalancers.DNSName" and not origin["inferred"]
    assert out(named("edge-acl"), "waf_protects") == ["www.example.org"] and web["waf"] == ["edge-acl"] and not shop.get("waf")
    # TGW routing domain: association, propagation and routes (a blackhole route names no attachment).
    table = named("edge-spokes")
    assert out(table, "route_table_of") == ["edge-tgw"]
    assert out(table, "tgw_rt_association") == out(table, "tgw_rt_propagation") == ["web-attachment"]
    routes = [e for e in graph["edges"] if e["source"] == table["id"] and e["relation"] == "tgw_route"]
    assert [e["qualifiers"]["destination"] for e in routes] == ["10.80.0.0/16"]
    assert [r["State"] for r in table["attributes"]["Routes"]] == ["active", "blackhole"]
    assert named("web-attachment")["tgw_route_table"] == "edge-spokes" and named("edge-tgw")["route_tables"] == ["edge-spokes"]
    # Direct Connect: a private VIF on the virtual private gateway, transit VIFs on the DX gateway, the gateway's
    # association with the TGW; an association in another region is listed, not linked.
    assert out(named("vgw-edge-corp"), "dx_virtual_interface") == ["corp-dx"]
    assert out(named("edge-dxgw"), "dx_virtual_interface") == ["edge-dx-1", "edge-dx-2"]
    assert out(named("edge-dxgw"), "dx_gateway_association") == ["edge-tgw"]
    assert ["다른 리전의 연결 게이트웨이", "tgw-other (us-west-2)"] in named("edge-dxgw")["properties"]
    vif = next(e for e in graph["edges"] if e["relation"] == "dx_virtual_interface" and e["target"].endswith("dxcon-edge-corp"))
    assert vif["details"]["Type"] == "private" and vif["details"]["BGP"] == "0/1 up" and vif["qualifiers"]["destination"] == "dxvif-corp"
    assert len({e["id"] for e in graph["edges"]}) == len(graph["edges"]) and build_graph(snapshot) == graph
    # Account-global resources keep one node each when two regions of one account are combined.
    other = copy.deepcopy(snapshot)
    other["metadata"]["region"] = "us-west-2"
    merged = build_graph(combine_snapshots([snapshot, other]))
    assert sum(n["kind"] == "cloudfront_distribution" for n in merged["nodes"]) == 2
    assert sum(n["kind"] == "dx_gateway" for n in merged["nodes"]) == 1


def test_a_subnet_group_places_a_node_only_when_one_subnet_is_in_its_zone():
    from diagram_bot.graph import group_subnet, tag_map
    entry = lambda subnet, zone: {"SubnetIdentifier": subnet, "SubnetAvailabilityZone": {"Name": zone}}
    group = {"Subnets": [entry("subnet-a", "az-1"), entry("subnet-b", "az-2"), entry("subnet-c", "az-2")]}
    assert group_subnet(group, "az-1") == "subnet-a"
    assert group_subnet(group, "az-2") is None and group_subnet(group, "az-3") is None and group_subnet({}, "az-1") is None
    assert tag_map({"Tags": [{"Key": "Name", "Value": "web"}, {"Key": "empty"}]}) == {"Name": "web", "empty": ""} and tag_map({}) == {}
