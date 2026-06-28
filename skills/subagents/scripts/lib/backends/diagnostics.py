"""Backend diagnostics — binary detection, smoke tests, and install guides."""

from __future__ import annotations

import shutil
import subprocess
import sys
from typing import Any

BACKEND_META: dict[str, dict[str, str]] = {
    "kimi": {
        "binary": "kimi",
        "homepage": "https://www.kimi.com/code",
        "auth_help": "Run 'kimi auth' to configure authentication.",
    },
    "claude": {
        "binary": "claude",
        "homepage": "https://claude.com/product/claude-code",
        "auth_help": "Run 'claude' and follow the authentication prompts.",
    },
    "codex": {
        "binary": "codex",
        "homepage": "https://openai.com/codex/",
        "auth_help": "Set OPENAI_API_KEY environment variable or run 'codex auth'.",
    },
    "pi": {
        "binary": "pi",
        "homepage": "https://pi.dev/",
        "auth_help": "Run 'pi auth' to configure authentication.",
    },
    "opencode": {
        "binary": "opencode",
        "homepage": "https://opencode.ai/",
        "auth_help": "Run 'opencode auth' to configure authentication.",
    },
    "qwen": {
        "binary": "qwen",
        "homepage": "https://qwen.ai/qwencode",
        "auth_help": "Run 'qwen auth' to configure authentication.",
    },
    "kiro": {
        "binary": "kiro-cli",
        "homepage": "https://kiro.dev/",
        "auth_help": "Run 'kiro-cli auth' to configure authentication.",
    },
    "gemini": {
        "binary": "gemini",
        "homepage": "https://geminicli.com/",
        "auth_help": "Run 'gemini auth' to configure authentication.",
    },
}


def check_binary(backend_name: str) -> bool:
    """Check if the backend's CLI binary is on PATH."""
    meta = BACKEND_META.get(backend_name)
    if meta is None:
        return False
    return shutil.which(meta["binary"]) is not None


def check_smoke(backend_name: str) -> tuple[bool, str]:
    """Run a quick smoke test (--version) to verify the binary works.

    Returns (ok, message) — ok=True if the binary returns exit code 0.
    """
    meta = BACKEND_META.get(backend_name)
    if meta is None:
        return False, f"Unknown backend '{backend_name}'."

    binary = meta["binary"]
    try:
        result = subprocess.run(
            [binary, "--version"],
            capture_output=True,
            timeout=5,
            text=True,
        )
        if result.returncode == 0:
            return True, ""
        stderr = result.stderr.strip()
        detail = f": {stderr}" if stderr else ""
        return False, f"'{binary} --version' failed (exit code {result.returncode}){detail}"
    except FileNotFoundError:
        return False, f"Command '{binary}' not found."
    except subprocess.TimeoutExpired:
        return False, f"'{binary} --version' timed out."
    except Exception as e:
        return False, f"'{binary} --version' error: {e}"


def diagnose(backend_name: str) -> tuple[bool, str]:
    """Run full diagnostic on a backend.

    Returns (ok, message). If ok is False, message explains why.
    """
    meta = BACKEND_META.get(backend_name)
    if meta is None:
        return False, f"Unknown backend '{backend_name}'."

    if not check_binary(backend_name):
        return False, (
            f"Backend '{backend_name}' requires the '{meta['binary']}' CLI, "
            f"which is not installed.\n\n"
            f"  Install: {meta['homepage']}\n"
            f"  Auth: {meta['auth_help']}"
        )

    ok, msg = check_smoke(backend_name)
    if not ok:
        return False, (
            f"Backend '{backend_name}' binary found but may not be functional.\n"
            f"  {msg}\n\n"
            f"  {meta['auth_help']}"
        )

    return True, ""


def list_available_backends() -> list[str]:
    """Return backend names whose binaries are on PATH."""
    return [name for name in BACKEND_META if check_binary(name)]


def format_install_guide(backend_name: str | None = None) -> str:
    """Format installation instructions for one or all backends."""
    if backend_name is not None:
        meta = BACKEND_META.get(backend_name)
        if meta is None:
            return f"Unknown backend '{backend_name}'."
        return f"Install '{meta['binary']}': {meta['homepage']}"

    lines = ["Install one of the following agent backends:\n"]
    for name, meta in BACKEND_META.items():
        lines.append(f"  {name:8s} {meta['homepage']}")
    return "\n".join(lines)


def print_diagnostics(backend_name: str, file: Any = None) -> bool:
    """Print diagnostic info to stderr (or given file). Return True if OK.

    Intended for use in the CLI flow — prints user-friendly messages.
    """
    out = file or sys.stderr
    ok, msg = diagnose(backend_name)
    if not ok:
        print(f"[subagents] Error: {msg}", file=out)
        available = [b for b in list_available_backends() if b != backend_name]
        if available:
            print(f"[subagents] Available backends: {', '.join(available)}", file=out)
            print("[subagents] Use --backend <name> to switch.", file=out)
        return False
    return True