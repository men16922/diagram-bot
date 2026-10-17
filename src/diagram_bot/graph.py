"""Deterministic joins. No traffic inference or IP-to-instance guessing."""


def build_graph(snapshot):
    nodes, edges = {}, []
    account, region = snapshot["metadata"]["account_id"], snapshot["metadata"]["region"]
    prefix = f"{account}/{region}/"

    def node(kind, resource_id, attrs=None, source=None, reference=False):
        key = prefix + kind + "/" + resource_id
        if key not in nodes or not reference:
            attrs = attrs or {}
            tags = {t["Key"]: t.get("Value", "") for t in attrs.get("Tags", [])}
            nodes[key] = {"id": key, "resource_id": resource_id, "kind": kind,
                          "name": tags.get("Name") or attrs.get("LoadBalancerName") or attrs.get("TargetGroupName") or resource_id,
                          "account": account, "region": region, "attributes": attrs,
                          "reference_only": reference, "source": source}
        return key

    def link(source, kind, resource_id, relation, evidence, details=None):
        if not resource_id:
            return
        target = node(kind, resource_id, reference=True)
        edges.append({"source": source, "target": target, "relation": relation,
                      "evidence": evidence, "details": details or {}, "inferred": False})

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
                    link(key, "subnet", az.get("SubnetId"), "in_subnet", api + ".AvailabilityZones[].SubnetId", {"availability_zone": az.get("ZoneName")})
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
            link(key, kind, rid, "registered_target", "DescribeTargetHealth.Target.Id", description)
    return {"nodes": sorted(nodes.values(), key=lambda x: x["id"]),
            "edges": sorted(edges, key=lambda x: (x["source"], x["relation"], x["target"]))}
