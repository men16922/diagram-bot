"""Load balancers, listeners and target groups with their registered targets."""
import ipaddress


class Balancing:
    def load_balancers(self):
        for lb in self.raw.get("load_balancers", []):
            arn, name = lb["LoadBalancerArn"], lb.get("LoadBalancerName")
            subnets = self.subnet_refs([z.get("SubnetId") for z in lb.get("AvailabilityZones", [])])
            if lb.get("Type") not in ("application", "network") or not subnets:
                self.skip("LoadBalancer", arn, "Gateway Load Balancer이거나 서브넷이 템플릿에 없습니다.", name)
                continue
            if any(address.get("AllocationId") for z in lb.get("AvailabilityZones", []) for address in z.get("LoadBalancerAddresses", [])):
                self.assume(f"NLB {name}: 수집된 고정 Elastic IP는 이어받지 않고 AWS가 주소를 새로 할당합니다.")
            logical = self.add(self.logical("LoadBalancer", name, arn), "AWS::ElasticLoadBalancingV2::LoadBalancer", {
                "Type": lb["Type"], "Scheme": lb.get("Scheme"), "Subnets": subnets, "IpAddressType": lb.get("IpAddressType"),
                "SecurityGroups": self.security_groups(lb.get("SecurityGroups", []), lb.get("VpcId")),
                "Tags": [{"Key": "Name", "Value": name}]}, physical=arn)
            self.outputs[f"{logical}DnsName"] = {"Description": name, "Value": {"Fn::GetAtt": [logical, "DNSName"]}}
        for group in self.raw.get("target_groups", []):
            arn, name = group["TargetGroupArn"], group.get("TargetGroupName")
            if group.get("VpcId") not in self.ids or group.get("TargetType") == "lambda":
                self.skip("TargetGroup", arn, "VPC가 템플릿에 없거나 Lambda 대상 그룹입니다.", name)
                continue
            http = group.get("Protocol") in ("HTTP", "HTTPS")
            self.add(self.logical("TargetGroup", name, arn), "AWS::ElasticLoadBalancingV2::TargetGroup", {
                "VpcId": self.ref(group["VpcId"]), "TargetType": group.get("TargetType", "instance"), "Protocol": group.get("Protocol"),
                "Port": group.get("Port"), "ProtocolVersion": group.get("ProtocolVersion") if http else None,
                "HealthCheckProtocol": group.get("HealthCheckProtocol"), "HealthCheckPort": group.get("HealthCheckPort"),
                "HealthCheckPath": group.get("HealthCheckPath") if http or group.get("HealthCheckProtocol") in ("HTTP", "HTTPS") else None,
                "Tags": [{"Key": "Name", "Value": name}]}, physical=arn)
        self.assume("리스너·리스너 규칙은 수집 범위 밖이라 대상 그룹과 보안 그룹 포트에서 추정합니다. 포트·프로토콜·경로 조건을 검토하세요.")
        for lb in self.raw.get("load_balancers", []):
            arn = lb["LoadBalancerArn"]
            if arn not in self.ids:
                continue
            groups = [g for g in self.raw.get("target_groups", []) if arn in g.get("LoadBalancerArns", []) and g["TargetGroupArn"] in self.ids]
            if not groups:
                continue
            (self.application_listener if lb["Type"] == "application" else self.network_listeners)(lb, groups)

    def certificate(self, lb):
        name = f"{self.ids[lb['LoadBalancerArn']]}CertificateArn"
        return self.param(name, "String", f"{lb.get('LoadBalancerName')} TLS 리스너의 ACM 인증서 ARN (수집하지 않음)", AllowedPattern=r"^arn:aws[a-z-]*:acm:.+$")

    def listener(self, lb, protocol, port, actions):
        logical = self.add(self.logical(self.ids[lb["LoadBalancerArn"]] + "Listener", f"{protocol}{port}"), "AWS::ElasticLoadBalancingV2::Listener", {
            "LoadBalancerArn": self.ref(lb["LoadBalancerArn"]), "Protocol": protocol, "Port": port,
            "Certificates": [{"CertificateArn": self.certificate(lb)}] if protocol in ("HTTPS", "TLS") else None, "DefaultActions": actions})
        self.listeners.setdefault(lb["LoadBalancerArn"], []).append(logical)
        return logical

    def application_listener(self, lb, groups):
        """One listener: the port the ALB's security group opens (else 443 when public or HTTPS), first target group as default."""
        opened = [p.get("FromPort") for g in lb.get("SecurityGroups", []) for p in self.groups.get(g, {}).get("IpPermissions", [])
                  if p.get("IpProtocol") == "tcp" and p.get("FromPort") == p.get("ToPort")]
        port = opened[0] if opened else 443 if lb.get("Scheme") == "internet-facing" or any(g.get("Protocol") == "HTTPS" for g in groups) else groups[0].get("Port")
        protocol = "HTTPS" if port == 443 else "HTTP"
        forward = lambda group: [{"Type": "forward", "TargetGroupArn": self.ref(group["TargetGroupArn"])}]
        listener = self.listener(lb, protocol, port, forward(groups[0]))
        self.listeners.setdefault(groups[0]["TargetGroupArn"], []).append(listener)
        for priority, group in enumerate(groups[1:], 1):
            name = f"{self.ids[group['TargetGroupArn']]}PathPattern"
            pattern = self.param(name, "String", f"{group.get('TargetGroupName')}로 보낼 경로 조건 (리스너 규칙은 수집하지 않아 자리표시 값)",
                                 Default=f"/{group.get('TargetGroupName')}/*")
            rule = self.add(self.logical(listener + "Rule", group.get("TargetGroupName")), "AWS::ElasticLoadBalancingV2::ListenerRule", {
                "ListenerArn": {"Ref": listener}, "Priority": priority * 10, "Actions": forward(group),
                "Conditions": [{"Field": "path-pattern", "PathPatternConfig": {"Values": [pattern]}}]})
            self.listeners.setdefault(group["TargetGroupArn"], []).append(rule)

    def network_listeners(self, lb, groups):
        """NLBs have no rules: one listener per target group on the group's own port and protocol."""
        used = set()
        for group in groups:
            port, protocol = group.get("Port"), group.get("Protocol", "TCP")
            if port in used:
                self.skip("Listener", group["TargetGroupArn"], f"{lb.get('LoadBalancerName')}에 같은 포트({port})의 대상 그룹이 둘 이상이라 리스너를 추정할 수 없습니다.",
                          group.get("TargetGroupName"))
                continue
            used.add(port)
            listener = self.listener(lb, protocol, port, [{"Type": "forward", "TargetGroupArn": self.ref(group["TargetGroupArn"])}])
            self.listeners.setdefault(group["TargetGroupArn"], []).append(listener)

    def targets(self):
        """Instance targets outside Auto Scaling, ALB targets of NLBs and IP targets outside every VPC (on premises)."""
        managed = {i["InstanceId"] for g in self.raw.get("auto_scaling_groups", []) for i in g.get("Instances", [])}
        ecs_groups = {lb.get("targetGroupArn") for s in self.raw.get("ecs_services", []) for lb in s.get("loadBalancers", [])}
        networks = [ipaddress.ip_network(v["CidrBlock"]) for v in self.raw.get("vpcs", []) if v.get("CidrBlock")]
        for health in self.raw.get("target_health", []):
            arn = health["TargetGroupArn"]
            if arn not in self.ids:
                continue
            group = next(g for g in self.raw.get("target_groups", []) if g["TargetGroupArn"] == arn)
            kind, targets, depends = group.get("TargetType", "instance"), [], []
            for description in health.get("Descriptions", []):
                target = description.get("Target") or {}
                identifier, port = target.get("Id"), target.get("Port")
                if kind == "instance":
                    if identifier in managed:
                        continue
                    if identifier not in self.ids:
                        self.skip("RegisteredTarget", identifier, f"{group.get('TargetGroupName')}의 대상 인스턴스를 템플릿에서 만들지 않습니다 (미조회 또는 미지원).")
                        continue
                    targets.append({"Id": self.ref(identifier), "Port": port})
                elif kind == "alb":
                    if identifier not in self.ids:
                        self.skip("RegisteredTarget", identifier, "대상 ALB가 템플릿에 없습니다.")
                        continue
                    targets.append({"Id": self.ref(identifier), "Port": port})
                    depends += self.listeners.get(identifier, [])
                elif arn in ecs_groups:
                    continue  # ECS registers its tasks' addresses
                else:
                    try:
                        inside = any(ipaddress.ip_address(identifier) in network for network in networks)
                    except ValueError:
                        inside = True
                    if inside:
                        self.skip("RegisteredTarget", identifier, f"{group.get('TargetGroupName')}: VPC 안 IP 대상은 배포 후 주소가 달라지고 컨트롤러(EKS 등)가 등록할 수 있어 넣지 않습니다.")
                        continue
                    targets.append({"Id": identifier, "Port": port, "AvailabilityZone": target.get("AvailabilityZone")})
            if targets:
                resource = self.resources[self.ids[arn]]
                resource["Properties"]["Targets"] = [{k: v for k, v in t.items() if v is not None} for t in targets]
                if depends:
                    resource["DependsOn"] = sorted(set(resource.get("DependsOn", []) + depends))
