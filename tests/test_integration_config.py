"""Integration tests — config improvement flows on localhost.

Tests the config improvements across multiple components:
- whoami returns correct identity from a running server
- add_contact fetches card from a real remote server
- Sender identity is derived from registry.card in real sends
- Smart default agent name from OS username

The only mocks are AmplifierSession (to avoid needing real LLM providers).
All HTTP communication is real, over localhost.
"""

import getpass
from unittest.mock import AsyncMock, MagicMock

from amplifier_module_hooks_a2a_server.card import build_agent_card
from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.pending import PendingQueue
from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.server import A2AServer
from amplifier_module_tool_a2a import A2ATool


# --- Shared helpers ---


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "integration-config"
    coordinator.parent_id = None
    coordinator.config = {
        "session": {
            "orchestrator": "loop-basic",
            "context": "context-simple",
        },
        "providers": [{"module": "provider-test", "config": {"model": "test"}}],
        "tools": [],
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


# --- Test classes ---


class TestWhoamiFromRunningServer:
    """whoami returns correct identity from a real running server."""

    async def test_whoami_returns_server_url_and_name(self):
        """Start real server, call whoami, verify URL matches actual port."""
        server_config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Whoami Test Agent",
        }
        registry = A2ARegistry()
        card = build_agent_card(server_config)
        coordinator = _make_mock_coordinator()
        server = A2AServer(registry, card, coordinator, server_config)

        await server.start()
        try:
            # Update card URL with actual port (simulating what mount() does
            # when it builds the card and stores it on the registry)
            actual_url = f"http://127.0.0.1:{server.port}"
            card["url"] = actual_url
            registry.card = card

            # Create tool with the server's registry
            tool_coordinator = MagicMock()
            tool_coordinator.get_capability = MagicMock(return_value=registry)
            tool = A2ATool(tool_coordinator, {})

            result = await tool.execute({"operation": "whoami"})

            assert result.success is True
            assert result.output["name"] == "Whoami Test Agent"
            assert result.output["url"] == actual_url
            assert str(server.port) in result.output["url"]
            assert result.output["server_running"] is True
        finally:
            await server.stop()


class TestAddContactFromRunningServer:
    """add_contact fetches card from a real remote server and adds to contacts."""

    async def test_add_contact_fetches_remote_card(self, tmp_path):
        """Start a remote server, add_contact by URL, verify name from card."""
        # Start a "remote" server
        remote_config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Remote Agent",
        }
        remote_registry = A2ARegistry()
        remote_card = build_agent_card(remote_config)
        remote_coordinator = _make_mock_coordinator()
        remote_server = A2AServer(
            remote_registry, remote_card, remote_coordinator, remote_config
        )

        await remote_server.start()
        try:
            remote_url = f"http://127.0.0.1:{remote_server.port}"

            # Create a SEPARATE registry (simulating a different session)
            local_registry = A2ARegistry()
            local_registry.contact_store = ContactStore(path=tmp_path / "contacts.json")
            local_registry.pending_queue = PendingQueue(base_dir=tmp_path)
            local_registry.card = {
                "name": "Local Agent",
                "url": "http://127.0.0.1:9999",
            }

            tool_coordinator = MagicMock()
            tool_coordinator.get_capability = MagicMock(return_value=local_registry)
            tool = A2ATool(tool_coordinator, {"default_timeout": 10.0})

            try:
                result = await tool.execute(
                    {"operation": "add_contact", "url": remote_url}
                )

                # Verify tool response
                assert result.success is True
                assert result.output["name"] == "Remote Agent"
                assert result.output["url"] == remote_url

                # Verify contact was persisted in the local contact_store
                contact = local_registry.contact_store.get_contact(remote_url)
                assert contact is not None
                assert contact["name"] == "Remote Agent"
                assert contact["tier"] == "known"
            finally:
                await tool.client.close()
        finally:
            await remote_server.stop()


class TestSenderIdentityFromRegistryCard:
    """Sender identity is derived from registry.card, not tool config."""

    async def test_sender_identity_in_real_send(self, tmp_path):
        """Two servers: Bob sends to Alice, sender info comes from registry.card."""
        # --- Start Alice's server (with contact_store for sender verification) ---
        alice_config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Alice Agent",
        }
        alice_registry = A2ARegistry()
        alice_registry.contact_store = ContactStore(
            path=tmp_path / "alice_contacts.json"
        )
        alice_registry.pending_queue = PendingQueue(base_dir=tmp_path / "alice_pending")
        alice_card = build_agent_card(alice_config)
        alice_coordinator = _make_mock_coordinator()
        alice_server = A2AServer(
            alice_registry, alice_card, alice_coordinator, alice_config
        )

        # --- Start Bob's server ---
        bob_config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Bob Agent",
        }
        bob_registry = A2ARegistry()
        bob_card = build_agent_card(bob_config)
        bob_coordinator = _make_mock_coordinator()
        bob_server = A2AServer(bob_registry, bob_card, bob_coordinator, bob_config)

        await alice_server.start()
        await bob_server.start()
        try:
            alice_url = f"http://127.0.0.1:{alice_server.port}"
            bob_url = f"http://127.0.0.1:{bob_server.port}"

            # Set Bob's registry.card with his actual URL (single source of truth)
            bob_card["url"] = bob_url
            bob_card["name"] = "Bob Agent"
            bob_registry.card = bob_card

            # Add Bob as a "known" contact on Alice's side.
            # Known tier → Mode A → message queued with sender info we can inspect.
            await alice_registry.contact_store.add_contact(
                bob_url, "Bob Agent", "known"
            )

            # Create tool for Bob — NO sender_url/sender_name in tool config!
            # Identity must come from registry.card alone.
            tool_coordinator = MagicMock()
            tool_coordinator.get_capability = MagicMock(return_value=bob_registry)
            tool = A2ATool(tool_coordinator, {"default_timeout": 10.0})

            try:
                result = await tool.execute(
                    {
                        "operation": "send",
                        "agent": alice_url,
                        "message": "Hello Alice, this is Bob!",
                    }
                )
                assert result.success is True

                # Verify Alice received the message with Bob's sender identity
                # derived from Bob's registry.card (not from tool config)
                messages = alice_registry.pending_queue.get_pending_messages()
                assert len(messages) == 1
                assert messages[0]["sender_url"] == bob_url  # From registry.card
                assert messages[0]["sender_name"] == "Bob Agent"  # From registry.card
            finally:
                await tool.client.close()
        finally:
            await bob_server.stop()
            await alice_server.stop()


class TestSmartDefaultAgentName:
    """Smart default agent name derived from OS username."""

    async def test_default_name_contains_os_username(self):
        """Card with no agent_name in config uses OS username."""
        config = {
            "port": 0,
            "host": "127.0.0.1",
            # No agent_name — should use OS username
        }
        card = build_agent_card(config)
        username = getpass.getuser()
        assert username in card["name"]
        assert card["name"] == f"{username}'s Agent"
