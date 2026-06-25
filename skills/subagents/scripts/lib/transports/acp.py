"""Generic ACP transport — JSON-RPC over stdio to an ACP-compliant agent."""

from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any, Callable


class AcpTransport:
    """Generic JSON-RPC client over stdio for ACP-compliant agents.

    Manages a persistent subprocess, handles the ACP initialization
    handshake, and provides `call()` for request/response and
    `on_notification()` for streaming updates.

    Usage:
        transport = AcpTransport(
            command=["kimi", "acp"],
            client_info={"name": "subagents", "version": "0.1.0"},
        )
        transport.start()
        result = transport.call("session/new", {"cwd": str(Path.cwd())})
        transport.close()
    """

    def __init__(
        self,
        command: list[str],
        client_info: dict[str, str] | None = None,
        capabilities: dict[str, Any] | None = None,
    ) -> None:
        self._command = command
        self._client_info = client_info or {"name": "subagents", "version": "0.1.0"}
        self._capabilities = capabilities or {
            "prompt": {"text": True},
            "fs": {"readTextFile": False, "writeTextFile": False},
            "terminal": False,
            "loadSession": True,
            "listSessions": True,
        }
        self._proc: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._next_id = 0
        self._notification_handlers: dict[str, Callable[[dict], None]] = {}

    # ── lifecycle ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the subprocess and perform ACP initialization."""
        if self._proc is not None:
            return

        self._proc = subprocess.Popen(
            self._command,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
        self._initialize()

    def _initialize(self) -> None:
        """ACP handshake: send initialize request to the agent."""
        result = self.call(
            "initialize",
            {
                "protocolVersion": 1,  # ACP protocol version 1 (current stable)
                "clientInfo": self._client_info,
                "capabilities": self._capabilities,
            },
        )
        # Server responds with its own capabilities — we don't need them
        _ = result

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            self._proc.stdin.close()
        except OSError:
            pass
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
            self._proc.wait()
        self._proc = None

    # ── request / response ─────────────────────────────────────────────────

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        """Send a JSON-RPC request and block until the response arrives.

        Returns the result dict. Raises RuntimeError on error response.
        """
        with self._lock:
            req_id = self._next_id
            self._next_id += 1

            self._write_json(
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "method": method,
                    "params": params,
                }
            )

            while True:
                resp = self._read_json()
                if resp is None:
                    raise RuntimeError(f"ACP: no response for {method}")
                rid = resp.get("id")
                if rid == req_id:
                    if "error" in resp:
                        err = resp["error"]
                        raise RuntimeError(
                            f"ACP {method} error: {err.get('message', err)}"
                        )
                    return resp.get("result", {})
                # Notification — dispatch to handler
                if "method" in resp:
                    self._dispatch_notification(resp)

    def on_notification(self, method: str, handler: Callable[[dict], None]) -> None:
        """Register a handler for a notification method (e.g. 'session/update')."""
        self._notification_handlers[method] = handler

    def _dispatch_notification(self, notification: dict[str, Any]) -> None:
        method = notification.get("method", "")
        handler = self._notification_handlers.get(method)
        if handler:
            handler(notification.get("params", {}))

    # ── raw I/O ────────────────────────────────────────────────────────────

    def _write_json(self, data: dict[str, Any]) -> None:
        assert self._proc is not None
        assert self._proc.stdin is not None
        line = json.dumps(data, ensure_ascii=False)
        self._proc.stdin.write(line + "\n")
        self._proc.stdin.flush()

    def _read_json(self) -> dict[str, Any] | None:
        assert self._proc is not None
        assert self._proc.stdout is not None
        line = self._proc.stdout.readline()
        if not line:
            return None
        try:
            return json.loads(line.strip())
        except json.JSONDecodeError:
            return None

    def __enter__(self) -> AcpTransport:
        self.start()
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()