"""The library surface (api.py) works without the web app: what an embedding backend calls."""
from diagram_bot import api
from diagram_bot.drawio import validate_document
from test_aws_emulator import ACCOUNT, REGION, environment  # noqa: F401 - fixture


def test_the_library_covers_collect_render_review_diff_and_iac(environment):  # noqa: F811
    session, built, _ = environment
    snapshot = api.collect_snapshot(session, REGION, expected_account=ACCOUNT)
    assert snapshot["graph"]["nodes"] and snapshot["metadata"]["account_id"] == ACCOUNT
    for kwargs in ({}, {"view": "detail", "vpc": built["vpc"]}):
        validate_document(api.render(snapshot, **kwargs))
    assert api.view(snapshot, "detail", built["vpc"])["counts"]["visible"] > 5
    assert api.review_snapshot(snapshot)["summary"]["high"] >= 1  # the open security group
    sample = api.SAMPLES["three-tier"]()
    assert not any(api.diff_snapshots(sample, api.SAMPLES["three-tier"]())["summary"].values())
    assert api.cloudformation(sample)["Resources"]
    # Raw snapshots (no graph yet) are accepted everywhere.
    raw = {k: v for k, v in sample.items() if k != "graph"}
    validate_document(api.render(raw))
    assert "summary" in api.review_snapshot(raw)
    design = api.Design()
    design.apply_standard("genai-rag")
    assert api.review_snapshot(design.snapshot())["summary"]["high"] == 0
