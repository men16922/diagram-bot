from contextlib import ExitStack

import boto3
import pytest
from botocore.stub import Stubber

from diagram_bot.collector import collect, query


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
                                                                  "opensearch", "ecs", "efs", "redshift")}

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
        result = collect("ap-northeast-2", expected_account="111122223333", session=session)
        assert result["metadata"]["status"] == "partial"
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


def test_target_health_error_is_scoped_to_group():
    elb = client("elbv2")
    arn = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/web/123"
    with Stubber(elb) as stub:
        stub.add_client_error("describe_target_health", "AccessDenied", expected_params={"TargetGroupArn": arn})
        rows, coverage = query(elb, "describe_target_health", "TargetHealthDescriptions", {"TargetGroupArn": arn})
        assert rows == [] and coverage["status"] == "unavailable"
        assert coverage["parameters"]["TargetGroupArn"] == arn
