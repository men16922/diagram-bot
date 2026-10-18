"""VPCs, subnets, gateways, security groups, route tables, endpoints and placement groups; VPN, Transit Gateway routing
and Elastic IPs."""
import json

from .base import GONE, ascii_text, camel, name_of


class Network:
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
                    self.attachments[gid] = self.add(self.unique(f"{logical}Attachment"), "AWS::EC2::VPCGatewayAttachment",
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
                    self.attachments[gid] = self.add(self.unique(f"{logical}Attachment"), "AWS::EC2::VPCGatewayAttachment",
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
        if self.raw.get("transit_gateways") and not self.raw.get("transit_gateway_route_tables"):
            self.assume("Transit Gateway 라우팅 테이블이 수집되지 않아 기본 연결·전파를 씁니다 (환경별 분리가 필요하면 추가하세요).")
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
                eip = self.add(self.unique(f"{logical}Eip"), "AWS::EC2::EIP", {"Domain": "vpc", "Tags": [{"Key": "Name", "Value": f"{name_of(gateway, gid)}-eip"}]},
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

    def tgw_routing(self):
        """Transit Gateway route tables (routing domains): their VPC associations and propagations and static routes.
        Propagated routes follow from the propagations; the gateway's own default table is created by the gateway."""
        tables = {}
        for table in self.raw.get("transit_gateway_route_tables", []):
            tid, gateway = table["TransitGatewayRouteTableId"], table.get("TransitGatewayId")
            if table.get("State") in GONE:
                continue
            if table.get("DefaultAssociationRouteTable") or table.get("DefaultPropagationRouteTable"):
                self.assume(f"Transit Gateway 기본 라우팅 테이블 {name_of(table, tid)}은 게이트웨이가 만들며 템플릿에서 참조할 수 없어, 그 연결·전파는 게이트웨이 기본값을 따릅니다.")
                continue
            if gateway not in self.ids or self.ids[gateway] in self.parameters:
                self.skip("TransitGatewayRouteTable", tid, "Transit Gateway가 템플릿에 없거나 다른 계정 소유입니다 (라우팅 테이블은 소유 계정에서 관리).", name_of(table))
                continue
            tables[tid] = self.add(self.logical("TgwRouteTable", name_of(table), tid), "AWS::EC2::TransitGatewayRouteTable",
                                   {"TransitGatewayId": self.ref(gateway), "Tags": self.tags(table)}, physical=tid)
        for kind, key, resource in (("Association", "transit_gateway_route_table_associations", "AWS::EC2::TransitGatewayRouteTableAssociation"),
                                    ("Propagation", "transit_gateway_route_table_propagations", "AWS::EC2::TransitGatewayRouteTablePropagation")):
            for entry in self.raw.get(key, []):
                tid, aid = entry.get("TransitGatewayRouteTableId"), entry.get("TransitGatewayAttachmentId")
                if tid not in tables or entry.get("State") in GONE + ("disassociated", "disabled"):
                    continue
                if aid not in self.ids:
                    # VPN and Direct Connect attachments are created implicitly; CloudFormation does not return their IDs.
                    self.skip(f"TransitGatewayRouteTable{kind}", f"{tid}/{aid}", f"{entry.get('ResourceType', '연결')} 연결의 TGW 연결 ID를 템플릿에서 참조할 수 없습니다 "
                              "(VPN·Direct Connect 연결은 배포 후 라우팅 테이블에 연결하세요).")
                    continue
                self.add(self.logical(tables[tid] + kind, name_of(self.attachment_item(aid), aid)), resource,
                         {"TransitGatewayAttachmentId": self.ref(aid), "TransitGatewayRouteTableId": {"Ref": tables[tid]}})
        for route in self.raw.get("transit_gateway_routes", []):
            tid, destination = route.get("TransitGatewayRouteTableId"), route.get("DestinationCidrBlock")
            if tid not in tables or route.get("Type") != "static" or not destination:
                continue
            props = {"TransitGatewayRouteTableId": {"Ref": tables[tid]}, "DestinationCidrBlock": destination}
            if route.get("State") == "blackhole":
                props["Blackhole"] = True
            else:
                targets = [t.get("TransitGatewayAttachmentId") for t in route.get("TransitGatewayAttachments", [])]
                if len(targets) != 1 or targets[0] not in self.ids:
                    self.skip("TransitGatewayRoute", f"{tid}/{destination}", "정적 경로의 대상 연결이 템플릿에 없습니다.")
                    continue
                props["TransitGatewayAttachmentId"] = self.ref(targets[0])
            self.add(self.logical(tables[tid] + "Route", destination.replace("/", "m")), "AWS::EC2::TransitGatewayRoute", props)

    def attachment_item(self, aid):
        return next((a for a in self.raw.get("transit_gateway_attachments", []) if a.get("TransitGatewayAttachmentId") == aid), {})

    def elastic_ips(self):
        """NAT and NLB addresses are re-allocated with their owners; an instance's address gets a new EIP and association."""
        owned = {a.get("AllocationId") for g in self.raw.get("nat_gateways", []) for a in g.get("NatGatewayAddresses", [])} \
            | {a.get("AllocationId") for b in self.raw.get("load_balancers", []) for z in b.get("AvailabilityZones", [])
               for a in z.get("LoadBalancerAddresses", [])}
        for address in self.raw.get("elastic_ips", []):
            allocation = address.get("AllocationId") or address.get("PublicIp")
            if allocation in owned:
                continue
            instance = address.get("InstanceId")
            if instance and instance in self.ids:
                logical = self.add(self.logical("Eip", name_of(address, ""), instance), "AWS::EC2::EIP",
                                   {"Domain": "vpc", "Tags": self.tags(address)}, physical=allocation)
                self.add(self.unique(f"{logical}Association"), "AWS::EC2::EIPAssociation",
                         {"AllocationId": {"Fn::GetAtt": [logical, "AllocationId"]}, "InstanceId": self.ref(instance)})
                self.assume("인스턴스의 Elastic IP는 새로 할당합니다 (수집된 공인 IP 주소는 이어받지 않음; 같은 주소가 필요하면 기존 할당을 연결하세요).")
            elif not (address.get("AssociationId") or address.get("NetworkInterfaceId")):
                self.skip("ElasticIp", allocation, "어디에도 연결되지 않은 Elastic IP입니다 (요금만 발생; COST-EIP-IDLE).", address.get("PublicIp"))
            else:
                self.skip("ElasticIp", allocation, "템플릿에 없는 네트워크 인터페이스·인스턴스에 연결된 주소입니다.", address.get("PublicIp"))


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

