"""A2A injection handler for provider:request hook.

Checks for pending approval requests and pending messages on EVERY
provider:request event, injecting only items whose task_id has not
been previously injected.  This turns the handler from a "drain on
session start" pattern into a "live stream" pattern (Mode B).
"""

import logging
from typing import Any

from amplifier_core.models import HookResult

from .pending import PendingQueue
from .registry import A2ARegistry

logger = logging.getLogger(__name__)


class A2AInjectionHandler:
    """Hook handler that injects pending approvals and messages into the active session.

    Registered on the ``provider:request`` event.  On every call it
    checks for pending approvals and messages whose task_id is NOT in
    ``_injected_ids`` and NOT in ``registry.deferred_ids``.  New items
    are injected and their IDs added to the set.  Items with
    ``status="deferred"`` are also excluded by PendingQueue filtering.
    """

    def __init__(
        self, pending_queue: PendingQueue, registry: A2ARegistry | None = None
    ) -> None:
        self._pending_queue = pending_queue
        self._registry = registry
        self._injected_ids: set[str] = set()

    async def __call__(self, event: str, data: dict[str, Any]) -> HookResult:
        """Check for new pending approvals/messages and inject any unseen items."""
        deferred = self._registry.deferred_ids if self._registry else set()

        # Gather new approvals (pending status only, not yet injected)
        new_approvals = [
            a
            for a in self._pending_queue.get_pending_approvals()
            if a["task_id"] not in self._injected_ids and a["task_id"] not in deferred
        ]

        # Gather new messages (pending status only, not yet injected/deferred)
        # Note: get_pending_messages() already filters status=="pending",
        # so deferred items are excluded automatically.  The deferred_ids
        # check is a second safety net for the injection handler.
        new_messages = [
            m
            for m in self._pending_queue.get_pending_messages()
            if m["task_id"] not in self._injected_ids and m["task_id"] not in deferred
        ]

        if not new_approvals and not new_messages:
            return HookResult(action="continue")

        # Mark these items as injected
        for a in new_approvals:
            self._injected_ids.add(a["task_id"])
        for m in new_messages:
            self._injected_ids.add(m["task_id"])

        # Build injection text
        sections: list[str] = []
        if new_approvals:
            sections.append(self._build_approval_text(new_approvals))
        if new_messages:
            sections.append(self._build_message_text(new_messages))
        text = "\n\n".join(sections)
        wrapped_text = (
            f'<system-reminder source="hooks-a2a-server">\n{text}\n</system-reminder>'
        )

        return HookResult(
            action="inject_context",
            context_injection=wrapped_text,
            context_injection_role="user",
            ephemeral=True,
            suppress_output=True,
        )

    @staticmethod
    def _extract_message_text(message: dict) -> str:
        """Extract plain text from message parts."""
        text = ""
        for part in message.get("parts", []):
            if isinstance(part, dict) and "text" in part:
                text += part["text"]
        return text

    @staticmethod
    def _build_approval_text(approvals: list[dict]) -> str:
        """Build the injection string listing all pending approval requests."""
        lines: list[str] = ["<a2a-approval-request>"]
        for approval in approvals:
            sender_name = approval.get("sender_name", "Unknown Agent")
            sender_url = approval.get("sender_url", "unknown")
            lines.append(f'New agent requesting access: "{sender_name}" ({sender_url})')
            msg_text = A2AInjectionHandler._extract_message_text(
                approval.get("message", {})
            )
            if msg_text:
                lines.append(f'Message: "{msg_text}"')
            lines.append(
                f'Use a2a(operation="approve", agent="{sender_url}") to allow,'
            )
            lines.append(f'or a2a(operation="block", agent="{sender_url}") to block.')
        lines.append("</a2a-approval-request>")
        return "\n".join(lines)

    @staticmethod
    def _build_message_text(messages: list[dict]) -> str:
        """Build the injection string listing all pending messages."""
        count = len(messages)
        noun = "message" if count == 1 else "messages"
        lines: list[str] = [
            "<a2a-pending-messages>",
            f"You have {count} pending {noun} from a remote agent:",
        ]
        for msg_entry in messages:
            sender_name = msg_entry.get("sender_name", "Unknown Agent")
            sender_url = msg_entry.get("sender_url", "unknown")
            task_id = msg_entry.get("task_id", "unknown")
            lines.append("")
            lines.append(f"From: {sender_name} ({sender_url})")
            msg_text = A2AInjectionHandler._extract_message_text(
                msg_entry.get("message", {})
            )
            if msg_text:
                lines.append(f'Message: "{msg_text}"')
            lines.append(
                f'Use a2a(operation="respond", task_id="{task_id}", message="your reply") to respond,'
            )
            lines.append(
                f'or a2a(operation="dismiss", task_id="{task_id}") to dismiss.'
            )
        lines.append("</a2a-pending-messages>")
        return "\n".join(lines)
