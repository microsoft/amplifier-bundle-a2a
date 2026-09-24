"""Integration tests — full Phase 2 flows on localhost.

Tests the complete data flow across multiple Phase 2 components:
- ContactStore + PendingQueue + first-contact approval
- Mode A routing + respond operation
- Mode C → A escalation via confidence evaluation
- Async send + status polling

The only mocks are AmplifierSession (to avoid needing real LLM providers)
and the confidence evaluation provider. All HTTP communication is real,
over localhost.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from amplifier_module_hooks_a2a_server.card import build_agent_card
from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.pending import PendingQueue
from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.server import A2AServer
from amplifier_module_tool_a2a import A2ATool
from amplifier_module_tool_a2a.client import A2AClient

LOCAL_TOOL_CONFIG = {
    "outbound_policy": {
        "allowed_hosts": ["127.0.0.1"],
        "require_https": False,
    }
}


# --- Shared helpers ---


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "integration-phase2"
    coordinator.parent_id = None
    coordinator.config = {
        "session": {
            "orchestrator": "loop-basic",
            "context": "context-simple",
        },
        "providers": [{"module": "provider-test", "config": {"model": "test"}}],
        "tools": [{"module": "tool-filesystem"}],
        "hooks": [
            {"module": "hooks-a2a-server", "config": {"port": 0}},
        ],
    }
    coordinator.register_capability = MagicMock()
    coordinator.register_cleanup = MagicMock()
    return coordinator


def _make_mock_session(response_text="The answer is 42"):
    """Create a mock AmplifierSession that returns a canned response."""
    mock = AsyncMock()
    mock.execute = AsyncMock(return_value=response_text)
    mock.initialize = AsyncMock()
    mock.cleanup = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


def _make_phase2_server(tmp_path, contacts=None, config_overrides=None):
    """Create a server with ContactStore and PendingQueue for Phase 2 testing.

    Returns (server, registry, coordinator) so tests can inspect/modify state.
    """
    config = {
        "port": 0,
        "host": "127.0.0.1",
        "agent_name": "Phase2 Test Agent",
        "authentication": {"required": False},
    }
    if config_overrides:
        config.update(config_overrides)

    registry = A2ARegistry()
    contact_store = ContactStore(path=tmp_path / "contacts.json")
    pending_queue = PendingQueue(base_dir=tmp_path)
    registry.contact_store = contact_store
    registry.pending_queue = pending_queue

    # Pre-populate contacts synchronously via internals
    if contacts:
        for c in contacts:
            contact_store._contacts.append(
                {
                    "url": c["url"],
                    "name": c["name"],
                    "tier": c.get("tier", "known"),
                    "first_seen": "2025-01-01T00:00:00+00:00",
                    "last_seen": "2025-01-01T00:00:00+00:00",
                }
            )

    card = build_agent_card(config)
    coordinator = _make_mock_coordinator()
    server = A2AServer(registry, card, coordinator, config)
    return server, registry, coordinator


async def _send_raw(base_url, text, sender_url, sender_name="Remote Agent"):
    """Send a message with sender_url via raw HTTP (simulating a remote agent)."""
    async with aiohttp.ClientSession() as session:
        payload = {
            "message": {
                "role": "user",
                "parts": [{"text": text}],
            },
            "sender_url": sender_url,
            "sender_name": sender_name,
        }
        async with session.post(
            f"{base_url}/a2a/v1/message:send", json=payload
        ) as resp:
            return resp.status, await resp.json()


async def _poll_task(base_url, task_id):
    """Poll task status via raw HTTP GET."""
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{base_url}/a2a/v1/tasks/{task_id}") as resp:
            return resp.status, await resp.json()


# --- Test classes ---


class TestFirstContactApprovalFlow:
    """Full flow: unknown agent → approval required → approve via tool → contact added."""

    async def test_full_approval_flow(self, tmp_path):
        # 1. Server with empty ContactStore (no contacts)
        server, registry, _coordinator = _make_phase2_server(tmp_path)

        await server.start()
        try:
            base_url = f"http://127.0.0.1:{server.port}"
            unknown_url = "http://unknown-agent:9000"

            # 2. Client sends from unknown agent → INPUT_REQUIRED
            status, data = await _send_raw(
                base_url, "Hello, I'm new!", unknown_url, "New Agent"
            )
            assert status == 200
            assert data["status"] == "INPUT_REQUIRED"
            task_id = data["id"]

            # 3-4. Verify approval is in PendingQueue
            approvals = registry.pending_queue.get_pending_approvals()
            assert len(approvals) == 1
            assert approvals[0]["sender_url"] == unknown_url
            assert approvals[0]["sender_name"] == "New Agent"
            assert approvals[0]["task_id"] == task_id
            assert approvals[0]["status"] == "pending"

            # 5. Simulate approval via tool: a2a(operation="approve", agent="...")
            tool_coordinator = MagicMock()
            tool_coordinator.get_capability = MagicMock(return_value=registry)
            tool = A2ATool(tool_coordinator, LOCAL_TOOL_CONFIG)

            result = await tool.execute(
                {"operation": "approve", "agent": unknown_url, "tier": "known"}
            )
            assert result.success is True

            # 6. Verify contact was added to ContactStore
            contact = registry.contact_store.get_contact(unknown_url)
            assert contact is not None
            assert contact["name"] == "New Agent"
            assert contact["tier"] == "known"

            # 7. Verify the original task is updated (approved → COMPLETED)
            task = registry.get_task(task_id)
            assert task is not None
            assert task["status"] == "COMPLETED"

        finally:
            await server.stop()


class TestModeARespondFlow:
    """Full flow: known contact → Mode A → respond via tool → COMPLETED with reply."""

    async def test_respond_flow(self, tmp_path):
        known_url = "http://known-agent:9000"

        # 1. Server with a "known" tier contact
        server, registry, _coordinator = _make_phase2_server(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        await server.start()
        try:
            base_url = f"http://127.0.0.1:{server.port}"

            # 2. Client sends from known contact → INPUT_REQUIRED (Mode A)
            status, data = await _send_raw(
                base_url, "Can you help me?", known_url, "Known Agent"
            )
            assert status == 200
            assert data["status"] == "INPUT_REQUIRED"
            task_id = data["id"]

            # 3-4. Verify message is in PendingQueue
            messages = registry.pending_queue.get_pending_messages()
            assert len(messages) == 1
            assert messages[0]["task_id"] == task_id
            assert messages[0]["sender_url"] == known_url
            assert messages[0]["status"] == "pending"

            # 5. Simulate respond via tool:
            #    a2a(operation="respond", task_id="...", message="reply")
            tool_coordinator = MagicMock()
            tool_coordinator.get_capability = MagicMock(return_value=registry)
            tool = A2ATool(tool_coordinator, LOCAL_TOOL_CONFIG)

            result = await tool.execute(
                {
                    "operation": "respond",
                    "task_id": task_id,
                    "message": "Here is your answer!",
                }
            )
            assert result.success is True

            # 6. Client polls task — should be COMPLETED with the reply as artifact
            poll_status, poll_data = await _poll_task(base_url, task_id)
            assert poll_status == 200
            assert poll_data["status"] == "COMPLETED"
            assert (
                poll_data["artifacts"][0]["parts"][0]["text"] == "Here is your answer!"
            )

        finally:
            await server.stop()


class TestModeCToAEscalation:
    """Full flow: trusted contact → Mode C → confidence NO → escalate to Mode A."""

    async def test_confidence_escalation(self, tmp_path):
        trusted_url = "http://trusted-agent:9000"

        # 1. Server with a "trusted" tier contact, confidence evaluation enabled
        server, registry, coordinator = _make_phase2_server(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            config_overrides={"confidence_evaluation": True},
        )

        # 2-3. Mock AmplifierSession (returns a response) and provider (returns "NO")
        mock_session = _make_mock_session("I'm not sure about this")
        mock_provider = AsyncMock()
        mock_provider.complete = AsyncMock(return_value="NO")
        coordinator.mount_points = {"providers": [mock_provider]}

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            await server.start()
            try:
                base_url = f"http://127.0.0.1:{server.port}"

                # 4. Client sends from trusted contact
                status, data = await _send_raw(
                    base_url,
                    "Complex question?",
                    trusted_url,
                    "Trusted Agent",
                )

                # 5. Server spawns Mode C child session, gets response,
                #    evaluates confidence → NO
                # 6. Server escalates to Mode A: task becomes INPUT_REQUIRED
                assert status == 200
                assert data["status"] == "INPUT_REQUIRED"
                task_id = data["id"]

                # 7. Verify message is in PendingQueue
                messages = registry.pending_queue.get_pending_messages()
                assert len(messages) == 1
                assert messages[0]["task_id"] == task_id
                assert messages[0]["sender_url"] == trusted_url

            finally:
                await server.stop()


class TestAsyncSendAndStatusPolling:
    """Full flow: send → poll (pending) → respond → poll (completed)."""

    async def test_async_poll_flow(self, tmp_path):
        known_url = "http://known-agent:9000"

        # 1. Server with "known" tier contact (Mode A — returns INPUT_REQUIRED)
        server, registry, _coordinator = _make_phase2_server(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        await server.start()
        try:
            base_url = f"http://127.0.0.1:{server.port}"

            # 2. Client sends (non-blocking at HTTP level) — gets INPUT_REQUIRED
            status, data = await _send_raw(
                base_url, "Async question", known_url, "Known Agent"
            )
            assert status == 200
            assert data["status"] == "INPUT_REQUIRED"
            task_id = data["id"]

            # 3. Client polls status — still INPUT_REQUIRED
            client = A2AClient(
                timeout=5.0,
                outbound_policy=LOCAL_TOOL_CONFIG["outbound_policy"],
            )
            try:
                polled = await client.get_task_status(base_url, task_id)
                assert polled["status"] == "INPUT_REQUIRED"

                # 4. Simulate user responding via tool (modifies PendingQueue + registry)
                tool_coordinator = MagicMock()
                tool_coordinator.get_capability = MagicMock(return_value=registry)
                tool = A2ATool(tool_coordinator, LOCAL_TOOL_CONFIG)

                result = await tool.execute(
                    {
                        "operation": "respond",
                        "task_id": task_id,
                        "message": "Async reply",
                    }
                )
                assert result.success is True

                # 5. Client polls status again — now COMPLETED
                polled = await client.get_task_status(base_url, task_id)
                assert polled["status"] == "COMPLETED"
                assert polled["artifacts"][0]["parts"][0]["text"] == "Async reply"
            finally:
                await client.close()

        finally:
            await server.stop()
