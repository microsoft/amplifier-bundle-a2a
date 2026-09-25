"""Request authentication for inbound A2A traffic."""

import hashlib
import hmac
import time
from dataclasses import dataclass
from typing import Any

from aiohttp import web

KEY_ID_HEADER = "X-A2A-Key-Id"
TIMESTAMP_HEADER = "X-A2A-Timestamp"
NONCE_HEADER = "X-A2A-Nonce"
SIGNATURE_HEADER = "X-A2A-Signature"


@dataclass(frozen=True)
class AuthenticatedPeer:
    """Verified peer identity bound to a configured sender URL."""

    key_id: str
    sender_url: str


class RequestAuthenticator:
    """Verify HMAC-authenticated requests and reject replays."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.required = config.get("required", True)
        self.allow_anonymous_first_contact = config.get(
            "allow_anonymous_first_contact", False
        )
        self.max_clock_skew = int(config.get("max_clock_skew_seconds", 300))
        self._peers = config.get("peers", {})
        self._seen_nonces: dict[tuple[str, str], float] = {}

    @staticmethod
    def _signing_input(timestamp: str, nonce: str, body: bytes) -> bytes:
        digest = hashlib.sha256(body).hexdigest()
        return f"{timestamp}\n{nonce}\n{digest}".encode()

    def verify(
        self, request: web.Request, body: bytes
    ) -> tuple[AuthenticatedPeer | None, web.Response | None]:
        """Return the authenticated peer, or an HTTP error response."""
        key_id = request.headers.get(KEY_ID_HEADER)
        timestamp = request.headers.get(TIMESTAMP_HEADER)
        nonce = request.headers.get(NONCE_HEADER)
        signature = request.headers.get(SIGNATURE_HEADER)

        if not any((key_id, timestamp, nonce, signature)):
            if self.allow_anonymous_first_contact:
                return None, None
            if not self.required:
                return None, None
            return None, web.json_response(
                {"error": "A2A request authentication required"}, status=401
            )

        if not all((key_id, timestamp, nonce, signature)):
            return None, web.json_response(
                {"error": "Incomplete A2A authentication headers"}, status=401
            )
        assert key_id is not None
        assert timestamp is not None
        assert nonce is not None
        assert signature is not None

        peer = self._peers.get(key_id)
        if not isinstance(peer, dict):
            return None, web.json_response(
                {"error": "Unknown A2A authentication key"}, status=401
            )
        secret = peer.get("secret")
        sender_url = peer.get("sender_url")
        if not isinstance(secret, str) or not secret or not isinstance(sender_url, str):
            return None, web.json_response(
                {"error": "Invalid server authentication configuration"}, status=500
            )

        try:
            request_time = int(timestamp)
        except ValueError:
            return None, web.json_response(
                {"error": "Invalid A2A authentication timestamp"}, status=401
            )

        now = int(time.time())
        if abs(now - request_time) > self.max_clock_skew:
            return None, web.json_response(
                {"error": "Expired A2A authentication timestamp"}, status=401
            )

        cutoff = now - self.max_clock_skew
        self._seen_nonces = {
            item: seen_at
            for item, seen_at in self._seen_nonces.items()
            if seen_at >= cutoff
        }
        replay_key = (key_id, nonce)
        if replay_key in self._seen_nonces:
            return None, web.json_response(
                {"error": "Replayed A2A request"}, status=401
            )

        expected = hmac.new(
            secret.encode(),
            self._signing_input(timestamp, nonce, body),
            hashlib.sha256,
        ).hexdigest()
        if not hmac.compare_digest(expected, signature):
            return None, web.json_response(
                {"error": "Invalid A2A request signature"}, status=401
            )

        self._seen_nonces[replay_key] = now
        return AuthenticatedPeer(key_id=key_id, sender_url=sender_url), None
