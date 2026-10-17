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
        self.clients = {name: client(name) for name in ("sts", "ec2", "elbv2", "rds", "autoscaling")}

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
        result = collect("ap-northeast-2", expected_account="111122223333", session=session)
        assert result["metadata"]["status"] == "partial"
        assert result["raw"]["instances"] == [{"InstanceId": "i-a"}, {"InstanceId": "i-b"}]
        assert next(c for c in result["coverage"] if c["operation"] == "describe_instances")["items"] == 2
        assert result["coverage"][-1]["status"] == "not_discovered"
        assert result["raw"]["internet_gateways"] == [{"InternetGatewayId": "igw-a"}]
        rds = next(c for c in result["coverage"] if c["operation"] == "describe_db_instances")
        assert rds["status"] == "unavailable" and rds["permission"] == "rds:DescribeDBInstances"
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
