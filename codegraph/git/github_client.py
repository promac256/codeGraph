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

    def get_merged_prs(
        self, owner: str, repo: str, limit: int = 30
    ) -> list[dict]:
        """Return recently merged pull requests."""
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/pulls",
            params={"state": "closed", "sort": "updated", "direction": "desc",
                    "per_page": min(limit, 100)},
        )
        resp.raise_for_status()
        return [pr for pr in resp.json() if pr.get("merged_at")][:limit]

    def get_pr_review_comments(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict]:
        """Return inline review comments on a PR."""
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/pulls/{pr_number}/comments",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()

    def get_pr_reviews(
        self, owner: str, repo: str, pr_number: int
    ) -> list[dict]:
        """Return top-level reviews (body text) on a PR."""
        resp = self._client.get(
            f"{self.BASE}/repos/{owner}/{repo}/pulls/{pr_number}/reviews",
            params={"per_page": 100},
        )
        resp.raise_for_status()
        return resp.json()

    def close(self) -> None:
        self._client.close()
