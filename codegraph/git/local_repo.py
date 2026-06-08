"""Local git repository operations via subprocess (pygit2-optional)."""

from __future__ import annotations

import subprocess
from pathlib import Path


class LocalRepo:
    def __init__(self, root: Path):
        self.root = root.resolve()

    def get_changed_files_since(self, since_sha: str | None) -> list[dict]:
        """Return list of {path, status} for files changed since since_sha."""
        if not since_sha:
            return []
        try:
            result = subprocess.run(
                ["git", "diff", "--name-status", since_sha, "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            changed = []
            for line in result.stdout.splitlines():
                parts = line.split("\t", 1)
                if len(parts) == 2:
                    status, path = parts
                    changed.append({"status": status[0], "path": path.strip()})
            return changed
        except subprocess.CalledProcessError:
            return []

    def get_changed_files_between(self, sha1: str, sha2: str) -> list[dict]:
        """Return list of {path, status} for files changed between sha1 and sha2."""
        try:
            result = subprocess.run(
                ["git", "diff", "--name-status", sha1, sha2],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            changed = []
            for line in result.stdout.splitlines():
                parts = line.split("\t")
                if len(parts) >= 2:
                    raw_status = parts[0].strip()
                    # Renames have format "R\told\tnew"; use new path
                    path = parts[-1].strip()
                    changed.append({"status": raw_status[0], "path": path})
            return changed
        except subprocess.CalledProcessError:
            return []

    def get_file_at_sha(self, sha: str, path: str) -> bytes | None:
        """Return the raw bytes of a file at a given commit SHA. None if missing."""
        try:
            result = subprocess.run(
                ["git", "show", f"{sha}:{path}"],
                cwd=self.root,
                capture_output=True,
                check=True,
            )
            return result.stdout
        except subprocess.CalledProcessError:
            return None

    def resolve_ref(self, ref: str) -> str | None:
        """Resolve a branch name / tag / short SHA to a full SHA."""
        try:
            result = subprocess.run(
                ["git", "rev-parse", "--verify", ref],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

    def get_commits_since(self, since_sha: str | None, limit: int = 50) -> list[dict]:
        """Return recent commits as dicts, newest first."""
        try:
            sep = "||SEP||"
            fmt = f"%H{sep}%h{sep}%an{sep}%ae{sep}%at{sep}%s"
            args = ["git", "log", f"--format={fmt}", f"-{limit}"]
            if since_sha:
                args.append(f"{since_sha}..HEAD")
            result = subprocess.run(
                args, cwd=self.root, capture_output=True, text=True, check=True
            )
            commits = []
            for line in result.stdout.splitlines():
                parts = line.split(sep)
                if len(parts) >= 6:
                    sha = parts[0]
                    files = self._get_commit_files(sha)
                    commits.append(
                        {
                            "sha": sha,
                            "short_sha": parts[1],
                            "author": parts[2],
                            "author_email": parts[3],
                            "timestamp": int(parts[4]),
                            "message": parts[5],
                            "files_changed": files,
                        }
                    )
            return commits
        except subprocess.CalledProcessError:
            return []

    def _get_commit_files(self, sha: str) -> list[str]:
        try:
            result = subprocess.run(
                ["git", "diff-tree", "--no-commit-id", "-r", "--name-only", sha],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            return [l.strip() for l in result.stdout.splitlines() if l.strip()]
        except subprocess.CalledProcessError:
            return []

    def get_head_sha(self) -> str | None:
        try:
            result = subprocess.run(
                ["git", "rev-parse", "HEAD"],
                cwd=self.root,
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            return None

