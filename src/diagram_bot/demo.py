from .collector import now

ARN = "arn:aws:elasticloadbalancing:ap-northeast-2:111122223333:"
ALB = ARN + "loadbalancer/app/demo-alb/123"
NLB = ARN + "loadbalancer/net/demo-internal-nlb/789"
TG_WEB = ARN + "targetgroup/demo-web/456"
TG_API = ARN + "targetgroup/demo-api/567"
TG_IP = ARN + "targetgroup/demo-ip-targets/678"


def tags(name, **extra):
    return [{"Key": "Name", "Value": name}] + [{"Key": k, "Value": v} for k, v in extra.items()]


def demo_snapshot():
    """Synthetic sample covering the documented edge cases. Never presented as a live account."""
    timestamp = now()
    subnets = [("subnet-a", "vpc-demo", "ap-northeast-2a", "10.20.1.0/24", "public-a"),
               ("subnet-c", "vpc-demo", "ap-northeast-2c", "10.20.3.0/24", "public-c"),
               ("subnet-app-a", "vpc-demo", "ap-northeast-2a", "10.20.11.0/24", "앱 프라이빗 서브넷 A"),
               ("subnet-app-c", "vpc-demo", "ap-northeast-2c", "10.20.13.0/24", "앱 프라이빗 서브넷 C"),
               ("subnet-staging", "vpc-staging", "ap-northeast-2a", "10.30.1.0/24", "staging-a")]
    instances = [
        {"InstanceId": "i-demo-a", "VpcId": "vpc-demo", "SubnetId": "subnet-app-a", "InstanceType": "t3.small",
         "PrivateIpAddress": "10.20.11.10", "State": {"Name": "running"}, "Placement": {"AvailabilityZone": "ap-northeast-2a"},
         "Tags": tags("web-a"), "SecurityGroups": [{"GroupId": "sg-web"}],
         "NetworkInterfaces": [{"NetworkInterfaceId": "eni-demo-a"}], "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-demo-a"}}]},
        {"InstanceId": "i-demo-c", "VpcId": "vpc-demo", "SubnetId": "subnet-app-c", "InstanceType": "t3.small",
         "PrivateIpAddress": "10.20.13.10", "State": {"Name": "running"}, "Placement": {"AvailabilityZone": "ap-northeast-2c"},
         "Tags": tags("web-c"), "SecurityGroups": [{"GroupId": "sg-web"}]},
        {"InstanceId": "i-demo-batch", "VpcId": "vpc-demo", "SubnetId": "subnet-app-a", "InstanceType": "m6i.large",
         "PrivateIpAddress": "10.20.11.25", "State": {"Name": "stopped"}, "Placement": {"AvailabilityZone": "ap-northeast-2a"},
         "Tags": tags("주문 정산 야간 배치 처리 서버 (월말 대사 작업 전용 인스턴스)", Environment="production")},
        {"InstanceId": "i-staging", "VpcId": "vpc-staging", "SubnetId": "subnet-staging", "InstanceType": "t3.micro",
         "State": {"Name": "running"}, "Tags": tags("staging-api", Environment="staging")},
    ]
    load_balancers = [
        {"LoadBalancerArn": ALB, "LoadBalancerName": "demo-alb", "Type": "application", "VpcId": "vpc-demo",
         "Scheme": "internet-facing", "SecurityGroups": ["sg-alb"],
         "AvailabilityZones": [{"ZoneName": "ap-northeast-2a", "SubnetId": "subnet-a"}, {"ZoneName": "ap-northeast-2c", "SubnetId": "subnet-c"}]},
        {"LoadBalancerArn": NLB, "LoadBalancerName": "demo-internal-nlb", "Type": "network", "VpcId": "vpc-demo", "Scheme": "internal",
         "AvailabilityZones": [{"ZoneName": "ap-northeast-2a", "SubnetId": "subnet-app-a"}, {"ZoneName": "ap-northeast-2c", "SubnetId": "subnet-app-c"}]},
    ]
    target_groups = [
        {"TargetGroupArn": TG_WEB, "TargetGroupName": "demo-web", "VpcId": "vpc-demo", "TargetType": "instance", "Port": 443, "Protocol": "HTTPS", "LoadBalancerArns": [ALB]},
        {"TargetGroupArn": TG_API, "TargetGroupName": "demo-api", "VpcId": "vpc-demo", "TargetType": "instance", "Port": 8080, "Protocol": "HTTP", "LoadBalancerArns": [ALB]},
        {"TargetGroupArn": TG_IP, "TargetGroupName": "demo-ip-targets", "VpcId": "vpc-demo", "TargetType": "ip", "Port": 9000, "Protocol": "TCP", "LoadBalancerArns": [NLB]},
    ]
    target_health = [
        {"TargetGroupArn": TG_WEB, "Descriptions": [
            {"Target": {"Id": "i-demo-a", "Port": 443}, "TargetHealth": {"State": "healthy"}},
            {"Target": {"Id": "i-demo-a", "Port": 8443}, "TargetHealth": {"State": "healthy"}},
            {"Target": {"Id": "i-demo-c", "Port": 443}, "TargetHealth": {"State": "unhealthy", "Reason": "Target.Timeout"}}]},
        {"TargetGroupArn": TG_API, "Descriptions": [
            {"Target": {"Id": "i-demo-c", "Port": 8080}, "TargetHealth": {"State": "healthy"}},
            # Not returned by the partially denied DescribeInstances call: stays a reference.
            {"Target": {"Id": "i-demo-missing", "Port": 8080}, "TargetHealth": {"State": "unused", "Reason": "Target.InvalidState"}}]},
        {"TargetGroupArn": TG_IP, "Descriptions": [
            # Same IP as i-demo-batch; it must not be merged into that instance.
            {"Target": {"Id": "10.20.11.25", "Port": 9000, "AvailabilityZone": "ap-northeast-2a"}, "TargetHealth": {"State": "healthy"}}]},
    ]
    vpcs = [{"VpcId": "vpc-demo", "CidrBlock": "10.20.0.0/16", "Tags": tags("주문 서비스 운영 네트워크")},
            {"VpcId": "vpc-staging", "CidrBlock": "10.30.0.0/16", "Tags": tags("Staging Network")},
            {"VpcId": "vpc-empty", "CidrBlock": "10.40.0.0/16", "Tags": tags("빈 검증 환경")}]
    coverage = [
        {"service": "ec2", "operation": "describe_vpcs", "status": "complete", "items": len(vpcs), "pages": 1},
        {"service": "ec2", "operation": "describe_subnets", "status": "complete", "items": len(subnets), "pages": 1},
        {"service": "ec2", "operation": "describe_instances", "status": "partial", "items": len(instances), "pages": 1,
         "error_code": "UnauthorizedOperation", "permission": "ec2:DescribeInstances"},
        {"service": "elbv2", "operation": "describe_load_balancers", "status": "complete", "items": len(load_balancers), "pages": 1},
        {"service": "elbv2", "operation": "describe_target_groups", "status": "complete", "items": len(target_groups), "pages": 1}]
    coverage += [{"service": "elbv2", "operation": "describe_target_health", "parameters": {"TargetGroupArn": h["TargetGroupArn"]},
                  "status": "complete", "items": len(h["Descriptions"]), "pages": 1} for h in target_health]
    return {"schema_version": 1, "metadata": {
        "account_id": "111122223333", "region": "ap-northeast-2", "partition": "aws",
        "started_at": timestamp, "finished_at": timestamp, "status": "partial", "demo": True,
        "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget"],
        "limitations": ["실제 AWS 조회가 아닌 샘플 데이터입니다.",
                        "보안 그룹·ENI·EBS는 참조만 표시합니다.",
                        "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."]},
        "coverage": coverage,
        "raw": {"vpcs": vpcs,
                "subnets": [{"SubnetId": s, "VpcId": v, "AvailabilityZone": az, "CidrBlock": cidr, "Tags": tags(name)}
                            for s, v, az, cidr, name in subnets],
                "instances": instances, "load_balancers": load_balancers,
                "target_groups": target_groups, "target_health": target_health}}
