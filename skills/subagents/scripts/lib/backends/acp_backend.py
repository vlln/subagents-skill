"""Base class for ACP-based backends, reducing boilerplate."""

from __future__ import annotations

import sys
from pathlib import Path

from base import BaseBackend
from transports.acp import AcpTransport


class AcpBackend(BaseBackend):
    """Base for ACP backends. Handles initialize, session/new, session/load, session/prompt.

    Subclasses only need to pass the command (e.g. ["kimi", "acp"]).
    """

    def __init__(self, command: list[str]):
        self._transport = AcpTransport(
            command=command,
            client_info={"name": "subagents", "version": "0.1.0"},
        )
        self._transport.on_notification("session/update", self._on_update)

    def _on_update(self, params: dict) -> None:
        u = params.get("update", {})
        if u.get("sessionUpdate") == "agent_message_chunk":
            c = u.get("content", {})
            if c.get("type") == "text":
                sys.stdout.write(c["text"])
                sys.stdout.flush()

    def create_session(
        self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append"
    ) -> tuple[str, int]:
        self._transport.start()
        r = self._transport.call("session/new", {"cwd": str(Path.cwd()), "mcpServers": []})
        sid = r.get("sessionId", "")
        if not sid:
            raise RuntimeError("ACP: session/new returned no sessionId")
        if model:
            self._transport.call("session/set_model", {"sessionId": sid, "modelId": model})
        prompt = f"{system}\n\n{user}" if system else user
        try:
            self._transport.call(
                "session/prompt",
                {"sessionId": sid, "prompt": [{"type": "text", "text": prompt}]},
            )
            return sid, 0
        except Exception:
            return sid, 1

    def resume_session(
        self, session_id: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append"
    ) -> int:
        self._transport.start()
        self._transport.call("session/load", {"sessionId": session_id})
        if model:
            self._transport.call("session/set_model", {"sessionId": session_id, "modelId": model})
        prompt = f"{system}\n\n{user}" if system else user
        try:
            self._transport.call(
                "session/prompt",
                {"sessionId": session_id, "prompt": [{"type": "text", "text": prompt}]},
            )
            return 0
        except Exception:
            return 1

    def list_sessions(self) -> list[dict]:
        self._transport.start()
        return self._transport.call("session/list", {}).get("sessions", [])

    def close(self) -> None:
        self._transport.close()