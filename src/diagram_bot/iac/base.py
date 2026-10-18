"""Template state and bookkeeping shared by every resource step: logical IDs, Ref, parameters, tags, AZs, security groups."""
import re

GONE = ("deleted", "deleting", "terminated", "shutting-down", "failed", "rejected", "expired")
ROLE_PATTERN = r"^arn:aws[a-z-]*:iam::\d{12}:role/.+$"
# Graviton instance families carry a "g" after the generation digit (m7g, c6gn, r8gd, g5g); everything else here is x86_64.
GRAVITON = re.compile(r"^[a-z]+\d+[a-z-]*g[a-z-]*\.")


def camel(text):
    return "".join(word[:1].upper() + word[1:] for word in re.findall(r"[A-Za-z0-9]+", str(text)))


def name_of(item, *fallback):
    tag = next((t.get("Value") for t in item.get("Tags") or [] if t.get("Key") == "Name"), None)
    return tag or next((value for value in fallback if value), None)



class Base:
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

    def logical(self, prefix, *labels):
        """Readable, unique, alphanumeric logical ID: the prefix plus the first label with enough ASCII to read."""
        label = next((camel(text) for text in labels if text and len(camel(text)) >= 2), camel(labels[-1] if labels else "") or "Resource")
        return self.unique((prefix + label)[:200])

    def unique(self, base):
        """`base`, or base2, base3, ... when a resource or parameter already has it (they share one namespace)."""
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

