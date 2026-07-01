"""Agent progress renderer — pure function: estimates → ANSI lines.

Three layouts:
  render_single  — full-width bar for one agent
  render_grid    — N-column grid (swarm)
  render_list    — vertical list, one agent per line

Supports 256-color ANSI and ASCII fallback.
"""

from __future__ import annotations

import os
from .types import Phase, ProgressEstimate

# ── braille ──────────────────────────────────────────────────────────────────

BRAILLE_EMPTY = "\u28c0"  # ⣀
BRAILLE_LEVELS = [
    "\u28c0",  # ⣀  level 0
    "\u28c4",  # ⣄  level 1
    "\u28e4",  # ⣤  level 2
    "\u28e6",  # ⣦  level 3
    "\u28f6",  # ⣶  level 4
    "\u28f7",  # ⣷  level 5
    "\u28ff",  # ⣿  level 6
]
BRAILLE_LEVEL_COUNT = len(BRAILLE_LEVELS)  # 7

# ── ANSI 256 colors ──────────────────────────────────────────────────────────

_ANSI = True  # set False for pure ASCII fallback


def _c256(n: int) -> str:
    """ANSI 256-color foreground escape."""
    return f"\033[38;5;{n}m" if _ANSI else ""


def _reset() -> str:
    return "\033[0m" if _ANSI else ""


# Color palette
C_PRIMARY = 51    # cyan
C_ACCENT = 33     # blue
C_SUCCESS = 46    # green
C_FAILED = 196    # red
C_WARNING = 220   # yellow
C_DIM = 240       # gray
C_TEXT = 252      # light gray

# ── status icons ─────────────────────────────────────────────────────────────

_ICONS = {
    Phase.COMPLETED: "✓",
    Phase.FAILED: "✗",
    Phase.CANCELLED: "⊘",
    Phase.PENDING: "·",
    Phase.QUEUED: "⏳",
    Phase.SUSPENDED: "⏸",
    Phase.RUNNING: "",
}

_ICON_COLORS = {
    Phase.COMPLETED: C_SUCCESS,
    Phase.FAILED: C_FAILED,
    Phase.CANCELLED: C_WARNING,
    Phase.PENDING: C_DIM,
    Phase.QUEUED: C_WARNING,
    Phase.SUSPENDED: C_WARNING,
    Phase.RUNNING: C_PRIMARY,
}

# ── label helpers ─────────────────────────────────────────────────────────────

def _label(estimate: ProgressEstimate) -> str:
    """Human-readable label for the current phase."""
    if estimate.phase == Phase.COMPLETED:
        return "done"
    if estimate.phase == Phase.FAILED:
        return "failed"
    if estimate.phase == Phase.CANCELLED:
        return "aborted"
    if estimate.phase == Phase.PENDING:
        return "pending"
    if estimate.phase == Phase.QUEUED:
        return "queued"
    if estimate.phase == Phase.SUSPENDED:
        return "rate limited"
    if estimate.phase == Phase.RUNNING:
        if estimate.estimated_total_tool_calls:
            return f"{estimate.raw_ticks}/{estimate.estimated_total_tool_calls} calls"
        return f"{estimate.raw_ticks} calls"
    return "unknown"


def _elapsed(estimate: ProgressEstimate) -> str:
    """Human-readable elapsed time."""
    s = estimate.active_duration_ms / 1000.0
    if s < 60:
        return f"{s:.0f}s"
    m = int(s // 60)
    sec = int(s % 60)
    return f"{m}m{sec}s"


# ── braille bar ──────────────────────────────────────────────────────────────

def _braille_bar(display_ticks: float, phase: Phase, bar_cells: int,
                 color: int = C_PRIMARY) -> str:
    """Render a braille progress bar.

    Each braille cell has 7 fill levels.  bar_cells controls how many
    braille characters wide the bar is.  Completed/failed bars are always
    filled to 100%.
    """
    total_levels = bar_cells * BRAILLE_LEVEL_COUNT

    # Terminal phases: fill the bar completely
    if phase in (Phase.COMPLETED, Phase.FAILED, Phase.CANCELLED):
        remaining = float(total_levels)
    else:
        remaining = max(0.0, min(float(total_levels), display_ticks))
    result: list[str] = []

    for i in range(bar_cells):
        cell_remaining = remaining - i * BRAILLE_LEVEL_COUNT
        if cell_remaining >= BRAILLE_LEVEL_COUNT:
            result.append(BRAILLE_LEVELS[-1])  # ⣿ full
        elif cell_remaining <= 0:
            result.append(BRAILLE_EMPTY)        # ⣀ empty
        else:
            result.append(BRAILLE_LEVELS[int(cell_remaining)])

    bar = "".join(result)
    if phase == Phase.COMPLETED:
        return f"{_c256(C_SUCCESS)}{bar}{_reset()}"
    if phase == Phase.FAILED:
        return f"{_c256(C_FAILED)}{bar}{_reset()}"
    if phase == Phase.CANCELLED:
        return f"{_c256(C_WARNING)}{bar}{_reset()}"
    return f"{_c256(color)}{bar}{_reset()}"


# ── cell rendering ───────────────────────────────────────────────────────────

def _render_cell(cell_id: str, estimate: ProgressEstimate,
                 cell_width: int, show_text: bool,
                 bar_width: int = 4, label_width: int = 0) -> str:
    """Render one cell: ID + braille bar + label.

    bar_width and label_width are pre-computed by the layout and never change,
    so cell width is stable across all frames.
    """
    id_str = f"{_c256(C_PRIMARY)}{cell_id}{_reset()}"

    if not show_text:
        # Compact mode: ID + bar + icon, all fixed width
        bar = _braille_bar(estimate.display_ticks, estimate.phase, bar_width)
        icon = _ICONS.get(estimate.phase, " ")
        icon_color = _ICON_COLORS.get(estimate.phase, C_DIM)
        cell = f"{id_str} {bar} {_c256(icon_color)}{icon}{_reset()}"
        return cell.ljust(cell_width)

    # Text mode: ID + bar + label, all fixed width
    bar = _braille_bar(estimate.display_ticks, estimate.phase, bar_width)
    label_text = _label(estimate)
    elapsed_text = _elapsed(estimate)
    label = f"{_c256(C_TEXT)}{label_text} {elapsed_text}{_reset()}"
    # Pad label to fixed width (accounting for ANSI codes — approximate)
    visible_len = len(label_text) + 1 + len(elapsed_text)
    label_padded = label + " " * max(0, label_width - visible_len)

    return f"{id_str} {bar} {label_padded}"


# ── public API ───────────────────────────────────────────────────────────────

class AgentProgressRenderer:
    """Pure-function renderer: estimates → ANSI lines."""

    def __init__(self, *, max_columns: int | None = None):
        self.max_columns = max_columns
        self._compact = False

    # ── single ────────────────────────────────────────────────────────────

    def render_single(self, estimate: ProgressEstimate, width: int,
                      label: str = "") -> list[str]:
        """Full-width bar for a single agent."""
        w = max(1, width - 2)  # margin
        bar_cells = min(16, max(4, w // 6))
        bar = _braille_bar(estimate.display_ticks, estimate.phase, bar_cells)

        status = _label(estimate)
        elapsed = _elapsed(estimate)
        info = f"{status}  {elapsed}"
        if label:
            info = f"{label}  {info}"

        if estimate.phase == Phase.COMPLETED:
            icon = f"{_c256(C_SUCCESS)}✓{_reset()}"
        elif estimate.phase == Phase.FAILED:
            icon = f"{_c256(C_FAILED)}✗{_reset()}"
        elif estimate.phase == Phase.QUEUED:
            icon = f"{_c256(C_WARNING)}⏳{_reset()}"
        else:
            icon = " "

        line = f" {icon} {bar} {_c256(C_TEXT)}{info}{_reset()}"
        return [line[:width]]

    # ── grid ───────────────────────────────────────────────────────────────

    def render_grid(self, estimates: dict[str, ProgressEstimate],
                    width: int, total_count: int | None = None,
                    description: str = "") -> list[str]:
        """Grid layout for multiple agents.

        Args:
            estimates: Current estimates keyed by agent ID.
            width: Terminal width in characters.
            total_count: Total number of agents in the swarm.  Layout is
                calculated once from this number and never changes, even if
                some agents haven't started yet.  Defaults to len(estimates).
            description: Optional task description shown in the header.
        """
        total = total_count if total_count is not None else len(estimates)
        if total <= 0:
            return [f"{_c256(C_PRIMARY)}── {description or 'Agent Swarm'} ──{_reset()}"]

        # Calculate layout once from total_count — never changes
        cell_gap = 2
        min_cell_width = 12
        inner_width = max(1, width - 2)

        if self.max_columns:
            columns = min(self.max_columns, inner_width // min_cell_width)
        else:
            columns = max(1, inner_width // min_cell_width)
        columns = max(1, min(columns, total))
        rows = (total + columns - 1) // columns

        cell_width = (inner_width - (columns - 1) * cell_gap) // columns
        show_text = not self._compact and cell_width >= 25

        # Pre-compute fixed widths — never change across frames
        id_width = 3
        gap_chars = 2  # space before and after bar
        if show_text:
            bar_width = max(3, min(8, (cell_width - id_width - gap_chars) // 5))
            label_width = max(0, cell_width - id_width - gap_chars - bar_width)
        else:
            bar_width = max(1, cell_width - id_width - 1 - 1)
            label_width = 0

        # Sort keys for stable output
        keys = sorted(estimates.keys())
        present = set(keys)

        # Header
        lines: list[str] = []
        title = "Agent Swarm"
        if description:
            title += f" — {description}"
        lines.append(f"{_c256(C_PRIMARY)}── {title} {_c256(C_DIM)}{'─' * max(0, width - len(title) - 6)}{_reset()}")

        # Grid — slots[i] is the key for position i, or None if empty
        slots: list[str | None] = [None] * total
        for i, key in enumerate(keys):
            if i < total:
                slots[i] = key

        for row_start in range(0, total, columns):
            cells: list[str] = []
            for col in range(columns):
                idx = row_start + col
                key = slots[idx] if idx < total else None
                if key is not None and key in present:
                    est = estimates[key]
                    cell = _render_cell(key, est, cell_width, show_text,
                                        bar_width, label_width)
                else:
                    cell = " " * cell_width
                cells.append(cell)
            lines.append(" " + (" " * cell_gap).join(cells))

        # Status bar
        lines.append(self._status_bar(estimates, width))

        return lines

    # ── list ───────────────────────────────────────────────────────────────

    def render_list(self, estimates: dict[str, ProgressEstimate],
                    width: int) -> list[str]:
        """Vertical list, one agent per line with full info."""
        lines: list[str] = []
        keys = sorted(estimates.keys())
        bar_cells = min(8, max(3, (width - 40) // 5))

        for key in keys:
            est = estimates[key]
            bar = _braille_bar(est.display_ticks, est.phase, bar_cells)
            icon = _ICONS.get(est.phase, " ")
            icon_color = _ICON_COLORS.get(est.phase, C_DIM)
            status = _label(est)
            elapsed = _elapsed(est)
            line = (
                f" {_c256(C_PRIMARY)}{key}{_reset()} "
                f"{bar} "
                f"{_c256(icon_color)}{icon}{_reset()} "
                f"{_c256(C_TEXT)}{status:<12s}{_reset()} "
                f"{_c256(C_DIM)}{elapsed}{_reset()}"
            )
            lines.append(line[:width])
        return lines

    # ── status bar ─────────────────────────────────────────────────────────

    def _status_bar(self, estimates: dict[str, ProgressEstimate],
                    width: int) -> str:
        """Aggregate status: completed / running / queued / failed."""
        total = len(estimates)
        completed = sum(1 for e in estimates.values() if e.phase == Phase.COMPLETED)
        failed = sum(1 for e in estimates.values() if e.phase == Phase.FAILED)
        running = sum(1 for e in estimates.values() if e.phase == Phase.RUNNING)
        queued = sum(1 for e in estimates.values() if e.phase in (Phase.PENDING, Phase.QUEUED))

        parts = []
        if completed:
            parts.append(f"{_c256(C_SUCCESS)}✓ {completed} done{_reset()}")
        if running:
            parts.append(f"{_c256(C_PRIMARY)}● {running} running{_reset()}")
        if queued:
            parts.append(f"{_c256(C_WARNING)}⏳ {queued} queued{_reset()}")
        if failed:
            parts.append(f"{_c256(C_FAILED)}✗ {failed} failed{_reset()}")

        text = "  ".join(parts) if parts else f"{_c256(C_DIM)}idle{_reset()}"
        return f" {text}"[:width]


def set_ascii_mode(enabled: bool = True) -> None:
    """Switch to ASCII fallback (no ANSI colors, no braille)."""
    global _ANSI
    _ANSI = not enabled