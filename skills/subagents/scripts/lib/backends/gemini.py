"""Gemini backend — CLI with ACP fallback."""

from __future__ import annotations

from base import BaseBackend
from acp_backend import AcpBackend
from cli_backend import CliBackend
from utils import check_acp


class GeminiBackend(BaseBackend):
    """Backend for gemini-cli. Tries ACP first, falls back to CLI."""

    def __init__(self, transport: str | None = None):
        use_acp = transport == "acp" or (transport is None and check_acp("gemini"))
        if transport == "cli":
            use_acp = False
        if use_acp:
            self._acp = AcpBackend(["gemini", "--acp"])
            self._cli = None
        else:
            self._acp = None
            self._cli = _GeminiCli()

    def create_session(self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> tuple[str, int]:
        if self._acp:
            try:
                return self._acp.create_session(user, system, model, system_mode)
            except Exception:
                self._acp = None
                self._cli = _GeminiCli()
        return self._cli.create_session(user, system, model, system_mode)

    def resume_session(self, sid: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> int:
        if self._acp:
            try:
                return self._acp.resume_session(sid, user, system, model, system_mode)
            except Exception:
                self._acp = None
                self._cli = _GeminiCli()
        return self._cli.resume_session(sid, user, system, model, system_mode)

    def list_sessions(self) -> list[dict]:
        return self._acp.list_sessions() if self._acp else []

    def close(self) -> None:
        if self._acp:
            self._acp.close()
        if self._cli:
            self._cli.close()


class _GeminiCli(CliBackend):
    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        prompt = f"System: {system}\n\nTask: {user}" if system else user
        cmd = ["gemini", "-p", prompt, "-y", "-o", "stream-json"]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]:
        prompt = f"System: {system}\n\nTask: {user}" if system else user
        cmd = ["gemini", "-p", prompt, "-y", "-o", "stream-json", "-r", sid]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        data = self._try_parse_json(line)
        if data is None:
            return (line, None)
        tp = data.get("type", "")
        if tp == "system" and data.get("subtype") == "init":
            return (None, data.get("session_id") or None)
        if tp == "assistant":
            content = data.get("message", {}).get("content", [])
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            return ("".join(texts), data.get("session_id") or None)
        if tp == "result":
            return (None, data.get("session_id") or None)
        return (None, None)