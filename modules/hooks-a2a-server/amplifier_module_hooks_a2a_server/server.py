"""A2A HTTP server — handles incoming requests from remote agents."""

import json
import logging
from typing import Any

from aiohttp import web
from amplifier_core.session import AmplifierSession

from .authentication import RequestAuthenticator

logger = logging.getLogger(__name__)


class A2AServer:
    """HTTP server that serves the Agent Card and handles A2A messages.

    Routes:
        GET  /.well-known/agent.json   — Agent Card (identity document)
        POST /a2a/v1/message:send      — Receive a message (Task 8)
        GET  /a2a/v1/tasks/{task_id}   — Poll task status (Task 9)
    """

    def __init__(
        self,
        registry: Any,
        card: dict[str, Any],
        coordinator: Any,
        config: dict[str, Any],
    ) -> None:
        self.registry = registry
        self.card = card
        self.coordinator = coordinator
        self.config = config
        self.port: int | None = None
        self.authenticator = RequestAuthenticator(config.get("authentication"))

        self.app = web.Application()
        self.app.router.add_get("/.well-known/agent.json", self.handle_agent_card)
        self.app.router.add_post("/a2a/v1/message:send", self.handle_send_message)
        self.app.router.add_get("/a2a/v1/tasks/{task_id}", self.handle_get_task)

        self._runner: web.AppRunner | None = None
        self._site: web.TCPSite | None = None

    async def start(self) -> None:
        """Start the HTTP server."""
        port = self.config.get("port", 8222)
        host = self.config.get("host", "0.0.0.0")

        runner = web.AppRunner(self.app)
        await runner.setup()
        site = web.TCPSite(runner, host, port)
        await site.start()

        self._runner = runner
        self._site = site

        # Resolve actual port (important when port=0 for OS assignment)
        sockets = site._server.sockets  # type: ignore[union-attr]
        if sockets:
            self.port = sockets[0].getsockname()[1]
        else:
            self.port = port

        logger.info("A2A server started on %s:%s", host, self.port)

    async def stop(self) -> None:
        """Stop the HTTP server."""
        if self._runner:
            await self._runner.cleanup()
            self._runner = None
            self._site = None
            logger.info("A2A server stopped")

    # --- Route handlers ---

    async def handle_agent_card(self, request: web.Request) -> web.Response:
        """GET /.well-known/agent.json — serve the Agent Card."""
        return web.json_response(self.card)

    async def handle_send_message(self, request: web.Request) -> web.Response:
        """POST /a2a/v1/message:send — receive a message from a remote agent.

        Phase 1: Mode C — spawn a child session to answer autonomously.
        """
        # Parse and validate request
        raw_body = await request.read()
        authenticated_peer, auth_error = self.authenticator.verify(request, raw_body)
        if auth_error is not None:
            return auth_error

        try:
            body = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return web.json_response({"error": "Invalid JSON"}, status=400)
        if not isinstance(body, dict):
            return web.json_response({"error": "Invalid JSON object"}, status=400)

        message = body.get("message")
        if not message or not isinstance(message.get("parts"), list):
            return web.json_response(
                {"error": "Missing or invalid 'message' with 'parts'"},
                status=400,
            )

        # Extract text from message parts
        text_parts = [
            part["text"]
            for part in message.get("parts", [])
            if isinstance(part, dict) and "text" in part
        ]
        text = " ".join(text_parts).strip()
        if not text:
            return web.json_response({"error": "Empty message text"}, status=400)

        # Extract sender info (used by contact check and confidence escalation)
        claimed_sender_url = body.get("sender_url", "")
        sender_name = body.get("sender_name", "Unknown Agent")
        if authenticated_peer is not None:
            if (
                claimed_sender_url
                and claimed_sender_url != authenticated_peer.sender_url
            ):
                return web.json_response(
                    {"error": "sender_url does not match authenticated identity"},
                    status=403,
                )
            sender_url = authenticated_peer.sender_url
        else:
            sender_url = claimed_sender_url

        # First-contact approval check (Phase 2)
        contact_store = getattr(self.registry, "contact_store", None)
        pending_queue = getattr(self.registry, "pending_queue", None)

        if contact_store is not None:
            if not sender_url:
                return web.json_response(
                    {"error": "Missing required field: sender_url"},
                    status=400,
                )
            sender_name = body.get("sender_name", "Unknown Agent")

            # Anonymous first-contact mode is an explicit onboarding mechanism.
            # It never grants the tier of a stored contact based on a claimed URL.
            is_authenticated = (
                authenticated_peer is not None or not self.authenticator.required
            )
            if not is_authenticated or not contact_store.is_known(sender_url):
                task_id = self.registry.create_task(message)
                if pending_queue is not None:
                    await pending_queue.add_approval(
                        task_id, sender_url, sender_name, message
                    )
                self.registry.update_task(task_id, "INPUT_REQUIRED")
                return web.json_response(self.registry.get_task(task_id))

            # Determine contact's trust tier (Phase 2)
            contact = contact_store.get_contact(sender_url)
            tier = contact["tier"] if contact else "known"
        else:
            if authenticated_peer is None and self.authenticator.required:
                return web.json_response(
                    {"error": "Authenticated contact store is required"}, status=503
                )
            tier = "trusted" if not self.authenticator.required else "known"

        # Mode A: known contacts get queued for user response
        if tier == "known":
            task_id = self.registry.create_task(message)
            self.registry.update_task(task_id, "INPUT_REQUIRED")

            if pending_queue is not None:
                await pending_queue.add_message(
                    task_id=task_id,
                    sender_url=sender_url,
                    sender_name=sender_name,
                    message=message,
                )

            return web.json_response(self.registry.get_task(task_id))

        # Create task in registry
        task_id = self.registry.create_task(message)
        self.registry.update_task(task_id, "WORKING")

        # Mode C: spawn child session to answer (trusted contacts)
        try:
            response_text = await self._spawn_child_session(task_id, text, tier=tier)

            # Confidence evaluation (only for trusted tier, and only if enabled)
            should_evaluate = tier == "trusted" and self.config.get(
                "confidence_evaluation", True
            )

            if should_evaluate:
                providers_map = getattr(self.coordinator, "mount_points", {}).get(
                    "providers", {}
                )
                # mount_points["providers"] is a dict keyed by module name,
                # not a list. Get the first available provider.
                provider_list = (
                    list(providers_map.values())
                    if isinstance(providers_map, dict)
                    else list(providers_map)
                    if providers_map
                    else []
                )
                if provider_list:
                    from .evaluation import evaluate_confidence

                    is_sufficient = await evaluate_confidence(
                        provider=provider_list[0],
                        question=text,
                        response=response_text,
                    )
                    if not is_sufficient:
                        # Escalate to Mode A: discard response, queue for user
                        logger.info(
                            "Mode C → A escalation: confidence check failed "
                            "for task %s",
                            task_id,
                        )
                        self.registry.update_task(task_id, "INPUT_REQUIRED")
                        pending_queue = getattr(self.registry, "pending_queue", None)
                        if pending_queue is not None:
                            await pending_queue.add_message(
                                task_id=task_id,
                                sender_url=sender_url,
                                sender_name=sender_name,
                                message=message,
                                escalated=True,
                            )
                        return web.json_response(self.registry.get_task(task_id))

            artifact = {"parts": [{"text": response_text}]}
            self.registry.update_task(
                task_id, "COMPLETED", artifacts=[artifact], attribution="autonomous"
            )
            return web.json_response(self.registry.get_task(task_id))
        except Exception as e:
            logger.exception("Mode C failed for task %s", task_id)
            self.registry.update_task(task_id, "FAILED", error=str(e))
            return web.json_response(self.registry.get_task(task_id), status=500)

    def _get_tools_for_tier(self, tier: str) -> list[dict]:
        """Get the filtered tool list for a given trust tier."""
        parent_tools = list(self.coordinator.config.get("tools", []))

        # Get tier config
        tier_config = self.config.get("trust_tiers", {}).get(tier, {})
        allowed_tools = tier_config.get("tools", None)

        # Secure defaults: remote sessions have no tools unless explicitly granted.
        if allowed_tools is None:
            allowed_tools = []

        if allowed_tools == "*":
            if not self.config.get("allow_unsafe_wildcard_tools", False):
                logger.warning(
                    "Ignoring trust_tiers.%s.tools='*' because "
                    "allow_unsafe_wildcard_tools is not enabled",
                    tier,
                )
                return []
            return parent_tools

        # Filter: only include tools whose module name is in the whitelist
        return [t for t in parent_tools if t.get("module") in allowed_tools]

    async def _spawn_child_session(
        self, task_id: str, prompt: str, tier: str = "trusted"
    ) -> str:
        """Spawn a child AmplifierSession to answer a prompt.

        Builds a child config from the parent (same providers, tools filtered
        by trust tier, but NO hooks to avoid recursive server starts). Returns
        the child session's response text.
        """
        parent_config = self.coordinator.config

        # Child gets same providers; tools filtered by trust tier; no hooks
        child_config = {
            "session": dict(parent_config.get("session", {})),
            "providers": list(parent_config.get("providers", [])),
            "tools": self._get_tools_for_tier(tier),
            # Deliberately omit "hooks" — child doesn't need a2a server
        }

        child_session_id = f"{self.coordinator.session_id}-a2a-{task_id[:8]}"

        child = AmplifierSession(
            config=child_config,
            session_id=child_session_id,
            parent_id=self.coordinator.session_id,
        )

        try:
            async with child:
                return await child.execute(prompt)
        except Exception as e:
            raise RuntimeError(f"Child session failed: {e}") from e

    async def handle_get_task(self, request: web.Request) -> web.Response:
        """GET /a2a/v1/tasks/{task_id} — poll task status."""
        task_id = request.match_info["task_id"]
        task = self.registry.get_task(task_id)
        if task is None:
            return web.json_response(
                {"error": f"Task not found: {task_id}"}, status=404
            )
        return web.json_response(task)
