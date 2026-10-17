import json
import os

import pytest
from botocore.exceptions import ClientError
from fastapi.testclient import TestClient
from strands.models.model import Model

from diagram_bot.agent import MODELS, Chat, Workspace, build_tools
from diagram_bot.cli import load_env_file
from diagram_bot.designer import STANDARDS, Design, DesignError
from fixtures import full_design
from diagram_bot.drawio import generate_document, layout_document, validate_document
from diagram_bot.graph import build_graph
from diagram_bot.server import create_app
from diagram_bot.views import view_graph


class Scripted(Model):
    """A model that replays turns: each turn is a list of (tool name, input) calls, or a final text."""

    def __init__(self, turns, fail=None):
        self.turns, self.fail, self.config = list(turns), fail, {}

    def update_config(self, **config):
        self.config.update(config)

    def get_config(self):
        return self.config

    async def structured_output(self, *args, **kwargs):
        raise NotImplementedError
        yield

    async def stream(self, messages, tool_specs=None, system_prompt=None, **kwargs):
        if self.fail:
            raise self.fail
        turn = self.turns.pop(0)
        yield {"messageStart": {"role": "assistant"}}
        if isinstance(turn, str):
            yield {"contentBlockDelta": {"delta": {"text": turn}}}
            yield {"contentBlockStop": {}}
            yield {"messageStop": {"stopReason": "end_turn"}}
            return
        for index, (name, arguments) in enumerate(turn):
            yield {"contentBlockStart": {"start": {"toolUse": {"toolUseId": f"t{len(self.turns)}-{index}", "name": name}}}}
            yield {"contentBlockDelta": {"delta": {"toolUse": {"input": json.dumps(arguments)}}}}
            yield {"contentBlockStop": {}}
        yield {"messageStop": {"stopReason": "tool_use"}}


def test_design_renders_like_a_collected_snapshot():
    d = full_design()
    snapshot = d.snapshot()
    assert snapshot["metadata"]["design"] is True and snapshot["metadata"]["region"] == "us-east-1"
    assert len(snapshot["metadata"]["steps"]) == 18
    graph = build_graph(snapshot)
    kinds = {n["kind"] for n in graph["nodes"]}
    assert {"load_balancer", "ec2", "ecs_task", "db_standby", "cache_node", "eks_cluster", "msk_broker", "search_node",
            "efs_mount_target", "lambda_function", "vpc_peering", "vpn_gateway", "nat_gateway", "vpc_endpoint"} <= kinds
    # Three AZ columns, NAT per AZ, the data tier isolated (main table only).
    subnets = {n["resource_id"]: n for n in graph["nodes"] if n["kind"] == "subnet"}
    assert subnets["subnet-shop-public-a"]["route_class"] == "public"
    assert subnets["subnet-shop-app-b"]["route_class"] == "nat"
    assert subnets["subnet-shop-data-c"]["route_class"] == "isolated"
    assert sum(n["kind"] == "nat_gateway" for n in graph["nodes"]) == 3
    # Each target group carries its service: three web instances, six ECS task IPs.
    nodes = {n["id"]: n for n in graph["nodes"]}
    registered = {}
    for e in graph["edges"]:
        if e["relation"] == "registered_target":
            registered.setdefault(nodes[e["source"]]["name"], set()).add(nodes[e["target"]]["kind"])
    assert registered == {"web": {"ec2"}, "orders": {"ecs_task"}}
    assert sum(e["relation"] == "registered_target" and nodes[e["source"]]["name"] == "orders" for e in graph["edges"]) == 6
    standby = next(n for n in graph["nodes"] if n["kind"] == "db_standby")
    assert standby["attributes"].get("AvailabilityZone", "").endswith("b") or "us-east-1b" in json.dumps(standby["attributes"])
    for vpc in ("vpc-shop", "vpc-analytics"):
        assert view_graph(graph, "detail", vpc)["counts"]["visible"] > 5
        layout_document(snapshot, "detail", vpc)
    xml = generate_document(snapshot)
    validate_document(xml)
    assert "DESIGN · 쇼핑몰 / us-east-1" in xml and "AWS Cloud · 설계안" in xml and "SAMPLE ·" not in xml
    assert d.review()["summary"]["high"] == 0
    assert d.describe()["vpcs"][0] == {"name": "shop", "cidr": "10.20.0.0/16", "azs": 3, "tiers": ["public", "web", "app", "data"]}


@pytest.mark.parametrize("step, message", [
    (lambda d: d.add_vpc("dup", "10.20.0.0/16"), "겹칩니다"),
    (lambda d: d.add_vpc("big", "10.0.0.0/8"), "/16~/20"),
    (lambda d: d.add_load_balancer("missing", "lb"), "설계에 없습니다"),
    (lambda d: d.add_ec2_service("shop", "x", load_balancer="nope"), "로드 밸런서"),
    (lambda d: d.add_ec2_service("shop", "y", tier="web"), "'web' 계층"),
    (lambda d: d.apply_standard("unknown"), "표준 아키텍처가 아닙니다"),
])
def test_design_errors_explain_what_to_fix(step, message):
    d = Design()
    d.add_vpc("shop", "10.20.0.0/16", 2)
    with pytest.raises(DesignError, match=message):
        step(d)


def test_standard_architectures_load_and_review():
    for name in STANDARDS:
        d = Design()
        result = d.apply_standard(name)
        assert result["practices"] and d.describe()["standard"] == name
        assert d.snapshot()["metadata"]["design"] is True
        d.review()


def test_tools_have_schemas_and_descriptions():
    tools = build_tools(Workspace(lambda s: "x"))
    specs = {t.tool_spec["name"]: t.tool_spec for t in tools}
    assert {"collect_aws_resources", "find_collected_resources", "review_collected_resources", "aws_identity",
            "add_vpc", "add_load_balancer", "add_database", "apply_standard_architecture", "review_design"} <= set(specs)
    assert all(spec["description"].strip() for spec in specs.values())
    assert specs["add_vpc"]["inputSchema"]["json"]["required"] == ["name"]


def test_chat_draws_through_tools_and_falls_back_when_the_preferred_model_is_denied():
    saved = []
    workspace = Workspace(lambda snapshot: saved.append(snapshot) or f"s{len(saved)}", region="ap-northeast-2")
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not available for this account"}}, "ConverseStream")
    scripted = Scripted([
        [("new_design", {"title": "웹 서비스", "region": "ap-northeast-2"}),
         ("add_vpc", {"name": "web", "cidr": "10.40.0.0/16", "azs": 2})],
        [("add_load_balancer", {"vpc": "web", "name": "alb"}),
         ("add_ec2_service", {"vpc": "web", "name": "app", "load_balancer": "alb", "port": 8080}),
         ("add_vpc", {"name": "web", "cidr": "10.40.0.0/16"})],  # duplicate: the tool error goes back to the model
        "VPC와 ALB, EC2 서비스를 그렸습니다.",
    ])
    models = {"gpt-6-luna": Scripted([], fail=denied), "nova-2-lite": scripted}
    chat = Chat(workspace, "gpt-6-luna", "nova-2-lite",
                model_factory=lambda key, record=None: _recording(models[key], key, record))
    result = chat.ask("서울에 2 AZ 웹 서비스를 그려줘")
    assert result["reply"] == "VPC와 ALB, EC2 서비스를 그렸습니다."
    assert result["model"] == "nova-2-lite" and result["fallback"] and result["model_label"] == MODELS["nova-2-lite"][0]
    assert result["tools_used"] == ["new_design", "add_vpc", "add_load_balancer", "add_ec2_service", "add_vpc"]
    assert result["snapshots"] == ["s1", "s2", "s3"]
    errors = [b["toolResult"] for m in chat.agent.messages for b in m["content"] if "toolResult" in b and b["toolResult"]["status"] == "error"]
    assert len(errors) == 1 and "이미 있는 VPC" in json.dumps(errors[0], ensure_ascii=False)
    graph = build_graph(saved[-1])
    assert {"load_balancer", "target_group", "ec2", "auto_scaling_group"} <= {n["kind"] for n in graph["nodes"]}


def _recording(model, key, record):
    original = model.stream

    async def stream(*args, **kwargs):
        record(key)
        async for event in original(*args, **kwargs):
            yield event
    model.stream = stream
    return model


def test_collection_tools_are_read_only_and_role_gated(monkeypatch):
    calls = []

    class Session:
        def client(self, service, **kwargs):
            calls.append(service)
            return type("STS", (), {"get_caller_identity": lambda self: {"Account": "123456789012", "Arn": "arn:aws:iam::123456789012:user/x"}})()
    workspace = Workspace(lambda s: "x", session_factory=lambda region: Session(), region="us-east-1")
    assert workspace.aws_identity() == {"account": "123456789012", "arn": "arn:aws:iam::123456789012:user/x", "region": "us-east-1"}
    with pytest.raises(ValueError, match="collect_aws_resources"):
        workspace.find_collected_resources()
    with pytest.raises(PermissionError):
        Workspace(lambda s: "x", allow_collection=False).collect_aws_resources("us-east-1")


def test_collection_tool_against_the_aws_emulator(monkeypatch):
    moto = pytest.importorskip("moto")
    import boto3
    for key, value in {"AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_DEFAULT_REGION": "us-east-1",
                       "AWS_CONFIG_FILE": "/dev/null", "AWS_SHARED_CREDENTIALS_FILE": "/dev/null"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("AWS_ACCOUNT_ID", raising=False)
    with moto.mock_aws():
        ec2 = boto3.client("ec2", region_name="us-east-1")
        vpc = ec2.create_vpc(CidrBlock="10.9.0.0/16")["Vpc"]["VpcId"]
        ec2.create_subnet(VpcId=vpc, CidrBlock="10.9.1.0/24", AvailabilityZone="us-east-1a")
        boto3.client("lambda", region_name="us-east-1")
        saved = []
        workspace = Workspace(lambda s: saved.append(s) or "live", region="us-east-1")
        result = workspace.collect_aws_resources()
        assert result["snapshot_id"] == "live" and result["resources"]["vpc"] >= 1 and result["resources"]["subnet"] >= 1
        found = workspace.find_collected_resources("vpc", "10.9.0.0")
        assert found["total"] == 1 and found["resources"][0]["id"] == vpc
        # A VPC scope (by ID or Name tag) finds resources whose names do not contain the VPC's name.
        ec2.create_tags(Resources=[vpc], Tags=[{"Key": "Name", "Value": "verify-net"}])
        workspace.collect_aws_resources()
        inside = workspace.find_collected_resources("subnet", vpc="verify-net")
        assert inside["total"] == 1 and inside["resources"][0]["vpc"] == "verify-net"
        assert workspace.find_collected_resources(vpc=vpc)["total"] >= 2
        with pytest.raises(ValueError, match="수집된 VPC"):
            workspace.find_collected_resources(vpc="nope")
        assert "summary" in workspace.review_collected_resources()
        assert saved[0]["metadata"].get("design") is None


class FakeChat:
    def __init__(self, workspace, model):
        self.workspace, self.model = workspace, model

    def ask(self, message):
        self.workspace.design.add_vpc("demo", "10.50.0.0/16", 2)
        identifier = self.workspace.publish(self.workspace.design.snapshot())
        return {"reply": f"{self.model}: {message}", "snapshots": [identifier], "tools_used": ["add_vpc"], "model": self.model,
                "model_label": MODELS[self.model][0], "fallback": False, "usage": {}}


def test_chat_api_saves_designs_as_snapshots(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor", chat_factory=FakeChat)) as client:
        client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
        info = client.get("/api/agent").json()
        assert info["ready"] and info["default"] == "nova-2-lite" and {m["key"] for m in info["models"]} == set(MODELS)
        assert client.post("/api/agent/chat", json={"message": "hi"}, headers={"X-Diagram-Bot": "wrong"}).status_code == 403
        assert client.post("/api/agent/chat", json={"message": "hi", "model": "gpt-9"}).status_code == 422
        result = client.post("/api/agent/chat", json={"message": "VPC 그려줘", "model": "nova-2-lite"}).json()
        assert result["reply"] == "nova-2-lite: VPC 그려줘"
        snapshot = client.get(f"/api/snapshots/{result['snapshots'][0]}").json()
        assert snapshot["metadata"]["design"] is True and any(n["kind"] == "vpc" for n in snapshot["graph"]["nodes"])
        assert any(row["id"] == result["snapshots"][0] for row in client.get("/api/snapshots").json())
        assert client.post("/api/agent/reset").json() == {"reset": True}
        log = (tmp_path / "data" / "audit.log").read_text()
        assert "agent snapshot" in log and "/api/agent/chat" in log


def test_env_file_loads_keys_and_drops_the_profile(tmp_path, monkeypatch):
    monkeypatch.setenv("AWS_PROFILE", "someone")
    for key in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_REGION", "AWS_ACCOUNT_ID"):
        monkeypatch.delenv(key, raising=False)
    env = tmp_path / ".env"
    env.write_text("# AWS\nAWS_REGION=us-east-1\nexport AWS_ACCESS_KEY_ID='AKIDEXAMPLE'\nAWS_SECRET_ACCESS_KEY=\"secret\"\nnot a line\n")
    assert load_env_file(env) == ["AWS_REGION", "AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY"]
    assert os.environ["AWS_ACCESS_KEY_ID"] == "AKIDEXAMPLE" and os.environ["AWS_SECRET_ACCESS_KEY"] == "secret"
    assert "AWS_PROFILE" not in os.environ


def test_gateway_endpoint_without_nat_uses_the_main_table():
    """A private-only VPC (no IGW or NAT) still gets an S3 gateway endpoint, routed through its main table."""
    d = Design()
    d.add_vpc("private", "10.30.0.0/16", 2, tiers=["app", "data"], nat="none")
    d.add_vpc_endpoints("private", ["s3"])
    endpoint = d.sample.raw["vpc_endpoints"][0]
    assert endpoint["VpcEndpointType"] == "Gateway" and endpoint["RouteTableIds"] == ["rtb-private-main"]


def test_gpu_node_groups_follow_the_ai_lenses():
    """Inference spreads over the cluster's AZs; EFA training stays in one AZ with a self-referencing group."""
    d = Design("AI", "us-east-1")
    d.add_vpc("ai", "10.40.0.0/16", 3, ["app", "data"], "none")
    d.add_vpc_endpoints("ai", ["s3", "bedrock-runtime", "ecr.api", "ecr.dkr"])
    d.add_eks_cluster("ai", "llm")
    assert d.add_gpu_node_group("ai", "llm", "inference", "g6e.2xlarge")["azs"] == ["us-east-1a", "us-east-1b", "us-east-1c"]
    result = d.add_gpu_node_group("ai", "llm", "train", "p5.48xlarge", per_az=2, training=True)
    assert result == {"node_group": "train", "nodes": 2, "azs": ["us-east-1a"], "efa": True}
    efa = next(g for g in d.sample.raw["security_groups"] if g["GroupId"] == "sg-ai-llm-train-efa")
    assert {"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": "sg-ai-llm-train-efa"}]} in efa["IpPermissions"]
    assert [n["nodegroupName"] for n in d.sample.raw["eks_clusters"][0]["nodegroups"]] == ["general", "inference", "train"]
    found = [(f["rule"], f["severity"]) for f in d.review()["findings"]]
    assert ("REL-ASG-AZ", "low") in found and not any(rule.startswith(("REL-GPU", "SEC-EFA")) for rule, _ in found)
    with pytest.raises(DesignError, match="GPU/가속"):
        d.add_gpu_node_group("ai", "llm", "cpu", "m7g.large")
    with pytest.raises(DesignError, match="EKS 클러스터가 설계에 없습니다"):
        d.add_gpu_node_group("ai", "missing", "gpu", "g6e.xlarge")
    with pytest.raises(DesignError, match="이미 있는 노드 그룹"):
        d.add_gpu_node_group("ai", "llm", "inference", "g6e.xlarge")
    assert "add_gpu_node_group" in {t.tool_spec["name"] for t in build_tools(Workspace(lambda s: "x"))}
