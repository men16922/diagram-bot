"""Synthetic reference architectures following AWS best-practice patterns. Never live data.

Each sample uses only fields the collector reads from the AWS APIs, so it renders exactly like a
collected snapshot would.
"""
import hashlib
import ipaddress

from .collector import now

ACCOUNT, REGION = "111122223333", "ap-northeast-2"
# Bumped whenever a sample's architecture changes; the server replaces stored samples of another version.
SAMPLE_VERSION = 2


class Sample:
    def __init__(self, key, title, practices, region=REGION, account=ACCOUNT):
        self.key, self.title, self.practices = key, title, practices
        self.region, self.account = region, account
        self.raw = {"vpcs": [], "subnets": [], "instances": [], "load_balancers": [], "target_groups": [], "target_health": [],
                    "internet_gateways": [], "nat_gateways": [], "route_tables": [], "db_instances": [], "auto_scaling_groups": [],
                    "vpc_endpoints": [], "transit_gateways": [], "transit_gateway_attachments": [],
                    "db_clusters": [], "cache_clusters": [], "replication_groups": [], "cache_subnet_groups": [],
                    "msk_clusters": [], "msk_nodes": [], "lambda_functions": [], "eks_clusters": [],
                    "opensearch_domains": [], "opensearch_nodes": [], "ecs_services": [], "ecs_tasks": [],
                    "efs_file_systems": [], "efs_mount_targets": [], "redshift_clusters": [], "redshift_subnet_groups": [],
                    "vpc_peering_connections": [], "vpn_gateways": [], "vpn_connections": [], "customer_gateways": [],
                    "security_groups": [], "placement_groups": [], "fsx_file_systems": [], "sagemaker_endpoints": [],
                    "sagemaker_clusters": []}
        self.counter = 0

    @property
    def arn(self):
        return f"arn:aws:elasticloadbalancing:{self.region}:{self.account}:"

    def tags(self, name, **extra):
        return [{"Key": "Name", "Value": name}] + [{"Key": k, "Value": v} for k, v in extra.items()]

    def vpc(self, key, cidr, name, **tags):
        self.raw["vpcs"].append({"VpcId": key, "CidrBlock": cidr, "Tags": self.tags(name, **tags)})
        return key

    def subnet(self, key, vpc, az, cidr, name, tier):
        self.raw["subnets"].append({"SubnetId": key, "VpcId": vpc, "AvailabilityZone": f"{self.region}{az}", "CidrBlock": cidr,
                                    "MapPublicIpOnLaunch": tier == "public", "Tags": self.tags(name, Tier=tier)})
        return key

    def instance(self, key, subnet, name, kind="t3.medium", ip=None, sg=None, efa=False, placement=None, lifecycle=None, **tags):
        vpc = next(s["VpcId"] for s in self.raw["subnets"] if s["SubnetId"] == subnet)
        az = next(s["AvailabilityZone"] for s in self.raw["subnets"] if s["SubnetId"] == subnet)
        self.counter += 1
        self.raw["instances"].append({
            "InstanceId": key, "VpcId": vpc, "SubnetId": subnet, "InstanceType": kind, "State": {"Name": "running"},
            "PrivateIpAddress": ip or f"10.0.{self.counter}.{10 + self.counter}",
            "Placement": {"AvailabilityZone": az, **({"GroupName": placement} if placement else {})},
            **({"InstanceLifecycle": lifecycle} if lifecycle else {}),
            "MetadataOptions": {"HttpTokens": "required"}, "Tags": self.tags(name, **tags),
            "SecurityGroups": [{"GroupId": sg}] if sg else [],
            "NetworkInterfaces": [{"NetworkInterfaceId": "eni-" + key[2:], "InterfaceType": "efa" if efa else "interface"}],
            "BlockDeviceMappings": [{"Ebs": {"VolumeId": "vol-" + key[2:]}}]})
        return key

    def load_balancer(self, name, vpc, subnets, kind="application", scheme="internet-facing", sg=None):
        arn = f"{self.arn}loadbalancer/{ {'application': 'app', 'network': 'net', 'gateway': 'gwy'}[kind]}/{name}/{len(self.raw['load_balancers']) + 1:016x}"
        azs = [{"ZoneName": next(s["AvailabilityZone"] for s in self.raw["subnets"] if s["SubnetId"] == key), "SubnetId": key} for key in subnets]
        self.raw["load_balancers"].append({"LoadBalancerArn": arn, "LoadBalancerName": name, "Type": kind, "VpcId": vpc,
                                           "Scheme": scheme, "AvailabilityZones": azs, "SecurityGroups": [sg] if sg else []})
        return arn

    def target_group(self, name, vpc, protocol, port, targets, lbs=(), target_type="instance", health="/health"):
        arn = f"{self.arn}targetgroup/{name}/{len(self.raw['target_groups']) + 1:016x}"
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

    def address(self, subnet, offset):
        cidr = next(s["CidrBlock"] for s in self.raw["subnets"] if s["SubnetId"] == subnet)
        return str(ipaddress.ip_network(cidr)[offset])

    def security_group(self, key, vpc, name, ingress, egress=("0.0.0.0/0",)):
        """Rules as (port, source): a CIDR, another group's ID or "self"; port None allows all traffic.

        Egress is a list of sources with all traffic allowed (the default is AWS's 0.0.0.0/0)."""
        def rule(port, source):
            source = key if source == "self" else source
            ports = {"IpProtocol": "-1"} if port is None else {"IpProtocol": "tcp", "FromPort": port, "ToPort": port}
            return {**ports, **({"UserIdGroupPairs": [{"GroupId": source}]} if source.startswith("sg-") else {"IpRanges": [{"CidrIp": source}]})}
        self.raw["security_groups"].append({"GroupId": key, "GroupName": name, "VpcId": vpc,
                                            "IpPermissions": [rule(port, source) for port, source in ingress],
                                            "IpPermissionsEgress": [rule(None, source) for source in egress]})

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

    def database(self, identifier, vpc, subnets, primary, engine="mysql", size="db.r6g.large", standby=None, replica=None):
        group = {"DBSubnetGroupName": f"{identifier}-subnets", "VpcId": vpc,
                 "Subnets": [{"SubnetIdentifier": subnet, "SubnetAvailabilityZone": {"Name": self.az_of(subnet)}} for subnet in subnets]}
        self.raw["db_instances"].append({
            "DBInstanceIdentifier": identifier, "DBInstanceArn": f"arn:aws:rds:{self.region}:{self.account}:db:{identifier}",
            "Engine": engine, "DBInstanceClass": size, "AvailabilityZone": f"{self.region}{primary}", "MultiAZ": bool(standby),
            "SecondaryAvailabilityZone": f"{self.region}{standby}" if standby else None, "PubliclyAccessible": False,
            "StorageEncrypted": True, "DBSubnetGroup": group, "ReadReplicaDBInstanceIdentifiers": [f"{identifier}-ro"] if replica else []})
        if replica:
            # Asynchronous read replica in a third AZ: read scaling, not failover.
            self.raw["db_instances"].append({
                "DBInstanceIdentifier": f"{identifier}-ro", "DBInstanceArn": f"arn:aws:rds:{self.region}:{self.account}:db:{identifier}-ro",
                "Engine": engine, "DBInstanceClass": size, "AvailabilityZone": f"{self.region}{replica}", "MultiAZ": False,
                "PubliclyAccessible": False, "StorageEncrypted": True, "DBSubnetGroup": group,
                "ReadReplicaSourceDBInstanceIdentifier": identifier})

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
                                          "ServiceName": f"com.amazonaws.{self.region}.{service}", "RouteTableIds": list(tables),
                                          "Tags": self.tags(f"{service}-gateway-endpoint")})
        for table in tables:
            self.route(table, f"pl-{service}", GatewayId=key)

    def interface_endpoint(self, key, vpc, service, subnets):
        self.raw["vpc_endpoints"].append({"VpcEndpointId": key, "VpcId": vpc, "VpcEndpointType": "Interface", "State": "available",
                                          "ServiceName": f"com.amazonaws.{self.region}.{service}", "SubnetIds": list(subnets),
                                          "PrivateDnsEnabled": True, "Tags": self.tags(f"{service}-endpoint")})

    def transit_gateway(self, key, name):
        self.raw["transit_gateways"].append({"TransitGatewayId": key, "State": "available", "Tags": self.tags(name)})
        return key

    def tgw_attachment(self, key, gateway, vpc, subnets, name):
        self.raw["transit_gateway_attachments"].append({"TransitGatewayAttachmentId": key, "TransitGatewayId": gateway, "VpcId": vpc,
                                                        "SubnetIds": list(subnets), "State": "available", "Tags": self.tags(name)})

    def aurora(self, cluster, vpc, subnets, writer, readers, engine="aurora-postgresql", size="db.r6g.large"):
        group = {"DBSubnetGroupName": f"{cluster}-subnets", "VpcId": vpc,
                 "Subnets": [{"SubnetIdentifier": subnet, "SubnetAvailabilityZone": {"Name": self.az_of(subnet)}} for subnet in subnets]}
        members = []
        for index, az in enumerate([writer, *readers]):
            identifier = f"{cluster}-{index + 1}"
            self.raw["db_instances"].append({"DBInstanceIdentifier": identifier, "DBClusterIdentifier": cluster, "Engine": engine,
                                             "DBInstanceClass": size, "AvailabilityZone": f"{self.region}{az}", "MultiAZ": False,
                                             "PubliclyAccessible": False, "StorageEncrypted": True, "DBSubnetGroup": group})
            members.append({"DBInstanceIdentifier": identifier, "IsClusterWriter": index == 0})
        self.raw["db_clusters"].append({"DBClusterIdentifier": cluster, "Engine": engine, "MultiAZ": bool(readers),
                                        "StorageEncrypted": True, "DBClusterMembers": members})

    def redis(self, group, vpc, subnets, primary, replicas, size="cache.r7g.large"):
        self.raw["cache_subnet_groups"].append({"CacheSubnetGroupName": f"{group}-subnets", "VpcId": vpc,
                                                "Subnets": [{"SubnetIdentifier": s, "SubnetAvailabilityZone": {"Name": self.az_of(s)}} for s in subnets]})
        members = []
        for index, az in enumerate([primary, *replicas]):
            node = f"{group}-00{index + 1}"
            self.raw["cache_clusters"].append({"CacheClusterId": node, "ReplicationGroupId": group, "Engine": "redis", "CacheNodeType": size,
                                               "PreferredAvailabilityZone": f"{self.region}{az}", "CacheSubnetGroupName": f"{group}-subnets"})
            members.append({"CacheClusterId": node, "CurrentRole": "primary" if index == 0 else "replica"})
        self.raw["replication_groups"].append({"ReplicationGroupId": group, "MultiAZ": "enabled", "AutomaticFailover": "enabled",
                                               "AtRestEncryptionEnabled": True, "TransitEncryptionEnabled": True,
                                               "NodeGroups": [{"NodeGroupId": "0001", "NodeGroupMembers": members}]})

    def kafka(self, name, subnets, brokers, size="kafka.m7g.large"):
        arn = f"arn:aws:kafka:{self.region}:{self.account}:cluster/{name}/1"
        self.raw["msk_nodes"] += [{"ClusterArn": arn, "NodeType": "BROKER", "InstanceType": size,
                                   "BrokerNodeInfo": {"BrokerId": i + 1, "ClientSubnet": subnets[i % len(subnets)]}} for i in range(brokers)]
        self.raw["msk_clusters"].append({
            "ClusterName": name, "ClusterArn": arn, "ClusterType": "PROVISIONED",
            "State": "ACTIVE", "Provisioned": {"NumberOfBrokerNodes": brokers, "CurrentBrokerSoftwareInfo": {"KafkaVersion": "3.7.x"},
                                               "BrokerNodeGroupInfo": {"InstanceType": size, "ClientSubnets": list(subnets)},
                                               "EncryptionInfo": {"EncryptionInTransit": {"ClientBroker": "TLS"}}}})

    def function(self, name, vpc, subnets, runtime="python3.12"):
        self.raw["lambda_functions"].append({"FunctionName": name, "Runtime": runtime,
                                             "VpcConfig": {"VpcId": vpc, "SubnetIds": list(subnets), "SecurityGroupIds": [f"sg-{name}"]}})

    def eks(self, name, vpc, subnets, nodegroups, version="1.31"):
        self.raw["eks_clusters"].append({"name": name, "version": version, "status": "ACTIVE",
                                         "resourcesVpcConfig": {"vpcId": vpc, "subnetIds": list(subnets), "endpointPublicAccess": False,
                                                                "endpointPrivateAccess": True},
                                         "nodegroups": [{"nodegroupName": group, "instanceTypes": [size], "scalingConfig": scaling,
                                                         "resources": {"autoScalingGroups": [{"name": asg}]}}
                                                        for group, asg, size, scaling in nodegroups]})

    def redis_cluster(self, group, vpc, subnets, shards, size="cache.r7g.large"):
        """Cluster mode enabled: one node group per shard, each with a primary and replicas in other AZs."""
        self.raw["cache_subnet_groups"].append({"CacheSubnetGroupName": f"{group}-subnets", "VpcId": vpc,
                                                "Subnets": [{"SubnetIdentifier": s, "SubnetAvailabilityZone": {"Name": self.az_of(s)}} for s in subnets]})
        node_groups = []
        for shard, (primary, replicas) in enumerate(shards, 1):
            members = []
            for index, az in enumerate([primary, *replicas]):
                node = f"{group}-000{shard}-00{index + 1}"
                self.raw["cache_clusters"].append({"CacheClusterId": node, "ReplicationGroupId": group, "Engine": "redis", "EngineVersion": "7.1",
                                                   "CacheNodeType": size, "PreferredAvailabilityZone": f"{self.region}{az}",
                                                   "CacheSubnetGroupName": f"{group}-subnets"})
                members.append({"CacheClusterId": node, "CurrentRole": "primary" if index == 0 else "replica"})
            node_groups.append({"NodeGroupId": f"000{shard}", "NodeGroupMembers": members})
        self.raw["replication_groups"].append({"ReplicationGroupId": group, "MultiAZ": "enabled", "AutomaticFailover": "enabled",
                                               "ClusterEnabled": True, "NodeGroups": node_groups})

    def opensearch(self, domain, vpc, subnets, data_per_az, masters_per_az=1, size="r6g.large.search", standby=None):
        """Three-AZ domain with dedicated masters; with standby, the standby AZ's data nodes take no traffic until failover."""
        arn = f"arn:aws:es:{self.region}:{self.account}:domain/{domain}"
        self.raw["opensearch_domains"].append({
            "DomainName": domain, "ARN": arn, "DomainId": f"{self.account}/{domain}", "EngineVersion": "OpenSearch_2.13",
            "ClusterConfig": {"InstanceType": size, "InstanceCount": data_per_az * len(subnets), "DedicatedMasterEnabled": bool(masters_per_az),
                              "DedicatedMasterType": "m6g.large.search", "DedicatedMasterCount": masters_per_az * len(subnets),
                              "ZoneAwarenessEnabled": True, "ZoneAwarenessConfig": {"AvailabilityZoneCount": len(subnets)},
                              "MultiAZWithStandbyEnabled": bool(standby)},
            "VPCOptions": {"VPCId": vpc, "SubnetIds": list(subnets), "AvailabilityZones": [self.az_of(x) for x in subnets]},
            "EncryptionAtRestOptions": {"Enabled": True}, "NodeToNodeEncryptionOptions": {"Enabled": True}})
        for subnet in subnets:
            zone = self.az_of(subnet)
            status = "StandBy" if standby and zone.endswith(standby) else "Active"
            self.raw["opensearch_nodes"] += [{"DomainName": domain, "NodeId": f"{domain}-{zone[-1]}-{kind[0]}{i}", "NodeType": kind,
                                              "AvailabilityZone": zone, "InstanceType": size if kind == "Data" else "m6g.large.search",
                                              "NodeStatus": status if kind == "Data" else "Active"}
                                             for kind, count in (("Data", data_per_az), ("Master", masters_per_az)) for i in range(count)]

    def ecs_service(self, cluster, name, vpc, tasks, target_group=None, port=8080, launch="FARGATE"):
        """Fargate service with awsvpc tasks; each task's ENI address is the IP target ECS registers."""
        cluster_arn = f"arn:aws:ecs:{self.region}:{self.account}:cluster/{cluster}"
        subnets = sorted({subnet for subnet, _ in tasks})
        self.raw["ecs_services"].append({
            "serviceArn": f"arn:aws:ecs:{self.region}:{self.account}:service/{cluster}/{name}", "serviceName": name, "clusterArn": cluster_arn,
            "status": "ACTIVE", "launchType": launch, "desiredCount": len(tasks), "runningCount": len(tasks), "platformVersion": "1.4.0",
            "networkConfiguration": {"awsvpcConfiguration": {"subnets": subnets, "securityGroups": [f"sg-{name}"], "assignPublicIp": "DISABLED"}},
            "loadBalancers": [{"targetGroupArn": target_group, "containerName": name, "containerPort": port}] if target_group else [],
            "deploymentConfiguration": {"deploymentCircuitBreaker": {"enable": True, "rollback": True}},
            "availabilityZoneRebalancing": "ENABLED"})
        for index, (subnet, ip) in enumerate(tasks):
            task_id = hashlib.sha256(f"{cluster}/{name}/{index}".encode()).hexdigest()[:32]
            self.raw["ecs_tasks"].append({
                "taskArn": f"arn:aws:ecs:{self.region}:{self.account}:task/{cluster}/{task_id}", "clusterArn": cluster_arn, "group": f"service:{name}",
                "launchType": launch, "lastStatus": "RUNNING", "availabilityZone": self.az_of(subnet), "cpu": "512", "memory": "1024",
                "attachments": [{"type": "ElasticNetworkInterface", "status": "ATTACHED",
                                 "details": [{"name": "subnetId", "value": subnet}, {"name": "privateIPv4Address", "value": ip}]}]})

    def efs(self, key, name, vpc, subnets, one_zone=None):
        self.raw["efs_file_systems"].append({"FileSystemId": key, "Name": name, "PerformanceMode": "generalPurpose", "ThroughputMode": "elastic",
                                             "Encrypted": True, "LifeCycleState": "available", "NumberOfMountTargets": len(subnets),
                                             **({"AvailabilityZoneName": f"{self.region}{one_zone}"} if one_zone else {}), "Tags": self.tags(name)})
        for i, subnet in enumerate(subnets):
            self.raw["efs_mount_targets"].append({"MountTargetId": f"fsmt-{key[3:]}{i}", "FileSystemId": key, "SubnetId": subnet, "VpcId": vpc,
                                                  "AvailabilityZoneName": self.az_of(subnet), "LifeCycleState": "available",
                                                  "IpAddress": self.address(subnet, 200)})

    def redshift(self, cluster, vpc, subnets, primary, secondary=None, node_type="ra3.xlplus", nodes=2):
        self.raw["redshift_subnet_groups"].append({"ClusterSubnetGroupName": f"{cluster}-subnets", "VpcId": vpc,
                                                   "Subnets": [{"SubnetIdentifier": s, "SubnetAvailabilityZone": {"Name": self.az_of(s)}} for s in subnets]})
        roles = lambda: [{"NodeRole": "LEADER" if nodes > 1 and i == 0 else "COMPUTE-" + str(i)} for i in range(nodes)]
        self.raw["redshift_clusters"].append({
            "ClusterIdentifier": cluster, "NodeType": node_type, "NumberOfNodes": nodes, "ClusterStatus": "available", "VpcId": vpc,
            "ClusterSubnetGroupName": f"{cluster}-subnets", "AvailabilityZone": f"{self.region}{primary}", "Encrypted": True,
            "PubliclyAccessible": False, "ClusterNodes": roles(), "MultiAZ": "Enabled" if secondary else "Disabled",
            **({"MultiAZSecondary": {"AvailabilityZone": f"{self.region}{secondary}", "ClusterNodes": roles()}} if secondary else {})})

    def peering(self, key, requester, accepter, name):
        cidr = lambda vpc: next(v["CidrBlock"] for v in self.raw["vpcs"] if v["VpcId"] == vpc)
        self.raw["vpc_peering_connections"].append({
            "VpcPeeringConnectionId": key, "Status": {"Code": "active"}, "Tags": self.tags(name),
            "RequesterVpcInfo": {"VpcId": requester, "OwnerId": self.account, "Region": self.region, "CidrBlock": cidr(requester)},
            "AccepterVpcInfo": {"VpcId": accepter, "OwnerId": self.account, "Region": self.region, "CidrBlock": cidr(accepter)}})

    def vpn(self, key, customer, ip, name, vgw=None, tgw=None, vpc=None, tunnels_up=2, vgw_name=None):
        """Site-to-Site VPN with two tunnels, terminated on a virtual private gateway or a Transit Gateway."""
        if vgw and not any(g["VpnGatewayId"] == vgw for g in self.raw["vpn_gateways"]):
            self.raw["vpn_gateways"].append({"VpnGatewayId": vgw, "State": "available", "Type": "ipsec.1", "AmazonSideAsn": 64512,
                                             "VpcAttachments": [{"VpcId": vpc, "State": "attached"}], "Tags": self.tags(vgw_name or vgw)})
        if not any(c["CustomerGatewayId"] == customer for c in self.raw["customer_gateways"]):
            self.raw["customer_gateways"].append({"CustomerGatewayId": customer, "IpAddress": ip, "BgpAsn": "65010", "State": "available",
                                                  "Type": "ipsec.1", "Tags": self.tags(name)})
        self.raw["vpn_connections"].append({
            "VpnConnectionId": key, "CustomerGatewayId": customer, "State": "available", "Type": "ipsec.1",
            **({"VpnGatewayId": vgw} if vgw else {"TransitGatewayId": tgw}), "Options": {"StaticRoutesOnly": False},
            "VgwTelemetry": [{"OutsideIpAddress": f"52.78.0.{10 + i}", "Status": "UP" if i < tunnels_up else "DOWN"} for i in range(2)]})

    def placement_group(self, name, strategy="cluster"):
        self.raw["placement_groups"].append({"GroupName": name, "GroupId": "pg-" + hashlib.sha256(name.encode()).hexdigest()[:17],
                                             "Strategy": strategy, "State": "available"})
        return name

    def fsx_lustre(self, key, name, vpc, subnet, deployment="PERSISTENT_2", capacity=4800, throughput=250):
        """FSx for Lustre in one subnet (same AZ as the compute reading it)."""
        self.raw["fsx_file_systems"].append({
            "FileSystemId": key, "FileSystemType": "LUSTRE", "Lifecycle": "AVAILABLE", "StorageCapacity": capacity, "StorageType": "SSD",
            "VpcId": vpc, "SubnetIds": [subnet], "KmsKeyId": f"arn:aws:kms:{self.region}:{self.account}:key/fsx",
            "LustreConfiguration": {"DeploymentType": deployment, "PerUnitStorageThroughput": throughput,
                                    "DataCompressionType": "LZ4"}, "Tags": self.tags(name)})
        return key

    def sagemaker_endpoint(self, name, instance_type, count, subnets=(), sg=None):
        """Real-time endpoint; a model VpcConfig places its instances' ENIs in the given subnets."""
        arn = f"arn:aws:sagemaker:{self.region}:{self.account}"
        self.raw["sagemaker_endpoints"].append({
            "EndpointName": name, "EndpointArn": f"{arn}:endpoint/{name}", "EndpointStatus": "InService", "EndpointConfigName": f"{name}-config",
            "ProductionVariants": [{"VariantName": "main", "CurrentInstanceCount": count, "DesiredInstanceCount": count}],
            "EndpointConfig": {"EndpointConfigName": f"{name}-config", "ProductionVariants": [
                {"VariantName": "main", "ModelName": f"{name}-model", "InstanceType": instance_type, "InitialInstanceCount": count}]},
            "Models": [{"ModelName": f"{name}-model", **({"VpcConfig": {"Subnets": list(subnets), "SecurityGroupIds": [sg] if sg else []}}
                                                         if subnets else {})}]})

    def hyperpod(self, name, subnets, groups, eks=None):
        """SageMaker HyperPod cluster: instance groups as (name, instance type, count)."""
        arn = f"arn:aws:sagemaker:{self.region}:{self.account}"
        self.raw["sagemaker_clusters"].append({
            "ClusterName": name, "ClusterArn": f"{arn}:cluster/{name}", "ClusterStatus": "InService",
            "InstanceGroups": [{"InstanceGroupName": group, "InstanceType": kind, "CurrentCount": count, "TargetCount": count}
                               for group, kind, count in groups],
            "VpcConfig": {"Subnets": list(subnets), "SecurityGroupIds": []},
            **({"Orchestrator": {"Eks": {"ClusterArn": f"arn:aws:eks:{self.region}:{self.account}:cluster/{eks}"}}} if eks else {})})

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
        # DescribeSubnets.AvailableIpAddressCount: the CIDR minus the five reserved addresses and the instances in it.
        for subnet in self.raw["subnets"]:
            used = sum(i["SubnetId"] == subnet["SubnetId"] for i in self.raw["instances"])
            subnet.setdefault("AvailableIpAddressCount", ipaddress.ip_network(subnet["CidrBlock"]).num_addresses - 5 - used)
        coverage = [{"service": service, "operation": operation, "status": "complete", "pages": 1, "items": len(self.raw[key])}
                    for service, operation, key in (("ec2", "describe_vpcs", "vpcs"), ("ec2", "describe_subnets", "subnets"),
                                                    ("ec2", "describe_instances", "instances"),
                                                    ("elbv2", "describe_load_balancers", "load_balancers"),
                                                    ("elbv2", "describe_target_groups", "target_groups"),
                                                    ("ec2", "describe_internet_gateways", "internet_gateways"),
                                                    ("ec2", "describe_nat_gateways", "nat_gateways"),
                                                    ("ec2", "describe_route_tables", "route_tables"),
                                                    ("rds", "describe_db_instances", "db_instances"),
                                                    ("autoscaling", "describe_auto_scaling_groups", "auto_scaling_groups"),
                                                    ("ec2", "describe_vpc_endpoints", "vpc_endpoints"),
                                                    ("ec2", "describe_transit_gateway_vpc_attachments", "transit_gateway_attachments"),
                                                    ("rds", "describe_db_clusters", "db_clusters"),
                                                    ("elasticache", "describe_cache_clusters", "cache_clusters"),
                                                    ("kafka", "list_clusters_v2", "msk_clusters"),
                                                    ("lambda", "list_functions", "lambda_functions"),
                                                    ("eks", "list_clusters", "eks_clusters"),
                                                    ("kafka", "list_nodes", "msk_nodes"),
                                                    ("opensearch", "describe_domains", "opensearch_domains"),
                                                    ("opensearch", "describe_domain_nodes", "opensearch_nodes"),
                                                    ("ecs", "describe_services", "ecs_services"), ("ecs", "describe_tasks", "ecs_tasks"),
                                                    ("efs", "describe_file_systems", "efs_file_systems"),
                                                    ("efs", "describe_mount_targets", "efs_mount_targets"),
                                                    ("redshift", "describe_clusters", "redshift_clusters"),
                                                    ("ec2", "describe_vpc_peering_connections", "vpc_peering_connections"),
                                                    ("ec2", "describe_vpn_gateways", "vpn_gateways"),
                                                    ("ec2", "describe_vpn_connections", "vpn_connections"),
                                                    ("ec2", "describe_security_groups", "security_groups"),
                                                    ("ec2", "describe_placement_groups", "placement_groups"),
                                                    ("fsx", "describe_file_systems", "fsx_file_systems"),
                                                    ("sagemaker", "list_endpoints", "sagemaker_endpoints"),
                                                    ("sagemaker", "list_clusters", "sagemaker_clusters"))]
        coverage += [{"service": "elbv2", "operation": "describe_target_health", "parameters": {"TargetGroupArn": h["TargetGroupArn"]},
                      "status": "complete", "pages": 1, "items": len(h["Descriptions"])} for h in self.raw["target_health"]]
        return {"schema_version": 1, "metadata": {
            "account_id": self.account, "region": self.region, "partition": "aws", "started_at": timestamp, "finished_at": timestamp,
            "status": "complete", "demo": True, "sample": self.key, "sample_version": SAMPLE_VERSION, "title": self.title, "practices": self.practices,
            "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget", "InternetGateway", "NatGateway",
                      "RouteTable", "RDS", "AutoScalingGroup", "VpcEndpoint", "TransitGateway", "EKS", "MSK", "ElastiCache", "Lambda",
                      "OpenSearch", "ECS", "EFS", "Redshift", "VpcPeering", "VPN"],
            "limitations": ["AWS best practice 패턴을 따른 합성 샘플이며 실제 계정 데이터가 아닙니다.",
                            "리스너 규칙·Direct Connect·CloudFront·WAF·Route 53은 수집 범위 밖이라 그리지 않습니다 (권고는 practices에 문구로).",
                            "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."]},
            "coverage": coverage, "raw": self.raw}


def three_tier():
    s = Sample("three-tier", "3계층 웹 애플리케이션 (3 AZ)", [
        "세 AZ에 같은 구성을 배치해 AZ 장애를 견딤 (Reliability)",
        "인터넷용 ALB만 퍼블릭 서브넷, 웹·앱·데이터는 프라이빗 서브넷 (Security)",
        "웹 계층과 앱 계층 사이에 내부 ALB를 두어 계층별 확장·격리",
        "AZ마다 NAT 게이트웨이를 두어 프라이빗 서브넷의 외부 통신이 AZ 장애에 묶이지 않음",
        "웹·앱 계층은 Auto Scaling 그룹(ELB 헬스 체크)으로 AZ에 분산",
        "데이터 계층은 인터넷 경로 없는 격리 서브넷의 RDS Multi-AZ (암호화, 비공개), 읽기는 세 번째 AZ의 읽기 전용 복제본",
        "S3는 게이트웨이 엔드포인트로 NAT를 거치지 않고, 관리 접근은 SSM·SSM Messages·EC2 Messages 엔드포인트의 Session Manager(배스천 없음)",
        "도형 밖 권고: CloudFront(정적 콘텐츠는 S3)·AWS WAF·Route 53 헬스 체크를 진입점 앞에 (REL02-BP01, 수집 범위 밖)"])
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
    s.database("prod-orders-db", vpc, data, "a", "mysql", "db.r6g.large", standby="b", replica="c")
    # Each tier admits only the tier in front of it; only the public ALB is open to the internet.
    s.security_group("sg-public-alb", vpc, "prod-public-alb", [(443, "0.0.0.0/0")])
    s.security_group("sg-web", vpc, "prod-web", [(443, "sg-public-alb")])
    s.security_group("sg-internal-alb", vpc, "prod-internal-alb", [(8080, "sg-web")])
    s.security_group("sg-app", vpc, "prod-app", [(8080, "sg-internal-alb")])
    s.security_group("sg-prod-db", vpc, "prod-db", [(3306, "sg-app")])
    s.gateway_endpoint("vpce-prod-s3", vpc, "s3", [f"rtb-prod-private-{az}" for az in "abc"])
    # Session Manager needs ssm, ssmmessages and ec2messages to reach instances without a NAT path.
    for service in ("ssm", "ssmmessages", "ec2messages"):
        s.interface_endpoint(f"vpce-prod-{service}", vpc, service, app)
    return s.snapshot()


def microservices():
    s = Sample("microservices", "마이크로서비스 경로 기반 라우팅 (ALB 1 · 대상 그룹 4)", [
        "ALB 하나에서 경로별로 서비스 대상 그룹을 나눔 (리스너 규칙은 수집 범위 밖)",
        "ECS Fargate 서비스(awsvpc)는 태스크 IP를 대상으로 등록, 배포 회로 차단기로 자동 롤백",
        "서비스마다 전용 포트·헬스 체크 경로",
        "두 AZ 이상에 각 서비스 대상을 분산하고 서비스별 Auto Scaling 그룹",
        "서비스 공용 데이터는 격리 서브넷의 RDS Multi-AZ",
        "이미지 풀은 ECR API·Docker 인터페이스와 S3 게이트웨이 엔드포인트, 컨테이너 로그는 CloudWatch Logs 엔드포인트로 사설 경로 사용",
        "도형 밖 권고: 인터넷용 ALB 앞에 AWS WAF·CloudFront (REL02-BP01, 수집 범위 밖)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60002", "10.20.0.0/16", "commerce-services", Environment="production")
    public = [s.subnet(f"subnet-ms-pub-{az}", vpc, az, f"10.20.{i}.0/24", f"public-{az}", "public") for i, az in enumerate("ab")]
    private = [s.subnet(f"subnet-ms-app-{az}", vpc, az, f"10.20.{8 + 4 * i}.0/22", f"services-{az}", "app") for i, az in enumerate("ab")]
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
    # Fargate image pulls need both ECR endpoints (plus S3); awslogs needs the logs endpoint.
    for service in ("ecr.api", "ecr.dkr", "logs"):
        s.interface_endpoint(f"vpce-commerce-{service.replace('.', '-')}", vpc, service, private)
    for name, port, offset in (("cart-svc", 9000, 41), ("payments-svc", 9100, 88)):
        tasks = [(subnet, s.address(subnet, offset + i)) for i, subnet in enumerate(private)]
        group = s.target_group(f"{name}-ecs", vpc, "HTTP", port, [(ip, port, s.az_of(subnet)) for subnet, ip in tasks],
                               [alb], target_type="ip", health=f"/{name.split('-')[0]}/health")
        s.ecs_service("commerce", name, vpc, tasks, group, port)
    return s.snapshot()


def static_ip_front():
    s = Sample("nlb-alb", "고정 IP가 필요한 진입점 (NLB → ALB)", [
        "고정 IP·PrivateLink가 필요한 진입점은 NLB, L7 라우팅은 뒤의 ALB (alb 대상 유형)",
        "TCP 서비스는 NLB 대상 그룹으로 직접 연결",
        "두 AZ에 대상을 분산하고 교차 영역 구성",
        "관리 접근은 배스천 대신 Session Manager 사용 (인스턴스 퍼블릭 IP 없음)",
        "도형 밖 권고: NLB 고정 IP는 AZ마다 Elastic IP, ALB에는 AWS WAF, 대안으로 Global Accelerator (수집 범위 밖)"])
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
        "Environment 태그로 소유·비용 구분",
        "스테이징 NAT 1개는 비운영 환경의 비용 절충 (REL10-BP01은 운영 워크로드 기준)",
        "도형 밖 권고: 환경별 AWS 계정 분리, Network 계정의 TGW를 RAM으로 공유, 운영·스테이징 TGW 라우팅 테이블 분리 (수집 범위 밖)"])
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
        "온프레미스 대역(192.168.0.0/16)은 Transit Gateway의 Site-to-Site VPN 2개로 연결: 고객 게이트웨이(장비·ISP) 2개, 각각 터널 2개, BGP (REL02-BP02)",
        "도형 밖 권고: 운영 주 연결은 Direct Connect(다중 위치), VPN은 백업. 하이브리드 DNS는 Route 53 Resolver 엔드포인트 (수집 범위 밖)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60005", "10.50.0.0/16", "migration-hub", Environment="production")
    app = [s.subnet(f"subnet-hy-app-{az}", vpc, az, f"10.50.{10 + i}.0/24", f"app-{az}", "app") for i, az in enumerate("ab")]
    cloud = [s.instance(f"i-erp{az}0000000000005", app[i], f"erp-cloud-{az}", "r6i.large", sg="sg-erp") for i, az in enumerate("ab")]
    # Internal only: no internet gateway; the main route table has just the local route
    # (the on-premises path over VPN/Direct Connect is outside the collected scope).
    s.route_table("rtb-hybrid-main", vpc, "hybrid-isolated", main=True)
    hub = s.transit_gateway("tgw-hybrid00000005", "hybrid-tgw")
    s.route("rtb-hybrid-main", "192.168.0.0/16", TransitGatewayId=hub)
    s.tgw_attachment("tgw-attach-hybrid", hub, vpc, app, "migration-attachment")
    # Redundant on-premises side: two customer gateways (separate devices and ISPs), each with a two-tunnel VPN.
    s.vpn("vpn-hybrid-seoul", "cgw-seoul-dc", "203.0.113.10", "seoul-dc-cgw", tgw=hub)
    s.vpn("vpn-hybrid-seoul-2", "cgw-seoul-dc-2", "198.51.100.10", "seoul-dc-cgw-2", tgw=hub)
    alb = s.load_balancer("erp-internal-alb", vpc, app, scheme="internal", sg="sg-erp-alb")
    erp = s.target_group("erp-cloud", vpc, "HTTPS", 443, cloud, [alb])
    s.auto_scaling_group("erp-cloud-asg", cloud, app, [erp])
    s.target_group("erp-onprem", vpc, "HTTPS", 443, [("192.168.20.11", 443, "all"), ("192.168.20.12", 443, "all")],
                   [alb], target_type="ip")
    return s.snapshot()


def eks_platform():
    s = Sample("eks-platform", "EKS 플랫폼 (EKS · MSK · ElastiCache · Aurora · Lambda)", [
        "EKS 컨트롤 플레인 엔드포인트는 비공개, 관리형 노드 그룹은 세 AZ 프라이빗 서브넷에 분산",
        "인그레스는 AWS Load Balancer Controller의 ALB → IP 대상(파드)",
        "Aurora PostgreSQL writer/reader를 서로 다른 AZ에, ElastiCache Redis는 primary/replica 자동 장애 조치",
        "MSK 브로커는 AZ마다 하나, 클라이언트-브로커 TLS",
        "데이터 계층(Aurora·Redis·MSK)은 인터넷 경로 없는 격리 서브넷",
        "ECR API·Docker·STS 인터페이스와 S3 게이트웨이 엔드포인트로 이미지·자격 증명·객체 접근을 사설 경로로",
        "도형 밖 권고: 인터넷 ALB에 AWS WAF, MSK 토픽 복제 계수 3 (수집 범위 밖)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60006", "10.60.0.0/16", "platform", Environment="production")
    azs = "abc"
    public = [s.subnet(f"subnet-pf-pub-{az}", vpc, az, f"10.60.{i}.0/24", f"public-{az}", "public") for i, az in enumerate(azs)]
    nodes = [s.subnet(f"subnet-pf-app-{az}", vpc, az, f"10.60.{16 * (i + 1)}.0/20", f"eks-nodes-{az}", "app") for i, az in enumerate(azs)]
    data = [s.subnet(f"subnet-pf-data-{az}", vpc, az, f"10.60.{100 + i}.0/24", f"data-{az}", "data") for i, az in enumerate(azs)]
    general = [s.instance(f"i-eksgen{az}00000006", nodes[i], f"general-node-{az}", "m6i.xlarge", sg="sg-eks-nodes",
                          **{"eks:cluster-name": "platform-eks", "eks:nodegroup-name": "general"}) for i, az in enumerate(azs)]
    batch = [s.instance(f"i-eksbat{az}00000006", nodes[i], f"batch-node-{az}", "c6i.2xlarge", sg="sg-eks-nodes",
                        **{"eks:cluster-name": "platform-eks", "eks:nodegroup-name": "batch"}) for i, az in enumerate(azs)]
    s.network(vpc, "platform", azs, public, [nodes])
    s.auto_scaling_group("eks-general-6a1b", general, nodes, minimum=3, maximum=12)
    s.auto_scaling_group("eks-batch-7c2d", batch, nodes, minimum=0, maximum=30)
    s.eks("platform-eks", vpc, nodes, [("general", "eks-general-6a1b", "m6i.xlarge", {"minSize": 3, "maxSize": 12, "desiredSize": 3}),
                                       ("batch", "eks-batch-7c2d", "c6i.2xlarge", {"minSize": 0, "maxSize": 30, "desiredSize": 3})])
    alb = s.load_balancer("k8s-platform-ingress", vpc, public, sg="sg-ingress")
    s.target_group("k8s-web-pods", vpc, "HTTP", 8080,
                   [("10.60.17.21", 8080, f"{REGION}a"), ("10.60.33.42", 8080, f"{REGION}b"), ("10.60.49.13", 8080, f"{REGION}c")],
                   [alb], target_type="ip")
    s.aurora("platform-aurora", vpc, data, "a", ["b"])
    s.redis("platform-redis", vpc, data, "b", ["c"])
    s.kafka("platform-events", data, 3)
    s.function("order-events-consumer", vpc, nodes)
    s.gateway_endpoint("vpce-platform-s3", vpc, "s3", [f"rtb-platform-private-{az}" for az in azs])
    s.interface_endpoint("vpce-platform-ecr-api", vpc, "ecr.api", nodes)
    s.interface_endpoint("vpce-platform-ecr", vpc, "ecr.dkr", nodes)
    s.interface_endpoint("vpce-platform-sts", vpc, "sts", nodes)
    return s.snapshot()


def data_platform():
    s = Sample("data-platform", "데이터 플랫폼 (ECS · OpenSearch · RDS · ElastiCache · EFS · Redshift · DocumentDB)", [
        "ECS Fargate 서비스는 세 AZ 프라이빗 서브넷에 태스크를 분산하고 ALB가 태스크 IP를 대상으로 등록",
        "OpenSearch는 3 AZ · 전용 마스터 노드 · Multi-AZ with Standby (한 AZ의 데이터 노드는 대기)",
        "RDS PostgreSQL Multi-AZ(동기식 대기) + 세 번째 AZ의 읽기 전용 복제본",
        "ElastiCache Redis 클러스터 모드: 샤드마다 primary와 replica를 서로 다른 AZ에, 자동 장애 조치",
        "EFS(리전 클래스)는 AZ마다 마운트 타깃을 두어 AZ 장애에도 같은 AZ 경로로 접근",
        "분석 VPC는 Redshift Multi-AZ(서브넷 3개, AZ당 2노드)와 DocumentDB writer/reader",
        "두 VPC와 사내 데이터 센터를 Transit Gateway 하나로 연결 (피어링은 비전이적이라 다른 VPC의 VPN을 쓸 수 없음)",
        "사내 데이터 센터는 고객 게이트웨이 2개(장비·ISP 분리)의 Site-to-Site VPN, 각각 터널 2개 (REL02-BP02)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60007", "10.70.0.0/16", "data-platform", Environment="production")
    azs = "abc"
    public = [s.subnet(f"subnet-dp-pub-{az}", vpc, az, f"10.70.{i}.0/24", f"public-{az}", "public") for i, az in enumerate(azs)]
    app = [s.subnet(f"subnet-dp-app-{az}", vpc, az, f"10.70.{16 * (i + 1)}.0/20", f"services-{az}", "app") for i, az in enumerate(azs)]
    data = [s.subnet(f"subnet-dp-data-{az}", vpc, az, f"10.70.{100 + i}.0/24", f"data-{az}", "data") for i, az in enumerate(azs)]
    s.network(vpc, "dp", azs, public, [app])
    alb = s.load_balancer("data-api-alb", vpc, public, sg="sg-data-api-alb")
    tasks = [(subnet, s.address(subnet, 21 + i)) for i, subnet in enumerate(app)]
    group = s.target_group("data-api-tasks", vpc, "HTTP", 8080, [(ip, 8080, s.az_of(subnet)) for subnet, ip in tasks], [alb],
                           target_type="ip", health="/healthz")
    s.ecs_service("data-apps", "data-api", vpc, tasks, group)
    s.ecs_service("data-apps", "ingest-worker", vpc, [(subnet, s.address(subnet, 60 + i)) for i, subnet in enumerate(app[:2])])
    s.efs("fs-0dp0000000000007", "shared-assets", vpc, app)
    s.opensearch("logs-search", vpc, data, 2, 1, standby="c")
    s.database("catalog-db", vpc, data, "a", "postgres", "db.r6g.xlarge", standby="b", replica="c")
    s.redis_cluster("session-cache", vpc, data, [("a", ["b"]), ("b", ["c"])])
    s.gateway_endpoint("vpce-dp-s3", vpc, "s3", [f"rtb-dp-private-{az}" for az in azs])
    s.interface_endpoint("vpce-dp-ecr", vpc, "ecr.dkr", app)
    analytics = s.vpc("vpc-0a1b2c3d4e5f60008", "10.71.0.0/16", "analytics", Environment="production")
    # Redshift Multi-AZ requires a subnet group with three or more subnets.
    warehouse = [s.subnet(f"subnet-an-data-{az}", analytics, az, f"10.71.{10 + i}.0/24", f"warehouse-{az}", "data") for i, az in enumerate("abc")]
    s.route_table("rtb-an-main", analytics, "analytics-isolated", main=True)
    # One Transit Gateway joins both VPCs and terminates the VPN. Peering is not transitive: the data platform
    # VPC could not reach on premises through a VPN attached to the analytics VPC.
    hub = s.transit_gateway("tgw-dp0000000000007", "data-tgw")
    s.tgw_attachment("tgw-attach-dp", hub, vpc, app, "data-platform-attachment")
    s.tgw_attachment("tgw-attach-an", hub, analytics, warehouse, "analytics-attachment")
    for cidr in ("10.71.0.0/16", "192.168.0.0/16"):
        for az in azs:
            s.route(f"rtb-dp-private-{az}", cidr, TransitGatewayId=hub)
    for cidr in ("10.70.0.0/16", "192.168.0.0/16"):
        s.route("rtb-an-main", cidr, TransitGatewayId=hub)
    # Two customer gateways on separate ISPs, each with its own two-tunnel VPN over BGP (ECMP on the TGW).
    s.vpn("vpn-dp-corp-1", "cgw-corp-dc-1", "203.0.113.20", "corp-dc-cgw-1", tgw=hub)
    s.vpn("vpn-dp-corp-2", "cgw-corp-dc-2", "198.51.100.20", "corp-dc-cgw-2", tgw=hub)
    s.redshift("warehouse", analytics, warehouse, "a", "b", "ra3.4xlarge", 4)  # Multi-AZ: two or more nodes per AZ
    s.aurora("profiles-docdb", analytics, warehouse, "a", ["b"], engine="docdb", size="db.r6g.large")
    return s.snapshot()


def ai_platform():
    s = Sample("ai-platform", "AI 플랫폼 (사설 EKS GPU 추론·학습 · Bedrock · Aurora pgvector)", [
        "인터넷 경로 없는 사설 VPC: IGW·NAT 없이 Transit Gateway로 사내망·중앙 egress에 연결 (MLSEC03-BP02)",
        "Bedrock·ECR·EKS·STS·CloudWatch Logs 등은 세 AZ의 인터페이스 엔드포인트, S3는 게이트웨이 엔드포인트로 사설 호출 (GENSEC01-BP02)",
        "GPU 추론 노드 그룹(g6e)은 세 AZ에 분산하고 내부 ALB가 추론 파드 IP를 대상으로 등록, 자동 확장 (GENREL02-BP01, MLREL05-BP01)",
        "EFA 학습 노드 그룹(p5)은 의도적으로 한 AZ의 전용 /20 서브넷에, 자기 참조 전체 허용 보안 그룹 (EFA는 AZ를 넘지 못함, HPCREL01-BP02)",
        "학습과 추론은 노드 그룹·인스턴스 유형을 분리 (MLPERF04-BP01), 학습 노드 그룹은 0까지 축소 (GENSUS01-BP01)",
        "RAG 벡터 저장소는 VPC 안 격리 서브넷의 Aurora PostgreSQL(pgvector) writer/reader, 노드 보안 그룹만 허용 (GENSEC01-BP03)",
        "모델 캐시는 세 AZ 마운트 타깃의 EFS (EKS AI/ML Storage)",
        "도형 밖 권고: 학습 배치는 클러스터 배치 그룹 또는 Capacity Block(둘 중 하나), 체크포인트는 같은 AZ의 S3 연동 FSx for Lustre (GENREL06-BP01)",
        "도형 밖 권고: Bedrock 모델 호출 로깅(기본 꺼짐)·Guardrails·InvokeModel만 허용하는 엔드포인트 정책, S3는 SSE-KMS와 aws:SourceVpce (수집 범위 밖)",
        "도형 밖 권고: Karpenter·KEDA·NVIDIA/EFA device plugin은 Kubernetes 객체라 그리지 않음 (Karpenter 노드는 ASG 없는 EC2로 표시)"])
    vpc = s.vpc("vpc-0a1b2c3d4e5f60009", "10.80.0.0/16", "ai-platform", Environment="production")
    azs = "abc"
    app = [s.subnet(f"subnet-ai-app-{az}", vpc, az, f"10.80.{32 * i}.0/19", f"app-{az}", "app") for i, az in enumerate(azs)]
    # GPU nodes use many IPs (a P5 takes 81 on EKS) and a subnet cannot grow later: a dedicated /20 for training.
    train = s.subnet("subnet-ai-train-a", vpc, "a", "10.80.96.0/20", "train-a", "app")
    data = [s.subnet(f"subnet-ai-data-{az}", vpc, az, f"10.80.{200 + i}.0/24", f"data-{az}", "data") for i, az in enumerate(azs)]
    hub = s.transit_gateway("tgw-ai00000000000009", "corp-tgw")
    s.tgw_attachment("tgw-attach-ai", hub, vpc, app, "ai-platform-attachment")
    # No internet gateway or NAT: the default route goes to the Transit Gateway (central egress); data stays local only.
    s.route_table("rtb-ai-app", vpc, "ai-app", [*app, train])
    s.route("rtb-ai-app", "0.0.0.0/0", TransitGatewayId=hub)
    s.route_table("rtb-ai-data", vpc, "ai-data-isolated", data, main=True)
    s.gateway_endpoint("vpce-ai-s3", vpc, "s3", ["rtb-ai-app", "rtb-ai-data"])
    for service in ("bedrock-runtime", "bedrock-agent-runtime", "ecr.api", "ecr.dkr", "sts", "eks", "eks-auth", "ec2",
                    "elasticloadbalancing", "logs", "ssm"):
        s.interface_endpoint(f"vpce-ai-{service.replace('.', '-')}", vpc, service, app)
    s.security_group("sg-ai-nodes", vpc, "ai-nodes", [(443, "10.80.0.0/16"), (8080, "sg-ai-alb")])
    s.security_group("sg-ai-alb", vpc, "ai-inference-alb", [(443, "10.0.0.0/8")])
    # EFA: every training node shares one group that allows all traffic from itself; no 0.0.0.0/0 egress.
    s.security_group("sg-train-efa", vpc, "train-efa", [(None, "self"), (443, "10.80.0.0/16")], egress=("self", "10.80.0.0/16"))
    s.security_group("sg-ai-db", vpc, "ai-vectors", [(5432, "sg-ai-nodes")])
    eks_tags = lambda group: {"eks:cluster-name": "ai-eks", "eks:nodegroup-name": group}
    system = [s.instance(f"i-aisys{az}000000009", app[i], f"system-node-{az}", "m7g.large", sg="sg-ai-nodes", **eks_tags("system"))
              for i, az in enumerate(azs)]
    infer = [s.instance(f"i-aiinf{az}000000009", app[i], f"gpu-inference-{az}", "g6e.2xlarge", sg="sg-ai-nodes", **eks_tags("gpu-inference"))
             for i, az in enumerate(azs)]
    trainers = [s.instance(f"i-aitrn{n}0000000009", train, f"gpu-train-{n}", "p5.48xlarge", sg="sg-train-efa", **eks_tags("gpu-train"))
                for n in (1, 2)]
    s.auto_scaling_group("eks-ai-system-1a2b", system, app, minimum=3, maximum=6)
    s.auto_scaling_group("eks-ai-gpu-inference-3c4d", infer, app, minimum=3, maximum=12)
    s.auto_scaling_group("eks-ai-gpu-train-5e6f", trainers, [train], minimum=0, maximum=8)
    s.eks("ai-eks", vpc, app, [("system", "eks-ai-system-1a2b", "m7g.large", {"minSize": 3, "maxSize": 6, "desiredSize": 3}),
                               ("gpu-inference", "eks-ai-gpu-inference-3c4d", "g6e.2xlarge", {"minSize": 3, "maxSize": 12, "desiredSize": 3}),
                               ("gpu-train", "eks-ai-gpu-train-5e6f", "p5.48xlarge", {"minSize": 0, "maxSize": 8, "desiredSize": 2})])
    alb = s.load_balancer("ai-inference-alb", vpc, app, scheme="internal", sg="sg-ai-alb")
    s.target_group("inference-pods", vpc, "HTTP", 8080, [(s.address(subnet, 40), 8080, s.az_of(subnet)) for subnet in app], [alb],
                   target_type="ip", health="/healthz")
    s.aurora("ai-vectors", vpc, data, "a", ["b"])
    s.efs("fs-0ai0000000000009", "ai-model-cache", vpc, app)
    return s.snapshot()


SAMPLES = {"three-tier": three_tier, "microservices": microservices, "nlb-alb": static_ip_front,
           "environments": environments, "hybrid": hybrid, "eks-platform": eks_platform, "data-platform": data_platform,
           "ai-platform": ai_platform}
