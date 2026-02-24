"""A2A client tool module — send messages to remote agents.

Provides a single 'a2a' tool to the LLM with operations:
  - agents: list known remote agents (merged from config, mDNS, contacts)
  - card: fetch a remote agent's identity card
  - send: send a message to a remote agent
  - status: check the status of a previously submitted task
  - discover: browse the local network for A2A agents via mDNS
  - approve: approve a pending first-contact request
  - block: block a pending first-contact request
  - contacts: list all contacts with their tiers
  - trust: upgrade an existing contact's tier
  - respond: reply to a pending Mode A message
  - dismiss: dismiss a pending Mode A message
  - defer: defer a pending message (Mode B → Mode A downgrade)
"""

import asyncio
import logging
from typing import Any

from amplifier_core import ToolResult

from .client import A2AClient

__amplifier_module_type__ = "tool"

logger = logging.getLogger(__name__)


class A2ATool:
    """A2A client tool — communicates with remote Amplifier agents."""

    name = "a2a"

    def __init__(self, coordinator: Any, config: dict[str, Any]) -> None:
        self.coordinator = coordinator
        self.config = config
        self.client = A2AClient(timeout=config.get("default_timeout", 30.0))
        self._registry: Any = None  # Lazy — hook may not have mounted yet
        self._pending_outgoing: dict[str, dict] = {}  # task_id -> tracking info
        self._completed_outgoing: list[dict] = []  # completed tasks ready for injection
        self._poll_interval: float = config.get("poll_interval", 5.0)
        self._poller_task: asyncio.Task | None = None

    @property
    def registry(self) -> Any:
        """Lazy-load the A2ARegistry from coordinator capabilities."""
        if self._registry is None:
            self._registry = self.coordinator.get_capability("a2a.registry")
        return self._registry

    @property
    def description(self) -> str:
        return (
            "Communicate with remote Amplifier agents via the A2A protocol. "
            "Operations: 'agents' (list known agents from all sources), "
            "'card' (fetch agent identity card), "
            "'send' (send a message and wait for response), "
            "'status' (check status of a previously submitted task), "
            "'discover' (browse LAN for agents via mDNS), "
            "'approve' (approve a pending first-contact request), "
            "'block' (block a pending first-contact request), "
            "'contacts' (list all contacts), 'trust' (update contact tier), "
            "'respond' (reply to a pending message), "
            "'dismiss' (dismiss a pending message), "
            "'defer' (defer a pending message for later), "
            "'whoami' (show this agent's identity and URL), "
            "'add_contact' (add a remote agent by URL)."
        )

    @property
    def input_schema(self) -> dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "operation": {
                    "type": "string",
                    "enum": [
                        "agents",
                        "card",
                        "send",
                        "status",
                        "discover",
                        "approve",
                        "block",
                        "contacts",
                        "trust",
                        "respond",
                        "dismiss",
                        "defer",
                        "whoami",
                        "add_contact",
                    ],
                    "description": "The operation to perform",
                },
                "agent": {
                    "type": "string",
                    "description": (
                        "Agent name or URL (required for 'card', 'send', "
                        "'approve', 'block', 'trust')"
                    ),
                },
                "task_id": {
                    "type": "string",
                    "description": (
                        "Task ID of a pending message "
                        "(required for 'respond', 'dismiss', 'defer')"
                    ),
                },
                "message": {
                    "type": "string",
                    "description": "Message to send (required for 'send')",
                },
                "timeout": {
                    "type": "number",
                    "description": "Timeout in seconds for 'send' (default: 30)",
                },
                "blocking": {
                    "type": "boolean",
                    "description": (
                        "If true (default), wait for response. "
                        "If false, return task handle immediately."
                    ),
                },
                "url": {
                    "type": "string",
                    "description": "Agent URL for 'add_contact' operation",
                },
                "tier": {
                    "type": "string",
                    "description": (
                        "Trust tier: 'known' or 'trusted' "
                        "(used by 'approve', 'trust', and 'add_contact')"
                    ),
                },
            },
            "required": ["operation"],
        }

    async def execute(self, input: dict[str, Any]) -> ToolResult:
        """Execute an A2A operation."""
        operation = input.get("operation", "")

        try:
            if operation == "agents":
                return await self._op_agents()
            elif operation == "card":
                return await self._op_card(input)
            elif operation == "send":
                return await self._op_send(input)
            elif operation == "status":
                return await self._op_status(input)
            elif operation == "discover":
                return await self._op_discover(input)
            elif operation == "approve":
                return await self._op_approve(input)
            elif operation == "block":
                return await self._op_block(input)
            elif operation == "contacts":
                return await self._op_contacts()
            elif operation == "trust":
                return await self._op_trust(input)
            elif operation == "respond":
                return await self._op_respond(input)
            elif operation == "dismiss":
                return await self._op_dismiss(input)
            elif operation == "defer":
                return await self._op_defer(input)
            elif operation == "whoami":
                return await self._op_whoami()
            elif operation == "add_contact":
                return await self._op_add_contact(input)
            else:
                return ToolResult(
                    success=False,
                    error={"message": f"Unknown operation: {operation}"},
                )
        except Exception as e:
            logger.exception("A2A operation '%s' failed", operation)
            error_msg = str(e).strip()
            if not error_msg:
                error_msg = f"{type(e).__name__}: (no details)"
            else:
                error_msg = f"{type(e).__name__}: {error_msg}"
            return ToolResult(success=False, error={"message": error_msg})

    async def _op_agents(self) -> ToolResult:
        """List all known remote agents from all sources."""
        if not self.registry:
            return ToolResult(
                success=False,
                error={
                    "message": (
                        "A2A registry not available. "
                        "Is the hooks-a2a-server module loaded?"
                    )
                },
            )

        # Merge: config + discovered + contacts
        seen_urls: set[str] = set()
        merged: list[dict[str, Any]] = []

        # Config agents
        for agent in self.registry.get_agents():
            agent["source"] = "config"
            seen_urls.add(agent["url"])
            merged.append(agent)

        # mDNS discovered
        for agent in self.registry.get_discovered_agents():
            if agent["url"] in seen_urls:
                # Mark existing entry as both sources
                for m in merged:
                    if m["url"] == agent["url"]:
                        m["source"] = "both"
                continue
            agent["source"] = "mdns"
            seen_urls.add(agent["url"])
            merged.append(agent)

        # Contacts
        if self.registry.contact_store:
            for contact in self.registry.contact_store.list_contacts():
                if contact["url"] not in seen_urls:
                    merged.append(
                        {
                            "name": contact["name"],
                            "url": contact["url"],
                            "source": "contact",
                        }
                    )
                    seen_urls.add(contact["url"])

        if not merged:
            return ToolResult(
                success=True,
                output="No known agents. Use 'discover' to find agents on "
                "the local network, or add agents to "
                "module_config.tool-a2a.known_agents in settings.",
            )
        return ToolResult(success=True, output=merged)

    async def _op_discover(self, input: dict[str, Any]) -> ToolResult:
        """Browse the local network for A2A agents via mDNS."""
        from .discovery import browse_mdns

        timeout = input.get("timeout", 2.0)
        agents = await browse_mdns(timeout=timeout)
        if not agents:
            return ToolResult(
                success=True,
                output="No agents discovered on the local network.",
            )
        # Cache discovered agents in registry
        if self.registry:
            self.registry.cache_discovered_agents(agents)
        return ToolResult(success=True, output=agents)

    async def _op_card(self, input: dict[str, Any]) -> ToolResult:
        """Fetch a remote agent's Agent Card."""
        agent = input.get("agent", "").strip()
        if not agent:
            return ToolResult(
                success=False,
                error={"message": "Agent name or URL required"},
            )

        # Resolve to URL
        url = self._resolve_url(agent)
        if not url:
            return ToolResult(
                success=False,
                error={"message": f"Unknown agent: {agent}"},
            )

        # Check cache first
        if self.registry:
            cached = self.registry.get_cached_card(url)
            if cached:
                return ToolResult(success=True, output=cached)

        # Fetch from remote
        card = await self.client.fetch_agent_card(url)
        if self.registry:
            self.registry.cache_card(url, card)
        return ToolResult(success=True, output=card)

    async def _op_send(self, input: dict[str, Any]) -> ToolResult:
        """Send a message to a remote agent, optionally polling for completion."""
        agent = input.get("agent", "").strip()
        if not agent:
            return ToolResult(
                success=False,
                error={"message": "Agent name or URL required"},
            )

        message = input.get("message", "").strip()
        if not message:
            return ToolResult(
                success=False,
                error={"message": "Message text required"},
            )

        blocking = input.get("blocking", True)
        timeout = input.get("timeout", self.config.get("default_timeout", 30.0))

        # Resolve to URL
        url = self._resolve_url(agent)
        if not url:
            return ToolResult(
                success=False,
                error={"message": f"Unknown agent: {agent}"},
            )

        # Ensure we have the agent's card (validates reachability)
        card = None
        if self.registry:
            card = self.registry.get_cached_card(url)
            if not card:
                card = await self.client.fetch_agent_card(url)
                self.registry.cache_card(url, card)

        # Check if remote agent supports real-time responses.
        # If not, override to non-blocking — don't waste time waiting
        # for a response that requires human interaction on the other end.
        is_async_agent = (
            not (card or {}).get("capabilities", {}).get("realtimeResponse", False)
        )
        if is_async_agent and blocking:
            blocking = False

        # Derive sender identity from the hook's live state (single source of truth)
        sender_url = None
        sender_name = None
        if self.registry and self.registry.card:
            sender_url = self.registry.card.get("url")
            sender_name = self.registry.card.get("name")
        # Config override as escape hatch (tool-only mode, behind reverse proxy)
        if self.config.get("sender_url"):
            sender_url = self.config["sender_url"]
        if self.config.get("sender_name"):
            sender_name = self.config["sender_name"]

        # Send the message (always non-blocking at the HTTP level)
        result = await self.client.send_message(
            url, message, sender_url=sender_url, sender_name=sender_name
        )

        if not blocking:
            # Track non-terminal tasks for background polling
            terminal_states = {"COMPLETED", "FAILED", "REJECTED"}
            task_id = result.get("id")
            if task_id and result.get("status") not in terminal_states:
                self._track_outgoing(task_id, url, agent)
            # Add context about why we went async if it was due to the
            # remote agent not supporting real-time responses
            if is_async_agent and result.get("status") not in terminal_states:
                result["_note"] = (
                    f"{agent} is an async agent (CLI session) — the user "
                    f"will see your message on their next interaction. "
                    f"The response will be delivered to you automatically "
                    f"when it arrives."
                )
            return ToolResult(success=True, output=result)

        # Blocking: poll until terminal state or timeout
        task_id = result.get("id")
        if not task_id:
            return ToolResult(success=True, output=result)

        terminal_states = {"COMPLETED", "FAILED", "REJECTED"}
        if result.get("status") in terminal_states:
            return ToolResult(success=True, output=result)

        # Poll loop
        poll_interval = 1.0
        elapsed = 0.0
        while elapsed < timeout:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval
            try:
                polled = await self.client.get_task_status(url, task_id)
                if polled.get("status") in terminal_states:
                    return ToolResult(success=True, output=polled)
            except Exception:
                pass  # Keep polling on transient errors

        # Timeout: track for background polling and return task handle
        self._track_outgoing(task_id, url, agent)
        return ToolResult(
            success=True,
            output={
                "id": task_id,
                "status": result.get("status", "UNKNOWN"),
                "message": (
                    f"Timeout after {timeout}s. Use "
                    f"a2a(operation='status', agent='{agent}', "
                    f"task_id='{task_id}') to check later."
                ),
            },
        )

    async def _op_status(self, input: dict[str, Any]) -> ToolResult:
        """Check the status of a remote task."""
        agent = input.get("agent", "").strip()
        task_id = input.get("task_id", "").strip()

        if not agent:
            return ToolResult(
                success=False,
                error={"message": "Agent name or URL required"},
            )
        if not task_id:
            return ToolResult(
                success=False,
                error={"message": "Task ID required"},
            )

        url = self._resolve_url(agent)
        if not url:
            return ToolResult(
                success=False,
                error={"message": f"Unknown agent: {agent}"},
            )

        result = await self.client.get_task_status(url, task_id)
        return ToolResult(success=True, output=result)

    def _check_stores(self) -> ToolResult | None:
        """Return an error ToolResult if contact_store/pending_queue are missing."""
        if (
            not self.registry
            or not self.registry.contact_store
            or not self.registry.pending_queue
        ):
            return ToolResult(
                success=False,
                error={
                    "message": (
                        "Contact management not available. "
                        "Is the hooks-a2a-server module loaded?"
                    )
                },
            )
        return None

    def _find_pending_approval(self, sender_url: str) -> dict | None:
        """Find a pending approval by sender URL."""
        for approval in self.registry.pending_queue.get_pending_approvals():
            if approval["sender_url"] == sender_url:
                return approval
        return None

    async def _op_approve(self, input: dict[str, Any]) -> ToolResult:
        """Approve a pending first-contact request."""
        err = self._check_stores()
        if err:
            return err

        agent = input.get("agent", "").strip()
        if not agent:
            return ToolResult(
                success=False,
                error={"message": "Agent URL required"},
            )

        approval = self._find_pending_approval(agent)
        if not approval:
            return ToolResult(
                success=False,
                error={"message": f"No pending approval found for {agent}"},
            )

        tier = input.get("tier", "known")
        await self.registry.contact_store.add_contact(
            approval["sender_url"], approval["sender_name"], tier
        )
        await self.registry.pending_queue.update_approval_status(
            approval["task_id"], "approved"
        )
        self.registry.update_task(
            approval["task_id"],
            "COMPLETED",
            artifacts=[
                {
                    "parts": [
                        {
                            "text": (
                                f"Contact {approval['sender_name']} approved. "
                                "The sender can now send messages."
                            )
                        }
                    ]
                }
            ],
        )
        return ToolResult(
            success=True,
            output=(
                f"Approved {approval['sender_name']} ({agent}) as '{tier}' contact."
            ),
        )

    async def _op_block(self, input: dict[str, Any]) -> ToolResult:
        """Block a pending first-contact request."""
        err = self._check_stores()
        if err:
            return err

        agent = input.get("agent", "").strip()
        if not agent:
            return ToolResult(
                success=False,
                error={"message": "Agent URL required"},
            )

        approval = self._find_pending_approval(agent)
        if not approval:
            return ToolResult(
                success=False,
                error={"message": f"No pending approval found for {agent}"},
            )

        await self.registry.pending_queue.update_approval_status(
            approval["task_id"], "blocked"
        )
        self.registry.update_task(
            approval["task_id"],
            "FAILED",
            error=f"Contact request from {approval['sender_name']} was blocked.",
        )
        return ToolResult(
            success=True,
            output=f"Blocked {approval['sender_name']} ({agent}).",
        )

    async def _op_contacts(self) -> ToolResult:
        """List all contacts."""
        err = self._check_stores()
        if err:
            return err

        contacts = self.registry.contact_store.list_contacts()
        if not contacts:
            return ToolResult(
                success=True,
                output="No contacts yet.",
            )
        return ToolResult(success=True, output=contacts)

    async def _op_trust(self, input: dict[str, Any]) -> ToolResult:
        """Update an existing contact's trust tier."""
        err = self._check_stores()
        if err:
            return err

        agent = input.get("agent", "").strip()
        if not agent:
            return ToolResult(
                success=False,
                error={"message": "Agent URL required"},
            )

        tier = input.get("tier", "").strip()
        if not tier:
            return ToolResult(
                success=False,
                error={"message": "Tier required (e.g. 'known' or 'trusted')"},
            )

        contact = self.registry.contact_store.get_contact(agent)
        if not contact:
            return ToolResult(
                success=False,
                error={"message": f"No contact found for {agent}"},
            )

        await self.registry.contact_store.update_tier(agent, tier)
        return ToolResult(
            success=True,
            output=f"Updated {contact['name']} ({agent}) to tier '{tier}'.",
        )

    async def _op_respond(self, input: dict[str, Any]) -> ToolResult:
        """Reply to a pending Mode A message."""
        err = self._check_stores()
        if err:
            return err

        task_id = input.get("task_id", "").strip()
        if not task_id:
            return ToolResult(
                success=False,
                error={"message": "task_id required"},
            )

        message = input.get("message", "").strip()
        if not message:
            return ToolResult(
                success=False,
                error={"message": "Message text required"},
            )

        pending = self.registry.pending_queue.get_message(task_id)
        if not pending:
            return ToolResult(
                success=False,
                error={"message": "No pending message with this task_id"},
            )

        attribution = (
            "escalated_user_response" if pending.get("escalated") else "user_response"
        )
        self.registry.update_task(
            task_id,
            "COMPLETED",
            artifacts=[{"parts": [{"text": message}]}],
            attribution=attribution,
        )
        await self.registry.pending_queue.update_message_status(task_id, "responded")
        return ToolResult(
            success=True,
            output=f"Response sent for task {task_id}.",
        )

    async def _op_defer(self, input: dict[str, Any]) -> ToolResult:
        """Defer a pending message (Mode B → Mode A downgrade)."""
        task_id = input.get("task_id", "").strip()
        if not task_id:
            return ToolResult(
                success=False,
                error={"message": "task_id required"},
            )

        err = self._check_stores()
        if err:
            return err

        pending = self.registry.pending_queue.get_message(task_id)
        if not pending:
            return ToolResult(
                success=False,
                error={"message": f"No pending message with task_id: {task_id}"},
            )

        await self.registry.pending_queue.update_message_status(task_id, "deferred")
        self.registry.deferred_ids.add(task_id)

        return ToolResult(
            success=True,
            output=(
                f"Message {task_id} deferred. You can still respond later with "
                f"a2a(operation='respond', task_id='{task_id}', message='...')"
            ),
        )

    async def _op_dismiss(self, input: dict[str, Any]) -> ToolResult:
        """Dismiss a pending Mode A message."""
        err = self._check_stores()
        if err:
            return err

        task_id = input.get("task_id", "").strip()
        if not task_id:
            return ToolResult(
                success=False,
                error={"message": "task_id required"},
            )

        pending = self.registry.pending_queue.get_message(task_id)
        if not pending:
            return ToolResult(
                success=False,
                error={"message": "No pending message with this task_id"},
            )

        self.registry.update_task(
            task_id,
            "REJECTED",
            error="Dismissed by user",
            attribution="dismissed",
        )
        await self.registry.pending_queue.update_message_status(task_id, "dismissed")
        return ToolResult(
            success=True,
            output=f"Message {task_id} dismissed.",
        )

    async def _op_whoami(self) -> ToolResult:
        """Return this agent's identity and URL for sharing."""
        if not self.registry or not self.registry.card:
            return ToolResult(
                success=True,
                output={
                    "server_running": False,
                    "message": "A2A server is not running. No identity available.",
                },
            )

        card = self.registry.card
        return ToolResult(
            success=True,
            output={
                "name": card.get("name", "Unknown"),
                "url": card.get("url", "Unknown"),
                "description": card.get("description", ""),
                "realtimeResponse": card.get("capabilities", {}).get(
                    "realtimeResponse", False
                ),
                "server_running": getattr(self.registry, "server_running", True),
                "skills": card.get("skills", []),
            },
        )

    async def _op_add_contact(self, input: dict[str, Any]) -> ToolResult:
        """Add a remote agent as a contact by fetching their Agent Card."""
        url = input.get("url", "").strip()
        if not url:
            return ToolResult(
                success=False,
                error={
                    "message": (
                        "URL required. Example: "
                        "a2a(operation='add_contact', url='http://agent.local:8222')"
                    )
                },
            )

        tier = input.get("tier", "known")

        # Fetch the remote agent's card to get their name and capabilities
        try:
            card = await self.client.fetch_agent_card(url)
        except ConnectionError as e:
            return ToolResult(
                success=False,
                error={"message": f"Could not reach agent at {url}: {e}"},
            )

        agent_name = card.get("name", "Unknown Agent")

        # Add to contacts
        stores = self._check_stores()
        if stores:
            return stores

        await self.registry.contact_store.add_contact(url, agent_name, tier)

        # Cache the card
        if self.registry:
            self.registry.cache_card(url, card)

        return ToolResult(
            success=True,
            output={
                "message": f"Added {agent_name} ({url}) as a {tier} contact.",
                "name": agent_name,
                "url": url,
                "tier": tier,
                "capabilities": card.get("capabilities", {}),
                "skills": card.get("skills", []),
            },
        )

    def _track_outgoing(self, task_id: str, agent_url: str, agent_name: str) -> None:
        """Track a sent message that hasn't received a terminal response yet."""
        from datetime import datetime, timezone

        self._pending_outgoing[task_id] = {
            "task_id": task_id,
            "agent_url": agent_url,
            "agent_name": agent_name,
            "submitted_at": datetime.now(timezone.utc).isoformat(),
        }

    def _collect_completed(self) -> list[dict]:
        """Collect and drain completed outgoing tasks for injection."""
        completed = list(self._completed_outgoing)
        self._completed_outgoing.clear()
        return completed

    async def _handle_outgoing_responses(self, event: str, data: dict) -> Any:
        """provider:request hook: inject completed outgoing responses into session."""
        from amplifier_core.models import HookResult

        completed = self._collect_completed()
        if not completed:
            return HookResult(action="continue")

        text = self._build_response_injection(completed)
        return HookResult(
            action="inject_context",
            context_injection=text,
            context_injection_role="user",
            ephemeral=True,
            suppress_output=True,
        )

    @staticmethod
    def _build_response_injection(completed: list[dict]) -> str:
        """Build injection text for completed outgoing responses."""
        parts = []
        for item in completed:
            result = item.get("result", {})
            agent_name = item.get("agent_name", "Unknown Agent")
            task_id = item.get("task_id", "unknown")
            status = result.get("status", "UNKNOWN")
            attribution = result.get("attribution", "unknown")

            # Extract response text from artifacts
            response_text = ""
            for artifact in result.get("artifacts", []):
                for part in artifact.get("parts", []):
                    if "text" in part:
                        response_text += part["text"]

            # Format attribution
            attr_display = {
                "autonomous": "Answered autonomously by agent",
                "user_response": "Answered by the user directly",
                "escalated_user_response": "Agent couldn't answer; user responded",
                "dismissed": "Dismissed by the user",
            }.get(attribution, f"Attribution: {attribution}")

            parts.append(
                f"<a2a-response>\n"
                f"Response from {agent_name} (task {task_id[:12]}...):\n"
                f"Status: {status}\n"
                f"{f'Response: "{response_text}"' if response_text else 'No response text'}\n"
                f"({attr_display})\n"
                f"</a2a-response>"
            )

        return "\n\n".join(parts)

    def _start_poller(self) -> None:
        """Start the background polling task."""
        if self._poller_task is None or self._poller_task.done():
            self._poller_task = asyncio.create_task(self._poll_outgoing())

    async def _poll_outgoing(self) -> None:
        """Background task: poll pending outgoing tasks for terminal status."""
        terminal_states = {"COMPLETED", "FAILED", "REJECTED"}
        while True:
            try:
                await asyncio.sleep(self._poll_interval)
                # Copy keys to avoid dict mutation during iteration
                task_ids = list(self._pending_outgoing.keys())
                for task_id in task_ids:
                    info = self._pending_outgoing.get(task_id)
                    if not info:
                        continue
                    try:
                        result = await self.client.get_task_status(
                            info["agent_url"], task_id
                        )
                        if result.get("status") in terminal_states:
                            # Move to completed
                            completed_info = {**info, "result": result}
                            self._completed_outgoing.append(completed_info)
                            del self._pending_outgoing[task_id]
                    except Exception:
                        pass  # Skip on error, retry next cycle
            except asyncio.CancelledError:
                break
            except Exception:
                pass  # Don't crash the poller on unexpected errors

    async def _stop_poller(self) -> None:
        """Cancel the background polling task."""
        if self._poller_task and not self._poller_task.done():
            self._poller_task.cancel()
            try:
                await self._poller_task
            except asyncio.CancelledError:
                pass
            self._poller_task = None

    def _resolve_url(self, agent: str) -> str | None:
        """Resolve an agent name or URL to a URL."""
        if self.registry:
            return self.registry.resolve_agent_url(agent)
        # Fallback: if it looks like a URL, use it directly
        if agent.startswith("http://") or agent.startswith("https://"):
            return agent
        return None


async def mount(coordinator: Any, config: dict[str, Any] | None = None) -> None:
    """Mount the A2A tool into the coordinator."""
    config = config or {}
    tool = A2ATool(coordinator, config)
    await coordinator.mount("tools", tool, name=tool.name)
    tool._start_poller()

    # Register hook for injecting completed outgoing responses
    try:
        coordinator.hooks.register(
            "provider:request",
            tool._handle_outgoing_responses,
            priority=6,
            name="a2a-outgoing-delivery",
        )
    except AttributeError:
        pass  # hooks not available (e.g., in test mocks)

    async def cleanup() -> None:
        await tool._stop_poller()
        await tool.client.close()

    coordinator.register_cleanup(cleanup)
