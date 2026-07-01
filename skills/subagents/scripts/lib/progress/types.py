"""Pure data types for progress estimation."""

from dataclasses import dataclass, field
from enum import Enum


class Phase(Enum):
    PENDING = "pending"
    QUEUED = "queued"
    SUSPENDED = "suspended"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class ProgressEstimate:
    """Output of a single estimate() call. Rendering layers consume only this."""

    raw_ticks: int
    display_ticks: float
    phase: Phase
    boosted: bool = False
    estimated_total_tool_calls: int | None = None
    estimated_progress: float | None = None
    target_progress: float | None = None
    target_ticks: float | None = None
    confidence: float | None = None
    active_duration_ms: float = 0.0