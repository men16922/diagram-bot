from .collector import now


def demo_snapshot():
    timestamp = now()
    lb = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:loadbalancer/app/demo-alb/123"
    tg = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:targetgroup/demo-web/456"
    return {"schema_version": 1, "metadata": {
        "account_id": "111122223333", "region": "ap-northeast-2", "partition": "aws",
        "started_at": timestamp, "finished_at": timestamp, "status": "partial", "demo": True,
        "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget"],
        "limitations": ["실제 AWS 조회가 아닌 샘플 데이터입니다.",
                        "보안 그룹·ENI·EBS는 참조만 표시합니다.",
                        "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."]},
        "coverage": [
            {"service": "ec2", "operation": "describe_vpcs", "status": "complete", "items": 1, "pages": 1},
            {"service": "ec2", "operation": "describe_subnets", "status": "complete", "items": 2, "pages": 1},
            {"service": "ec2", "operation": "describe_instances", "status": "partial", "items": 2, "pages": 1, "error_code": "UnauthorizedOperation"},
            {"service": "elbv2", "operation": "describe_load_balancers", "status": "complete", "items": 1, "pages": 1},
            {"service": "elbv2", "operation": "describe_target_groups", "status": "complete", "items": 1, "pages": 1},
            {"service": "elbv2", "operation": "describe_target_health", "parameters": {"TargetGroupArn": tg}, "status": "complete", "items": 2, "pages": 1}],
        "raw": {
            "vpcs": [{"VpcId": "vpc-demo", "CidrBlock": "10.20.0.0/16", "Tags": [{"Key": "Name", "Value": "Diagram Bot Demo Network"}]}],
            "subnets": [{"SubnetId": "subnet-a", "VpcId": "vpc-demo", "AvailabilityZone": "ap-northeast-2a", "CidrBlock": "10.20.1.0/24"},
                        {"SubnetId": "subnet-c", "VpcId": "vpc-demo", "AvailabilityZone": "ap-northeast-2c", "CidrBlock": "10.20.3.0/24"}],
            "instances": [{"InstanceId": "i-demo-a", "VpcId": "vpc-demo", "SubnetId": "subnet-a", "InstanceType": "t3.small", "State": {"Name": "running"}, "Placement": {"AvailabilityZone": "ap-northeast-2a"}, "Tags": [{"Key": "Name", "Value": "web-a"}], "SecurityGroups": [{"GroupId": "sg-web"}], "NetworkInterfaces": [{"NetworkInterfaceId": "eni-demo-a"}], "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-demo-a"}}]},
                          {"InstanceId": "i-demo-c", "VpcId": "vpc-demo", "SubnetId": "subnet-c", "InstanceType": "t3.small", "State": {"Name": "running"}, "Tags": [{"Key": "Name", "Value": "web-c"}]}],
            "load_balancers": [{"LoadBalancerArn": lb, "LoadBalancerName": "demo-alb", "Type": "application", "VpcId": "vpc-demo", "Scheme": "internet-facing", "AvailabilityZones": [{"ZoneName": "ap-northeast-2a", "SubnetId": "subnet-a"}, {"ZoneName": "ap-northeast-2c", "SubnetId": "subnet-c"}], "SecurityGroups": ["sg-alb"]}],
            "target_groups": [{"TargetGroupArn": tg, "TargetGroupName": "demo-web", "VpcId": "vpc-demo", "TargetType": "instance", "Port": 443, "Protocol": "HTTPS", "LoadBalancerArns": [lb]}],
            "target_health": [{"TargetGroupArn": tg, "Descriptions": [
                {"Target": {"Id": "i-demo-a", "Port": 443}, "TargetHealth": {"State": "healthy"}},
                {"Target": {"Id": "i-demo-c", "Port": 443}, "TargetHealth": {"State": "unhealthy", "Reason": "Target.Timeout"}}]}]}}
