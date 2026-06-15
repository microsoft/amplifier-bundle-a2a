"""Agent Card generation — builds the A2A identity document from config."""

import getpass
import socket
from typing import Any


def _default_agent_name() -> str:
    """Derive a default agent name from the OS username."""
    try:
        username = getpass.getuser()
        return f"{username}'s Agent"
    except Exception:
        return "Amplifier Agent"


def build_agent_card(config: dict[str, Any]) -> dict[str, Any]:
    """Build an A2A Agent Card dict from module config.

    The Agent Card is served at GET /.well-known/agent.json and tells
    remote agents who we are, what we can do, and how to talk to us.
    """
    port = config.get("port", 8222)
    host = config.get("host", "0.0.0.0")
    # Don't use 0.0.0.0 in the URL — it's a listen address, not reachable.
    # Use the machine's hostname for a shareable URL.
    if host == "0.0.0.0":
        try:
            url_host = socket.gethostname()
            # Advertise the mDNS-resolvable .local name so peers can reach us
            # without static IPs.  discovery.py already uses "<hostname>.local."
            # for the mDNS record — keep the card URL consistent with that.
            if not url_host.endswith(".local"):
                url_host = f"{url_host}.local"
        except Exception:
            url_host = "127.0.0.1"
    else:
        url_host = host
    base_url = config.get("base_url", f"http://{url_host}:{port}")

    return {
        "name": config.get("agent_name") or _default_agent_name(),
        "description": config.get("agent_description", "An Amplifier-powered agent"),
        "version": "1.0",
        "url": base_url,
        "supportedInterfaces": [
            {
                "url": base_url,
                "protocolBinding": "HTTP+JSON",
                "protocolVersion": "1.0",
            }
        ],
        "capabilities": {
            "streaming": False,
            "realtimeResponse": config.get("realtime_response", False),
        },
        "skills": config.get("skills", []),
    }
