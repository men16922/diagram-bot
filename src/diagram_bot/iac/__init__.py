"""Snapshot (design, sample or collection) → CloudFormation template draft (JSON).

The template recreates what the snapshot's API-shaped `raw` data states, with Ref/GetAtt between the
resources it creates; collected physical IDs never appear for those. What the APIs do not return
(AMIs, container images, IAM roles, certificates, listener rules, passwords) becomes a Parameter or a
documented assumption, and whatever cannot be expressed faithfully is listed in
Metadata.DiagramBot.Unsupported instead of being invented. A draft for review, never applied by the app.
"""
import json

from .balancing import Balancing
from .base import GONE, GRAVITON, ROLE_PATTERN, Base, ascii_text, camel, clean, name_of
from .compute import Compute
from .data import Data
from .network import Network, default_egress, rules

__all__ = ["GONE", "GRAVITON", "PSEUDO", "ROLE_PATTERN", "STATEFUL", "Template", "ascii_text", "camel", "clean", "default_egress", "dumps", "name_of",
           "rules", "template", "templates"]

PSEUDO = {"AWS::AccountId", "AWS::NoValue", "AWS::Partition", "AWS::Region", "AWS::StackId", "AWS::StackName", "AWS::URLSuffix"}
STATEFUL = {"AWS::RDS::DBInstance": "Snapshot", "AWS::RDS::DBCluster": "Snapshot", "AWS::DocDB::DBCluster": "Snapshot", "AWS::DocDB::DBInstance": "Delete",
            "AWS::ElastiCache::ReplicationGroup": "Snapshot", "AWS::ElastiCache::CacheCluster": "Snapshot", "AWS::Redshift::Cluster": "Snapshot",
            "AWS::EFS::FileSystem": "Retain", "AWS::FSx::FileSystem": "Retain", "AWS::OpenSearchService::Domain": "Retain", "AWS::MSK::Cluster": "Retain"}


class Template(Network, Balancing, Compute, Data, Base):
    def build(self):
        # One ordered list across the mixins: a step refers (ref) only to resources earlier steps added (groups before routes,
        # instances before the targets that register them), and resource order and numbered logical IDs follow it.
        for step in (self.network, self.gateways, self.nat_gateways, self.security_group_resources, self.route_tables, self.endpoints,
                     self.security_group_rules, self.placement_groups, self.load_balancers, self.auto_scaling, self.standalone_instances,
                     self.targets, self.databases, self.caches, self.kafka, self.search, self.file_systems, self.warehouse, self.kubernetes,
                     self.containers, self.vpn, self.tgw_routing, self.elastic_ips, self.not_expressible):
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

    def not_expressible(self):
        for distribution in self.raw.get("cloudfront_distributions", []):
            self.skip("CloudFrontDistribution", distribution.get("Id"), "전역 서비스라 리전 템플릿과 따로 배포합니다 (ACM 인증서는 us-east-1). "
                      "캐시 정책·동작 전체는 수집하지 않습니다.", (distribution.get("Aliases") or {}).get("Items", [None])[0])
        for acl in self.raw.get("waf_web_acls", []):
            self.skip("WafWebAcl", acl.get("ARN") or acl.get("Name"), "규칙 본문(GetWebACL)은 수집하지 않습니다. 보호 대상 연결만 구성도에 있습니다.", acl.get("Name"))
        for connection in self.raw.get("dx_connections", []):
            self.skip("DirectConnectConnection", connection.get("connectionId"), "물리 회선은 Direct Connect 위치에서 주문·교차 연결하며 CloudFormation으로 만들 수 없습니다.",
                      connection.get("connectionName"))
        for interface in self.raw.get("dx_virtual_interfaces", []):
            self.skip("DirectConnectVirtualInterface", interface.get("virtualInterfaceId"), "가상 인터페이스는 회선에 종속되어 CloudFormation 리소스가 없습니다.",
                      interface.get("virtualInterfaceName"))
        for gateway in self.raw.get("dx_gateways", []):
            self.skip("DirectConnectGateway", gateway.get("directConnectGatewayId"), "Direct Connect 게이트웨이는 CloudFormation 리소스가 없습니다 (콘솔·CLI로 만들고 연결).",
                      gateway.get("directConnectGatewayName"))
        for function in self.raw.get("lambda_functions", []):
            self.skip("LambdaFunction", function.get("FunctionName"), "함수 코드 패키지와 실행 역할은 조회하지 않습니다 (SAM·CDK 등 배포 파이프라인에서 관리).")
        for endpoint in self.raw.get("sagemaker_endpoints", []):
            self.skip("SageMakerEndpoint", endpoint.get("EndpointName"), "모델 아티팩트·컨테이너 이미지·실행 역할을 조회하지 않습니다.")
        for cluster in self.raw.get("sagemaker_clusters", []):
            self.skip("SageMakerHyperPodCluster", cluster.get("ClusterName"), "수명 주기 스크립트(S3)와 실행 역할을 조회하지 않습니다.")


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
