"""Base class for CLI-based backends, reducing boilerplate."""

from __future__ import annotations

import json
import sys
import time
from abc import abstractmethod
from typing import Callable, ClassVar

from base import BaseBackend
from transports.cli import CliTransport


class CliBackend(BaseBackend):
    """Base for CLI backends. Subclass defines commands and line parsing.

    Subclasses must implement:
      - _cmd_create(user, system, model, system_mode) → list[str]
      - _cmd_resume(sid, user, system, model, system_mode) → list[str]
      - _parse_line(line) → (text | None, session_id | None)

    Set _sid_on_stderr = True if the session_id appears on stderr.

    text_handler: optional callback(text_chunk) for JSONL output mode.
    When set, agent text output is routed through it instead of stdout.
    """

    _sid_on_stderr: ClassVar[bool] = False

    def __init__(self, text_handler: Callable[[str], None] | None = None, backend_name: str | None = None) -> None:
        self._transport = CliTransport(backend_name=backend_name)
        self._text_handler = text_handler

    @abstractmethod
    def _cmd_create(self, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]: ...

    @abstractmethod
    def _cmd_resume(self, sid: str, user: str, system: str | None, model: str | None, system_mode: str) -> list[str]: ...

    @abstractmethod
    def _parse_line(self, line: str) -> tuple[str | None, str | None]: ...

    def _emit_text(self, text: str) -> None:
        if self._text_handler:
            self._text_handler(text)
        else:
            sys.stdout.write(text)
            sys.stdout.flush()

    def create_session(self, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> tuple[str, int]:
        cmd = self._cmd_create(user, system, model, system_mode)
        sid: str = ""

        if self._sid_on_stderr:
            def _on_stdout_line(line: str) -> None:
                if self._text_handler:
                    self._text_handler(line)
                else:
                    sys.stdout.write(line + "\n")
                    sys.stdout.flush()
            ec = self._transport.run(
                cmd,
                on_stdout=_on_stdout_line,
                on_stderr=_StderrSidExtractor(self, sid_capture := _SidCapture()),
            )
            sid = sid_capture.value
        else:
            def on_stdout(line: str) -> None:
                nonlocal sid
                text, new_sid = self._parse_line(line)
                if text:
                    self._emit_text(text)
                if new_sid:
                    sid = new_sid
            ec = self._transport.run(cmd, on_stdout=on_stdout, on_stderr=lambda _: None)

        if not self._text_handler:
            print()
        return (sid or f"unknown-{int(time.time_ns())}", ec)

    def resume_session(self, session_id: str, user: str, system: str | None = None, model: str | None = None, system_mode: str = "append") -> int:
        cmd = self._cmd_resume(session_id, user, system, model, system_mode)

        if self._sid_on_stderr:
            def _on_stdout_line(line: str) -> None:
                if self._text_handler:
                    self._text_handler(line)
                else:
                    sys.stdout.write(line + "\n")
                    sys.stdout.flush()
            ec = self._transport.run(
                cmd,
                on_stdout=_on_stdout_line,
                on_stderr=lambda _: None,
            )
        else:
            def on_stdout(line: str) -> None:
                text, _ = self._parse_line(line)
                if text:
                    self._emit_text(text)
            ec = self._transport.run(cmd, on_stdout=on_stdout, on_stderr=lambda _: None)

        if not self._text_handler:
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