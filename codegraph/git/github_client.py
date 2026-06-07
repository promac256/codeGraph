"""GitHub REST API client for remote repository access."""

from __future__ import annotations

import httpx


class GitHubClient:
    """Minimal GitHub REST API v3 client."""

    BASE = "https://api.github.com"

    def __init__(self, token: str | None = None):
        headers = {"Accept": "application/vnd.github+json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = httpx.Client(headers=headers, timeout=30.0)

    def get_file_tree(self, owner: str, repo: str, sha: str = "HEAD") -> list[dict]:
        """Return flat file tree for a repo."""
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/git/trees/{sha}",
            params={"recursive": "1"},
        )
        resp.raise_for_status()
        return resp.json().get("tree", [])

    def get_file_content(self, owner: str, repo: str, path: str) -> bytes:
        """Download raw file content."""
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/contents/{path}",
            headers={"Accept": "application/vnd.github.raw"},
        )
        resp.raise_for_status()
        return resp.content

    def get_recent_commits(
        self, owner: str, repo: str, branch: str = "main", limit: int = 30
    ) -> list[dict]:
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/commits",
            params={"sha": branch, "per_page": limit},
        )
        resp.raise_for_status()
        return resp.json()

    def get_compare(
        self, owner: str, repo: str, base: str, head: str
    ) -> dict:
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/compare/{base}...{head}"
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
