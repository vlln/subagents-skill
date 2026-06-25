"""Kiro backend — CLI with ACP fallback."""

from __future__ import annotations

from base import BaseBackend
from acp_backend import AcpBackend
from cli_backend import CliBackend
from utils import check_acp


class KiroBackend(BaseBackend):
    def __init__(self, transport: str | None = None, text_handler=None):
        use_acp = transport == "acp" or (transport is None and check_acp("kiro-cli"))
        if transport == "cli":
            use_acp = False
        self._th = text_handler
        if use_acp:
            self._acp = AcpBackend(["kiro-cli", "acp"], text_handler=text_handler)
            self._cli = None
        else:
            self._acp = None
            self._cli = _KiroCli(text_handler=text_handler)

    def create_session(self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> tuple[str, int]:
        if self._acp:
            try:
                return self._acp.create_session(user, system, model, system_mode)
            except Exception:
                self._acp = None
                self._cli = _KiroCli(text_handler=self._th)
        return self._cli.create_session(user, system, model, system_mode)

    def resume_session(self, sid: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> int:
        if self._acp:
            try:
                return self._acp.resume_session(sid, user, system, model, system_mode)
            except Exception:
                self._acp = None
                self._cli = _KiroCli(text_handler=self._th)
        return self._cli.resume_session(sid, user, system, model, system_mode)

    def list_sessions(self) -> list[dict]:
        return self._acp.list_sessions() if self._acp else []

    def close(self) -> None:
        if self._acp:
            self._acp.close()
        if self._cli:
            self._cli.close()


class _KiroCli(CliBackend):
    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        prompt = f"System: {system}\n\nTask: {user}" if system else user
        cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", prompt]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        cmd = ["kiro-cli", "chat", "--no-interactive", "--trust-all-tools", "--resume-id", sid, user]
        if model:
            cmd.extend(["--model", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        data = self._try_parse_json(line)
        if data is None:
            return (line, None)
        role = data.get("role", "")
        if role == "assistant":
            return (data.get("content", ""), None)
        if role == "meta":
            return (None, data.get("session_id") or data.get("sessionId") or None)
        content = data.get("content") or data.get("text") or data.get("message")
        if isinstance(content, str):
            return (content, None)
        return (None, None)