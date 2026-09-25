"""Tests for Mode C — autonomous session spawning."""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient as AioTestClient, TestServer as AioTestServer

from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.card import build_agent_card


def _make_mock_coordinator():
    coordinator = MagicMock()
    coordinator.session_id = "parent-session-123"
    coordinator.parent_id = None
    coordinator.config = {
        "session": {
            "orchestrator": "loop-basic",
            "context": "context-simple",
        },
        "providers": [{"module": "provider-test", "config": {"model": "test-model"}}],
        "tools": [
            {"module": "tool-filesystem"},
        ],
        "hooks": [
            {"module": "hooks-a2a-server", "config": {"port": 8222}},
        ],
    }
    return coordinator


def _make_mock_session(response_text="The answer is 42"):
    """Create a mock AmplifierSession that returns a canned response."""
    mock_session = AsyncMock()
    mock_session.execute = AsyncMock(return_value=response_text)
    mock_session.initialize = AsyncMock()
    mock_session.cleanup = AsyncMock()
    mock_session.__aenter__ = AsyncMock(return_value=mock_session)
    mock_session.__aexit__ = AsyncMock(return_value=False)
    return mock_session


def _make_server(coordinator=None):
    config = {
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


class TestModeCSendMessage:
    async def test_spawns_session_and_returns_completed_task(self):
        mock_session = _make_mock_session("The answer is 42")
        server, registry = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json={
                        "message": {
                            "role": "user",
                            "parts": [{"text": "What is the answer?"}],
                        }
                    },
                )
                assert resp.status == 200
                data = await resp.json()

                assert data["status"] == "COMPLETED"
                assert data["artifacts"][0]["parts"][0]["text"] == "The answer is 42"
                assert data["history"][0]["role"] == "user"

    async def test_child_session_gets_parent_id(self):
        mock_session = _make_mock_session("OK")
        server, _ = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            async with AioTestClient(AioTestServer(server.app)) as client:
                await client.post(
                    "/a2a/v1/message:send",
                    json={
                        "message": {
                            "role": "user",
                            "parts": [{"text": "Hi"}],
                        }
                    },
                )
                # Verify AmplifierSession was created with parent_id
                call_kwargs = mock_cls.call_args[1]
                assert call_kwargs["parent_id"] == "parent-session-123"

    async def test_child_session_excludes_hooks(self):
        mock_session = _make_mock_session("OK")
        server, _ = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ) as mock_cls:
            async with AioTestClient(AioTestServer(server.app)) as client:
                await client.post(
                    "/a2a/v1/message:send",
                    json={
                        "message": {
                            "role": "user",
                            "parts": [{"text": "Hi"}],
                        }
                    },
                )
                # Verify child config has no hooks
                call_kwargs = mock_cls.call_args[1]
                child_config = call_kwargs["config"]
                assert "hooks" not in child_config

    async def test_returns_500_when_session_fails(self):
        mock_session = _make_mock_session()
        mock_session.execute = AsyncMock(side_effect=RuntimeError("LLM error"))
        server, _ = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json={
                        "message": {
                            "role": "user",
                            "parts": [{"text": "Fail please"}],
                        }
                    },
                )
                assert resp.status == 500
                data = await resp.json()
                assert data["status"] == "FAILED"

    async def test_rejects_invalid_json(self):
        server, _ = _make_server()
        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400

    async def test_rejects_missing_message(self):
        server, _ = _make_server()
        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json={"not_a_message": "nope"},
            )
            assert resp.status == 400

    async def test_rejects_empty_message_parts(self):
        server, _ = _make_server()
        async with AioTestClient(AioTestServer(server.app)) as client:
            resp = await client.post(
                "/a2a/v1/message:send",
                json={"message": {"role": "user", "parts": []}},
            )
            assert resp.status == 400

    async def test_task_stored_in_registry(self):
        mock_session = _make_mock_session("Stored answer")
        server, registry = _make_server()

        with patch(
            "amplifier_module_hooks_a2a_server.server.AmplifierSession",
            return_value=mock_session,
        ):
            async with AioTestClient(AioTestServer(server.app)) as client:
                resp = await client.post(
                    "/a2a/v1/message:send",
                    json={
                        "message": {
                            "role": "user",
                            "parts": [{"text": "Store me"}],
                        }
                    },
                )
                data = await resp.json()
                task_id = data["id"]

                # Verify task is in registry
                task = registry.get_task(task_id)
                assert task is not None
                assert task["status"] == "COMPLETED"
