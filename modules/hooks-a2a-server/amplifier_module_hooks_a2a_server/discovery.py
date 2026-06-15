"""mDNS service advertisement via Zeroconf."""

import logging
import socket
from typing import Any

logger = logging.getLogger(__name__)

# Zeroconf is optional — graceful degradation if not installed
try:
    from zeroconf import ServiceInfo, Zeroconf  # pyright: ignore[reportMissingImports]

    ZEROCONF_AVAILABLE = True
except ImportError:
    ZEROCONF_AVAILABLE = False
    logger.debug("zeroconf not installed — mDNS discovery disabled")


SERVICE_TYPE = "_a2a._tcp.local."


async def advertise_mdns(agent_name: str, port: int, base_url: str) -> Any | None:
    """Register an A2A service via mDNS/Zeroconf.

    Returns the (Zeroconf, ServiceInfo) tuple needed for cleanup,
    or None if zeroconf is unavailable or registration fails.
    """
    if not ZEROCONF_AVAILABLE:
        logger.info("mDNS advertisement skipped — zeroconf not installed")
        return None

    try:
        from urllib.parse import urlparse

        # Build service info
        service_name = f"{agent_name}.{SERVICE_TYPE}"

        # Get local hostname for the service
        hostname = socket.gethostname()

        # Resolve a reachable IPv4 address for the service record. Zeroconf
        # registration FAILS (with an empty error) on multi-interface hosts
        # when no addresses are supplied, so derive one from base_url. Prefer
        # the host embedded in base_url (already the LAN-reachable address);
        # fall back to resolving the hostname.
        host = urlparse(base_url).hostname or hostname
        try:
            addr = socket.inet_aton(host)  # host is already an IP literal
        except OSError:
            addr = socket.inet_aton(socket.gethostbyname(host))

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
            addresses=[addr],
            server=f"{hostname}.local.",
        )

        zc = Zeroconf()  # type: ignore[reportPossiblyUnbound]
        zc.register_service(info)
        logger.info("mDNS: advertising '%s' on port %d", agent_name, port)
        return (zc, info)
    except Exception as e:
        logger.warning("mDNS advertisement failed: %s", e)
        return None


async def unadvertise_mdns(mdns_handle: Any | None) -> None:
    """Unregister the mDNS service. Safe to call with None."""
    if mdns_handle is None:
        return

    try:
        zc, info = mdns_handle
        zc.unregister_service(info)
        zc.close()
        logger.info("mDNS: unadvertised service")
    except Exception as e:
        logger.warning("mDNS cleanup failed: %s", e)
