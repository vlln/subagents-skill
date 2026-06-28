"""Live ANSI display for workflow execution — status panel and summary table."""

from __future__ import annotations

import sys
import threading
import time
from typing import Any

# ── ANSI helpers ────────────────────────────────────────────────────────────

_CURSOR_HIDE = "\033[?25l"
_CURSOR_SHOW = "\033[?25h"
_CURSOR_SAVE = "\033[s"
_CURSOR_RESTORE = "\033[u"
_CLEAR_BELOW = "\033[J"
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"

_SPINNERS = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes and spinner characters for comparison."""
    import re
    return re.sub(r"\033\[[0-9;]*[a-zA-Z]", "", text)


def _status_icon(status: str, spinner_idx: int = 0) -> str:
    if status == "done":
        return f"{_GREEN}✓{_RESET}"
    elif status == "failed":
        return f"{_RED}✗{_RESET}"
    elif status == "running":
        return f"{_YELLOW}{_SPINNERS[spinner_idx % len(_SPINNERS)]}{_RESET}"
    else:
        return f"{_DIM}○{_RESET}"


def _fmt_elapsed(seconds: float) -> str:
    if seconds < 0:
        return ""
    if seconds < 60:
        return f"{seconds:.1f}s"
    m, s = divmod(int(seconds), 60)
    return f"{m}m{s}s"


# ── Display ─────────────────────────────────────────────────────────────────

class Display:
    """Tracks workflow state and renders a live-updating ANSI panel.

    Thread-safe — state mutations are protected by a lock.
    """

    def __init__(self, name: str = "", run_id: str = "", width: int = 66) -> None:
        self._name = name
        self._run_id = run_id
        self._width = width
        self._lock = threading.Lock()
        self._phases: list[dict[str, Any]] = []
        self._agents: list[dict[str, Any]] = []
        self._start_time = time.time()
        self._spinner_idx = 0
        self._refresh_thread: threading.Thread | None = None
        self._running = False
        self._enabled = sys.stderr.isatty()
        self._total_phases = 0
        self._last_status = ""  # debounce: skip duplicate status lines

    # ── state tracking ──────────────────────────────────────────────────

    def set_total_phases(self, n: int) -> None:
        """Pre-set total phase count for progress display (e.g. Phase 1/3)."""
        with self._lock:
            self._total_phases = n

    def phase(self, title: str) -> None:
        with self._lock:
            # Mark previous phase as done
            if self._phases:
                prev = self._phases[-1]
                if prev["status"] == "running":
                    prev["status"] = "done"
                    prev["end_time"] = time.time()
            self._phases.append({
                "title": title,
                "status": "running",
                "start_time": time.time(),
                "end_time": 0.0,
            })
        if not self._enabled:
            self._emit_status()

    def agent_start(self, label: str, prompt: str = "") -> None:
        with self._lock:
            phase_title = self._phases[-1]["title"] if self._phases else ""
            self._agents.append({
                "label": label,
                "prompt": prompt,
                "phase": phase_title,
                "status": "running",
                "start_time": time.time(),
                "elapsed": 0.0,
            })
        if not self._enabled:
            self._emit_status()

    def agent_done(self, label: str, success: bool, elapsed: float = 0.0) -> None:
        with self._lock:
            for a in self._agents:
                if a["label"] == label and a["status"] == "running":
                    a["status"] = "done" if success else "failed"
                    a["elapsed"] = elapsed
                    break
        if not self._enabled:
            self._emit_status()

    def agent_skip(self, label: str) -> None:
        with self._lock:
            for a in self._agents:
                if a["label"] == label and a["status"] == "running":
                    a["status"] = "skipped"
                    break

    # ── rendering ───────────────────────────────────────────────────────

    def _render(self) -> str:
        with self._lock:
            elapsed = time.time() - self._start_time
            lines: list[str] = []

            # Header
            header = f" Workflow: {self._name}"
            if self._run_id:
                header += f" ({self._run_id})"
            right = f"{_fmt_elapsed(elapsed)} "
            pad = self._width - len(header) - len(right) - 2
            if pad > 0:
                header += " " * pad + right
            lines.append(f"┌{header}┐")
            lines.append(f"│{' ' * self._width}│")

            # Phases and agents
            total = self._total_phases or len(self._phases)
            for pi, ph in enumerate(self._phases):
                phase_num = f"{pi + 1}/{total}" if total > 0 else ""
                icon = _status_icon(ph["status"], self._spinner_idx)
                p_elapsed = ""
                if ph["status"] == "done" and ph["end_time"]:
                    p_elapsed = _fmt_elapsed(ph["end_time"] - ph["start_time"])
                elif ph["status"] == "running":
                    p_elapsed = _fmt_elapsed(time.time() - ph["start_time"])

                title = f" Phase {phase_num}: {ph['title']}" if phase_num else f" Phase: {ph['title']}"
                line = f"  {icon} {title}"
                if p_elapsed:
                    line += f"  {_DIM}{p_elapsed}{_RESET}"
                lines.append(self._pad_line(line))

                phase_agents = [a for a in self._agents if a["phase"] == ph["title"]]
                for ai, a in enumerate(phase_agents):
                    is_last = ai == len(phase_agents) - 1
                    prefix = "   └─" if is_last else "   ├─"
                    a_icon = _status_icon(a["status"], self._spinner_idx)
                    a_elapsed = ""
                    if a["status"] in ("done", "failed"):
                        a_elapsed = _fmt_elapsed(a["elapsed"])
                    elif a["status"] == "running":
                        a_elapsed = _fmt_elapsed(time.time() - a["start_time"])

                    a_line = f" {prefix} {a_icon} {a['label']}"
                    if a_elapsed:
                        a_line += f"  {_DIM}{a_elapsed}{_RESET}"
                    if a["status"] == "failed":
                        a_line += f"  {_RED}failed{_RESET}"
                    lines.append(self._pad_line(a_line))

                if phase_agents:
                    lines.append(f"│{' ' * self._width}│")

            # Footer
            lines.append(f"└{'─' * self._width}┘")

            return "\n".join(lines)

    def _draw(self) -> None:
        self._spinner_idx += 1
        sys.stderr.write(_CURSOR_RESTORE)
        sys.stderr.write(self._render())
        sys.stderr.write(_CLEAR_BELOW)
        sys.stderr.flush()

    def _pad_line(self, line: str) -> str:
        """Pad a content line to fill the panel width, accounting for ANSI codes."""
        visible = len(_strip_ansi(line))
        pad = self._width - visible
        if pad > 0:
            return f"│{line}{' ' * pad}│"
        return f"│{line}│"

    def _emit_status(self) -> None:
        """Non-TTY: print one compact status line on state change."""
        with self._lock:
            elapsed = _fmt_elapsed(time.time() - self._start_time)
            self._spinner_idx += 1
            parts: list[str] = [f"[workflow] {self._name}"]
            if self._run_id:
                parts.append(f"({self._run_id})")
            parts.append(f"{elapsed}")

            for ph in self._phases:
                icon = _status_icon(ph["status"], self._spinner_idx)
                parts.append(f"| {icon} {ph['title']}")

            line = " ".join(parts)
            # Compare by phase statuses only (ignore spinner variation)
            key = "|".join(f"{ph['status']}:{ph['title']}" for ph in self._phases)
            if key == self._last_status:
                return
            self._last_status = key
        sys.stderr.write(f"{line}\n")
        sys.stderr.flush()

    # ── lifecycle ───────────────────────────────────────────────────────

    def start_auto_refresh(self, interval: float = 0.3) -> None:
        """Start live panel refresh. Saves cursor position for in-place updates."""
        if self._enabled:
            sys.stderr.write(_CURSOR_HIDE)
            sys.stderr.write(_CURSOR_SAVE)
            sys.stderr.write(self._render())
            sys.stderr.write(_CLEAR_BELOW)
            sys.stderr.flush()
            self._running = True

            def _refresh() -> None:
                while self._running:
                    self._draw()
                    time.sleep(interval)

            self._refresh_thread = threading.Thread(target=_refresh, daemon=True)
            self._refresh_thread.start()

    def stop(self) -> None:
        """Stop refresh, draw final panel (TTY) or summary (non-TTY)."""
        self._running = False
        if self._refresh_thread:
            self._refresh_thread.join(timeout=1.0)
        if self._enabled:
            sys.stderr.write(_CURSOR_RESTORE)
            sys.stderr.write(self._render())
            sys.stderr.write(_CLEAR_BELOW)
            sys.stderr.write(_CURSOR_SHOW)
            sys.stderr.write("\n")
            sys.stderr.flush()
        else:
            sys.stderr.write("\n")
            sys.stderr.write(self.summary())
            sys.stderr.write("\n")
            sys.stderr.flush()

    def summary(self) -> str:
        """Render the final summary table."""
        with self._lock:
            elapsed = time.time() - self._start_time
            lines: list[str] = []

            # Header
            lines.append(f"{_BOLD}══ Workflow Summary: {self._name} ══{_RESET}")
            lines.append("")

            # Stats
            done = sum(1 for a in self._agents if a["status"] == "done")
            failed = sum(1 for a in self._agents if a["status"] == "failed")
            skipped = sum(1 for a in self._agents if a["status"] == "skipped")
            lines.append(f"  Duration: {_fmt_elapsed(elapsed)}    "
                         f"Phases: {len(self._phases)}    "
                         f"Sessions: {len(self._agents)}")
            lines.append(f"  {_GREEN}✓{_RESET} {done} done    "
                         f"{_RED}✗{_RESET} {failed} failed    "
                         f"{_DIM}○{_RESET} {skipped} skipped")
            lines.append("")

            # Phases detail
            for ph in self._phases:
                p_elapsed = ""
                p_status = ph["status"]
                if ph["end_time"]:
                    p_elapsed = _fmt_elapsed(ph["end_time"] - ph["start_time"])
                elif p_status == "running":
                    p_status = "done"
                    p_elapsed = _fmt_elapsed(time.time() - ph["start_time"])

                icon = _status_icon(p_status, 0)
                phase_agents = [a for a in self._agents if a["phase"] == ph["title"]]
                phase_failed = sum(1 for a in phase_agents if a["status"] == "failed")

                extra = ""
                if phase_failed > 0:
                    extra = f"  ({phase_failed} failed)"

                p_line = f"  {icon} {ph['title']}  {_DIM}{p_elapsed}{_RESET}{extra}"
                lines.append(p_line)

                for a in phase_agents:
                    a_icon = _status_icon(a["status"], 0)
                    a_elapsed = _fmt_elapsed(a["elapsed"])
                    a_line = f"     {a_icon} {a['label']}  {_DIM}{a_elapsed}{_RESET}"
                    lines.append(a_line)

                lines.append("")

            return "\n".join(lines)