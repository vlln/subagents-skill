"""Abstract backend interface for subagent."""

from abc import ABC, abstractmethod


class BaseBackend(ABC):
    """Abstract backend for running subagent sessions.

    Each backend implements how to create, resume, and list sessions
    for a specific agent provider (e.g. kimi, claude, codex, pi, kiro).
    """

    @abstractmethod
    def create_session(
        self, user: str, system: str | None = None, model: str | None = None
    ) -> tuple[str, int]:
        """Create a new session and run the prompt."""

    @abstractmethod
    def resume_session(
        self, session_id: str, user: str, system: str | None = None, model: str | None = None
    ) -> int:
        """Resume an existing session and run the prompt."""

    @abstractmethod
    def list_sessions(self) -> list[dict]:
        """List all sessions known to this backend."""

    def close(self) -> None:
        """Clean up backend resources. Optional override."""