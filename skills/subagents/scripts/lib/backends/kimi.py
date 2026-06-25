"""Kimi backend — CLI with ACP fallback."""

from __future__ import annotations

import re
import sys

from base import BaseBackend
from acp_backend import AcpBackend
from cli_backend import CliBackend
from utils import check_acp

_SESSION_ID_RE = re.compile(r"kimi -r (session_[a-f0-9-]+)")


class KimiBackend(BaseBackend):
    """Backend for kimi-code. Tries ACP first, falls back to CLI."""

    def __init__(self, transport: str | None = None, text_handler=None):
        use_acp = transport == "acp" or (transport is None and check_acp("kimi"))
        if transport == "cli":
            use_acp = False
        self._th = text_handler
        if use_acp:
            self._acp = AcpBackend(["kimi", "acp"], text_handler=text_handler)
            self._cli = None
        else:
            self._acp = None
            self._cli = _KimiCli(text_handler=text_handler)

    def create_session(self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> tuple[str, int]:
        if self._acp:
            try:
                return self._acp.create_session(user, system, model, system_mode)
            except Exception:
                self._acp = None
                self._cli = _KimiCli(text_handler=self._th)
        return self._cli.create_session(user, system, model, system_mode)

    def resume_session(self, sid: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> int:
        if self._acp:
            try:
                return self._acp.resume_session(sid, user, system, model, system_mode)
            except Exception:
                self._acp = None
                self._cli = _KimiCli(text_handler=self._th)
        return self._cli.resume_session(sid, user, system, model, system_mode)

    def list_sessions(self) -> list[dict]:
        return self._acp.list_sessions() if self._acp else []

    def close(self) -> None:
        if self._acp:
            self._acp.close()
        if self._cli:
            self._cli.close()


class _KimiCli(CliBackend):
    _sid_on_stderr = True

    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        prompt = f"System: {system}\n\nTask: {user}" if system else user
        cmd = ["kimi", "-p", prompt]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        prompt = f"System: {system}\n\nTask: {user}" if system else user
        cmd = ["kimi", "-S", sid, "-p", prompt]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        m = _SESSION_ID_RE.search(line)
        return (None, m.group(1) if m else None)

    def _on_stderr_line(self, line: str) -> None:
        if not line.startswith("\u2022") and "To resume this session:" not in line:
            print(line, file=sys.stderr, flush=True)