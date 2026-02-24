"""A2ARegistry — shared state between tool-a2a and hooks-a2a-server."""

import time
import uuid
from dataclasses import dataclass, field
from typing import Any


@dataclass
class _KnownAgent:
    name: str
    url: str


@dataclass
class _Task:
    id: str
    status: str  # SUBMITTED, WORKING, COMPLETED, FAILED, INPUT_REQUIRED
    history: list[dict[str, Any]] = field(default_factory=list)
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    attribution: str | None = None


@dataclass
class _CachedCard:
    card: dict[str, Any]
    expires_at: float


class A2ARegistry:
    """Shared state for A2A communication.

    Holds known agents (from config), pending tasks, and cached agent cards.
    Registered as a coordinator capability by hooks-a2a-server.
    Retrieved by tool-a2a via coordinator.get_capability("a2a.registry").
    """

    def __init__(self, known_agents: list[dict[str, Any]] | None = None) -> None:
        self._agents: list[_KnownAgent] = []
        if known_agents:
            for agent in known_agents:
                self._agents.append(_KnownAgent(name=agent["name"], url=agent["url"]))
        self._tasks: dict[str, _Task] = {}
        self._card_cache: dict[str, _CachedCard] = {}
        self._discovered_agents: list[dict[str, Any]] = []
        self._discovered_agents_expires: float = 0.0
        self.contact_store: Any | None = None  # Set by mount()
        self.pending_queue: Any | None = None  # Set by mount()
        self.deferred_ids: set[str] = set()
        self.card: dict[str, Any] | None = None  # Set by mount()
        self.server_running: bool = True  # Set to False on port collision

    def get_agents(self) -> list[dict[str, Any]]:
        """Return all known agents as a list of dicts."""
        return [{"name": a.name, "url": a.url} for a in self._agents]

    def resolve_agent_url(self, name_or_url: str) -> str | None:
        """Resolve an agent name or URL to a URL.

        If the input looks like a URL (starts with http:// or https://),
        return it directly. Otherwise, look up by name (case-insensitive).
        Returns None if the agent is not found.
        """
        if name_or_url.startswith(("http://", "https://")):
            return name_or_url
        for agent in self._agents:
            if agent.name.lower() == name_or_url.lower():
                return agent.url
        return None

    def create_task(self, message: dict[str, Any]) -> str:
        """Create a new task from an incoming message. Returns the task ID."""
        task_id = str(uuid.uuid4())
        self._tasks[task_id] = _Task(
            id=task_id,
            status="SUBMITTED",
            history=[message],
        )
        return task_id

    def update_task(
        self,
        task_id: str,
        status: str,
        artifacts: list[dict[str, Any]] | None = None,
        error: str | None = None,
        attribution: str | None = None,
    ) -> None:
        """Update a task's status, artifacts, and/or error."""
        task = self._tasks.get(task_id)
        if task is None:
            return
        task.status = status
        if artifacts is not None:
            task.artifacts = artifacts
        if error is not None:
            task.error = error
        if attribution is not None:
            task.attribution = attribution

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID, or None if not found."""
        task = self._tasks.get(task_id)
        if task is None:
            return None
        result: dict[str, Any] = {
            "id": task.id,
            "status": task.status,
            "history": task.history,
            "artifacts": task.artifacts,
        }
        if task.error is not None:
            result["error"] = task.error
        if task.attribution is not None:
            result["attribution"] = task.attribution
        return result

    def cache_discovered_agents(
        self, agents: list[dict[str, Any]], ttl: float = 30.0
    ) -> None:
        """Cache mDNS discovered agents with a TTL."""
        self._discovered_agents = agents
        self._discovered_agents_expires = time.time() + ttl

    def get_discovered_agents(self) -> list[dict[str, Any]]:
        """Get cached discovered agents, or empty list if expired."""
        if self._discovered_agents_expires <= time.time():
            self._discovered_agents = []
            return []
        return self._discovered_agents

    def cache_card(self, url: str, card: dict[str, Any], ttl: float = 300.0) -> None:
        """Cache an agent card with a TTL (seconds)."""
        self._card_cache[url] = _CachedCard(card=card, expires_at=time.time() + ttl)

    def get_cached_card(self, url: str) -> dict[str, Any] | None:
        """Get a cached agent card, or None if expired/missing."""
        cached = self._card_cache.get(url)
        if cached is None:
            return None
        if cached.expires_at <= time.time():
            del self._card_cache[url]
            return None
        return cached.card
