"""Concurrency helpers."""

from __future__ import annotations

import os
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager


@contextmanager
def cpu_bound_executor(max_workers: int | None = None):
    workers = max_workers or min(32, (os.cpu_count() or 1) + 4)
    with ThreadPoolExecutor(max_workers=workers) as executor:
        yield executor
