"""Stable node ID generation and content hashing."""

from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def make_file_id(path: Path, repo_root: Path) -> str:
    rel = path.relative_to(repo_root).as_posix()
    return f"file:{rel}"


def make_func_id(file_rel: str, qualified_name: str) -> str:
    return f"func:{file_rel}::{qualified_name}"


def make_class_id(file_rel: str, class_name: str) -> str:
    return f"class:{file_rel}::{class_name}"


def make_type_id(file_rel: str, type_name: str) -> str:
    return f"type:{file_rel}::{type_name}"


def make_module_id(module_name: str) -> str:
    return f"module:{module_name}"


def make_commit_id(sha: str) -> str:
    return f"commit:{sha}"
