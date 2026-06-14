"""Tests for mDNS service advertisement via Zeroconf."""

from unittest.mock import MagicMock, patch


class TestAdvertiseMdns:
    async def test_advertise_returns_handle_when_zeroconf_available(self):
        """advertise_mdns returns a (Zeroconf, ServiceInfo) tuple when zeroconf works."""
        mock_zc = MagicMock()
        mock_info_cls = MagicMock()

        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.Zeroconf",
                return_value=mock_zc,
            ),
            patch(
                "amplifier_module_hooks_a2a_server.discovery.ServiceInfo",
                mock_info_cls,
            ),
            patch(
                "amplifier_module_hooks_a2a_server.discovery.ZEROCONF_AVAILABLE",
                True,
            ),
        ):
            from amplifier_module_hooks_a2a_server.discovery import advertise_mdns

            handle = await advertise_mdns("TestAgent", 9000, "http://localhost:9000")

        assert handle is not None
        zc, info = handle
        assert zc is mock_zc
        mock_zc.register_service.assert_called_once()

    async def test_advertise_returns_none_when_zeroconf_unavailable(self):
        """advertise_mdns returns None when zeroconf is not installed."""
        with patch(
            "amplifier_module_hooks_a2a_server.discovery.ZEROCONF_AVAILABLE",
            False,
        ):
            from amplifier_module_hooks_a2a_server.discovery import advertise_mdns

            result = await advertise_mdns("TestAgent", 9000, "http://localhost:9000")

        assert result is None

    async def test_advertise_catches_exceptions(self):
        """advertise_mdns returns None (doesn't crash) if Zeroconf() raises."""
        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.Zeroconf",
                side_effect=OSError("network unavailable"),
            ),
            patch(
                "amplifier_module_hooks_a2a_server.discovery.ZEROCONF_AVAILABLE",
                True,
            ),
        ):
            from amplifier_module_hooks_a2a_server.discovery import advertise_mdns

            result = await advertise_mdns("TestAgent", 9000, "http://localhost:9000")

        assert result is None


class TestUnadvertiseMdns:
    async def test_unadvertise_with_none_is_noop(self):
        """unadvertise_mdns(None) should not raise."""
        from amplifier_module_hooks_a2a_server.discovery import unadvertise_mdns

        await unadvertise_mdns(None)  # should not raise

    async def test_unadvertise_cleans_up_service(self):
        """unadvertise_mdns calls unregister_service and close on the handle."""
        mock_zc = MagicMock()
        mock_info = MagicMock()

        from amplifier_module_hooks_a2a_server.discovery import unadvertise_mdns

        await unadvertise_mdns((mock_zc, mock_info))

        mock_zc.unregister_service.assert_called_once_with(mock_info)
        mock_zc.close.assert_called_once()


class TestMountMdnsIntegration:
    async def test_mount_calls_advertise_and_cleanup_unadvertises(self):
        """mount() advertises mDNS on start, and cleanup unadvertises."""
        from amplifier_module_hooks_a2a_server import mount

        coordinator = MagicMock()
        coordinator.parent_id = None
        coordinator.session_id = "test-session"
        coordinator.config = {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            "providers": [],
            "tools": [],
        }
        coordinator.register_capability = MagicMock()
        coordinator.register_cleanup = MagicMock()

        mock_handle = (MagicMock(), MagicMock())

        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.advertise_mdns",
                return_value=mock_handle,
            ) as mock_advertise,
            patch(
                "amplifier_module_hooks_a2a_server.discovery.unadvertise_mdns",
            ) as mock_unadvertise,
        ):
            config = {
                "enabled": True,
                "port": 0,
                "host": "127.0.0.1",
                "agent_name": "mDNS Test Agent",
                "discovery": {"mdns": True},
            }
            await mount(coordinator, config)

            # advertise was called
            mock_advertise.assert_called_once()
            call_kwargs = mock_advertise.call_args
            assert call_kwargs[1]["agent_name"] == "mDNS Test Agent"

            # cleanup was registered
            coordinator.register_cleanup.assert_called_once()
            cleanup_fn = coordinator.register_cleanup.call_args[0][0]

            # calling cleanup should unadvertise
            await cleanup_fn()
            mock_unadvertise.assert_called_once_with(mock_handle)

    async def test_mount_skips_mdns_when_disabled(self):
        """mount() does not advertise mDNS when discovery.mdns is False."""
        from amplifier_module_hooks_a2a_server import mount

        coordinator = MagicMock()
        coordinator.parent_id = None
        coordinator.session_id = "test-session"
        coordinator.config = {
            "session": {"orchestrator": "loop-basic", "context": "context-simple"},
            "providers": [],
            "tools": [],
        }
        coordinator.register_capability = MagicMock()
        coordinator.register_cleanup = MagicMock()

        with patch(
            "amplifier_module_hooks_a2a_server.discovery.advertise_mdns",
        ) as mock_advertise:
            config = {
                "enabled": True,
                "port": 0,
                "host": "127.0.0.1",
                "agent_name": "No mDNS Agent",
                "discovery": {"mdns": False},
            }
            await mount(coordinator, config)

            mock_advertise.assert_not_called()

            # Cleanup still registered (for server stop), call it
            coordinator.register_cleanup.assert_called_once()
            cleanup_fn = coordinator.register_cleanup.call_args[0][0]
            await cleanup_fn()
