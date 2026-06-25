"""Generic CLI transport — spawn a subprocess, stream stdout, capture exit code."""

from __future__ import annotations

import subprocess
import sys
import threading
from typing import Callable


class CliTransport:
    """Generic subprocess-based communication with a CLI tool.

    Spawns a command, streams stdout/stderr line-by-line through
    optional callbacks, and returns the exit code.
    """

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None

    def run(
        self,
        args: list[str],
        *,
        on_stdout: Callable[[str], None] | None = None,
        on_stderr: Callable[[str], None] | None = None,
        timeout: float | None = None,
    ) -> int:
        """Run a command and return its exit code.

        Args:
            args: Command and arguments to execute.
            on_stdout: Called for each stdout line (newline stripped).
            on_stderr: Called for each stderr line (newline stripped).
            timeout: Maximum time in seconds. None = no timeout.

        Returns:
            The process exit code.
        """
        try:
            self._proc = subprocess.Popen(
                args,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError:
            raise RuntimeError(
                f"Command not found: {args[0]}\n"
                f"Please install '{args[0]}' or use a different backend with --backend <name>."
            ) from None

        assert self._proc.stdout is not None
        assert self._proc.stderr is not None

        errors: list[Exception] = []

        def _read(stream, callback, write_fn, flush_fn):
            try:
                for line in iter(stream.readline, ""):
                    line = line.rstrip("\n")
                    if callback:
                        callback(line)
                    else:
                        write_fn(line + "\n")
                        flush_fn()
            except Exception as e:
                errors.append(e)

        t_stdout = threading.Thread(
            target=_read,
            args=(self._proc.stdout, on_stdout, sys.stdout.write, sys.stdout.flush),
            daemon=True,
        )
        t_stderr = threading.Thread(
            target=_read,
            args=(self._proc.stderr, on_stderr, sys.stderr.write, sys.stderr.flush),
            daemon=True,
        )
        t_stdout.start()
        t_stderr.start()

        t_stdout.join(timeout=timeout)
        t_stderr.join(timeout=timeout)

        if t_stdout.is_alive() or t_stderr.is_alive():
            self._proc.kill()
            self._proc.wait()
            raise TimeoutError(
                f"Command timed out: {args[0]}\n"
                f"The agent took too long to respond. Try a simpler task or check your connection."
            )

        exit_code = self._proc.wait()

        if errors:
            raise errors[0]

        return exit_code

    def close(self) -> None:
        """Terminate the process if still running."""
        if self._proc is not None:
            try:
                self._proc.terminate()
                self._proc.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                try:
                    self._proc.kill()
                except OSError:
                    pass
            self._proc = None