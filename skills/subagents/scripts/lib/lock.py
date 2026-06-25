"""Cross-platform file-based session lock.

Uses O_CREAT|O_EXCL for atomic lock acquisition (works on all platforms).
Stale locks are detected by age (default 30 minutes).
"""

from __future__ import annotations

import os
import time
from pathlib import Path

_STALE_SECONDS = 30 * 60  # locks older than this are considered stale


def _get_lock_dir() -> Path:
    env = os.environ.get("SU BAGENT_LOCKS", "")
    if env:
        return Path(env)
    return Path(".agents/subagents/locks")


def _get_lock_path(session: str) -> Path:
    return _get_lock_dir() / f"{session}.lock"


def _cleanup_stale() -> None:
    """Remove locks older than _STALE_SECONDS."""
    lock_dir = _get_lock_dir()
    if not lock_dir.is_dir():
        return
    cutoff = time.time() - _STALE_SECONDS
    for lock_file in lock_dir.glob("*.lock"):
        try:
            if lock_file.stat().st_mtime < cutoff:
                lock_file.unlink()
        except OSError:
            pass


def acquire(session: str) -> Path:
    """Acquire a lock for the given session name.

    Uses O_CREAT|O_EXCL for atomic file creation — works on all platforms.
    Returns the lock file path. Raises RuntimeError if already locked.
    """
    _cleanup_stale()

    lock_dir = _get_lock_dir()
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = _get_lock_path(session)

    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_RDWR)
        os.write(fd, str(time.time()).encode())
        os.close(fd)
        return lock_path
    except FileExistsError:
        raise RuntimeError(
            f"Session '{session}' is already running. "
            f"Wait for it with: subagents wait {session}"
        )


def release(lock_path: Path) -> None:
    """Release a previously acquired lock."""
    try:
        lock_path.unlink()
    except OSError:
        pass


def check(session: str) -> bool:
    """Check if a session is currently locked."""
    _cleanup_stale()
    return _get_lock_path(session).exists()


def get_age(session: str) -> float | None:
    """Return lock age in seconds, or None if not locked."""
    lock_path = _get_lock_path(session)
    if not lock_path.exists():
        return None
    try:
        return time.time() - lock_path.stat().st_mtime
    except OSError:
        return None