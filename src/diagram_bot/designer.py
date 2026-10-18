"""Standard infrastructure designs built from intent-level steps (the chat agent's drawing tools).

A design is written in the same API-shaped `raw` structure the collector produces, so the existing
graph, layout, review and export pipeline renders it exactly like a collected snapshot. Nothing here
calls AWS; a design is a proposal, never a description of a real account.
"""
import copy
import hashlib
import ipaddress
import re

from .findings import review, summary
from .graph import build_graph
from .samples import SAMPLES, Sample

AZ_LETTERS = "abcdef"
TIERS = ("public", "web", "app", "data")
ACCELERATED = re.compile(r"^(p|g|trn|inf|dl)\d")
STANDARDS = {
    "three-tier": "3계층 웹 (ALB → 웹 ASG → 내부 ALB → 앱 ASG → RDS Multi-AZ, 3 AZ)",
    "microservices": "마이크로서비스 (ALB 경로 라우팅, EC2 ASG + ECS Fargate, RDS Multi-AZ, 3 AZ)",
    "nlb-alb": "고정 IP 진입 (NLB → ALB, 2 AZ)",
    "environments": "운영·스테이징 VPC 분리 (공용 서비스 VPC, 중앙 egress VPC, Transit Gateway)",
    "hybrid": "하이브리드 (Transit Gateway + Direct Connect, Site-to-Site VPN 백업)",
    "eks-platform": "EKS 플랫폼 (EKS 전용 /28 클러스터 서브넷, Aurora 3 AZ, Redis, MSK, Lambda, 엔드포인트)",
    "data-platform": "데이터 플랫폼 (ECS, OpenSearch, RDS, Redis, EFS, Redshift, TGW, 이중화 VPN)",
    "ai-platform": "AI 플랫폼 (사설 EKS GPU 추론 3 AZ·EFA 학습 단일 AZ·배치 그룹, FSx for Lustre, Bedrock 엔드포인트, Aurora pgvector, EFS)",
    "genai-rag": "생성형 AI RAG 앱 (ECS Fargate 3 AZ, Bedrock 엔드포인트, Aurora pgvector, 시맨틱 캐시, SageMaker 리랭커)",
    "gpu-training": "GPU 분산 학습 클러스터 (p5 Capacity Block·trn2 배치 그룹, EFA, FSx for Lustre, HyperPod)",
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
        self.base = None  # the collected snapshot a brownfield design started from

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
            raise DesignError(f"VPC '{vpc['name']}'에 '{tier}' 계층 서브넷이 없습니다. (계층: {', '.join(vpc['tiers'])}) "
                              "add_subnet_tier로 계층을 추가할 수 있습니다.")
        return subnets

    def routing_tables(self, vpc):
        """Tables that carry a new route: the private (NAT) tables, else the main table. A collected VPC whose
        route tables were not read has neither, and a route is never written into a table that was not seen."""
        tables = vpc["private_tables"] or [t for t in [vpc["main_table"]] if t]
        if not tables:
            raise DesignError(f"VPC '{vpc['name']}'의 라우트 테이블이 수집되지 않아 라우트를 추가할 수 없습니다 "
                              "(ec2:DescribeRouteTables 권한으로 다시 수집하세요). 인터페이스 엔드포인트는 추가할 수 있습니다.")
        return tables

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
            s.network(vpc_id, key, letters, layers["public"], private, nat_per_az=nat == "per_az", nat=nat != "none")
        else:
            # The data tier (and private subnets without NAT) keep the main table: local route only.
            s.route_table(f"rtb-{key}-main", vpc_id, f"{key}-isolated", main=True)
        self.vpcs[key] = {"name": name, "key": key, "id": vpc_id, "cidr": cidr, "azs": letters, "tiers": layers,
                          "private_tables": [f"rtb-{key}-private-{az}" for az in letters] if "public" in layers and private else [],
                          "main_table": f"rtb-{key}-main", "sgs": set()}
        self.record("add_vpc", name=name, cidr=cidr, azs=int(azs), tiers=tiers, nat=nat)
        return {"vpc": name, "subnets": {t: len(v) for t, v in layers.items()}, "azs": [f"{self.region}{a}" for a in letters],
                "nat_gateways": 0 if nat == "none" or "public" not in layers else (len(letters) if nat == "per_az" else 1)}

    def add_subnet_tier(self, vpc, tier):
        """A missing tier, one /24 per AZ in free CIDR space (mostly for designs started from a collected VPC).
        public routes to the VPC's internet gateway, web/app join the NAT route table of their AZ, data stays isolated."""
        v, s = self.vpc(vpc), self.sample
        if tier not in TIERS:
            raise DesignError(f"계층은 {', '.join(TIERS)} 중에서 고르세요.")
        if tier in v["tiers"]:
            raise DesignError(f"VPC '{v['name']}'에 이미 '{tier}' 계층이 있습니다.")
        used = [ipaddress.ip_network(x["CidrBlock"]) for x in s.raw["subnets"] if x["VpcId"] == v["id"]]
        free = (b for b in ipaddress.ip_network(v["cidr"]).subnets(new_prefix=24) if not any(b.overlaps(u) for u in used))
        created = []
        for az in v["azs"]:
            block = next(free, None)
            if block is None:
                raise DesignError(f"VPC {v['name']}에 /24 서브넷을 더 만들 여유 CIDR이 없습니다.")
            created.append(s.subnet(f"subnet-{v['key']}-{tier}-{az}", v["id"], az, str(block), f"{v['key']}-{tier}-{az}", tier))
        if tier == "public":
            igw = next((g["InternetGatewayId"] for g in s.raw["internet_gateways"]
                        if any(a.get("VpcId") == v["id"] for a in g.get("Attachments", []))), None) \
                or s.internet_gateway(f"igw-{v['key']}", v["id"], f"{v['key']}-igw")
            s.route_table(f"rtb-{v['key']}-public", v["id"], f"{v['key']}-public", created, gateway=igw)
        elif tier in ("web", "app") and v["private_tables"]:
            tables = {t["RouteTableId"]: t for t in s.raw["route_tables"]}
            for subnet in created:
                az = s.az_of(subnet)
                table = next((t for t in v["private_tables"] if any(a.get("SubnetId") and s.az_of(a["SubnetId"]) == az
                                                                    for a in tables[t]["Associations"])), v["private_tables"][0])
                tables[table]["Associations"].append({"RouteTableAssociationId": f"rtbassoc-{subnet[7:]}", "SubnetId": subnet})
        else:
            s.route_table(f"rtb-{v['key']}-{tier}", v["id"], f"{v['key']}-{tier}", created)
        v["tiers"][tier] = created
        v["tiers"] = {t: v["tiers"][t] for t in TIERS if t in v["tiers"]}
        self.record("add_subnet_tier", vpc=vpc, tier=tier)
        return {"vpc": v["key"], "tier": tier, "subnets": len(created), "azs": [s.az_of(x) for x in created]}

    def security_group(self, vpc, name, ingress, egress=("0.0.0.0/0",)):
        key = f"sg-{vpc['key']}-{slug(name)}"
        if key not in vpc["sgs"]:
            self.sample.security_group(key, vpc["id"], f"{vpc['key']}-{slug(name)}", ingress, egress)
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
        for subnet in subnets:
            for n in range(int(per_az)):
                az = self.sample.az_of(subnet)[-1]
                instances.append(self.sample.instance(f"i-{key}-{az}{n + 1}", subnet, f"{key}-{az}{n + 1}",
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
        nodes = [self.sample.instance(f"i-{key}-node-{self.sample.az_of(subnet)[-1]}{n + 1}", subnet,
                                      f"{key}-node-{self.sample.az_of(subnet)[-1]}{n + 1}", node_type, sg=sg,
                                      **{"eks:cluster-name": key, "eks:nodegroup-name": "general"})
                 for subnet in subnets for n in range(int(nodes_per_az))]
        asg = f"eks-{key}-general"
        self.sample.auto_scaling_group(asg, nodes, subnets, minimum=len(nodes), maximum=len(nodes) * 4)
        self.sample.eks(key, v["id"], subnets, [("general", asg, node_type, {"minSize": len(nodes), "maxSize": len(nodes) * 4,
                                                                            "desiredSize": len(nodes)})])
        self.record("add_eks_cluster", vpc=vpc, name=name, node_type=node_type, nodes_per_az=int(nodes_per_az))
        return {"cluster": key, "nodes": len(nodes), "endpoint": "private"}

    def training_subnet(self, v, beside):
        """A dedicated /20 next to `beside` (same AZ and route table): GPU nodes take many IPs and a subnet cannot grow."""
        az = self.sample.az_of(beside)[-1]
        key = f"subnet-{v['key']}-train-{az}"
        if any(s["SubnetId"] == key for s in self.sample.raw["subnets"]):
            return key
        used = [ipaddress.ip_network(s["CidrBlock"]) for s in self.sample.raw["subnets"] if s["VpcId"] == v["id"]]
        block = next((b for b in ipaddress.ip_network(v["cidr"]).subnets(new_prefix=20) if not any(b.overlaps(u) for u in used)), None)
        if block is None:
            raise DesignError(f"VPC {v['name']}에 학습용 /20 서브넷을 만들 여유 CIDR이 없습니다. 더 큰 VPC CIDR을 쓰세요.")
        self.sample.subnet(key, v["id"], az, str(block), f"{v['key']}-train-{az}", "app")
        for table in self.sample.raw["route_tables"]:
            if any(a.get("SubnetId") == beside for a in table["Associations"]):
                table["Associations"].append({"RouteTableAssociationId": f"rtbassoc-{key[7:]}", "SubnetId": key})
        return key

    def add_gpu_node_group(self, vpc, cluster, name, instance_type, per_az=1, training=False, minimum=None, maximum=None):
        """GPU/accelerator node group on an existing EKS cluster: inference spreads over the cluster's AZs,
        EFA training stays in one AZ with a self-referencing security group (EFA cannot cross AZs)."""
        v = self.vpc(vpc)
        key = slug(cluster)
        eks = next((c for c in self.sample.raw["eks_clusters"] if c["name"] == key and c["resourcesVpcConfig"]["vpcId"] == v["id"]), None)
        if not eks:
            raise DesignError(f"EKS 클러스터가 설계에 없습니다: {cluster}. add_eks_cluster로 먼저 만드세요.")
        if not ACCELERATED.match(str(instance_type)):
            raise DesignError(f"GPU/가속 인스턴스 유형을 지정하세요 (예: g6e.2xlarge, p5.48xlarge, inf2.xlarge, trn2.48xlarge): {instance_type}")
        group = slug(name)
        if any(n["nodegroupName"] == group for n in eks["nodegroups"]):
            raise DesignError(f"이미 있는 노드 그룹입니다: {name}")
        subnets = [self.training_subnet(v, eks["resourcesVpcConfig"]["subnetIds"][0])] if training else eks["resourcesVpcConfig"]["subnetIds"]
        sg = self.security_group(v, f"{key}-{group}-efa", [(None, "self"), (443, v["cidr"])], egress=("self", v["cidr"])) if training \
            else self.security_group(v, f"{key}-nodes", [(443, v["cidr"])])
        # Training nodes: EFA interfaces in one cluster placement group (EC2 placement strategies).
        placement = self.sample.placement_group(f"{key}-{group}-pg") if training else None
        nodes = [self.sample.instance(f"i-{key}-{group}-{self.sample.az_of(subnet)[-1]}{n + 1}", subnet,
                                      f"{group}-{self.sample.az_of(subnet)[-1]}{n + 1}", instance_type, sg=sg, efa=training, placement=placement,
                                      **{"eks:cluster-name": key, "eks:nodegroup-name": group})
                 for subnet in subnets for n in range(int(per_az))]
        minimum = int(minimum) if minimum is not None else (0 if training else len(nodes))
        maximum = int(maximum) if maximum is not None else max(len(nodes) * 4, minimum)
        asg = f"eks-{key}-{group}"
        self.sample.auto_scaling_group(asg, nodes, subnets, minimum=minimum, maximum=maximum)
        eks["nodegroups"].append({"nodegroupName": group, "instanceTypes": [instance_type],
                                  "scalingConfig": {"minSize": minimum, "maxSize": maximum, "desiredSize": len(nodes)},
                                  "resources": {"autoScalingGroups": [{"name": asg}]}})
        self.record("add_gpu_node_group", vpc=vpc, cluster=cluster, name=name, instance_type=instance_type, per_az=int(per_az),
                    training=bool(training), minimum=minimum, maximum=maximum)
        return {"node_group": group, "nodes": len(nodes), "azs": sorted({self.sample.az_of(s) for s in subnets}), "efa": bool(training)}

    def add_fsx_lustre(self, vpc, name, deployment="PERSISTENT_2", capacity_tib=4.8, throughput=250):
        """FSx for Lustre beside the training nodes (their dedicated subnet), else in the first app subnet."""
        v = self.vpc(vpc)
        if deployment not in ("PERSISTENT_1", "PERSISTENT_2", "SCRATCH_1", "SCRATCH_2"):
            raise DesignError("deployment는 PERSISTENT_2(권장), PERSISTENT_1, SCRATCH_2, SCRATCH_1 중 하나입니다.")
        train = sorted(s["SubnetId"] for s in self.sample.raw["subnets"] if s["SubnetId"].startswith(f"subnet-{v['key']}-train-"))
        subnet = train[0] if train else self.tier(v, "app" if "app" in v["tiers"] else next(iter(v["tiers"])))[0]
        key = f"fs-{hashlib.sha256((v['key'] + name).encode()).hexdigest()[:17]}"
        self.sample.fsx_lustre(key, slug(name), v["id"], subnet, deployment, int(float(capacity_tib) * 1024), int(throughput))
        self.record("add_fsx_lustre", vpc=vpc, name=name, deployment=deployment, capacity_tib=float(capacity_tib))
        return {"file_system": slug(name), "subnet": subnet, "az": self.sample.az_of(subnet), "next_to_training": bool(train)}

    def add_sagemaker_endpoint(self, vpc, name, instance_type="ml.g5.xlarge", count=2, tier="app"):
        """SageMaker real-time endpoint in the VPC: the model's VpcConfig uses the tier's subnets in every AZ."""
        v = self.vpc(vpc)
        if not str(instance_type).startswith("ml."):
            raise DesignError(f"SageMaker 인스턴스 유형은 ml.로 시작합니다 (예: ml.g5.xlarge): {instance_type}")
        if int(count) < 1:
            raise DesignError("인스턴스 수는 1 이상입니다 (AZ 장애 대비 2 이상 권장).")
        subnets = self.tier(v, tier)
        sg = self.security_group(v, f"{slug(name)}-endpoint", [(443, v["cidr"])])
        self.sample.sagemaker_endpoint(slug(name), instance_type, int(count), subnets, sg)
        self.record("add_sagemaker_endpoint", vpc=vpc, name=name, instance_type=instance_type, count=int(count), tier=tier)
        return {"endpoint": slug(name), "instances": int(count), "azs": len({self.sample.az_of(s) for s in subnets})}

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
        services = [str(service).lower() for service in services]
        # Without NAT the private subnets keep the main table, which then carries the endpoint route.
        tables = self.routing_tables(v) if {"s3", "dynamodb"} & set(services) else None  # checked before anything is added
        added = []
        for service in services:
            if service in ("s3", "dynamodb"):
                self.sample.gateway_endpoint(f"vpce-{v['key']}-{service}", v["id"], service, tables)
            else:
                tier = "app" if "app" in v["tiers"] else next(iter(v["tiers"]))
                self.sample.interface_endpoint(f"vpce-{v['key']}-{slug(service)}", v["id"], service, self.tier(v, tier))
            added.append(service)
        self.record("add_vpc_endpoints", vpc=vpc, services=added)
        return {"endpoints": added}

    def connect_vpcs(self, a, b):
        va, vb = self.vpc(a), self.vpc(b)
        self.routing_tables(va), self.routing_tables(vb)
        key = f"pcx-{va['key']}-{vb['key']}"
        self.sample.peering(key, va["id"], vb["id"], f"{va['key']}-to-{vb['key']}")
        for this, other in ((va, vb), (vb, va)):
            for table in self.routing_tables(this):
                self.sample.route(table, other["cidr"], VpcPeeringConnectionId=key)
        self.record("connect_vpcs", a=a, b=b)
        return {"peering": key}

    def add_vpn(self, vpc, customer_ip="203.0.113.10", name="on-premises"):
        v = self.vpc(vpc)
        try:
            ipaddress.ip_address(customer_ip)
        except ValueError as exc:
            raise DesignError(f"고객 게이트웨이 IP 형식이 아닙니다: {customer_ip}") from exc
        tables = self.routing_tables(v)
        vgw = f"vgw-{v['key']}"
        self.sample.vpn(f"vpn-{v['key']}-{slug(name)}", f"cgw-{slug(name)}", customer_ip, slug(name), vgw=vgw, vpc=v["id"],
                        vgw_name=f"{v['key']}-vgw")
        for table in tables:
            self.sample.route(table, "192.168.0.0/16", GatewayId=vgw)
        self.record("add_vpn", vpc=vpc, customer_ip=customer_ip, name=name)
        return {"vpn": f"vpn-{v['key']}-{slug(name)}", "tunnels": 2}

    # --- standards, review and output ------------------------------------------------------------
    def apply_standard(self, name):
        # Standards are single-account designs; the multi-account sample is a combined collection, not a design.
        if name not in STANDARDS:
            raise DesignError(f"표준 아키텍처가 아닙니다: {name}. 사용 가능: {', '.join(STANDARDS)}")
        snapshot = SAMPLES[name]()
        self.start_from(snapshot)
        self.standard = name
        self.record("apply_standard", name=name)
        return {"standard": name, "title": self.title, "practices": self.sample.practices, "vpcs": self.vpc_summary(),
                "note": "표준 아키텍처를 불러왔습니다. 위 VPC 이름으로 add_* 단계를 이어서 쓸 수 있습니다."}

    def start_from_snapshot(self, snapshot, title=None):
        """Brownfield: the design starts as a copy of a collected (or saved) snapshot; add_* steps extend its VPCs.
        The account's own data is never changed: the result is a new design snapshot."""
        if snapshot["metadata"].get("accounts"):
            raise DesignError("여러 계정을 합친 스냅샷에서는 설계를 시작할 수 없습니다. 계정 하나의 스냅샷을 고르세요.")
        source = snapshot["metadata"]
        self.start_from(snapshot, title or f"{source.get('title') or source.get('account_id') or '현재 구성'} 기반 설계")
        self.base = {k: source.get(k) for k in ("account_id", "region", "collected_at", "title") if source.get(k)}
        self.record("start_from_snapshot", source=source.get("account_id") or source.get("title"), region=self.region)
        return {"title": self.title, "region": self.region, "vpcs": self.vpc_summary(),
                "load_balancers": sorted(self.load_balancers),
                "note": "현재 구성을 설계의 출발점으로 불러왔습니다. 위 VPC 이름으로 add_* 단계를 쓰세요(실제 계정은 바뀌지 않습니다)."}

    def start_from(self, snapshot, title=None):
        metadata = snapshot["metadata"]
        self.reset(title or metadata.get("title") or self.title, metadata.get("region") or self.region)
        self.sample.account = metadata.get("account_id") or self.sample.account
        raw = copy.deepcopy(snapshot["raw"])
        self.sample.raw = {**{key: [] for key in self.sample.raw}, **raw}
        self.sample.practices = metadata.get("practices", [])
        self.adopt()

    def adopt(self):
        """Rebuild step state (VPCs with tiers per AZ, load balancers, target groups) from the raw data."""
        raw = self.sample.raw
        route_class = {n["resource_id"]: n.get("route_class") for n in build_graph(self.sample.snapshot())["nodes"] if n["kind"] == "subnet"}

        def tag(item, key):
            return next((t["Value"] for t in item.get("Tags", []) if t["Key"] == key), None)

        def tier_of(subnet):
            if route_class.get(subnet["SubnetId"]) == "public":
                return "public"
            if tag(subnet, "Tier") in TIERS:
                return tag(subnet, "Tier")
            name = (tag(subnet, "Name") or "").lower()
            if "web" in name:
                return "web"
            if re.search(r"data|db|rds|database|cache|isolated", name) or route_class.get(subnet["SubnetId"]) == "isolated":
                return "data"
            return "app"

        for vpc in raw["vpcs"]:
            name = tag(vpc, "Name") or vpc["VpcId"]
            key = slug(name) if re.search(r"[A-Za-z0-9]", name) else slug(vpc["VpcId"])  # e.g. a Korean Name tag
            if key in self.vpcs:
                key = slug(f"{name}-{vpc['VpcId'][-6:]}")
            subnets = sorted((s for s in raw["subnets"] if s["VpcId"] == vpc["VpcId"]), key=lambda s: (s["AvailabilityZone"], s["SubnetId"]))
            letters = "".join(sorted({s["AvailabilityZone"][-1] for s in subnets}))
            layers = {}
            for tier in TIERS:
                chosen = {}
                for subnet in subnets:
                    if tier_of(subnet) == tier:
                        chosen.setdefault(subnet["AvailabilityZone"], subnet["SubnetId"])
                if chosen:
                    layers[tier] = [chosen[az] for az in sorted(chosen)]
            tables = [t for t in raw["route_tables"] if t.get("VpcId") == vpc["VpcId"]]
            main = next((t["RouteTableId"] for t in tables if any(a.get("Main") for a in t.get("Associations", []))), None)
            private = {sid for tier in ("web", "app") for sid in layers.get(tier, [])}
            nat_tables = [t["RouteTableId"] for t in tables if any(r.get("NatGatewayId") for r in t.get("Routes", []))
                          and any(a.get("SubnetId") in private for a in t.get("Associations", []))]
            self.vpcs[key] = {"name": name, "key": key, "id": vpc["VpcId"], "cidr": vpc["CidrBlock"], "azs": letters, "tiers": layers,
                              "private_tables": nat_tables, "main_table": main,
                              "sgs": {g["GroupId"] for g in raw["security_groups"] if g.get("VpcId") == vpc["VpcId"]}}
        by_id = {v["id"]: v["key"] for v in self.vpcs.values()}
        for lb in raw["load_balancers"]:
            if lb.get("VpcId") in by_id:
                self.load_balancers[slug(lb["LoadBalancerName"])] = {"arn": lb["LoadBalancerArn"], "vpc": by_id[lb["VpcId"]],
                                                                     "sg": (lb.get("SecurityGroups") or [None])[0], "kind": lb.get("Type", "application")}
        for group in raw["target_groups"]:
            self.target_groups[slug(group["TargetGroupName"])] = group["TargetGroupArn"]

    def vpc_summary(self):
        return [{"name": v["key"], "id": v["id"], "cidr": v["cidr"], "azs": len(v["azs"]), "tiers": {t: len(s) for t, s in v["tiers"].items()}}
                for v in self.vpcs.values()]

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
            practices=self.sample.practices, **({"based_on": dict(self.base)} if self.base else {}),
            limitations=(["현재 구성 스냅샷에서 시작한 설계안입니다. 추가한 요소는 제안이며 실제 계정에는 없습니다."] if self.base else []) + [
                         "채팅으로 만든 표준 아키텍처 설계안이며 실제 계정 데이터가 아닙니다.",
                         "보안 그룹 규칙은 계층 간 최소 허용으로 가정했습니다. 실제 배포 전 IaC로 검토하세요.",
                         "설정 관계이며 실제 통신이나 보안 적합성을 의미하지 않습니다."])
        return snapshot
