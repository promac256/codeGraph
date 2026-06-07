"""Settings and configuration."""

from __future__ import annotations

from pathlib import Path

import toml
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

CODEGRAPH_DIR = ".codegraph"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="CODEGRAPH_", env_file=".env")

    repo_path: Path = Field(default=Path("."))
    anthropic_api_key: str | None = Field(default=None)
    github_token: str | None = Field(default=None)
    max_workers: int = Field(default=8)
    token_budget: int = Field(default=8000)
    llm_enrich: bool = Field(default=False)
    log_level: str = Field(default="INFO")

    @property
    def codegraph_dir(self) -> Path:
        return self.repo_path / CODEGRAPH_DIR

    @property
    def db_path(self) -> Path:
        return self.codegraph_dir / "graph.db"

    @property
    def snapshot_path(self) -> Path:
        return self.codegraph_dir / "graph.nx.json.gz"

    @property
    def context_pack_path(self) -> Path:
        return self.codegraph_dir / "context_pack.json"

    @property
    def session_notes_path(self) -> Path:
        return self.codegraph_dir / "session_notes.md"

    @classmethod
    def from_repo(cls, repo_path: Path) -> "Settings":
        repo_path = repo_path.resolve()
        config_file = repo_path / "codegraph.toml"
        overrides: dict = {}
        if config_file.exists():
            raw = toml.load(config_file)
            overrides = {k: v for k, v in raw.items() if k != "repo_path"}
        s = cls(repo_path=repo_path, **overrides)
        s.codegraph_dir.mkdir(exist_ok=True)
        return s
