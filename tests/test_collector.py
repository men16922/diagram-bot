import json
from contextlib import ExitStack

import boto3
import pytest
from botocore.stub import Stubber

from diagram_bot.collector import DETAILS, OPERATIONS, collect, opensearch, query, transit_routes


def client(service):
    return boto3.client(service, region_name="ap-northeast-2",
                        aws_access_key_id="testing", aws_secret_access_key="testing")


def test_pagination_keeps_all_pages():
    ec2 = client("ec2")
    with Stubber(ec2) as stub:
        stub.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-a"}], "NextToken": "next"}, {})
        stub.add_response("describe_vpcs", {"Vpcs": [{"VpcId": "vpc-b"}]}, {"NextToken": "next"})
        rows, coverage = query(ec2, "describe_vpcs", "Vpcs")
        assert [v["VpcId"] for v in rows] == ["vpc-a", "vpc-b"]
        assert coverage["status"] == "complete" and coverage["pages"] == 2
        stub.assert_no_pending_responses()


def test_second_page_access_denied_preserves_first_page():
    ec2 = client("ec2")
    with Stubber(ec2) as stub:
        stub.add_response("describe_instances", {"Reservations": [{"Instances": [{"InstanceId": "i-a"}]}], "NextToken": "next"}, {})
        stub.add_client_error("describe_instances", "UnauthorizedOperation", expected_params={"NextToken": "next"})
        rows, coverage = query(ec2, "describe_instances", "Reservations")
        assert len(rows) == 1
        assert coverage["status"] == "partial"
        assert coverage["error_code"] == "UnauthorizedOperation"


def test_denied_is_distinct_from_empty_success():
    ec2 = client("ec2")
    with Stubber(ec2) as stub:
        stub.add_client_error("describe_subnets", "UnauthorizedOperation", expected_params={})
        stub.add_response("describe_subnets", {"Subnets": []}, {})
        assert query(ec2, "describe_subnets", "Subnets")[1]["status"] == "unavailable"
        assert query(ec2, "describe_subnets", "Subnets")[1]["status"] == "complete"


def test_elbv2_marker_pagination():
    elb = client("elbv2")
    with Stubber(elb) as stub:
        stub.add_response("describe_load_balancers", {"LoadBalancers": [{"LoadBalancerName": "a"}], "NextMarker": "next"}, {})
        stub.add_response("describe_load_balancers", {"LoadBalancers": [{"LoadBalancerName": "b"}]}, {"Marker": "next"})
        rows, coverage = query(elb, "describe_load_balancers", "LoadBalancers")
        assert len(rows) == 2 and coverage["pages"] == 2


class Session:
    def __init__(self):
        self.clients = {name: client(name) for name in ("sts", "ec2", "elbv2", "rds", "autoscaling", "elasticache", "kafka", "lambda", "eks",
                                                                  "opensearch", "ecs", "efs", "redshift", "fsx", "sagemaker", "cloudfront", "wafv2",
                                                                  "directconnect")}

    def client(self, name, **kwargs):
        return self.clients[name]


def stub_identity(stub):
    stub.add_response("get_caller_identity", {"Account": "111122223333", "Arn": "arn:aws:iam::111122223333:user/test", "UserId": "test"}, {})


def test_account_mismatch_stops_before_resource_calls():
    session = Session()
    with ExitStack() as stack:
        stubs = {name: stack.enter_context(Stubber(c)) for name, c in session.clients.items()}
        stub_identity(stubs["sts"])
        with pytest.raises(ValueError, match="계정 불일치"):
            collect("ap-northeast-2", expected_account="999999999999", session=session)


def test_full_collection_flags_undiscovered_targets_and_flattens_instances():
    session = Session()
    with ExitStack() as stack:
        stubs = {name: stack.enter_context(Stubber(c)) for name, c in session.clients.items()}
        stub_identity(stubs["sts"])
        stubs["ec2"].add_response("describe_vpcs", {"Vpcs": []}, {})
        stubs["ec2"].add_response("describe_subnets", {"Subnets": []}, {})
        stubs["ec2"].add_response("describe_instances", {"Reservations": [{"Instances": [{"InstanceId": "i-a"}, {"InstanceId": "i-b"}]}]}, {})
        stubs["elbv2"].add_response("describe_load_balancers", {"LoadBalancers": []}, {})
        stubs["elbv2"].add_client_error("describe_target_groups", "AccessDenied", expected_params={})
        stubs["ec2"].add_response("describe_internet_gateways", {"InternetGateways": [{"InternetGatewayId": "igw-a"}]}, {})
        stubs["ec2"].add_response("describe_nat_gateways", {"NatGateways": []}, {})
        stubs["ec2"].add_response("describe_route_tables", {"RouteTables": []}, {})
        stubs["rds"].add_client_error("describe_db_instances", "AccessDenied", expected_params={})
        stubs["autoscaling"].add_response("describe_auto_scaling_groups", {"AutoScalingGroups": []}, {})
        stubs["ec2"].add_response("describe_vpc_endpoints", {"VpcEndpoints": []}, {})
        stubs["ec2"].add_response("describe_transit_gateways", {"TransitGateways": []}, {})
        stubs["ec2"].add_response("describe_transit_gateway_vpc_attachments", {"TransitGatewayVpcAttachments": []}, {})
        stubs["rds"].add_response("describe_db_clusters", {"DBClusters": []}, {})
        stubs["elasticache"].add_response("describe_cache_clusters", {"CacheClusters": []}, {})
        stubs["elasticache"].add_response("describe_replication_groups", {"ReplicationGroups": []}, {})
        stubs["elasticache"].add_response("describe_cache_subnet_groups", {"CacheSubnetGroups": []}, {})
        stubs["kafka"].add_response("list_clusters_v2", {"ClusterInfoList": []}, {})
        stubs["lambda"].add_response("list_functions", {"Functions": []}, {})
        stubs["eks"].add_response("list_clusters", {"clusters": ["platform"]}, {})
        stubs["opensearch"].add_response("list_domain_names", {"DomainNames": [{"DomainName": "logs"}]}, {})
        stubs["ecs"].add_response("list_clusters", {"clusterArns": ["arn:aws:ecs:ap-northeast-2:111122223333:cluster/apps"]}, {})
        stubs["efs"].add_response("describe_file_systems", {"FileSystems": [{"FileSystemId": "fs-a", "OwnerId": "111122223333", "CreationToken": "t",
                                                                               "CreationTime": "2026-01-01T00:00:00Z", "LifeCycleState": "available",
                                                                               "NumberOfMountTargets": 1, "SizeInBytes": {"Value": 1},
                                                                               "PerformanceMode": "generalPurpose", "Tags": []}]}, {})
        stubs["redshift"].add_response("describe_clusters", {"Clusters": []}, {})
        stubs["redshift"].add_response("describe_cluster_subnet_groups", {"ClusterSubnetGroups": []}, {})
        stubs["ec2"].add_response("describe_vpc_peering_connections", {"VpcPeeringConnections": []}, {})
        stubs["ec2"].add_client_error("describe_vpn_gateways", "UnauthorizedOperation", expected_params={})
        stubs["ec2"].add_response("describe_vpn_connections", {"VpnConnections": []}, {})
        stubs["ec2"].add_response("describe_customer_gateways", {"CustomerGateways": []}, {})
        stubs["ec2"].add_response("describe_security_groups", {"SecurityGroups": [{"GroupId": "sg-a", "GroupName": "web"}]}, {})
        stubs["ec2"].add_response("describe_placement_groups", {"PlacementGroups": [{"GroupName": "train-pg", "Strategy": "cluster"}]}, {})
        stubs["fsx"].add_response("describe_file_systems", {"FileSystems": [{"FileSystemId": "fs-0123456789abcdef0", "FileSystemType": "LUSTRE",
                                                                               "SubnetIds": ["subnet-0000000a"], "VpcId": "vpc-0000000a"}]}, {})
        stubs["sagemaker"].add_response("list_endpoints", {"Endpoints": [{"EndpointName": "llm", "EndpointArn": "arn:aws:sagemaker:ap-northeast-2:111122223333:endpoint/llm",
                                                                         "CreationTime": "2026-01-01T00:00:00Z", "LastModifiedTime": "2026-01-01T00:00:00Z",
                                                                         "EndpointStatus": "InService"}]}, {})
        stubs["sagemaker"].add_response("list_clusters", {"ClusterSummaries": [{"ClusterName": "hp", "ClusterArn": "arn:aws:sagemaker:ap-northeast-2:111122223333:cluster/hp",
                                                                               "CreationTime": "2026-01-01T00:00:00Z", "ClusterStatus": "InService"}]}, {})
        stubs["ec2"].add_response("describe_addresses", {"Addresses": []}, {})
        stubs["ec2"].add_response("describe_transit_gateway_route_tables", {"TransitGatewayRouteTables": []}, {})
        stubs["cloudfront"].add_response("list_distributions", EMPTY[("cloudfront", "list_distributions")], {})
        stubs["directconnect"].add_response("describe_connections", {"connections": []}, {})
        stubs["directconnect"].add_response("describe_virtual_interfaces", {"virtualInterfaces": []}, {})
        stubs["directconnect"].add_response("describe_direct_connect_gateways", {"directConnectGateways": []}, {})
        stubs["wafv2"].add_response("list_web_acls", {"WebACLs": []}, {"Scope": "REGIONAL", "Limit": 100})
        stubs["wafv2"].add_response("list_web_acls", {"WebACLs": []}, {"Scope": "CLOUDFRONT", "Limit": 100})
        stubs["eks"].add_response("describe_cluster", {"cluster": {"name": "platform", "resourcesVpcConfig": {"vpcId": "vpc-a", "subnetIds": ["subnet-a"]}}}, {"name": "platform"})
        stubs["eks"].add_response("list_nodegroups", {"nodegroups": ["general"]}, {"clusterName": "platform"})
        stubs["eks"].add_response("describe_nodegroup", {"nodegroup": {"nodegroupName": "general", "resources": {"autoScalingGroups": [{"name": "eks-general"}]}}},
                                  {"clusterName": "platform", "nodegroupName": "general"})
        stubs["opensearch"].add_response("describe_domains", {"DomainStatusList": [{"DomainId": "1/logs", "DomainName": "logs", "ARN": "arn:aws:es:ap-northeast-2:111122223333:domain/logs",
                                                                                     "ClusterConfig": {}, "VPCOptions": {"SubnetIds": ["subnet-a"]}}]},
                                         {"DomainNames": ["logs"]})
        stubs["opensearch"].add_response("describe_domain_nodes", {"DomainNodesStatusList": [{"NodeId": "node-00000001", "NodeType": "Data",
                                                                                              "AvailabilityZone": "ap-northeast-2a"}]}, {"DomainName": "logs"})
        cluster = "arn:aws:ecs:ap-northeast-2:111122223333:cluster/apps"
        stubs["ecs"].add_response("list_services", {"serviceArns": ["svc-a"]}, {"cluster": cluster})
        stubs["ecs"].add_response("describe_services", {"services": [{"serviceName": "web"}]}, {"cluster": cluster, "services": ["svc-a"]})
        stubs["ecs"].add_response("list_tasks", {"taskArns": ["task-a"]}, {"cluster": cluster, "desiredStatus": "RUNNING"})
        stubs["ecs"].add_client_error("describe_tasks", "AccessDeniedException", expected_params={"cluster": cluster, "tasks": ["task-a"]})
        stubs["efs"].add_response("describe_mount_targets", {"MountTargets": [{"MountTargetId": "fsmt-0000000a", "FileSystemId": "fs-a", "SubnetId": "subnet-0000000a",
                                                                               "LifeCycleState": "available"}]}, {"FileSystemId": "fs-a"})
        stubs["sagemaker"].add_response("describe_endpoint", {"EndpointName": "llm", "EndpointArn": "arn:aws:sagemaker:ap-northeast-2:111122223333:endpoint/llm",
                                                               "EndpointConfigName": "llm-config", "EndpointStatus": "InService",
                                                               "CreationTime": "2026-01-01T00:00:00Z", "LastModifiedTime": "2026-01-01T00:00:00Z"},
                                        {"EndpointName": "llm"})
        stubs["sagemaker"].add_response("describe_endpoint_config", {"EndpointConfigName": "llm-config", "CreationTime": "2026-01-01T00:00:00Z",
                                                                     "EndpointConfigArn": "arn:aws:sagemaker:ap-northeast-2:111122223333:endpoint-config/llm-config",
                                                                     "ProductionVariants": [{"VariantName": "main", "ModelName": "llm-model",
                                                                                             "InstanceType": "ml.g5.2xlarge", "InitialInstanceCount": 2}]},
                                        {"EndpointConfigName": "llm-config"})
        stubs["sagemaker"].add_response("describe_model", {"ModelName": "llm-model", "CreationTime": "2026-01-01T00:00:00Z",
                                                           "ModelArn": "arn:aws:sagemaker:ap-northeast-2:111122223333:model/llm-model",
                                                           "VpcConfig": {"Subnets": ["subnet-a"], "SecurityGroupIds": ["sg-a"]}}, {"ModelName": "llm-model"})
        stubs["sagemaker"].add_client_error("describe_cluster", "AccessDeniedException", expected_params={"ClusterName": "hp"})
        result = collect("ap-northeast-2", expected_account="111122223333", session=session)
        assert result["metadata"]["status"] == "partial"
        # AI/ML: placement groups and FSx from list calls; a SageMaker endpoint joined with its config and models.
        assert result["raw"]["placement_groups"][0]["Strategy"] == "cluster" and result["raw"]["fsx_file_systems"][0]["FileSystemType"] == "LUSTRE"
        endpoint = result["raw"]["sagemaker_endpoints"][0]
        assert endpoint["EndpointConfig"]["ProductionVariants"][0]["InstanceType"] == "ml.g5.2xlarge"
        assert endpoint["Models"][0]["VpcConfig"]["Subnets"] == ["subnet-a"] and "ResponseMetadata" not in endpoint
        hyperpod = next(c for c in result["coverage"] if c["operation"] == "describe_cluster" and c["service"] == "sagemaker")
        assert hyperpod["status"] == "unavailable" and hyperpod["permission"] == "sagemaker:DescribeCluster"
        assert result["raw"]["sagemaker_clusters"] == [] and "sagemaker_endpoint_names" not in result["raw"]
        assert result["raw"]["instances"] == [{"InstanceId": "i-a"}, {"InstanceId": "i-b"}]
        assert next(c for c in result["coverage"] if c["operation"] == "describe_instances")["items"] == 2
        assert result["coverage"][-1]["status"] == "not_discovered"
        assert result["raw"]["internet_gateways"] == [{"InternetGatewayId": "igw-a"}]
        rds = next(c for c in result["coverage"] if c["operation"] == "describe_db_instances")
        assert rds["status"] == "unavailable" and rds["permission"] == "rds:DescribeDBInstances"
        eks = result["raw"]["eks_clusters"][0]
        assert eks["resourcesVpcConfig"]["vpcId"] == "vpc-a" and eks["nodegroups"][0]["nodegroupName"] == "general"
        assert "eks_cluster_names" not in result["raw"]
        assert result["raw"]["opensearch_nodes"][0]["DomainName"] == "logs"
        assert result["raw"]["ecs_services"] == [{"serviceName": "web"}] and result["raw"]["ecs_tasks"] == []
        tasks = next(c for c in result["coverage"] if c["operation"] == "describe_tasks")
        assert tasks["status"] == "unavailable" and tasks["permission"] == "ecs:DescribeTasks"
        vpn = next(c for c in result["coverage"] if c["operation"] == "describe_vpn_gateways")
        assert vpn["permission"] == "ec2:DescribeVpnGateways"
        assert next(c for c in result["coverage"] if c["operation"] == "describe_mount_targets")["permission"] == "elasticfilesystem:DescribeMountTargets"
        assert next(c for c in result["coverage"] if c["operation"] == "describe_domains")["permission"] == "es:DescribeDomains"
        assert result["raw"]["efs_mount_targets"][0]["SubnetId"] == "subnet-0000000a"
        for stub in stubs.values():
            stub.assert_no_pending_responses()


EMPTY = {("cloudfront", "list_distributions"): {"DistributionList": {"Marker": "", "MaxItems": 100, "IsTruncated": False, "Quantity": 0}}}


def stub_listing(stubs, overrides):
    """Every first-phase call answers empty unless overridden with a response, or an error code (denied)."""
    for service, operation, key, _ in OPERATIONS:
        value = overrides.get((service, operation), EMPTY.get((service, operation), {key: []}))
        if isinstance(value, str):
            stubs[service].add_client_error(operation, value, expected_params={})
        else:
            stubs[service].add_response(operation, value, {})


def test_edge_routing_and_direct_connect_calls_keep_partial_and_denied_results():
    """Elastic IPs, TGW route tables (associations, propagations, routes), CloudFront, WAF (both scopes, marker pages)
    and Direct Connect: each call's rows, and denied or truncated calls marked with the IAM action to grant."""
    import json
    from pathlib import Path
    session = Session()
    acl = lambda name, scope: {"Name": name, "Id": name + "-id", "ARN": f"arn:aws:wafv2:us-east-1:111122223333:{scope}/webacl/{name}/{name}-id",
                               "LockToken": "t"}
    route = lambda cidr, state, attachments: {"DestinationCidrBlock": cidr, "Type": "static" if not attachments else "propagated", "State": state,
                                              "TransitGatewayAttachments": [{"TransitGatewayAttachmentId": a, "ResourceId": "vpc-a", "ResourceType": "vpc"}
                                                                            for a in attachments]}
    with ExitStack() as stack:
        stubs = {name: stack.enter_context(Stubber(c)) for name, c in session.clients.items()}
        stub_identity(stubs["sts"])
        stub_listing(stubs, {
            ("ec2", "describe_addresses"): {"Addresses": [{"AllocationId": "eipalloc-nat", "PublicIp": "203.0.113.5", "AssociationId": "eipassoc-1"},
                                                          {"AllocationId": "eipalloc-idle", "PublicIp": "203.0.113.6"}]},
            ("ec2", "describe_transit_gateway_route_tables"): {"TransitGatewayRouteTables": [
                {"TransitGatewayRouteTableId": "tgw-rtb-a", "TransitGatewayId": "tgw-a"}, {"TransitGatewayRouteTableId": "tgw-rtb-b", "TransitGatewayId": "tgw-a"}]},
            ("cloudfront", "list_distributions"): {"DistributionList": {
                "Marker": "", "MaxItems": 100, "IsTruncated": False, "Quantity": 1,
                "Items": [{"Id": "E1", "ARN": "arn:aws:cloudfront::111122223333:distribution/E1", "Status": "Deployed",
                           "LastModifiedTime": "2026-01-01T00:00:00Z", "DomainName": "d1.cloudfront.net", "Aliases": {"Quantity": 0},
                           "Origins": {"Quantity": 1, "Items": [{"Id": "o", "DomainName": "origin.example.com"}]},
                           "DefaultCacheBehavior": {"TargetOriginId": "o", "ViewerProtocolPolicy": "https-only"},
                           "CacheBehaviors": {"Quantity": 0}, "CustomErrorResponses": {"Quantity": 0}, "Comment": "", "PriceClass": "PriceClass_All",
                           "Enabled": True, "ViewerCertificate": {}, "Restrictions": {"GeoRestriction": {"RestrictionType": "none", "Quantity": 0}},
                           "WebACLId": "", "HttpVersion": "http2", "IsIPV6Enabled": True, "Staging": False}]}},
            ("directconnect", "describe_connections"): {"connections": [{"connectionId": "dxcon-a", "connectionName": "dc-1", "connectionState": "available"}]},
            ("directconnect", "describe_virtual_interfaces"): "DirectConnectClientException",
            ("directconnect", "describe_direct_connect_gateways"): {"directConnectGateways": [{"directConnectGatewayId": "dxgw-a", "directConnectGatewayName": "core"}]},
        })
        ec2, waf = stubs["ec2"], stubs["wafv2"]
        ec2.add_response("get_transit_gateway_route_table_associations", {"Associations": [
            {"TransitGatewayAttachmentId": "tgw-attach-a", "ResourceId": "vpc-a", "ResourceType": "vpc", "State": "associated"}]},
            {"TransitGatewayRouteTableId": "tgw-rtb-a"})
        ec2.add_response("get_transit_gateway_route_table_propagations", {"TransitGatewayRouteTablePropagations": [
            {"TransitGatewayAttachmentId": "tgw-attach-a", "ResourceId": "vpc-a", "ResourceType": "vpc", "State": "enabled"}]},
            {"TransitGatewayRouteTableId": "tgw-rtb-a"})
        search = lambda table: {"TransitGatewayRouteTableId": table, "Filters": [{"Name": "state", "Values": ["active", "blackhole"]}]}
        ec2.add_response("search_transit_gateway_routes", {"Routes": [route("10.0.0.0/16", "active", ["tgw-attach-a"]), route("10.9.0.0/16", "blackhole", [])],
                                                           "AdditionalRoutesAvailable": True}, search("tgw-rtb-a"))
        ec2.add_client_error("get_transit_gateway_route_table_associations", "UnauthorizedOperation", expected_params={"TransitGatewayRouteTableId": "tgw-rtb-b"})
        ec2.add_response("get_transit_gateway_route_table_propagations", {"TransitGatewayRouteTablePropagations": []}, {"TransitGatewayRouteTableId": "tgw-rtb-b"})
        ec2.add_response("search_transit_gateway_routes", {"Routes": []}, search("tgw-rtb-b"))
        # Regional web ACLs over two marker pages; the second ACL's resources are denied.
        waf.add_response("list_web_acls", {"WebACLs": [acl("app", "regional")], "NextMarker": "page-2"}, {"Scope": "REGIONAL", "Limit": 100})
        waf.add_response("list_web_acls", {"WebACLs": [acl("api", "regional")]}, {"Scope": "REGIONAL", "Limit": 100, "NextMarker": "page-2"})
        alb = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:loadbalancer/app/web/1"
        waf.add_response("list_resources_for_web_acl", {"ResourceArns": [alb]},
                         {"WebACLArn": acl("app", "regional")["ARN"], "ResourceType": "APPLICATION_LOAD_BALANCER"})
        waf.add_client_error("list_resources_for_web_acl", "WAFNonexistentItemException",
                             expected_params={"WebACLArn": acl("api", "regional")["ARN"], "ResourceType": "APPLICATION_LOAD_BALANCER"})
        waf.add_response("list_web_acls", {"WebACLs": [acl("edge", "global")]}, {"Scope": "CLOUDFRONT", "Limit": 100})
        stubs["directconnect"].add_response("describe_direct_connect_gateway_associations", {"directConnectGatewayAssociations": [
            {"directConnectGatewayId": "dxgw-a", "associationState": "associated",
             "associatedGateway": {"id": "tgw-a", "type": "transitGateway", "ownerAccount": "111122223333", "region": "ap-northeast-2"}}]},
            {"directConnectGatewayId": "dxgw-a"})
        result = collect("ap-northeast-2", session=session)
        for stub in stubs.values():
            stub.assert_no_pending_responses()
    raw, coverage = result["raw"], result["coverage"]
    assert [a["AllocationId"] for a in raw["elastic_ips"]] == ["eipalloc-nat", "eipalloc-idle"]
    assert raw["transit_gateway_route_table_associations"] == [{"TransitGatewayAttachmentId": "tgw-attach-a", "ResourceId": "vpc-a", "ResourceType": "vpc",
                                                                "State": "associated", "TransitGatewayRouteTableId": "tgw-rtb-a"}]
    assert [(r["DestinationCidrBlock"], r["State"], r["TransitGatewayRouteTableId"]) for r in raw["transit_gateway_routes"]] == [
        ("10.0.0.0/16", "active", "tgw-rtb-a"), ("10.9.0.0/16", "blackhole", "tgw-rtb-a")]
    assert raw["cloudfront_distributions"][0]["Id"] == "E1"
    assert [(a["Name"], a["Scope"], a.get("ResourceArns")) for a in raw["waf_web_acls"]] == [
        ("app", "REGIONAL", [alb]), ("api", "REGIONAL", []), ("edge", "CLOUDFRONT", None)]
    assert raw["dx_gateway_associations"][0]["associatedGateway"]["id"] == "tgw-a" and raw["dx_virtual_interfaces"] == []
    rows = lambda operation: [c for c in coverage if c["operation"] == operation]
    truncated = rows("search_transit_gateway_routes")[0]
    assert truncated["status"] == "partial" and truncated["error_code"] == "AdditionalRoutesAvailable" and truncated["items"] == 2
    assert truncated["permission"] == "ec2:SearchTransitGatewayRoutes"
    denied = rows("get_transit_gateway_route_table_associations")[1]
    assert denied["status"] == "unavailable" and denied["permission"] == "ec2:GetTransitGatewayRouteTableAssociations"
    vifs = rows("describe_virtual_interfaces")[0]
    assert vifs["status"] == "unavailable" and vifs["permission"] == "directconnect:DescribeVirtualInterfaces"
    assert rows("list_web_acls")[0]["pages"] == 2 and rows("list_web_acls")[0]["permission"] == "wafv2:ListWebACLs"
    assert rows("list_resources_for_web_acl")[1]["permission"] == "wafv2:ListResourcesForWebACL"
    assert result["metadata"]["status"] == "partial"
    # Every call the collector makes is granted by the read-only policy.
    policy = set(json.loads((Path(__file__).resolve().parents[1] / "readonly-policy.json").read_text())["Statement"][0]["Action"])
    assert {c["permission"] for c in coverage if c.get("permission")} <= policy


def test_target_health_error_is_scoped_to_group():
    elb = client("elbv2")
    arn = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/web/123"
    with Stubber(elb) as stub:
        stub.add_client_error("describe_target_health", "AccessDenied", expected_params={"TargetGroupArn": arn})
        rows, coverage = query(elb, "describe_target_health", "TargetHealthDescriptions", {"TargetGroupArn": arn})
        assert rows == [] and coverage["status"] == "unavailable"
        assert coverage["parameters"]["TargetGroupArn"] == arn


def stub_call(answers):
    """A collect() call stand-in: logs each request and answers from {operation: rows}."""
    def call(log, service, operation, key, params=None, client=None):
        log.append({"service": service, "operation": operation, "parameters": params, "status": "complete"})
        return answers.get(operation, [])
    return call


def test_detail_steps_run_on_a_stub_call():
    empty = {name: [] for *_, name in OPERATIONS}
    assert all(value == [] for detail in DETAILS for value in detail(empty, stub_call({}), []).values())
    log = []
    names = [{"DomainName": f"d{i}"} for i in range(7)]
    domains = opensearch({**empty, "opensearch_domain_names": names}, stub_call({"describe_domains": [{"DomainName": "d0", "VPCOptions": {"SubnetIds": ["s"]}}]}), log)
    assert [c["parameters"]["DomainNames"] for c in log[:2]] == [[f"d{i}" for i in range(5)], ["d5", "d6"]]  # five names per call
    assert [c["operation"] for c in log[2:]] == ["describe_domain_nodes"] * 2 and len(domains["opensearch_domains"]) == 2
    log = []
    tables = [{"TransitGatewayRouteTableId": "tgw-rtb-1"}]
    routes = transit_routes({**empty, "transit_gateway_route_tables": tables},
                            stub_call({"search_transit_gateway_routes": [{"Routes": [{"State": "active"}], "AdditionalRoutesAvailable": True}]}), log)
    assert routes["transit_gateway_routes"] == [{"State": "active", "TransitGatewayRouteTableId": "tgw-rtb-1"}]
    assert log[-1]["status"] == "partial" and log[-1]["items"] == 1 and log[-1]["error_code"] == "AdditionalRoutesAvailable"


def test_assume_role_caps_session_to_read_only_and_checks_the_assumed_account(monkeypatch):
    base, assumed = Session(), Session()
    monkeypatch.setattr("diagram_bot.sessions.boto3.Session", lambda **kwargs: assumed)
    role = "arn:aws:iam::444455556666:role/DiagramBotReadOnly"
    with Stubber(base.clients["sts"]) as stub, Stubber(assumed.clients["sts"]) as assumed_stub:
        stub.add_response("assume_role", {"Credentials": {"AccessKeyId": "AKIAEXAMPLE000000000", "SecretAccessKey": "s" * 40,
                                                          "SessionToken": "t" * 40, "Expiration": "2026-01-01T00:00:00Z"}},
                          {"RoleArn": role, "RoleSessionName": "diagram-bot-readonly", "DurationSeconds": 3600, "ExternalId": "ext-123",
                           "PolicyArns": [{"arn": "arn:aws:iam::aws:policy/ReadOnlyAccess"}]})
        assumed_stub.add_response("get_caller_identity", {"Account": "444455556666", "Arn": "arn:aws:sts::444455556666:assumed-role/x/y",
                                                          "UserId": "x"}, {})
        with pytest.raises(ValueError, match="현재 인증 444455556666"):
            collect("ap-northeast-2", expected_account="111122223333", session=base, role_arn=role, external_id="ext-123")
        stub.assert_no_pending_responses()


def test_cli_collects_every_role_and_region(monkeypatch, tmp_path, capsys):
    from diagram_bot import cli
    from diagram_bot.demo import demo_snapshot
    calls = []

    def fake(region, profile, expected, role_arn=None, external_id=None):
        calls.append((region, expected, role_arn))
        if region == "us-east-1" and expected == "444455556666":
            raise ValueError("계정 불일치")
        return demo_snapshot()
    monkeypatch.setattr(cli, "collect", fake)
    monkeypatch.setattr("sys.argv", ["diagram-bot", "collect", "--region", "ap-northeast-2,us-east-1",
                                     "--role-arn", "arn:aws:iam::111122223333:role/R", "--role-arn", "arn:aws:iam::444455556666:role/R",
                                     "--output", str(tmp_path)])
    assert cli.main() == 1  # one target failed; the others still produced reports
    assert [c[:2] for c in calls] == [("ap-northeast-2", "111122223333"), ("us-east-1", "111122223333"),
                                      ("ap-northeast-2", "444455556666"), ("us-east-1", "444455556666")]
    assert sorted(p.name for p in tmp_path.iterdir()) == ["111122223333-ap-northeast-2", "111122223333-us-east-1", "444455556666-ap-northeast-2"]
    monkeypatch.setattr("sys.argv", ["diagram-bot", "collect", "--region", "ap-northeast-2", "--output", str(tmp_path / "x")])
    with pytest.raises(SystemExit):
        cli.main()


def test_deploy_template_grants_exactly_the_collector_actions():
    import json
    from pathlib import Path

    from diagram_bot.collector import OPERATIONS, permission
    root = Path(__file__).resolve().parents[1]
    policy = set(json.loads((root / "readonly-policy.json").read_text())["Statement"][0]["Action"])
    template = json.loads((root / "deploy/readonly-role.cfn.json").read_text())
    role = template["Resources"]["CollectionRole"]["Properties"]
    granted = set(role["Policies"][0]["PolicyDocument"]["Statement"][0]["Action"])
    assert granted == policy | {"sts:GetCallerIdentity"}
    assert {permission(service, operation) for service, operation, *_ in OPERATIONS} <= policy
    assert all(a.split(":")[1].startswith(("Describe", "List", "Get", "Search")) for a in granted)
    assert "sts:ExternalId" in json.dumps(role["AssumeRolePolicyDocument"])


def test_cli_combine_writes_one_report_for_several_accounts(monkeypatch, tmp_path):
    import json

    from diagram_bot import cli
    from diagram_bot.samples import SAMPLES
    paths = []
    for i, part in enumerate(SAMPLES["multi-account"]()["parts"]):
        paths.append(tmp_path / f"part-{i}.json")
        paths[-1].write_text(json.dumps(part), encoding="utf-8")
    monkeypatch.setattr("sys.argv", ["diagram-bot", "combine", *map(str, paths), "--output", str(tmp_path / "out"), "--title", "조직"])
    assert cli.main() == 0
    report = json.loads((tmp_path / "out" / "snapshot.json").read_text(encoding="utf-8"))
    assert report["metadata"]["combined"] and len(report["parts"]) == 3
    assert "AWS 계정 · 111111111111" in (tmp_path / "out" / "topology.drawio").read_text(encoding="utf-8")


def test_every_producer_builds_the_same_envelope_and_files_are_checked_on_load(tmp_path):
    from diagram_bot.combine import combine_snapshots
    from diagram_bot.demo import demo_snapshot
    from diagram_bot.samples import SAMPLES
    from diagram_bot.snapshot import SCHEMA_VERSION, load_snapshot
    sample = SAMPLES["three-tier"]()
    other = {**sample, "metadata": {**sample["metadata"], "account_id": "444455556666"}}
    for snapshot in (demo_snapshot(), sample, combine_snapshots([sample, other])):
        assert list(snapshot)[:4] == ["schema_version", "metadata", "coverage", "raw"] and snapshot["schema_version"] == SCHEMA_VERSION
    path = tmp_path / "snapshot.json"
    path.write_text(json.dumps(sample), encoding="utf-8")
    assert load_snapshot(path) == sample
    for wrong in ({**sample, "schema_version": 2}, [sample]):
        path.write_text(json.dumps(wrong), encoding="utf-8")
        with pytest.raises(ValueError, match="schema_version"):
            load_snapshot(path)
