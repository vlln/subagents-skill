"""Session registry backed by a JSON file.

Tracks: agent → session → { session_id, status, created, tasks[] }
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


_cached_registry_path: Path | None = None


def _get_registry_path() -> Path:
    global _cached_registry_path
    if _cached_registry_path is not None:
        return _cached_registry_path
    env = os.environ.get("SU BAGENT_REGISTRY", "")
    if env:
        _cached_registry_path = Path(env).resolve()
    else:
        _cached_registry_path = Path(".agents/subagents/agents.json").resolve()
    return _cached_registry_path


def _read() -> dict:
    path = _get_registry_path()
    if path.is_file():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _write(data: dict) -> None:
    path = _get_registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def register(agent: str, session: str, session_id: str, cwd: str | None = None, background: bool = False) -> None:
    """Register a new session for an agent, or update an existing session's id.

    Args:
        agent: Agent name
        session: Session name
        session_id: Unique session identifier
        cwd: Working directory (absolute path), None means current directory
        background: Whether this is a background session with queue support
    """
    now = datetime.now(timezone.utc).isoformat()
    data = _read()
    existing = data.get(agent, {}).get("sessions", {}).get(session, {})

    session_data = dict(existing)  # preserve existing fields (goal, cwd, etc.)
    session_data["session_id"] = session_id
    session_data["status"] = "running"
    if "created" not in session_data:
        session_data["created"] = now
    if "tasks" not in session_data:
        session_data["tasks"] = []

    # Add cwd if specified (don't overwrite existing)
    if cwd and "cwd" not in session_data:
        session_data["cwd"] = cwd

    # Add queue support for background sessions (only if not already set)
    if background and "mode" not in session_data:
        session_data["mode"] = "background"
        session_data["queue"] = []
        session_data["current_task"] = None

    data.setdefault(agent, {}).setdefault("sessions", {})[session] = session_data
    _write(data)


def complete(agent: str, session: str) -> None:
    """Mark a session as completed."""
    data = _read()
    try:
        data[agent]["sessions"][session]["status"] = "done"
        _write(data)
    except KeyError:
        pass


def add_task(agent: str, session: str, prompt: str, status: str) -> None:
    """Add a task record to a session."""
    now = datetime.now(timezone.utc).isoformat()
    data = _read()
    try:
        data[agent]["sessions"][session]["tasks"].append(
            {"prompt": prompt, "status": status, "time": now}
        )
        _write(data)
    except KeyError:
        pass


def get_session_id(agent: str, session: str) -> str | None:
    """Get the session_id for an agent+session pair."""
    data = _read()
    try:
        return data[agent]["sessions"][session]["session_id"]
    except KeyError:
        return None


def get_session_id_from_any(session: str) -> str | None:
    """Get the session_id for a session name across all agents."""
    data = _read()
    for agent_data in data.values():
        if session in agent_data.get("sessions", {}):
            return agent_data["sessions"][session]["session_id"]
    return None


def get_session_status(agent: str, session: str) -> str:
    """Get the status of a session, combining registry and lock info.

    Returns: "running", "done", "crashed", or "unknown".
    """
    from lock import check as is_locked

    data = _read()
    try:
        reg = data[agent]["sessions"][session]["status"]
    except KeyError:
        return "unknown"

    if is_locked(session):
        return "running"
    if reg == "running":
        return "crashed"  # registry says running but lock is gone
    return reg


def find_agent_for_session(session: str) -> str | None:
    """Find which agent owns a session name."""
    data = _read()
    for agent_name, agent_data in data.items():
        if session in agent_data.get("sessions", {}):
            return agent_name
    return None


def list_agents() -> list[str]:
    """List all registered agent names."""
    data = _read()
    return list(data.keys())


def list_sessions(agent: str) -> list[str]:
    """List all session names for an agent."""
    data = _read()
    try:
        return list(data[agent]["sessions"].keys())
    except KeyError:
        return []


def get_all_data() -> dict:
    """Return the full registry data."""
    return _read()


def enqueue_task(agent: str, session: str, prompt: str) -> str | None:
    """Add a task to the session's queue.

    Returns:
        Task ID if successful, None if session not found or not a background session.
    """
    import uuid
    data = _read()
    try:
        session_data = data[agent]["sessions"][session]
        if session_data.get("mode") != "background":
            return None

        task_id = f"task-{uuid.uuid4().hex[:8]}"
        task = {
            "id": task_id,
            "prompt": prompt,
            "status": "queued",
            "queued_at": datetime.now(timezone.utc).isoformat(),
        }
        session_data.setdefault("queue", []).append(task)
        _write(data)
        return task_id
    except KeyError:
        return None


def dequeue_task(agent: str, session: str) -> dict | None:
    """Remove and return the next task from the queue.

    Returns:
        Task dict if queue not empty, None otherwise.
    """
    data = _read()
    try:
        session_data = data[agent]["sessions"][session]
        queue = session_data.get("queue", [])
        if not queue:
            return None

        task = queue.pop(0)
        _write(data)
        return task
    except KeyError:
        return None


def set_current_task(agent: str, session: str, task: dict | None) -> None:
    """Set the currently running task."""
    data = _read()
    try:
        data[agent]["sessions"][session]["current_task"] = task
        _write(data)
    except KeyError:
        pass


def cancel_task(agent: str, session: str, task_index: int | None = None, cancel_all: bool = False) -> int:
    """Cancel task(s) in the queue.

    Args:
        agent: Agent name
        session: Session name
        task_index: Index of task to cancel (0-based), None to cancel current
        cancel_all: Cancel all queued tasks

    Returns:
        Number of tasks cancelled
    """
    data = _read()
    try:
        session_data = data[agent]["sessions"][session]
        queue = session_data.get("queue", [])

        if cancel_all:
            count = len(queue)
            session_data["queue"] = []
            _write(data)
            return count
        elif task_index is not None:
            if 0 <= task_index < len(queue):
                queue.pop(task_index)
                _write(data)
                return 1

        return 0
    except KeyError:
        return 0


def get_session_data(agent: str, session: str) -> dict | None:
    """Get full session data including cwd, queue, current_task."""
    data = _read()
    try:
        return data[agent]["sessions"][session]
    except KeyError:
        return None


# ── goal ──────────────────────────────────────────────────────────────────

def set_goal(agent: str, session: str, goal_text: str, max_iterations: int = 10) -> bool:
    """Set a goal on a session. Fails if session has active queue.

    Returns:
        True if goal was set, False if session has active queue or doesn't exist.
    """
    data = _read()
    try:
        session_data = data[agent]["sessions"][session]
        # Goal and queue are mutually exclusive
        queue = session_data.get("queue", [])
        current_task = session_data.get("current_task")
        if queue or current_task:
            return False
        session_data["goal"] = {
            "text": goal_text,
            "max_iterations": max_iterations,
            "current_iteration": 0,
            "status": "active",
            "started_at": datetime.now(timezone.utc).isoformat(),
        }
        _write(data)
        return True
    except KeyError:
        return False


def clear_goal(agent: str, session: str) -> bool:
    """Remove goal from session. Returns True if a goal was cleared."""
    data = _read()
    try:
        if "goal" in data[agent]["sessions"][session]:
            del data[agent]["sessions"][session]["goal"]
            _write(data)
            return True
        return False
    except KeyError:
        return False


def cancel_goal(agent: str, session: str) -> bool:
    """Mark goal as cancelled (worker checks this and stops)."""
    data = _read()
    try:
        goal = data[agent]["sessions"][session].get("goal")
        if goal and goal.get("status") == "active":
            goal["status"] = "cancelled"
            goal["last_update"] = datetime.now(timezone.utc).isoformat()
            _write(data)
            return True
        return False
    except KeyError:
        return False


def get_goal(agent: str, session: str) -> dict | None:
    """Get goal data for a session, or None if no goal set."""
    data = _read()
    try:
        return data[agent]["sessions"][session].get("goal")
    except KeyError:
        return None


def has_active_goal(agent: str, session: str) -> bool:
    """Check if session has an active (not cancelled/completed) goal."""
    goal = get_goal(agent, session)
    return goal is not None and goal.get("status") in ("active",)


def has_active_queue(agent: str, session: str) -> bool:
    """Check if session has an active queue (tasks queued or running)."""
    data = _read()
    try:
        session_data = data[agent]["sessions"][session]
        return bool(session_data.get("queue") or session_data.get("current_task"))
    except KeyError:
        return False


def update_goal_iteration(agent: str, session: str, iteration: int, status: str = "active") -> None:
    """Update goal progress (iteration counter and status)."""
    data = _read()
    try:
        goal = data[agent]["sessions"][session].get("goal")
        if goal:
            goal["current_iteration"] = iteration
            goal["status"] = status
            goal["last_update"] = datetime.now(timezone.utc).isoformat()
            _write(data)
    except KeyError:
        pass


def mark_goal_complete(agent: str, session: str, iterations: int) -> None:
    """Mark goal as successfully completed."""
    data = _read()
    try:
        goal = data[agent]["sessions"][session].get("goal")
        if goal:
            goal["status"] = "completed"
            goal["current_iteration"] = iterations
            goal["completed_at"] = datetime.now(timezone.utc).isoformat()
            _write(data)
    except KeyError:
        pass


def mark_goal_failed(agent: str, session: str, iterations: int, reason: str = "max_iterations") -> None:
    """Mark goal as failed (max iterations reached or error)."""
    data = _read()
    try:
        goal = data[agent]["sessions"][session].get("goal")
        if goal:
            goal["status"] = "failed"
            goal["current_iteration"] = iterations
            goal["failed_reason"] = reason
            goal["failed_at"] = datetime.now(timezone.utc).isoformat()
            _write(data)
    except KeyError:
        pass