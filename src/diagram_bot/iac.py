"""Snapshot (design, sample or collection) → CloudFormation template draft (JSON).

The template recreates what the snapshot's API-shaped `raw` data states, with Ref/GetAtt between the
resources it creates; collected physical IDs never appear for those. What the APIs do not return
(AMIs, container images, IAM roles, certificates, listener rules, passwords) becomes a Parameter or a
documented assumption, and whatever cannot be expressed faithfully is listed in
Metadata.DiagramBot.Unsupported instead of being invented. A draft for review, never applied by the app.
"""
import ipaddress
import json
import re
from collections import Counter

PSEUDO = {"AWS::AccountId", "AWS::NoValue", "AWS::Partition", "AWS::Region", "AWS::StackId", "AWS::StackName", "AWS::URLSuffix"}
STATEFUL = {"AWS::RDS::DBInstance": "Snapshot", "AWS::RDS::DBCluster": "Snapshot", "AWS::DocDB::DBCluster": "Snapshot", "AWS::DocDB::DBInstance": "Delete",
            "AWS::ElastiCache::ReplicationGroup": "Snapshot", "AWS::ElastiCache::CacheCluster": "Snapshot", "AWS::Redshift::Cluster": "Snapshot",
            "AWS::EFS::FileSystem": "Retain", "AWS::FSx::FileSystem": "Retain", "AWS::OpenSearchService::Domain": "Retain", "AWS::MSK::Cluster": "Retain"}
GONE = ("deleted", "deleting", "terminated", "shutting-down", "failed", "rejected", "expired")
ROLE_PATTERN = r"^arn:aws[a-z-]*:iam::\d{12}:role/.+$"
# Graviton instance families carry a "g" after the generation digit (m7g, c6gn, r8gd, g5g); everything else here is x86_64.
GRAVITON = re.compile(r"^[a-z]+\d+[a-z-]*g[a-z-]*\.")


def camel(text):
    return "".join(word[:1].upper() + word[1:] for word in re.findall(r"[A-Za-z0-9]+", str(text)))


def name_of(item, *fallback):
    tag = next((t.get("Value") for t in item.get("Tags") or [] if t.get("Key") == "Name"), None)
    return tag or next((value for value in fallback if value), None)


class Template:
    def __init__(self, snapshot):
        self.raw, self.meta = snapshot.get("raw") or {}, snapshot["metadata"]
        self.region, self.account = self.meta.get("region"), self.meta.get("account_id")
        self.resources, self.parameters, self.outputs = {}, {}, {}
        self.ids = {}  # physical ID (or ARN, name) → logical ID
        self.unsupported, self.assumptions = [], []
        self.attachments = {}  # gateway physical ID → logical ID of its VPC attachment (routes depend on it)
        self.listeners = {}  # target group / load balancer ARN → listener logical IDs
        self.default_vpcs = set()  # skipped as a whole: their subnets, groups and tables are not listed one by one
        self.subnets = {s["SubnetId"]: s for s in self.raw.get("subnets", [])}
        self.instances = {i["InstanceId"]: i for i in self.raw.get("instances", []) if (i.get("State") or {}).get("Name") not in GONE}
        self.groups = {g["GroupId"]: g for g in self.raw.get("security_groups", [])}

    # --- bookkeeping ---------------------------------------------------------------------------
    def logical(self, prefix, *labels):
        """Readable, unique, alphanumeric logical ID: the prefix plus the first label with enough ASCII to read."""
        label = next((camel(text) for text in labels if text and len(camel(text)) >= 2), camel(labels[-1] if labels else "") or "Resource")
        base = (prefix + label)[:200]
        candidate, n = base, 2
        while candidate in self.resources or candidate in self.parameters:
            candidate, n = f"{base}{n}", n + 1
        return candidate

    def add(self, logical, kind, props, physical=None, depends=None):
        self.resources[logical] = {"Type": kind, "Properties": {k: clean(v) for k, v in props.items() if v not in (None, [], {})}}
        if depends:
            self.resources[logical]["DependsOn"] = sorted(set(depends))
        if physical:
            self.ids[physical] = logical
        return logical

    def ref(self, physical):
        return {"Ref": self.ids[physical]} if physical in self.ids else None

    def param(self, name, kind, description, **extra):
        if name not in self.parameters:
            self.parameters[name] = {"Type": kind, "Description": description, **extra}
        return {"Ref": name}

    def skip(self, kind, identifier, reason, name=None):
        entry = {"Type": kind, "Id": identifier, "Reason": reason}
        if name and name != identifier:
            entry["Name"] = name
        if entry not in self.unsupported:
            self.unsupported.append(entry)

    def assume(self, text):
        if text not in self.assumptions:
            self.assumptions.append(text)

    def tags(self, item, name=None):
        tags = [{"Key": t["Key"], "Value": str(t.get("Value", ""))} for t in item.get("Tags") or []
                if t.get("Key") and not t["Key"].startswith("aws:")]
        if name and not any(t["Key"] == "Name" for t in tags):
            tags.insert(0, {"Key": "Name", "Value": str(name)})
        return tags

    def subnet_refs(self, subnets):
        refs = [self.ref(s) for s in subnets if s]
        return refs if refs and all(refs) else None

    def vpc_of(self, subnets):
        return next((self.subnets[s].get("VpcId") for s in subnets if s in self.subnets), None)

    def security_group(self, group, vpc):
        """Ref to a group the template creates; a group attached but not described becomes an empty stub in the same VPC."""
        if group in self.ids:
            return {"Ref": self.ids[group]}
        known = self.groups.get(group)
        if known and known.get("GroupName") == "default" and known.get("VpcId") in self.ids:
            return {"Fn::GetAtt": [self.ids[known["VpcId"]], "DefaultSecurityGroup"]}
        if known:
            self.skip("SecurityGroupReference", group, "연결된 보안 그룹이 템플릿 밖 VPC(기본 VPC 등)에 있어 참조에서 뺍니다.", known.get("GroupName"))
            return None
        if vpc not in self.ids:
            return None
        logical = self.logical("SecurityGroup", group)
        self.add(logical, "AWS::EC2::SecurityGroup", {"GroupDescription": "Diagram Bot stub (rules not collected)", "VpcId": self.ref(vpc),
                                                      "Tags": [{"Key": "Name", "Value": logical}]}, physical=group)
        self.assume(f"보안 그룹 {group}: 리소스에 연결돼 있으나 규칙이 수집되지 않아 인바운드 규칙 없이 만듭니다 (아웃바운드는 AWS 기본값).")
        return {"Ref": self.ids[group]}

    def security_groups(self, groups, vpc):
        refs = [self.security_group(g, vpc) for g in groups if g]
        return [r for r in refs if r] or None

    def image(self, instance_type, architecture=None):
        arm = architecture == "arm64" or (architecture is None and bool(GRAVITON.match(str(instance_type))))
        name = "ImageIdArm64" if arm else "ImageIdX8664"
        return self.param(name, "AWS::EC2::Image::Id", f"{'arm64' if arm else 'x86_64'} 인스턴스용 AMI ID (스냅샷은 AMI를 조회하지 않음; 가속 인스턴스는 드라이버 포함 AMI)")

    def zone(self, name):
        """AZ names become parameters defaulting to the snapshot's AZs, so the draft can move to another region."""
        if not name or not self.region or not str(name).startswith(self.region):
            return name
        key = "AvailabilityZone" + camel(str(name)[len(self.region):]).upper()
        return self.param(key, "AWS::EC2::AvailabilityZone::Name", f"스냅샷의 {name}에 해당하는 가용 영역", Default=name)

    def master_username(self, collected=None):
        if collected:
            return collected
        return self.param("DBMasterUsername", "String", "DB 관리자 사용자 이름 (비밀번호는 Secrets Manager가 관리)", Default="dbadmin",
                          AllowedPattern="^[A-Za-z][A-Za-z0-9_]{0,15}$")

    # --- build ---------------------------------------------------------------------------------
    def build(self):
        for step in (self.network, self.gateways, self.nat_gateways, self.security_group_resources, self.route_tables, self.endpoints,
                     self.security_group_rules, self.placement_groups, self.load_balancers, self.auto_scaling, self.standalone_instances,
                     self.targets, self.databases, self.caches, self.kafka, self.search, self.file_systems, self.warehouse, self.kubernetes,
                     self.containers, self.vpn, self.not_expressible):
            step()
        for resource in self.resources.values():
            policy = STATEFUL.get(resource["Type"])
            if resource["Type"] == "AWS::RDS::DBInstance" and {"DBClusterIdentifier", "SourceDBInstanceIdentifier"} & set(resource["Properties"]):
                policy = "Delete"  # cluster members and read replicas take no final snapshot
            if policy:
                resource.update(DeletionPolicy=policy, UpdateReplacePolicy=policy)
        if any(r.get("DeletionPolicy") in ("Snapshot", "Retain") for r in self.resources.values()):
            self.assume("데이터를 가진 리소스는 스택 삭제·교체 때 스냅샷(지원 시) 또는 보존 정책을 둡니다.")
        meta = self.meta
        source = {k: meta.get(k) for k in ("title", "account_id", "region", "sample", "finished_at", "status") if meta.get(k) is not None}
        source["design"] = bool(meta.get("design"))
        template = {
            "AWSTemplateFormatVersion": "2010-09-09",
            "Description": f"Diagram Bot CloudFormation draft from snapshot '{ascii_text(meta.get('sample') or meta.get('title') or 'snapshot')}' "
                           f"({self.region}). Review parameters and Metadata.DiagramBot before deploying.",
            "Metadata": {"DiagramBot": {"Generator": "diagram-bot export-cfn", "Source": source,
                                        "Unsupported": self.unsupported, "Assumptions": self.assumptions}},
            "Parameters": self.parameters,
            "Resources": self.resources,
            "Outputs": self.outputs,
        }
        if not self.parameters:
            del template["Parameters"]
        if not self.outputs:
            del template["Outputs"]
        return template

    # --- network -------------------------------------------------------------------------------
    def network(self):
        self.assume("VPC의 DNS 지원·DNS 호스트 이름을 켭니다 (DescribeVpcs는 이 속성을 돌려주지 않으며, 인터페이스 엔드포인트 사설 DNS와 EKS 사설 엔드포인트에 필요).")
        secondary = {}
        for vpc in self.raw.get("vpcs", []):
            vid = vpc["VpcId"]
            if vpc.get("IsDefault"):
                # AWS creates the default VPC (and its subnets and groups) in every region; resources placed in it are listed below.
                self.skip("VPC", vid, "기본 VPC는 AWS가 리전마다 만듭니다. 기본 VPC와 그 안의 리소스는 템플릿에 넣지 않습니다.", name_of(vpc))
                self.default_vpcs.add(vid)
                continue
            logical = self.add(self.logical("Vpc", name_of(vpc), vid), "AWS::EC2::VPC", {
                "CidrBlock": vpc["CidrBlock"], "EnableDnsSupport": True, "EnableDnsHostnames": True,
                "InstanceTenancy": vpc.get("InstanceTenancy") if vpc.get("InstanceTenancy") == "dedicated" else None,
                "Tags": self.tags(vpc)}, physical=vid)
            self.outputs[f"{logical}Id"] = {"Description": name_of(vpc, logical), "Value": {"Ref": logical}}
            for block in vpc.get("CidrBlockAssociationSet") or []:
                if block.get("CidrBlock") and block["CidrBlock"] != vpc["CidrBlock"] and (block.get("CidrBlockState") or {}).get("State", "associated") == "associated":
                    secondary.setdefault(vid, []).append(self.add(self.logical(logical + "Cidr", block["CidrBlock"]), "AWS::EC2::VPCCidrBlock",
                                                                  {"VpcId": {"Ref": logical}, "CidrBlock": block["CidrBlock"]}))
        for subnet in self.raw.get("subnets", []):
            sid = subnet["SubnetId"]
            if subnet.get("VpcId") in self.default_vpcs:
                continue
            if subnet.get("VpcId") not in self.ids:
                self.skip("Subnet", sid, "소속 VPC가 템플릿에 없습니다.", name_of(subnet))
                continue
            self.add(self.logical("Subnet", name_of(subnet), sid), "AWS::EC2::Subnet", {
                "VpcId": self.ref(subnet["VpcId"]), "CidrBlock": subnet["CidrBlock"], "AvailabilityZone": self.zone(subnet.get("AvailabilityZone")),
                "MapPublicIpOnLaunch": subnet.get("MapPublicIpOnLaunch"), "Tags": self.tags(subnet)},
                physical=sid, depends=secondary.get(subnet["VpcId"]))

    def gateways(self):
        for gateway in self.raw.get("internet_gateways", []):
            gid = gateway["InternetGatewayId"]
            if gateway.get("Attachments") and all(a.get("VpcId") in self.default_vpcs for a in gateway["Attachments"]):
                continue
            logical = self.add(self.logical("InternetGateway", name_of(gateway), gid), "AWS::EC2::InternetGateway", {"Tags": self.tags(gateway)}, physical=gid)
            for attachment in gateway.get("Attachments", []):
                if attachment.get("VpcId") in self.ids:
                    self.attachments[gid] = self.add(f"{logical}Attachment", "AWS::EC2::VPCGatewayAttachment",
                                                     {"VpcId": self.ref(attachment["VpcId"]), "InternetGatewayId": {"Ref": logical}})
        for gateway in self.raw.get("vpn_gateways", []):
            gid = gateway["VpnGatewayId"]
            if gateway.get("State") in GONE:
                continue
            logical = self.add(self.logical("VpnGateway", name_of(gateway), gid), "AWS::EC2::VPNGateway",
                               {"Type": gateway.get("Type", "ipsec.1"), "AmazonSideAsn": gateway.get("AmazonSideAsn"), "Tags": self.tags(gateway)},
                               physical=gid)
            for attachment in gateway.get("VpcAttachments", []):
                if attachment.get("State") == "attached" and attachment.get("VpcId") in self.ids:
                    self.attachments[gid] = self.add(f"{logical}Attachment", "AWS::EC2::VPCGatewayAttachment",
                                                     {"VpcId": self.ref(attachment["VpcId"]), "VpnGatewayId": {"Ref": logical}})
        for gateway in self.raw.get("transit_gateways", []):
            gid = gateway["TransitGatewayId"]
            if gateway.get("State") in GONE:
                continue
            if gateway.get("OwnerId", self.account) != self.account:
                # Shared through RAM: owned by another account, so referenced by ID, never created here.
                self.ids[gid] = self.logical("TransitGatewayId", name_of(gateway), gid)
                self.param(self.ids[gid], "String", f"공유받은 Transit Gateway ID ({name_of(gateway, gid)}, 소유 계정 {gateway['OwnerId']})", Default=gid)
                self.assume(f"Transit Gateway {gid}는 다른 계정({gateway['OwnerId']}) 소유라 매개변수로 참조합니다. RAM 공유와 연결 수락은 소유 계정에서 처리하세요.")
                continue
            options = gateway.get("Options") or {}
            self.add(self.logical("TransitGateway", name_of(gateway), gid), "AWS::EC2::TransitGateway", {
                "Description": gateway.get("Description"), "AmazonSideAsn": options.get("AmazonSideAsn"),
                **{key: options.get(key) for key in ("AutoAcceptSharedAttachments", "DefaultRouteTableAssociation",
                                                    "DefaultRouteTablePropagation", "DnsSupport", "VpnEcmpSupport")},
                "Tags": self.tags(gateway)}, physical=gid)
        if self.raw.get("transit_gateways"):
            self.assume("Transit Gateway 라우팅 테이블은 수집 범위 밖이라 기본 연결·전파를 씁니다 (환경별 분리가 필요하면 추가하세요).")
        for attachment in self.raw.get("transit_gateway_attachments", []):
            aid, gateway = attachment["TransitGatewayAttachmentId"], attachment.get("TransitGatewayId")
            if attachment.get("State") in GONE:
                continue
            subnets = self.subnet_refs(attachment.get("SubnetIds", []))
            if attachment.get("VpcId") not in self.ids or not subnets or gateway not in self.ids:
                self.skip("TransitGatewayVpcAttachment", aid, "연결된 VPC·서브넷·Transit Gateway 중 템플릿에 없는 것이 있습니다 (다른 계정의 연결일 수 있음).",
                          name_of(attachment))
                continue
            logical = self.add(self.logical("TgwAttachment", name_of(attachment), aid), "AWS::EC2::TransitGatewayVpcAttachment", {
                "TransitGatewayId": self.ref(gateway), "VpcId": self.ref(attachment["VpcId"]), "SubnetIds": subnets,
                "Tags": self.tags(attachment)}, physical=aid)
            self.attachments[(gateway, attachment["VpcId"])] = logical
        for peering in self.raw.get("vpc_peering_connections", []):
            pid = peering["VpcPeeringConnectionId"]
            if (peering.get("Status") or {}).get("Code") in GONE:
                continue
            requester, accepter = ((peering.get(side) or {}).get("VpcId") for side in ("RequesterVpcInfo", "AccepterVpcInfo"))
            if requester in self.ids and accepter in self.ids:
                self.add(self.logical("VpcPeering", name_of(peering), pid), "AWS::EC2::VPCPeeringConnection",
                         {"VpcId": self.ref(requester), "PeerVpcId": self.ref(accepter), "Tags": self.tags(peering)}, physical=pid)
            elif requester in self.ids or accepter in self.ids:
                self.skip("VpcPeeringConnection", pid, "상대 VPC가 다른 계정·리전에 있어 수락 역할(PeerRoleArn) 없이 만들 수 없습니다.", name_of(peering))

    def nat_gateways(self):
        for gateway in self.raw.get("nat_gateways", []):
            gid = gateway["NatGatewayId"]
            if gateway.get("State") in GONE:
                continue
            if gateway.get("SubnetId") not in self.ids:
                self.skip("NatGateway", gid, "NAT 게이트웨이 서브넷이 템플릿에 없습니다.", name_of(gateway))
                continue
            logical = self.logical("NatGateway", name_of(gateway), gid)
            props = {"SubnetId": self.ref(gateway["SubnetId"]), "Tags": self.tags(gateway)}
            if gateway.get("ConnectivityType", "public") == "public":
                igw = next((self.attachments[g["InternetGatewayId"]] for g in self.raw.get("internet_gateways", [])
                            if g["InternetGatewayId"] in self.attachments and any(a.get("VpcId") == gateway.get("VpcId") for a in g.get("Attachments", []))), None)
                eip = self.add(f"{logical}Eip", "AWS::EC2::EIP", {"Domain": "vpc", "Tags": [{"Key": "Name", "Value": f"{name_of(gateway, gid)}-eip"}]},
                               depends=[igw] if igw else None)
                props["AllocationId"] = {"Fn::GetAtt": [eip, "AllocationId"]}
                self.assume("퍼블릭 NAT 게이트웨이마다 새 Elastic IP를 할당합니다 (수집된 공인 IP는 이어받지 않음).")
            else:
                props["ConnectivityType"] = "private"
            self.add(logical, "AWS::EC2::NatGateway", props, physical=gid)

    def security_group_resources(self):
        for group in self.raw.get("security_groups", []):
            gid = group["GroupId"]
            if group.get("VpcId") in self.default_vpcs:
                continue
            if group.get("VpcId") not in self.ids:
                self.skip("SecurityGroup", gid, "소속 VPC가 템플릿에 없습니다.", group.get("GroupName"))
                continue
            if group.get("GroupName") == "default":
                self.assume("VPC 기본 보안 그룹은 VPC와 함께 자동으로 생기며 이 템플릿이 규칙을 관리하지 않습니다.")
                continue
            egress = group.get("IpPermissionsEgress")
            props = {"GroupDescription": ascii_text(group.get("Description") or f"Diagram Bot {group.get('GroupName') or gid}")[:255],
                     "VpcId": self.ref(group["VpcId"]), "Tags": self.tags(group, group.get("GroupName"))}
            if egress is not None and not default_egress(egress):
                # CIDR egress inline replaces AWS's default allow-all; group references are added as separate resources.
                inline = [rule for permission in egress for rule in rules(permission, "egress") if "CidrIp" in rule or "CidrIpv6" in rule]
                if not inline:
                    inline = [{"IpProtocol": "-1", "CidrIp": "127.0.0.1/32", "Description": "Removes the default allow-all egress"}]
                    self.assume(f"보안 그룹 {group.get('GroupName') or gid}: 기본 전체 허용 아웃바운드를 없애려고 127.0.0.1/32 자리표시 규칙을 둡니다 (CloudFormation 관례).")
                props["SecurityGroupEgress"] = inline
            self.add(self.logical("SecurityGroup", group.get("GroupName"), gid), "AWS::EC2::SecurityGroup", props, physical=gid)

    def security_group_rules(self):
        for group in self.raw.get("security_groups", []):
            gid = group["GroupId"]
            if gid not in self.ids:
                continue
            owner = self.ids[gid]
            for direction, field in (("ingress", "IpPermissions"), ("egress", "IpPermissionsEgress")):
                if direction == "egress" and default_egress(group.get(field) or []):
                    continue
                for permission in group.get(field) or []:
                    for rule in rules(permission, direction):
                        if direction == "egress" and ("CidrIp" in rule or "CidrIpv6" in rule):
                            continue  # inline on the group
                        peer = rule.pop("PeerGroup", None)
                        if peer is not None:
                            reference = self.ref(peer) or (self.security_group(peer, None) if self.groups.get(peer, {}).get("GroupName") == "default" else None)
                            if not reference:
                                self.skip("SecurityGroupRule", gid, f"{direction} 규칙이 스냅샷에 없는 보안 그룹 {peer}를 참조합니다.", group.get("GroupName"))
                                continue
                            rule["SourceSecurityGroupId" if direction == "ingress" else "DestinationSecurityGroupId"] = reference
                        port = rule.get("FromPort", "All")
                        kind = "AWS::EC2::SecurityGroupIngress" if direction == "ingress" else "AWS::EC2::SecurityGroupEgress"
                        self.add(self.logical(owner + direction.title(), f"{rule['IpProtocol'].replace('-1', 'all')}{port}"), kind, {"GroupId": {"Ref": owner}, **rule})

    def route_tables(self):
        tables = self.raw.get("route_tables")
        if tables is None:
            if self.raw.get("subnets"):
                self.assume("라우팅 테이블을 수집하지 않아 VPC 기본 라우팅 테이블(로컬 경로만)을 그대로 씁니다.")
            return
        explicit = {a["SubnetId"] for t in tables for a in t.get("Associations", []) if a.get("SubnetId")}
        for table in tables:
            tid, vpc = table["RouteTableId"], table.get("VpcId")
            if vpc in self.default_vpcs:
                continue
            if vpc not in self.ids:
                self.skip("RouteTable", tid, "소속 VPC가 템플릿에 없습니다.", name_of(table))
                continue
            logical = self.add(self.logical("RouteTable", name_of(table), tid), "AWS::EC2::RouteTable",
                               {"VpcId": self.ref(vpc), "Tags": self.tags(table)}, physical=tid)
            subnets = [a["SubnetId"] for a in table.get("Associations", []) if a.get("SubnetId")]
            if any(a.get("Main") for a in table.get("Associations", [])):
                # CloudFormation cannot replace a VPC's main table: subnets that used it implicitly get an explicit association.
                implicit = [s for s, item in self.subnets.items() if item.get("VpcId") == vpc and s not in explicit]
                if implicit:
                    self.assume("기본(main) 라우팅 테이블은 CloudFormation이 지정할 수 없어, 암묵적으로 쓰던 서브넷을 명시적으로 연결합니다.")
                subnets += implicit
            for subnet in subnets:
                if subnet in self.ids:
                    self.add(self.logical(logical + "Assoc", self.ids[subnet].removeprefix("Subnet")), "AWS::EC2::SubnetRouteTableAssociation",
                             {"RouteTableId": {"Ref": logical}, "SubnetId": self.ref(subnet)})
            for association in table.get("Associations", []):
                if association.get("GatewayId") in self.ids:
                    self.add(self.logical(logical + "EdgeAssoc", association["GatewayId"]), "AWS::EC2::GatewayRouteTableAssociation",
                             {"RouteTableId": {"Ref": logical}, "GatewayId": self.ref(association["GatewayId"])})
            for route in table.get("Routes", []):
                self.route(table, logical, route)
            for propagation in table.get("PropagatingVgws") or []:
                gateway = propagation.get("GatewayId")
                if gateway in self.ids:
                    self.add(self.logical(logical + "Propagation", gateway), "AWS::EC2::VPNGatewayRoutePropagation",
                             {"RouteTableIds": [{"Ref": logical}], "VpnGatewayId": self.ref(gateway)},
                             depends=[self.attachments[gateway]] if gateway in self.attachments else None)

    def route(self, table, logical, route):
        if route.get("GatewayId") == "local" or route.get("Origin") in ("CreateRouteTable", "EnableVgwRoutePropagation"):
            return
        destination = {k: route[k] for k in ("DestinationCidrBlock", "DestinationIpv6CidrBlock", "DestinationPrefixListId") if route.get(k)}
        shown = next(iter(destination.values()), "?")
        gateway = str(route.get("GatewayId") or "")
        if gateway.startswith("vpce-"):
            if gateway not in {e["VpcEndpointId"] for e in self.raw.get("vpc_endpoints", [])}:
                self.skip("Route", f"{table['RouteTableId']} {shown}", f"경로 대상 엔드포인트 {gateway}가 스냅샷에 없습니다.")
            return  # a gateway endpoint adds its own prefix-list routes through RouteTableIds
        if route.get("State") == "blackhole":
            self.skip("Route", f"{table['RouteTableId']} {shown}", "대상이 사라진 blackhole 경로입니다.")
            return
        depends = None
        if gateway.startswith(("igw-", "vgw-")):
            target, field = gateway, "GatewayId"
            depends = [self.attachments[gateway]] if gateway in self.attachments else None
        elif route.get("NatGatewayId"):
            target, field = route["NatGatewayId"], "NatGatewayId"
        elif route.get("TransitGatewayId"):
            target, field = route["TransitGatewayId"], "TransitGatewayId"
            attachment = self.attachments.get((target, table.get("VpcId")))
            if not attachment:
                self.skip("Route", f"{table['RouteTableId']} {shown}", f"이 VPC의 Transit Gateway {target} 연결이 스냅샷에 없습니다.")
                return
            depends = [attachment]
        elif route.get("VpcPeeringConnectionId"):
            target, field = route["VpcPeeringConnectionId"], "VpcPeeringConnectionId"
        else:
            other = next((k for k in ("EgressOnlyInternetGatewayId", "NetworkInterfaceId", "InstanceId", "CarrierGatewayId", "LocalGatewayId",
                                      "CoreNetworkArn", "VpcEndpointId") if route.get(k)), gateway or "알 수 없음")
            self.skip("Route", f"{table['RouteTableId']} {shown}", f"지원하지 않는 경로 대상입니다 ({other}).")
            return
        if target not in self.ids:
            self.skip("Route", f"{table['RouteTableId']} {shown}", f"경로 대상 {target}가 템플릿에 없습니다.")
            return
        self.add(self.logical(logical + "Route", shown.replace("0.0.0.0/0", "Default").replace("/", "m")), "AWS::EC2::Route",
                 {"RouteTableId": {"Ref": logical}, **destination, field: self.ref(target)}, depends=depends)

    def endpoints(self):
        for endpoint in self.raw.get("vpc_endpoints", []):
            eid, kind = endpoint["VpcEndpointId"], endpoint.get("VpcEndpointType", "Gateway")
            if endpoint.get("State") in GONE:
                continue
            service = endpoint.get("ServiceName", "")
            prefix = f"com.amazonaws.{self.region}."
            short = service.removeprefix(prefix) if service.startswith(prefix) else None
            if endpoint.get("VpcId") not in self.ids or kind not in ("Gateway", "Interface"):
                self.skip("VpcEndpoint", eid, "VPC가 템플릿에 없거나 Gateway Load Balancer 엔드포인트입니다.", service)
                continue
            props = {"VpcId": self.ref(endpoint["VpcId"]), "VpcEndpointType": kind,
                     # AWS service names follow the stack's region; PrivateLink partner services keep their own name.
                     "ServiceName": {"Fn::Sub": f"com.amazonaws.${{AWS::Region}}.{short}"} if short else service}
            if endpoint.get("PolicyDocument"):
                try:
                    props["PolicyDocument"] = json.loads(endpoint["PolicyDocument"])
                except (TypeError, ValueError):
                    pass
            if kind == "Gateway":
                props["RouteTableIds"] = [self.ref(t) for t in endpoint.get("RouteTableIds", []) if t in self.ids]
            else:
                subnets = self.subnet_refs(endpoint.get("SubnetIds", []))
                if not subnets:
                    self.skip("VpcEndpoint", eid, "엔드포인트 서브넷이 템플릿에 없습니다.", service)
                    continue
                props.update(SubnetIds=subnets, PrivateDnsEnabled=endpoint.get("PrivateDnsEnabled"),
                             SecurityGroupIds=self.security_groups([g.get("GroupId") for g in endpoint.get("Groups", [])], endpoint["VpcId"]))
            # Same service in several VPCs: the VPC's logical ID tells them apart.
            label = short if short and "Endpoint" + camel(short) not in self.resources else f"{self.ids[endpoint['VpcId']]} {short or name_of(endpoint, eid)}"
            self.add(self.logical("Endpoint", label, eid), "AWS::EC2::VPCEndpoint", props, physical=eid)

    def placement_groups(self):
        for group in self.raw.get("placement_groups", []):
            if group.get("State") in GONE:
                continue
            self.add(self.logical("PlacementGroup", group["GroupName"]), "AWS::EC2::PlacementGroup", {
                "Strategy": group.get("Strategy"), "PartitionCount": group.get("PartitionCount"), "SpreadLevel": group.get("SpreadLevel")},
                physical="placement:" + group["GroupName"])

    # --- load balancing ----------------------------------------------------------------------------
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

    # --- compute -------------------------------------------------------------------------------
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
            template = self.add(f"{logical}LaunchTemplate", "AWS::EC2::LaunchTemplate", {"LaunchTemplateData": data})
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
                template = self.add(f"{logical}LaunchTemplate", "AWS::EC2::LaunchTemplate", {"LaunchTemplateData": settings})
                props = {"LaunchTemplate": {"LaunchTemplateId": {"Ref": template}, "Version": {"Fn::GetAtt": [template, "LatestVersionNumber"]}}}
            else:
                props = {**settings, "SubnetId": self.ref(instance["SubnetId"]), "PlacementGroupName": placement}
            self.add(logical, "AWS::EC2::Instance", {**props, "Tags": self.tags(instance)}, physical=iid)

    # --- data stores ---------------------------------------------------------------------------
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
            if group.get("ClusterEnabled"):
                props.update(NumNodeGroups=len(shards), ReplicasPerNodeGroup=max(len(s.get("NodeGroupMembers", [])) for s in shards) - 1,
                             NodeGroupConfiguration=[{"NodeGroupId": s.get("NodeGroupId"),
                                                      "PrimaryAvailabilityZone": next((zone(m) for m in s["NodeGroupMembers"] if m.get("CurrentRole") == "primary"), None),
                                                      "ReplicaAvailabilityZones": [zone(m) for m in s["NodeGroupMembers"] if m.get("CurrentRole") != "primary"]}
                                                     for s in shards])
            else:
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
            if (info.get("CurrentBrokerSoftwareInfo") or {}).get("KafkaVersion", "").endswith(".x"):
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
            image = self.param(f"{logical}Image", "String", f"ECS 서비스 {name}의 컨테이너 이미지 URI (작업 정의는 수집하지 않음)")
            definition = self.add(f"{logical}TaskDefinition", "AWS::ECS::TaskDefinition", {
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

    # --- hybrid --------------------------------------------------------------------------------
    def vpn(self):
        used = {c.get("CustomerGatewayId") for c in self.raw.get("vpn_connections", []) if c.get("State") not in GONE}
        for gateway in self.raw.get("customer_gateways", []):
            gid = gateway["CustomerGatewayId"]
            if gateway.get("State") in GONE or (gid not in used and not gateway.get("IpAddress")):
                continue
            self.add(self.logical("CustomerGateway", name_of(gateway), gid), "AWS::EC2::CustomerGateway", {
                "Type": gateway.get("Type", "ipsec.1"), "IpAddress": gateway.get("IpAddress"), "BgpAsn": int(gateway.get("BgpAsn") or 65000),
                "Tags": self.tags(gateway)}, physical=gid)
        for connection in self.raw.get("vpn_connections", []):
            cid = connection["VpnConnectionId"]
            if connection.get("State") in GONE:
                continue
            hub = connection.get("VpnGatewayId") or connection.get("TransitGatewayId")
            if connection.get("CustomerGatewayId") not in self.ids or hub not in self.ids:
                self.skip("VpnConnection", cid, "고객 게이트웨이 또는 가상 프라이빗 게이트웨이·Transit Gateway가 템플릿에 없습니다.", name_of(connection))
                continue
            static = (connection.get("Options") or {}).get("StaticRoutesOnly")
            logical = self.add(self.logical("VpnConnection", name_of(connection), cid), "AWS::EC2::VPNConnection", {
                "Type": connection.get("Type", "ipsec.1"), "CustomerGatewayId": self.ref(connection["CustomerGatewayId"]),
                "VpnGatewayId" if connection.get("VpnGatewayId") else "TransitGatewayId": self.ref(hub),
                "StaticRoutesOnly": static, "Tags": self.tags(connection)}, physical=cid)
            for route in connection.get("Routes", []) if static else []:
                if route.get("DestinationCidrBlock") and route.get("State") not in GONE:
                    self.add(self.logical(logical + "Route", route["DestinationCidrBlock"].replace("/", "m")), "AWS::EC2::VPNConnectionRoute",
                             {"VpnConnectionId": {"Ref": logical}, "DestinationCidrBlock": route["DestinationCidrBlock"]})
            self.assume("VPN 터널 사전 공유 키·내부 CIDR은 수집하지 않아 AWS가 생성합니다. 고객 게이트웨이 장비 설정을 새로 받으세요.")

    def not_expressible(self):
        for function in self.raw.get("lambda_functions", []):
            self.skip("LambdaFunction", function.get("FunctionName"), "함수 코드 패키지와 실행 역할은 조회하지 않습니다 (SAM·CDK 등 배포 파이프라인에서 관리).")
        for endpoint in self.raw.get("sagemaker_endpoints", []):
            self.skip("SageMakerEndpoint", endpoint.get("EndpointName"), "모델 아티팩트·컨테이너 이미지·실행 역할을 조회하지 않습니다.")
        for cluster in self.raw.get("sagemaker_clusters", []):
            self.skip("SageMakerHyperPodCluster", cluster.get("ClusterName"), "수명 주기 스크립트(S3)와 실행 역할을 조회하지 않습니다.")


def clean(value):
    """Drop unset (None) fields at every depth: optional API fields map to optional template properties."""
    if isinstance(value, dict):
        return {k: clean(v) for k, v in value.items() if v is not None}
    if isinstance(value, list):
        return [clean(v) for v in value if v is not None]
    return value


def ascii_text(text):
    """Security group descriptions accept ASCII letters, digits, spaces and ._-:/()#,@[]+=&;{}!$* only."""
    cleaned = re.sub(r"[^A-Za-z0-9 ._\-:/()#,@\[\]+=&;{}!$*]", "", str(text)).strip()
    return re.sub(r"\s+", " ", cleaned) or "Diagram Bot"


def default_egress(permissions):
    return len(permissions) == 1 and permissions[0].get("IpProtocol") == "-1" and [r.get("CidrIp") for r in permissions[0].get("IpRanges", [])] == ["0.0.0.0/0"] \
        and not permissions[0].get("UserIdGroupPairs") and not permissions[0].get("Ipv6Ranges") and not permissions[0].get("PrefixListIds")


def rules(permission, direction):
    """One CloudFormation rule per source of an API permission; group references come back as PeerGroup for the caller."""
    base = {"IpProtocol": str(permission.get("IpProtocol", "-1"))}
    if base["IpProtocol"] != "-1" and permission.get("FromPort") is not None:
        base.update(FromPort=permission["FromPort"], ToPort=permission.get("ToPort", permission["FromPort"]))
    out = []
    for item in permission.get("IpRanges", []):
        out.append({**base, "CidrIp": item["CidrIp"], **({"Description": item["Description"]} if item.get("Description") else {})})
    for item in permission.get("Ipv6Ranges", []):
        out.append({**base, "CidrIpv6": item["CidrIpv6"], **({"Description": item["Description"]} if item.get("Description") else {})})
    for item in permission.get("PrefixListIds", []):
        out.append({**base, ("SourcePrefixListId" if direction == "ingress" else "DestinationPrefixListId"): item["PrefixListId"]})
    for item in permission.get("UserIdGroupPairs", []):
        out.append({**base, "PeerGroup": item.get("GroupId"), **({"Description": item["Description"]} if item.get("Description") else {})})
    return out


def template(snapshot):
    """CloudFormation template for one account and region (a combined snapshot needs templates())."""
    if snapshot.get("parts"):
        raise ValueError("여러 계정·리전을 합친 스냅샷입니다. 계정·리전마다 템플릿을 따로 만드세요 (templates).")
    return Template(snapshot).build()


def templates(snapshot):
    """[(label, template)]: one per account × region part of a combined snapshot, else one."""
    parts = snapshot.get("parts") or [snapshot]
    return [(f"{p['metadata'].get('account_id')}-{p['metadata'].get('region')}", Template(p).build()) for p in parts]


def dumps(value):
    return json.dumps(value, ensure_ascii=False, indent=2) + "\n"
