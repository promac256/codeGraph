"""Incremental graph update driven by git commit diffs."""

from __future__ import annotations

import hashlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

log = logging.getLogger(__name__)

if TYPE_CHECKING:
    from codegraph.graph.builder import GraphBuilder
    from codegraph.graph.store import GraphStore
    from codegraph.parsers.registry import ParserRegistry


class GraphUpdater:
    def __init__(
        self,
        store: "GraphStore",
        builder: "GraphBuilder",
        repo_root: Path,
        registry: "ParserRegistry",
    ):
        self.store = store
        self.builder = builder
        self.repo_root = repo_root.resolve()
        self.registry = registry

    def update_from_commits(self, since_sha: str | None = None) -> dict:
        from codegraph.git.local_repo import LocalRepo

        repo = LocalRepo(self.repo_root)
        last_sha = since_sha or self.store.get_config("last_indexed_sha")

        stats = {
            "commits_processed": 0,
            "files_updated": 0,
            "files_deleted": 0,
            "errors": 0,
        }

        changed_files = repo.get_changed_files_since(last_sha)
        if not changed_files:
            # No git history to diff — try HEAD commits
            new_commits = repo.get_commits_since(last_sha, limit=20)
            if new_commits:
                changed = set()
                for c in new_commits:
                    changed.update(c.get("files_changed", []))
                changed_files = [{"path": p, "status": "M"} for p in changed]

        to_delete = [f for f in changed_files if f["status"] == "D"]
        to_update = [f for f in changed_files if f["status"] != "D"]

        for entry in to_delete:
            file_id = f"file:{entry['path']}"
            with self.store.transaction():
                self.store.remove_file_nodes(file_id)
            stats["files_deleted"] += 1

        for entry in to_update:
            path = self.repo_root / entry["path"]
            if not path.exists():
                continue
            try:
                source = path.read_bytes()
                new_hash = hashlib.sha256(source).hexdigest()
                if self.store.get_file_sha(entry["path"]) == new_hash:
                    continue

                parser = self.registry.get_parser(path)
                if parser is None:
                    continue

                file_id = f"file:{entry['path']}"
                with self.store.transaction():
                    self.store.remove_file_nodes(file_id)
                    result = parser.parse(path, source, self.repo_root)
                    self.builder._ingest_parse_result(result)
                stats["files_updated"] += 1
            except Exception as e:
                stats["errors"] += 1
                log.warning("failed to update %s: %s", entry["path"], e)

        new_commits = repo.get_commits_since(last_sha, limit=50)
        for c in new_commits:
            self._ingest_commit(c)
            stats["commits_processed"] += 1

        self.builder._resolve_cross_file_references()

        if new_commits:
            self.store.set_config("last_indexed_sha", new_commits[0]["sha"])
        elif not last_sha:
            head = repo.get_head_sha()
            if head:
                self.store.set_config("last_indexed_sha", head)

        return stats

    def _ingest_commit(self, commit: dict) -> None:
        from codegraph.models import EdgeKind, GraphEdge

        with self.store.transaction():
            self.store.insert_commit(commit)
            commit_id = f"commit:{commit['sha']}"
            for file_path in commit.get("files_changed", []):
                file_id = f"file:{file_path}"
                self.store.link_file_commit(file_id, commit["sha"])
                self.store.upsert_edge(
                    GraphEdge(
                        src=commit_id,
                        dst=file_id,
                        kind=EdgeKind.MODIFIES,
                        meta={"insertions": 0, "deletions": 0},
                    )
                )
