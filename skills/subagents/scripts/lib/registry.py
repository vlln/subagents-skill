"""Session registry backed by a JSON file.

Tracks: agent → session → { session_id, status, created, tasks[] }
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _get_registry_path() -> Path:
    env = os.environ.get("SU BAGENT_REGISTRY", "")
    if env:
        return Path(env)
    return Path(".agents/subagents/agents.json")


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


def register(agent: str, session: str, session_id: str) -> None:
    """Register a new session for an agent."""
    now = datetime.now(timezone.utc).isoformat()
    data = _read()
    data.setdefault(agent, {}).setdefault("sessions", {})[session] = {
        "session_id": session_id,
        "status": "running",
        "created": now,
        "tasks": [],
    }
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