"""Auto Scaling groups, launch templates and instances; EKS clusters and node groups; ECS clusters and services."""
from collections import Counter

from .base import ROLE_PATTERN, camel, name_of


class Compute:
    def eks_groups(self):
        return {a.get("name") for c in self.raw.get("eks_clusters", []) for n in c.get("nodegroups", [])
                for a in (n.get("resources") or {}).get("autoScalingGroups", [])}

    def instance_settings(self, instance, vpc):
        """Shared EC2 settings for launch templates and instances: AMI parameter, groups (EFA interface when present), IMDS."""
        groups = self.security_groups([g.get("GroupId") for g in instance.get("SecurityGroups", [])], vpc)
        settings = {"ImageId": self.image(instance.get("InstanceType"), instance.get("Architecture")), "InstanceType": instance.get("InstanceType")}
        if any(n.get("InterfaceType") == "efa" for n in instance.get("NetworkInterfaces", [])):
            settings["NetworkInterfaces"] = [{"DeviceIndex": 0, "InterfaceType": "efa", "Groups": groups}]
        else:
            settings["SecurityGroupIds"] = groups
        if (instance.get("MetadataOptions") or {}).get("HttpTokens"):
            settings["MetadataOptions"] = {"HttpTokens": instance["MetadataOptions"]["HttpTokens"]}
        if instance.get("KeyName"):
            settings["KeyName"] = self.param("KeyName", "AWS::EC2::KeyPair::KeyName", "SSH 키 페어 이름 (수집된 인스턴스가 키 페어를 씀)")
        if instance.get("IamInstanceProfile"):
            self.assume("IAM 인스턴스 프로필은 수집 범위 밖이라 붙이지 않습니다. 필요한 역할을 추가하세요.")
        return settings

    def auto_scaling(self):
        eks = self.eks_groups()
        for group in self.raw.get("auto_scaling_groups", []):
            name = group["AutoScalingGroupName"]
            if name in eks:
                continue  # EKS managed node groups own their Auto Scaling groups
            subnets_ids = [s for s in (group.get("VPCZoneIdentifier") or "").split(",") if s]
            subnets = self.subnet_refs(subnets_ids)
            members = [self.instances[i["InstanceId"]] for i in group.get("Instances", []) if i.get("InstanceId") in self.instances]
            if not subnets or not members:
                self.skip("AutoScalingGroup", name, "서브넷이 템플릿에 없거나, 구성원 인스턴스가 없어 인스턴스 유형을 알 수 없습니다.")
                continue
            sample = Counter(m.get("InstanceType") for m in members).most_common(1)[0][0]
            instance = next(m for m in members if m.get("InstanceType") == sample)
            data = self.instance_settings(instance, self.vpc_of(subnets_ids))
            placement = (instance.get("Placement") or {}).get("GroupName")
            if placement and "placement:" + placement in self.ids:
                data["Placement"] = {"GroupName": self.ref("placement:" + placement)}
            logical = self.logical("Asg", name)
            template = self.add(self.unique(f"{logical}LaunchTemplate"), "AWS::EC2::LaunchTemplate", {"LaunchTemplateData": data})
            self.add(logical, "AWS::AutoScaling::AutoScalingGroup", {
                "MinSize": str(group.get("MinSize", 0)), "MaxSize": str(group.get("MaxSize", 0)), "DesiredCapacity": str(group.get("DesiredCapacity", len(members))),
                "VPCZoneIdentifier": subnets, "HealthCheckType": group.get("HealthCheckType"), "HealthCheckGracePeriod": group.get("HealthCheckGracePeriod"),
                "TargetGroupARNs": [self.ref(arn) for arn in group.get("TargetGroupARNs", []) if arn in self.ids],
                "LaunchTemplate": {"LaunchTemplateId": {"Ref": template}, "Version": {"Fn::GetAtt": [template, "LatestVersionNumber"]}},
                "Tags": [{"Key": "Name", "Value": name, "PropagateAtLaunch": True}] +
                        [{"Key": t["Key"], "Value": t.get("Value", ""), "PropagateAtLaunch": bool(t.get("PropagateAtLaunch"))}
                         for t in group.get("Tags", []) if t.get("Key") not in ("Name", None) and not t["Key"].startswith("aws:")]},
                physical="asg:" + name)
            if len({m.get("InstanceType") for m in members}) > 1:
                self.assume(f"Auto Scaling 그룹 {name}: 구성원 인스턴스 유형이 여럿이라 가장 많은 {sample}로 시작 템플릿을 만듭니다.")

    def standalone_instances(self):
        members = {i["InstanceId"] for g in self.raw.get("auto_scaling_groups", []) for i in g.get("Instances", [])}
        for iid, instance in self.instances.items():
            if iid in members:
                continue
            tags = {t.get("Key"): t.get("Value") for t in instance.get("Tags") or []}
            name = name_of(instance, iid)
            reason = ("Auto Scaling 그룹이 스냅샷에 없습니다." if "aws:autoscaling:groupName" in tags else
                      "노드 그룹 밖의 EKS 노드(Karpenter 등)는 쿠버네티스 컨트롤러가 만듭니다." if "eks:cluster-name" in tags else
                      "Capacity Block 인스턴스는 템플릿 밖에서 용량을 구매해야 합니다." if instance.get("InstanceLifecycle") == "capacity-block" else
                      "Spot 인스턴스는 템플릿에서 온디맨드와 구분해 표현하지 않습니다." if instance.get("InstanceLifecycle") == "spot" else
                      "서브넷이 템플릿에 없습니다." if instance.get("SubnetId") not in self.ids else None)
            if reason:
                self.skip("EC2Instance", iid, reason, name)
                continue
            settings = self.instance_settings(instance, instance.get("VpcId"))
            placement = (instance.get("Placement") or {}).get("GroupName")
            placement = self.ref("placement:" + placement) if placement and "placement:" + placement in self.ids else None
            if (instance.get("State") or {}).get("Name") == "stopped":
                self.assume(f"인스턴스 {name}: 수집 시 중지 상태였지만 CloudFormation은 실행 상태로 만듭니다.")
            logical = self.logical("Instance", name, iid)
            if "NetworkInterfaces" in settings or instance.get("CapacityReservationId"):
                # EFA interfaces and capacity reservation targets exist only in launch templates.
                if "NetworkInterfaces" in settings:
                    settings["NetworkInterfaces"][0]["SubnetId"] = self.ref(instance["SubnetId"])
                else:
                    settings["NetworkInterfaces"] = [{"DeviceIndex": 0, "SubnetId": self.ref(instance["SubnetId"]), "Groups": settings.pop("SecurityGroupIds", None)}]
                if placement:
                    settings["Placement"] = {"GroupName": placement}
                if instance.get("CapacityReservationId"):
                    settings["CapacityReservationSpecification"] = {"CapacityReservationTarget": {"CapacityReservationId": self.param(
                        "CapacityReservation" + camel(instance["CapacityReservationId"]), "String", "온디맨드 용량 예약 ID (템플릿 밖에서 구매한 예약)",
                        Default=instance["CapacityReservationId"])}}
                settings["NetworkInterfaces"] = [{k: v for k, v in n.items() if v is not None} for n in settings["NetworkInterfaces"]]
                template = self.add(self.unique(f"{logical}LaunchTemplate"), "AWS::EC2::LaunchTemplate", {"LaunchTemplateData": settings})
                props = {"LaunchTemplate": {"LaunchTemplateId": {"Ref": template}, "Version": {"Fn::GetAtt": [template, "LatestVersionNumber"]}}}
            else:
                props = {**settings, "SubnetId": self.ref(instance["SubnetId"]), "PlacementGroupName": placement}
            self.add(logical, "AWS::EC2::Instance", {**props, "Tags": self.tags(instance)}, physical=iid)

    # --- containers ----------------------------------------------------------------------------
    def kubernetes(self):
        clusters = self.raw.get("eks_clusters", [])
        if not clusters:
            return
        role = lambda name, text: self.param(name, "String", text, AllowedPattern=ROLE_PATTERN)
        asgs = {g["AutoScalingGroupName"]: g for g in self.raw.get("auto_scaling_groups", [])}
        for cluster in clusters:
            name, config = cluster.get("name"), cluster.get("resourcesVpcConfig") or {}
            subnets = self.subnet_refs(config.get("subnetIds", []))
            if not subnets:
                self.skip("EKSCluster", name, "클러스터 서브넷이 템플릿에 없습니다.")
                continue
            vpc = config.get("vpcId")
            logical = self.add(self.logical("Eks", name), "AWS::EKS::Cluster", {
                "Version": cluster.get("version"), "RoleArn": role("EksClusterRoleArn", "EKS 클러스터 IAM 역할 ARN (IAM은 수집 범위 밖)"),
                "ResourcesVpcConfig": {"SubnetIds": subnets, "SecurityGroupIds": self.security_groups(config.get("securityGroupIds", []), vpc),
                                       "EndpointPublicAccess": config.get("endpointPublicAccess"), "EndpointPrivateAccess": config.get("endpointPrivateAccess"),
                                       "PublicAccessCidrs": config.get("publicAccessCidrs") if config.get("endpointPublicAccess") else None},
                "Tags": [{"Key": "Name", "Value": name}]}, physical="eks:" + name)
            for group in cluster.get("nodegroups", []):
                asg_names = [a.get("name") for a in (group.get("resources") or {}).get("autoScalingGroups", [])]
                asg = asgs.get(asg_names[0]) if asg_names else None
                group_subnets = group.get("subnets") or [s for s in ((asg or {}).get("VPCZoneIdentifier") or "").split(",") if s] or config.get("subnetIds", [])
                refs = self.subnet_refs(group_subnets)
                if not refs:
                    self.skip("EKSNodegroup", f"{name}/{group.get('nodegroupName')}", "노드 그룹 서브넷이 템플릿에 없습니다.")
                    continue
                members = [self.instances[i["InstanceId"]] for i in (asg or {}).get("Instances", []) if i.get("InstanceId") in self.instances]
                props = {"ClusterName": {"Ref": logical}, "NodeRole": role("EksNodeRoleArn", "EKS 노드 IAM 역할 ARN (IAM은 수집 범위 밖)"),
                         "Subnets": refs, "InstanceTypes": group.get("instanceTypes"), "AmiType": group.get("amiType"), "CapacityType": group.get("capacityType"),
                         "ScalingConfig": {k[0].upper() + k[1:]: v for k, v in (group.get("scalingConfig") or {}).items()},
                         "Labels": group.get("labels"),
                         "Taints": [{"Key": t.get("key"), "Value": t.get("value"), "Effect": t.get("effect")} for t in group.get("taints") or []],
                         "Tags": {"Name": group.get("nodegroupName")}}
                efa = any(n.get("InterfaceType") == "efa" for m in members for n in m.get("NetworkInterfaces", []))
                placement = next(((m.get("Placement") or {}).get("GroupName") for m in members if (m.get("Placement") or {}).get("GroupName")), None)
                if efa or placement:
                    # EFA interfaces and cluster placement need a launch template (no AMI: EKS keeps choosing the node AMI).
                    groups = self.security_groups([g.get("GroupId") for g in members[0].get("SecurityGroups", [])], vpc) if members else None
                    data = {}
                    if efa:
                        data["NetworkInterfaces"] = [{"DeviceIndex": 0, "InterfaceType": "efa", "Groups": groups}]
                    elif groups:
                        data["SecurityGroupIds"] = groups
                    if placement and "placement:" + placement in self.ids:
                        data["Placement"] = {"GroupName": self.ref("placement:" + placement)}
                    template = self.add(self.logical(logical + "LaunchTemplate", group.get("nodegroupName")), "AWS::EC2::LaunchTemplate", {"LaunchTemplateData": data})
                    props["LaunchTemplate"] = {"Id": {"Ref": template}, "Version": {"Fn::GetAtt": [template, "LatestVersionNumber"]}}
                elif group.get("diskSize"):
                    props["DiskSize"] = group["diskSize"]
                if group.get("launchTemplate"):
                    self.assume(f"EKS 노드 그룹 {group.get('nodegroupName')}: 수집된 사용자 시작 템플릿 내용은 조회하지 않아 이어받지 않습니다.")
                self.add(self.logical(logical + "Nodegroup", group.get("nodegroupName")), "AWS::EKS::Nodegroup", {k: v for k, v in props.items() if v is not None})
            self.assume("EKS 관리형 노드 그룹은 EKS 클러스터 보안 그룹을 씁니다 (EFA·배치 그룹이 있는 노드 그룹만 시작 템플릿에 보안 그룹 지정).")

    def containers(self):
        services = self.raw.get("ecs_services", [])
        if not services:
            return
        clusters = {}
        for service in services:
            name, cluster_arn = service.get("serviceName"), service.get("clusterArn", "")
            network = (service.get("networkConfiguration") or {}).get("awsvpcConfiguration") or {}
            subnets = self.subnet_refs(network.get("subnets", []))
            strategy = [s for s in service.get("capacityProviderStrategy") or [] if s.get("capacityProvider") in ("FARGATE", "FARGATE_SPOT")]
            if service.get("status") in ("INACTIVE", "DRAINING") or not subnets or (service.get("launchType") != "FARGATE" and not strategy):
                self.skip("ECSService", service.get("serviceArn"), "Fargate awsvpc 서비스만 지원합니다 (EC2 용량 공급자·서브넷 없음).", name)
                continue
            if cluster_arn not in clusters:
                cluster_name = cluster_arn.rsplit("/", 1)[-1]
                clusters[cluster_arn] = self.add(self.logical("EcsCluster", cluster_name), "AWS::ECS::Cluster", {
                    "Tags": [{"Key": "Name", "Value": cluster_name}]}, physical=cluster_arn)
            if strategy:
                self.resources[clusters[cluster_arn]]["Properties"]["CapacityProviders"] = ["FARGATE", "FARGATE_SPOT"]
            balancers = [lb for lb in service.get("loadBalancers", []) if lb.get("targetGroupArn") in self.ids]
            task = next((t for t in self.raw.get("ecs_tasks", []) if t.get("group") == f"service:{name}" and t.get("clusterArn") == cluster_arn), {})
            if not task:
                self.assume(f"ECS 서비스 {name}: 실행 중 태스크가 없어 CPU·메모리를 Fargate 최소 크기(256/512)로 둡니다.")
            logical = self.logical("EcsService", name)
            container = balancers[0].get("containerName") if balancers else name
            port = balancers[0].get("containerPort") if balancers else None
            image = self.param(self.unique(f"{logical}Image"), "String", f"ECS 서비스 {name}의 컨테이너 이미지 URI (작업 정의는 수집하지 않음)")
            definition = self.add(self.unique(f"{logical}TaskDefinition"), "AWS::ECS::TaskDefinition", {
                "RequiresCompatibilities": ["FARGATE"], "NetworkMode": "awsvpc", "Cpu": str(task.get("cpu", "256")), "Memory": str(task.get("memory", "512")),
                "ExecutionRoleArn": self.param("EcsTaskExecutionRoleArn", "String", "ECS 태스크 실행 IAM 역할 ARN (ECR 이미지 풀·로그; IAM은 수집 범위 밖)",
                                               AllowedPattern=ROLE_PATTERN),
                "ContainerDefinitions": [{"Name": container, "Image": image, "Essential": True,
                                          **({"PortMappings": [{"ContainerPort": port, "Protocol": "tcp"}]} if port else {})}]})
            breaker = ((service.get("deploymentConfiguration") or {}).get("deploymentCircuitBreaker")) or None
            self.add(logical, "AWS::ECS::Service", {
                "Cluster": {"Ref": clusters[cluster_arn]}, "TaskDefinition": {"Ref": definition}, "DesiredCount": service.get("desiredCount"),
                "LaunchType": "FARGATE" if not strategy else None,
                "CapacityProviderStrategy": [{"CapacityProvider": s["capacityProvider"], "Weight": s.get("weight"), "Base": s.get("base")} for s in strategy] or None,
                "NetworkConfiguration": {"AwsvpcConfiguration": {
                    "Subnets": subnets, "AssignPublicIp": network.get("assignPublicIp"),
                    "SecurityGroups": self.security_groups(network.get("securityGroups", []), self.vpc_of(network.get("subnets", [])))}},
                "LoadBalancers": [{"TargetGroupArn": self.ref(lb["targetGroupArn"]), "ContainerName": container, "ContainerPort": lb.get("containerPort")}
                                  for lb in balancers],
                "DeploymentConfiguration": {"DeploymentCircuitBreaker": {"Enable": breaker.get("enable"), "Rollback": breaker.get("rollback")}} if breaker else None,
                "AvailabilityZoneRebalancing": service.get("availabilityZoneRebalancing"),
                "Tags": [{"Key": "Name", "Value": name}]},
                physical=service.get("serviceArn"), depends=[d for lb in balancers for d in self.listeners.get(lb["targetGroupArn"], [])])
