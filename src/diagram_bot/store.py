"""Where snapshots and edited documents live: JSON files under one data directory.

The web layer only uses the methods below, so another backend (a database, S3) can replace these classes when the
app is embedded elsewhere. Snapshots are immutable once saved; documents carry a revision for optimistic locking and
keep superseded revisions so an explicit relayout, overwrite or restore never loses an edit.
"""
import hashlib
import json
import logging
import os
import re
import threading
from pathlib import Path
from uuid import uuid4

from .collector import now
from .fileio import atomic_write
from .graph import build_graph, upgrade_graph

HISTORY = 20  # archived revisions kept per document


def data_path():
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "diagram-bot"


def atomic_json(path, value):
    atomic_write(path, json.dumps(value, ensure_ascii=False, default=str))


def read_json(path, default=None):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


class NotFound(LookupError):
    """No such snapshot, document revision or job; the message is shown to the user."""


class Conflict(RuntimeError):
    """The document changed since the caller read it (another window saved)."""


def validate_snapshot(snapshot):
    if snapshot.get("schema_version") != 1:
        raise ValueError("지원하지 않는 스냅샷 버전입니다.")
    metadata = snapshot["metadata"]
    for name in ("account_id", "region", "started_at", "finished_at", "status"):
        if not isinstance(metadata.get(name), str):
            raise ValueError(f"스냅샷 metadata.{name} 필드가 필요합니다.")
    if not isinstance(snapshot.get("coverage"), list) or not isinstance(snapshot.get("raw"), dict):
        raise ValueError("스냅샷 coverage/raw 형식이 잘못되었습니다.")


class SnapshotStore:
    def __init__(self, directory):
        self.directory = Path(directory) / "snapshots"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()
        self.cache, self.summaries = {}, {}  # parsed snapshots (a few) and list rows, keyed by file modification time

    def save(self, snapshot, identifier=None):
        """Validate, (re)build the graph from raw API data and store; returns the snapshot ID."""
        validate_snapshot(snapshot)
        snapshot = {**snapshot, "graph": build_graph(snapshot)}
        identifier = identifier or uuid4().hex
        atomic_json(self.directory / f"{identifier}.json", snapshot)
        return identifier

    def get(self, identifier):
        if not re.fullmatch(r"[a-zA-Z0-9-]{1,64}", identifier or ""):
            raise NotFound("스냅샷을 찾을 수 없습니다.")
        path = self.directory / f"{identifier}.json"
        if not path.is_file():
            raise NotFound("스냅샷을 찾을 수 없습니다.")
        key = (identifier, path.stat().st_mtime_ns)
        # Read once: another request may evict the key (a re-saved snapshot) between a check and a lookup.
        snapshot = self.cache.get(key)
        if snapshot is None:
            snapshot = json.loads(path.read_text(encoding="utf-8"))
            upgrade_graph(snapshot["graph"])
            with self.lock:
                for old in [k for k in self.cache if k[0] == identifier] + list(self.cache)[:max(0, len(self.cache) - 7)]:
                    self.cache.pop(old, None)
                self.cache[key] = snapshot
        return snapshot

    def list(self):
        """Newest first: id, metadata and resource count of every stored snapshot."""
        result, keys = [], set()
        for path in self.directory.glob("*.json"):
            key = (path.stem, path.stat().st_mtime_ns)
            if key not in self.summaries:
                try:
                    snapshot = json.loads(path.read_text(encoding="utf-8"))
                    row = {"id": path.stem, "metadata": snapshot["metadata"], "resource_count": len(snapshot["graph"]["nodes"])}
                    if not isinstance(row["metadata"].get("finished_at"), str):
                        raise TypeError("metadata.finished_at")
                except (OSError, ValueError, LookupError, TypeError, AttributeError):
                    # One unreadable file must not hide every other snapshot.
                    logging.getLogger(__name__).warning("Skipping unreadable snapshot file %s", path.name, exc_info=True)
                    continue
                self.summaries[key] = row
            keys.add(key)
            result.append(self.summaries[key])
        with self.lock:
            for stale in set(self.summaries) - keys:
                self.summaries.pop(stale, None)
        return sorted(result, key=lambda row: row["metadata"]["finished_at"], reverse=True)

    def seed(self, demo, samples, version):
        """The demo once, and each bundled sample whenever its architecture changed (sample version)."""
        if not (self.directory / "demo.json").exists():
            self.save(demo(), "demo")
        for key, build in samples.items():
            path = self.directory / f"sample-{key}.json"
            try:
                current = json.loads(path.read_text(encoding="utf-8"))["metadata"].get("sample_version") == version
            except (OSError, ValueError, KeyError, TypeError):
                current = False
            if not current:
                self.save(build(), f"sample-{key}")


class DocumentStore:
    """draw.io documents per snapshot × view scope, with revisions and archived history."""

    def __init__(self, directory):
        self.directory = Path(directory) / "documents"
        self.directory.mkdir(parents=True, exist_ok=True)
        self.lock = threading.Lock()

    def path(self, identifier, scope):
        view, vpc, resource, references, routes = scope
        # Detail views keep the pre-view key so documents saved by earlier versions stay attached;
        # a route-filtered detail view is its own document.
        key = (f"{identifier}|{vpc}|{references}" if view == "detail"
               else f"{identifier}|{view}|{vpc}|{resource}|{references}") + (f"|routes={routes}" if routes else "")
        return self.directory / f"{hashlib.sha256(key.encode()).hexdigest()}.json"

    def read(self, identifier, scope):
        """(saved document or None, archived history)."""
        path = self.path(identifier, scope)
        return read_json(path), read_json(path.with_suffix(".history.json"), [])

    def archive(self, path, saved, reason):
        history_path = path.with_suffix(".history.json")
        history = read_json(history_path, [])
        if saved.get("xml"):
            history = (history + [{"revision": saved["revision"], "saved_at": saved.get("saved_at"), "reason": reason, "xml": saved["xml"]}])[-HISTORY:]
            atomic_json(history_path, history)
        return history

    def replace(self, identifier, scope, revision, xml=None, reason="편집 저장", source_revision=None, merge=None):
        """Store a new revision if `revision` is still current; returns (document, history).

        xml: the new content; source_revision: restore that archived revision instead; merge(old_xml, xml): combine the
        current edit with the new content (relayout keeps user notes). An unchanged save is a no-op."""
        path = self.path(identifier, scope)
        with self.lock:
            saved = read_json(path, {"revision": 0})
            if saved["revision"] != revision:
                raise Conflict("다른 창에서 편집본을 저장했습니다. 새로고침 후 다시 편집하세요.")
            if source_revision is not None:
                history = read_json(path.with_suffix(".history.json"), [])
                source = next((h for h in history if h["revision"] == source_revision), None)
                if not source:
                    raise NotFound("보관된 편집본을 찾을 수 없습니다.")
                xml, reason = source["xml"], f"v{source['revision']} 복원 전"
            elif merge is None and saved.get("xml") == xml:
                return saved, read_json(path.with_suffix(".history.json"), [])
            if merge and saved.get("xml"):
                xml = merge(saved["xml"], xml)
            history = self.archive(path, saved, reason)
            saved = {"xml": xml, "revision": saved["revision"] + 1, "saved_at": now()}
            atomic_json(path, saved)
        return saved, history
