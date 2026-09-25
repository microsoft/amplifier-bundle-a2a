"""Tests for per-contact capability scoping — tool filtering by trust tier."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient as AioTestClient, TestServer as AioTestServer

from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.card import build_agent_card


# --- Helpers ---

_PARENT_TOOLS = [
    {"module": "tool-filesystem"},
    {"module": "tool-bash"},
    {"module": "tool-search"},
    {"module": "tool-web"},
]


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.parent_id = None
    coordinator.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        "providers": [{"module": "provider-test"}],
        "tools": list(_PARENT_TOOLS),
        "hooks": [{"module": "hooks-a2a-server", "config": {"port": 8222}}],
    }
    return coordinator


def _make_mock_session(response_text="OK"):
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=response_text)
    mock_session.initialize = AsyncMock()
    mock_session.cleanup = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _make_server(config=None, coordinator=None):
    config = config or {
        "port": 0,
        "agent_name": "Test Agent",
        "agent_description": "Test",
        "authentication": {"required": False},
    }
    registry = A2ARegistry()
    card = build_agent_card(config)
    coordinator = coordinator or _make_mock_coordinator()
    from amplifier_module_hooks_a2a_server.server import A2AServer

    return A2AServer(registry, card, coordinator, config), registry


def _make_server_with_contact(tmp_path, sender_url, tier="known", config=None):
    """Create a server with a ContactStore containing one contact at the given tier."""
    config = config or {
        "port": 0,
        "agent_name": "Test Agent",
        "agent_description": "Test",
        "authentication": {"required": False},
    }
    registry = A2ARegistry()
    registry.contact_store = ContactStore(path=tmp_path / "contacts.json")
    registry.contact_store._contacts.append(
        {
            "url": sender_url,
            "name": "Test Contact",
            "tier": tier,
            "first_seen": "2025-01-01T00:00:00+00:00",
            "last_seen": "2025-01-01T00:00:00+00:00",
        }
    )
    card = build_agent_card(config)
    coordinator = _make_mock_coordinator()
    from amplifier_module_hooks_a2a_server.server import A2AServer

    return A2AServer(registry, card, coordinator, config), registry


def _msg_payload(text="Hello", sender_url="http://remote:9000"):
    return {
        "message": {"role": "user", "parts": [{"text": text}]},
        "sender_url": sender_url,
    }


# --- Tests ---


class TestTrustedTierGetsNoToolsByDefault:
    async def test_trusted_tier_gets_no_tools_by_default(self):
        """Trusted contacts receive no tools without an explicit grant."""
        mock_session = _make_mock_session()
        server, _ = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            await server._spawn_child_session("task-1", "Hello", tier="trusted")

            call_kwargs = mock_cls.call_args[1]
            child_tools = call_kwargs["config"]["tools"]
            assert child_tools == []


class TestKnownTierGetsFilteredTools:
    async def test_known_tier_gets_filtered_tools(self):
        """Known contact → child config only has whitelisted tools."""
        mock_session = _make_mock_session()
        server, _ = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            await server._spawn_child_session("task-2", "Hello", tier="known")

            call_kwargs = mock_cls.call_args[1]
            child_tools = call_kwargs["config"]["tools"]
            assert child_tools == []


class TestKnownTierDefaultWhitelist:
    async def test_known_tier_default_whitelist(self):
        """Known contacts receive no tools by default."""
        mock_session = _make_mock_session()
        server, _ = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            await server._spawn_child_session("task-3", "Hello", tier="known")

            call_kwargs = mock_cls.call_args[1]
            child_tools = call_kwargs["config"]["tools"]
            assert child_tools == []


class TestCustomWhitelistFromConfig:
    async def test_custom_whitelist_from_config(self):
        """Custom trust_tiers config overrides default whitelist."""
        mock_session = _make_mock_session()
        config = {
            "port": 0,
            "agent_name": "Test",
            "trust_tiers": {
                "known": {"tools": ["tool-bash", "tool-web"]},
            },
        }
        server, _ = _make_server(config=config)

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            await server._spawn_child_session("task-4", "Hello", tier="known")

            call_kwargs = mock_cls.call_args[1]
            child_tools = call_kwargs["config"]["tools"]
            tool_modules = [t["module"] for t in child_tools]
            assert tool_modules == ["tool-bash", "tool-web"]


class TestWildcardToolsConfig:
    async def test_wildcard_tools_config(self):
        """tools: "*" in tier config → all tools passed through."""
        mock_session = _make_mock_session()
        config = {
            "port": 0,
            "agent_name": "Test",
            "trust_tiers": {
                "known": {"tools": "*"},
            },
            "allow_unsafe_wildcard_tools": True,
        }
        server, _ = _make_server(config=config)

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            await server._spawn_child_session("task-5", "Hello", tier="known")

            call_kwargs = mock_cls.call_args[1]
            child_tools = call_kwargs["config"]["tools"]
            assert child_tools == _PARENT_TOOLS


class TestEmptyWhitelistMeansNoTools:
    async def test_empty_whitelist_means_no_tools(self):
        """Empty whitelist → child gets no tools."""
        mock_session = _make_mock_session()
        config = {
            "port": 0,
            "agent_name": "Test",
            "trust_tiers": {
                "known": {"tools": []},
            },
        }
        server, _ = _make_server(config=config)

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            await server._spawn_child_session("task-6", "Hello", tier="known")

            call_kwargs = mock_cls.call_args[1]
            child_tools = call_kwargs["config"]["tools"]
            assert child_tools == []


class TestHandleSendMessagePassesTier:
    async def test_known_contact_gets_mode_a(self, tmp_path):
        """Known contact → Mode A (INPUT_REQUIRED), no child session spawned."""
        sender_url = "http://known-agent:9000"
        mock_session = _make_mock_session("response")
        server, registry = _make_server_with_contact(tmp_path, sender_url, tier="known")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(text="Hi", sender_url=sender_url),
                )
                assert resp.status == 200
                data = await resp.json()
                # Known tier → Mode A (queued for user), not Mode C
                assert data["status"] == "INPUT_REQUIRED"
                # No child session should have been spawned
                mock_cls.assert_not_called()

    async def test_trusted_contact_sends_trusted_tier(self, tmp_path):
        """handle_send_message passes 'trusted' tier for trusted contacts."""
        sender_url = "http://trusted-agent:9000"
        mock_session = _make_mock_session("response")
        server, registry = _make_server_with_contact(
            tmp_path, sender_url, tier="trusted"
        )

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json=_msg_payload(text="Hi", sender_url=sender_url),
                )
                assert resp.status == 200

                call_kwargs = mock_cls.call_args[1]
                child_tools = call_kwargs["config"]["tools"]
                # trusted tier → no tools without an explicit grant
                assert child_tools == []
