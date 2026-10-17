"""Standard infrastructure designs built from intent-level steps (the chat agent's drawing tools).

A design is written in the same API-shaped `raw` structure the collector produces, so the existing
graph, layout, review and export pipeline renders it exactly like a collected snapshot. Nothing here
calls AWS; a design is a proposal, never a description of a real account.
"""
import ipaddress
import re

from .findings import review, summary
from .graph import build_graph
from .samples import SAMPLES, Sample

AZ_LETTERS = "abcdef"
TIERS = ("public", "web", "app", "data")
STANDARDS = {
    "three-tier": "3계층 웹 (ALB → 웹 ASG → 내부 ALB → 앱 ASG → RDS Multi-AZ, 3 AZ)",
    "microservices": "마이크로서비스 (ALB 경로 라우팅, EC2 ASG + ECS Fargate, RDS Multi-AZ)",
    "nlb-alb": "고정 IP 진입 (NLB → ALB, 2 AZ)",
    "environments": "운영·스테이징 VPC 분리",
    "hybrid": "하이브리드 (Transit Gateway + Site-to-Site VPN)",
    "eks-platform": "EKS 플랫폼 (EKS, Aurora, Redis, MSK, Lambda, 엔드포인트)",
    "data-platform": "데이터 플랫폼 (ECS, OpenSearch, RDS, Redis, EFS, Redshift, 피어링, VPN)",
}


class DesignError(ValueError):
    """A step the design cannot apply; the message is shown to the model and the user."""


def slug(text):
    value = re.sub(r"[^a-z0-9-]+", "-", str(text).lower()).strip("-")
    if not value:
        raise DesignError("이름에는 영문 소문자·숫자·하이픈을 쓰세요.")
    return value[:40]


class Design:
    def __init__(self, title="표준 아키텍처 설계", region="ap-northeast-2"):
        self.reset(title, region)

    def reset(self, title="표준 아키텍처 설계", region="ap-northeast-2"):
        if not re.fullmatch(r"[a-z]{2}(?:-[a-z0-9]+)+-\d+", region or ""):
            raise DesignError(f"리전 형식이 아닙니다: {region}")
        self.title, self.region = title, region
        self.sample = Sample("design", title, [], region=region, account="000000000000")
        self.vpcs, self.steps, self.standard = {}, [], None
        self.target_groups, self.load_balancers = {}, {}

    # --- state ---------------------------------------------------------------------------------
    def vpc(self, name):
        key = slug(name)
        if key not in self.vpcs:
            known = ", ".join(self.vpcs) or "없음"
            raise DesignError(f"VPC '{name}'가 설계에 없습니다. 먼저 add_vpc로 만드세요. (현재: {known})")
        return self.vpcs[key]

    def tier(self, vpc, tier):
        subnets = vpc["tiers"].get(tier)
        if not subnets:
            raise DesignError(f"VPC '{vpc['name']}'에 '{tier}' 계층 서브넷이 없습니다. (계층: {', '.join(vpc['tiers'])})")
        return subnets

    def record(self, step, **params):
        self.steps.append({"step": step, **{k: v for k, v in params.items() if v not in (None, "", [], ())}})

    def unique(self, kind, name):
        key = slug(name)
        taken = {"lb": self.load_balancers, "tg": self.target_groups}.get(kind)
        if taken is not None and key in taken:
            raise DesignError(f"이미 있는 이름입니다: {name}")
        return key

    # --- network -------------------------------------------------------------------------------
    def add_vpc(self, name, cidr="10.0.0.0/16", azs=3, tiers=("public", "app", "data"), nat="per_az"):
        key = slug(name)
        if key in self.vpcs:
            raise DesignError(f"이미 있는 VPC입니다: {name}")
        try:
            network = ipaddress.ip_network(cidr, strict=True)
        except ValueError as exc:
            raise DesignError(f"CIDR 형식이 아닙니다: {cidr}") from exc
        if network.version != 4 or not 16 <= network.prefixlen <= 20:
            raise DesignError("VPC CIDR은 /16~/20 IPv4로 지정하세요.")
        if any(network.overlaps(ipaddress.ip_network(v["cidr"])) for v in self.vpcs.values()):
            raise DesignError(f"다른 VPC와 CIDR이 겹칩니다: {cidr}")
        if not 1 <= int(azs) <= 6:
            raise DesignError("AZ 수는 1~6입니다.")
        tiers = [t for t in TIERS if t in set(tiers)]
        if not tiers:
            raise DesignError(f"계층은 {', '.join(TIERS)} 중에서 고르세요.")
        if nat not in ("per_az", "single", "none"):
            raise DesignError("nat은 per_az, single, none 중 하나입니다.")
        s, letters = self.sample, AZ_LETTERS[:int(azs)]
        blocks = network.subnets(new_prefix=24)
        vpc_id = s.vpc(f"vpc-{key}", cidr, name, Environment="design")
        layers = {}
        for tier in tiers:
            layers[tier] = [s.subnet(f"subnet-{key}-{tier}-{az}", vpc_id, az, str(next(blocks)), f"{key}-{tier}-{az}", tier)
                            for az in letters]
        private = [layers[t] for t in ("web", "app") if t in layers]
        if "public" in layers:
            igw = s.internet_gateway(f"igw-{key}", vpc_id, f"{key}-igw")
            s.route_table(f"rtb-{key}-public", vpc_id, f"{key}-public", layers["public"], gateway=igw)
            nats = []
            if nat != "none":
                nats = [s.nat_gateway(f"nat-{key}-{az}", vpc_id, layers["public"][i], f"{key}-nat-{az}")
                        for i, az in enumerate(letters) if nat == "per_az" or i == 0]
            for i, az in enumerate(letters):
                if private:
                    s.route_table(f"rtb-{key}-private-{az}", vpc_id, f"{key}-private-{az}", [p[i] for p in private],
                                  nat=(nats[i] if nat == "per_az" else nats[0]) if nats else None)
        # The data tier (and private subnets without NAT) keep the main table: local route only.
        s.route_table(f"rtb-{key}-main", vpc_id, f"{key}-isolated", main=True)
        self.vpcs[key] = {"name": name, "key": key, "id": vpc_id, "cidr": cidr, "azs": letters, "tiers": layers,
                          "private_tables": [f"rtb-{key}-private-{az}" for az in letters] if "public" in layers and private else [],
                          "sgs": set()}
        self.record("add_vpc", name=name, cidr=cidr, azs=int(azs), tiers=tiers, nat=nat)
        return {"vpc": name, "subnets": {t: len(v) for t, v in layers.items()}, "azs": [f"{self.region}{a}" for a in letters],
                "nat_gateways": 0 if nat == "none" or "public" not in layers else (len(letters) if nat == "per_az" else 1)}

    def security_group(self, vpc, name, ingress):
        key = f"sg-{vpc['key']}-{slug(name)}"
        if key not in vpc["sgs"]:
            self.sample.security_group(key, vpc["id"], f"{vpc['key']}-{slug(name)}", ingress)
            vpc["sgs"].add(key)
        return key

    def add_load_balancer(self, vpc, name, kind="application", scheme="internet-facing"):
        v = self.vpc(vpc)
        key = self.unique("lb", name)
        if kind not in ("application", "network"):
            raise DesignError("kind는 application 또는 network입니다.")
        if scheme not in ("internet-facing", "internal"):
            raise DesignError("scheme은 internet-facing 또는 internal입니다.")
        subnets = self.tier(v, "public") if scheme == "internet-facing" else self.tier(v, "app" if "app" in v["tiers"] else "web")
        source = "0.0.0.0/0" if scheme == "internet-facing" else v["cidr"]
        sg = self.security_group(v, f"{key}-lb", [(443, source)]) if kind == "application" else None
        arn = self.sample.load_balancer(key, v["id"], subnets, kind, scheme, sg=sg)
        self.load_balancers[key] = {"arn": arn, "vpc": v["key"], "sg": sg, "kind": kind}
        self.record("add_load_balancer", vpc=vpc, name=name, kind=kind, scheme=scheme)
        return {"load_balancer": key, "type": kind, "scheme": scheme, "subnets": len(subnets)}

    def balancer(self, v, name):
        if not name:
            return None
        lb = self.load_balancers.get(slug(name))
        if not lb or lb["vpc"] != v["key"]:
            raise DesignError(f"VPC '{v['name']}'에 로드 밸런서 '{name}'가 없습니다. 먼저 add_load_balancer로 만드세요.")
        return lb

    def add_ec2_service(self, vpc, name, tier="app", instance_type="m6i.large", per_az=1, load_balancer=None, port=8080,
                        autoscaling=True):
        v = self.vpc(vpc)
        key = self.unique("tg", name)
        subnets = self.tier(v, tier)
        lb = self.balancer(v, load_balancer)
        if not 1 <= int(per_az) <= 10:
            raise DesignError("AZ당 인스턴스 수는 1~10입니다.")
        sg = self.security_group(v, key, [(int(port), lb["sg"] or v["cidr"]) if lb else (int(port), v["cidr"])])
        instances = []
        for i, subnet in enumerate(subnets):
            for n in range(int(per_az)):
                instances.append(self.sample.instance(f"i-{key}-{v['azs'][i]}{n + 1}", subnet, f"{key}-{v['azs'][i]}{n + 1}",
                                                      instance_type, ip=self.sample.address(subnet, 10 + len(instances)), sg=sg))
        groups = []
        if lb:
            groups.append(self.sample.target_group(key, v["id"], "HTTP", int(port), instances, [lb["arn"]]))
            self.target_groups[key] = groups[0]
        if autoscaling:
            self.sample.auto_scaling_group(f"{key}-asg", instances, subnets, groups, len(instances), len(instances) * 3)
        self.record("add_ec2_service", vpc=vpc, name=name, tier=tier, instance_type=instance_type, per_az=int(per_az),
                    load_balancer=load_balancer, port=int(port), autoscaling=autoscaling)
        return {"service": key, "instances": len(instances), "auto_scaling": bool(autoscaling), "load_balancer": load_balancer}

    def add_ecs_service(self, vpc, name, tasks_per_az=1, load_balancer=None, port=8080, tier="app", cluster=None):
        v = self.vpc(vpc)
        key = self.unique("tg", name)
        subnets = self.tier(v, tier)
        lb = self.balancer(v, load_balancer)
        tasks = [(subnet, self.sample.address(subnet, 100 + n * len(subnets) + i + 7 * len(self.sample.raw["ecs_tasks"])))
                 for n in range(int(tasks_per_az)) for i, subnet in enumerate(subnets)]
        group = None
        if lb:
            group = self.sample.target_group(key, v["id"], "HTTP", int(port), [(ip, int(port), self.sample.az_of(s)) for s, ip in tasks],
                                             [lb["arn"]], target_type="ip")
            self.target_groups[key] = group
        self.sample.ecs_service(slug(cluster or f"{v['key']}-cluster"), key, v["id"], tasks, group, int(port))
        self.record("add_ecs_service", vpc=vpc, name=name, tasks_per_az=int(tasks_per_az), load_balancer=load_balancer, port=int(port))
        return {"service": key, "tasks": len(tasks), "launch": "FARGATE", "load_balancer": load_balancer}

    def add_eks_cluster(self, vpc, name, node_type="m6i.xlarge", nodes_per_az=1, tier="app"):
        v = self.vpc(vpc)
        key = slug(name)
        subnets = self.tier(v, tier)
        sg = self.security_group(v, f"{key}-nodes", [(443, v["cidr"])])
        nodes = [self.sample.instance(f"i-{key}-node-{v['azs'][i]}{n + 1}", subnet, f"{key}-node-{v['azs'][i]}{n + 1}", node_type, sg=sg,
                                      **{"eks:cluster-name": key, "eks:nodegroup-name": "general"})
                 for i, subnet in enumerate(subnets) for n in range(int(nodes_per_az))]
        asg = f"eks-{key}-general"
        self.sample.auto_scaling_group(asg, nodes, subnets, minimum=len(nodes), maximum=len(nodes) * 4)
        self.sample.eks(key, v["id"], subnets, [("general", asg, node_type, {"minSize": len(nodes), "maxSize": len(nodes) * 4,
                                                                            "desiredSize": len(nodes)})])
        self.record("add_eks_cluster", vpc=vpc, name=name, node_type=node_type, nodes_per_az=int(nodes_per_az))
        return {"cluster": key, "nodes": len(nodes), "endpoint": "private"}

    def add_database(self, vpc, name, engine="postgres", multi_az=True, read_replica=False, instance_class="db.r6g.large"):
        v = self.vpc(vpc)
        subnets = self.tier(v, "data")
        azs = v["azs"]
        if multi_az and len(azs) < 2:
            raise DesignError("Multi-AZ에는 AZ가 2개 이상 필요합니다.")
        if read_replica and len(azs) < (3 if multi_az else 2):
            raise DesignError("읽기 전용 복제본을 별도 AZ에 두려면 AZ가 더 필요합니다.")
        port = 3306 if "mysql" in engine or "maria" in engine else 5432
        self.security_group(v, f"{slug(name)}-db", [(port, v["cidr"])])
        self.sample.database(slug(name), v["id"], subnets, azs[0], engine, instance_class,
                             standby=azs[1] if multi_az else None, replica=azs[2 if multi_az else 1] if read_replica else None)
        self.record("add_database", vpc=vpc, name=name, engine=engine, multi_az=multi_az, read_replica=read_replica)
        return {"database": slug(name), "engine": engine, "multi_az": bool(multi_az), "read_replica": bool(read_replica)}

    def add_aurora(self, vpc, name, engine="aurora-postgresql", readers=1, instance_class="db.r6g.large"):
        v = self.vpc(vpc)
        azs = v["azs"]
        readers = int(readers)
        if not 0 <= readers <= 15:
            raise DesignError("Aurora 리더는 0~15개입니다.")
        self.sample.aurora(slug(name), v["id"], self.tier(v, "data"), azs[0], [azs[(i + 1) % len(azs)] for i in range(readers)],
                           engine, instance_class)
        self.record("add_aurora", vpc=vpc, name=name, engine=engine, readers=readers)
        return {"cluster": slug(name), "writer": 1, "readers": readers}

    def add_cache(self, vpc, name, replicas=1, node_type="cache.r7g.large"):
        v = self.vpc(vpc)
        azs = v["azs"]
        self.sample.redis(slug(name), v["id"], self.tier(v, "data"), azs[0], [azs[(i + 1) % len(azs)] for i in range(int(replicas))],
                          node_type)
        self.record("add_cache", vpc=vpc, name=name, replicas=int(replicas))
        return {"cache": slug(name), "engine": "redis", "primary": 1, "replicas": int(replicas)}

    def add_kafka(self, vpc, name, brokers_per_az=1, broker_type="kafka.m7g.large"):
        v = self.vpc(vpc)
        subnets = self.tier(v, "data")
        self.sample.kafka(slug(name), subnets, len(subnets) * int(brokers_per_az), broker_type)
        self.record("add_kafka", vpc=vpc, name=name, brokers_per_az=int(brokers_per_az))
        return {"cluster": slug(name), "brokers": len(subnets) * int(brokers_per_az)}

    def add_opensearch(self, vpc, name, data_per_az=1, standby=False):
        v = self.vpc(vpc)
        subnets = self.tier(v, "data")
        if standby and len(subnets) != 3:
            raise DesignError("Multi-AZ with Standby는 AZ 3개가 필요합니다.")
        self.sample.opensearch(slug(name), v["id"], subnets, int(data_per_az), 1, standby=v["azs"][-1] if standby else None)
        self.record("add_opensearch", vpc=vpc, name=name, data_per_az=int(data_per_az), standby=standby)
        return {"domain": slug(name), "data_nodes": len(subnets) * int(data_per_az), "standby": bool(standby)}

    def add_file_system(self, vpc, name, tier="app"):
        v = self.vpc(vpc)
        self.sample.efs(f"fs-{slug(name)}", name, v["id"], self.tier(v, tier))
        self.record("add_file_system", vpc=vpc, name=name, tier=tier)
        return {"file_system": slug(name), "mount_targets": len(v["azs"])}

    def add_lambda(self, name, vpc=None, tier="app", runtime="python3.12"):
        if vpc:
            v = self.vpc(vpc)
            self.sample.function(slug(name), v["id"], self.tier(v, tier), runtime)
        else:
            self.sample.raw["lambda_functions"].append({"FunctionName": slug(name), "Runtime": runtime})
        self.record("add_lambda", name=name, vpc=vpc, runtime=runtime)
        return {"function": slug(name), "vpc": vpc or "VPC 미연결"}

    def add_vpc_endpoints(self, vpc, services=("s3", "ssm")):
        v = self.vpc(vpc)
        added = []
        for service in services:
            service = str(service).lower()
            if service in ("s3", "dynamodb"):
                # Without NAT the private subnets keep the main table, which then carries the endpoint route.
                tables = v["private_tables"] or [f"rtb-{v['key']}-main"]
                self.sample.gateway_endpoint(f"vpce-{v['key']}-{service}", v["id"], service, tables)
            else:
                tier = "app" if "app" in v["tiers"] else next(iter(v["tiers"]))
                self.sample.interface_endpoint(f"vpce-{v['key']}-{slug(service)}", v["id"], service, self.tier(v, tier))
            added.append(service)
        self.record("add_vpc_endpoints", vpc=vpc, services=added)
        return {"endpoints": added}

    def connect_vpcs(self, a, b):
        va, vb = self.vpc(a), self.vpc(b)
        key = f"pcx-{va['key']}-{vb['key']}"
        self.sample.peering(key, va["id"], vb["id"], f"{va['key']}-to-{vb['key']}")
        for this, other in ((va, vb), (vb, va)):
            for table in this["private_tables"] or [f"rtb-{this['key']}-main"]:
                self.sample.route(table, other["cidr"], VpcPeeringConnectionId=key)
        self.record("connect_vpcs", a=a, b=b)
        return {"peering": key}

    def add_vpn(self, vpc, customer_ip="203.0.113.10", name="on-premises"):
        v = self.vpc(vpc)
        try:
            ipaddress.ip_address(customer_ip)
        except ValueError as exc:
            raise DesignError(f"고객 게이트웨이 IP 형식이 아닙니다: {customer_ip}") from exc
        vgw = f"vgw-{v['key']}"
        self.sample.vpn(f"vpn-{v['key']}-{slug(name)}", f"cgw-{slug(name)}", customer_ip, slug(name), vgw=vgw, vpc=v["id"],
                        vgw_name=f"{v['key']}-vgw")
        for table in v["private_tables"] or [f"rtb-{v['key']}-main"]:
            self.sample.route(table, "192.168.0.0/16", GatewayId=vgw)
        self.record("add_vpn", vpc=vpc, customer_ip=customer_ip, name=name)
        return {"vpn": f"vpn-{v['key']}-{slug(name)}", "tunnels": 2}

    # --- standards, review and output ------------------------------------------------------------
    def apply_standard(self, name):
        if name not in SAMPLES:
            raise DesignError(f"표준 아키텍처가 아닙니다: {name}. 사용 가능: {', '.join(STANDARDS)}")
        snapshot = SAMPLES[name]()
        self.reset(snapshot["metadata"]["title"], snapshot["metadata"]["region"])
        self.sample.raw = snapshot["raw"]
        self.sample.practices = snapshot["metadata"].get("practices", [])
        self.standard = name
        self.record("apply_standard", name=name)
        return {"standard": name, "title": self.title, "practices": self.sample.practices,
                "note": "표준 아키텍처를 불러왔습니다. 이 위에 add_* 단계로 요소를 더하려면 새 VPC를 추가하세요."}

    def graph(self):
        return build_graph(self.snapshot())

    def review(self):
        findings = review(self.graph())
        return {"summary": summary(findings),
                "findings": [{k: f[k] for k in ("severity", "pillar", "rule", "resource_name", "title", "recommendation")}
                             for f in findings[:20]]}

    def describe(self):
        graph = self.graph()
        kinds = {}
        for node in graph["nodes"]:
            if not node["reference_only"]:
                kinds[node["kind"]] = kinds.get(node["kind"], 0) + 1
        return {"title": self.title, "region": self.region, "standard": self.standard,
                "vpcs": [{"name": v["name"], "cidr": v["cidr"], "azs": len(v["azs"]), "tiers": list(v["tiers"])}
                         for v in self.vpcs.values()], "resources": kinds, "steps": len(self.steps)}

    def snapshot(self):
        snapshot = self.sample.snapshot()
        snapshot["metadata"].update(
            design=True, sample=self.standard or "design", title=self.title, steps=list(self.steps),
            practices=self.sample.practices,
            limitations=["채팅으로 만든 표준 아키텍처 설계안이며 실제 계정 데이터가 아닙니다.",
                         "보안 그룹 규칙은 계층 간 최소 허용으로 가정했습니다. 실제 배포 전 IaC로 검토하세요.",
                         "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."])
        return snapshot
