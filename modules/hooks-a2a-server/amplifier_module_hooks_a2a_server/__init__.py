"""A2A server hook module — receives messages from remote agents.

Starts an HTTP server in mount() to serve the Agent Card and handle
incoming A2A messages. Registers the shared A2ARegistry as a coordinator
capability for tool-a2a to access.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

__amplifier_module_type__ = "hook"

logger = logging.getLogger(__name__)


async def mount(
    coordinator: Any, config: dict[str, Any] | None = None
) -> Callable[[], Awaitable[None]] | None:
    """Mount the A2A server.

    - Skips in child sessions (parent_id guard)
    - Creates the A2ARegistry and registers it as a capability
    - Starts the HTTP server
    - Registers cleanup for graceful shutdown
    """
    config = config or {}

    # Opt-in gate: the server is INERT unless explicitly enabled.
    # This lets the a2a behavior be installed globally (e.g. `amplifier bundle add
    # ...#subdirectory=behaviors/a2a.yaml --app`) while staying dormant in every
    # session until a directory opts in. Turn it on per-directory by overriding
    # this module's config in that project's .amplifier/settings.yaml with
    # `enabled: true` plus your agent_name/port/known_agents.
    if not config.get("enabled", False):
        logger.debug(
            "A2A server not enabled — skipping. Set hooks-a2a-server config "
            "'enabled: true' (e.g. in a project's .amplifier/settings.yaml) to activate."
        )
        return

    # Don't start the server in child sessions (e.g., Mode C spawned sessions)
    if coordinator.parent_id:
        logger.debug("Skipping A2A server in child session %s", coordinator.session_id)
        return

    from .card import build_agent_card
    from .contacts import ContactStore
    from .pending import PendingQueue
    from .registry import A2ARegistry
    from .server import A2AServer

    # Build shared state from config
    known_agents = config.get("known_agents", [])
    registry = A2ARegistry(known_agents=known_agents)

    # Create persistent stores and attach to registry
    registry.contact_store = ContactStore()
    registry.pending_queue = PendingQueue()

    # Build the Agent Card
    card = build_agent_card(config)
    registry.card = card

    # Register shared state so tool-a2a can access it
    coordinator.register_capability("a2a.registry", registry)

    # Register hook for injecting pending approvals/messages into active session
    from .injection import A2AInjectionHandler

    injection_handler = A2AInjectionHandler(registry.pending_queue, registry)
    coordinator.hooks.register(
        "provider:request",
        injection_handler,
        priority=5,
        name="a2a-pending-injection",
    )

    # Create and start the HTTP server
    server = A2AServer(registry, card, coordinator, config)
    try:
        await server.start()
    except OSError as e:
        port = config.get("port", 8222)
        registry.server_running = False
        logger.warning(
            "A2A server failed to start: port %s is already in use. "
            "Another Amplifier session or service may be using this port. "
            "Configure a different port via hooks-a2a-server config: "
            "hooks: [{module: hooks-a2a-server, config: {port: <new_port>}}]. "
            "Error: %s",
            port,
            e,
        )
        return

    # Start mDNS advertisement if enabled
    from .discovery import ZEROCONF_AVAILABLE, advertise_mdns, unadvertise_mdns

    mdns_handle = None
    discovery_config = config.get("discovery", {})
    if discovery_config.get("mdns", True):  # Default: enabled
        if not ZEROCONF_AVAILABLE:
            # Fail loud, not silent: the user asked for LAN discovery but it
            # can't run. 'zeroconf' is a declared dependency, so this should
            # only happen in a broken/partial environment.
            logger.warning(
                "A2A: mDNS/LAN discovery is enabled but the 'zeroconf' package is "
                "not installed — this agent will NOT be discoverable on the local "
                "network. Install it with `uv pip install zeroconf` (or reinstall the "
                "hooks-a2a-server module). To intentionally disable LAN discovery and "
                "silence this warning, set hooks-a2a-server config "
                "'discovery: {mdns: false}'."
            )
        server_port = server.port or 0
        mdns_handle = await advertise_mdns(
            agent_name=config.get("agent_name", "Amplifier Agent"),
            port=server_port,
            base_url=card.get("url", f"http://0.0.0.0:{server_port}"),
        )

    # Register cleanup for graceful shutdown (server + mDNS).
    #
    # IDEMPOTENCY: the cleanup closure is registered TWICE in a normal session:
    #   1. Here, via coordinator.register_cleanup(cleanup).
    #   2. Again by _session_init.py, which does `if cleanup: coordinator.register_cleanup(cleanup)`
    #      on the value returned from mount().
    # Both registrations fire at session end.  The `_cleaned` guard ensures the
    # second invocation is a silent no-op rather than a double-stop.
    #
    # The VALIDATOR path (amplifier_core/validation/hook.py) calls:
    #     mount_result = await mount_fn(coordinator, actual_config)
    #     if mount_result is not None and callable(mount_result):
    #         await mount_result()
    # Returning this cleanup lets the validator tear down the validation-mount
    # server before the real RustCoordinator mount runs — preventing the
    # "[Errno 98] address already in use" collision on a fixed port (e.g. 8222).
    original_stop = server.stop
    _cleaned = False

    async def cleanup() -> None:
        nonlocal _cleaned
        if _cleaned:
            return
        _cleaned = True
        await unadvertise_mdns(mdns_handle)
        await original_stop()

    coordinator.register_cleanup(cleanup)
    return cleanup
