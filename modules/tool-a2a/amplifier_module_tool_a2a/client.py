"""A2A HTTP client with request authentication and egress controls."""

import asyncio
import hashlib
import hmac
import ipaddress
import json
import logging
import secrets
import socket
import time
from fnmatch import fnmatch
from typing import Any, Self
from urllib.parse import urlsplit, urlunsplit

import aiohttp
from aiohttp.abc import AbstractResolver, ResolveResult

logger = logging.getLogger(__name__)


class OutboundPolicy:
    """Validate A2A destinations before network access."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        config = config or {}
        self.require_https = config.get("require_https", True)
        self.allow_private_networks = config.get("allow_private_networks", False)
        self.allowed_hosts = {
            str(host).lower().rstrip(".") for host in config.get("allowed_hosts", [])
        }
        self.allowed_cidrs = [
            ipaddress.ip_network(cidr, strict=False)
            for cidr in config.get("allowed_cidrs", [])
        ]

    @staticmethod
    def normalize_base_url(base_url: str) -> str:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"}:
            raise ValueError("A2A destinations must use http or https")
        if parsed.username or parsed.password:
            raise ValueError("A2A destination URLs must not contain credentials")
        if not parsed.hostname:
            raise ValueError("A2A destination URL must include a hostname")
        if parsed.query or parsed.fragment:
            raise ValueError(
                "A2A base URLs must not include query strings or fragments"
            )
        path = parsed.path.rstrip("/")
        return urlunsplit(
            (
                parsed.scheme.lower(),
                parsed.netloc,
                path,
                "",
                "",
            )
        )

    def _host_is_allowed(self, hostname: str) -> bool:
        hostname = hostname.lower().rstrip(".")
        return any(fnmatch(hostname, pattern) for pattern in self.allowed_hosts)

    def _address_is_allowed(
        self, address: ipaddress.IPv4Address | ipaddress.IPv6Address
    ) -> bool:
        if any(address in network for network in self.allowed_cidrs):
            return True
        if self.allow_private_networks:
            return not (
                address.is_multicast or address.is_unspecified or address.is_reserved
            )
        return address.is_global

    async def resolve(self, hostname: str, port: int) -> list[str]:
        """Resolve and validate every address returned for a hostname."""
        try:
            literal = ipaddress.ip_address(hostname)
            addresses = [literal]
        except ValueError:
            loop = asyncio.get_running_loop()
            results = await loop.getaddrinfo(
                hostname,
                port,
                type=socket.SOCK_STREAM,
            )
            addresses = list({ipaddress.ip_address(result[4][0]) for result in results})

        if not addresses:
            raise ValueError(f"A2A destination did not resolve: {hostname}")

        if not self._host_is_allowed(hostname):
            blocked = [
                str(address)
                for address in addresses
                if not self._address_is_allowed(address)
            ]
            if blocked:
                raise ValueError(
                    "A2A destination resolves to a blocked address: "
                    + ", ".join(sorted(blocked))
                )
        return [str(address) for address in addresses]

    async def validate(self, base_url: str) -> str:
        """Normalize a base URL and enforce scheme, host, and address policy."""
        normalized = self.normalize_base_url(base_url)
        parsed = urlsplit(normalized)
        hostname = parsed.hostname or ""
        if (
            self.require_https
            and parsed.scheme != "https"
            and not self._host_is_allowed(hostname)
        ):
            raise ValueError(
                "A2A destination must use HTTPS unless its host is explicitly allowed"
            )
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        await self.resolve(hostname, port)
        return normalized


class PolicyResolver(AbstractResolver):
    """Resolve through OutboundPolicy at connection time to prevent DNS rebinding."""

    def __init__(self, policy: OutboundPolicy) -> None:
        self.policy = policy

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[ResolveResult]:
        addresses = await self.policy.resolve(host, port)
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": socket.AF_INET6 if ":" in address else socket.AF_INET,
                "proto": 0,
                "flags": 0,
            }
            for address in addresses
        ]

    async def close(self) -> None:
        return None


class A2AClient:
    """HTTP client for the A2A protocol."""

    def __init__(
        self,
        timeout: float = 30.0,
        outbound_policy: dict[str, Any] | None = None,
        authentication: dict[str, Any] | None = None,
    ) -> None:
        self.default_timeout = timeout
        self.outbound_policy = OutboundPolicy(outbound_policy)
        self.authentication = authentication or {}
        self._session: aiohttp.ClientSession | None = None

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            connector = aiohttp.TCPConnector(
                resolver=PolicyResolver(self.outbound_policy),
                ttl_dns_cache=0,
            )
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=self.default_timeout),
                connector=connector,
            )
        return self._session

    async def close(self) -> None:
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    def _credentials_for(self, base_url: str) -> dict[str, str] | None:
        peers = self.authentication.get("peers", {})
        normalized = self.outbound_policy.normalize_base_url(base_url)
        credentials = peers.get(normalized) or peers.get(base_url.rstrip("/"))
        return credentials if isinstance(credentials, dict) else None

    @staticmethod
    def _signed_headers(body: bytes, credentials: dict[str, str]) -> dict[str, str]:
        key_id = credentials.get("key_id", "")
        secret = credentials.get("secret", "")
        if not key_id or not secret:
            raise ValueError("A2A peer credentials require key_id and secret")
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        digest = hashlib.sha256(body).hexdigest()
        signing_input = f"{timestamp}\n{nonce}\n{digest}".encode()
        signature = hmac.new(secret.encode(), signing_input, hashlib.sha256).hexdigest()
        return {
            "X-A2A-Key-Id": key_id,
            "X-A2A-Timestamp": timestamp,
            "X-A2A-Nonce": nonce,
            "X-A2A-Signature": signature,
        }

    async def fetch_agent_card(self, base_url: str) -> dict[str, Any]:
        base_url = await self.outbound_policy.validate(base_url)
        url = f"{base_url}/.well-known/agent.json"
        session = await self._get_session()
        try:
            async with session.get(url, allow_redirects=False) as resp:
                if 300 <= resp.status < 400:
                    raise ConnectionError("A2A redirects are not allowed")
                if resp.status != 200:
                    raise ConnectionError(
                        f"Failed to fetch agent card from {url}: HTTP {resp.status}"
                    )
                return await resp.json()
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Connection failed to {url}: {e}") from e

    async def send_message(
        self,
        base_url: str,
        message_text: str,
        timeout: float | None = None,
        sender_url: str | None = None,
        sender_name: str | None = None,
    ) -> dict[str, Any]:
        base_url = await self.outbound_policy.validate(base_url)
        url = f"{base_url}/a2a/v1/message:send"
        payload: dict[str, Any] = {
            "message": {
                "role": "user",
                "parts": [{"text": message_text}],
            }
        }
        if sender_url:
            payload["sender_url"] = sender_url
        if sender_name:
            payload["sender_name"] = sender_name
        body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        headers = {"Content-Type": "application/json"}
        credentials = self._credentials_for(base_url)
        if credentials:
            headers.update(self._signed_headers(body, credentials))

        session = await self._get_session()
        request_timeout = aiohttp.ClientTimeout(total=timeout or self.default_timeout)
        try:
            async with session.post(
                url,
                data=body,
                headers=headers,
                timeout=request_timeout,
                allow_redirects=False,
            ) as resp:
                if 300 <= resp.status < 400:
                    raise ConnectionError("A2A redirects are not allowed")
                if resp.status >= 500:
                    error_text = await resp.text()
                    raise ConnectionError(
                        f"Server error from {url}: HTTP {resp.status} - {error_text[:200]}"
                    )
                return await resp.json()
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Connection failed to {url}: {e}") from e

    async def get_task_status(self, base_url: str, task_id: str) -> dict[str, Any]:
        base_url = await self.outbound_policy.validate(base_url)
        url = f"{base_url}/a2a/v1/tasks/{task_id}"
        session = await self._get_session()
        try:
            async with session.get(url, allow_redirects=False) as resp:
                if 300 <= resp.status < 400:
                    raise ConnectionError("A2A redirects are not allowed")
                if resp.status == 404:
                    raise ValueError(f"Task not found: {task_id}")
                if resp.status >= 400:
                    raise ConnectionError(
                        f"Error polling task {task_id} from {url}: HTTP {resp.status}"
                    )
                return await resp.json()
        except aiohttp.ClientError as e:
            raise ConnectionError(f"Connection failed to {url}: {e}") from e
