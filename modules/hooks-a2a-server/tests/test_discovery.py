"""Tests for mDNS service advertisement via Zeroconf."""

from unittest.mock import AsyncMock, MagicMock, patch


class TestAdvertiseMdns:
    async def test_advertise_returns_handle_when_zeroconf_available(self):
        """advertise_mdns returns a (AsyncZeroconf, ServiceInfo) tuple when zeroconf works."""
        mock_aiozc = AsyncMock()
        mock_info_cls = MagicMock()

        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.AsyncZeroconf",
                return_value=mock_aiozc,
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
        aiozc, info = handle
        assert aiozc is mock_aiozc
        mock_aiozc.async_register_service.assert_awaited_once()

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
        """advertise_mdns returns None (doesn't crash) if AsyncZeroconf() raises."""
        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.AsyncZeroconf",
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

    async def test_advertise_logs_repr_on_no_arg_exception(self, caplog):
        """When mDNS registration fails with a no-arg exception, the log must be diagnosable.

        TDD: FAILS before fix — `%s` of `OSError()` produces an empty string,
        so the logged message is "mDNS advertisement failed: " with NO diagnostic info.
        PASSES after fix — `%r` of `OSError()` produces "OSError()", giving the
        exception type even when the exception carries no message.

        This is the exact failure mode observed in production (session log showed
        "mDNS advertisement failed:" with nothing after the colon), making it
        impossible to diagnose why LAN discovery was silently broken.
        """
        import logging

        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.AsyncZeroconf",
                side_effect=OSError(),  # no-arg: str(OSError()) == "" but repr != ""
            ),
            patch(
                "amplifier_module_hooks_a2a_server.discovery.ZEROCONF_AVAILABLE",
                True,
            ),
            caplog.at_level(logging.WARNING),
        ):
            from amplifier_module_hooks_a2a_server.discovery import advertise_mdns

            result = await advertise_mdns("TestAgent", 9000, "http://localhost:9000")

        assert result is None

        # The log must contain at least the exception TYPE, not just an empty string.
        # Before fix: message ends with "failed: " (empty — %s of no-arg OSError).
        # After fix:  message ends with "failed: OSError()" (%r shows type + args).
        warning_messages = [
            r.message for r in caplog.records if r.levelno == logging.WARNING
        ]
        assert warning_messages, "Expected a WARNING log from advertise_mdns"
        combined = " ".join(warning_messages)
        assert "OSError" in combined, (
            f"mDNS failure log must contain the exception type for diagnosability. "
            f"Got: {warning_messages!r}. "
            f"Fix: change '%s' to '%r' in discovery.py advertise_mdns exception handler."
        )


class TestUnadvertiseMdns:
    async def test_unadvertise_with_none_is_noop(self):
        """unadvertise_mdns(None) should not raise."""
        from amplifier_module_hooks_a2a_server.discovery import unadvertise_mdns

        await unadvertise_mdns(None)  # should not raise

    async def test_unadvertise_cleans_up_service(self):
        """unadvertise_mdns awaits async_unregister_service and async_close on the handle."""
        mock_aiozc = AsyncMock()
        mock_info = MagicMock()

        from amplifier_module_hooks_a2a_server.discovery import unadvertise_mdns

        await unadvertise_mdns((mock_aiozc, mock_info))

        mock_aiozc.async_unregister_service.assert_awaited_once_with(mock_info)
        mock_aiozc.async_close.assert_awaited_once()


class TestAdvertiseMdnsAsyncApi:
    """TDD tests: advertise_mdns must use AsyncZeroconf to avoid EventLoopBlocked.

    Confirmed root cause: the sync Zeroconf().register_service() call raises
    zeroconf._exceptions.EventLoopBlocked when awaited inside a running asyncio
    event loop (str(EventLoopBlocked()) == "" which is why logs showed "failed: ").
    Fix: switch to AsyncZeroconf / async_register_service.

    These tests are RED before the fix and GREEN after.
    """

    async def test_advertise_awaits_async_register_service(self):
        """advertise_mdns must call AsyncZeroconf and await async_register_service.

        TDD: FAILS before fix — code uses Zeroconf().register_service() (sync),
        which raises EventLoopBlocked inside a running asyncio event loop.
        PASSES after fix — code uses AsyncZeroconf().async_register_service() (async).
        """
        mock_aiozc = AsyncMock()

        with (
            patch(
                "amplifier_module_hooks_a2a_server.discovery.AsyncZeroconf",
                return_value=mock_aiozc,
                create=True,  # name may not exist pre-fix
            ),
            patch(
                "amplifier_module_hooks_a2a_server.discovery.ServiceInfo",
                MagicMock(),
            ),
            patch(
                "amplifier_module_hooks_a2a_server.discovery.ZEROCONF_AVAILABLE",
                True,
            ),
        ):
            from amplifier_module_hooks_a2a_server.discovery import advertise_mdns

            handle = await advertise_mdns("TestAgent", 9000, "http://localhost:9000")

        assert handle is not None, (
            "advertise_mdns must return a non-None handle on success"
        )
        mock_aiozc.async_register_service.assert_awaited_once()

    async def test_unadvertise_awaits_async_unregister_and_close(self):
        """unadvertise_mdns must await async_unregister_service and async_close.

        TDD: FAILS before fix — code calls zc.unregister_service() / zc.close() (sync),
        so async_unregister_service / async_close are never awaited.
        PASSES after fix — code awaits the async variants.
        """
        from amplifier_module_hooks_a2a_server.discovery import unadvertise_mdns

        mock_aiozc = AsyncMock()
        mock_info = MagicMock()

        await unadvertise_mdns((mock_aiozc, mock_info))

        mock_aiozc.async_unregister_service.assert_awaited_once_with(mock_info)
        mock_aiozc.async_close.assert_awaited_once()


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
