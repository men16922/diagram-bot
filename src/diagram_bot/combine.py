"""Several account × region snapshots as one snapshot, so the overview can draw the network across accounts
(Transit Gateways shared through RAM, cross-account peering) and every VPC keeps its detail view.

The parts are stored unchanged; the graph is merged from them (graph.merge_graphs). Nothing is inferred
between accounts beyond resource IDs the APIs report on both sides.
"""
from .errors import InputError


def combine_snapshots(snapshots, title=None):
    parts = []
    for snapshot in snapshots:
        parts += snapshot["parts"] if snapshot.get("parts") else [{k: v for k, v in snapshot.items() if k != "graph"}]
    if len(parts) < 2:
        raise InputError("계정·리전 스냅샷을 두 개 이상 고르세요.")
    keys = [(p["metadata"]["account_id"], p["metadata"]["region"]) for p in parts]
    if len(set(keys)) != len(keys):
        raise InputError("같은 계정·리전 스냅샷이 두 번 들어 있습니다. 하나만 고르세요.")
    meta = [p["metadata"] for p in parts]
    accounts, regions = sorted({m["account_id"] for m in meta}), sorted({m["region"] for m in meta})
    scope = sorted({item for m in meta for item in m.get("scope", [])})
    return {"schema_version": 1, "metadata": {
        "combined": True, "accounts": accounts, "regions": regions,
        "account_id": accounts[0] if len(accounts) == 1 else f"{len(accounts)}개 계정",
        "region": regions[0] if len(regions) == 1 else f"{len(regions)}개 리전",
        "partition": meta[0].get("partition", "aws"), "started_at": min(m["started_at"] for m in meta),
        "finished_at": max(m["finished_at"] for m in meta),
        "status": "complete" if all(m.get("status") == "complete" for m in meta) else "partial",
        "demo": all(m.get("demo") for m in meta), "title": title or f"계정 통합 · {', '.join(accounts)}",
        "parts": [{"account_id": m["account_id"], "region": m["region"], "finished_at": m["finished_at"], "title": m.get("title")}
                  for m in meta],
        "scope": scope,
        "limitations": ["계정·리전별 수집을 합친 보기입니다. 수집 시점이 서로 다를 수 있습니다.",
                        "계정 간 연결은 양쪽 API가 같은 리소스 ID(Transit Gateway, 피어링)를 보고할 때만 잇습니다.",
                        "설정 관계만 표시하며 실제 통신 여부는 확인하지 않습니다."]},
        "coverage": [{**c, "account_id": m["account_id"], "region": m["region"]} for p, m in zip(parts, meta) for c in p["coverage"]],
        "raw": {}, "parts": parts}
