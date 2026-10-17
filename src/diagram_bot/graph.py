"""Deterministic joins. No traffic inference or IP-to-instance guessing."""
import hashlib
import ipaddress
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
                          "name": tags.get("Name") or attrs.get("DisplayName") or attrs.get("LoadBalancerName") or attrs.get("TargetGroupName")
                          or attrs.get("DBInstanceIdentifier") or attrs.get("AutoScalingGroupName") or attrs.get("FunctionName")
                          or attrs.get("CacheClusterId") or attrs.get("ClusterName") or attrs.get("serviceName")
                          or attrs.get("DomainName") or attrs.get("ClusterIdentifier") or attrs.get("GroupName") or resource_id,
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
    tasks = ecs_tasks(raw, node, link)
    for result in raw.get("target_health", []):
        arn = result["TargetGroupArn"]
        group = groups.get(arn, {})
        key = node("target_group", arn, reference=True)
        for description in result["Descriptions"]:
            target = description["Target"]
            target_type = group.get("TargetType", "unknown")
            kind = {"instance": "ec2", "alb": "load_balancer"}.get(target_type, "registered_target")
            rid = target["Id"]
            if kind == "registered_target" and (arn, rid) in tasks:
                # The ECS service registers its task's ENI address in the target group it names.
                kind, rid = "ecs_task", tasks[(arn, rid)]
            elif kind == "registered_target":
                rid = f"{arn}#{rid}:{target.get('Port', '')}:{target.get('AvailabilityZone', '')}"
                target_key = node(kind, rid, {**description, "TargetType": target_type}, "DescribeTargetHealth")
                place_ip_target(raw, target_key, target, group.get("VpcId"), link)
            link(key, kind, rid, "registered_target", "DescribeTargetHealth.Target.Id", description,
                 {"port": target.get("Port"), "availability_zone": target.get("AvailabilityZone")})
    for group in raw.get("security_groups", []):
        # Collected for its rules (review, inspector); drawn only as a reference when requested.
        node("security_group", group["GroupId"], group, "DescribeSecurityGroups")
    endpoints_and_transit(raw, node, link)
    infrastructure(raw, prefix, nodes, node, link)
    services(raw, prefix, nodes, node, link)
    more_services(raw, prefix, nodes, node, link)
    connectivity(raw, prefix, nodes, node, link)
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
            elif route.get("VpcPeeringConnectionId"):
                kind, field = "vpc_peering", "VpcPeeringConnectionId"
            elif gateway.startswith(("igw-", "vpce-", "vgw-")):
                kind, field = {"igw": "internet_gateway", "vpce": "vpc_endpoint", "vgw": "vpn_gateway"}[gateway.split("-")[0]], "GatewayId"
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
        source = db.get("ReadReplicaSourceDBInstanceIdentifier")
        if source:
            # Same-region sources are identifiers; a cross-region source is an ARN in another region.
            nodes[key]["db_role"] = "cross-region replica" if source.startswith("arn:") else "read replica"
            if not source.startswith("arn:"):
                link(key, "db_instance", source, "read_replica_of", "DescribeDBInstances.ReadReplicaSourceDBInstanceIdentifier")
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
                # Aurora readers serve reads and fail over; a Multi-AZ DB cluster's readers are readable standbys.
                aurora = str(cluster.get("Engine", "")).startswith("aurora") or cluster.get("Engine") in ("docdb", "neptune")
                nodes[instance]["db_role"] = "writer" if member.get("IsClusterWriter") else ("reader" if aurora else "readable standby")
            link(key, "db_instance", member["DBInstanceIdentifier"], "cluster_member", "DescribeDBClusters.DBClusterMembers[]",
                 {"writer": member.get("IsClusterWriter")})
    groups = {g["CacheSubnetGroupName"]: g for g in raw.get("cache_subnet_groups", [])}
    roles = {m.get("CacheClusterId"): m.get("CurrentRole") for g in raw.get("replication_groups", [])
             for ng in g.get("NodeGroups", []) for m in ng.get("NodeGroupMembers", [])}
    # Cluster mode enabled: one node group per shard.
    shards = {m.get("CacheClusterId"): ng.get("NodeGroupId") for g in raw.get("replication_groups", []) if len(g.get("NodeGroups", [])) > 1
              for ng in g["NodeGroups"] for m in ng.get("NodeGroupMembers", [])}
    for group in raw.get("replication_groups", []):
        node("cache_replication_group", group["ReplicationGroupId"], group, "DescribeReplicationGroups")
    for cache in raw.get("cache_clusters", []):
        key = node("cache_node", cache["CacheClusterId"], {**cache, "CurrentRole": roles.get(cache["CacheClusterId"]),
                                                            "Shard": shards.get(cache["CacheClusterId"])}, "DescribeCacheClusters")
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
        listed = {}
        for item in raw.get("msk_nodes", []):
            info = item.get("BrokerNodeInfo") or {}
            if item.get("ClusterArn") == cluster["ClusterArn"] and info.get("ClientSubnet"):
                listed.setdefault(info["ClientSubnet"], []).append(info.get("BrokerId"))
        for subnet in subnets:
            # ListNodes names each broker's subnet; without it MSK's even spread over client subnets is shown.
            ids = listed.get(subnet)
            per_subnet = len(ids) if listed else provisioned.get("NumberOfBrokerNodes", 0) // max(1, len(subnets))
            broker = node("msk_broker", cluster["ClusterArn"] + "#" + subnet,
                          {"ClusterName": cluster.get("ClusterName"), "Brokers": per_subnet, "InstanceType": brokers.get("InstanceType"),
                           "BrokerIds": sorted(ids or []), "Estimated": not listed,
                           "KafkaVersion": (provisioned.get("CurrentBrokerSoftwareInfo") or {}).get("KafkaVersion")},
                          "ListNodes.BrokerNodeInfo.ClientSubnet" if listed else "ListClustersV2.Provisioned.BrokerNodeGroupInfo.ClientSubnets[]")
            link(broker, "vpc", subnet_vpc.get(subnet), "in_vpc", "ClientSubnets[] → DescribeSubnets.VpcId")
            link(broker, "subnet", subnet, "in_subnet", "ListClustersV2.Provisioned.BrokerNodeGroupInfo.ClientSubnets[]")
            link(broker, "msk_cluster", cluster["ClusterArn"], "broker_of", "ListClustersV2.ClusterArn")
        if subnets and subnet_vpc.get(subnets[0]):
            link(key, "vpc", subnet_vpc[subnets[0]], "in_vpc", "ClientSubnets[] → DescribeSubnets.VpcId")
    for function in raw.get("lambda_functions", []):
        config = function.get("VpcConfig") or {}
        key = node("lambda_function", function["FunctionName"], function, "ListFunctions")
        if not config.get("VpcId"):
            continue  # not attached to a VPC: in the inventory, outside the network diagram
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


def subnet_index(raw):
    return {s["SubnetId"]: s for s in raw.get("subnets", [])}


def ecs_tasks(raw, node, link):
    """ECS services and their awsvpc tasks. Returns (target group, task IP) -> task ARN for target registration."""
    subnets = subnet_index(raw)
    services = {(s.get("clusterArn"), s.get("serviceName")): s for s in raw.get("ecs_services", [])}
    for service in services.values():
        config = (service.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {}
        launch = service.get("launchType") or "/".join(c.get("capacityProvider", "") for c in service.get("capacityProviderStrategy", []))
        key = node("ecs_service", service["serviceArn"], {**service, "LaunchSummary": launch}, "DescribeServices")
        vpcs = {subnets.get(s, {}).get("VpcId") for s in config.get("subnets", [])} - {None}
        if len(vpcs) == 1:
            link(key, "vpc", vpcs.pop(), "in_vpc", "DescribeServices.networkConfiguration.awsvpcConfiguration.subnets[] → DescribeSubnets.VpcId")
        for subnet in config.get("subnets", []):
            link(key, "subnet", subnet, "service_subnet", "DescribeServices.networkConfiguration.awsvpcConfiguration.subnets[]")
        for balancer in service.get("loadBalancers", []):
            link(key, "target_group", balancer.get("targetGroupArn"), "ecs_target_group", "DescribeServices.loadBalancers[].targetGroupArn",
                 {"container": balancer.get("containerName"), "port": balancer.get("containerPort")})
    registered = {}
    for task in raw.get("ecs_tasks", []):
        details = {d.get("name"): d.get("value") for a in task.get("attachments", []) if a.get("type") == "ElasticNetworkInterface"
                   for d in a.get("details", [])}
        service = services.get((task.get("clusterArn"), str(task.get("group", "")).removeprefix("service:")))
        if not details.get("subnetId"):
            continue  # bridge/host networking: no task ENI to place
        name = (service or {}).get("serviceName") or task.get("group", "task")
        key = node("ecs_task", task["taskArn"], {**task, "DisplayName": f"{name}/{task['taskArn'].rsplit('/', 1)[-1][:8]}",
                                                 "ServiceName": name, "PrivateIp": details.get("privateIPv4Address"),
                                                 "SubnetId": details["subnetId"]}, "DescribeTasks")
        link(key, "vpc", subnets.get(details["subnetId"], {}).get("VpcId"), "in_vpc", "DescribeTasks.attachments[].details.subnetId → DescribeSubnets.VpcId")
        link(key, "subnet", details["subnetId"], "in_subnet", "DescribeTasks.attachments[].details.subnetId",
             {"availability_zone": task.get("availabilityZone")}, {"availability_zone": task.get("availabilityZone")})
        if service:
            link(node("ecs_service", service["serviceArn"], reference=True), "ecs_task", task["taskArn"], "service_task", "DescribeTasks.group")
            for balancer in service.get("loadBalancers", []):
                if balancer.get("targetGroupArn") and details.get("privateIPv4Address"):
                    registered[(balancer["targetGroupArn"], details["privateIPv4Address"])] = task["taskArn"]
    return registered


def place_ip_target(raw, key, target, vpc, link):
    """An IP target in its target group's VPC sits in the subnet whose CIDR holds it (and whose AZ matches)."""
    zone = target.get("AvailabilityZone")
    if not vpc or zone == "all":
        return  # AvailabilityZone=all: an address outside the VPC
    try:
        address = ipaddress.ip_address(target["Id"])
    except ValueError:
        return
    matches = [s for s in raw.get("subnets", []) if s.get("VpcId") == vpc and s.get("CidrBlock")
               and address in ipaddress.ip_network(s["CidrBlock"], strict=False) and zone in (None, s.get("AvailabilityZone"))]
    if len(matches) == 1:
        link(key, "vpc", vpc, "in_vpc", "DescribeTargetGroups.VpcId")
        link(key, "subnet", matches[0]["SubnetId"], "in_subnet", "DescribeTargetHealth.Target.Id ∈ DescribeSubnets.CidrBlock",
             {"availability_zone": matches[0].get("AvailabilityZone")}, {"availability_zone": matches[0].get("AvailabilityZone")})


def more_services(raw, prefix, nodes, node, link):
    """OpenSearch, EFS and Redshift, placed per subnet/AZ from their own API fields."""
    subnets = subnet_index(raw)
    for domain in raw.get("opensearch_domains", []):
        options = domain.get("VPCOptions") or {}
        config = domain.get("ClusterConfig") or {}
        key = node("opensearch_domain", domain["ARN"], domain, "DescribeDomains")
        link(key, "vpc", options.get("VPCId"), "in_vpc", "DescribeDomains.VPCOptions.VPCId")
        listed = [n for n in raw.get("opensearch_nodes", []) if n.get("DomainName") == domain["DomainName"]]
        chosen = options.get("SubnetIds", [])
        for subnet in chosen:
            zone = subnets.get(subnet, {}).get("AvailabilityZone")
            here = [n for n in listed if n.get("AvailabilityZone") == zone]
            count = lambda kind: sum(1 for n in here if n.get("NodeType") == kind)
            attrs = {"DomainName": domain["DomainName"], "EngineVersion": domain.get("EngineVersion"), "InstanceType": config.get("InstanceType"),
                     "DedicatedMasterType": config.get("DedicatedMasterType"), "AvailabilityZone": zone, "Estimated": not listed,
                     "MultiAZWithStandby": config.get("MultiAZWithStandbyEnabled"), "ZoneAwareness": config.get("ZoneAwarenessEnabled")}
            if listed:
                attrs.update(Data=count("Data"), Master=count("Master"), Warm=count("Ultrawarm") + count("Warm"),
                             Standby=bool(here) and all(n.get("NodeStatus") == "StandBy" for n in here if n.get("NodeType") == "Data"))
            else:
                attrs.update(Data=config.get("InstanceCount", 0) // max(1, len(chosen)))
            search = node("search_node", domain["ARN"] + "#" + subnet, {**attrs, "DisplayName": domain["DomainName"]},
                          "DescribeDomainNodes.AvailabilityZone" if listed else "DescribeDomains.VPCOptions.SubnetIds[]")
            link(search, "vpc", options.get("VPCId"), "in_vpc", "DescribeDomains.VPCOptions.VPCId")
            link(search, "subnet", subnet, "in_subnet", "DescribeDomains.VPCOptions.SubnetIds[]", {"availability_zone": zone}, {"availability_zone": zone})
            link(search, "opensearch_domain", domain["ARN"], "node_of", "DescribeDomains.ARN")
    systems = {f["FileSystemId"]: f for f in raw.get("efs_file_systems", [])}
    for system in systems.values():
        node("efs_file_system", system["FileSystemId"], system, "DescribeFileSystems")
    for target in raw.get("efs_mount_targets", []):
        system = systems.get(target.get("FileSystemId"), {})
        tags = {t["Key"]: t.get("Value", "") for t in system.get("Tags", [])}
        key = node("efs_mount_target", target["MountTargetId"],
                   {**target, "DisplayName": system.get("Name") or tags.get("Name") or target.get("FileSystemId"),
                    "OneZone": bool(system.get("AvailabilityZoneName")), "ThroughputMode": system.get("ThroughputMode"),
                    "PerformanceMode": system.get("PerformanceMode"), "Encrypted": system.get("Encrypted")}, "DescribeMountTargets")
        link(key, "vpc", target.get("VpcId") or subnets.get(target.get("SubnetId"), {}).get("VpcId"), "in_vpc", "DescribeMountTargets.VpcId")
        link(key, "subnet", target.get("SubnetId"), "in_subnet", "DescribeMountTargets.SubnetId",
             {"availability_zone": target.get("AvailabilityZoneName")}, {"availability_zone": target.get("AvailabilityZoneName")})
        link(key, "efs_file_system", target.get("FileSystemId"), "mount_target_of", "DescribeMountTargets.FileSystemId")
    groups = {g["ClusterSubnetGroupName"]: g for g in raw.get("redshift_subnet_groups", [])}
    for cluster in raw.get("redshift_clusters", []):
        group = groups.get(cluster.get("ClusterSubnetGroupName"), {})

        def subnet_in(zone):
            matches = [s["SubnetIdentifier"] for s in group.get("Subnets", []) if (s.get("SubnetAvailabilityZone") or {}).get("Name") == zone]
            return matches[0] if len(matches) == 1 else None

        secondary = cluster.get("MultiAZSecondary") or {}
        multi = str(cluster.get("MultiAZ", "")).lower() == "enabled"
        for suffix, zone, role, nodes_in in (("", cluster.get("AvailabilityZone"), "primary" if multi else None, cluster.get("ClusterNodes", [])),
                                             ("#secondary", secondary.get("AvailabilityZone"), "secondary", secondary.get("ClusterNodes", []))):
            if not zone:
                continue
            attrs = {**cluster, "AvailabilityZone": zone, "Role": role, "NodesHere": len(nodes_in) or cluster.get("NumberOfNodes"),
                     "DisplayName": cluster["ClusterIdentifier"]} if suffix else {**cluster, "Role": role, "NodesHere": len(nodes_in) or cluster.get("NumberOfNodes")}
            key = node("redshift_cluster", cluster["ClusterIdentifier"] + suffix, attrs, "DescribeClusters" + (".MultiAZSecondary" if suffix else ""))
            link(key, "vpc", cluster.get("VpcId"), "in_vpc", "DescribeClusters.VpcId")
            link(key, "subnet", subnet_in(zone), "in_subnet", "DescribeClusters.AvailabilityZone + DescribeClusterSubnetGroups.Subnets[]",
                 {"availability_zone": zone}, {"availability_zone": zone})
            if suffix:
                link(key, "redshift_cluster", cluster["ClusterIdentifier"], "standby_of", "DescribeClusters.MultiAZSecondary")


def connectivity(raw, prefix, nodes, node, link):
    """VPC peering, virtual private gateways and Site-to-Site VPN to customer gateways."""
    local = {v["VpcId"] for v in raw.get("vpcs", [])}
    for peering in raw.get("vpc_peering_connections", []):
        sides = [(peering.get(side) or {}).get("VpcId") for side in ("RequesterVpcInfo", "AccepterVpcInfo")]
        mine = [v for v in sides if v in local]
        if not mine:
            continue
        key = node("vpc_peering", peering["VpcPeeringConnectionId"], peering, "DescribeVpcPeeringConnections")
        link(key, "vpc", mine[0], "in_vpc", "DescribeVpcPeeringConnections.RequesterVpcInfo/AccepterVpcInfo.VpcId")
        if len(mine) == 2:
            # Both ends collected: the peering is drawn in each VPC's detail view.
            link(key, "vpc", mine[1], "peer_vpc", "DescribeVpcPeeringConnections.AccepterVpcInfo.VpcId")
    for gateway in raw.get("vpn_gateways", []):
        key = node("vpn_gateway", gateway["VpnGatewayId"], gateway, "DescribeVpnGateways")
        for attachment in gateway.get("VpcAttachments", []):
            if attachment.get("State") == "attached":
                link(key, "vpc", attachment.get("VpcId"), "in_vpc", "DescribeVpnGateways.VpcAttachments[].VpcId")
    customers = {c["CustomerGatewayId"]: c for c in raw.get("customer_gateways", [])}
    tunnels = {}
    for connection in raw.get("vpn_connections", []):
        if connection.get("State") in ("deleted", "deleting"):
            continue
        telemetry = connection.get("VgwTelemetry", [])
        up = sum(1 for t in telemetry if t.get("Status") == "UP")
        tunnels.setdefault(connection.get("CustomerGatewayId"), []).append(f"{up}/{len(telemetry)}")
        customer = connection.get("CustomerGatewayId")
        if customer and customer not in customers:
            customers[customer] = {"CustomerGatewayId": customer}
        hub = ("vpn_gateway", connection["VpnGatewayId"]) if connection.get("VpnGatewayId") else ("transit_gateway", connection.get("TransitGatewayId"))
        if hub[1]:
            source = node(hub[0], hub[1], reference=True)
            link(source, "customer_gateway", customer, "vpn_connection", f"DescribeVpnConnections.{'VpnGatewayId' if hub[0] == 'vpn_gateway' else 'TransitGatewayId'} + CustomerGatewayId",
                 {"VpnConnectionId": connection.get("VpnConnectionId"), "State": connection.get("State"), "Tunnels": f"{up}/{len(telemetry)} UP",
                  "StaticRoutesOnly": (connection.get("Options") or {}).get("StaticRoutesOnly")},
                 {"destination": connection.get("VpnConnectionId")})
    for customer in customers.values():
        node("customer_gateway", customer["CustomerGatewayId"], {**customer, "VpnTunnels": tunnels.get(customer["CustomerGatewayId"], [])},
             "DescribeCustomerGateways", reference="IpAddress" not in customer)


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
        elif any(str(r.get("GatewayId", "")).startswith("vgw-") for r in default):
            route_class = "vgw"
        else:
            route_class = "isolated"
        nodes[key]["route_class"] = route_class
        nodes[key]["route_table"] = table["RouteTableId"]
