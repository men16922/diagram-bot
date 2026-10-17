import copy
import json
import subprocess
import sys

from diagram_bot.demo import demo_snapshot
from diagram_bot.diff import diff, markdown
from diagram_bot.samples import SAMPLES


def changed_snapshot():
    snapshot = copy.deepcopy(demo_snapshot())
    raw = snapshot["raw"]
    raw["instances"][0]["InstanceType"] = "t3.large"            # setting change
    raw["instances"] = [i for i in raw["instances"] if i["InstanceId"] != "i-staging"]  # removed
    raw["instances"].append({"InstanceId": "i-demo-new", "VpcId": "vpc-demo", "SubnetId": "subnet-app-c", "InstanceType": "t3.small",
                             "Placement": {"AvailabilityZone": "ap-northeast-2c"}, "Tags": [{"Key": "Name", "Value": "web-new"}]})
    raw["security_groups"][1]["IpPermissions"].pop()             # SSH rule closed
    for result in raw["target_health"]:
        for description in result["Descriptions"]:
            description["TargetHealth"] = {"State": "draining"}  # observation only
    raw["target_health"][0]["Descriptions"].append({"Target": {"Id": "i-demo-new", "Port": 443}, "TargetHealth": {"State": "healthy"}})
    return snapshot


def test_diff_reports_resources_settings_and_relations_but_not_health():
    result = diff(demo_snapshot(), changed_snapshot())
    assert [n["name"] for n in result["added"]] == ["web-new"]
    assert [n["name"] for n in result["removed"]] == ["staging-api"]
    changes = {n["name"]: {f["field"] for f in n["fields"]} for n in result["changed"]}
    assert changes == {"web-a": {"InstanceType"}, "demo-web-sg": {"IpPermissions"}}
    assert any(r["target"] == "web-new" and r["relation"] == "registered_target" for r in result["relations_added"])
    assert result["comparable"] and result["summary"]["changed"] == 2
    text = markdown(result)
    assert "`InstanceType`: t3.small → t3.large" in text and "부분 조회" in text


def test_identical_snapshots_have_no_changes_and_other_scope_is_flagged():
    assert not any(diff(demo_snapshot(), demo_snapshot())["summary"].values())
    elsewhere = SAMPLES["three-tier"]()
    elsewhere["metadata"]["region"] = "us-east-1"
    other = diff(demo_snapshot(), elsewhere)
    assert other["comparable"] is False and other["summary"]["added"] and other["summary"]["removed"]


def test_cli_diff_exit_code_detects_drift(tmp_path):
    before, after = tmp_path / "before.json", tmp_path / "after.json"
    before.write_text(json.dumps(demo_snapshot()), encoding="utf-8")
    after.write_text(json.dumps(changed_snapshot()), encoding="utf-8")
    run = lambda b, a, *extra: subprocess.run([sys.executable, "-m", "diagram_bot.cli", "diff", str(b), str(a), *extra],
                                              capture_output=True, text=True)
    assert run(before, after, "--fail-on-change").returncode == 4
    assert run(before, before, "--fail-on-change").returncode == 0
    assert json.loads(run(before, after, "--format", "json").stdout)["summary"]["added"] == 1
