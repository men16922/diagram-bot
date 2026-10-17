"""Deterministic joins. No traffic inference or IP-to-instance guessing."""
import hashlib
import json

# Fields that distinguish two configured relations between the same resources. Health state and
# other volatile description fields are deliberately excluded so IDs survive re-collection.
QUALIFIERS = ("port", "availability_zone")


def relation_id(edge):
    """Stable ID from endpoints, relation type and qualifiers; independent of array order."""
    given = edge.get("qualifiers", {})
    qualifiers = {k: given.get(k) for k in QUALIFIERS}
    if given.get("destination") is not None:
        # Route destinations only exist on routing edges; older IDs keep their original shape.
        qualifiers["destination"] = given["destination"]
    key = json.dumps([edge["source"], edge["relation"], edge["target"], qualifiers], sort_keys=True, default=str)
    return "rel-" + hashlib.sha256(key.encode()).hexdigest()[:20]


def upgrade_graph(graph):
    """Add relation IDs to graphs stored before IDs existed, keeping edge order for relationIndex."""
    for edge in graph.get("edges", []):
        if "id" not in edge:
            details = edge.get("details") or {}
            target = details.get("Target", {})
            edge.setdefault("qualifiers", {k: v for k, v in (("port", target.get("Port")),
                            ("availability_zone", target.get("AvailabilityZone") or details.get("availability_zone"))) if v is not None})
            edge["id"] = relation_id(edge)
    return graph


def build_graph(snapshot):
    nodes, edges = {}, {}
    account, region = snapshot["metadata"]["account_id"], snapshot["metadata"]["region"]
    prefix = f"{account}/{region}/"

    def node(kind, resource_id, attrs=None, source=None, reference=False):
        key = prefix + kind + "/" + resource_id
        if key not in nodes or not reference:
            attrs = attrs or {}
            tags = {t["Key"]: t.get("Value", "") for t in attrs.get("Tags", [])}
            nodes[key] = {"id": key, "resource_id": resource_id, "kind": kind,
                          "name": tags.get("Name") or attrs.get("LoadBalancerName") or attrs.get("TargetGroupName")
                          or attrs.get("DBInstanceIdentifier") or attrs.get("AutoScalingGroupName") or attrs.get("FunctionName")
                          or attrs.get("CacheClusterId") or attrs.get("ClusterName") or resource_id,
                          "account": account, "region": region, "attributes": attrs,
                          "reference_only": reference, "source": source}
        return key

    def link(source, kind, resource_id, relation, evidence, details=None, qualifiers=None):
        if not resource_id:
            return
        target = node(kind, resource_id, reference=True)
        edge = {"source": source, "target": target, "relation": relation, "evidence": evidence,
                "qualifiers": {k: v for k, v in (qualifiers or {}).items() if v is not None},
                "details": details or {}, "inferred": False}
        edge["id"] = relation_id(edge)
        # A duplicated API entry is the same configured relation, not a second one.
        edges.setdefault(edge["id"], edge)

    specs = [("vpcs", "vpc", "VpcId", "DescribeVpcs"),
             ("subnets", "subnet", "SubnetId", "DescribeSubnets"),
             ("instances", "ec2", "InstanceId", "DescribeInstances"),
             ("load_balancers", "load_balancer", "LoadBalancerArn", "DescribeLoadBalancers"),
             ("target_groups", "target_group", "TargetGroupArn", "DescribeTargetGroups")]
    raw = snapshot["raw"]
    for collection, kind, field, api in specs:
        for item in raw.get(collection, []):
            node(kind, item[field], item, api)
    for collection, kind, field, api in specs:
        for item in raw.get(collection, []):
            key = prefix + kind + "/" + item[field]
            if kind != "vpc":
                link(key, "vpc", item.get("VpcId"), "in_vpc", api + ".VpcId")
            if kind == "ec2":
                link(key, "subnet", item.get("SubnetId"), "in_subnet", api + ".SubnetId")
                for sg in item.get("SecurityGroups", []):
                    link(key, "security_group", sg["GroupId"], "uses_security_group", api + ".SecurityGroups[].GroupId")
                for eni in item.get("NetworkInterfaces", []):
                    link(key, "network_interface", eni["NetworkInterfaceId"], "attached_interface", api + ".NetworkInterfaces[].NetworkInterfaceId")
                for block in item.get("BlockDeviceMappings", []):
                    link(key, "volume", block.get("Ebs", {}).get("VolumeId"), "attached_volume", api + ".BlockDeviceMappings[].Ebs.VolumeId")
            if kind == "load_balancer":
                for az in item.get("AvailabilityZones", []):
                    link(key, "subnet", az.get("SubnetId"), "in_subnet", api + ".AvailabilityZones[].SubnetId",
                         {"availability_zone": az.get("ZoneName")}, {"availability_zone": az.get("ZoneName")})
                for sg in item.get("SecurityGroups", []):
                    link(key, "security_group", sg, "uses_security_group", api + ".SecurityGroups[]")
            if kind == "target_group":
                for lb in item.get("LoadBalancerArns", []):
                    link(key, "load_balancer", lb, "associated_load_balancer", api + ".LoadBalancerArns[]")
    groups = {x["TargetGroupArn"]: x for x in raw.get("target_groups", [])}
    for result in raw.get("target_health", []):
        arn = result["TargetGroupArn"]
        group = groups.get(arn, {})
        key = node("target_group", arn, reference=True)
        for description in result["Descriptions"]:
            target = description["Target"]
            target_type = group.get("TargetType", "unknown")
            kind = {"instance": "ec2", "alb": "load_balancer"}.get(target_type, "registered_target")
            rid = target["Id"]
            if kind == "registered_target":
                rid = f"{arn}#{rid}:{target.get('Port', '')}:{target.get('AvailabilityZone', '')}"
                node(kind, rid, {**description, "TargetType": target_type}, "DescribeTargetHealth")
            link(key, kind, rid, "registered_target", "DescribeTargetHealth.Target.Id", description,
                 {"port": target.get("Port"), "availability_zone": target.get("AvailabilityZone")})
    endpoints_and_transit(raw, node, link)
    infrastructure(raw, prefix, nodes, node, link)
    services(raw, prefix, nodes, node, link)
    classify_subnets(raw, prefix, nodes, link)
    return {"nodes": sorted(nodes.values(), key=lambda x: x["id"]),
            "edges": sorted(edges.values(), key=lambda x: (x["source"], x["relation"], x["target"], x["id"]))}


def infrastructure(raw, prefix, nodes, node, link):
    """Internet path, egress, routing, data tier and scaling groups, joined only on API fields."""
    subnet_vpc = {s["SubnetId"]: s.get("VpcId") for s in raw.get("subnets", [])}
    for gateway in raw.get("internet_gateways", []):
        key = node("internet_gateway", gateway["InternetGatewayId"], gateway, "DescribeInternetGateways")
        for attachment in gateway.get("Attachments", []):
            link(key, "vpc", attachment.get("VpcId"), "in_vpc", "DescribeInternetGateways.Attachments[].VpcId")
    for gateway in raw.get("nat_gateways", []):
        key = node("nat_gateway", gateway["NatGatewayId"], gateway, "DescribeNatGateways")
        link(key, "vpc", gateway.get("VpcId"), "in_vpc", "DescribeNatGateways.VpcId")
        link(key, "subnet", gateway.get("SubnetId"), "in_subnet", "DescribeNatGateways.SubnetId")
    for table in raw.get("route_tables", []):
        key = node("route_table", table["RouteTableId"], table, "DescribeRouteTables")
        link(key, "vpc", table.get("VpcId"), "in_vpc", "DescribeRouteTables.VpcId")
        for association in table.get("Associations", []):
            link(key, "subnet", association.get("SubnetId"), "associated_subnet", "DescribeRouteTables.Associations[].SubnetId")
        for route in table.get("Routes", []):
            destination = route.get("DestinationCidrBlock") or route.get("DestinationIpv6CidrBlock") or route.get("DestinationPrefixListId")
            gateway = str(route.get("GatewayId", ""))
            if route.get("NatGatewayId"):
                kind, field = "nat_gateway", "NatGatewayId"
            elif route.get("TransitGatewayId"):
                kind, field = "transit_gateway", "TransitGatewayId"
            elif gateway.startswith(("igw-", "vpce-")):
                kind, field = ("internet_gateway" if gateway.startswith("igw-") else "vpc_endpoint"), "GatewayId"
            else:
                continue  # local and other targets carry no drawable relation
            link(key, kind, route[field], "routes_to", f"DescribeRouteTables.Routes[].{field}", {"state": route.get("State")},
                 {"destination": destination})
    for db in raw.get("db_instances", []):
        identifier = db["DBInstanceIdentifier"]
        key = node("db_instance", identifier, db, "DescribeDBInstances")
        group = db.get("DBSubnetGroup") or {}
        link(key, "vpc", group.get("VpcId"), "in_vpc", "DescribeDBInstances.DBSubnetGroup.VpcId")

        def subnet_in(zone):
            # The subnet group lists subnets per AZ; placement is only stated when one subnet matches.
            matches = [s["SubnetIdentifier"] for s in group.get("Subnets", []) if (s.get("SubnetAvailabilityZone") or {}).get("Name") == zone]
            return matches[0] if len(matches) == 1 else None

        link(key, "subnet", subnet_in(db.get("AvailabilityZone")), "in_subnet",
             "DescribeDBInstances.AvailabilityZone + DBSubnetGroup.Subnets[]", {"availability_zone": db.get("AvailabilityZone")})
        if db.get("MultiAZ") and db.get("SecondaryAvailabilityZone"):
            standby = node("db_standby", identifier + "#standby",
                           {"DBInstanceIdentifier": identifier + " (standby)", "AvailabilityZone": db["SecondaryAvailabilityZone"],
                            "Engine": db.get("Engine"), "DBInstanceClass": db.get("DBInstanceClass")},
                           "DescribeDBInstances.SecondaryAvailabilityZone")
            link(standby, "db_instance", identifier, "standby_of", "DescribeDBInstances.MultiAZ + SecondaryAvailabilityZone")
            link(standby, "vpc", group.get("VpcId"), "in_vpc", "DescribeDBInstances.DBSubnetGroup.VpcId")
            link(standby, "subnet", subnet_in(db["SecondaryAvailabilityZone"]), "in_subnet",
                 "DescribeDBInstances.SecondaryAvailabilityZone + DBSubnetGroup.Subnets[]",
                 {"availability_zone": db["SecondaryAvailabilityZone"]})
    for group in raw.get("auto_scaling_groups", []):
        key = node("auto_scaling_group", group["AutoScalingGroupName"], group, "DescribeAutoScalingGroups")
        vpcs = {subnet_vpc.get(s) for s in (group.get("VPCZoneIdentifier") or "").split(",") if s} - {None}
        if len(vpcs) == 1:
            link(key, "vpc", vpcs.pop(), "in_vpc", "DescribeAutoScalingGroups.VPCZoneIdentifier → DescribeSubnets.VpcId")
        for instance in group.get("Instances", []):
            link(key, "ec2", instance.get("InstanceId"), "asg_member", "DescribeAutoScalingGroups.Instances[].InstanceId",
                 {"availability_zone": instance.get("AvailabilityZone")})
        for arn in group.get("TargetGroupARNs", []):
            link(key, "target_group", arn, "asg_target_group", "DescribeAutoScalingGroups.TargetGroupARNs[]")


def endpoints_and_transit(raw, node, link):
    """VPC endpoints (gateway via route tables, interface via subnets) and Transit Gateway VPC attachments."""
    for endpoint in raw.get("vpc_endpoints", []):
        # com.amazonaws.<region>.<service>, e.g. ecr.dkr or s3.
        attrs = {**endpoint, "ServiceShortName": ".".join(endpoint.get("ServiceName", "").split(".")[3:])}
        key = node("vpc_endpoint", endpoint["VpcEndpointId"], attrs, "DescribeVpcEndpoints")
        link(key, "vpc", endpoint.get("VpcId"), "in_vpc", "DescribeVpcEndpoints.VpcId")
        for subnet in endpoint.get("SubnetIds", []):
            link(key, "subnet", subnet, "endpoint_subnet", "DescribeVpcEndpoints.SubnetIds[]")
        for table in endpoint.get("RouteTableIds", []):
            link(key, "route_table", table, "endpoint_route_table", "DescribeVpcEndpoints.RouteTableIds[]")
    for gateway in raw.get("transit_gateways", []):
        node("transit_gateway", gateway["TransitGatewayId"], gateway, "DescribeTransitGateways")
    for attachment in raw.get("transit_gateway_attachments", []):
        key = node("tgw_attachment", attachment["TransitGatewayAttachmentId"], attachment, "DescribeTransitGatewayVpcAttachments")
        link(key, "vpc", attachment.get("VpcId"), "in_vpc", "DescribeTransitGatewayVpcAttachments.VpcId")
        link(key, "transit_gateway", attachment.get("TransitGatewayId"), "attached_transit_gateway",
             "DescribeTransitGatewayVpcAttachments.TransitGatewayId")
        for subnet in attachment.get("SubnetIds", []):
            link(key, "subnet", subnet, "attachment_subnet", "DescribeTransitGatewayVpcAttachments.SubnetIds[]")


def services(raw, prefix, nodes, node, link):
    """EKS, MSK, ElastiCache, Aurora roles and VPC-attached Lambda, joined only on API fields."""
    subnet_vpc = {s["SubnetId"]: s.get("VpcId") for s in raw.get("subnets", [])}
    for cluster in raw.get("db_clusters", []):
        key = node("db_cluster", cluster["DBClusterIdentifier"], cluster, "DescribeDBClusters")
        for member in cluster.get("DBClusterMembers", []):
            instance = prefix + "db_instance/" + member["DBInstanceIdentifier"]
            if instance in nodes:
                nodes[instance]["db_role"] = "writer" if member.get("IsClusterWriter") else "reader"
            link(key, "db_instance", member["DBInstanceIdentifier"], "cluster_member", "DescribeDBClusters.DBClusterMembers[]",
                 {"writer": member.get("IsClusterWriter")})
    groups = {g["CacheSubnetGroupName"]: g for g in raw.get("cache_subnet_groups", [])}
    roles = {m.get("CacheClusterId"): m.get("CurrentRole") for g in raw.get("replication_groups", [])
             for ng in g.get("NodeGroups", []) for m in ng.get("NodeGroupMembers", [])}
    for group in raw.get("replication_groups", []):
        node("cache_replication_group", group["ReplicationGroupId"], group, "DescribeReplicationGroups")
    for cache in raw.get("cache_clusters", []):
        key = node("cache_node", cache["CacheClusterId"], {**cache, "CurrentRole": roles.get(cache["CacheClusterId"])},
                   "DescribeCacheClusters")
        subnet_group = groups.get(cache.get("CacheSubnetGroupName"), {})
        link(key, "vpc", subnet_group.get("VpcId"), "in_vpc", "DescribeCacheClusters.CacheSubnetGroupName → DescribeCacheSubnetGroups.VpcId")
        zone = cache.get("PreferredAvailabilityZone")
        matches = [s["SubnetIdentifier"] for s in subnet_group.get("Subnets", []) if (s.get("SubnetAvailabilityZone") or {}).get("Name") == zone]
        link(key, "subnet", matches[0] if len(matches) == 1 else None, "in_subnet",
             "DescribeCacheClusters.PreferredAvailabilityZone + DescribeCacheSubnetGroups.Subnets[]", {"availability_zone": zone})
        link(key, "cache_replication_group", cache.get("ReplicationGroupId"), "cache_member_of", "DescribeCacheClusters.ReplicationGroupId")
    for cluster in raw.get("msk_clusters", []):
        provisioned = cluster.get("Provisioned") or {}
        brokers = provisioned.get("BrokerNodeGroupInfo") or {}
        subnets = brokers.get("ClientSubnets", [])
        key = node("msk_cluster", cluster["ClusterArn"], cluster, "ListClustersV2")
        for subnet in subnets:
            # MSK spreads brokers evenly across the client subnets (one per AZ).
            per_subnet = provisioned.get("NumberOfBrokerNodes", 0) // max(1, len(subnets))
            broker = node("msk_broker", cluster["ClusterArn"] + "#" + subnet,
                          {"ClusterName": cluster.get("ClusterName"), "Brokers": per_subnet, "InstanceType": brokers.get("InstanceType"),
                           "KafkaVersion": (provisioned.get("CurrentBrokerSoftwareInfo") or {}).get("KafkaVersion")},
                          "ListClustersV2.Provisioned.BrokerNodeGroupInfo.ClientSubnets[]")
            link(broker, "vpc", subnet_vpc.get(subnet), "in_vpc", "ClientSubnets[] → DescribeSubnets.VpcId")
            link(broker, "subnet", subnet, "in_subnet", "ListClustersV2.Provisioned.BrokerNodeGroupInfo.ClientSubnets[]")
            link(broker, "msk_cluster", cluster["ClusterArn"], "broker_of", "ListClustersV2.ClusterArn")
        if subnets and subnet_vpc.get(subnets[0]):
            link(key, "vpc", subnet_vpc[subnets[0]], "in_vpc", "ClientSubnets[] → DescribeSubnets.VpcId")
    for function in raw.get("lambda_functions", []):
        config = function.get("VpcConfig") or {}
        if not config.get("VpcId"):
            continue  # not attached to a VPC: outside the network diagram
        key = node("lambda_function", function["FunctionName"], function, "ListFunctions")
        link(key, "vpc", config["VpcId"], "in_vpc", "ListFunctions.VpcConfig.VpcId")
        for subnet in config.get("SubnetIds", []):
            link(key, "subnet", subnet, "lambda_subnet", "ListFunctions.VpcConfig.SubnetIds[]")
    for cluster in raw.get("eks_clusters", []):
        config = cluster.get("resourcesVpcConfig") or {}
        key = node("eks_cluster", cluster["name"], cluster, "DescribeCluster")
        link(key, "vpc", config.get("vpcId"), "in_vpc", "DescribeCluster.resourcesVpcConfig.vpcId")
        for subnet in config.get("subnetIds", []):
            link(key, "subnet", subnet, "eks_subnet", "DescribeCluster.resourcesVpcConfig.subnetIds[]")
        for group in cluster.get("nodegroups", []):
            for asg in (group.get("resources") or {}).get("autoScalingGroups", []):
                link(key, "auto_scaling_group", asg.get("name"), "eks_nodegroup", "DescribeNodegroup.resources.autoScalingGroups[]",
                     {"nodegroup": group.get("nodegroupName"), "instanceTypes": group.get("instanceTypes"),
                      "scaling": group.get("scalingConfig")})


def classify_subnets(raw, prefix, nodes, link):
    """Subnet internet path from its route table: explicit association, else the VPC's main table."""
    tables = raw.get("route_tables")
    if not tables:
        return
    main = {t.get("VpcId"): t for t in tables if any(a.get("Main") for a in t.get("Associations", []))}
    explicit = {a["SubnetId"]: t for t in tables for a in t.get("Associations", []) if a.get("SubnetId")}
    for subnet in raw.get("subnets", []):
        table = explicit.get(subnet["SubnetId"]) or main.get(subnet.get("VpcId"))
        key = prefix + "subnet/" + subnet["SubnetId"]
        if not table or key not in nodes:
            continue
        if subnet["SubnetId"] not in explicit:
            link(prefix + "route_table/" + table["RouteTableId"], "subnet", subnet["SubnetId"], "associated_subnet",
                 "DescribeRouteTables.Associations[].Main (implicit)", {"implicit": True})
        default = [r for r in table.get("Routes", []) if r.get("DestinationCidrBlock") == "0.0.0.0/0" and r.get("State", "active") == "active"]
        if any(str(r.get("GatewayId", "")).startswith("igw-") for r in default):
            route_class = "public"
        elif any(r.get("NatGatewayId") for r in default):
            route_class = "nat"
        elif any(r.get("TransitGatewayId") for r in default):
            route_class = "tgw"
        else:
            route_class = "isolated"
        nodes[key]["route_class"] = route_class
        nodes[key]["route_table"] = table["RouteTableId"]
