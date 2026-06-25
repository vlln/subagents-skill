"""Qwen backend — CLI with ACP fallback."""

from __future__ import annotations

from base import BaseBackend
from acp_backend import AcpBackend
from cli_backend import CliBackend


class QwenBackend(BaseBackend):
    def __init__(self, transport: str | None = None):
        use_acp = transport == "acp"
        if use_acp:
            self._acp = AcpBackend(["qwen", "--acp"])
            self._cli = None
        else:
            self._acp = None
            self._cli = _QwenCli()

    def create_session(self, user: str, system: str | None = None, model: str | None = None) -> tuple[str, int]:
        if self._acp:
            try:
                return self._acp.create_session(user, system, model)
            except Exception:
                self._acp = None
                self._cli = _QwenCli()
        return self._cli.create_session(user, system, model)

    def resume_session(self, sid: str, user: str, system: str | None = None, model: str | None = None) -> int:
        if self._acp:
            try:
                return self._acp.resume_session(sid, user, system, model)
            except Exception:
                self._acp = None
                self._cli = _QwenCli()
        return self._cli.resume_session(sid, user, system, model)

    def list_sessions(self) -> list[dict]:
        return self._acp.list_sessions() if self._acp else []

    def close(self) -> None:
        if self._acp:
            self._acp.close()
        if self._cli:
            self._cli.close()


class _QwenCli(CliBackend):
    def _cmd_create(self, user: str, system: str | None, model: str | None) -> list[str]:
        cmd = ["qwen", "-y", "-o", "stream-json"]
        if system:
            cmd.extend(["--system-prompt", system])
        cmd.append(user)
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None) -> list[str]:
        cmd = ["qwen", "-y", "-o", "stream-json", "-r", sid, user]
        if model:
            cmd.extend(["-m", model])
        return cmd

    def _parse_line(self, line: str) -> tuple[str | None, str | None]:
        data = self._try_parse_json(line)
        if data is None:
            return (line, None)
        tp = data.get("type", "")
        if tp == "system":
            return (None, data.get("session_id") or None)
        if tp == "assistant":
            content = data.get("message", {}).get("content", [])
            texts = [b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text"]
            return ("".join(texts), data.get("session_id") or None)
        if tp == "result":
            return (None, data.get("session_id") or None)
        return (None, None)