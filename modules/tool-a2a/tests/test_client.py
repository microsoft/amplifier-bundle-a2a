"""Tests for A2AClient HTTP client."""

from aiohttp import web
from aiohttp.test_utils import TestClient as AioTestClient
from aiohttp.test_utils import TestServer as AioTestServer

LOCAL_POLICY = {
    "allowed_hosts": ["127.0.0.1"],
    "require_https": False,
}


def _make_mock_a2a_server():
    """Create a mock A2A server for client testing."""
    app = web.Application()

    async def handle_card(request):
        return web.json_response(
            {
                "name": "Mock Agent",
                "version": "1.0",
                "url": "http://mock:8222",
            }
        )

    async def handle_send(request):
        body = await request.json()
        message = body["message"]
        text = message["parts"][0]["text"]
        return web.json_response(
            {
                "id": "task-abc",
                "status": "COMPLETED",
                "artifacts": [{"parts": [{"text": f"Echo: {text}"}]}],
                "history": [message],
            }
        )

    async def handle_task(request):
        task_id = request.match_info["task_id"]
        if task_id == "not-found":
            return web.json_response({"error": "Not found"}, status=404)
        return web.json_response(
            {
                "id": task_id,
                "status": "COMPLETED",
                "artifacts": [{"parts": [{"text": "Done"}]}],
            }
        )

    app.router.add_get("/.well-known/agent.json", handle_card)
    app.router.add_post("/a2a/v1/message:send", handle_send)
    app.router.add_get("/a2a/v1/tasks/{task_id}", handle_task)
    return app


class TestFetchAgentCard:
    async def test_fetches_card_successfully(self):
        from amplifier_module_tool_a2a.client import A2AClient

        app = _make_mock_a2a_server()
        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                card = await client.fetch_agent_card(base_url)
                assert card["name"] == "Mock Agent"
                assert card["version"] == "1.0"
            finally:
                await client.close()

    async def test_raises_on_http_error(self):
        import pytest
        from amplifier_module_tool_a2a.client import A2AClient

        # Server that returns 500
        app = web.Application()
        app.router.add_get(
            "/.well-known/agent.json",
            lambda r: web.Response(status=500, text="Internal Error"),
        )

        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                with pytest.raises(ConnectionError, match="HTTP 500"):
                    await client.fetch_agent_card(base_url)
            finally:
                await client.close()

    async def test_strips_trailing_slash_from_url(self):
        from amplifier_module_tool_a2a.client import A2AClient

        app = _make_mock_a2a_server()
        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url("")) + "/"  # trailing slash
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                card = await client.fetch_agent_card(base_url)
                assert card["name"] == "Mock Agent"
            finally:
                await client.close()


class TestSendMessage:
    async def test_sends_message_and_returns_task(self):
        from amplifier_module_tool_a2a.client import A2AClient

        app = _make_mock_a2a_server()
        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                result = await client.send_message(base_url, "What is 2+2?")
                assert result["status"] == "COMPLETED"
                assert (
                    result["artifacts"][0]["parts"][0]["text"] == "Echo: What is 2+2?"
                )
            finally:
                await client.close()

    async def test_raises_on_server_error(self):
        import pytest
        from amplifier_module_tool_a2a.client import A2AClient

        app = web.Application()
        app.router.add_post(
            "/a2a/v1/message:send",
            lambda r: web.json_response({"error": "Broken"}, status=500),
        )

        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                with pytest.raises(ConnectionError, match="Server error"):
                    await client.send_message(base_url, "Hello")
            finally:
                await client.close()

    async def test_raises_on_non_json_server_error(self):
        """Fix 1: send_message must not crash on non-JSON 5xx responses."""
        import pytest
        from amplifier_module_tool_a2a.client import A2AClient

        app = web.Application()
        app.router.add_post(
            "/a2a/v1/message:send",
            lambda r: web.Response(
                status=502, text="<html>Bad Gateway</html>", content_type="text/html"
            ),
        )

        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                with pytest.raises(ConnectionError, match="HTTP 502"):
                    await client.send_message(base_url, "Hello")
            finally:
                await client.close()


class TestGetTaskStatus:
    async def test_gets_task_status(self):
        from amplifier_module_tool_a2a.client import A2AClient

        app = _make_mock_a2a_server()
        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                result = await client.get_task_status(base_url, "task-123")
                assert result["id"] == "task-123"
                assert result["status"] == "COMPLETED"
            finally:
                await client.close()

    async def test_raises_on_not_found(self):
        import pytest
        from amplifier_module_tool_a2a.client import A2AClient

        app = _make_mock_a2a_server()
        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                with pytest.raises(ValueError, match="Task not found"):
                    await client.get_task_status(base_url, "not-found")
            finally:
                await client.close()

    async def test_raises_on_server_error(self):
        """Fix 2: get_task_status must raise ConnectionError on 5xx."""
        import pytest
        from amplifier_module_tool_a2a.client import A2AClient

        app = web.Application()
        app.router.add_get(
            "/a2a/v1/tasks/{task_id}",
            lambda r: web.Response(status=500, text="Internal Server Error"),
        )

        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                with pytest.raises(ConnectionError, match="HTTP 500"):
                    await client.get_task_status(base_url, "task-xyz")
            finally:
                await client.close()


class TestClientLifecycle:
    async def test_close_is_idempotent(self):
        from amplifier_module_tool_a2a.client import A2AClient

        client = A2AClient()
        await client.close()  # No session created yet — should not crash
        await client.close()  # Double close — should not crash

    async def test_async_context_manager(self):
        """Fix 3: A2AClient supports async with for automatic cleanup."""
        from amplifier_module_tool_a2a.client import A2AClient

        app = _make_mock_a2a_server()
        async with AioTestClient(AioTestServer(app)) as mock:
            base_url = str(mock.make_url(""))
            async with A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY) as client:
                card = await client.fetch_agent_card(base_url)
                assert card["name"] == "Mock Agent"
            # After exiting the context, session should be closed
            assert client._session is None


class TestOutboundPolicy:
    async def test_blocks_loopback_by_default(self):
        import pytest
        from amplifier_module_tool_a2a.client import OutboundPolicy

        with pytest.raises(ValueError, match="blocked address"):
            await OutboundPolicy({"require_https": False}).validate(
                "http://127.0.0.1:8222"
            )

    async def test_allows_explicit_private_host(self):
        from amplifier_module_tool_a2a.client import OutboundPolicy

        result = await OutboundPolicy(LOCAL_POLICY).validate("http://127.0.0.1:8222")
        assert result == "http://127.0.0.1:8222"

    async def test_blocks_non_http_schemes(self):
        import pytest
        from amplifier_module_tool_a2a.client import OutboundPolicy

        with pytest.raises(ValueError, match="http or https"):
            await OutboundPolicy().validate("file:///etc/passwd")

    async def test_redirects_are_rejected(self):
        import pytest
        from amplifier_module_tool_a2a.client import A2AClient

        app = web.Application()
        app.router.add_get(
            "/.well-known/agent.json",
            lambda request: web.Response(
                status=302, headers={"Location": "http://169.254.169.254/"}
            ),
        )
        async with AioTestClient(AioTestServer(app)) as mock:
            client = A2AClient(timeout=5.0, outbound_policy=LOCAL_POLICY)
            try:
                with pytest.raises(ConnectionError, match="redirects"):
                    await client.fetch_agent_card(str(mock.make_url("")))
            finally:
                await client.close()
