"""Synthetic fixtures for layout scale checks. Never presented as live account data."""
from diagram_bot.demo import demo_snapshot

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
