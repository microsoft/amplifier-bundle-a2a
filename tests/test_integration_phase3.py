"""Integration tests — full Phase 3 flows on localhost.

Tests the complete data flow across Phase 3 components:
- Mode B live injection via A2AInjectionHandler
- B→A downgrade (defer then respond)
- Full escalation with attribution (Mode C → A → user respond)
- Mode C success with autonomous attribution

The only mocks are AmplifierSession (to avoid needing real LLM providers)
and the confidence evaluation provider. All HTTP communication is real,
over localhost.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from amplifier_module_hooks_a2a_server.card import build_agent_card
from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
from amplifier_module_hooks_a2a_server.pending import PendingQueue
from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.server import A2AServer
from amplifier_module_tool_a2a import A2ATool

LOCAL_TOOL_CONFIG = {
    "outbound_policy": {
        "allowed_hosts": ["127.0.0.1"],
        "require_https": False,
    }
}


# --- Shared helpers (same pattern as test_integration_phase2) ---


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "integration-phase3"
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


def _make_phase3_server(tmp_path, contacts=None, config_overrides=None):
    """Create a server with ContactStore and PendingQueue for Phase 3 testing.

    Returns (server, registry, coordinator) so tests can inspect/modify state.
    """
    config = {
        "port": 0,
        "host": "127.0.0.1",
        "agent_name": "Phase3 Test Agent",
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


def _make_tool(registry):
    """Create an A2ATool wired to the given registry."""
    tool_coordinator = MagicMock()
    tool_coordinator.get_capability = MagicMock(return_value=registry)
    return A2ATool(tool_coordinator, LOCAL_TOOL_CONFIG)


# --- Test classes ---


class TestModeBLiveInjection:
    """Full flow: Mode B live injection via A2AInjectionHandler.

    Verifies:
    - Pending messages are injected into the session
    - Already-injected messages are NOT re-injected
    - New messages arriving later ARE injected
    """

    async def test_live_injection_flow(self, tmp_path):
        known_url = "http://known-agent:9000"

        # 1. Server with a "known" tier contact + PendingQueue
        server, registry, _coordinator = _make_phase3_server(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        await server.start()
        try:
            base_url = f"http://127.0.0.1:{server.port}"

            # 2. Send a message from the known contact → Mode A / INPUT_REQUIRED
            status, data = await _send_raw(
                base_url, "First question?", known_url, "Known Agent"
            )
            assert status == 200
            assert data["status"] == "INPUT_REQUIRED"
            task_id_1 = data["id"]

            # 3. Create an A2AInjectionHandler with the same pending queue
            handler = A2AInjectionHandler(registry.pending_queue, registry)

            # 4. Call handler → should inject the pending message (Mode B)
            result = await handler("provider:request", {})
            assert result.action == "inject_context"
            assert task_id_1 in result.context_injection
            assert "First question?" in result.context_injection

            # 5. Call handler again with NO new messages → "continue" (no re-injection)
            result = await handler("provider:request", {})
            assert result.action == "continue"

            # 6. Add ANOTHER message → call handler → only the new message injected
            status, data = await _send_raw(
                base_url, "Second question?", known_url, "Known Agent"
            )
            assert status == 200
            task_id_2 = data["id"]

            result = await handler("provider:request", {})
            assert result.action == "inject_context"
            assert task_id_2 in result.context_injection
            assert "Second question?" in result.context_injection
            # Must NOT contain the first message again
            assert task_id_1 not in result.context_injection

        finally:
            await server.stop()


class TestDeferThenRespond:
    """Full flow: B→A downgrade — defer then respond later.

    Verifies:
    - Defer changes message status to "deferred"
    - Injection handler skips deferred items
    - Responding to deferred message still works → COMPLETED
    """

    async def test_defer_then_respond(self, tmp_path):
        known_url = "http://known-agent:9000"

        # 1. Server with a "known" tier contact
        server, registry, _coordinator = _make_phase3_server(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        await server.start()
        try:
            base_url = f"http://127.0.0.1:{server.port}"

            # 2. Send a message → INPUT_REQUIRED
            status, data = await _send_raw(
                base_url, "Can you help?", known_url, "Known Agent"
            )
            assert status == 200
            assert data["status"] == "INPUT_REQUIRED"
            task_id = data["id"]

            # 3. Defer the message via tool → status becomes "deferred"
            tool = _make_tool(registry)
            result = await tool.execute({"operation": "defer", "task_id": task_id})
            assert result.success is True

            # Verify deferred status in PendingQueue
            msg = registry.pending_queue.get_message(task_id)
            assert msg is not None
            assert msg["status"] == "deferred"

            # Verify task_id is in registry.deferred_ids
            assert task_id in registry.deferred_ids

            # 4. Verify injection handler skips the deferred item
            handler = A2AInjectionHandler(registry.pending_queue, registry)
            result = await handler("provider:request", {})
            assert result.action == "continue"  # Nothing to inject

            # 5. Respond to the deferred message via tool → COMPLETED
            result = await tool.execute(
                {
                    "operation": "respond",
                    "task_id": task_id,
                    "message": "Here's my late reply!",
                }
            )
            assert result.success is True

            # 6. Poll the task → COMPLETED with the user's response
            poll_status, poll_data = await _poll_task(base_url, task_id)
            assert poll_status == 200
            assert poll_data["status"] == "COMPLETED"
            assert (
                poll_data["artifacts"][0]["parts"][0]["text"] == "Here's my late reply!"
            )
            assert poll_data["attribution"] == "user_response"

        finally:
            await server.stop()


class TestEscalationWithAttribution:
    """Full flow: trusted contact → Mode C → confidence NO → escalate → respond.

    Verifies:
    - Escalation sets pending entry with escalated=True
    - Responding after escalation sets attribution to "escalated_user_response"
    """

    async def test_full_escalation_attribution(self, tmp_path):
        trusted_url = "http://trusted-agent:9000"

        # 1. Server with a "trusted" tier contact, confidence evaluation enabled
        server, registry, coordinator = _make_phase3_server(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            config_overrides={"confidence_evaluation": True},
        )

        # 2. Mock AmplifierSession + provider that returns "NO" for confidence
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

                # 3. Send message → Mode C runs → confidence NO → escalates
                status, data = await _send_raw(
                    base_url,
                    "Complex question?",
                    trusted_url,
                    "Trusted Agent",
                )
                assert status == 200
                assert data["status"] == "INPUT_REQUIRED"
                task_id = data["id"]

                # 4. Verify pending entry has escalated=True
                messages = registry.pending_queue.get_pending_messages()
                assert len(messages) == 1
                assert messages[0]["task_id"] == task_id
                assert messages[0]["escalated"] is True

                # 5. Respond via tool → COMPLETED with "escalated_user_response"
                tool = _make_tool(registry)
                result = await tool.execute(
                    {
                        "operation": "respond",
                        "task_id": task_id,
                        "message": "Here's the real answer",
                    }
                )
                assert result.success is True

                # 6. Poll → COMPLETED with escalated_user_response attribution
                poll_status, poll_data = await _poll_task(base_url, task_id)
                assert poll_status == 200
                assert poll_data["status"] == "COMPLETED"
                assert poll_data["attribution"] == "escalated_user_response"
                assert (
                    poll_data["artifacts"][0]["parts"][0]["text"]
                    == "Here's the real answer"
                )

            finally:
                await server.stop()


class TestModeCSuccessAttribution:
    """Full flow: trusted contact → Mode C → COMPLETED with autonomous attribution.

    Verifies:
    - Successful Mode C sets attribution to "autonomous"
    """

    async def test_autonomous_attribution(self, tmp_path):
        trusted_url = "http://trusted-agent:9000"

        # 1. Server with a "trusted" tier contact, confidence evaluation DISABLED
        server, registry, coordinator = _make_phase3_server(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            config_overrides={"confidence_evaluation": False},
        )

        # 2. Mock AmplifierSession to return a clear answer
        mock_session = _make_mock_session("The capital of France is Paris.")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            await server.start()
            try:
                base_url = f"http://127.0.0.1:{server.port}"

                # 3. Send message → Mode C runs → COMPLETED
                status, data = await _send_raw(
                    base_url,
                    "What is the capital of France?",
                    trusted_url,
                    "Trusted Agent",
                )
                assert status == 200
                assert data["status"] == "COMPLETED"
                task_id = data["id"]

                # 4. Verify task has attribution "autonomous"
                assert data["attribution"] == "autonomous"
                assert (
                    data["artifacts"][0]["parts"][0]["text"]
                    == "The capital of France is Paris."
                )

                # 5. Verify via HTTP poll as well
                poll_status, poll_data = await _poll_task(base_url, task_id)
                assert poll_status == 200
                assert poll_data["status"] == "COMPLETED"
                assert poll_data["attribution"] == "autonomous"

            finally:
                await server.stop()
