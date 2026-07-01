"""Shared backend utilities."""

from __future__ import annotations

import json
import shutil
import subprocess
import time


def check_acp(command: str | list[str], *, timeout: float = 5.0) -> bool:
    """Check if an agent's ACP mode is available.

    Starts the agent in ACP mode and sends initialize.  Returns True if
    the agent responds with a valid protocol version.  Does NOT require
    authentication — agents that require auth are still considered ACP-capable.

    Args:
        command: Either a binary name (e.g. "kimi" → runs "kimi acp") or
                 a full command list (e.g. ["gemini", "--acp"]).
    """
    if isinstance(command, str):
        binary = command
        acp_cmd = [binary, "acp"]
    else:
        binary = command[0]
        acp_cmd = list(command)

    if not shutil.which(binary):
        return False

    proc = None
    try:
        proc = subprocess.Popen(
            acp_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return False

    try:
        _write(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": 1,
            "clientInfo": {"name": "subagents-probe", "version": "0.0"},
            "capabilities": {"prompt": {"text": True}, "terminal": False},
        }})
        resp = _expect_id(proc, 1, timeout)
        if resp is None:
            return False
        if "error" in resp:
            return False
        # initialize succeeded — ACP is available
        return True
    except Exception:
        return False
    finally:
        if proc is not None:
            try:
                proc.stdin.close()
            except OSError:
                pass
            try:
                proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()


def _write(proc: subprocess.Popen, data: dict) -> None:
    proc.stdin.write(json.dumps(data, ensure_ascii=False) + "\n")
    proc.stdin.flush()


def _expect_id(proc: subprocess.Popen, req_id: int, timeout: float) -> dict | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        line = proc.stdout.readline()
        if not line:
            return None
        try:
            msg = json.loads(line.strip())
        except json.JSONDecodeError:
            continue
        if msg.get("id") == req_id:
            return msg
    return None