"""The snapshot envelope, the one contract between what produces a snapshot (collector, samples, designs, the demo,
combine) and everything that reads it (graph, review, documents, CloudFormation). Fields: docs/wiki/snapshot-model.md."""
import json
from pathlib import Path

SCHEMA_VERSION = 1


def make_snapshot(metadata, coverage, raw, **extra):
    """extra: further top-level keys after raw (a combined snapshot's parts)."""
    return {"schema_version": SCHEMA_VERSION, "metadata": metadata, "coverage": coverage, "raw": raw, **extra}


def load_snapshot(path):
    """A snapshot JSON file; ValueError when it is not one of this schema version."""
    snapshot = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(snapshot, dict) or snapshot.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("지원하지 않는 snapshot schema_version")
    return snapshot
