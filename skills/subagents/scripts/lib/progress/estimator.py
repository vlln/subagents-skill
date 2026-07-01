"""Pure-data progress estimator — ported from kimi-code AgentSwarmProgressEstimator.

Algorithm overview:
  1. Build prior from completed members: typical duration, typical tool calls, typical rate.
  2. For running members, estimate local rate with exponential-decay weighting.
  3. Blend local rate and prior rate via geometric interpolation.
  4. Estimate total tool calls: blended_rate * estimated_total_time.
  5. Soft-bound the estimate within [typical/1.5, typical*1.5].
  6. Confidence-weighted boost: more samples + more observations → bolder progress bar.
  7. Exponential-decay catch-up animation for display_ticks.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from .types import Phase, ProgressEstimate

# ── default constants ───────────────────────────────────────────────────────

DEFAULT_RATE_WINDOW_MS = 45_000
DEFAULT_CATCHUP_TIME_MS = 1_500
DEFAULT_WORKLOAD_SPREAD_FACTOR = 1.5
DEFAULT_UNFINISHED_PROGRESS_CAP = 0.85
DEFAULT_MAX_BOOST_GAIN = 0.75
RATE_TOOL_CONFIDENCE_SCALE = 4
BOOST_TOOL_CONFIDENCE_SCALE = 3
MIN_RATE_FACTOR = 0.25
HALF_TICK = 0.5


# ── internal state ───────────────────────────────────────────────────────────

@dataclass
class _MemberState:
    started_at_ms: float | None = None
    paused_at_ms: float | None = None
    paused_duration_ms: float = 0.0
    terminal_at_ms: float | None = None
    terminal_kind: str | None = None  # 'completed' | 'failed' | 'cancelled'
    raw_ticks: int = 0
    seen_tool_call_ids: set[str] = field(default_factory=set)
    tool_call_active_times_ms: list[float] = field(default_factory=list)
    display_ticks: float = 0.0
    last_estimate_at_ms: float | None = None
    last_target_ticks: float | None = None


@dataclass
class _CompletedSample:
    total_ms: float
    raw_ticks: int


@dataclass
class _EstimatePrior:
    completed_count: int
    typical_total_ms: float
    typical_tool_calls: float
    typical_rate_per_ms: float


# ── helpers ──────────────────────────────────────────────────────────────────

def _confidence(count: float, scale: float) -> float:
    return 1.0 - math.exp(-max(0.0, count) / scale)


def _geometric_interpolate(low: float, high: float, weight: float) -> float:
    safe_low = max(1e-15, low)
    safe_high = max(1e-15, high)
    return math.exp((1.0 - weight) * math.log(safe_low) + weight * math.log(safe_high))


def _log_median(values: list[float]) -> float:
    logs = sorted(math.log(v) for v in values if v > 0 and math.isfinite(v))
    if not logs:
        return 1.0
    n = len(logs)
    if n % 2 == 1:
        return math.exp(logs[n // 2])
    return math.exp((logs[n // 2 - 1] + logs[n // 2]) / 2.0)


# ── estimator ────────────────────────────────────────────────────────────────

class ProgressEstimator:
    """Pure-data progress estimator.  Feed events, query estimates."""

    def __init__(
        self,
        *,
        rate_window_ms: float = DEFAULT_RATE_WINDOW_MS,
        catchup_time_ms: float = DEFAULT_CATCHUP_TIME_MS,
        max_catchup_ticks_per_second: float | None = None,
        workload_spread_factor: float = DEFAULT_WORKLOAD_SPREAD_FACTOR,
        unfinished_progress_cap: float = DEFAULT_UNFINISHED_PROGRESS_CAP,
        max_boost_gain: float = DEFAULT_MAX_BOOST_GAIN,
    ) -> None:
        self._members: dict[str, _MemberState] = {}
        self._rate_window_ms = rate_window_ms
        self._catchup_time_ms = catchup_time_ms
        self._max_catchup_ticks_per_second = max_catchup_ticks_per_second
        self._workload_spread_factor = workload_spread_factor
        self._unfinished_progress_cap = unfinished_progress_cap
        self._max_boost_gain = max_boost_gain

    # ── event input ──────────────────────────────────────────────────────

    def ensure_member(self, member_key: str) -> None:
        self._get_or_create(member_key)

    def mark_started(self, member_key: str, now_ms: float) -> None:
        state = self._get_or_create(member_key)
        self._start_work(state, now_ms)
        if state.raw_ticks == 0:
            state.raw_ticks = 1
            state.display_ticks = max(state.display_ticks, 1.0)
        state.terminal_at_ms = None
        state.terminal_kind = None

    def mark_queued(self, member_key: str, now_ms: float) -> None:
        state = self._get_or_create(member_key)
        if state.started_at_ms is None or state.terminal_kind is not None:
            return
        if state.paused_at_ms is None:
            state.paused_at_ms = now_ms
        state.last_estimate_at_ms = now_ms
        state.last_target_ticks = None

    def record_tool_call(self, member_key: str, tool_call_id: str, now_ms: float) -> bool:
        """Record a tool call. Returns True if accepted (not a duplicate)."""
        state = self._get_or_create(member_key)
        self._start_work(state, now_ms)
        if tool_call_id in state.seen_tool_call_ids:
            return False
        state.seen_tool_call_ids.add(tool_call_id)
        state.tool_call_active_times_ms.append(self._active_elapsed_ms(state, now_ms))
        state.raw_ticks += 1
        state.display_ticks = max(state.display_ticks + 1.0, float(state.raw_ticks))
        state.terminal_at_ms = None
        state.terminal_kind = None
        return True

    def mark_completed(self, member_key: str, now_ms: float) -> None:
        self._mark_terminal(member_key, now_ms, "completed")

    def mark_failed(self, member_key: str, now_ms: float) -> None:
        self._mark_terminal(member_key, now_ms, "failed")

    def mark_cancelled(self, member_key: str, now_ms: float) -> None:
        self._mark_terminal(member_key, now_ms, "cancelled")

    # ── query ────────────────────────────────────────────────────────────

    def estimate(self, member_key: str, capacity_ticks: int, now_ms: float) -> ProgressEstimate:
        """Estimate progress for a single member."""
        state = self._get_or_create(member_key)
        capacity = max(1, capacity_ticks)
        raw_ticks = state.raw_ticks
        previous_display = max(state.display_ticks, float(raw_ticks))
        prior = self._build_prior()
        phase = self._phase_of(state)

        base = ProgressEstimate(
            raw_ticks=raw_ticks,
            display_ticks=previous_display,
            phase=phase,
            boosted=False,
            active_duration_ms=self._active_elapsed_ms(state, now_ms),
        )

        if phase != Phase.RUNNING or raw_ticks <= 0 or prior is None:
            state.display_ticks = previous_display
            state.last_estimate_at_ms = now_ms
            state.last_target_ticks = None
            return base

        completed_conf = _confidence(float(prior.completed_count), 1.0 + self._workload_spread_factor)
        estimated_total = self._estimate_total_tool_calls(state, prior, now_ms, completed_conf)
        estimated_progress = min(self._unfinished_progress_cap, raw_ticks / estimated_total)
        raw_progress = min(1.0, raw_ticks / capacity)

        if estimated_progress <= raw_progress:
            state.display_ticks = previous_display
            state.last_estimate_at_ms = now_ms
            state.last_target_ticks = None
            return ProgressEstimate(
                raw_ticks=raw_ticks,
                display_ticks=previous_display,
                phase=phase,
                boosted=False,
                estimated_total_tool_calls=round(estimated_total),
                estimated_progress=estimated_progress,
                active_duration_ms=self._active_elapsed_ms(state, now_ms),
            )

        tool_conf = _confidence(float(raw_ticks), BOOST_TOOL_CONFIDENCE_SCALE)
        boost_conf = completed_conf * tool_conf
        boost_gain = self._max_boost_gain * boost_conf
        target_progress = raw_progress + boost_gain * (estimated_progress - raw_progress)
        target_ticks = max(float(raw_ticks), target_progress * capacity)
        display_ticks = self._catch_up_display_ticks(
            state, previous_display, target_ticks, capacity, now_ms
        )

        state.display_ticks = display_ticks
        state.last_estimate_at_ms = now_ms
        state.last_target_ticks = target_ticks

        return ProgressEstimate(
            raw_ticks=raw_ticks,
            display_ticks=display_ticks,
            phase=phase,
            boosted=display_ticks > float(raw_ticks),
            estimated_total_tool_calls=round(estimated_total),
            estimated_progress=estimated_progress,
            target_progress=target_progress,
            target_ticks=target_ticks,
            confidence=boost_conf,
            active_duration_ms=self._active_elapsed_ms(state, now_ms),
        )

    def estimate_all(
        self, capacity_ticks: int, now_ms: float
    ) -> dict[str, ProgressEstimate]:
        """Estimate progress for all members."""
        return {
            key: self.estimate(key, capacity_ticks, now_ms)
            for key in self._members
        }

    def has_pending_catchup(self) -> bool:
        """True if any member's display_ticks is still catching up to target."""
        return any(
            s.last_target_ticks is not None
            and s.last_target_ticks > s.display_ticks + 0.1
            for s in self._members.values()
        )

    # ── internal: lifecycle ───────────────────────────────────────────────

    def _mark_terminal(self, member_key: str, now_ms: float, kind: str) -> None:
        state = self._get_or_create(member_key)
        self._finish_paused_interval(state, now_ms)
        state.terminal_at_ms = now_ms
        state.terminal_kind = kind
        state.display_ticks = max(state.display_ticks, float(state.raw_ticks))
        state.last_target_ticks = None

    def _start_work(self, state: _MemberState, now_ms: float) -> None:
        was_queued = state.started_at_ms is None or state.paused_at_ms is not None
        if state.started_at_ms is None:
            state.started_at_ms = now_ms
        self._finish_paused_interval(state, now_ms)
        if was_queued:
            state.last_estimate_at_ms = None
            state.last_target_ticks = None

    def _finish_paused_interval(self, state: _MemberState, now_ms: float) -> None:
        if state.paused_at_ms is None:
            return
        state.paused_duration_ms += max(0.0, now_ms - state.paused_at_ms)
        state.paused_at_ms = None

    def _active_elapsed_ms(self, state: _MemberState, now_ms: float) -> float:
        if state.started_at_ms is None:
            return 0.0
        current_paused = (
            0.0 if state.paused_at_ms is None else max(0.0, now_ms - state.paused_at_ms)
        )
        return max(0.0, now_ms - state.started_at_ms - state.paused_duration_ms - current_paused)

    def _get_or_create(self, member_key: str) -> _MemberState:
        if member_key not in self._members:
            self._members[member_key] = _MemberState()
        return self._members[member_key]

    @staticmethod
    def _phase_of(state: _MemberState) -> Phase:
        if state.terminal_kind == "completed":
            return Phase.COMPLETED
        if state.terminal_kind == "failed":
            return Phase.FAILED
        if state.terminal_kind == "cancelled":
            return Phase.CANCELLED
        if state.paused_at_ms is not None:
            return Phase.SUSPENDED
        if state.started_at_ms is not None:
            return Phase.RUNNING
        if state.raw_ticks > 0 or state.started_at_ms is not None:
            return Phase.QUEUED
        return Phase.PENDING

    # ── internal: prior ───────────────────────────────────────────────────

    def _build_prior(self) -> _EstimatePrior | None:
        samples = self._completed_samples()
        if not samples:
            return None
        return _EstimatePrior(
            completed_count=len(samples),
            typical_total_ms=_log_median([s.total_ms for s in samples]),
            typical_tool_calls=_log_median([float(s.raw_ticks) for s in samples]),
            typical_rate_per_ms=_log_median(
                [(s.raw_ticks + HALF_TICK) / s.total_ms for s in samples]
            ),
        )

    def _completed_samples(self) -> list[_CompletedSample]:
        samples: list[_CompletedSample] = []
        for state in self._members.values():
            if state.terminal_kind != "completed":
                continue
            if state.started_at_ms is None or state.terminal_at_ms is None:
                continue
            if state.raw_ticks <= 0:
                continue
            total_ms = self._active_elapsed_ms(state, state.terminal_at_ms)
            if total_ms <= 0:
                continue
            samples.append(_CompletedSample(total_ms=total_ms, raw_ticks=state.raw_ticks))
        return samples

    # ── internal: total tool call estimation ──────────────────────────────

    def _estimate_total_tool_calls(
        self,
        state: _MemberState,
        prior: _EstimatePrior,
        now_ms: float,
        completed_conf: float,
    ) -> float:
        elapsed_ms = self._active_elapsed_ms(state, now_ms)
        local_rate = self._estimate_local_rate_per_ms(state, elapsed_ms)
        rate_weight = _confidence(float(state.raw_ticks), RATE_TOOL_CONFIDENCE_SCALE)
        clamped_local_rate = max(local_rate, prior.typical_rate_per_ms * MIN_RATE_FACTOR)
        rate_per_ms = _geometric_interpolate(
            prior.typical_rate_per_ms, clamped_local_rate, rate_weight
        )
        total_ms = max(prior.typical_total_ms, elapsed_ms / self._unfinished_progress_cap)
        estimated = rate_per_ms * total_ms
        bounded = self._soft_bound_total_tool_calls(estimated, prior, completed_conf)
        return max(bounded, state.raw_ticks / self._unfinished_progress_cap, 1.0)

    def _soft_bound_total_tool_calls(
        self, total: float, prior: _EstimatePrior, completed_conf: float
    ) -> float:
        lower = prior.typical_tool_calls / self._workload_spread_factor
        upper = prior.typical_tool_calls * self._workload_spread_factor
        bounded = max(lower, min(upper, total))
        if bounded == total:
            return total
        return _geometric_interpolate(total, bounded, completed_conf)

    def _estimate_local_rate_per_ms(
        self, state: _MemberState, elapsed_ms: float
    ) -> float:
        if elapsed_ms <= 0 or not state.tool_call_active_times_ms:
            return 0.0
        decayed = 0.0
        for t in state.tool_call_active_times_ms:
            decayed += math.exp(-max(0.0, elapsed_ms - t) / self._rate_window_ms)
        decayed_elapsed = self._rate_window_ms * (1.0 - math.exp(-elapsed_ms / self._rate_window_ms))
        if decayed_elapsed <= 0:
            return 0.0
        return decayed / decayed_elapsed

    # ── internal: catch-up animation ──────────────────────────────────────

    def _catch_up_display_ticks(
        self,
        state: _MemberState,
        previous_display: float,
        target_ticks: float,
        capacity_ticks: int,
        now_ms: float,
    ) -> float:
        if target_ticks <= previous_display:
            return previous_display
        last_estimate = state.last_estimate_at_ms if state.last_estimate_at_ms is not None else now_ms
        elapsed = max(0.0, now_ms - last_estimate)
        if elapsed <= 0:
            return previous_display
        alpha = 1.0 - math.exp(-elapsed / self._catchup_time_ms)
        desired_delta = (target_ticks - previous_display) * alpha
        max_rate = (
            self._max_catchup_ticks_per_second
            if self._max_catchup_ticks_per_second is not None
            else capacity_ticks / 2.0
        )
        max_delta = max(0.0, max_rate * (elapsed / 1000.0))
        return previous_display + min(desired_delta, max_delta)