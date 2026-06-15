"""Tests for Agent Card generation."""

from unittest.mock import patch


class TestBuildAgentCard:
    def test_default_values(self):
        import getpass

        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({})
        username = getpass.getuser()
        assert card["name"] == f"{username}'s Agent"
        assert card["description"] == "An Amplifier-powered agent"
        assert card["version"] == "1.0"
        assert card["capabilities"] == {"streaming": False, "realtimeResponse": False}
        assert card["skills"] == []

    def test_custom_name_and_description(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card(
            {"agent_name": "Alice", "agent_description": "Alice's helper"}
        )
        assert card["name"] == "Alice"
        assert card["description"] == "Alice's helper"

    def test_default_port_in_url(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({})
        assert "8222" in card["url"]

    def test_custom_port_in_url(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"port": 9999})
        assert "9999" in card["url"]

    def test_explicit_base_url_overrides_host_port(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"base_url": "https://my-agent.example.com"})
        assert card["url"] == "https://my-agent.example.com"

    def test_supported_interfaces_structure(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"port": 8222})
        interfaces = card["supportedInterfaces"]
        assert len(interfaces) == 1
        assert interfaces[0]["protocolBinding"] == "HTTP+JSON"
        assert interfaces[0]["protocolVersion"] == "1.0"
        assert "8222" in interfaces[0]["url"]

    def test_skills_from_config(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        skills = [{"name": "code-review", "description": "Reviews code for quality"}]
        card = build_agent_card({"skills": skills})
        assert card["skills"] == skills


class TestDefaultAgentName:
    def test_default_name_uses_username(self):
        """No agent_name in config -> name derived from OS username."""
        import getpass

        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({})
        username = getpass.getuser()
        assert card["name"] == f"{username}'s Agent"

    def test_custom_name_overrides_default(self):
        """agent_name in config -> uses the custom name, not the username."""
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"agent_name": "Custom Bot"})
        assert card["name"] == "Custom Bot"

    def test_empty_name_falls_through_to_default(self):
        """agent_name: "" -> falls through to username-based default."""
        import getpass

        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"agent_name": ""})
        username = getpass.getuser()
        assert card["name"] == f"{username}'s Agent"

    def test_default_name_fallback_when_getuser_fails(self):
        """getpass.getuser() raises -> falls back to 'Amplifier Agent'."""
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        with patch(
            "amplifier_module_hooks_a2a_server.card.getpass.getuser",
            side_effect=OSError,
        ):
            card = build_agent_card({})
        assert card["name"] == "Amplifier Agent"


class TestMdnsHostnameUrl:
    """Verify that build_agent_card advertises the mDNS-resolvable .local hostname.

    When host is 0.0.0.0 (listen-all), the card URL must use <hostname>.local so
    peers without static IPs can reach us via mDNS — not a bare hostname that only
    resolves locally.
    """

    def test_bare_hostname_gets_local_appended(self):
        """host=0.0.0.0, gethostname='spark-1' -> URL uses 'spark-1.local'."""
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        with patch(
            "amplifier_module_hooks_a2a_server.card.socket.gethostname",
            return_value="spark-1",
        ):
            card = build_agent_card({})
        assert card["url"] == "http://spark-1.local:8222"
        assert card["supportedInterfaces"][0]["url"] == "http://spark-1.local:8222"

    def test_hostname_already_ending_in_local_is_not_doubled(self):
        """gethostname already returns 'spark-1.local' -> stays 'spark-1.local' (no double)."""
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        with patch(
            "amplifier_module_hooks_a2a_server.card.socket.gethostname",
            return_value="spark-1.local",
        ):
            card = build_agent_card({})
        assert card["url"] == "http://spark-1.local:8222"
        assert ".local.local" not in card["url"]

    def test_explicit_base_url_overrides_local_logic(self):
        """Explicit base_url in config is used verbatim — .local logic doesn't run."""
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        with patch(
            "amplifier_module_hooks_a2a_server.card.socket.gethostname",
            return_value="spark-1",
        ):
            card = build_agent_card({"base_url": "http://192.168.1.99:9000"})
        assert card["url"] == "http://192.168.1.99:9000"

    def test_explicit_host_ip_is_used_verbatim(self):
        """host='192.168.1.5' (not 0.0.0.0) -> URL uses the IP, no .local appended."""
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"host": "192.168.1.5"})
        assert card["url"] == "http://192.168.1.5:8222"
        assert ".local" not in card["url"]
