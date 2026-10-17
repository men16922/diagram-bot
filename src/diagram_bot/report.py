import csv
import io
import json
from importlib.resources import files
from pathlib import Path

from .graph import build_graph
from .drawio import generate_document


def csv_safe(value):
    value = str(value)
    return "'" + value if value.lstrip().startswith(("=", "+", "-", "@")) else value


def write_report(snapshot, directory):
    snapshot = {**snapshot, "graph": build_graph(snapshot)}
    output = Path(directory)
    output.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(snapshot, ensure_ascii=False, indent=2, default=str)
    (output / "snapshot.json").write_text(payload, encoding="utf-8")
    (output / "topology.drawio").write_text(generate_document(snapshot), encoding="utf-8")
    # JSON is embedded in HTML; escape characters capable of closing a script tag.
    embedded = payload.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    template = files("diagram_bot").joinpath("viewer.html").read_text(encoding="utf-8")
    (output / "index.html").write_text(template.replace("__SNAPSHOT_JSON__", embedded), encoding="utf-8")
    buffer = io.StringIO(newline="")
    fields = ["account", "region", "kind", "resource_id", "name", "reference_only", "source", "attributes"]
    writer = csv.writer(buffer)
    writer.writerow(fields + ["collection_started_at", "collection_finished_at", "collection_status"])
    for node in snapshot["graph"]["nodes"]:
        writer.writerow([csv_safe(json.dumps(node[f], ensure_ascii=False, default=str) if isinstance(node[f], dict)
                                 else node[f] if node[f] is not None else "") for f in fields] +
                        [snapshot["metadata"][k] for k in ("started_at", "finished_at", "status")])
    (output / "inventory.csv").write_text(buffer.getvalue(), encoding="utf-8-sig")
    checks = ["# TA 검토 자료", "", f"- 계정: {snapshot['metadata']['account_id']}",
              f"- 리전: {snapshot['metadata']['region']}",
              f"- 수집 구간: {snapshot['metadata']['started_at']} ~ {snapshot['metadata']['finished_at']}",
              f"- 수집 상태: {snapshot['metadata']['status']}",
              f"- 샘플 데이터: {snapshot['metadata'].get('demo', False)}", "", "## 조회 범위와 결과", ""]
    for row in snapshot["coverage"]:
        checks.append(f"- {row['service']} / {row['operation']}: {row['status']}, "
                      f"{row.get('items', '—')}개, 오류 {row.get('error_code', '없음')}; "
                      + (f"필요 권한 {row['permission']}; " if row["status"] != "complete" and row.get("permission") else "") +
                      f"범위 {json.dumps(row.get('parameters', {}), ensure_ascii=False)}")
    checks += ["", "## 해석 범위", ""] + [f"- {s}" for s in snapshot["metadata"].get("limitations", [])]
    checks += ["", "## 검토할 항목", "", "- 조회 불가·부분 조회 범위의 권한 및 재수집 필요 여부",
               "- 참조만 확인된 리소스와 미확인 배치 정보", "- VPC·서브넷·가용 영역 배치가 설계 의도와 일치하는지",
               "- 대상 그룹의 등록 대상과 조회 시점의 TargetHealth 상태",
               "- 실제 통신·보안 적합성 판정은 별도 증거로 검토", ""]
    (output / "review.md").write_text("\n".join(checks), encoding="utf-8")
    return output / "index.html"
