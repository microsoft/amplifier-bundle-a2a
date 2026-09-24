"""Tests for LLM confidence evaluation."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient as AioTestClient, TestServer as AioTestServer

from amplifier_module_hooks_a2a_server.card import build_agent_card
from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.evaluation import evaluate_confidence
from amplifier_module_hooks_a2a_server.pending import PendingQueue
from amplifier_module_hooks_a2a_server.registry import A2ARegistry


# ---------------------------------------------------------------------------
# Unit tests for evaluate_confidence()
# ---------------------------------------------------------------------------


class TestEvaluateConfidence:
    """Unit tests for the standalone evaluate_confidence function."""

    async def test_returns_true_for_yes(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="YES")
        result = await evaluate_confidence(provider, "What is 2+2?", "4")
        assert result is True

    async def test_returns_true_for_yes_with_extra_text(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="YES, the response is good")
        result = await evaluate_confidence(provider, "What is 2+2?", "4")
        assert result is True

    async def test_returns_false_for_no(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="NO")
        result = await evaluate_confidence(provider, "What is 2+2?", "I like cats")
        assert result is False

    async def test_returns_false_for_no_with_explanation(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="NO, it doesn't answer the question")
        result = await evaluate_confidence(provider, "What is 2+2?", "I like cats")
        assert result is False

    async def test_returns_true_on_timeout(self):
        async def slow_complete(messages):
            await asyncio.sleep(10)
            return "NO"

        provider = MagicMock()
        provider.complete = slow_complete
        result = await evaluate_confidence(provider, "What is 2+2?", "4", timeout=0.05)
        assert result is True

    async def test_returns_true_on_error(self):
        provider = MagicMock()
        provider.complete = AsyncMock(side_effect=RuntimeError("LLM exploded"))
        result = await evaluate_confidence(provider, "What is 2+2?", "4")
        assert result is True

    async def test_handles_dict_response(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value={"content": "YES"})
        result = await evaluate_confidence(provider, "What is 2+2?", "4")
        assert result is True

    async def test_handles_empty_response(self):
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="")
        result = await evaluate_confidence(provider, "What is 2+2?", "4")
        assert result is False

    async def test_handles_object_with_content_attr(self):
        """Provider returns an object with a .content attribute."""
        response_obj = MagicMock()
        response_obj.content = "YES"
        provider = MagicMock()
        provider.complete = AsyncMock(return_value=response_obj)
        result = await evaluate_confidence(provider, "Q?", "A")
        assert result is True

    async def test_handles_dict_with_text_key(self):
        """Provider returns {"text": "NO"} format."""
        provider = MagicMock()
        provider.complete = AsyncMock(return_value={"text": "NO"})
        result = await evaluate_confidence(provider, "Q?", "A")
        assert result is False

    async def test_passes_correct_messages_to_provider(self):
        """Verify the messages sent to the provider contain question and response."""
        provider = MagicMock()
        provider.complete = AsyncMock(return_value="YES")
        await evaluate_confidence(provider, "my question", "my response")

        call_args = provider.complete.call_args[0][0]
        assert len(call_args) == 2
        assert call_args[0]["role"] == "system"
        assert call_args[1]["role"] == "user"
        assert "my question" in call_args[1]["content"]
        assert "my response" in call_args[1]["content"]


# ---------------------------------------------------------------------------
# Integration tests: confidence evaluation in server.py
# ---------------------------------------------------------------------------


def _make_mock_coordinator(providers=None):
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.parent_id = None
    coordinator.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        "providers": [{"module": "provider-test", "config": {"model": "test-model"}}],
        "tools": [{"module": "tool-filesystem"}],
        "hooks": [{"module": "hooks-a2a-server", "config": {"port": 8222}}],
    }
    coordinator.mount_points = {"providers": providers or []}
    return coordinator


def _make_mock_session(response_text="The answer is 42"):
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=response_text)
    mock_session.initialize = AsyncMock()
    mock_session.cleanup = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _make_server_with_contacts(
    tmp_path, contacts=None, config_overrides=None, providers=None
):
    config = {
        "port": 0,
        "agent_name": "Test Agent",
        "agent_description": "Test",
        "authentication": {"required": False},
    }
    if config_overrides:
        config.update(config_overrides)

    registry = A2ARegistry()
    registry.contact_store = ContactStore(path=tmp_path / "contacts.json")
    registry.pending_queue = PendingQueue(base_dir=tmp_path)

    if contacts:
        for c in contacts:
            registry.contact_store._contacts.append(
                {
                    "url": c["url"],
                    "name": c["name"],
                    "tier": c.get("tier", "known"),
                    "first_seen": "2025-01-01T00:00:00+00:00",
                    "last_seen": "2025-01-01T00:00:00+00:00",
                }
            )

    card = build_agent_card(config)
    coordinator = _make_mock_coordinator(providers=providers)
    from amplifier_module_hooks_a2a_server.server import A2AServer

    return A2AServer(registry, card, coordinator, config), registry


def _msg_payload(text="Hello", sender_url="http://remote-agent:9000", sender_name=None):
    body = {
        "message": {"role": "user", "parts": [{"text": text}]},
        "sender_url": sender_url,
    }
    if sender_name is not None:
        body["sender_name"] = sender_name
    return body


class TestConfidenceEscalation:
    """Server integration: Mode C -> confidence check -> possible Mode A escalation."""

    async def test_mode_c_escalates_to_mode_a_on_low_confidence(self, tmp_path):
        """Trusted contact -> Mode C -> confidence NO -> INPUT_REQUIRED."""
        trusted_url = "http://trusted-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="NO")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            providers=[mock_provider],
        )
        mock_session = _make_mock_session("Autonomous answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="What is the answer?",
                        sender_url=trusted_url,
                        sender_name="Trusted Agent",
                    ),
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "INPUT_REQUIRED"

    async def test_mode_c_completes_on_high_confidence(self, tmp_path):
        """Trusted contact -> Mode C -> confidence YES -> COMPLETED."""
        trusted_url = "http://trusted-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="YES")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            providers=[mock_provider],
        )
        mock_session = _make_mock_session("Autonomous answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="What is the answer?",
                        sender_url=trusted_url,
                    ),
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "COMPLETED"
                assert data["artifacts"][0]["parts"][0]["text"] == "Autonomous answer"

    async def test_confidence_skipped_when_disabled(self, tmp_path):
        """config.confidence_evaluation=false -> no evaluation call."""
        trusted_url = "http://trusted-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="NO")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            config_overrides={"confidence_evaluation": False},
            providers=[mock_provider],
        )
        mock_session = _make_mock_session("Autonomous answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="What is the answer?",
                        sender_url=trusted_url,
                    ),
                )
                data = await resp.json()
                # Should complete normally — evaluation was disabled
                assert data["status"] == "COMPLETED"
                # Provider.complete should NOT have been called for evaluation
                mock_provider.complete.assert_not_called()

    async def test_confidence_skipped_for_known_tier(self, tmp_path):
        """Known contacts go to Mode A directly — no evaluation call."""
        known_url = "http://known-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="NO")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
            providers=[mock_provider],
        )

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Help me",
                    sender_url=known_url,
                ),
            )
            data = await resp.json()
            assert data["status"] == "INPUT_REQUIRED"
            # Provider should not be called — known tier skips evaluation
            mock_provider.complete.assert_not_called()

    async def test_escalation_queues_pending_message(self, tmp_path):
        """When confidence fails, the message is queued in pending_queue."""
        trusted_url = "http://trusted-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="NO")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            providers=[mock_provider],
        )
        mock_session = _make_mock_session("Bad answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="Important question",
                        sender_url=trusted_url,
                        sender_name="Trusted Agent",
                    ),
                )
                data = await resp.json()
                assert data["status"] == "INPUT_REQUIRED"

        # Verify the message was queued
        messages = registry.pending_queue.get_pending_messages()
        assert len(messages) == 1
        assert messages[0]["sender_url"] == trusted_url
        assert messages[0]["sender_name"] == "Trusted Agent"

    async def test_confidence_skipped_when_no_providers(self, tmp_path):
        """No providers available -> skip evaluation, complete normally."""
        trusted_url = "http://trusted-agent:9000"

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            providers=[],  # No providers
        )
        mock_session = _make_mock_session("Autonomous answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="What is the answer?",
                        sender_url=trusted_url,
                    ),
                )
                data = await resp.json()
                assert data["status"] == "COMPLETED"


class TestAttributionAndEscalatedFlag:
    """Task 5: Mode C sets attribution='autonomous' and escalation sets escalated=True."""

    async def test_mode_c_sets_autonomous_attribution(self, tmp_path):
        """Mode C success → task has attribution: 'autonomous'."""
        trusted_url = "http://trusted-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="YES")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            providers=[mock_provider],
        )
        mock_session = _make_mock_session("Autonomous answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="What is the answer?",
                        sender_url=trusted_url,
                    ),
                )
                data = await resp.json()
                assert data["status"] == "COMPLETED"
                task = registry.get_task(data["id"])
                assert task["attribution"] == "autonomous"

    async def test_escalation_sets_escalated_flag_on_pending(self, tmp_path):
        """C→A escalation → pending entry has escalated: True."""
        trusted_url = "http://trusted-agent:9000"
        mock_provider = MagicMock()
        mock_provider.complete = AsyncMock(return_value="NO")

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
            providers=[mock_provider],
        )
        mock_session = _make_mock_session("Bad answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="Important question",
                        sender_url=trusted_url,
                        sender_name="Trusted Agent",
                    ),
                )
                data = await resp.json()
                assert data["status"] == "INPUT_REQUIRED"

        # Verify the pending entry has escalated=True
        messages = registry.pending_queue.get_pending_messages()
        assert len(messages) == 1
        assert messages[0]["escalated"] is True

    async def test_mode_a_no_escalation_no_escalated_flag(self, tmp_path):
        """Direct Mode A (known contact) → pending entry has escalated: False."""
        known_url = "http://known-agent:9000"

        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Help me",
                    sender_url=known_url,
                    sender_name="Known Agent",
                ),
            )
            data = await resp.json()
            assert data["status"] == "INPUT_REQUIRED"

        # Verify the pending entry has escalated=False (direct Mode A, not escalated)
        messages = registry.pending_queue.get_pending_messages()
        assert len(messages) == 1
        assert messages[0]["escalated"] is False
