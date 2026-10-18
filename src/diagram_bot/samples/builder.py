"""The Sample builder: API-shaped raw collections, written like the collector would read them, and their snapshot."""
import copy
import hashlib
import ipaddress
from itertools import islice

from ..collector import OPERATIONS, now
from ..snapshot import make_snapshot

ACCOUNT, REGION = "111122223333", "ap-northeast-2"
# Bumped whenever a sample's architecture changes; the server replaces stored samples of another version.
SAMPLE_VERSION = 7
# Collector listings whose raw collection is replaced by the detail calls behind them (collector phase 2).
LISTINGS = {"eks_cluster_names": "eks_clusters", "opensearch_domain_names": "opensearch_domains", "ecs_cluster_arns": "ecs_services",
            "sagemaker_endpoint_names": "sagemaker_endpoints", "sagemaker_cluster_names": "sagemaker_clusters"}
# Edge, Elastic IP, TGW routing and Direct Connect collections exist only in samples that use them.
OPTIONAL = {"elastic_ips", "transit_gateway_route_tables", "cloudfront_distributions", "dx_connections", "dx_virtual_interfaces", "dx_gateways"}
# Phase-2 detail calls the collector makes per resource: (service, operation, raw key).
DETAILS = (("kafka", "list_nodes", "msk_nodes"), ("opensearch", "describe_domains", "opensearch_domains"),
           ("opensearch", "describe_domain_nodes", "opensearch_nodes"), ("ecs", "describe_services", "ecs_services"),
           ("ecs", "describe_tasks", "ecs_tasks"), ("efs", "describe_mount_targets", "efs_mount_targets"))
RAW_KEYS = list(dict.fromkeys([LISTINGS.get(name, name) for *_, name in OPERATIONS if name not in OPTIONAL]
                              + ["target_health"] + [key for *_, key in DETAILS]))


class Sample:
    def __init__(self, key, title, practices, region=REGION, account=ACCOUNT):
        self.key, self.title, self.practices = key, title, practices
        self.region, self.account = region, account
        self.raw = {key: [] for key in RAW_KEYS}

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
        self.raw["instances"].append({
            "InstanceId": key, "VpcId": vpc, "SubnetId": subnet, "InstanceType": kind, "State": {"Name": "running"},
            "PrivateIpAddress": ip or self.free_addresses(subnet, 1)[0],
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

    def used_addresses(self):
        """Private addresses already taken: instances, ECS task ENIs, EFS mount targets and IP targets."""
        tasks = (d.get("value") for t in self.raw["ecs_tasks"] for a in t.get("attachments", []) for d in a.get("details", [])
                 if d.get("name") == "privateIPv4Address")
        return ({i.get("PrivateIpAddress") for i in self.raw["instances"]} | set(tasks)
                | {m.get("IpAddress") for m in self.raw["efs_mount_targets"]}
                | {d["Target"]["Id"] for h in self.raw["target_health"] for d in h["Descriptions"]})

    def free_addresses(self, subnet, count, start=10):
        """`count` unused addresses in the subnet from offset `start` (AWS reserves the first four and the last)."""
        network = ipaddress.ip_network(next(s["CidrBlock"] for s in self.raw["subnets"] if s["SubnetId"] == subnet))
        last, used = network.num_addresses - 1, self.used_addresses()
        offsets = (*range(min(start, last), last), *range(4, min(start, last)))
        found = list(islice((ip for ip in map(lambda o: str(network[o]), offsets) if ip not in used), count))
        if len(found) < count:
            raise IndexError(f"{subnet} ({network}) has {len(found)} free addresses, {count} needed")
        return found

    def security_group(self, key, vpc, name, ingress, egress=("0.0.0.0/0",)):
        """Rules as (port, source): a CIDR, another group's ID, a prefix list ID or "self"; port None allows all traffic.

        Egress is a list of sources with all traffic allowed (the default is AWS's 0.0.0.0/0)."""
        def rule(port, source):
            source = key if source == "self" else source
            ports = {"IpProtocol": "-1"} if port is None else {"IpProtocol": "tcp", "FromPort": port, "ToPort": port}
            if source.startswith("pl-"):
                return {**ports, "PrefixListIds": [{"PrefixListId": source}]}
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

    def transit_gateway(self, key, name, owner=None):
        """A Transit Gateway; `owner` set to another account is how a RAM-shared gateway appears to this account."""
        self.raw["transit_gateways"].append({"TransitGatewayId": key, "State": "available", "OwnerId": owner or self.account,
                                             "Tags": self.tags(name)})
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

    # --- Edge, Elastic IPs, Transit Gateway routing domains and Direct Connect --------------------------------------
    # Their raw collections exist only in samples that use them, so other samples report no such coverage.
    def digest(self, *parts):
        return hashlib.sha256("/".join(map(str, parts)).encode()).hexdigest()

    def uuid(self, *parts):
        """A deterministic UUID-shaped ID (WAF web ACLs, Direct Connect gateways)."""
        h = self.digest(*parts)
        return f"{h[:8]}-{h[8:12]}-{h[12:16]}-{h[16:20]}-{h[20:32]}"

    def elastic_ip(self, key, ip, name, nat=None, balancer=None, subnet=None):
        """An Elastic IP on a NAT gateway, or on a network load balancer's subnet (one static address per AZ)."""
        eni = "eni-" + self.digest(key)[:17]
        self.raw.setdefault("elastic_ips", []).append({"AllocationId": key, "PublicIp": ip, "Domain": "vpc", "NetworkInterfaceId": eni,
                                                       "AssociationId": "eipassoc-" + self.digest(key, "assoc")[:17], "Tags": self.tags(name)})
        if nat:
            gateway = next(g for g in self.raw["nat_gateways"] if g["NatGatewayId"] == nat)
            gateway.setdefault("NatGatewayAddresses", []).append({"AllocationId": key, "PublicIp": ip, "NetworkInterfaceId": eni, "IsPrimary": True})
        if balancer:
            zone = next(z for b in self.raw["load_balancers"] if b["LoadBalancerArn"] == balancer for z in b["AvailabilityZones"] if z["SubnetId"] == subnet)
            zone.setdefault("LoadBalancerAddresses", []).append({"IpAddress": ip, "AllocationId": key})

    def web_acl(self, name, scope="REGIONAL", resources=()):
        """AWS WAF web ACL: REGIONAL ones list the load balancers they protect, CLOUDFRONT ones are named by distributions."""
        ident = self.uuid(name, scope)
        where = "us-east-1:{}:global" if scope == "CLOUDFRONT" else f"{self.region}:{{}}:regional"
        arn = f"arn:aws:wafv2:{where.format(self.account)}/webacl/{name}/{ident}"
        self.raw.setdefault("waf_web_acls", []).append({"Name": name, "Id": ident, "ARN": arn, "LockToken": ident, "Scope": scope,
                                                        **({"ResourceArns": list(resources)} if scope == "REGIONAL" else {})})
        return arn

    def cloudfront(self, key, alias, origins, web_acl=""):
        """A CloudFront distribution; origins are load balancer ARNs (matched by DNS name) or S3 bucket names."""
        items = []
        for origin in origins:
            balancer = next((b for b in self.raw["load_balancers"] if b["LoadBalancerArn"] == origin), None)
            if balancer:
                balancer.setdefault("DNSName", f"{balancer['LoadBalancerName']}-{int(self.digest(origin)[:8], 16)}.{self.region}.elb.amazonaws.com")
                items.append({"Id": f"alb-{balancer['LoadBalancerName']}", "DomainName": balancer["DNSName"],
                              "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "https-only"}})
            else:
                items.append({"Id": f"s3-{origin}", "DomainName": f"{origin}.s3.{self.region}.amazonaws.com", "S3OriginConfig": {"OriginAccessIdentity": ""},
                              "OriginAccessControlId": "E" + self.digest(origin).upper()[:13]})
        self.raw.setdefault("cloudfront_distributions", []).append({
            "Id": key, "ARN": f"arn:aws:cloudfront::{self.account}:distribution/{key}", "Status": "Deployed", "Enabled": True,
            "DomainName": f"d{self.digest(key)[:13]}.cloudfront.net", "Aliases": {"Quantity": 1, "Items": [alias]}, "Comment": alias,
            "Origins": {"Quantity": len(items), "Items": items}, "PriceClass": "PriceClass_200", "HttpVersion": "http2and3",
            "DefaultCacheBehavior": {"TargetOriginId": items[0]["Id"], "ViewerProtocolPolicy": "redirect-to-https"}, "WebACLId": web_acl})

    def tgw_route_table(self, key, gateway, name, associate=(), propagate=(), routes=()):
        """A Transit Gateway route table. Attachments are (attachment ID, resource type, resource ID); routes are
        (destination, attachments), with no attachments meaning a static blackhole route."""
        entry = lambda item: {"TransitGatewayAttachmentId": item[0], "ResourceType": item[1], "ResourceId": item[2], "TransitGatewayRouteTableId": key}
        self.raw.setdefault("transit_gateway_route_tables", []).append({
            "TransitGatewayRouteTableId": key, "TransitGatewayId": gateway, "State": "available", "DefaultAssociationRouteTable": False,
            "DefaultPropagationRouteTable": False, "Tags": self.tags(name)})
        self.raw.setdefault("transit_gateway_route_table_associations", []).extend({**entry(a), "State": "associated"} for a in associate)
        self.raw.setdefault("transit_gateway_route_table_propagations", []).extend({**entry(p), "State": "enabled"} for p in propagate)
        self.raw.setdefault("transit_gateway_routes", []).extend({
            "DestinationCidrBlock": destination, "TransitGatewayRouteTableId": key, "Type": "propagated" if targets else "static",
            "State": "active" if targets else "blackhole",
            "TransitGatewayAttachments": [{"TransitGatewayAttachmentId": t[0], "ResourceType": t[1], "ResourceId": t[2]} for t in targets]}
            for destination, targets in routes)

    def dx_gateway(self, name, tgw=None, vgw=None, prefixes=()):
        """A Direct Connect gateway associated with a Transit Gateway (transit VIFs) or a virtual private gateway."""
        key = self.uuid("dxgw", name)
        self.raw.setdefault("dx_gateways", []).append({"directConnectGatewayId": key, "directConnectGatewayName": name, "amazonSideAsn": 64513,
                                                       "ownerAccount": self.account, "directConnectGatewayState": "available"})
        self.raw.setdefault("dx_gateway_associations", []).append({
            "directConnectGatewayId": key, "associationState": "associated", "associationId": self.uuid("assoc", name),
            "associatedGateway": {"id": tgw or vgw, "type": "transitGateway" if tgw else "virtualPrivateGateway", "ownerAccount": self.account,
                                  "region": self.region},
            "allowedPrefixesToDirectConnectGateway": [{"cidr": cidr} for cidr in prefixes]})
        return key

    def direct_connect(self, key, name, location, gateway, vlan, bandwidth="10Gbps"):
        """A dedicated connection at a Direct Connect location with a transit VIF (BGP up) to a Direct Connect gateway."""
        self.raw.setdefault("dx_connections", []).append({"connectionId": key, "connectionName": name, "location": location, "bandwidth": bandwidth,
                                                          "connectionState": "available", "region": self.region, "ownerAccount": self.account})
        self.raw.setdefault("dx_virtual_interfaces", []).append({
            "virtualInterfaceId": "dxvif-" + self.digest(key)[:8], "virtualInterfaceName": f"{name}-transit", "virtualInterfaceType": "transit",
            "connectionId": key, "vlan": vlan, "asn": 65010, "amazonSideAsn": 64513, "directConnectGatewayId": gateway,
            "virtualInterfaceState": "available", "region": self.region, "ownerAccount": self.account,
            "bgpPeers": [{"bgpPeerId": "dxpeer-" + self.digest(key, "bgp")[:8], "addressFamily": "ipv4", "bgpPeerState": "available", "bgpStatus": "up"}]})

    def network(self, vpc, prefix, azs, public, private, nat_per_az=True, nat=True):
        """Internet gateway, NAT in public subnets, and one route table per tier (private per AZ)."""
        igw = self.internet_gateway(f"igw-{prefix}", vpc, f"{prefix}-igw")
        self.route_table(f"rtb-{prefix}-public", vpc, f"{prefix}-public", public, gateway=igw)
        nats = [self.nat_gateway(f"nat-{prefix}-{az}", vpc, public[i], f"{prefix}-nat-{az}") for i, az in enumerate(azs)
                if nat_per_az or i == 0] if nat else []
        for i, az in enumerate(azs if private else ()):
            self.route_table(f"rtb-{prefix}-private-{az}", vpc, f"{prefix}-private-{az}", [p[i] for p in private],
                             nat=(nats[i] if nat_per_az else nats[0]) if nats else None)
        # Subnets without an explicit association (the data tier) use the main table: local route only.
        self.route_table(f"rtb-{prefix}-main", vpc, f"{prefix}-isolated", main=True)

    def snapshot(self):
        timestamp, raw = now(), copy.deepcopy(self.raw)
        # DescribeSubnets.AvailableIpAddressCount: the CIDR minus the five reserved addresses and the instances in it.
        # Computed on the copy each time, so instances added after an earlier snapshot still count.
        for subnet in raw["subnets"]:
            used = sum(i["SubnetId"] == subnet["SubnetId"] for i in raw["instances"])
            subnet.setdefault("AvailableIpAddressCount", ipaddress.ip_network(subnet["CidrBlock"]).num_addresses - 5 - used)
        # One row per collector listing (in the collector's order), then the detail calls behind them.
        items = {**{name: len(raw[key]) for name, key in LISTINGS.items()},
                 "ecs_cluster_arns": len({s["clusterArn"] for s in raw["ecs_services"]})}
        coverage = [{"service": service, "operation": operation, "status": "complete", "pages": 1,
                     "items": items[name] if name in LISTINGS else len(raw[name])}
                    for service, operation, _, name in OPERATIONS if name not in OPTIONAL]
        coverage += [{"service": service, "operation": operation, "status": "complete", "pages": 1, "items": len(raw[key])}
                     for service, operation, key in DETAILS]
        coverage += [{"service": "elbv2", "operation": "describe_target_health", "parameters": {"TargetGroupArn": h["TargetGroupArn"]},
                      "status": "complete", "pages": 1, "items": len(h["Descriptions"])} for h in raw["target_health"]]
        # Edge, Elastic IP, TGW routing and Direct Connect calls appear only in samples that use them.
        coverage += [{"service": service, "operation": operation, "parameters": params, "status": "complete", "pages": 1,
                      "items": sum(1 for x in raw[key] if all(x.get(k) == v for k, v in params.items()))}
                     for service, operation, key, params in (
                         ("ec2", "describe_addresses", "elastic_ips", {}),
                         ("ec2", "describe_transit_gateway_route_tables", "transit_gateway_route_tables", {}),
                         ("cloudfront", "list_distributions", "cloudfront_distributions", {}),
                         ("wafv2", "list_web_acls", "waf_web_acls", {"Scope": "REGIONAL"}),
                         ("wafv2", "list_web_acls", "waf_web_acls", {"Scope": "CLOUDFRONT"}),
                         ("directconnect", "describe_connections", "dx_connections", {}),
                         ("directconnect", "describe_virtual_interfaces", "dx_virtual_interfaces", {}),
                         ("directconnect", "describe_direct_connect_gateways", "dx_gateways", {}),
                         ("directconnect", "describe_direct_connect_gateway_associations", "dx_gateway_associations", {}))
                     if key in raw]
        return make_snapshot({
            "account_id": self.account, "region": self.region, "partition": "aws", "started_at": timestamp, "finished_at": timestamp,
            "status": "complete", "demo": True, "sample": self.key, "sample_version": SAMPLE_VERSION, "title": self.title, "practices": self.practices,
            "scope": ["VPC", "Subnet", "EC2", "ELBv2", "TargetGroup", "RegisteredTarget", "InternetGateway", "NatGateway",
                      "RouteTable", "RDS", "AutoScalingGroup", "VpcEndpoint", "TransitGateway", "EKS", "MSK", "ElastiCache", "Lambda",
                      "OpenSearch", "ECS", "EFS", "Redshift", "VpcPeering", "VPN", "ElasticIP", "TransitGatewayRouteTable", "CloudFront", "WAF",
                      "DirectConnect"],
            "limitations": ["AWS best practice 패턴을 따른 합성 샘플이며 실제 계정 데이터가 아닙니다.",
                            "리스너 규칙·Route 53·Shield·Network Firewall은 수집 범위 밖이라 그리지 않습니다 (권고는 practices에 문구로).",
                            "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."]},
            coverage, raw)
