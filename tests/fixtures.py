"""Synthetic fixtures for layout scale checks. Never presented as live account data."""
from diagram_bot.demo import demo_snapshot
from diagram_bot.designer import Design

ARN = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:"


def scale_snapshot(vpcs=2, azs=3, instances_per_subnet=12, load_balancers=3, groups_per_lb=2):
    """About 100 resources: VPCs, subnets per AZ, instances, ALBs and target groups."""
    snapshot = demo_snapshot()
    raw = {"vpcs": [], "subnets": [], "instances": [], "load_balancers": [], "target_groups": [], "target_health": []}
    for v in range(vpcs):
        vpc = f"vpc-scale-{v}"
        raw["vpcs"].append({"VpcId": vpc, "CidrBlock": f"10.{50 + v}.0.0/16", "Tags": [{"Key": "Name", "Value": f"규모 검증 네트워크 {v}"}]})
        ids = []
        for a in range(azs):
            az = f"ap-northeast-2{'abcd'[a]}"
            subnet = f"subnet-scale-{v}-{a}"
            raw["subnets"].append({"SubnetId": subnet, "VpcId": vpc, "AvailabilityZone": az, "CidrBlock": f"10.{50 + v}.{a}.0/24"})
            for i in range(instances_per_subnet):
                instance = f"i-scale-{v}-{a}-{i}"
                ids.append(instance)
                raw["instances"].append({"InstanceId": instance, "VpcId": vpc, "SubnetId": subnet,
                                         "Tags": [{"Key": "Name", "Value": f"app-{v}-{a}-{i}"}], "SecurityGroups": [{"GroupId": f"sg-scale-{v}"}]})
        for b in range(load_balancers):
            lb = f"{ARN}loadbalancer/app/scale-{v}-{b}/{b}"
            raw["load_balancers"].append({"LoadBalancerArn": lb, "LoadBalancerName": f"scale-{v}-{b}", "Type": "application", "VpcId": vpc,
                                          "Scheme": "internal", "AvailabilityZones": [{"ZoneName": f"ap-northeast-2{'abcd'[a]}", "SubnetId": f"subnet-scale-{v}-{a}"} for a in range(azs)]})
            for g in range(groups_per_lb):
                tg = f"{ARN}targetgroup/scale-{v}-{b}-{g}/{g}"
                raw["target_groups"].append({"TargetGroupArn": tg, "TargetGroupName": f"scale-{v}-{b}-{g}", "VpcId": vpc, "TargetType": "instance",
                                             "Port": 8000 + g, "Protocol": "HTTP", "LoadBalancerArns": [lb]})
                members = ids[(b * groups_per_lb + g) % len(ids)::load_balancers * groups_per_lb]
                raw["target_health"].append({"TargetGroupArn": tg, "Descriptions": [{"Target": {"Id": m, "Port": 8000 + g}} for m in members]})
    snapshot["raw"] = raw
    snapshot["coverage"] = [{"service": "ec2", "operation": "describe_vpcs", "status": "complete"}]
    return snapshot


def edge_snapshot():
    """Edge, Elastic IPs, TGW routing domains and Direct Connect around three VPCs: a TGW-attached web VPC behind CloudFront
    with WAF, an unattached VPC whose internet ALB is a CloudFront origin without WAF, and a VPC whose virtual private
    gateway has a private VIF and a VPN; a Direct Connect gateway joins the TGW over two transit VIFs."""
    from diagram_bot.samples import Sample
    s = Sample("edge", "Edge and hybrid", [])
    web = s.vpc("vpc-edge-web", "10.80.0.0/16", "web")
    pub = [s.subnet(f"subnet-edge-pub-{az}", web, az, f"10.80.{i}.0/24", f"web-public-{az}", "public") for i, az in enumerate("ab")]
    app = [s.subnet(f"subnet-edge-app-{az}", web, az, f"10.80.{10 + i}.0/24", f"web-app-{az}", "app") for i, az in enumerate("ab")]
    s.network(web, "edge", "ab", pub, [app])
    nodes = [s.instance(f"i-edgeweb{az}", app[i], f"web-{az}", sg="sg-edge-web") for i, az in enumerate("ab")]
    alb = s.load_balancer("web-alb", web, pub, sg="sg-edge-alb")
    s.target_group("web", web, "HTTPS", 443, nodes, [alb])
    s.security_group("sg-edge-alb", web, "web-alb", [(443, "0.0.0.0/0")])
    hub = s.transit_gateway("tgw-edge0000000001", "edge-tgw")
    s.tgw_attachment("tgw-attach-edge-web", hub, web, app, "web-attachment")
    shop = s.vpc("vpc-edge-shop", "10.81.0.0/16", "shop")
    shop_pub = [s.subnet(f"subnet-shop-pub-{az}", shop, az, f"10.81.{i}.0/24", f"shop-public-{az}", "public") for i, az in enumerate("ab")]
    s.route_table("rtb-shop-public", shop, "shop-public", shop_pub, gateway=s.internet_gateway("igw-shop", shop, "shop-igw"))
    shop_alb = s.load_balancer("shop-alb", shop, shop_pub, sg="sg-shop-alb")
    s.security_group("sg-shop-alb", shop, "shop-alb", [(443, "pl-22a6434b")])
    corp = s.vpc("vpc-edge-corp", "10.82.0.0/16", "corp")
    corp_app = [s.subnet(f"subnet-corp-app-{az}", corp, az, f"10.82.{i}.0/24", f"corp-app-{az}", "app") for i, az in enumerate("ab")]
    s.route_table("rtb-corp-main", corp, "corp-main", main=True)
    s.instance("i-edgecorp", corp_app[0], "corp-app")
    s.vpn("vpn-edge-corp", "cgw-edge-corp", "203.0.113.70", "corp-cgw", vgw="vgw-edge-corp", vpc=corp)
    # Private VIF straight onto the virtual private gateway, transit VIFs through a Direct Connect gateway to the TGW.
    s.raw.setdefault("dx_connections", []).append({"connectionId": "dxcon-edge-corp", "connectionName": "corp-dx", "location": "loc-c",
                                                   "bandwidth": "1Gbps", "connectionState": "available"})
    s.raw.setdefault("dx_virtual_interfaces", []).append({"virtualInterfaceId": "dxvif-corp", "virtualInterfaceType": "private",
                                                          "connectionId": "dxcon-edge-corp", "vlan": 300, "virtualGatewayId": "vgw-edge-corp",
                                                          "virtualInterfaceState": "available", "bgpPeers": [{"bgpStatus": "down"}]})
    dxgw = s.dx_gateway("edge-dxgw", tgw=hub, prefixes=["10.80.0.0/16"])
    s.direct_connect("dxcon-edge-1", "edge-dx-1", "loc-a", dxgw, 101)
    s.direct_connect("dxcon-edge-2", "edge-dx-2", "loc-b", dxgw, 102)
    s.raw["dx_gateway_associations"].append({"directConnectGatewayId": dxgw, "associationState": "associated",
                                             "associatedGateway": {"id": "tgw-other", "type": "transitGateway", "region": "us-west-2"}})
    acl = s.web_acl("edge-acl", "CLOUDFRONT")
    s.cloudfront("E1EDGEWEB", "www.example.org", [alb, "edge-static"], acl)
    s.cloudfront("E2EDGESHOP", "shop.example.org", [shop_alb])
    s.raw["cloudfront_distributions"][-1]["Origins"]["Items"].append({"Id": "cname", "DomainName": "origin.shop.example.org"})
    s.web_acl("shop-acl", "REGIONAL", [])
    s.elastic_ip("eipalloc-edgenat-a", "203.0.113.80", "nat-a-eip", nat="nat-edge-a")
    s.raw["elastic_ips"] += [{"AllocationId": "eipalloc-edge-i", "PublicIp": "203.0.113.81", "InstanceId": "i-edgeweba", "AssociationId": "x"},
                             {"AllocationId": "eipalloc-edge-idle", "PublicIp": "203.0.113.82"}]
    spokes = [("tgw-attach-edge-web", "vpc", web)]
    s.tgw_route_table("tgw-rtb-edge", hub, "edge-spokes", spokes, spokes, [("10.80.0.0/16", spokes), ("10.99.0.0/16", [])])
    return s.snapshot()


def full_design():
    """A chat-style design using every designer step: two VPCs, peering, VPN."""
    d = Design("쇼핑몰", "us-east-1")
    d.add_vpc("shop", "10.20.0.0/16", 3, ["public", "web", "app", "data"])
    d.add_load_balancer("shop", "public-alb")
    d.add_ec2_service("shop", "web", tier="web", load_balancer="public-alb", port=443)
    d.add_load_balancer("shop", "internal-alb", scheme="internal")
    d.add_ecs_service("shop", "orders", 2, "internal-alb")
    d.add_database("shop", "orders-db", "mysql", True, True)
    d.add_cache("shop", "session")
    d.add_vpc_endpoints("shop", ["s3", "ssm"])
    d.add_vpc("analytics", "10.30.0.0/16", 2, ["app", "data"], "none")
    d.add_eks_cluster("analytics", "ml")
    d.add_aurora("analytics", "warehouse")
    d.add_kafka("analytics", "events")
    d.add_opensearch("analytics", "logs")
    d.add_file_system("analytics", "shared")
    d.add_lambda("thumbnailer")
    d.add_lambda("etl", vpc="analytics")
    d.connect_vpcs("shop", "analytics")
    d.add_vpn("analytics", "198.51.100.7")
    return d
