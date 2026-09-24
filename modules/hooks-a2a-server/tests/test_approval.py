"""Tests for first-contact approval — unknown senders get INPUT_REQUIRED."""

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


def _make_server_with_contacts(tmp_path, known_urls=None, trusted_urls=None):
    """Create a server with ContactStore and PendingQueue wired up."""
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
    if known_urls:
        for url, name in known_urls:
            registry.contact_store._contacts.append(
                {
                    "url": url,
                    "name": name,
                    "tier": "known",
                    "first_seen": "2025-01-01T00:00:00+00:00",
                    "last_seen": "2025-01-01T00:00:00+00:00",
                }
            )
    if trusted_urls:
        for url, name in trusted_urls:
            registry.contact_store._contacts.append(
                {
                    "url": url,
                    "name": name,
                    "tier": "trusted",
                    "first_seen": "2025-01-01T00:00:00+00:00",
                    "last_seen": "2025-01-01T00:00:00+00:00",
                }
            )

    card = build_agent_card(config)
    coordinator = _make_mock_coordinator()
    from amplifier_module_hooks_a2a_server.server import A2AServer

    return A2AServer(registry, card, coordinator, config), registry


def _make_server_no_contacts():
    """Create a server without ContactStore (Phase 1 compatibility)."""
    config = {
        "port": 0,
        "agent_name": "Test Agent",
        "agent_description": "Test",
        "authentication": {"required": False},
    }
    registry = A2ARegistry()
    # contact_store and pending_queue stay None
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


class TestFirstContactApproval:
    async def test_unknown_sender_gets_input_required(self, tmp_path):
        """Unknown sender → task created with INPUT_REQUIRED status."""
        server, registry = _make_server_with_contacts(tmp_path)

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Hi there",
                    sender_url="http://stranger:9000",
                ),
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "INPUT_REQUIRED"

    async def test_trusted_sender_proceeds_to_mode_c(self, tmp_path):
        """Trusted contact → Mode C (child session spawned, COMPLETED)."""
        known_url = "http://trusted-agent:9000"
        server, registry = _make_server_with_contacts(
            tmp_path, trusted_urls=[(known_url, "Trusted Agent")]
        )
        mock_session = _make_mock_session("The answer is 42")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(
                        text="What is the answer?",
                        sender_url=known_url,
                    ),
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "COMPLETED"
                assert data["artifacts"][0]["parts"][0]["text"] == "The answer is 42"

    async def test_sender_url_required_when_contacts_enabled(self, tmp_path):
        """Missing sender_url → 400 error when contact_store is set."""
        server, registry = _make_server_with_contacts(tmp_path)

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json={
                    "message": {
                        "role": "user",
                        "parts": [{"text": "Hello"}],
                    },
                    # No sender_url
                },
            )
            assert resp.status == 400
            data = await resp.json()
            assert "sender_url" in data["error"].lower()

    async def test_approval_queued_in_pending_queue(self, tmp_path):
        """After unknown sender request, pending_queue has the approval entry."""
        server, registry = _make_server_with_contacts(tmp_path)

        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Please help",
                    sender_url="http://unknown:9000",
                    sender_name="Unknown Bot",
                ),
            )
            data = await resp.json()
            task_id = data["id"]

        assert registry.pending_queue is not None
        approvals = registry.pending_queue.get_pending_approvals()
        assert len(approvals) == 1
        assert approvals[0]["task_id"] == task_id
        assert approvals[0]["sender_url"] == "http://unknown:9000"
        assert approvals[0]["sender_name"] == "Unknown Bot"

    async def test_sender_name_defaults_to_unknown_agent(self, tmp_path):
        """Missing sender_name → defaults to 'Unknown Agent'."""
        server, registry = _make_server_with_contacts(tmp_path)

        async with AioTestClient(AioTestServer(server.app)) as client:
            await client.post(
                "/a2a/v1/message:send",
                json=_msg_payload(
                    text="Hello",
                    sender_url="http://no-name:9000",
                    # No sender_name → should default
                ),
            )

        assert registry.pending_queue is not None
        approvals = registry.pending_queue.get_pending_approvals()
        assert len(approvals) == 1
        assert approvals[0]["sender_name"] == "Unknown Agent"

    async def test_no_contact_check_when_contact_store_is_none(self):
        """When contact_store is None (Phase 1 compat), proceed to Mode C regardless."""
        server, registry = _make_server_no_contacts()
        mock_session = _make_mock_session("Phase 1 response")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json={
                        "message": {
                            "role": "user",
                            "parts": [{"text": "No sender_url needed"}],
                        },
                        # No sender_url, no contact_store — should work fine
                    },
                )
                assert resp.status == 200
                data = await resp.json()
                assert data["status"] == "COMPLETED"
                assert data["artifacts"][0]["parts"][0]["text"] == "Phase 1 response"
