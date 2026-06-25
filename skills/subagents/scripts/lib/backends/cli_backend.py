"""Base class for CLI-based backends, reducing boilerplate."""

from __future__ import annotations

import json
import time
from abc import abstractmethod
from typing import ClassVar

from base import BaseBackend
from transports.cli import CliTransport


class CliBackend(BaseBackend):
    """Base for CLI backends. Subclass defines commands and line parsing.

    Subclasses must implement:
      - _cmd_create(user, system, model, system_mode) → list[str]
      - _cmd_resume(sid, user, system, model, system_mode) → list[str]
      - _parse_line(line) → (text | None, session_id | None)

    Set _sid_on_stderr = True if the session_id appears on stderr.
    """

    _sid_on_stderr: ClassVar[bool] = False

    def __init__(self) -> None:
        self._transport = CliTransport()

    @abstractmethod
    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]: ...

    @abstractmethod
    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]: ...

    @abstractmethod
    def _parse_line(self, line: str) -> tuple[str | None, str | None]: ...

    def create_session(self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> tuple[str, int]:
        cmd = self._cmd_create(user, system, model, system_mode)
        sid: str = ""

        if self._sid_on_stderr:
            ec = self._transport.run(cmd, on_stderr=_StderrSidExtractor(self, sid_capture := _SidCapture()))
            sid = sid_capture.value
        else:
            def on_stdout(line: str) -> None:
                nonlocal sid
                text, new_sid = self._parse_line(line)
                if text:
                    print(text, end="", flush=True)
                if new_sid:
                    sid = new_sid
            ec = self._transport.run(cmd, on_stdout=on_stdout, on_stderr=lambda _: None)

        print()
        return (sid or f"unknown-{int(time.time_ns())}", ec)

    def resume_session(self, session_id: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> int:
        cmd = self._cmd_resume(session_id, user, system, model, system_mode)

        if self._sid_on_stderr:
            ec = self._transport.run(cmd, on_stderr=lambda _: None)
        else:
            def on_stdout(line: str) -> None:
                text, _ = self._parse_line(line)
                if text:
                    print(text, end="", flush=True)
            ec = self._transport.run(cmd, on_stdout=on_stdout, on_stderr=lambda _: None)

        print()
        return ec

    def list_sessions(self) -> list[dict]:
        return []

    def close(self) -> None:
        self._transport.close()

    def _on_stderr_line(self, line: str) -> None:
        pass

    @staticmethod
    def _try_parse_json(line: str) -> dict | None:
        try:
            return json.loads(line)
        except (json.JSONDecodeError, ValueError):
            return None


class _SidCapture:
    value: str = ""


def _StderrSidExtractor(backend: CliBackend, cap: _SidCapture):
    def on_stderr(line: str) -> None:
        if not cap.value:
            _, sid = backend._parse_line(line)
            if sid:
                cap.value = sid
                return
        backend._on_stderr_line(line)
    return on_stderr