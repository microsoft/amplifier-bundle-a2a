"""Tests for Mode A — known contacts get queued for user response."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient as AioTestClient, TestServer as AioTestServer

from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.pending import PendingQueue
from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.card import build_agent_card


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.parent_id = None
    coordinator.config = {
        "session": {
            "orchestrator": "loop-basic",
            "context": "context-simple",
        },
        "providers": [{"module": "provider-test", "config": {"model": "test-model"}}],
        "tools": [{"module": "tool-filesystem"}],
        "hooks": [{"module": "hooks-a2a-server", "config": {"port": 8222}}],
    }
    return coordinator


def _make_mock_session(response_text="The answer is 42"):
    """Create a mock AmplifierSession that returns a canned response."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=response_text)
    mock_session.initialize = AsyncMock()
    mock_session.cleanup = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _make_server_with_contacts(tmp_path, contacts=None):
    """Create a server with ContactStore and PendingQueue wired up.

    contacts: list of dicts with url, name, tier keys.
    """
    config = {
        "port": 0,
        "agent_name": "Test Agent",
        "agent_description": "Test",
        "authentication": {"required": False},
    }
    registry = A2ARegistry()
    registry.contact_store = ContactStore(path=tmp_path / "contacts.json")
    registry.pending_queue = PendingQueue(base_dir=tmp_path)

    # Pre-populate contacts synchronously via internals
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
    coordinator = _make_mock_coordinator()
    from amplifier_module_hooks_a2a_server.server import A2AServer

    return A2AServer(registry, card, coordinator, config), registry


def _msg_payload(text="Hello", sender_url="http://remote-agent:9000", sender_name=None):
    """Build a request body for /a2a/v1/message:send."""
    body = {
        "message": {
            "role": "user",
            "parts": [{"text": text}],
        },
        "sender_url": sender_url,
    }
    if sender_name is not None:
        body["sender_name"] = sender_name
    return body


class TestModeAKnownContacts:
    """Mode A: known-tier contacts get queued, return INPUT_REQUIRED."""

    async def test_known_contact_gets_mode_a(self, tmp_path):
        """Known tier contact → task created with INPUT_REQUIRED status."""
        known_url = "http://known-agent:9000"
        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Please help me",
                    sender_url=known_url,
                    sender_name="Known Agent",
                ),
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "INPUT_REQUIRED"

    async def test_trusted_contact_still_gets_mode_c(self, tmp_path):
        """Trusted tier contact → Mode C (COMPLETED with artifacts) — regression test."""
        trusted_url = "http://trusted-agent:9000"
        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": trusted_url, "name": "Trusted Agent", "tier": "trusted"}],
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

    async def test_mode_a_queues_pending_message(self, tmp_path):
        """After Mode A, pending_queue has the message entry with correct fields."""
        known_url = "http://known-agent:9000"
        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Help me out",
                    sender_url=known_url,
                    sender_name="Known Agent",
                ),
            )
            data = await resp.json()
            task_id = data["id"]

        assert registry.pending_queue is not None
        messages = registry.pending_queue.get_pending_messages()
        assert len(messages) == 1
        assert messages[0]["task_id"] == task_id
        assert messages[0]["sender_url"] == known_url
        assert messages[0]["sender_name"] == "Known Agent"
        assert messages[0]["status"] == "pending"

    async def test_mode_a_returns_task_with_id(self, tmp_path):
        """The INPUT_REQUIRED response includes a task ID that can be polled later."""
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
                ),
            )
            data = await resp.json()

        assert "id" in data
        assert isinstance(data["id"], str)
        assert len(data["id"]) > 0

    async def test_mode_a_task_pollable(self, tmp_path):
        """After Mode A, GET /a2a/v1/tasks/{task_id} returns the INPUT_REQUIRED task."""
        known_url = "http://known-agent:9000"
        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        async with AioTestClient(AioTestServer(server.app)) as client:
            # Send message — gets Mode A
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Help me",
                    sender_url=known_url,
                ),
            )
            data = await resp.json()
            task_id = data["id"]

            # Poll the task
            poll_resp = await client.get(f"/a2a/v1/tasks/{task_id}")
            assert poll_resp.status == 200
            poll_data = await poll_resp.json()
            assert poll_data["id"] == task_id
            assert poll_data["status"] == "INPUT_REQUIRED"

    async def test_mode_a_includes_message_history(self, tmp_path):
        """The INPUT_REQUIRED task includes the original message in history."""
        known_url = "http://known-agent:9000"
        server, registry = _make_server_with_contacts(
            tmp_path,
            contacts=[{"url": known_url, "name": "Known Agent", "tier": "known"}],
        )

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Important question",
                    sender_url=known_url,
                ),
            )
            data = await resp.json()

        assert "history" in data
        assert len(data["history"]) >= 1
        assert data["history"][0]["parts"][0]["text"] == "Important question"
