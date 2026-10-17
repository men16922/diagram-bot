"""Change review between two snapshots of the same account and region: resources, settings, relations."""
import json

from .graph import build_graph, upgrade_graph

# Observations that change between collections without a configuration change.
VOLATILE = {"State", "TargetHealth", "runningCount", "pendingCount", "events", "deployments", "VgwTelemetry", "VpnTunnels",
            "LaunchTime", "lastStatus", "healthStatus", "startedAt", "createdAt", "updatedAt", "ClusterStatus", "LifeCycleState",
            "NumberOfMountTargets", "SizeInBytes", "Instances", "DesiredCapacity", "Status", "currentServiceDeployment",
            "NodeStatus", "BrokerIds", "Estimated", "CurrentRole", "Processing", "UpgradeProcessing", "AvailableIpAddressCount"}


def diff(before, after):
    """Compare two graphs; nodes match on their account/region/kind/resource key, relations on their stable IDs."""
    before, after = with_graph(before), with_graph(after)
    old = {n["id"]: n for n in before["graph"]["nodes"]}
    new = {n["id"]: n for n in after["graph"]["nodes"]}
    old_edges = {e["id"]: e for e in before["graph"]["edges"]}
    new_edges = {e["id"]: e for e in after["graph"]["edges"]}
    describe = lambda n: {"id": n["id"], "kind": n["kind"], "name": n["name"], "resource_id": n["resource_id"]}
    changed = []
    for key in sorted(old.keys() & new.keys()):
        a, b = old[key], new[key]
        if a["reference_only"] or b["reference_only"]:
            continue  # a reference carries no settings to compare
        fields = sorted(k for k in (set(a["attributes"]) | set(b["attributes"])) - VOLATILE
                        if canonical(a["attributes"].get(k)) != canonical(b["attributes"].get(k)))
        for derived in ("db_role", "route_class"):
            if a.get(derived) != b.get(derived):
                fields.append(derived)
        if fields:
            changed.append({**describe(b), "fields": [{"field": f, "before": value(a, f), "after": value(b, f)} for f in fields]})
    relation = lambda e, nodes: {"id": e["id"], "relation": e["relation"], "evidence": e["evidence"],
                                 "source": nodes.get(e["source"], {}).get("name", e["source"]),
                                 "target": nodes.get(e["target"], {}).get("name", e["target"]), "qualifiers": e.get("qualifiers", {})}
    result = {
        "before": meta(before), "after": meta(after),
        "added": [describe(new[k]) for k in sorted(new.keys() - old.keys()) if not new[k]["reference_only"]],
        "removed": [describe(old[k]) for k in sorted(old.keys() - new.keys()) if not old[k]["reference_only"]],
        "changed": changed,
        "relations_added": [relation(new_edges[k], new) for k in sorted(new_edges.keys() - old_edges.keys())],
        "relations_removed": [relation(old_edges[k], old) for k in sorted(old_edges.keys() - new_edges.keys())],
    }
    result["summary"] = {k: len(result[k]) for k in ("added", "removed", "changed", "relations_added", "relations_removed")}
    same = (before["metadata"]["account_id"], before["metadata"]["region"]) == (after["metadata"]["account_id"], after["metadata"]["region"])
    result["comparable"] = same
    return result


def with_graph(snapshot):
    """Collector output has no graph yet; stored snapshots may predate relation IDs."""
    if "graph" not in snapshot:
        return {**snapshot, "graph": build_graph(snapshot)}
    upgrade_graph(snapshot["graph"])
    return snapshot


def canonical(value):
    return json.dumps(value, sort_keys=True, default=str)


def value(node, field):
    if field in ("db_role", "route_class"):
        return node.get(field)
    item = node["attributes"].get(field)
    text = item if isinstance(item, str) else canonical(item)
    return text if len(text) <= 200 else text[:199] + "…"


def meta(snapshot):
    m = snapshot["metadata"]
    return {"account_id": m["account_id"], "region": m["region"], "finished_at": m["finished_at"], "status": m["status"]}


def markdown(result):
    s = result["summary"]
    b, a = result["before"], result["after"]
    lines = ["# 구성 변경 비교", "", f"- 이전: {b['account_id']} / {b['region']} · {b['finished_at']} ({b['status']})",
             f"- 이후: {a['account_id']} / {a['region']} · {a['finished_at']} ({a['status']})",
             f"- 리소스 추가 {s['added']} · 삭제 {s['removed']} · 설정 변경 {s['changed']} · 관계 추가 {s['relations_added']} · 관계 삭제 {s['relations_removed']}", ""]
    if not result["comparable"]:
        lines += ["> 계정 또는 리전이 달라 모든 리소스가 추가·삭제로 표시됩니다.", ""]
    if "partial" in (a["status"], b["status"]):
        lines += ["> 부분 조회 스냅샷이 포함되어 있어 삭제로 보이는 리소스는 권한 문제로 조회되지 않았을 수 있습니다.", ""]
    for key, title in (("added", "추가된 리소스"), ("removed", "삭제된 리소스")):
        if result[key]:
            lines += [f"## {title}", ""] + [f"- {n['kind']} · {n['name']} (`{n['resource_id']}`)" for n in result[key]] + [""]
    if result["changed"]:
        lines += ["## 설정이 바뀐 리소스", ""]
        for n in result["changed"]:
            lines.append(f"- {n['kind']} · {n['name']}")
            lines += [f"  - `{f['field']}`: {f['before']} → {f['after']}" for f in n["fields"]]
        lines.append("")
    for key, title in (("relations_added", "추가된 관계"), ("relations_removed", "삭제된 관계")):
        if result[key]:
            lines += [f"## {title}", ""] + [f"- {r['source']} —{r['relation']}→ {r['target']}" for r in result[key]] + [""]
    return "\n".join(lines)
