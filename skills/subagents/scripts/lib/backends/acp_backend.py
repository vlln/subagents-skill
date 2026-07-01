"""Base class for ACP-based backends, reducing boilerplate."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable

from base import BaseBackend
from transports.acp import AcpTransport


class AcpBackend(BaseBackend):
    """Base for ACP backends. Handles initialize, authentication, session/new, session/load, session/prompt.

    Subclasses only need to pass the command (e.g. ["kimi", "acp"]).

    text_handler: optional callback(text_chunk) for JSONL output mode.
    """

    def __init__(self, command: list[str], text_handler: Callable[[str], None] | None = None):
        self._transport = AcpTransport(
            command=command,
            client_info={"name": "subagents", "version": "0.1.0"},
        )
        self._text_handler = text_handler
        self._transport.on_notification("session/update", self._on_update)
        self._auth_methods: list[dict] = []
        self._authenticated = False

    def _on_update(self, params: dict) -> None:
        u = params.get("update", {})
        if u.get("sessionUpdate") == "agent_message_chunk":
            c = u.get("content", {})
            if c.get("type") == "text":
                if self._text_handler:
                    self._text_handler(c["text"])
                else:
                    sys.stdout.write(c["text"])
                    sys.stdout.flush()

    def _ensure_initialized(self) -> None:
        """Start transport and initialize, storing auth methods."""
        self._transport.start()
        r = self._transport.call("initialize", {
            "protocolVersion": 1,
            "clientInfo": {"name": "subagents", "version": "0.1.0"},
            "capabilities": {"prompt": {"text": True}, "terminal": False},
        })
        self._auth_methods = r.get("authMethods", [])

    def _authenticate(self) -> None:
        """Try to authenticate with the agent.  Raises if no method works."""
        if self._authenticated:
            return
        if not self._auth_methods:
            return  # no auth required

        for method in self._auth_methods:
            method_id = method.get("id", "")
            if not method_id:
                continue
            try:
                self._transport.call("authenticate", {"methodId": method_id})
                self._authenticated = True
                return
            except Exception:
                continue

        raise RuntimeError(
            f"ACP: authentication failed. Available methods: "
            f"{[m.get('id', '?') for m in self._auth_methods]}"
        )

    def create_session(
        self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append"
    ) -> tuple[str, int]:
        self._ensure_initialized()

        # Try session/new; if it fails with auth error, authenticate and retry
        try:
            r = self._transport.call("session/new", {"cwd": str(Path.cwd()), "mcpServers": []})
        except RuntimeError:
            self._authenticate()
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
        self._ensure_initialized()

        try:
            self._transport.call("session/load", {
                "sessionId": session_id,
                "cwd": str(Path.cwd()),
                "mcpServers": [],
            })
        except RuntimeError:
            self._authenticate()
            self._transport.call("session/load", {
                "sessionId": session_id,
                "cwd": str(Path.cwd()),
                "mcpServers": [],
            })

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