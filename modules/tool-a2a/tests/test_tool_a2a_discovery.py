"""Tests for mDNS browsing and merged agent discovery."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_coordinator(registry=None):
    """Create a mock coordinator that returns the given registry."""
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=registry)
    coordinator.mount = AsyncMock()
    coordinator.register_cleanup = MagicMock()
    return coordinator


def _make_mock_registry(agents=None):
    """Create a mock registry with the given agents."""
    registry = MagicMock()
    registry.get_agents = MagicMock(return_value=agents or [])
    registry.resolve_agent_url = MagicMock(return_value=None)
    registry.get_cached_card = MagicMock(return_value=None)
    registry.cache_card = MagicMock()
    registry.get_discovered_agents = MagicMock(return_value=[])
    registry.cache_discovered_agents = MagicMock()
    registry.contact_store = None
    registry.pending_queue = None
    return registry


# ---------------------------------------------------------------------------
# browse_mdns tests
# ---------------------------------------------------------------------------


class TestBrowseMdnsAsyncApi:
    """TDD tests: browse_mdns must use AsyncZeroconf to avoid EventLoopBlocked.

    Confirmed root cause: Zeroconf().close() (and similar sync calls) raise
    EventLoopBlocked when called from inside a running asyncio event loop.
    Fix: switch to AsyncZeroconf + AsyncServiceBrowser + await async_close().

    These tests are RED before the fix and GREEN after.
    """

    @pytest.mark.asyncio
    async def test_browse_uses_async_zeroconf_and_awaits_close(self):
        """browse_mdns must use AsyncZeroconf and await async_close.

        TDD: FAILS before fix — code uses Zeroconf() (sync) so AsyncZeroconf
        is never instantiated and async_close is never awaited.
        PASSES after fix — code uses AsyncZeroconf and awaits async_close.
        """
        from amplifier_module_tool_a2a.discovery import browse_mdns

        mock_aiozc = AsyncMock()

        with (
            patch(
                "amplifier_module_tool_a2a.discovery.AsyncZeroconf",
                return_value=mock_aiozc,
                create=True,  # name may not exist pre-fix
            ),
            patch(
                "amplifier_module_tool_a2a.discovery.AsyncServiceBrowser",
                MagicMock(),
                create=True,  # name may not exist pre-fix
            ),
            patch(
                "amplifier_module_tool_a2a.discovery.ZEROCONF_AVAILABLE",
                True,
            ),
        ):
            result = await browse_mdns(timeout=0.01)

        mock_aiozc.async_close.assert_awaited_once()
        assert isinstance(result, list)


class TestBrowseMdns:
    """Tests for the browse_mdns function in discovery.py."""

    @pytest.mark.asyncio
    async def test_browse_returns_empty_when_no_services(self):
        """Mock AsyncZeroconf with no services found -> empty list."""
        from amplifier_module_tool_a2a.discovery import browse_mdns

        mock_aiozc = AsyncMock()

        with (
            patch(
                "amplifier_module_tool_a2a.discovery.AsyncZeroconf",
                return_value=mock_aiozc,
            ),
            patch("amplifier_module_tool_a2a.discovery.AsyncServiceBrowser"),
            patch("amplifier_module_tool_a2a.discovery.ZEROCONF_AVAILABLE", True),
        ):
            result = await browse_mdns(timeout=0.01)
            assert result == []

    @pytest.mark.asyncio
    async def test_browse_returns_empty_when_zeroconf_unavailable(self):
        """Patch ZEROCONF_AVAILABLE=False -> empty list."""
        from amplifier_module_tool_a2a.discovery import browse_mdns

        with patch("amplifier_module_tool_a2a.discovery.ZEROCONF_AVAILABLE", False):
            result = await browse_mdns(timeout=0.01)
            assert result == []

    @pytest.mark.asyncio
    async def test_browse_catches_exceptions(self):
        """Mock AsyncZeroconf to raise -> empty list, no crash."""
        from amplifier_module_tool_a2a.discovery import browse_mdns

        with (
            patch(
                "amplifier_module_tool_a2a.discovery.AsyncZeroconf",
                side_effect=RuntimeError("network down"),
            ),
            patch("amplifier_module_tool_a2a.discovery.ZEROCONF_AVAILABLE", True),
        ):
            result = await browse_mdns(timeout=0.01)
            assert result == []


# ---------------------------------------------------------------------------
# discover operation tests
# ---------------------------------------------------------------------------


class TestDiscoverOperation:
    """Tests for the 'discover' tool operation."""

    @pytest.mark.asyncio
    async def test_discover_operation_returns_agents(self):
        """Mock browse_mdns to return agents -> ToolResult with agent list."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        fake_agents = [
            {"name": "Alice", "url": "http://alice:8222", "source": "mdns"},
            {"name": "Bob", "url": "http://bob:8222", "source": "mdns"},
        ]
        with patch(
            "amplifier_module_tool_a2a.discovery.browse_mdns",
            new_callable=AsyncMock,
            return_value=fake_agents,
        ):
            result = await tool.execute({"operation": "discover"})
            assert result.success is True
            assert result.output == fake_agents

    @pytest.mark.asyncio
    async def test_discover_operation_empty(self):
        """Mock browse_mdns to return [] -> 'No agents discovered' message."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        with patch(
            "amplifier_module_tool_a2a.discovery.browse_mdns",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await tool.execute({"operation": "discover"})
            assert result.success is True
            assert "No agents discovered" in str(result.output)


# ---------------------------------------------------------------------------
# Merged agents operation tests
# ---------------------------------------------------------------------------


class TestAgentsMerging:
    """Tests for the updated 'agents' operation merging config + mdns + contacts."""

    @pytest.mark.asyncio
    async def test_agents_merges_config_and_discovered(self):
        """Config has agent A, discovered has agent B -> both appear with correct source."""
        from amplifier_module_tool_a2a import A2ATool

        config_agents = [{"name": "Alice", "url": "http://alice:8222"}]
        discovered_agents = [
            {"name": "Bob", "url": "http://bob:8222", "source": "mdns"},
        ]

        registry = _make_mock_registry(agents=config_agents)
        registry.get_discovered_agents = MagicMock(return_value=discovered_agents)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is True

        agents = result.output
        assert agents is not None
        assert len(agents) == 2

        alice = next(a for a in agents if a["name"] == "Alice")
        bob = next(a for a in agents if a["name"] == "Bob")
        assert alice["source"] == "config"
        assert bob["source"] == "mdns"

    @pytest.mark.asyncio
    async def test_agents_deduplicates_by_url(self):
        """Same URL in config and discovered -> single entry with source='both'."""
        from amplifier_module_tool_a2a import A2ATool

        config_agents = [{"name": "Alice", "url": "http://alice:8222"}]
        discovered_agents = [
            {"name": "Alice-LAN", "url": "http://alice:8222", "source": "mdns"},
        ]

        registry = _make_mock_registry(agents=config_agents)
        registry.get_discovered_agents = MagicMock(return_value=discovered_agents)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is True

        agents = result.output
        assert agents is not None
        assert len(agents) == 1
        assert agents[0]["url"] == "http://alice:8222"
        assert agents[0]["source"] == "both"

    @pytest.mark.asyncio
    async def test_agents_includes_contacts(self):
        """Contact in contact_store -> appears with source='contact'."""
        from amplifier_module_tool_a2a import A2ATool

        config_agents = [{"name": "Alice", "url": "http://alice:8222"}]
        registry = _make_mock_registry(agents=config_agents)

        # Set up a mock contact_store
        contact_store = MagicMock()
        contact_store.list_contacts = MagicMock(
            return_value=[
                {"name": "Charlie", "url": "http://charlie:8222", "tier": "known"},
            ]
        )
        registry.contact_store = contact_store

        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is True

        agents = result.output
        assert agents is not None
        assert len(agents) == 2

        charlie = next(a for a in agents if a["name"] == "Charlie")
        assert charlie["source"] == "contact"
        assert charlie["url"] == "http://charlie:8222"
