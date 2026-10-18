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


def test_chat_streams_progress_while_it_draws():
    """Text, tool start/finish, the model that answered and each saved snapshot arrive as the turn runs."""
    saved = []
    workspace = Workspace(lambda snapshot: saved.append(snapshot) or f"s{len(saved)}", region="us-east-1")
    denied = ClientError({"Error": {"Code": "AccessDeniedException", "Message": "not available"}}, "ConverseStream")
    scripted = Scripted([[("new_design", {"title": "웹"}), ("add_vpc", {"name": "web", "azs": 2})],
                         [("add_vpc", {"name": "web"})], "VPC를 그렸습니다."])
    models = {"gpt-6-luna": Scripted([], fail=denied), "nova-2-lite": scripted}
    chat = Chat(workspace, "gpt-6-luna", "nova-2-lite", model_factory=lambda key, record=None: _recording(models[key], key, record))
    events = []
    result = chat.ask("VPC 그려줘", on_event=events.append)
    tools = [(e["name"], e["status"]) for e in events if e["type"] == "tool"]
    assert tools == [("new_design", "start"), ("add_vpc", "start"), ("new_design", "success"), ("add_vpc", "success"),
                     ("add_vpc", "start"), ("add_vpc", "error")]
    assert [e["id"] for e in events if e["type"] == "snapshot"] == result["snapshots"] == ["s1"]
    # The snapshot is announced right after its tool ran, before the model's closing text.
    kinds = [e["type"] for e in events]
    assert kinds.index("snapshot") < kinds.index("text")
    assert "".join(e["text"] for e in events if e["type"] == "text") == "VPC를 그렸습니다."
    models_seen = [(e["model"], e["fallback"]) for e in events if e["type"] == "model"]
    assert models_seen[0] == ("gpt-6-luna", False) and models_seen[-1] == ("nova-2-lite", True)
    # A later turn without a listener streams nothing and leaves no hooks behind.
    assert workspace.on_publish is None and chat.emit is None


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

    def ask(self, message, on_event=None):
        if message == "fail":
            raise RuntimeError("model went away")
        if on_event:
            on_event({"type": "tool", "name": "add_vpc", "status": "start"})
            self.workspace.on_publish = lambda identifier: on_event({"type": "snapshot", "id": identifier})
        self.workspace.design.add_vpc("demo", "10.50.0.0/16", 2)
        identifier = self.workspace.publish(self.workspace.design.snapshot())
        self.workspace.on_publish = None
        if on_event:
            on_event({"type": "text", "text": f"{self.model}: "})
            on_event({"type": "text", "text": message})
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


def test_chat_stream_api_sends_ndjson_progress_then_the_result(tmp_path):
    with TestClient(create_app(tmp_path / "data", tmp_path / "no-editor", chat_factory=FakeChat)) as client:
        client.headers["X-Diagram-Bot"] = client.get("/api/bootstrap").json()["token"]
        assert client.post("/api/agent/chat/stream", json={"message": "hi"}, headers={"X-Diagram-Bot": "wrong"}).status_code == 403
        assert client.post("/api/agent/chat/stream", json={"message": "hi", "model": "gpt-9"}).status_code == 422
        response = client.post("/api/agent/chat/stream", json={"message": "VPC 그려줘"})
        assert response.status_code == 200 and response.headers["content-type"].startswith("application/x-ndjson")
        events = [json.loads(line) for line in response.text.splitlines()]
        assert [e["type"] for e in events] == ["tool", "snapshot", "text", "text", "done"]
        done = events[-1]
        assert done["reply"] == "nova-2-lite: VPC 그려줘" and done["snapshots"] == [events[1]["id"]]
        assert client.get(f"/api/snapshots/{events[1]['id']}").json()["metadata"]["design"] is True
        # A failing turn ends the stream with an error line; the chat stays usable.
        failed = [json.loads(line) for line in client.post("/api/agent/chat/stream", json={"message": "fail"}).text.splitlines()]
        assert failed == [{"type": "error", "detail": "에이전트 응답 실패: RuntimeError: model went away"}]
        assert client.post("/api/agent/chat", json={"message": "fail"}).status_code == 502
        client.post("/api/agent/reset")
        assert client.post("/api/agent/chat", json={"message": "again"}).json()["reply"] == "nova-2-lite: again"


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
    # A dedicated /20 training subnet in the same AZ, EFA interfaces and one cluster placement group.
    train = next(s for s in d.sample.raw["subnets"] if s["SubnetId"] == "subnet-ai-train-a")
    assert train["CidrBlock"].endswith("/20") and train["AvailabilityZone"] == "us-east-1a"
    trainers = [i for i in d.sample.raw["instances"] if i["InstanceType"] == "p5.48xlarge"]
    assert all(i["SubnetId"] == "subnet-ai-train-a" and i["Placement"]["GroupName"] == "llm-train-pg"
               and i["NetworkInterfaces"][0]["InterfaceType"] == "efa" for i in trainers)
    efa = next(g for g in d.sample.raw["security_groups"] if g["GroupId"] == "sg-ai-llm-train-efa")
    assert {"IpProtocol": "-1", "UserIdGroupPairs": [{"GroupId": "sg-ai-llm-train-efa"}]} in efa["IpPermissions"]
    assert [n["nodegroupName"] for n in d.sample.raw["eks_clusters"][0]["nodegroups"]] == ["general", "inference", "train"]
    found = [(f["rule"], f["severity"]) for f in d.review()["findings"]]
    assert ("REL-ASG-AZ", "low") in found and not any(rule.startswith(("REL-GPU", "SEC-EFA", "OPS-GPU")) for rule, _ in found)
    with pytest.raises(DesignError, match="GPU/가속"):
        d.add_gpu_node_group("ai", "llm", "cpu", "m7g.large")
    with pytest.raises(DesignError, match="EKS 클러스터가 설계에 없습니다"):
        d.add_gpu_node_group("ai", "missing", "gpu", "g6e.xlarge")
    with pytest.raises(DesignError, match="이미 있는 노드 그룹"):
        d.add_gpu_node_group("ai", "llm", "inference", "g6e.xlarge")
    assert "add_gpu_node_group" in {t.tool_spec["name"] for t in build_tools(Workspace(lambda s: "x"))}


def test_ai_designer_steps_place_lustre_and_sagemaker_by_the_lenses():
    d = Design("학습", "us-east-1")
    d.add_vpc("ml", "10.50.0.0/16", 2, ["public", "app", "data"])
    d.add_vpc_endpoints("ml", ["s3"])
    d.add_eks_cluster("ml", "train")
    d.add_gpu_node_group("ml", "train", "p5", "p5.48xlarge", per_az=2, training=True)
    lustre = d.add_fsx_lustre("ml", "checkpoints")
    assert lustre["next_to_training"] and lustre["subnet"] == "subnet-ml-train-a"
    endpoint = d.add_sagemaker_endpoint("ml", "reranker", "ml.g5.xlarge", 2)
    assert endpoint == {"endpoint": "reranker", "instances": 2, "azs": 2}
    found = {f["rule"] for f in d.review()["findings"]}
    assert not found & {"REL-FSX-TRAIN-AZ", "OPS-FSX-SCRATCH", "SEC-SAGEMAKER-VPC", "REL-SAGEMAKER-AZ", "OPS-GPU-PLACEMENT", "REL-GPU-SUBNET-IP"}
    with pytest.raises(DesignError, match="PERSISTENT_2"):
        d.add_fsx_lustre("ml", "bad", deployment="FAST")
    with pytest.raises(DesignError, match="ml.로 시작"):
        d.add_sagemaker_endpoint("ml", "bad", "g5.xlarge")
    names = {t.tool_spec["name"] for t in build_tools(Workspace(lambda s: "x"))}
    assert {"add_fsx_lustre", "add_sagemaker_endpoint", "add_gpu_node_group"} <= names


def test_a_standard_can_be_extended_in_its_own_vpcs():
    d = Design()
    result = d.apply_standard("three-tier")
    vpc = result["vpcs"][0]
    assert vpc["tiers"]["public"] == 3 and {"app", "data"} <= set(vpc["tiers"])
    d.add_cache(vpc["name"], "sessions")
    d.add_vpc_endpoints(vpc["name"], ["s3", "ecr.api"])
    graph = d.graph()
    nodes = {n["id"]: n for n in graph["nodes"]}
    vpc_of = {e["source"]: nodes[e["target"]]["resource_id"] for e in graph["edges"] if e["relation"] == "in_vpc"}
    caches = [n for n in graph["nodes"] if n["kind"] == "cache_node"]
    assert caches and all(vpc_of.get(n["id"]) == vpc["id"] for n in caches)
    # The S3 gateway endpoint uses the standard's own private route tables (one per AZ, each with a NAT route).
    endpoint = next(e for e in d.sample.raw["vpc_endpoints"] if e["ServiceName"].endswith(".s3"))
    tables = {t["RouteTableId"]: t for t in d.sample.raw["route_tables"]}
    assert len(endpoint["RouteTableIds"]) == 3 and all(any(r.get("NatGatewayId") for r in tables[t]["Routes"]) for t in endpoint["RouteTableIds"])
    assert d.review()["summary"]["high"] == 0


def test_brownfield_design_extends_a_collected_snapshot_without_touching_it():
    from diagram_bot.demo import demo_snapshot
    collected = demo_snapshot()
    before = json.dumps(collected, sort_keys=True, default=str)
    d = Design()
    result = d.start_from_snapshot(collected)
    names = [v["name"] for v in result["vpcs"]]
    assert len(names) == len(collected["raw"]["vpcs"]) and all(names)  # Korean Name tags fall back to the VPC ID
    target = next(v for v in result["vpcs"] if "app" in v["tiers"])
    with pytest.raises(DesignError, match="add_subnet_tier"):
        d.add_database(target["name"], "orders-db")
    added = d.add_subnet_tier(target["name"], "data")
    assert added["subnets"] == target["azs"]
    d.add_database(target["name"], "orders-db")
    # The demo's partial collection has no route tables: no route is written into a table nobody saw.
    with pytest.raises(DesignError, match="DescribeRouteTables"):
        d.add_vpc_endpoints(target["name"], ["s3"])
    d.add_vpc_endpoints(target["name"], ["ssm"])
    snapshot = d.snapshot()
    assert json.dumps(collected, sort_keys=True, default=str) == before
    assert snapshot["metadata"]["design"] and snapshot["metadata"]["based_on"]["account_id"] == collected["metadata"]["account_id"]
    assert snapshot["metadata"]["steps"][0]["step"] == "start_from_snapshot"
    graph = build_graph(snapshot)
    ids = {n["resource_id"] for n in graph["nodes"]}
    assert {n["resource_id"] for n in build_graph(collected)["nodes"] if not n["reference_only"]} <= ids
    assert any(n["kind"] == "db_instance" and n["name"] == "orders-db" for n in graph["nodes"])
    layout_document(snapshot, "detail", target["id"])
    with pytest.raises(DesignError, match="여러 계정"):
        Design().start_from_snapshot({"metadata": {"accounts": ["1", "2"]}, "raw": {}})


@pytest.mark.parametrize("name", [n for n in STANDARDS])
def test_every_standard_adopts_its_vpcs_and_takes_another_step(name):
    d = Design()
    vpcs = d.apply_standard(name)["vpcs"]
    assert vpcs and all(v["tiers"] for v in vpcs)
    d.add_vpc_endpoints(vpcs[0]["name"], ["s3", "sts"])
    graph = d.graph()
    assert sum(n["kind"] == "vpc_endpoint" for n in graph["nodes"]) >= 2
    layout_document(d.snapshot(), "detail", vpcs[0]["id"])


def test_start_from_current_uses_the_open_snapshot_then_the_last_collection():
    from diagram_bot.samples import SAMPLES
    stored, saved = {"open": SAMPLES["hybrid"]()}, []
    workspace = Workspace(lambda s: saved.append(s) or f"s{len(saved)}", load=lambda i: stored[i])
    with pytest.raises(ValueError, match="시작할 스냅샷이 없습니다"):
        workspace.start_from_snapshot()
    workspace.current_snapshot = "open"
    result = workspace.start_from_snapshot()
    assert result["snapshot_id"] == "s1" and saved[0]["metadata"]["based_on"]["title"] == stored["open"]["metadata"]["title"]
    with pytest.raises(ValueError, match="스냅샷을 찾을 수 없습니다: gone"):
        workspace.start_from_snapshot("gone")
    # The open snapshot is named in the message the model reads, so "this configuration" has a referent.
    assert workspace.context_note().startswith("\n\n[현재 화면] 수집한 구성 '") and "진행 중인 설계가 이 스냅샷에서 시작함" in workspace.context_note()
    workspace.current_snapshot = None
    assert workspace.context_note() == ""
    tools = {t.tool_spec["name"] for t in build_tools(workspace)}
    assert {"start_from_current", "add_subnet_tier"} <= tools and len(tools) == 29


def test_a_missing_tier_takes_free_space_and_the_right_routes():
    d = Design()
    d.add_vpc("edge", "10.60.0.0/20", 2, tiers=["public", "app"], nat="per_az")
    d.add_subnet_tier("edge", "data")
    d.add_vpc("plain", "10.61.0.0/20", 2, tiers=["app"], nat="none")
    d.add_subnet_tier("plain", "public")
    with pytest.raises(DesignError, match="이미"):
        d.add_subnet_tier("plain", "public")
    graph = d.graph()
    classes = {n["resource_id"]: n["route_class"] for n in graph["nodes"] if n["kind"] == "subnet"}
    assert classes["subnet-edge-data-a"] == classes["subnet-edge-data-b"] == "isolated"
    assert classes["subnet-plain-public-a"] == "public" and classes["subnet-plain-app-a"] == "isolated"
    cidrs = [s["CidrBlock"] for s in d.sample.raw["subnets"] if s["VpcId"] == "vpc-edge"]
    assert len(set(cidrs)) == len(cidrs) == 6
    # A web tier added later joins the NAT route table of its own AZ.
    d.add_subnet_tier("edge", "web")
    assert classes.get("subnet-edge-web-a") is None
    tables = {t["RouteTableId"]: {a.get("SubnetId") for a in t["Associations"]} for t in d.sample.raw["route_tables"]}
    assert "subnet-edge-web-a" in tables["rtb-edge-private-a"] and "subnet-edge-web-b" in tables["rtb-edge-private-b"]
