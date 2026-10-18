"""End-to-end collection against an AWS API emulator (moto): real boto3 request/response serialization,
pagination and the parallel collector, from a built environment to graph, review and report.

The emulator is not AWS. Where it lacks an operation, the test asserts the collector's degradation
(the API marked unavailable, everything else collected) instead of hiding the gap.
"""
import importlib
import io
import json
import zipfile

import pytest

moto = pytest.importorskip("moto")
boto3 = pytest.importorskip("boto3")

from diagram_bot.collector import collect  # noqa: E402
from diagram_bot.drawio import generate_document, layout_document, validate_document  # noqa: E402
from diagram_bot.findings import review  # noqa: E402
from diagram_bot.graph import build_graph  # noqa: E402
from diagram_bot.report import write_report  # noqa: E402

REGION, ACCOUNT = "ap-northeast-2", "123456789012"
# Operations the emulator does not implement; real AWS answers them.
EMULATOR_GAPS = {"list_nodes", "describe_domain_nodes", "describe_placement_groups",  # not implemented in moto
                 "describe_virtual_interfaces", "describe_direct_connect_gateways"}


def build(s, steps):
    ec2, R = s.client("ec2"), s.region_name
    def step(name, fn):
        try:
            out = fn()
            steps[name] = "ok"
            return out
        except Exception as exc:  # emulator gaps are recorded, not hidden
            steps[name] = f"{type(exc).__name__}: {str(exc)[:120]}"
    vpc = ec2.create_vpc(CidrBlock="10.0.0.0/16")["Vpc"]["VpcId"]
    ec2.create_tags(Resources=[vpc], Tags=[{"Key": "Name", "Value": "prod"}])
    sub = {}
    for tier, base in (("public", 0), ("app", 10), ("data", 20)):
        for i, az in enumerate("ac"):
            sid = ec2.create_subnet(VpcId=vpc, CidrBlock=f"10.0.{base + i}.0/24", AvailabilityZone=f"{R}{az}")["Subnet"]["SubnetId"]
            ec2.create_tags(Resources=[sid], Tags=[{"Key": "Name", "Value": f"{tier}-{az}"}, {"Key": "Tier", "Value": tier}])
            sub[f"{tier}-{az}"] = sid
    igw = ec2.create_internet_gateway()["InternetGateway"]["InternetGatewayId"]
    ec2.attach_internet_gateway(InternetGatewayId=igw, VpcId=vpc)
    public_rt = ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=public_rt, DestinationCidrBlock="0.0.0.0/0", GatewayId=igw)
    for az in "ac":
        ec2.associate_route_table(RouteTableId=public_rt, SubnetId=sub[f"public-{az}"])
    eip = ec2.allocate_address(Domain="vpc")["AllocationId"]
    nat = ec2.create_nat_gateway(SubnetId=sub["public-a"], AllocationId=eip)["NatGateway"]["NatGatewayId"]
    private_rt = ec2.create_route_table(VpcId=vpc)["RouteTable"]["RouteTableId"]
    ec2.create_route(RouteTableId=private_rt, DestinationCidrBlock="0.0.0.0/0", NatGatewayId=nat)
    for az in "ac":
        ec2.associate_route_table(RouteTableId=private_rt, SubnetId=sub[f"app-{az}"])
    alb_sg = ec2.create_security_group(GroupName="alb", Description="alb", VpcId=vpc)["GroupId"]
    web_sg = ec2.create_security_group(GroupName="web", Description="web", VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=alb_sg, IpPermissions=[{"IpProtocol": "tcp", "FromPort": 443, "ToPort": 443, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    ec2.authorize_security_group_ingress(GroupId=web_sg, IpPermissions=[{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]}])
    image = ec2.describe_images()["Images"][0]["ImageId"]
    instances = [ec2.run_instances(ImageId=image, MinCount=1, MaxCount=1, InstanceType="t3.small", SubnetId=sub[f"app-{az}"],
                                   SecurityGroupIds=[web_sg], MetadataOptions={"HttpTokens": "required"},
                                   TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": f"web-{az}"}]}])["Instances"][0]["InstanceId"]
                 for az in "ac"]
    elb = s.client("elbv2")
    alb = elb.create_load_balancer(Name="prod-alb", Subnets=[sub["public-a"], sub["public-c"]], SecurityGroups=[alb_sg])["LoadBalancers"][0]["LoadBalancerArn"]
    tg = elb.create_target_group(Name="web", Protocol="HTTP", Port=80, VpcId=vpc, TargetType="instance")["TargetGroups"][0]["TargetGroupArn"]
    elb.create_listener(LoadBalancerArn=alb, Protocol="HTTP", Port=80, DefaultActions=[{"Type": "forward", "TargetGroupArn": tg}])
    elb.register_targets(TargetGroupArn=tg, Targets=[{"Id": i} for i in instances])
    def asg():
        lt = ec2.create_launch_template(LaunchTemplateName="app", LaunchTemplateData={"ImageId": image, "InstanceType": "t3.small"})["LaunchTemplate"]["LaunchTemplateId"]
        s.client("autoscaling").create_auto_scaling_group(AutoScalingGroupName="app-asg", LaunchTemplate={"LaunchTemplateId": lt}, MinSize=2, MaxSize=4,
                                                          DesiredCapacity=2, VPCZoneIdentifier=f"{sub['app-a']},{sub['app-c']}", TargetGroupARNs=[tg])
    step("autoscaling", asg)
    def rds():
        r = s.client("rds")
        r.create_db_subnet_group(DBSubnetGroupName="data", DBSubnetGroupDescription="data", SubnetIds=[sub["data-a"], sub["data-c"]])
        r.create_db_instance(DBInstanceIdentifier="orders-db", DBInstanceClass="db.r6g.large", Engine="postgres", MasterUsername="admin",
                             MasterUserPassword="password123", AllocatedStorage=20, DBSubnetGroupName="data", MultiAZ=True,
                             AvailabilityZone=f"{R}a", StorageEncrypted=True, PubliclyAccessible=False)
    step("rds", rds)
    def cache():
        c = s.client("elasticache")
        c.create_cache_subnet_group(CacheSubnetGroupName="data", CacheSubnetGroupDescription="data", SubnetIds=[sub["data-a"], sub["data-c"]])
        c.create_replication_group(ReplicationGroupId="sessions", ReplicationGroupDescription="sessions", Engine="redis", CacheNodeType="cache.t4g.small",
                                   NumCacheClusters=2, CacheSubnetGroupName="data", AutomaticFailoverEnabled=True, MultiAZEnabled=True)
    step("elasticache", cache)
    def efs():
        e = s.client("efs")
        fs = e.create_file_system(CreationToken="shared", Encrypted=True, Tags=[{"Key": "Name", "Value": "shared"}])["FileSystemId"]
        for az in "ac":
            e.create_mount_target(FileSystemId=fs, SubnetId=sub[f"app-{az}"])
    step("efs", efs)
    def redshift():
        r = s.client("redshift")
        r.create_cluster_subnet_group(ClusterSubnetGroupName="warehouse", Description="w", SubnetIds=[sub["data-a"], sub["data-c"]])
        r.create_cluster(ClusterIdentifier="warehouse", NodeType="ra3.xlplus", MasterUsername="admin", MasterUserPassword="Password123",
                         ClusterSubnetGroupName="warehouse", NumberOfNodes=2, Encrypted=True, AvailabilityZone=f"{R}a")
    step("redshift", redshift)
    def eks():
        k = s.client("eks")
        k.create_cluster(name="platform", roleArn="arn:aws:iam::123456789012:role/eks", resourcesVpcConfig={"subnetIds": [sub["app-a"], sub["app-c"]]})
        k.create_nodegroup(clusterName="platform", nodegroupName="general", subnets=[sub["app-a"], sub["app-c"]], nodeRole="arn:aws:iam::123456789012:role/node",
                           scalingConfig={"minSize": 2, "maxSize": 4, "desiredSize": 2})
    step("eks", eks)
    def ecs():
        c = s.client("ecs")
        c.create_cluster(clusterName="apps")
        c.register_task_definition(family="api", networkMode="awsvpc", requiresCompatibilities=["FARGATE"], cpu="256", memory="512",
                                   containerDefinitions=[{"name": "api", "image": "nginx", "portMappings": [{"containerPort": 80}]}])
        c.create_service(cluster="apps", serviceName="api", taskDefinition="api", desiredCount=2, launchType="FARGATE",
                         networkConfiguration={"awsvpcConfiguration": {"subnets": [sub["app-a"], sub["app-c"]], "securityGroups": [web_sg]}})
        c.run_task(cluster="apps", taskDefinition="api", count=2, launchType="FARGATE", startedBy="ecs-svc",
                   networkConfiguration={"awsvpcConfiguration": {"subnets": [sub["app-a"]], "securityGroups": [web_sg]}})
    step("ecs", ecs)
    def opensearch():
        s.client("opensearch").create_domain(DomainName="logs", EngineVersion="OpenSearch_2.11",
                                             ClusterConfig={"InstanceType": "r6g.large.search", "InstanceCount": 2, "ZoneAwarenessEnabled": True},
                                             VPCOptions={"SubnetIds": [sub["data-a"], sub["data-c"]]}, EncryptionAtRestOptions={"Enabled": True})
    step("opensearch", opensearch)
    def msk():
        s.client("kafka").create_cluster_v2(ClusterName="events", Provisioned={"KafkaVersion": "3.6.0", "NumberOfBrokerNodes": 2,
                                            "BrokerNodeGroupInfo": {"InstanceType": "kafka.m5.large", "ClientSubnets": [sub["data-a"], sub["data-c"]]}})
    step("msk", msk)
    def lam():
        role = s.client("iam").create_role(RoleName="lambda", AssumeRolePolicyDocument='{"Version":"2012-10-17","Statement":[]}')["Role"]["Arn"]
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("index.py", "def handler(e, c):\n    return e\n")
        s.client("lambda").create_function(FunctionName="consumer", Runtime="python3.12", Role=role,
                                           Handler="index.handler", Code={"ZipFile": buf.getvalue()},
                                           VpcConfig={"SubnetIds": [sub["app-a"], sub["app-c"]], "SecurityGroupIds": [web_sg]})
    step("lambda", lam)
    step("vpc_endpoint", lambda: ec2.create_vpc_endpoint(VpcId=vpc, ServiceName=f"com.amazonaws.{R}.s3", RouteTableIds=[private_rt]))
    def vpn():
        vgw = ec2.create_vpn_gateway(Type="ipsec.1")["VpnGateway"]["VpnGatewayId"]
        ec2.attach_vpn_gateway(VpcId=vpc, VpnGatewayId=vgw)
        cgw = ec2.create_customer_gateway(Type="ipsec.1", PublicIp="203.0.113.10", BgpAsn=65000)["CustomerGateway"]["CustomerGatewayId"]
        ec2.create_vpn_connection(Type="ipsec.1", CustomerGatewayId=cgw, VpnGatewayId=vgw)
    step("vpn", vpn)
    def peering():
        other = ec2.create_vpc(CidrBlock="10.1.0.0/16")["Vpc"]["VpcId"]
        ec2.create_vpc_peering_connection(VpcId=vpc, PeerVpcId=other)
    step("peering", peering)
    def tgw():
        t = ec2.create_transit_gateway()["TransitGateway"]["TransitGatewayId"]
        ec2.create_transit_gateway_vpc_attachment(TransitGatewayId=t, VpcId=vpc, SubnetIds=[sub["app-a"], sub["app-c"]])
    step("tgw", tgw)
    idle = ec2.allocate_address(Domain="vpc")["AllocationId"]  # reserved, attached to nothing
    return {"vpc": vpc, "subnets": sub, "instances": instances, "alb": alb, "tg": tg, "web_sg": web_sg, "nat": nat, "eip": eip, "idle": idle}

@pytest.fixture
def environment(monkeypatch):
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "testing",
                       "AWS_DEFAULT_REGION": REGION, "AWS_CONFIG_FILE": "/dev/null", "AWS_SHARED_CREDENTIALS_FILE": "/dev/null"}.items():
        monkeypatch.setenv(key, value)
    # moto loads service backends lazily; the parallel collector would race on those imports.
    for name in ("ec2", "elb", "elbv2", "rds", "autoscaling", "elasticache", "kafka", "awslambda", "eks", "ecs", "efs", "redshift",
                 "opensearch", "es", "sts", "iam", "fsx", "sagemaker", "cloudfront", "wafv2", "directconnect"):
        importlib.import_module(f"moto.{name}.models")
    with moto.mock_aws():
        session = boto3.Session(region_name=REGION)
        steps = {}
        built = build(session, steps)
        yield session, built, steps


def test_collects_a_built_environment_end_to_end(environment, tmp_path):
    session, built, steps = environment
    assert {k for k, v in steps.items() if v != "ok"} <= {"ecs"}, {k: v for k, v in steps.items() if v != "ok"}  # emulator rejects Fargate run_task
    snapshot = collect(REGION, session=session, expected_account=ACCOUNT)
    gaps = {c["operation"]: c for c in snapshot["coverage"] if c["status"] != "complete"}
    assert set(gaps) <= EMULATOR_GAPS, gaps
    assert all(c["status"] == "unavailable" and c["permission"] for c in gaps.values())
    graph = build_graph(snapshot)
    nodes = {n["id"]: n for n in graph["nodes"]}
    by_resource = {n["resource_id"]: n for n in graph["nodes"]}
    edges = graph["edges"]
    placed = lambda resource: {nodes[e["target"]]["name"] for e in edges if e["source"] == by_resource[resource]["id"] and e["relation"] == "in_subnet"}
    assert [placed(i) for i in built["instances"]] == [{"app-a"}, {"app-c"}]
    assert placed(built["alb"]) == {"public-a", "public-c"}
    targets = {nodes[e["target"]]["resource_id"] for e in edges if e["relation"] == "registered_target" and e["source"] == by_resource[built["tg"]]["id"]}
    assert set(built["instances"]) <= targets
    classes = {n["name"]: n.get("route_class") for n in graph["nodes"] if n["kind"] == "subnet"}
    assert classes["public-a"] == "public" and classes["app-c"] == "nat" and classes["data-a"] == "isolated"
    kinds = {n["kind"] for n in graph["nodes"] if not n["reference_only"]}
    assert {"auto_scaling_group", "db_instance", "cache_replication_group", "efs_mount_target", "redshift_cluster", "eks_cluster",
            "ecs_service", "search_node", "msk_broker", "lambda_function", "vpc_endpoint", "vpn_gateway", "customer_gateway",
            "vpc_peering", "transit_gateway", "tgw_attachment", "nat_gateway", "internet_gateway"} <= kinds
    assert placed("orders-db") == {"data-a"}
    # Without ListNodes/DescribeDomainNodes the even spread is shown and marked as an estimate.
    assert all(n["attributes"]["Estimated"] for n in graph["nodes"] if n["kind"] in ("msk_broker", "search_node"))
    assert any(e["relation"] == "vpn_connection" for e in edges)
    # The NAT gateway's Elastic IP is joined by allocation ID and shown on the NAT; the spare one is reported as idle.
    nat = by_resource[built["nat"]]
    assert nat["elastic_ips"] and any(e["relation"] == "eip_associated" and e["target"] == nat["id"] for e in edges)
    # The Transit Gateway's default route table is associated with the VPC attachment (its routing domain).
    table = next(n for n in graph["nodes"] if n["kind"] == "tgw_route_table")
    attachment = next(n for n in graph["nodes"] if n["kind"] == "tgw_attachment" and not n["reference_only"])
    assert any(e["relation"] == "tgw_rt_association" and e["source"] == table["id"] and e["target"] == attachment["id"] for e in edges)
    assert attachment["tgw_route_table"] == table["name"]
    findings = {(f["rule"], f["resource_name"]) for f in review(graph, snapshot["coverage"])}
    assert ("SEC-SG-OPEN", "web") in findings and ("OPS-COVERAGE", "수집 범위") in findings
    idle = by_resource[built["idle"]]["name"]
    assert ("COST-EIP-IDLE", idle) in findings and ("COST-EIP-IDLE", by_resource[built["eip"]]["name"]) not in findings
    # No regional web ACL in the account and WAF fully read: the internet-facing ALB is reported (low). The emulator
    # omits DescribeLoadBalancers.Scheme, which AWS always returns (internet-facing by default).
    assert next(c for c in snapshot["coverage"] if c["operation"] == "list_web_acls")["status"] == "complete"
    for lb in snapshot["raw"]["load_balancers"]:
        lb.setdefault("Scheme", "internet-facing")
    assert ("SEC-ALB-WAF", "prod-alb") in {(f["rule"], f["resource_name"]) for f in review(build_graph(snapshot), snapshot["coverage"])}
    assert ("SEC-IMDSV1", "web-a") not in findings  # launched with HttpTokens=required
    layout = layout_document(snapshot, "detail", built["vpc"])
    from test_drawio import assert_no_shared_segments, assert_readable
    assert_readable(layout)
    assert_no_shared_segments(layout)
    pages = validate_document(generate_document(snapshot)).findall("diagram")
    # Overview + one page per VPC node: collected VPCs (incl. the default VPC) and any VPC known only by
    # reference (the emulator reports a placeholder VpcConfig.VpcId for Lambda).
    assert len(pages) == 1 + sum(n["kind"] == "vpc" for n in graph["nodes"])
    write_report(snapshot, tmp_path)
    assert {p.name for p in tmp_path.iterdir()} >= {"snapshot.json", "topology.drawio", "index.html", "inventory.csv", "review.md"}
    assert "SEC-SG-OPEN" in (tmp_path / "review.md").read_text(encoding="utf-8")
    json.dumps(snapshot, default=str)


def test_cross_account_role_through_sts(environment):
    session, built, _ = environment
    role = f"arn:aws:iam::{ACCOUNT}:role/DiagramBotReadOnly"
    snapshot = collect(REGION, session=session, expected_account=ACCOUNT, role_arn=role, external_id="ext-1234")
    assert snapshot["metadata"]["role_arn"] == role
    assert any(v["VpcId"] == built["vpc"] for v in snapshot["raw"]["vpcs"])
    with pytest.raises(ValueError, match="계정 불일치"):
        collect(REGION, session=session, expected_account="999999999999", role_arn=role)
