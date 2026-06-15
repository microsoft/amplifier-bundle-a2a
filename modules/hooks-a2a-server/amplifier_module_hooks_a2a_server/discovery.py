"""mDNS service advertisement via Zeroconf (async API to avoid EventLoopBlocked)."""

import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)

# Zeroconf is optional — graceful degradation if not installed
try:
    from zeroconf import ServiceInfo  # pyright: ignore[reportMissingImports]
    from zeroconf.asyncio import AsyncZeroconf  # pyright: ignore[reportMissingImports]

    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False
    logger.debug("zeroconf not installed — mDNS discovery disabled")


SERVICE_TYPE = "_a2a._tcp.local."


async def advertise_mdns(agent_name: str, port: int, base_url: str) -> Any | None:
    """Register an A2A service via mDNS/Zeroconf (async API, safe inside asyncio.run).

    Uses AsyncZeroconf.async_register_service so the call is awaitable and does
    not raise EventLoopBlocked when called from inside a running asyncio event loop.

    Returns the (AsyncZeroconf, ServiceInfo) tuple needed for cleanup,
    or None if zeroconf is unavailable or registration fails.
    """
    if not ZEROCONF_AVAILABLE:
        logger.info("mDNS advertisement skipped — zeroconf not installed")
        return None

    try:
        # Build service info
        service_name = f"{agent_name}.{SERVICE_TYPE}"

        # Get local hostname for the service
        hostname = socket.gethostname()

        properties = {
            "name": agent_name,
            "version": "1.0",
            "url": base_url,
        }

        info = ServiceInfo(  # type: ignore[reportPossiblyUnbound]
            type_=SERVICE_TYPE,
            name=service_name,
            port=port,
            properties=properties,
            server=f"{hostname}.local.",
        )

        aiozc = AsyncZeroconf()  # type: ignore[reportPossiblyUnbound]
        await aiozc.async_register_service(info)
        logger.info("mDNS: advertising '%s' on port %d", agent_name, port)
        return (aiozc, info)
    except Exception as e:
        logger.warning("mDNS advertisement failed: %r", e, exc_info=True)
        return None


async def unadvertise_mdns(mdns_handle: Any | None) -> None:
    """Unregister the mDNS service. Safe to call with None."""
    if mdns_handle is None:
        return

    try:
        aiozc, info = mdns_handle
        await aiozc.async_unregister_service(info)
        await aiozc.async_close()
        logger.info("mDNS: unadvertised service")
    except Exception as e:
        logger.warning("mDNS cleanup failed: %s", e)
