"""Tests for authenticated inbound A2A sender identity."""

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient as AioTestClient
from aiohttp.test_utils import TestServer as AioTestServer
from amplifier_module_hooks_a2a_server.card import build_agent_card
from amplifier_module_hooks_a2a_server.contacts import ContactStore
from amplifier_module_hooks_a2a_server.pending import PendingQueue
from amplifier_module_hooks_a2a_server.registry import A2ARegistry
from amplifier_module_hooks_a2a_server.server import A2AServer
from amplifier_module_tool_a2a.client import A2AClient

SENDER_URL = "https://peer.example"
KEY_ID = "peer-key"
SECRET = "test-secret"


def _server(tmp_path, tier: str = "trusted") -> tuple[A2AServer, A2ARegistry]:
    config = {
        "port": 0,
        "authentication": {
            "peers": {
                KEY_ID: {
                    "secret": SECRET,
                    "sender_url": SENDER_URL,
                }
            }
        },
    }
    registry = A2ARegistry()
    registry.contact_store = ContactStore(path=tmp_path / "contacts.json")
    registry.pending_queue = PendingQueue(base_dir=tmp_path)
    registry.contact_store._contacts.append(
        {
            "url": SENDER_URL,
            "name": "Authenticated Peer",
            "tier": tier,
            "first_seen": "2026-01-01T00:00:00+00:00",
            "last_seen": "2026-01-01T00:00:00+00:00",
        }
    )
    coordinator = MagicMock()
    coordinator.session_id = "parent"
    coordinator.config = {"session": {}, "providers": [], "tools": []}
    return A2AServer(registry, build_agent_card(config), coordinator, config), registry


def _request(sender_url: str = SENDER_URL, nonce: str = "nonce-1"):
    payload = {
        "message": {"role": "user", "parts": [{"text": "Hello"}]},
        "sender_url": sender_url,
    }
    body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    timestamp = str(int(time.time()))
    digest = hashlib.sha256(body).hexdigest()
    signature = hmac.new(
        SECRET.encode(),
        f"{timestamp}\n{nonce}\n{digest}".encode(),
        hashlib.sha256,
    ).hexdigest()
    headers = {
        "Content-Type": "application/json",
        "X-A2A-Key-Id": KEY_ID,
        "X-A2A-Timestamp": timestamp,
        "X-A2A-Nonce": nonce,
        "X-A2A-Signature": signature,
    }
    return body, headers


async def test_missing_authentication_is_rejected(tmp_path):
    server, _ = _server(tmp_path)
    async with AioTestClient(AioTestServer(server.app)) as client:
        response = await client.post(
            "/a2a/v1/message:send",
            json={"message": {"parts": [{"text": "Hello"}]}, "sender_url": SENDER_URL},
        )
    assert response.status == 401


async def test_spoofed_sender_url_is_rejected(tmp_path):
    server, _ = _server(tmp_path)
    body, headers = _request(sender_url="https://victim.example")
    async with AioTestClient(AioTestServer(server.app)) as client:
        response = await client.post("/a2a/v1/message:send", data=body, headers=headers)
    assert response.status == 403


async def test_valid_authenticated_sender_reaches_trusted_mode(tmp_path):
    server, _ = _server(tmp_path)
    body, headers = _request()
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value="Authenticated response")
    with patch(
        "amplifier_module_hooks_a2a_server.server.AmplifierSession",
        return_value=session,
    ):
        async with AioTestClient(AioTestServer(server.app)) as client:
            response = await client.post(
                "/a2a/v1/message:send", data=body, headers=headers
            )
            data = await response.json()
    assert response.status == 200
    assert data["status"] == "COMPLETED"


async def test_replayed_request_is_rejected(tmp_path):
    server, _ = _server(tmp_path, tier="known")
    body, headers = _request()
    async with AioTestClient(AioTestServer(server.app)) as client:
        first = await client.post("/a2a/v1/message:send", data=body, headers=headers)
        replay = await client.post("/a2a/v1/message:send", data=body, headers=headers)
    assert first.status == 200
    assert replay.status == 401


async def test_client_and_server_hmac_configuration_interoperate(tmp_path):
    server, _ = _server(tmp_path)
    session = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    session.execute = AsyncMock(return_value="Signed response")

    with patch(
        "amplifier_module_hooks_a2a_server.server.AmplifierSession",
        return_value=session,
    ):
        async with AioTestClient(AioTestServer(server.app)) as http_server:
            base_url = str(http_server.make_url("")).rstrip("/")
            client = A2AClient(
                outbound_policy={
                    "allowed_hosts": ["127.0.0.1"],
                    "require_https": False,
                },
                authentication={
                    "peers": {
                        base_url: {
                            "key_id": KEY_ID,
                            "secret": SECRET,
                        }
                    }
                },
            )
            try:
                result = await client.send_message(
                    base_url,
                    "Hello",
                    sender_url=SENDER_URL,
                    sender_name="Authenticated Peer",
                )
            finally:
                await client.close()

    assert result["status"] == "COMPLETED"
    assert result["artifacts"][0]["parts"][0]["text"] == "Signed response"
