"""Integration test — full A2A round-trip on localhost.

Tests the complete flow:
1. hooks-a2a-server starts an HTTP server
2. tool-a2a's client sends a message
3. Server receives message, spawns child session (mocked)
4. Child session returns a response
5. Server returns completed task
6. Client receives the response

The only mock is AmplifierSession (to avoid needing real LLM providers).
All HTTP communication is real, over localhost.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import aiohttp

from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.card import build_agent_card
from amplifier_module_hooks_a2a_server.server import A2AServer
from amplifier_module_tool_a2a.client import A2AClient

LOCAL_POLICY = {
    "allowed_hosts": ["127.0.0.1"],
    "require_https": False,
}


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "integration-parent"
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


def _make_mock_session(response_text):
    mock = AsyncMock()
    mock.execute = AsyncMock(return_value=response_text)
    mock.initialize = AsyncMock()
    mock.cleanup = AsyncMock()
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=False)
    return mock


class TestFullRoundTrip:
    async def test_send_message_and_receive_response(self):
        """Full round-trip: client → server → child session → response."""
        # --- Set up the server ---
        server_config = {
            "port": 0,  # OS assigns free port
            "host": "127.0.0.1",
            "agent_name": "Integration Test Agent",
            "agent_description": "Agent for integration testing",
            "skills": [{"name": "math", "description": "Does math"}],
            "known_agents": [],
            "authentication": {"required": False},
        }
        registry = A2ARegistry()
        card = build_agent_card(server_config)
        coordinator = _make_mock_coordinator()
        server = A2AServer(registry, card, coordinator, server_config)

        mock_session = _make_mock_session("The capital of France is Paris.")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            await server.start()
            try:
                base_url = f"http://127.0.0.1:{server.port}"

                # --- Client fetches agent card ---
                client = A2AClient(timeout=10.0, outbound_policy=LOCAL_POLICY)
                try:
                    agent_card = await client.fetch_agent_card(base_url)
                    assert agent_card["name"] == "Integration Test Agent"
                    assert agent_card["skills"][0]["name"] == "math"

                    # --- Client sends a message ---
                    result = await client.send_message(
                        base_url,
                        "What is the capital of France?",
                    )

                    # --- Verify the response ---
                    assert result["status"] == "COMPLETED"
                    assert len(result["artifacts"]) == 1
                    assert (
                        result["artifacts"][0]["parts"][0]["text"]
                        == "The capital of France is Paris."
                    )
                    assert result["history"][0]["role"] == "user"
                    assert (
                        "capital of France" in result["history"][0]["parts"][0]["text"]
                    )

                    # --- Verify task is pollable ---
                    task_id = result["id"]
                    polled = await client.get_task_status(base_url, task_id)
                    assert polled["status"] == "COMPLETED"
                    assert polled["id"] == task_id

                finally:
                    await client.close()
            finally:
                await server.stop()

    async def test_agent_card_served_correctly(self):
        """Verify the Agent Card endpoint works via real HTTP."""
        server_config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Card Test Agent",
        }
        registry = A2ARegistry()
        card = build_agent_card(server_config)
        coordinator = _make_mock_coordinator()
        server = A2AServer(registry, card, coordinator, server_config)

        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.port}/.well-known/agent.json"
                async with session.get(url) as resp:
                    assert resp.status == 200
                    data = await resp.json()
                    assert data["name"] == "Card Test Agent"
                    assert data["capabilities"]["streaming"] is False
                    assert (
                        data["supportedInterfaces"][0]["protocolBinding"] == "HTTP+JSON"
                    )
        finally:
            await server.stop()

    async def test_task_not_found_returns_404(self):
        """Verify polling a nonexistent task returns 404."""
        server_config = {"port": 0, "host": "127.0.0.1", "agent_name": "Test"}
        registry = A2ARegistry()
        card = build_agent_card(server_config)
        coordinator = _make_mock_coordinator()
        server = A2AServer(registry, card, coordinator, server_config)

        await server.start()
        try:
            async with aiohttp.ClientSession() as session:
                url = f"http://127.0.0.1:{server.port}/a2a/v1/tasks/fake-id"
                async with session.get(url) as resp:
                    assert resp.status == 404
        finally:
            await server.stop()

    async def test_tool_agents_operation_with_registry(self):
        """Verify the tool can list agents from a real registry."""
        from amplifier_module_tool_a2a import A2ATool

        registry = A2ARegistry(
            known_agents=[
                {"name": "Alice", "url": "http://alice:8222"},
                {"name": "Bob", "url": "http://bob:8222"},
            ]
        )
        coordinator = MagicMock()
        coordinator.get_capability = MagicMock(return_value=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is True
        assert len(result.output) == 2
        assert result.output[0]["name"] == "Alice"
        assert result.output[1]["name"] == "Bob"

    async def test_tool_send_through_real_server(self):
        """Verify tool-a2a can send through a real HTTP server."""
        from amplifier_module_tool_a2a import A2ATool

        # Start a real server
        server_config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Remote",
            "authentication": {"required": False},
        }
        registry = A2ARegistry()
        card = build_agent_card(server_config)
        coordinator = _make_mock_coordinator()
        server = A2AServer(registry, card, coordinator, server_config)

        mock_session = _make_mock_session("42 is the answer")

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            await server.start()
            try:
                base_url = f"http://127.0.0.1:{server.port}"

                # Set up tool with the server's URL
                tool_registry = A2ARegistry(
                    known_agents=[{"name": "Remote", "url": base_url}]
                )
                tool_coordinator = MagicMock()
                tool_coordinator.get_capability = MagicMock(return_value=tool_registry)
                tool = A2ATool(
                    tool_coordinator,
                    {
                        "default_timeout": 10.0,
                        "outbound_policy": LOCAL_POLICY,
                    },
                )

                try:
                    result = await tool.execute(
                        {
                            "operation": "send",
                            "agent": "Remote",
                            "message": "What is the meaning of life?",
                        }
                    )
                    assert result.success is True
                    assert result.output is not None
                    assert result.output["status"] == "COMPLETED"
                    assert (
                        result.output["artifacts"][0]["parts"][0]["text"]
                        == "42 is the answer"
                    )
                finally:
                    await tool.client.close()
            finally:
                await server.stop()
