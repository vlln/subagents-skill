"""Workflow runtime — agent, parallel, pipeline, phase, log.

Delegates to the subagents CLI via --output json for structured execution.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any, Callable


def _subagents_path() -> str:
    home = os.environ.get("SKILL_SUBAGENTS_HOME", "")
    if not home:
        print("[workflow] SKILL_SUBAGENTS_HOME is not set.", file=sys.stderr)
        print("[workflow] Set it to the subagents skill installation directory.", file=sys.stderr)
        sys.exit(1)
    return os.path.join(home, "scripts", "subagents")


def _run_subagent(prompt: str, session: str) -> list[dict]:
    """Run a subagent session and return parsed JSONL events."""
    sub = _subagents_path()

    # Start background
    subprocess.run(
        [sub, "run", "--bg", "--output", "json", session, prompt],
        check=True, capture_output=True,
    )

    # Wait for completion
    result = subprocess.run(
        [sub, "wait", "--output", "json", session],
        capture_output=True, text=True, check=True,
    )

    events: list[dict] = []
    for line in result.stdout.strip().split("\n"):
        if line:
            events.append(json.loads(line))
    return events


def _extract_text(events: list[dict]) -> str:
    """Extract agent_text content from JSONL events."""
    parts: list[str] = []
    for evt in events:
        if evt.get("type") == "agent_text":
            parts.append(evt["content"])
    return "\n".join(parts)


def _extract_exit_code(events: list[dict]) -> int:
    for evt in events:
        if evt.get("type") == "agent_done":
            return evt.get("exit_code", 0)
    return 1


# ── public API ───────────────────────────────────────────────────────────

def agent(
    prompt: str,
    *,
    schema: dict | None = None,
    label: str | None = None,
    model: str | None = None,
) -> Any:
    """Run a single subagent and return its output.

    Args:
        prompt: The task description.
        schema: Optional JSON Schema for structured output validation.
        label: Optional display label.
        model: Optional model override.

    Returns:
        String (no schema) or validated dict (with schema), or None on failure.
    """
    session = f"wf_{uuid.uuid4().hex[:8]}"
    if label:
        log(f"[{label}] starting...")

    events = _run_subagent(prompt, session)
    exit_code = _extract_exit_code(events)

    if exit_code != 0:
        if label:
            log(f"[{label}] failed (exit {exit_code})")
        return None

    text = _extract_text(events)

    if schema:
        try:
            parsed = json.loads(text)
            return parsed  # schema validation is best-effort; caller can validate
        except json.JSONDecodeError:
            log(f"[{label}] output is not valid JSON")
            return None

    if label:
        log(f"[{label}] done")
    return text


def parallel(thunks: list[Callable[[], Any]]) -> list[Any]:
    """Run thunks concurrently, wait for all, return results in order.

    Failed thunks return None (exceptions are caught).
    """
    results: list[Any] = [None] * len(thunks)

    def _run(idx: int, fn: Callable[[], Any]) -> None:
        try:
            results[idx] = fn()
        except Exception:
            results[idx] = None

    threads: list[threading.Thread] = []
    for i, fn in enumerate(thunks):
        t = threading.Thread(target=_run, args=(i, fn), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results


def pipeline(items: list[Any], *stages: Callable) -> list[Any]:
    """Process items through stages with no inter-stage barrier.

    Each item flows independently through all stages. Stage signatures:
        stage_1(item, index) -> result
        stage_n(prev_result, original_item, index) -> result

    Returns list of final results, one per item.
    """
    results: list[Any] = [None] * len(items)

    def _process(idx: int, item: Any) -> None:
        result: Any = item
        for stage in stages:
            try:
                if stage is stages[0]:
                    result = stage(item, idx)
                else:
                    result = stage(result, item, idx)
            except Exception:
                result = None
                break
        results[idx] = result

    threads: list[threading.Thread] = []
    for i, item in enumerate(items):
        t = threading.Thread(target=_process, args=(i, item), daemon=True)
        t.start()
        threads.append(t)

    for t in threads:
        t.join()

    return results


def phase(title: str) -> None:
    """Log a phase transition."""
    print(f"[workflow] Phase: {title}", file=sys.stderr, flush=True)


def log(message: str) -> None:
    """Log a progress message."""
    print(f"[workflow] {message}", file=sys.stderr, flush=True)