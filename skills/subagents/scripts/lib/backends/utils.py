"""Shared backend utilities."""

from __future__ import annotations

import subprocess


def check_acp(command: str) -> bool:
    """Check if an agent's ACP mode is available."""
    import shutil

    if not shutil.which(command):
        return False
    try:
        result = subprocess.run(
            [command, "acp", "--help"], capture_output=True, timeout=5
        )
        return result.returncode == 0
    except Exception:
        return False