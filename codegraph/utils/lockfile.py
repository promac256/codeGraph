"""Cross-platform repo-level lock preventing concurrent graph writes.

`codegraph watch` spawns `codegraph update` on every commit; two rapid commits
previously raced two writers against the same SQLite database. The lock is a
`.codegraph/.lock` file created with O_EXCL containing the holder's pid; a
stale lock (holder no longer running) is reclaimed automatically.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import contextmanager
from pathlib import Path

log = logging.getLogger(__name__)


class LockHeldError(RuntimeError):
    """Another codegraph process holds the repo lock."""


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if os.name == "nt":
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid
            )
            if handle:
                ctypes.windll.kernel32.CloseHandle(handle)
                return True
            return False
        os.kill(pid, 0)
        return True
    except (OSError, AttributeError):
        return False


@contextmanager
def repo_lock(codegraph_dir: Path, timeout: float = 10.0, poll: float = 0.25):
    """Acquire the exclusive graph-write lock, waiting up to `timeout` seconds.

    Raises LockHeldError if the lock is still held by a live process after
    the timeout. Stale locks (dead pid, unreadable content) are reclaimed.
    """
    codegraph_dir.mkdir(exist_ok=True)
    lock_path = codegraph_dir / ".lock"
    deadline = time.monotonic() + timeout

    while True:
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            break
        except FileExistsError:
            try:
                holder = int(lock_path.read_text().strip() or "0")
            except (OSError, ValueError):
                holder = 0
            if holder and not _pid_alive(holder):
                log.warning("reclaiming stale lock held by dead pid %d", holder)
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            if time.monotonic() >= deadline:
                raise LockHeldError(
                    f"another codegraph process (pid {holder or 'unknown'}) is "
                    f"updating this graph — retry shortly or remove {lock_path} "
                    "if no codegraph process is running"
                )
            time.sleep(poll)

    try:
        yield
    finally:
        try:
            lock_path.unlink()
        except OSError:
            pass
