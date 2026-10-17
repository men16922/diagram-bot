"""Synthetic reference architectures following AWS best-practice patterns. Never live data.

Each sample stays inside the collector's scope (VPC, subnet, EC2, ELBv2, target groups and
registered targets), so it renders exactly like a collected snapshot would.
"""
from .collector import now

ACCOUNT, REGION = "111122223333", "ap-northeast-2"
ARN = f"arn:aws:elasticloadbalancing:{REGION}:{ACCOUNT}:"


class Sample:
    def __init__(self, key, title, practices):
        self.key, self.title, self.practices = key, title, practices
        self.raw = {"vpcs": [], "subnets": [], "instances": [], "load_balancers": [], "target_groups": [], "target_health": [],
                    "internet_gateways": [], "nat_gateways": [], "route_tables": [], "db_instances": [], "auto_scaling_groups": [],
                    "vpc_endpoints": [], "transit_gateways": [], "transit_gateway_attachments": []}
        self.counter = 0

    def tags(self, name, **extra):
        return [{"Key": "Name", "Value": name}] + [{"Key": k, "Value": v} for k, v in extra.items()]

    def vpc(self, key, cidr, name, **tags):
        self.raw["vpcs"].append({"VpcId": key, "CidrBlock": cidr, "Tags": self.tags(name, **tags)})
        return key

    def subnet(self, key, vpc, az, cidr, name, tier):
        self.raw["subnets"].append({"SubnetId": key, "VpcId": vpc, "AvailabilityZone": f"{REGION}{az}", "CidrBlock": cidr,
                                    "MapPublicIpOnLaunch": tier == "public", "Tags": self.tags(name, Tier=tier)})
        return key

    def instance(self, key, subnet, name, kind="t3.medium", ip=None, sg=None, **tags):
        vpc = next(s["VpcId"] for s in self.raw["subnets"] if s["SubnetId"] == subnet)
        az = next(s["AvailabilityZone"] for s in self.raw["subnets"] if s["SubnetId"] == subnet)
        self.counter += 1
        self.raw["instances"].append({
            "InstanceId": key, "VpcId": vpc, "SubnetId": subnet, "InstanceType": kind, "State": {"Name": "running"},
            "PrivateIpAddress": ip or f"10.0.{self.counter}.{10 + self.counter}", "Placement": {"AvailabilityZone": az},
            "MetadataOptions": {"HttpTokens": "required"}, "Tags": self.tags(name, **tags),
            "SecurityGroups": [{"GroupId": sg}] if sg else [],
            "NetworkInterfaces": [{"NetworkInterfaceId": "eni-" + key[2:]}],
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-" + key[2:]}}]})
        return key

    def load_balancer(self, name, vpc, subnets, kind="application", scheme="internet-facing", sg=None):
        arn = f"{ARN}loadbalancer/{ {'application': 'app', 'network': 'net', 'gateway': 'gwy'}[kind]}/{name}/{len(self.raw['load_balancers']) + 1:016x}"
        azs = [{"ZoneName": next(s["AvailabilityZone"] for s in self.raw["subnets"] if s["SubnetId"] == key), "SubnetId": key} for key in subnets]
        self.raw["load_balancers"].append({"LoadBalancerArn": arn, "LoadBalancerName": name, "Type": kind, "VpcId": vpc,
                                           "Scheme": scheme, "AvailabilityZones": azs, "SecurityGroups": [sg] if sg else []})
        return arn

    def target_group(self, name, vpc, protocol, port, targets, lbs=(), target_type="instance", health="/health"):
        arn = f"{ARN}targetgroup/{name}/{len(self.raw['target_groups']) + 1:016x}"
        self.raw["target_groups"].append({"TargetGroupArn": arn, "TargetGroupName": name, "VpcId": vpc, "TargetType": target_type,
                                          "Protocol": protocol, "Port": port, "HealthCheckPath": health, "LoadBalancerArns": list(lbs)})
        descriptions = []
        for target in targets:
            identifier, target_port, zone = (target + (None, None))[:3] if isinstance(target, tuple) else (target, None, None)
            entry = {"Id": identifier, "Port": target_port or port}
            if zone:
                entry["AvailabilityZone"] = zone
            descriptions.append({"Target": entry, "TargetHealth": {"State": "healthy"}})
        self.raw["target_health"].append({"TargetGroupArn": arn, "Descriptions": descriptions})
        return arn

    def az_of(self, subnet):
        return next(s["AvailabilityZone"] for s in self.raw["subnets"] if s["SubnetId"] == subnet)

    def internet_gateway(self, key, vpc, name):
        self.raw["internet_gateways"].append({"InternetGatewayId": key, "Attachments": [{"VpcId": vpc, "State": "available"}],
                                              "Tags": self.tags(name)})
        return key

    def nat_gateway(self, key, vpc, subnet, name):
        self.raw["nat_gateways"].append({"NatGatewayId": key, "VpcId": vpc, "SubnetId": subnet, "State": "available",
                                         "ConnectivityType": "public", "Tags": self.tags(name)})
        return key

    def route_table(self, key, vpc, name, subnets=(), gateway=None, nat=None, main=False):
        routes = [{"DestinationCidrBlock": next(v["CidrBlock"] for v in self.raw["vpcs"] if v["VpcId"] == vpc), "GatewayId": "local", "State": "active"}]
        if gateway:
            routes.append({"DestinationCidrBlock": "0.0.0.0/0", "GatewayId": gateway, "State": "active"})
        if nat:
            routes.append({"DestinationCidrBlock": "0.0.0.0/0", "NatGatewayId": nat, "State": "active"})
        associations = [{"RouteTableAssociationId": f"rtbassoc-{key[4:]}-{i}", "SubnetId": subnet} for i, subnet in enumerate(subnets)]
        if main:
            associations.append({"RouteTableAssociationId": f"rtbassoc-{key[4:]}-main", "Main": True})
        self.raw["route_tables"].append({"RouteTableId": key, "VpcId": vpc, "Routes": routes, "Associations": associations, "Tags": self.tags(name)})
        return key

    def database(self, identifier, vpc, subnets, primary, engine="mysql", size="db.r6g.large", standby=None):
        group = {"DBSubnetGroupName": f"{identifier}-subnets", "VpcId": vpc,
                 "Subnets": [{"SubnetIdentifier": subnet, "SubnetAvailabilityZone": {"Name": self.az_of(subnet)}} for subnet in subnets]}
        self.raw["db_instances"].append({
            "DBInstanceIdentifier": identifier, "DBInstanceArn": f"arn:aws:rds:{REGION}:{ACCOUNT}:db:{identifier}",
            "Engine": engine, "DBInstanceClass": size, "AvailabilityZone": f"{REGION}{primary}", "MultiAZ": bool(standby),
            "SecondaryAvailabilityZone": f"{REGION}{standby}" if standby else None, "PubliclyAccessible": False,
            "StorageEncrypted": True, "DBSubnetGroup": group})

    def auto_scaling_group(self, name, instances, subnets, target_groups=(), minimum=2, maximum=6):
        self.raw["auto_scaling_groups"].append({
            "AutoScalingGroupName": name, "MinSize": minimum, "MaxSize": maximum, "DesiredCapacity": len(instances),
            "VPCZoneIdentifier": ",".join(subnets), "TargetGroupARNs": list(target_groups), "HealthCheckType": "ELB",
            "Instances": [{"InstanceId": i, "AvailabilityZone": next(x["Placement"]["AvailabilityZone"] for x in self.raw["instances"] if x["InstanceId"] == i),
                           "LifecycleState": "InService"} for i in instances]})

    def route(self, table, destination, **target):
        routes = next(t for t in self.raw["route_tables"] if t["RouteTableId"] == table)["Routes"]
        routes.append({("DestinationPrefixListId" if destination.startswith("pl-") else "DestinationCidrBlock"): destination,
                       **target, "State": "active"})

    def gateway_endpoint(self, key, vpc, service, tables):
        """S3/DynamoDB gateway endpoint: reached through a prefix-list route in each listed table."""
        self.raw["vpc_endpoints"].append({"VpcEndpointId": key, "VpcId": vpc, "VpcEndpointType": "Gateway", "State": "available",
                                          "ServiceName": f"com.amazonaws.{REGION}.{service}", "RouteTableIds": list(tables),
                                          "Tags": self.tags(f"{service}-gateway-endpoint")})
        for table in tables:
            self.route(table, f"pl-{service}", GatewayId=key)

    def interface_endpoint(self, key, vpc, service, subnets):
        self.raw["vpc_endpoints"].append({"VpcEndpointId": key, "VpcId": vpc, "VpcEndpointType": "Interface", "State": "available",
                                          "ServiceName": f"com.amazonaws.{REGION}.{service}", "SubnetIds": list(subnets),
                                          "PrivateDnsEnabled": True, "Tags": self.tags(f"{service}-endpoint")})

    def transit_gateway(self, key, name):
        self.raw["transit_gateways"].append({"TransitGatewayId": key, "State": "available", "Tags": self.tags(name)})
        return key

    def tgw_attachment(self, key, gateway, vpc, subnets, name):
        self.raw["transit_gateway_attachments"].append({"TransitGatewayAttachmentId": key, "TransitGatewayId": gateway, "VpcId": vpc,
                                                        "SubnetIds": list(subnets), "State": "available", "Tags": self.tags(name)})

    def network(self, vpc, prefix, azs, public, private, nat_per_az=True):
        """Internet gateway, NAT in public subnets, and one route table per tier (private per AZ)."""
        igw = self.internet_gateway(f"igw-{prefix}", vpc, f"{prefix}-igw")
        self.route_table(f"rtb-{prefix}-public", vpc, f"{prefix}-public", public, gateway=igw)
        nats = [self.nat_gateway(f"nat-{prefix}-{az}", vpc, public[i], f"{prefix}-nat-{az}") for i, az in enumerate(azs)
                if nat_per_az or i == 0]
        for i, az in enumerate(azs):
            self.route_table(f"rtb-{prefix}-private-{az}", vpc, f"{prefix}-private-{az}", [p[i] for p in private],
                             nat=nats[i] if nat_per_az else nats[0])
        # Subnets without an explicit association (the data tier) use the main table: local route only.
        self.route_table(f"rtb-{prefix}-main", vpc, f"{prefix}-isolated", main=True)

    def snapshot(self):
        timestamp = now()
        coverage = [{"service": service, "operation": operation, "status": "complete", "pages": 1, "items": len(self.raw[key])}
                    for service, operation, key in (("ec2", "describe_vpcs", "vpcs"), ("ec2", "describe_subnets", "subnets"),
                                                    ("ec2", "describe_instances", "instances"),
                                                    ("elbv2", "describe_load_balancers", "load_balancers"),
                                                    ("elbv2", "describe_target_groups", "target_groups"),
                                                    ("ec2", "describe_internet_gateways", "internet_gateways"),
                                                    ("ec2", "describe_nat_gateways", "nat_gateways"),
                                                    ("ec2", "describe_route_tables", "route_tables"),
                                                    ("rds", "describe_db_instances", "db_instances"),
                                                    ("autoscaling", "describe_auto_scaling_groups", "auto_scaling_groups"))]
        coverage += [{"service": "elbv2", "operation": "describe_target_health", "parameters": {"TargetGroupArn": h["TargetGroupArn"]},
                      "status": "complete", "pages": 1, "items": len(h["Descriptions"])} for h in self.raw["target_health"]]
        return {"schema_version": 1, "metadata": {
            "account_id": ACCOUNT, "region": REGION, "partition": "aws", "started_at": timestamp, "finished_at": timestamp,
            "status": "complete", "demo": True, "sample": self.key, "title": self.title, "practices": self.practices,
            "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget", "InternetGateway", "NatGateway",
                      "RouteTable", "RDS", "AutoScalingGroup"],
            "limitations": ["AWS best practice 패턴을 따른 합성 샘플이며 실제 계정 데이터가 아닙니다.",
                            "보안 그룹 규칙·VPC 엔드포인트·리스너 규칙은 수집 범위 밖이라 표시하지 않습니다.",
                            "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."]},
            "coverage": coverage, "raw": self.raw}


def three_tier():
    s = Sample("three-tier", "3계층 웹 애플리케이션 (3 AZ)", [
        "세 AZ에 같은 구성을 배치해 AZ 장애를 견딤 (Reliability)",
        "인터넷용 ALB만 퍼블릭 서브넷, 웹·앱·데이터는 프라이빗 서브넷 (Security)",
        "웹 계층과 앱 계층 사이에 내부 ALB를 두어 계층별 확장·격리",
        "AZ마다 NAT 게이트웨이를 두어 프라이빗 서브넷의 외부 통신이 AZ 장애에 묶이지 않음",
        "웹·앱 계층은 Auto Scaling 그룹(ELB 헬스 체크)으로 AZ에 분산",
        "데이터 계층은 인터넷 경로 없는 격리 서브넷의 RDS Multi-AZ (암호화, 비공개)",
        "S3는 게이트웨이 엔드포인트로 NAT를 거치지 않고, 관리 접근은 SSM 인터페이스 엔드포인트(배스천 없음)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60001", "10.10.0.0/16", "prod-web", Environment="production")
    public, web, app, data = [], [], [], []
    for i, az in enumerate("abc"):
        public.append(s.subnet(f"subnet-pub-{az}", vpc, az, f"10.10.{i}.0/24", f"public-{az}", "public"))
        web.append(s.subnet(f"subnet-web-{az}", vpc, az, f"10.10.{10 + i}.0/24", f"web-private-{az}", "web"))
        app.append(s.subnet(f"subnet-app-{az}", vpc, az, f"10.10.{20 + i}.0/24", f"app-private-{az}", "app"))
        data.append(s.subnet(f"subnet-data-{az}", vpc, az, f"10.10.{30 + i}.0/24", f"data-private-{az}", "data"))
    webs = [s.instance(f"i-web{az}000000000001", web[i], f"web-{az}", "t3.medium", sg="sg-web") for i, az in enumerate("abc")]
    apps = [s.instance(f"i-app{az}000000000001", app[i], f"app-{az}", "m6i.large", sg="sg-app") for i, az in enumerate("abc")]
    s.network(vpc, "prod", "abc", public, [web, app])
    public_alb = s.load_balancer("prod-public-alb", vpc, public, sg="sg-public-alb")
    internal_alb = s.load_balancer("prod-internal-alb", vpc, app, scheme="internal", sg="sg-internal-alb")
    web_tg = s.target_group("prod-web-https", vpc, "HTTPS", 443, webs, [public_alb])
    app_tg = s.target_group("prod-app-http", vpc, "HTTP", 8080, apps, [internal_alb])
    s.auto_scaling_group("prod-web-asg", webs, web, [web_tg], 3, 9)
    s.auto_scaling_group("prod-app-asg", apps, app, [app_tg], 3, 12)
    s.database("prod-orders-db", vpc, data, "a", "mysql", "db.r6g.large", standby="b")
    s.gateway_endpoint("vpce-prod-s3", vpc, "s3", [f"rtb-prod-private-{az}" for az in "abc"])
    s.interface_endpoint("vpce-prod-ssm", vpc, "ssm", app)
    return s.snapshot()


def microservices():
    s = Sample("microservices", "마이크로서비스 경로 기반 라우팅 (ALB 1 · 대상 그룹 4)", [
        "ALB 하나에서 경로별로 서비스 대상 그룹을 나눔 (리스너 규칙은 수집 범위 밖)",
        "컨테이너(awsvpc) 서비스는 IP 대상 유형으로 등록",
        "서비스마다 전용 포트·헬스 체크 경로",
        "두 AZ 이상에 각 서비스 대상을 분산하고 서비스별 Auto Scaling 그룹",
        "서비스 공용 데이터는 격리 서브넷의 RDS Multi-AZ",
        "이미지 풀·S3 접근은 ECR 인터페이스·S3 게이트웨이 엔드포인트로 사설 경로 사용"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60002", "10.20.0.0/16", "commerce-services", Environment="production")
    public = [s.subnet(f"subnet-ms-pub-{az}", vpc, az, f"10.20.{i}.0/24", f"public-{az}", "public") for i, az in enumerate("ab")]
    private = [s.subnet(f"subnet-ms-app-{az}", vpc, az, f"10.20.{10 + i}.0/22", f"services-{az}", "app") for i, az in enumerate("ab")]
    data = [s.subnet(f"subnet-ms-data-{az}", vpc, az, f"10.20.{20 + i}.0/24", f"data-{az}", "data") for i, az in enumerate("ab")]
    catalog = [s.instance(f"i-catalog{az}00000001", private[i], f"catalog-{az}", "c6i.large", sg="sg-services") for i, az in enumerate("ab")]
    orders = [s.instance(f"i-orders{az}000000001", private[i], f"orders-{az}", "c6i.large", sg="sg-services") for i, az in enumerate("ab")]
    s.network(vpc, "commerce", "ab", public, [private])
    alb = s.load_balancer("commerce-alb", vpc, public, sg="sg-commerce-alb")
    catalog_tg = s.target_group("catalog-svc", vpc, "HTTP", 8080, catalog, [alb], health="/catalog/health")
    orders_tg = s.target_group("orders-svc", vpc, "HTTP", 8081, orders, [alb], health="/orders/health")
    s.auto_scaling_group("catalog-asg", catalog, private, [catalog_tg])
    s.auto_scaling_group("orders-asg", orders, private, [orders_tg])
    s.database("commerce-db", vpc, data, "a", "postgres", "db.r6g.xlarge", standby="b")
    s.gateway_endpoint("vpce-commerce-s3", vpc, "s3", [f"rtb-commerce-private-{az}" for az in "ab"])
    s.interface_endpoint("vpce-commerce-ecr", vpc, "ecr.dkr", private)
    s.target_group("cart-svc-ecs", vpc, "HTTP", 9000, [("10.20.10.41", 9000, f"{REGION}a"), ("10.20.11.57", 9000, f"{REGION}b")],
                   [alb], target_type="ip", health="/cart/health")
    s.target_group("payments-svc-ecs", vpc, "HTTP", 9100, [("10.20.10.88", 9100, f"{REGION}a"), ("10.20.11.92", 9100, f"{REGION}b")],
                   [alb], target_type="ip", health="/payments/health")
    return s.snapshot()


def static_ip_front():
    s = Sample("nlb-alb", "고정 IP가 필요한 진입점 (NLB → ALB)", [
        "고정 IP·PrivateLink가 필요한 진입점은 NLB, L7 라우팅은 뒤의 ALB (alb 대상 유형)",
        "TCP 서비스는 NLB 대상 그룹으로 직접 연결",
        "두 AZ에 대상을 분산하고 교차 영역 구성",
        "관리 접근은 배스천 대신 Session Manager 사용 (인스턴스 퍼블릭 IP 없음)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60003", "10.30.0.0/16", "partner-gateway", Environment="production")
    public = [s.subnet(f"subnet-gw-pub-{az}", vpc, az, f"10.30.{i}.0/24", f"public-{az}", "public") for i, az in enumerate("ac")]
    private = [s.subnet(f"subnet-gw-app-{az}", vpc, az, f"10.30.{10 + i}.0/24", f"app-{az}", "app") for i, az in enumerate("ac")]
    api = [s.instance(f"i-api{az}0000000000001", private[i], f"partner-api-{az}", "m6i.large", sg="sg-partner-api") for i, az in enumerate("ac")]
    mqtt = [s.instance(f"i-mqtt{az}000000000001", private[i], f"mqtt-broker-{az}", "c6gn.large", sg="sg-mqtt") for i, az in enumerate("ac")]
    s.network(vpc, "partner", "ac", public, [private])
    nlb = s.load_balancer("partner-nlb", vpc, public, kind="network")
    alb = s.load_balancer("partner-alb", vpc, private, scheme="internal", sg="sg-partner-alb")
    api_tg = s.target_group("partner-api-https", vpc, "HTTPS", 443, api, [alb])
    s.target_group("partner-alb-tcp", vpc, "TCP", 443, [(alb, 443)], [nlb], target_type="alb", health="/")
    mqtt_tg = s.target_group("mqtt-tls", vpc, "TLS", 8883, mqtt, [nlb], health=None)
    s.auto_scaling_group("partner-api-asg", api, private, [api_tg])
    s.auto_scaling_group("mqtt-broker-asg", mqtt, private, [mqtt_tg])
    s.interface_endpoint("vpce-partner-ssm", vpc, "ssm", private)
    return s.snapshot()


def environments():
    s = Sample("environments", "환경별 VPC 분리 (운영 · 스테이징 · 공용 서비스)", [
        "환경마다 VPC와 CIDR을 분리하고 겹치지 않게 할당 (권장: 계정까지 분리)",
        "운영은 3 AZ·AZ별 NAT, 스테이징은 비용을 고려한 2 AZ·NAT 1개",
        "공용 서비스 VPC에는 내부 도구만 두고 인터넷 경로 없음",
        "VPC 간 연결은 Transit Gateway 허브로 일원화 (VPC 피어링 그물망 대신)",
        "Environment 태그로 소유·비용 구분"])
    for env, cidr, azs, size in (("prod", "10.40", "abc", "m6i.large"), ("staging", "10.41", "ab", "t3.medium")):
        vpc = s.vpc(f"vpc-{env}0000000000004", f"{cidr}.0.0/16", f"{env}-vpc", Environment=env)
        public = [s.subnet(f"subnet-{env}-pub-{az}", vpc, az, f"{cidr}.{i}.0/24", f"{env}-public-{az}", "public") for i, az in enumerate(azs)]
        app = [s.subnet(f"subnet-{env}-app-{az}", vpc, az, f"{cidr}.{10 + i}.0/24", f"{env}-app-{az}", "app") for i, az in enumerate(azs)]
        nodes = [s.instance(f"i-{env}{az}00000000001", app[i], f"{env}-api-{az}", size, sg=f"sg-{env}-api", Environment=env)
                 for i, az in enumerate(azs)]
        # Production keeps a NAT per AZ; staging shares one NAT to save cost.
        s.network(vpc, env, azs, public, [app], nat_per_az=env == "prod")
        alb = s.load_balancer(f"{env}-alb", vpc, public, sg=f"sg-{env}-alb")
        group = s.target_group(f"{env}-api", vpc, "HTTPS", 443, nodes, [alb])
        s.auto_scaling_group(f"{env}-api-asg", nodes, app, [group], len(azs), 3 * len(azs))
    shared = s.vpc("vpc-shared000000000004", "10.42.0.0/16", "shared-services", Environment="shared")
    tools = [s.subnet(f"subnet-shared-{az}", shared, az, f"10.42.{i}.0/24", f"tools-{az}", "app") for i, az in enumerate("ab")]
    hub = s.transit_gateway("tgw-corp0000000004", "corp-tgw")
    s.instance("i-ci0000000000000004", tools[0], "ci-runner", "c6i.xlarge", sg="sg-ci", Environment="shared")
    s.instance("i-monitor00000000004", tools[1], "monitoring", "m6i.large", sg="sg-monitoring", Environment="shared")
    s.route_table("rtb-shared-main", shared, "shared-isolated", main=True)
    # Environments reach shared services only through the Transit Gateway (10.0.0.0/8), never by peering.
    s.route("rtb-shared-main", "10.0.0.0/8", TransitGatewayId=hub)
    s.tgw_attachment("tgw-attach-shared", hub, shared, tools, "shared-attachment")
    for env, azs in (("prod", "abc"), ("staging", "ab")):
        s.tgw_attachment(f"tgw-attach-{env}", hub, f"vpc-{env}0000000000004", [f"subnet-{env}-app-{az}" for az in azs], f"{env}-attachment")
        for az in azs:
            s.route(f"rtb-{env}-private-{az}", "10.0.0.0/8", TransitGatewayId=hub)
    return s.snapshot()


def hybrid():
    s = Sample("hybrid", "하이브리드 대상 (온프레미스 IP 포함)", [
        "점진 이전 중 온프레미스 서버를 IP 대상으로 함께 등록 (AvailabilityZone=all)",
        "VPC 밖 대상은 서브넷에 넣지 않고 별도 영역에 표시",
        "클라우드 대상과 온프레미스 대상을 같은 대상 그룹에서 헬스 체크",
        "내부 ALB로 사내망 전용 노출",
        "온프레미스 대역(192.168.0.0/16)은 Transit Gateway로 라우팅 (VPN/Direct Connect 연결은 수집 범위 밖)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60005", "10.50.0.0/16", "migration-hub", Environment="production")
    app = [s.subnet(f"subnet-hy-app-{az}", vpc, az, f"10.50.{10 + i}.0/24", f"app-{az}", "app") for i, az in enumerate("ab")]
    cloud = [s.instance(f"i-erp{az}0000000000005", app[i], f"erp-cloud-{az}", "r6i.large", sg="sg-erp") for i, az in enumerate("ab")]
    # Internal only: no internet gateway; the main route table has just the local route
    # (the on-premises path over VPN/Direct Connect is outside the collected scope).
    s.route_table("rtb-hybrid-main", vpc, "hybrid-isolated", main=True)
    hub = s.transit_gateway("tgw-hybrid00000005", "hybrid-tgw")
    s.route("rtb-hybrid-main", "192.168.0.0/16", TransitGatewayId=hub)
    s.tgw_attachment("tgw-attach-hybrid", hub, vpc, app, "migration-attachment")
    alb = s.load_balancer("erp-internal-alb", vpc, app, scheme="internal", sg="sg-erp-alb")
    erp = s.target_group("erp-cloud", vpc, "HTTPS", 443, cloud, [alb])
    s.auto_scaling_group("erp-cloud-asg", cloud, app, [erp])
    s.target_group("erp-onprem", vpc, "HTTPS", 443, [("192.168.20.11", 443, "all"), ("192.168.20.12", 443, "all")],
                   [alb], target_type="ip")
    return s.snapshot()


SAMPLES = {"three-tier": three_tier, "microservices": microservices, "nlb-alb": static_ip_front,
           "environments": environments, "hybrid": hybrid}
