"""Databases, caches, Kafka, OpenSearch, file systems and the data warehouse."""
from .base import name_of


class Data:
    def subnet_group(self, family, name, vpc, subnets):
        key = f"{family}-subnets:{name}"
        if key in self.ids:
            return self.ref(key)
        refs = self.subnet_refs(subnets)
        if not refs:
            return None
        kind, field = {"rds": ("AWS::RDS::DBSubnetGroup", "DBSubnetGroupDescription"), "docdb": ("AWS::DocDB::DBSubnetGroup", "DBSubnetGroupDescription"),
                       "cache": ("AWS::ElastiCache::SubnetGroup", "Description"), "redshift": ("AWS::Redshift::ClusterSubnetGroup", "Description")}[family]
        self.add(self.logical("SubnetGroup", name), kind, {field: f"Diagram Bot {name}", "SubnetIds": refs}, physical=key)
        return self.ref(key)

    def databases(self):
        clusters = {c["DBClusterIdentifier"]: c for c in self.raw.get("db_clusters", [])}
        instances = self.raw.get("db_instances", [])
        storage = lambda db: db.get("AllocatedStorage") and str(db["AllocatedStorage"]) or self.param(
            "DBAllocatedStorage", "String", "RDS 할당 스토리지 GiB (스냅샷에 값이 없을 때)", Default="100")
        for cluster in clusters.values():
            cid, engine = cluster["DBClusterIdentifier"], cluster.get("Engine", "")
            members = [db for db in instances if db.get("DBClusterIdentifier") == cid]
            group = next((db.get("DBSubnetGroup") for db in members if isinstance(db.get("DBSubnetGroup"), dict)), None)
            docdb = engine == "docdb"
            if engine.startswith("neptune") or not group or not (engine.startswith("aurora") or docdb or cluster.get("DBClusterInstanceClass")):
                self.skip("DBCluster", cid, "Neptune이거나 서브넷 그룹·인스턴스 클래스를 스냅샷에서 알 수 없습니다.")
                continue
            if cluster.get("GlobalClusterIdentifier") or cluster.get("ReplicationSourceIdentifier"):
                self.assume(f"DB 클러스터 {cid}: 글로벌 데이터베이스·리전 간 복제는 표현하지 않고 독립 클러스터로 만듭니다.")
            subnets = [s["SubnetIdentifier"] for s in group.get("Subnets", [])]
            subnet_group = self.subnet_group("docdb" if docdb else "rds", group.get("DBSubnetGroupName", cid), group.get("VpcId"), subnets)
            if not subnet_group:
                self.skip("DBCluster", cid, "DB 서브넷 그룹의 서브넷이 템플릿에 없습니다.")
                continue
            vpc = group.get("VpcId")
            groups = self.security_groups([g.get("VpcSecurityGroupId") for g in cluster.get("VpcSecurityGroups", [])], vpc)
            logical = self.logical("DocDbCluster" if docdb else "DbCluster", cid)
            props = {"DBSubnetGroupName": subnet_group, "StorageEncrypted": cluster.get("StorageEncrypted"), "EngineVersion": cluster.get("EngineVersion"),
                     "MasterUsername": self.master_username(cluster.get("MasterUsername")), "ManageMasterUserPassword": True,
                     "VpcSecurityGroupIds": groups, "Tags": self.tags({"Tags": cluster.get("TagList")}, cid)}
            if not docdb:
                props.update(Engine=engine, ServerlessV2ScalingConfiguration=cluster.get("ServerlessV2ScalingConfiguration"),
                             DBClusterInstanceClass=cluster.get("DBClusterInstanceClass"), StorageType=cluster.get("StorageType"),
                             AllocatedStorage=cluster.get("AllocatedStorage"), Iops=cluster.get("Iops"))
            self.add(logical, "AWS::DocDB::DBCluster" if docdb else "AWS::RDS::DBCluster", props, physical="cluster:" + cid)
            if not engine.startswith("aurora") and not docdb:
                continue  # Multi-AZ DB clusters create their own instances
            writer = next((m["DBInstanceIdentifier"] for m in cluster.get("DBClusterMembers", []) if m.get("IsClusterWriter")), None)
            ordered = sorted(members, key=lambda db: db["DBInstanceIdentifier"] != writer)
            first = None
            for db in ordered:
                member = self.add(self.logical(logical + "Instance", db["DBInstanceIdentifier"]), "AWS::DocDB::DBInstance" if docdb else "AWS::RDS::DBInstance", {
                    "DBClusterIdentifier": {"Ref": logical}, "DBInstanceClass": db.get("DBInstanceClass"), "AvailabilityZone": self.zone(db.get("AvailabilityZone")),
                    **({} if docdb else {"Engine": engine, "PubliclyAccessible": db.get("PubliclyAccessible")})},
                    physical=db["DBInstanceIdentifier"], depends=[first] if first else None)
                first = first or member  # the first instance created becomes the writer
        if clusters or instances:
            self.assume("DB 관리자 비밀번호는 Secrets Manager가 관리합니다 (ManageMasterUserPassword); 템플릿에 비밀번호를 두지 않습니다.")
        for db in instances:
            if db.get("DBClusterIdentifier") or db.get("ReadReplicaSourceDBInstanceIdentifier"):
                continue
            self.rds_instance(db, storage)
        for db in instances:
            source = db.get("ReadReplicaSourceDBInstanceIdentifier")
            if not source or db.get("DBClusterIdentifier"):
                continue
            if source not in self.ids:
                self.skip("DBInstance", db["DBInstanceIdentifier"], "원본 DB가 다른 리전에 있거나 템플릿에 없어 읽기 전용 복제본을 만들 수 없습니다.")
                continue
            self.add(self.logical("Db", db["DBInstanceIdentifier"]), "AWS::RDS::DBInstance", {
                "SourceDBInstanceIdentifier": self.ref(source), "DBInstanceClass": db.get("DBInstanceClass"),
                "AvailabilityZone": self.zone(db.get("AvailabilityZone")), "PubliclyAccessible": db.get("PubliclyAccessible"),
                "Tags": self.tags({"Tags": db.get("TagList")}, db["DBInstanceIdentifier"])}, physical=db["DBInstanceIdentifier"])

    def rds_instance(self, db, storage):
        identifier = db["DBInstanceIdentifier"]
        group = db.get("DBSubnetGroup") or {}
        subnet_group = self.subnet_group("rds", group.get("DBSubnetGroupName", identifier), group.get("VpcId"),
                                         [s["SubnetIdentifier"] for s in group.get("Subnets", [])]) if group else None
        if not subnet_group:
            self.skip("DBInstance", identifier, "DB 서브넷 그룹의 서브넷이 템플릿에 없습니다.")
            return
        self.add(self.logical("Db", identifier), "AWS::RDS::DBInstance", {
            "Engine": db.get("Engine"), "EngineVersion": db.get("EngineVersion"), "DBInstanceClass": db.get("DBInstanceClass"),
            "AllocatedStorage": storage(db), "StorageType": db.get("StorageType"), "Iops": db.get("Iops"), "LicenseModel": db.get("LicenseModel"),
            "MultiAZ": bool(db.get("MultiAZ")), "AvailabilityZone": None if db.get("MultiAZ") else self.zone(db.get("AvailabilityZone")),
            "StorageEncrypted": db.get("StorageEncrypted"), "PubliclyAccessible": db.get("PubliclyAccessible"),
            "DBSubnetGroupName": subnet_group, "MasterUsername": self.master_username(db.get("MasterUsername")), "ManageMasterUserPassword": True,
            "VPCSecurityGroups": self.security_groups([g.get("VpcSecurityGroupId") for g in db.get("VpcSecurityGroups", [])], group.get("VpcId")),
            "Tags": self.tags({"Tags": db.get("TagList")}, identifier)}, physical=identifier)

    def caches(self):
        groups = {g["CacheSubnetGroupName"]: g for g in self.raw.get("cache_subnet_groups", [])}
        clusters = self.raw.get("cache_clusters", [])

        def subnet_group(name):
            group = groups.get(name)
            return self.subnet_group("cache", name, group.get("VpcId"), [s["SubnetIdentifier"] for s in group.get("Subnets", [])]) if group else None

        for group in self.raw.get("replication_groups", []):
            gid = group["ReplicationGroupId"]
            members = {c["CacheClusterId"]: c for c in clusters if c.get("ReplicationGroupId") == gid}
            node = next(iter(members.values()), None)
            subnets = subnet_group(node.get("CacheSubnetGroupName")) if node else None
            if not subnets:
                self.skip("ElastiCacheReplicationGroup", gid, "노드 또는 캐시 서브넷 그룹이 템플릿에 없습니다 (서버리스 캐시일 수 있음).")
                continue
            zone = lambda member: self.zone((members.get(member.get("CacheClusterId")) or {}).get("PreferredAvailabilityZone"))
            shards = group.get("NodeGroups", [])
            props = {"ReplicationGroupDescription": f"Diagram Bot {gid}", "Engine": node.get("Engine"), "EngineVersion": node.get("EngineVersion"),
                     "CacheNodeType": node.get("CacheNodeType"), "CacheSubnetGroupName": subnets,
                     "AutomaticFailoverEnabled": group.get("AutomaticFailover") in ("enabled", "enabling"), "MultiAZEnabled": group.get("MultiAZ") == "enabled",
                     "AtRestEncryptionEnabled": group.get("AtRestEncryptionEnabled"), "TransitEncryptionEnabled": group.get("TransitEncryptionEnabled"),
                     "SecurityGroupIds": self.security_groups([g.get("SecurityGroupId") for g in node.get("SecurityGroups", [])],
                                                              groups[node["CacheSubnetGroupName"]].get("VpcId")),
                     "Tags": [{"Key": "Name", "Value": gid}]}
            if group.get("ClusterEnabled") and shards:  # without NodeGroups (not reported) CloudFormation's defaults apply
                props.update(NumNodeGroups=len(shards), ReplicasPerNodeGroup=max(len(s.get("NodeGroupMembers", [])) for s in shards) - 1,
                             NodeGroupConfiguration=[{"NodeGroupId": s.get("NodeGroupId"),
                                                      "PrimaryAvailabilityZone": next((zone(m) for m in s.get("NodeGroupMembers", [])
                                                                                       if m.get("CurrentRole") == "primary"), None),
                                                      "ReplicaAvailabilityZones": [zone(m) for m in s.get("NodeGroupMembers", [])
                                                                                   if m.get("CurrentRole") != "primary"]}
                                                     for s in shards])
            elif not group.get("ClusterEnabled"):
                ordered = sorted((m for s in shards for m in s.get("NodeGroupMembers", [])), key=lambda m: m.get("CurrentRole") != "primary")
                props.update(NumCacheClusters=len(ordered) or len(members), PreferredCacheClusterAZs=[zone(m) for m in ordered if zone(m)])
            self.add(self.logical("Cache", gid), "AWS::ElastiCache::ReplicationGroup", props, physical="cache:" + gid)
        for cluster in clusters:
            if cluster.get("ReplicationGroupId"):
                continue
            subnets = subnet_group(cluster.get("CacheSubnetGroupName"))
            if not subnets:
                self.skip("ElastiCacheCluster", cluster["CacheClusterId"], "캐시 서브넷 그룹이 템플릿에 없습니다.")
                continue
            self.add(self.logical("Cache", cluster["CacheClusterId"]), "AWS::ElastiCache::CacheCluster", {
                "Engine": cluster.get("Engine"), "EngineVersion": cluster.get("EngineVersion"), "CacheNodeType": cluster.get("CacheNodeType"),
                "NumCacheNodes": cluster.get("NumCacheNodes", 1), "CacheSubnetGroupName": subnets,
                "PreferredAvailabilityZone": self.zone(cluster.get("PreferredAvailabilityZone")) if cluster.get("NumCacheNodes", 1) == 1 else None,
                "VpcSecurityGroupIds": self.security_groups([g.get("SecurityGroupId") for g in cluster.get("SecurityGroups", [])],
                                                            groups[cluster["CacheSubnetGroupName"]].get("VpcId"))}, physical="cache:" + cluster["CacheClusterId"])

    def kafka(self):
        for cluster in self.raw.get("msk_clusters", []):
            name, info = cluster.get("ClusterName"), cluster.get("Provisioned") or {}
            broker = info.get("BrokerNodeGroupInfo") or {}
            subnets = self.subnet_refs(broker.get("ClientSubnets", []))
            if cluster.get("ClusterType", "PROVISIONED") != "PROVISIONED" or not subnets:
                self.skip("MSKCluster", cluster.get("ClusterArn"), "MSK Serverless이거나 브로커 서브넷이 템플릿에 없습니다.", name)
                continue
            transit = ((info.get("EncryptionInfo") or {}).get("EncryptionInTransit")) or None
            storage = ((broker.get("StorageInfo") or {}).get("EbsStorageInfo") or {}).get("VolumeSize")
            self.add(self.logical("Msk", name), "AWS::MSK::Cluster", {
                "ClusterName": name, "KafkaVersion": (info.get("CurrentBrokerSoftwareInfo") or {}).get("KafkaVersion"),
                "NumberOfBrokerNodes": info.get("NumberOfBrokerNodes"),
                "BrokerNodeGroupInfo": {"InstanceType": broker.get("InstanceType"), "ClientSubnets": subnets,
                                        "SecurityGroups": self.security_groups(broker.get("SecurityGroups", []), self.vpc_of(broker.get("ClientSubnets", []))),
                                        **({"StorageInfo": {"EBSStorageInfo": {"VolumeSize": storage}}} if storage else {})},
                "EncryptionInfo": {"EncryptionInTransit": {k: v for k, v in transit.items() if k in ("ClientBroker", "InCluster")}} if transit else None},
                physical=cluster.get("ClusterArn"))
            if ((info.get("CurrentBrokerSoftwareInfo") or {}).get("KafkaVersion") or "").endswith(".x"):
                self.assume(f"MSK {name}: Kafka 버전 {(info.get('CurrentBrokerSoftwareInfo') or {}).get('KafkaVersion')}를 그대로 씁니다 (.x는 MSK가 패치 버전을 관리).")

    def search(self):
        for domain in self.raw.get("opensearch_domains", []):
            name, vpc_options = domain.get("DomainName"), domain.get("VPCOptions") or {}
            subnets = self.subnet_refs(vpc_options.get("SubnetIds", [])) if vpc_options.get("SubnetIds") else None
            if vpc_options.get("SubnetIds") and not subnets:
                self.skip("OpenSearchDomain", domain.get("ARN", name), "도메인 서브넷이 템플릿에 없습니다.", name)
                continue
            config = {k: v for k, v in (domain.get("ClusterConfig") or {}).items() if k in (
                "InstanceType", "InstanceCount", "DedicatedMasterEnabled", "DedicatedMasterType", "DedicatedMasterCount", "ZoneAwarenessEnabled",
                "ZoneAwarenessConfig", "MultiAZWithStandbyEnabled", "WarmEnabled", "WarmType", "WarmCount")}
            if not config.get("DedicatedMasterEnabled"):
                config = {k: v for k, v in config.items() if not k.startswith("DedicatedMaster") or k == "DedicatedMasterEnabled"}
            ebs = domain.get("EBSOptions")
            if not ebs:
                ebs = {"EBSEnabled": True, "VolumeSize": self.param("OpenSearchVolumeSize", "Number", "OpenSearch 데이터 노드 EBS 볼륨 GiB (스냅샷에 값이 없을 때)", Default=100)}
                self.assume("OpenSearch EBS 볼륨 크기가 스냅샷에 없어 매개변수(기본 100 GiB)로 둡니다.")
            self.assume("OpenSearch 액세스 정책은 수집 범위 밖이라 두지 않습니다 (VPC 안에서 보안 그룹으로만 접근).")
            self.add(self.logical("OpenSearch", name), "AWS::OpenSearchService::Domain", {
                "EngineVersion": domain.get("EngineVersion"), "ClusterConfig": config,
                "EBSOptions": {k: v for k, v in ebs.items() if k in ("EBSEnabled", "VolumeType", "VolumeSize", "Iops", "Throughput")},
                "VPCOptions": {"SubnetIds": subnets, "SecurityGroupIds": self.security_groups(vpc_options.get("SecurityGroupIds", []), vpc_options.get("VPCId"))}
                if subnets else None,
                "EncryptionAtRestOptions": {"Enabled": (domain.get("EncryptionAtRestOptions") or {}).get("Enabled")} if domain.get("EncryptionAtRestOptions") else None,
                "NodeToNodeEncryptionOptions": domain.get("NodeToNodeEncryptionOptions"),
                "Tags": [{"Key": "Name", "Value": name}]}, physical=domain.get("ARN", name))

    def file_systems(self):
        targets = self.raw.get("efs_mount_targets", [])
        for fs in self.raw.get("efs_file_systems", []):
            fid = fs["FileSystemId"]
            logical = self.add(self.logical("Efs", fs.get("Name"), fid), "AWS::EFS::FileSystem", {
                "Encrypted": fs.get("Encrypted"), "PerformanceMode": fs.get("PerformanceMode"), "ThroughputMode": fs.get("ThroughputMode"),
                "ProvisionedThroughputInMibps": fs.get("ProvisionedThroughputInMibps"), "AvailabilityZoneName": self.zone(fs.get("AvailabilityZoneName")),
                "FileSystemTags": [{"Key": t["Key"], "Value": t.get("Value", "")} for t in self.tags(fs, fs.get("Name"))]}, physical=fid)
            for target in targets:
                if target.get("FileSystemId") != fid:
                    continue
                if target.get("SubnetId") not in self.ids:
                    self.skip("EFSMountTarget", target.get("MountTargetId"), "마운트 타깃 서브넷이 템플릿에 없습니다.")
                    continue
                vpc = target.get("VpcId") or self.vpc_of([target["SubnetId"]])
                groups = self.security_groups(target.get("SecurityGroups", []), vpc) or [{"Fn::GetAtt": [self.ids[vpc], "DefaultSecurityGroup"]}]
                if not target.get("SecurityGroups"):
                    self.assume("EFS 마운트 타깃 보안 그룹은 수집하지 않아 VPC 기본 보안 그룹을 씁니다 (API 기본 동작과 같음).")
                self.add(self.logical(logical + "Mount", self.ids[target["SubnetId"]].removeprefix("Subnet")), "AWS::EFS::MountTarget",
                         {"FileSystemId": {"Ref": logical}, "SubnetId": self.ref(target["SubnetId"]), "SecurityGroups": groups})
        for fs in self.raw.get("fsx_file_systems", []):
            fid = fs["FileSystemId"]
            subnets = self.subnet_refs(fs.get("SubnetIds", []))
            if fs.get("FileSystemType") != "LUSTRE" or not subnets:
                self.skip("FSxFileSystem", fid, "FSx for Lustre만 지원합니다 (Windows·ONTAP·OpenZFS는 디렉터리·SVM 설정이 필요) 또는 서브넷이 템플릿에 없습니다.", name_of(fs))
                continue
            lustre = fs.get("LustreConfiguration") or {}
            persistent = str(lustre.get("DeploymentType", "")).startswith("PERSISTENT")
            self.add(self.logical("Fsx", name_of(fs), fid), "AWS::FSx::FileSystem", {
                "FileSystemType": "LUSTRE", "FileSystemTypeVersion": fs.get("FileSystemTypeVersion"), "StorageCapacity": fs.get("StorageCapacity"),
                "StorageType": fs.get("StorageType"), "SubnetIds": subnets,
                "LustreConfiguration": {"DeploymentType": lustre.get("DeploymentType"), "DataCompressionType": lustre.get("DataCompressionType"),
                                        **({"PerUnitStorageThroughput": lustre.get("PerUnitStorageThroughput")} if persistent else {})},
                "Tags": self.tags(fs)}, physical=fid)
            if fs.get("KmsKeyId"):
                self.assume("FSx 암호화 키는 AWS 관리형 키를 씁니다 (수집된 KMS 키 ARN은 템플릿 밖 리소스).")

    def warehouse(self):
        groups = {g["ClusterSubnetGroupName"]: g for g in self.raw.get("redshift_subnet_groups", [])}
        for cluster in self.raw.get("redshift_clusters", []):
            cid = cluster["ClusterIdentifier"]
            group = groups.get(cluster.get("ClusterSubnetGroupName"))
            subnets = self.subnet_group("redshift", cluster["ClusterSubnetGroupName"], group.get("VpcId"),
                                        [s["SubnetIdentifier"] for s in group.get("Subnets", [])]) if group else None
            if not subnets:
                self.skip("RedshiftCluster", cid, "클러스터 서브넷 그룹이 템플릿에 없습니다.")
                continue
            multi_az = cluster.get("MultiAZ") == "Enabled"
            nodes = cluster.get("NumberOfNodes", 1)
            self.add(self.logical("Redshift", cid), "AWS::Redshift::Cluster", {
                "NodeType": cluster.get("NodeType"), "ClusterType": "multi-node" if nodes > 1 else "single-node",
                "NumberOfNodes": nodes if nodes > 1 else None, "DBName": self.param("RedshiftDatabaseName", "String", "Redshift 첫 데이터베이스 이름 (수집하지 않음)", Default="dev"),
                "MasterUsername": self.master_username(cluster.get("MasterUsername")), "ManageMasterPassword": True,
                "ClusterSubnetGroupName": subnets, "Encrypted": cluster.get("Encrypted"), "PubliclyAccessible": cluster.get("PubliclyAccessible"),
                "MultiAZ": multi_az or None, "AvailabilityZone": None if multi_az else self.zone(cluster.get("AvailabilityZone")),
                "VpcSecurityGroupIds": self.security_groups([g.get("VpcSecurityGroupId") for g in cluster.get("VpcSecurityGroups", [])], cluster.get("VpcId")),
                "Tags": self.tags(cluster, cid)}, physical=cid)
