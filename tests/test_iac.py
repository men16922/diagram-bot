"""Snapshot → CloudFormation draft: self-contained references, no collected physical IDs for created resources,
unsupported items listed instead of invented, and (with the moto[cloudformation] extra) cfn-lint and an emulated stack."""
import json
import re

import pytest

from diagram_bot import cli
from diagram_bot.demo import demo_snapshot
from diagram_bot.designer import Design
from diagram_bot.iac import PSEUDO, template, templates
from diagram_bot.samples import SAMPLES
import test_aws_emulator

emulated_account = test_aws_emulator.environment  # the emulator test's moto-built account, as a fixture of this module

# Physical IDs of resources the template recreates; none may appear as a value in Resources or Outputs.
ID_FIELDS = {"vpcs": "VpcId", "subnets": "SubnetId", "internet_gateways": "InternetGatewayId", "nat_gateways": "NatGatewayId",
             "route_tables": "RouteTableId", "security_groups": "GroupId", "vpc_endpoints": "VpcEndpointId", "transit_gateways": "TransitGatewayId",
             "transit_gateway_attachments": "TransitGatewayAttachmentId", "vpc_peering_connections": "VpcPeeringConnectionId",
             "vpn_gateways": "VpnGatewayId", "customer_gateways": "CustomerGatewayId", "vpn_connections": "VpnConnectionId", "instances": "InstanceId",
             "load_balancers": "LoadBalancerArn", "target_groups": "TargetGroupArn", "efs_file_systems": "FileSystemId",
             "efs_mount_targets": "MountTargetId", "fsx_file_systems": "FileSystemId"}


def all_snapshots():
    return {"demo": demo_snapshot(), **{key: build() for key, build in SAMPLES.items()}}


def walk(node):
    if isinstance(node, dict):
        yield node
        for value in node.values():
            yield from walk(value)
    elif isinstance(node, list):
        for value in node:
            yield from walk(value)


def strings(node):
    if isinstance(node, str):
        yield node
    elif isinstance(node, dict):
        for value in node.values():
            yield from strings(value)
    elif isinstance(node, list):
        for value in node:
            yield from strings(value)


def assert_self_contained(result, raw):
    """Every Ref/GetAtt/Sub/DependsOn target exists, every parameter is used, nothing is null, no physical ID is hardcoded."""
    assert json.loads(json.dumps(result, ensure_ascii=False)) == result
    resources, parameters = result["Resources"], result.get("Parameters", {})
    assert resources and all(re.fullmatch(r"[A-Za-z0-9]{1,255}", key) for key in [*resources, *parameters])
    assert not set(resources) & set(parameters)
    used = set()
    for node in walk({"Resources": resources, "Outputs": result.get("Outputs", {})}):
        assert None not in node.values(), node
        if "Ref" in node:
            assert node["Ref"] in resources or node["Ref"] in parameters or node["Ref"] in PSEUDO, node
            used.add(node["Ref"])
        if "Fn::GetAtt" in node:
            assert node["Fn::GetAtt"][0] in resources, node
        if "Fn::Sub" in node:
            for name in re.findall(r"\$\{([^}!]+)\}", node["Fn::Sub"]):
                assert name in resources or name in parameters or name in PSEUDO, node
                used.add(name)
    for resource in resources.values():
        assert resource["Type"].startswith("AWS::") and isinstance(resource["Properties"], dict)
        assert set(resource.get("DependsOn", [])) <= set(resources)
    assert set(parameters) <= used, set(parameters) - used
    physical = {item[field] for key, field in ID_FIELDS.items() for item in raw.get(key, []) if item.get(field)}
    leaked = physical & set(strings({"Resources": resources, "Outputs": result.get("Outputs", {})}))
    assert not leaked, leaked
    meta = result["Metadata"]["DiagramBot"]
    assert all({"Type", "Id", "Reason"} <= set(entry) and entry["Reason"] for entry in meta["Unsupported"])
    return meta


def kinds(result):
    counts = {}
    for resource in result["Resources"].values():
        counts[resource["Type"]] = counts.get(resource["Type"], 0) + 1
    return counts


def test_every_sample_exports_a_self_contained_template():
    for key, snapshot in all_snapshots().items():
        parts = snapshot.get("parts") or [snapshot]
        results = templates(snapshot)
        assert len(results) == len(parts), key
        for part, (label, result) in zip(parts, results):
            assert label == f"{part['metadata']['account_id']}-{part['metadata']['region']}"
            meta = assert_self_contained(result, part["raw"])
            assert meta["Source"]["region"] == part["metadata"]["region"] and meta["Assumptions"], key


def test_three_tier_template_matches_the_design():
    result = template(SAMPLES["three-tier"]())
    counts = kinds(result)
    assert counts["AWS::EC2::VPC"] == 1 and counts["AWS::EC2::Subnet"] == 12 and counts["AWS::EC2::NatGateway"] == counts["AWS::EC2::EIP"] == 3
    assert counts["AWS::ElasticLoadBalancingV2::LoadBalancer"] == counts["AWS::ElasticLoadBalancingV2::Listener"] == 2
    assert counts["AWS::AutoScaling::AutoScalingGroup"] == counts["AWS::EC2::LaunchTemplate"] == 2
    assert counts["AWS::RDS::DBInstance"] == 2 and counts["AWS::EC2::VPCEndpoint"] == 4
    # Only the edge is left out: CloudFront is global and the WAF rules are not collected.
    assert [u["Type"] for u in result["Metadata"]["DiagramBot"]["Unsupported"]] == ["CloudFrontDistribution", "WafWebAcl"]
    resources = result["Resources"]
    # Data subnets used the main table implicitly; CloudFormation gets explicit associations instead.
    isolated = [r for r in resources.values() if r["Type"] == "AWS::EC2::SubnetRouteTableAssociation"
                and r["Properties"]["RouteTableId"] == {"Ref": "RouteTableProdIsolated"}]
    assert len(isolated) == 3
    primary = resources["DbProdOrdersDb"]
    assert primary["Properties"]["MultiAZ"] and primary["Properties"]["ManageMasterUserPassword"] and primary["DeletionPolicy"] == "Snapshot"
    assert not any("MasterUserPassword" in r["Properties"] for r in resources.values())
    replica = resources["DbProdOrdersDbRo"]
    assert replica["Properties"]["SourceDBInstanceIdentifier"] == {"Ref": "DbProdOrdersDb"} and replica["DeletionPolicy"] == "Delete"
    assert replica["Properties"]["AvailabilityZone"] == {"Ref": "AvailabilityZoneC"}
    assert result["Parameters"]["AvailabilityZoneC"]["Default"] == "ap-northeast-2c"
    # Rules reference groups, never hardcoded IDs; the public ALB listener needs a certificate parameter.
    ingress = resources["SecurityGroupProdDbIngressTcp3306"]["Properties"]
    assert ingress["SourceSecurityGroupId"] == {"Ref": "SecurityGroupProdApp"} and ingress["FromPort"] == 3306
    listener = resources["LoadBalancerProdPublicAlbListenerHTTPS443"]["Properties"]
    assert listener["Certificates"] == [{"CertificateArn": {"Ref": "LoadBalancerProdPublicAlbCertificateArn"}}]
    assert resources["LoadBalancerProdInternalAlbListenerHTTP8080"]["Properties"]["Port"] == 8080  # the port its security group opens
    template_data = resources["AsgProdWebAsgLaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert template_data["ImageId"] == {"Ref": "ImageIdX8664"} and template_data["MetadataOptions"] == {"HttpTokens": "required"}
    assert resources["EndpointS3"]["Properties"]["ServiceName"] == {"Fn::Sub": "com.amazonaws.${AWS::Region}.s3"}
    assert len(resources["EndpointS3"]["Properties"]["RouteTableIds"]) == 3


def test_unexpressible_items_are_listed_not_invented():
    eks = template(SAMPLES["eks-platform"]())
    unsupported = {(u["Type"], u["Id"]) for u in eks["Metadata"]["DiagramBot"]["Unsupported"]}
    assert ("LambdaFunction", "order-events-consumer") in unsupported
    assert {u for u in unsupported if u[0] == "RegisteredTarget"} == {("RegisteredTarget", ip) for ip in ("10.60.17.21", "10.60.33.42", "10.60.49.13")}
    # EKS managed node groups own their Auto Scaling groups; the IAM roles are parameters.
    assert "AWS::AutoScaling::AutoScalingGroup" not in kinds(eks) and kinds(eks)["AWS::EKS::Nodegroup"] == 2
    assert {"EksClusterRoleArn", "EksNodeRoleArn"} <= set(eks["Parameters"])
    assert kinds(eks)["AWS::RDS::DBCluster"] == 1 and kinds(eks)["AWS::ElastiCache::ReplicationGroup"] == 1 and kinds(eks)["AWS::MSK::Cluster"] == 1
    gpu = template(SAMPLES["gpu-training"]())
    listed = {(u["Type"], u.get("Name")) for u in gpu["Metadata"]["DiagramBot"]["Unsupported"]}
    assert {("EC2Instance", f"p5-train-{n}") for n in range(1, 5)} <= listed and ("SageMakerHyperPodCluster", None) in listed
    trainer = gpu["Resources"]["InstanceTrn2Train1LaunchTemplate"]["Properties"]["LaunchTemplateData"]
    assert trainer["NetworkInterfaces"][0]["InterfaceType"] == "efa" and trainer["Placement"] == {"GroupName": {"Ref": "PlacementGroupTrn2Pg"}}
    assert trainer["CapacityReservationSpecification"]["CapacityReservationTarget"]["CapacityReservationId"] == {"Ref": "CapacityReservationCr0trn2reserved0011"}
    rag = template(SAMPLES["genai-rag"]())
    assert {u["Type"] for u in rag["Metadata"]["DiagramBot"]["Unsupported"]} == {"LambdaFunction", "SageMakerEndpoint"}
    service = rag["Resources"]["EcsServiceChatApi"]
    assert service["Properties"]["LoadBalancers"][0]["TargetGroupArn"] == {"Ref": "TargetGroupChatApiTasks"} and service["DependsOn"]
    assert "Default" not in rag["Parameters"]["EcsServiceChatApiImage"]  # never guessed
    data = template(SAMPLES["data-platform"]())
    assert kinds(data)["AWS::DocDB::DBCluster"] == 1 and kinds(data)["AWS::Redshift::Cluster"] == 1 and kinds(data)["AWS::EC2::VPNConnection"] == 2
    cache = next(r for r in data["Resources"].values() if r["Type"] == "AWS::ElastiCache::ReplicationGroup")["Properties"]
    assert cache["NumNodeGroups"] == 2 and cache["ReplicasPerNodeGroup"] == 1


def test_shared_transit_gateway_is_a_parameter_and_combined_snapshots_need_a_part():
    combined = SAMPLES["multi-account"]()
    with pytest.raises(ValueError, match="계정"):
        template(combined)
    (_, network), (_, prod), _ = templates(combined)
    assert kinds(network)["AWS::EC2::TransitGateway"] == 1 and kinds(network)["AWS::EC2::VPNConnection"] == 2
    assert "AWS::EC2::TransitGateway" not in kinds(prod)
    parameter = next(spec for spec in prod["Parameters"].values() if spec.get("Default") == "tgw-0sra000000000012")
    assert parameter["Type"] == "String"
    route = next(r for r in prod["Resources"].values() if r["Type"] == "AWS::EC2::Route")
    assert "Ref" in route["Properties"]["TransitGatewayId"] and route["DependsOn"]


def test_design_snapshot_exports_every_designer_resource():
    d = Design("IaC", "us-east-1")
    d.add_vpc("shop", "10.20.0.0/16", 3, ["public", "web", "app", "data"])
    d.add_vpc("ops", "10.30.0.0/16", 2, ["app", "data"], "none")
    d.add_load_balancer("shop", "front")
    d.add_load_balancer("shop", "inner", scheme="internal")
    d.add_ec2_service("shop", "web", tier="web", load_balancer="front", port=443)
    d.add_ecs_service("shop", "api", load_balancer="inner")
    d.add_eks_cluster("shop", "platform")
    d.add_gpu_node_group("shop", "platform", "train", "p5.48xlarge", training=True)
    d.add_fsx_lustre("shop", "checkpoints")
    d.add_database("shop", "orders", read_replica=True)
    d.add_aurora("shop", "vectors")
    d.add_cache("shop", "sessions")
    d.add_kafka("shop", "events")
    d.add_opensearch("shop", "logs", standby=True)
    d.add_file_system("shop", "assets")
    d.add_lambda("ingest", vpc="shop")
    d.add_sagemaker_endpoint("shop", "ranker")
    d.add_vpc_endpoints("shop", ["s3", "ssm"])
    d.add_vpc_endpoints("ops", ["s3"])
    d.connect_vpcs("shop", "ops")
    d.add_vpn("ops")
    snapshot = d.snapshot()
    result = template(snapshot)
    meta = assert_self_contained(result, snapshot["raw"])
    counts = kinds(result)
    for kind in ("AWS::ECS::Service", "AWS::EKS::Cluster", "AWS::EKS::Nodegroup", "AWS::FSx::FileSystem", "AWS::RDS::DBCluster", "AWS::MSK::Cluster",
                 "AWS::OpenSearchService::Domain", "AWS::EFS::MountTarget", "AWS::EC2::VPCPeeringConnection", "AWS::EC2::VPNGateway",
                 "AWS::EC2::VPNConnection", "AWS::EC2::PlacementGroup", "AWS::ElastiCache::ReplicationGroup"):
        assert counts.get(kind), kind
    # Each VPC keeps its own S3 endpoint; peering and VPN routes reference the template's own gateways.
    assert counts["AWS::EC2::VPCEndpoint"] == 3
    peering = [r for r in result["Resources"].values() if r["Type"] == "AWS::EC2::Route" and "VpcPeeringConnectionId" in r["Properties"]]
    vgw = [r for r in result["Resources"].values() if r["Type"] == "AWS::EC2::Route" and r["Properties"].get("DestinationCidrBlock") == "192.168.0.0/16"]
    assert len(peering) == 3 + 1 and vgw and all(r["DependsOn"] for r in vgw)
    assert result["Parameters"]["AvailabilityZoneA"]["Default"] == "us-east-1a"
    assert {u["Type"] for u in meta["Unsupported"]} == {"LambdaFunction", "SageMakerEndpoint"}
    # The training node group carries EFA and the cluster placement group through its launch template.
    nodegroup = next(r for k, r in result["Resources"].items() if r["Type"] == "AWS::EKS::Nodegroup" and k.endswith("Train"))
    launch = result["Resources"][nodegroup["Properties"]["LaunchTemplate"]["Id"]["Ref"]]["Properties"]["LaunchTemplateData"]
    assert launch["NetworkInterfaces"][0]["InterfaceType"] == "efa" and "Placement" in launch


def test_collected_snapshot_from_the_emulator(emulated_account):
    from test_aws_emulator import ACCOUNT, REGION
    from diagram_bot.collector import collect
    session, _, _ = emulated_account
    snapshot = json.loads(json.dumps(collect(REGION, session=session, expected_account=ACCOUNT), default=str))
    result = template(snapshot)
    meta = assert_self_contained(result, snapshot["raw"])
    listed = {u["Type"] for u in meta["Unsupported"]}
    assert {"VPC", "LambdaFunction"} <= listed  # the default VPC and the function's code package
    counts = kinds(result)
    assert counts["AWS::EC2::VPC"] == 2 and counts["AWS::EC2::Subnet"] == 6 and counts["AWS::EC2::VPCPeeringConnection"] == 1
    assert counts["AWS::EC2::TransitGatewayVpcAttachment"] == 1 and counts["AWS::EC2::VPNConnection"] == 1
    db = next(r for r in result["Resources"].values() if r["Type"] == "AWS::RDS::DBInstance")["Properties"]
    assert db["MasterUsername"] == "admin" and db["AllocatedStorage"] == "20"  # collected values win over parameters


def test_cli_exports_files_and_stdout(monkeypatch, tmp_path, capsys):
    single, combined = tmp_path / "three-tier.json", tmp_path / "multi.json"
    single.write_text(json.dumps(SAMPLES["three-tier"]()), encoding="utf-8")
    combined.write_text(json.dumps(SAMPLES["multi-account"]()), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["diagram-bot", "export-cfn", str(single)])
    assert cli.main() == 0
    out, err = capsys.readouterr()
    assert json.loads(out)["AWSTemplateFormatVersion"] == "2010-09-09" and "미지원 2개" in err
    monkeypatch.setattr("sys.argv", ["diagram-bot", "export-cfn", str(combined), "--output", str(tmp_path / "out" / "landing.json")])
    assert cli.main() == 0
    assert sorted(p.name for p in (tmp_path / "out").iterdir()) == [f"landing-{a}-ap-northeast-2.json" for a in ("111111111111", "222222222222", "333333333333")]
    monkeypatch.setattr("sys.argv", ["diagram-bot", "export-cfn", str(combined)])
    with pytest.raises(SystemExit):
        cli.main()


def test_cfn_lint_accepts_every_template():
    lint = pytest.importorskip("cfnlint.api", reason="moto[cloudformation] extra (cfn-lint) not installed")
    for key, snapshot in all_snapshots().items():
        for label, result in templates(snapshot):
            config = lint.ManualArgs(regions=[result["Metadata"]["DiagramBot"]["Source"]["region"]])
            # E3628: cfn-lint's per-region instance-type list (trn2 is not offered in every region); the sample chooses the type.
            problems = [f"{m.rule.id} {m.message}" for m in lint.lint(json.dumps(result), config=config) if m.rule.id[0] in "EW" and m.rule.id != "E3628"]
            assert not problems, (key, label, problems[:5])


@pytest.mark.parametrize("key", ["three-tier", "microservices"])
def test_emulated_stack_creates_the_resources(emulated_account, key):
    pytest.importorskip("cfnlint", reason="moto[cloudformation] extra not installed")
    session, _, _ = emulated_account
    result = template(SAMPLES[key]())
    image = session.client("ec2").describe_images()["Images"][0]["ImageId"]
    certificate = session.client("acm").request_certificate(DomainName="example.com")["CertificateArn"]
    values = {name: image if spec["Type"] == "AWS::EC2::Image::Id" else certificate if name.endswith("CertificateArn")
              else "arn:aws:iam::123456789012:role/test" if name.endswith("RoleArn") else "public.ecr.aws/nginx/nginx:latest"
              for name, spec in result.get("Parameters", {}).items() if "Default" not in spec}
    ec2, elbv2, ecs = session.client("ec2"), session.client("elbv2"), session.client("ecs")
    services = lambda: sum(len(ecs.list_services(cluster=c)["serviceArns"]) for c in ecs.list_clusters()["clusterArns"])
    before = {"vpcs": len(ec2.describe_vpcs()["Vpcs"]), "lbs": len(elbv2.describe_load_balancers()["LoadBalancers"]),
              "dbs": len(session.client("rds").describe_db_instances()["DBInstances"]),
              "asgs": len(session.client("autoscaling").describe_auto_scaling_groups()["AutoScalingGroups"]), "services": services()}
    stacks = session.client("cloudformation")
    # moto's SecurityGroupIngress needs a CIDR or group source; a managed prefix list (CloudFront origin-facing, valid in
    # CloudFormation and checked by cfn-lint above) is dropped from the emulated stack only.
    body = {**result, "Resources": {k: r for k, r in result["Resources"].items()
                                    if not (r["Type"] == "AWS::EC2::SecurityGroupIngress" and "SourcePrefixListId" in r["Properties"])}}
    stacks.create_stack(StackName=f"diagram-bot-{key}", TemplateBody=json.dumps(body),
                        Parameters=[{"ParameterKey": k, "ParameterValue": v} for k, v in values.items()])
    assert stacks.describe_stacks(StackName=f"diagram-bot-{key}")["Stacks"][0]["StackStatus"] == "CREATE_COMPLETE"
    counts = kinds(result)
    assert len(ec2.describe_vpcs()["Vpcs"]) - before["vpcs"] == counts["AWS::EC2::VPC"]
    assert len(elbv2.describe_load_balancers()["LoadBalancers"]) - before["lbs"] == counts["AWS::ElasticLoadBalancingV2::LoadBalancer"]
    assert len(session.client("rds").describe_db_instances()["DBInstances"]) - before["dbs"] == counts["AWS::RDS::DBInstance"]
    vpc = stacks.describe_stack_resource(StackName=f"diagram-bot-{key}", LogicalResourceId=next(
        k for k, r in result["Resources"].items() if r["Type"] == "AWS::EC2::VPC"))["StackResourceDetail"]["PhysicalResourceId"]
    subnets = ec2.describe_subnets(Filters=[{"Name": "vpc-id", "Values": [vpc]}])["Subnets"]
    assert len(subnets) == counts["AWS::EC2::Subnet"]
    groups = len(session.client("autoscaling").describe_auto_scaling_groups()["AutoScalingGroups"])
    assert groups - before["asgs"] == counts["AWS::AutoScaling::AutoScalingGroup"]
    assert services() - before["services"] == counts.get("AWS::ECS::Service", 0) == (2 if key == "microservices" else 0)


def test_api_downloads_the_template_per_account(tmp_path):
    from fastapi.testclient import TestClient

    from diagram_bot.server import create_app
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor")) as client:
        response = client.get("/api/snapshots/sample-three-tier/cloudformation")
        assert response.status_code == 200 and response.headers["content-type"].startswith("application/json")
        assert 'filename="diagram-bot-cfn-three-tier-111122223333-ap-northeast-2.json"' in response.headers["content-disposition"]
        assert response.json()["Resources"] == template(SAMPLES["three-tier"]())["Resources"]
        assert client.get("/api/snapshots/sample-multi-account/cloudformation").status_code == 422
        part = client.get("/api/snapshots/sample-multi-account/cloudformation?part=1")
        assert part.status_code == 200 and part.json()["Metadata"]["DiagramBot"]["Source"]["account_id"] == "222222222222"
        assert client.get("/api/snapshots/sample-multi-account/cloudformation?part=3").status_code == 422
        assert client.get("/api/snapshots/missing/cloudformation").status_code == 404



def test_routing_domains_edge_and_dedicated_links_are_drafted_or_listed():
    """TGW route tables become routing domains; CloudFront, WAF rules and Direct Connect are listed, never invented."""
    platform = template(SAMPLES["data-platform"]())
    types = kinds(platform)
    raw = SAMPLES["data-platform"]()["raw"]
    custom = [t for t in raw["transit_gateway_route_tables"] if not t.get("DefaultAssociationRouteTable")]
    assert types["AWS::EC2::TransitGatewayRouteTable"] == len(custom) == 2
    vpc_links = [a for a in raw["transit_gateway_route_table_associations"] if a["ResourceType"] == "vpc"]
    assert types["AWS::EC2::TransitGatewayRouteTableAssociation"] == len(vpc_links)
    # VPN attachments are implicit in CloudFormation: their association is listed, not referenced by a made-up ID.
    skipped = {u["Type"] for u in platform["Metadata"]["DiagramBot"]["Unsupported"]}
    assert "TransitGatewayRouteTableAssociation" in skipped
    assert not any("기본 연결·전파를 씁니다" in a for a in platform["Metadata"]["DiagramBot"]["Assumptions"])
    hybrid = template(SAMPLES["hybrid"]())["Metadata"]["DiagramBot"]["Unsupported"]
    assert {"DirectConnectConnection", "DirectConnectVirtualInterface", "DirectConnectGateway"} <= {u["Type"] for u in hybrid}
    edge = template(SAMPLES["three-tier"]())
    assert {"CloudFrontDistribution", "WafWebAcl"} <= {u["Type"] for u in edge["Metadata"]["DiagramBot"]["Unsupported"]}
    assert not any(r["Type"].startswith("AWS::CloudFront") for r in edge["Resources"].values())
