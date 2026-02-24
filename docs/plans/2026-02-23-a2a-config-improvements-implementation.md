# A2A Config Improvements Implementation Plan

> **Execution:** Use the subagent-driven-development workflow to implement this plan.

**Goal:** Simplify configuration so the only configs most users need are `port` and `agent_name` — by deriving sender identity from the hook's Agent Card, adding smart defaults, improving port collision errors, and adding `whoami`/`add_contact` operations for easy agent-to-agent connection.

**Architecture:** The hook owns identity and server lifecycle. After building the Agent Card, mount() stores it on `registry.card`. The tool reads identity from the registry instead of its own config. Two new operations (`whoami`, `add_contact`) let users discover and share their address in-session rather than editing YAML. Port collisions get clear error messages and set `registry.server_running = False` so `whoami` can report the problem.

**Tech Stack:** Python 3.11+, aiohttp (HTTP client/server), amplifier-core (peer dependency), pytest + pytest-asyncio (testing)

**Design Doc:** `docs/plans/2026-02-23-a2a-config-improvements-design.md` (in git at commit `725e5ee`)

---

## Prerequisites

**Working directory:** `/home/bkrabach/dev/a2a-investigate/`

All paths in this plan are relative to `amplifier-bundle-a2a/` unless stated otherwise.

### Dev Environment Setup

The Phase 1–3 environment is already set up. No new dependencies needed.

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
source .venv/bin/activate
```

### Key API Reference (from amplifier-core)

| API | Signature | Notes |
|-----|-----------|-------|
| Tool protocol | `name: str`, `description: str`, `input_schema: dict`, `async execute(input: dict) -> ToolResult` | Duck-typed, no inheritance |
| ToolResult | `ToolResult(success=True, output=...)` or `ToolResult(success=False, error={"message": "..."})` | Import: `from amplifier_core import ToolResult` |
| Register capability | `coordinator.register_capability("a2a.registry", obj)` | NOT `coordinator.register()` |
| Get capability | `coordinator.get_capability("a2a.registry")` | Returns `None` if not registered |
| Register cleanup | `coordinator.register_cleanup(async_fn)` | Called in LIFO order on session end |

### Existing Codebase Reference

**Key files modified in this plan:**

| File | Current State |
|------|---------------|
| `modules/hooks-a2a-server/.../registry.py` | `A2ARegistry` with `contact_store`, `pending_queue`, `deferred_ids` attributes. No `card` or `server_running`. |
| `modules/hooks-a2a-server/.../card.py` | `build_agent_card(config)` — hardcoded default `"Amplifier Agent"`. No `getpass` logic. |
| `modules/hooks-a2a-server/.../__init__.py` | `mount()` builds card but doesn't store on registry. OSError handling logs generic warning and returns. |
| `modules/tool-a2a/.../__init__.py` | `A2ATool` with 12 operations. `_op_send` reads `sender_url`/`sender_name` from `self.config`. No `whoami` or `add_contact`. |
| `modules/tool-a2a/.../client.py` | `A2AClient.send_message()` already accepts `sender_url`/`sender_name` params. **No changes needed.** |
| `context/a2a-instructions.md` | Documents 12 operations. Missing `whoami` and `add_contact`. |
| `behaviors/a2a.yaml` | Tool config is `{}`. Hook config has `agent_name: "Amplifier Agent"`. No `sender_url`/`sender_name` in tool config. |
| `README.md` | Installation example shows `sender_url` and `sender_name` in tool config section. |

### Existing Test Conventions

- Tests use `pytest-asyncio` with `asyncio_mode = auto` (no `@pytest.mark.asyncio` needed on async test methods inside classes)
- Tests use `from unittest.mock import AsyncMock, MagicMock, patch`
- `_make_mock_coordinator()` and `_make_mock_registry()` helper pattern for test setup
- Imports inside test methods (not at module level) for module-specific imports
- `assert result.output is not None` / `assert result.error is not None` type-narrowing guards before subscript access

### Run All Tests Command

After any task, verify no regressions:

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a && source .venv/bin/activate
pytest -v
```

Expected before this plan: ~260 tests, all passing.

---

## Task 1: Add `card` and `server_running` Attributes to A2ARegistry

Pure data layer — add two new attributes to `A2ARegistry.__init__`. No behavior change. These will be set by `mount()` in later tasks.

**Files:**

- Modify: `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/registry.py`
- Modify: `modules/hooks-a2a-server/tests/test_registry.py`

### Step 1: Write the failing tests

Add to the **end** of `modules/hooks-a2a-server/tests/test_registry.py`:

```python
class TestCardAndServerRunningAttributes:
    """Tests for card and server_running attributes on registry."""

    def test_registry_card_defaults_to_none(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.card is None

    def test_registry_server_running_defaults_to_true(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.server_running is True

    def test_set_and_get_card(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        card = {"name": "Test Agent", "url": "http://localhost:8222"}
        registry.card = card
        assert registry.card is card
        assert registry.card["name"] == "Test Agent"
        assert registry.card["url"] == "http://localhost:8222"

    def test_set_server_running_false(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        registry.server_running = False
        assert registry.server_running is False
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_registry.py::TestCardAndServerRunningAttributes -v
```

Expected: FAIL — `AttributeError: 'A2ARegistry' object has no attribute 'card'`

### Step 3: Update the registry implementation

In `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/registry.py`, find:

```python
        self.contact_store: Any | None = None  # Set by mount()
        self.pending_queue: Any | None = None  # Set by mount()
        self.deferred_ids: set[str] = set()
```

Replace with:

```python
        self.contact_store: Any | None = None  # Set by mount()
        self.pending_queue: Any | None = None  # Set by mount()
        self.deferred_ids: set[str] = set()
        self.card: dict[str, Any] | None = None  # Set by mount() after building Agent Card
        self.server_running: bool = True  # Set to False by mount() on port collision
```

### Step 4: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_registry.py -v
```

Expected: All registry tests PASS (25 existing + 4 new).

### Step 5: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass, count increases by 4.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/registry.py
git add amplifier-bundle-a2a/modules/hooks-a2a-server/tests/test_registry.py
git commit -m "feat(a2a): add card and server_running attributes to A2ARegistry"
```

---

## Task 2: Smart Default for Agent Name in card.py

Change `build_agent_card` to derive `agent_name` from `getpass.getuser()` when not explicitly configured. Default: `f"{username}'s Agent"`. Fallback if `getpass.getuser()` fails: `"Amplifier Agent"`.

**Files:**

- Modify: `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/card.py`
- Modify: `modules/hooks-a2a-server/tests/test_card.py`

### Step 1: Write the failing tests

Add to the **end** of `modules/hooks-a2a-server/tests/test_card.py`:

```python
class TestSmartAgentNameDefault:
    """Tests for smart agent name default from username."""

    def test_default_name_uses_username(self):
        from unittest.mock import patch

        from amplifier_module_hooks_a2a_server.card import build_agent_card

        with patch("amplifier_module_hooks_a2a_server.card.getpass") as mock_getpass:
            mock_getpass.getuser.return_value = "bkrabach"
            card = build_agent_card({})
            assert card["name"] == "bkrabach's Agent"

    def test_explicit_agent_name_overrides_default(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({"agent_name": "Custom Name"})
        assert card["name"] == "Custom Name"

    def test_fallback_when_getuser_fails(self):
        from unittest.mock import patch

        from amplifier_module_hooks_a2a_server.card import build_agent_card

        with patch("amplifier_module_hooks_a2a_server.card.getpass") as mock_getpass:
            mock_getpass.getuser.side_effect = Exception("no user")
            card = build_agent_card({})
            assert card["name"] == "Amplifier Agent"
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_card.py::TestSmartAgentNameDefault -v
```

Expected: FAIL — patching `amplifier_module_hooks_a2a_server.card.getpass` fails because `getpass` is not imported in `card.py` yet.

### Step 3: Update card.py implementation

Replace the **entire contents** of `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/card.py`:

```python
"""Agent Card generation — builds the A2A identity document from config."""

import getpass
import logging
from typing import Any

logger = logging.getLogger(__name__)


def _default_agent_name() -> str:
    """Derive a default agent name from the system username.

    Returns "{username}'s Agent" or "Amplifier Agent" as fallback.
    """
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
    base_url = config.get("base_url", f"http://{host}:{port}")

    # Use configured agent_name, or derive from system username
    agent_name = config.get("agent_name") or _default_agent_name()

    return {
        "name": agent_name,
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
```

### Step 4: Update existing test for new default behavior

The existing test `test_default_values` in `TestBuildAgentCard` asserts `card["name"] == "Amplifier Agent"`, but now the default will be `"{username}'s Agent"`. Update it to account for the dynamic default.

In `modules/hooks-a2a-server/tests/test_card.py`, find:

```python
    def test_default_values(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({})
        assert card["name"] == "Amplifier Agent"
```

Replace with:

```python
    def test_default_values(self):
        from amplifier_module_hooks_a2a_server.card import build_agent_card

        card = build_agent_card({})
        # Default name is now derived from username, so just check it's a non-empty string
        assert isinstance(card["name"], str)
        assert len(card["name"]) > 0
        assert card["description"] == "An Amplifier-powered agent"
        assert card["version"] == "1.0"
        assert card["capabilities"] == {"streaming": False, "realtimeResponse": False}
        assert card["skills"] == []
```

### Step 5: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_card.py -v
```

Expected: All 10 tests PASS (7 existing + 3 new).

### Step 6: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass. (Any test elsewhere that asserts `card["name"] == "Amplifier Agent"` on a default config will need updating — check for failures and fix if found.)

### Step 7: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/card.py
git add amplifier-bundle-a2a/modules/hooks-a2a-server/tests/test_card.py
git commit -m "feat(a2a): smart default agent name from system username"
```

---

## Task 3: Improve Port Collision Error Message

Update `mount()` in the hook's `__init__.py`: on `OSError` during `server.start()`, set `registry.server_running = False` and log a prominent warning with the port number and remediation steps. Currently it logs a generic warning and returns.

**Files:**

- Modify: `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py`
- Modify: `modules/hooks-a2a-server/tests/test_server.py`

### Step 1: Write the failing tests

Add to the **end** of `modules/hooks-a2a-server/tests/test_server.py`:

```python
class TestPortCollision:
    """Tests for port collision error handling in mount()."""

    async def test_port_collision_sets_server_running_false(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from amplifier_module_hooks_a2a_server import mount

        coordinator = MagicMock()
        coordinator.parent_id = None
        coordinator.session_id = "test-session"
        coordinator.hooks = MagicMock()

        # Use a port that will fail to bind
        config = {"port": 0, "host": "127.0.0.1", "agent_name": "Test"}

        # Mock A2AServer.start to raise OSError
        with patch(
            "amplifier_module_hooks_a2a_server.server.A2AServer.start",
            side_effect=OSError("Address already in use"),
        ):
            await mount(coordinator, config)

        # Verify registry was still registered as capability
        coordinator.register_capability.assert_called_once()
        registry = coordinator.register_capability.call_args[0][1]
        assert registry.server_running is False

    async def test_port_collision_logs_prominent_message(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from amplifier_module_hooks_a2a_server import mount

        coordinator = MagicMock()
        coordinator.parent_id = None
        coordinator.session_id = "test-session"
        coordinator.hooks = MagicMock()

        config = {"port": 8222, "host": "127.0.0.1", "agent_name": "Test"}

        with patch(
            "amplifier_module_hooks_a2a_server.server.A2AServer.start",
            side_effect=OSError("Address already in use"),
        ), patch("amplifier_module_hooks_a2a_server.logger") as mock_logger:
            await mount(coordinator, config)

        # Verify the warning mentions the port number
        mock_logger.warning.assert_called()
        warning_msg = str(mock_logger.warning.call_args)
        assert "8222" in warning_msg

    async def test_port_collision_still_registers_capability(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from amplifier_module_hooks_a2a_server import mount

        coordinator = MagicMock()
        coordinator.parent_id = None
        coordinator.session_id = "test-session"
        coordinator.hooks = MagicMock()

        config = {"port": 0, "host": "127.0.0.1", "agent_name": "Test"}

        with patch(
            "amplifier_module_hooks_a2a_server.server.A2AServer.start",
            side_effect=OSError("Address already in use"),
        ):
            await mount(coordinator, config)

        # Capability must still be registered (so whoami can report the issue)
        coordinator.register_capability.assert_called_once()
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_server.py::TestPortCollision -v
```

Expected: FAIL — `registry.server_running` is `True` (never set to `False`) and `register_capability` is called before the error happens but the test checks for registry attributes that don't exist yet (they do now after Task 1).

### Step 3: Update mount() implementation

In `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py`, find:

```python
    # Create and start the HTTP server
    server = A2AServer(registry, card, coordinator, config)
    try:
        await server.start()
    except OSError as e:
        logger.warning("A2A server failed to start (port conflict?): %s", e)
        return
```

Replace with:

```python
    # Create and start the HTTP server
    server = A2AServer(registry, card, coordinator, config)
    try:
        await server.start()
    except OSError as e:
        port = config.get("port", 8222)
        registry.server_running = False
        logger.warning(
            "\n"
            "╔══════════════════════════════════════════════════════════╗\n"
            "║  A2A SERVER FAILED TO START — PORT %s IN USE            ║\n"
            "╠══════════════════════════════════════════════════════════╣\n"
            "║  Another process is using port %s.                      ║\n"
            "║  To fix: change 'port' in your hook config to a         ║\n"
            "║  different value (e.g., 8223, 8224).                    ║\n"
            "║                                                         ║\n"
            "║  A2A messaging will not work in this session.            ║\n"
            "║  Use a2a(operation='whoami') to check status.            ║\n"
            "╚══════════════════════════════════════════════════════════╝",
            port,
            port,
        )
        return
```

### Step 4: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_server.py -v
```

Expected: All server tests PASS (existing + 3 new).

### Step 5: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py
git add amplifier-bundle-a2a/modules/hooks-a2a-server/tests/test_server.py
git commit -m "feat(a2a): improve port collision error message and set server_running=False"
```

---

## Task 4: Store Card on Registry After Build

Update `mount()` to set `registry.card = card` after building the Agent Card. This is what enables the tool to derive sender identity in Task 5 and `whoami` to return identity info in Task 6.

**Files:**

- Modify: `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py`
- Modify: `modules/hooks-a2a-server/tests/test_server.py`

### Step 1: Write the failing test

Add to the **end** of `modules/hooks-a2a-server/tests/test_server.py`:

```python
class TestCardOnRegistry:
    """Tests for storing the Agent Card on the registry."""

    async def test_mount_stores_card_on_registry(self):
        from amplifier_module_hooks_a2a_server import mount

        coordinator = _make_mock_coordinator(parent_id=None)
        config = {
            "port": 0,
            "host": "127.0.0.1",
            "agent_name": "Card Test Agent",
        }
        await mount(coordinator, config)

        # Get the registry from the register_capability call
        coordinator.register_capability.assert_called_once()
        registry = coordinator.register_capability.call_args[0][1]

        # Verify card is stored on registry
        assert registry.card is not None
        assert registry.card["name"] == "Card Test Agent"
        assert "url" in registry.card

        # Clean up: call the cleanup function
        cleanup_fn = coordinator.register_cleanup.call_args[0][0]
        await cleanup_fn()

    async def test_card_stored_even_on_port_collision(self):
        from unittest.mock import patch

        from amplifier_module_hooks_a2a_server import mount

        coordinator = _make_mock_coordinator(parent_id=None)
        config = {
            "port": 9999,
            "host": "127.0.0.1",
            "agent_name": "Collision Test",
        }

        with patch(
            "amplifier_module_hooks_a2a_server.server.A2AServer.start",
            side_effect=OSError("Address already in use"),
        ):
            await mount(coordinator, config)

        registry = coordinator.register_capability.call_args[0][1]
        assert registry.card is not None
        assert registry.card["name"] == "Collision Test"
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_server.py::TestCardOnRegistry -v
```

Expected: FAIL — `registry.card` is `None` because `mount()` doesn't set it yet.

### Step 3: Update mount() to store card on registry

In `modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py`, find:

```python
    # Build the Agent Card
    card = build_agent_card(config)

    # Register shared state so tool-a2a can access it
    coordinator.register_capability("a2a.registry", registry)
```

Replace with:

```python
    # Build the Agent Card and store on registry
    card = build_agent_card(config)
    registry.card = card

    # Register shared state so tool-a2a can access it
    coordinator.register_capability("a2a.registry", registry)
```

### Step 4: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/hooks-a2a-server/tests/test_server.py -v
```

Expected: All server tests PASS.

### Step 5: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/hooks-a2a-server/amplifier_module_hooks_a2a_server/__init__.py
git add amplifier-bundle-a2a/modules/hooks-a2a-server/tests/test_server.py
git commit -m "feat(a2a): store Agent Card on registry after build"
```

---

## Task 5: Tool Derives Sender Identity from registry.card

Change `_op_send` in `tool-a2a/__init__.py` to read `sender_url` from `registry.card["url"]` and `sender_name` from `registry.card["name"]` first, then fall back to `self.config.get("sender_url")` / `self.config.get("sender_name")`.

**Files:**

- Modify: `modules/tool-a2a/amplifier_module_tool_a2a/__init__.py`
- Modify: `modules/tool-a2a/tests/test_tool_a2a.py`

### Step 1: Write the failing tests

Add to the **end** of `modules/tool-a2a/tests/test_tool_a2a.py`:

```python
class TestSenderIdentityDerivation:
    """Tests for deriving sender identity from registry.card."""

    async def test_send_uses_registry_card_for_sender_identity(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "My Agent",
            "url": "http://my-machine:8222",
        }
        registry.resolve_agent_url = MagicMock(
            return_value="http://remote:8222"
        )

        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            return_value={"name": "Remote", "capabilities": {"realtimeResponse": True}}
        )
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute(
            {"operation": "send", "agent": "http://remote:8222", "message": "Hello"}
        )

        # Verify send_message was called with sender identity from card
        tool.client.send_message.assert_called_once()
        call_kwargs = tool.client.send_message.call_args
        assert call_kwargs[1]["sender_url"] == "http://my-machine:8222"
        assert call_kwargs[1]["sender_name"] == "My Agent"

    async def test_send_falls_back_to_config_when_no_card(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = None
        registry.resolve_agent_url = MagicMock(
            return_value="http://remote:8222"
        )

        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {
            "sender_url": "http://fallback:8222",
            "sender_name": "Fallback Agent",
        })
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            return_value={"name": "Remote", "capabilities": {"realtimeResponse": True}}
        )
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute(
            {"operation": "send", "agent": "http://remote:8222", "message": "Hello"}
        )

        call_kwargs = tool.client.send_message.call_args
        assert call_kwargs[1]["sender_url"] == "http://fallback:8222"
        assert call_kwargs[1]["sender_name"] == "Fallback Agent"

    async def test_send_uses_card_over_config(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "Card Agent",
            "url": "http://card-machine:8222",
        }
        registry.resolve_agent_url = MagicMock(
            return_value="http://remote:8222"
        )

        coordinator = _make_mock_coordinator(registry=registry)
        # Config also has sender values — card should win
        tool = A2ATool(coordinator, {
            "sender_url": "http://config-machine:8222",
            "sender_name": "Config Agent",
        })
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            return_value={"name": "Remote", "capabilities": {"realtimeResponse": True}}
        )
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute(
            {"operation": "send", "agent": "http://remote:8222", "message": "Hello"}
        )

        call_kwargs = tool.client.send_message.call_args
        assert call_kwargs[1]["sender_url"] == "http://card-machine:8222"
        assert call_kwargs[1]["sender_name"] == "Card Agent"
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/tool-a2a/tests/test_tool_a2a.py::TestSenderIdentityDerivation -v
```

Expected: FAIL — `sender_url` is read from `self.config`, not from `registry.card`.

### Step 3: Update `_op_send` sender identity logic

In `modules/tool-a2a/amplifier_module_tool_a2a/__init__.py`, find:

```python
        # Determine our sender identity for the remote server's contact check
        sender_url = None
        sender_name = None
        if self.registry:
            # Get our server's URL from the agent card config (if available)
            sender_url = self.config.get("sender_url")
            sender_name = self.config.get("sender_name")
```

Replace with:

```python
        # Determine our sender identity for the remote server's contact check.
        # Priority: registry.card (hook-owned identity) > config (escape hatch)
        sender_url = None
        sender_name = None
        if self.registry and getattr(self.registry, "card", None):
            sender_url = self.registry.card.get("url")
            sender_name = self.registry.card.get("name")
        if not sender_url:
            sender_url = self.config.get("sender_url")
        if not sender_name:
            sender_name = self.config.get("sender_name")
```

### Step 4: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/tool-a2a/tests/test_tool_a2a.py -v
```

Expected: All tool tests PASS (existing + 3 new).

### Step 5: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/tool-a2a/amplifier_module_tool_a2a/__init__.py
git add amplifier-bundle-a2a/modules/tool-a2a/tests/test_tool_a2a.py
git commit -m "feat(a2a): derive sender identity from registry.card, fallback to config"
```

---

## Task 6: Add `whoami` Tool Operation

New operation that returns the agent's identity from `registry.card`: name, url, port, and server status. If the server isn't running, include a message explaining why.

**Files:**

- Modify: `modules/tool-a2a/amplifier_module_tool_a2a/__init__.py`
- Modify: `modules/tool-a2a/tests/test_tool_a2a.py`

### Step 1: Write the failing tests

Add to the **end** of `modules/tool-a2a/tests/test_tool_a2a.py`:

```python
class TestWhoamiOperation:
    """Tests for the whoami operation."""

    async def test_whoami_returns_identity(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "bkrabach's Agent",
            "url": "http://0.0.0.0:8222",
            "capabilities": {"realtimeResponse": False},
        }
        registry.server_running = True
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["name"] == "bkrabach's Agent"
        assert result.output["url"] == "http://0.0.0.0:8222"
        assert result.output["server_running"] is True

    async def test_whoami_reports_server_not_running(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "bkrabach's Agent",
            "url": "http://0.0.0.0:8222",
            "capabilities": {"realtimeResponse": False},
        }
        registry.server_running = False
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["server_running"] is False
        assert "message" in result.output

    async def test_whoami_without_registry(self):
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(registry=None)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is False
        assert result.error is not None

    async def test_whoami_without_card(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = None
        registry.server_running = True
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is False
        assert result.error is not None
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/tool-a2a/tests/test_tool_a2a.py::TestWhoamiOperation -v
```

Expected: FAIL — `Unknown operation: whoami`

### Step 3: Add whoami to schema, description, and dispatch

In `modules/tool-a2a/amplifier_module_tool_a2a/__init__.py`:

**3a.** Update the module docstring. Find:

```python
  - defer: defer a pending message (Mode B → Mode A downgrade)
```

Add after it:

```python
  - whoami: show this agent's identity and URL for sharing
  - add_contact: connect to a remote agent by URL
```

**3b.** Update the `description` property. Find:

```python
            "'defer' (defer a pending message for later)."
```

Replace with:

```python
            "'defer' (defer a pending message for later), "
            "'whoami' (show your identity and URL for sharing), "
            "'add_contact' (connect to a remote agent by URL)."
```

**3c.** Update the `input_schema` property. Find the `"enum"` list:

```python
                    "enum": [
                        "agents",
                        "card",
                        "send",
                        "status",
                        "discover",
                        "approve",
                        "block",
                        "contacts",
                        "trust",
                        "respond",
                        "dismiss",
                        "defer",
                    ],
```

Replace with:

```python
                    "enum": [
                        "agents",
                        "card",
                        "send",
                        "status",
                        "discover",
                        "approve",
                        "block",
                        "contacts",
                        "trust",
                        "respond",
                        "dismiss",
                        "defer",
                        "whoami",
                        "add_contact",
                    ],
```

Also add a `url` property to `input_schema["properties"]`. Find:

```python
                "tier": {
                    "type": "string",
                    "description": (
                        "Trust tier: 'known' or 'trusted' "
                        "(used by 'approve' and 'trust')"
                    ),
                },
```

Add after it:

```python
                "url": {
                    "type": "string",
                    "description": (
                        "URL of a remote agent "
                        "(required for 'add_contact')"
                    ),
                },
```

**3d.** Add dispatch in `execute`. Find:

```python
            elif operation == "defer":
                return await self._op_defer(input)
```

Add after it:

```python
            elif operation == "whoami":
                return await self._op_whoami()
            elif operation == "add_contact":
                return await self._op_add_contact(input)
```

**3e.** Add the `_op_whoami` method. Add it after the `_op_defer` method:

```python
    async def _op_whoami(self) -> ToolResult:
        """Return this agent's identity and URL for sharing."""
        if not self.registry:
            return ToolResult(
                success=False,
                error={"message": "A2A registry not available"},
            )

        card = getattr(self.registry, "card", None)
        if not card:
            return ToolResult(
                success=False,
                error={"message": "Agent Card not available — server may not have started"},
            )

        server_running = getattr(self.registry, "server_running", True)
        result: dict[str, Any] = {
            "name": card.get("name", "Unknown"),
            "url": card.get("url", "Unknown"),
            "capabilities": card.get("capabilities", {}),
            "server_running": server_running,
        }

        if not server_running:
            result["message"] = (
                "The A2A server is NOT running — the port was likely in use. "
                "Other agents cannot reach you. Change 'port' in your hook "
                "config and restart the session."
            )

        return ToolResult(success=True, output=result)
```

### Step 4: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/tool-a2a/tests/test_tool_a2a.py::TestWhoamiOperation -v
```

Expected: All 4 tests PASS.

### Step 5: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/tool-a2a/amplifier_module_tool_a2a/__init__.py
git add amplifier-bundle-a2a/modules/tool-a2a/tests/test_tool_a2a.py
git commit -m "feat(a2a): add whoami operation to show agent identity and URL"
```

---

## Task 7: Add `add_contact` Tool Operation

New operation: takes `url` param, fetches Agent Card from the URL, extracts name/capabilities, adds to contacts as "known" tier (configurable via `tier` param), caches the card, returns confirmation.

**Files:**

- Modify: `modules/tool-a2a/amplifier_module_tool_a2a/__init__.py`
- Modify: `modules/tool-a2a/tests/test_tool_a2a.py`

### Step 1: Write the failing tests

Add to the **end** of `modules/tool-a2a/tests/test_tool_a2a.py`:

```python
class TestAddContactOperation:
    """Tests for the add_contact operation."""

    async def test_add_contact_fetches_card_and_adds(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = AsyncMock()
        registry.contact_store.add_contact = AsyncMock()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            return_value={
                "name": "Sarah's Agent",
                "url": "http://sarah:8222",
                "capabilities": {"realtimeResponse": False},
            }
        )

        result = await tool.execute(
            {"operation": "add_contact", "url": "http://sarah:8222"}
        )

        assert result.success is True
        assert result.output is not None
        # Verify contact was added
        registry.contact_store.add_contact.assert_called_once_with(
            "http://sarah:8222", "Sarah's Agent", tier="known"
        )
        # Verify card was cached
        registry.cache_card.assert_called_once()

    async def test_add_contact_with_trusted_tier(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = AsyncMock()
        registry.contact_store.add_contact = AsyncMock()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            return_value={
                "name": "Trusted Agent",
                "url": "http://trusted:8222",
                "capabilities": {},
            }
        )

        result = await tool.execute(
            {"operation": "add_contact", "url": "http://trusted:8222", "tier": "trusted"}
        )

        assert result.success is True
        registry.contact_store.add_contact.assert_called_once_with(
            "http://trusted:8222", "Trusted Agent", tier="trusted"
        )

    async def test_add_contact_requires_url(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "add_contact"})
        assert result.success is False
        assert result.error is not None
        assert "url" in result.error["message"].lower()

    async def test_add_contact_handles_unreachable_agent(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = AsyncMock()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            side_effect=Exception("Connection refused")
        )

        result = await tool.execute(
            {"operation": "add_contact", "url": "http://unreachable:8222"}
        )

        # The execute() method catches all exceptions, so this should be a failure
        assert result.success is False

    async def test_add_contact_without_contact_store(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "add_contact", "url": "http://remote:8222"}
        )
        assert result.success is False
```

### Step 2: Run tests to verify they fail

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/tool-a2a/tests/test_tool_a2a.py::TestAddContactOperation -v
```

Expected: FAIL — `Unknown operation: add_contact` (dispatch was added in Task 6 step 3d, but the method doesn't exist yet)

### Step 3: Add the `_op_add_contact` method

In `modules/tool-a2a/amplifier_module_tool_a2a/__init__.py`, add the method after `_op_whoami`:

```python
    async def _op_add_contact(self, input: dict[str, Any]) -> ToolResult:
        """Add a remote agent as a contact by fetching their Agent Card."""
        url = input.get("url", "").strip()
        if not url:
            return ToolResult(
                success=False,
                error={"message": "URL required — provide the remote agent's URL"},
            )

        if not self.registry:
            return ToolResult(
                success=False,
                error={"message": "A2A registry not available"},
            )

        contact_store = getattr(self.registry, "contact_store", None)
        if not contact_store:
            return ToolResult(
                success=False,
                error={"message": "Contact store not available"},
            )

        # Fetch the remote agent's card to get their name
        card = await self.client.fetch_agent_card(url)
        agent_name = card.get("name", "Unknown Agent")

        # Add to contacts
        tier = input.get("tier", "known")
        await contact_store.add_contact(url, agent_name, tier=tier)

        # Cache the card
        self.registry.cache_card(url, card)

        capabilities = card.get("capabilities", {})
        realtime = capabilities.get("realtimeResponse", False)

        return ToolResult(
            success=True,
            output={
                "added": True,
                "name": agent_name,
                "url": url,
                "tier": tier,
                "realtimeResponse": realtime,
                "message": (
                    f"Added {agent_name} ({url}) as a '{tier}' contact. "
                    f"You can now send them messages with "
                    f"a2a(operation='send', agent='{url}', message='...')."
                ),
            },
        )
```

### Step 4: Run tests to verify they pass

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest modules/tool-a2a/tests/test_tool_a2a.py::TestAddContactOperation -v
```

Expected: All 5 tests PASS.

### Step 5: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass.

### Step 6: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/modules/tool-a2a/amplifier_module_tool_a2a/__init__.py
git add amplifier-bundle-a2a/modules/tool-a2a/tests/test_tool_a2a.py
git commit -m "feat(a2a): add add_contact operation to connect agents by URL"
```

---

## Task 8: Update LLM Instructions, Behavior YAML, and README

Add `whoami` and `add_contact` to `a2a-instructions.md`. Document the connection flow. Update `behaviors/a2a.yaml` to remove `agent_name` hardcoded default (runtime-derived now). Update `README.md` to remove `sender_url`/`sender_name` from the installation example and document the new operations.

**Files:**

- Modify: `context/a2a-instructions.md`
- Modify: `behaviors/a2a.yaml`
- Modify: `README.md`

### Step 1: Update `context/a2a-instructions.md`

Replace the **entire contents** of `context/a2a-instructions.md`:

```markdown
# A2A — Agent-to-Agent Communication

You have access to the `a2a` tool for communicating with remote Amplifier agents on the network.

## Quick Start — Connecting Two Agents

1. **Find your address:** `a2a(operation="whoami")` — returns your name, URL, and port
2. **Share your URL** with the other person (via text, chat, verbally)
3. **They add you:** `a2a(operation="add_contact", url="http://your-machine:8222")`
4. **You add them:** `a2a(operation="add_contact", url="http://their-machine:8222")`
5. **Send messages:** `a2a(operation="send", agent="http://their-machine:8222", message="Hello!")`

## Operations

### Identity & Connection
- **`whoami`** — Show your agent's identity, URL, and server status. Use this to find your address for sharing.
- **`add_contact`** — Connect to a remote agent by URL. Fetches their Agent Card, adds to contacts. Requires `url`. Optional `tier` (default "known").

### Sending Messages
- **`agents`** — List all known remote agents from all sources (configured, discovered, contacts).
- **`discover`** — Browse the local network for agents via mDNS. Optional `timeout` (seconds, default 2).
- **`card`** — Fetch a remote agent's identity card. Requires `agent` (name or URL).
- **`send`** — Send a message to a remote agent. Requires `agent` and `message`. Optional `blocking` (default true), `timeout` (seconds, default 30).
- **`status`** — Check the status of a previously sent async task. Requires `agent` and `task_id`.

### Handling Incoming Messages
- **`respond`** — Reply to a pending incoming message. Requires `task_id` and `message`.
- **`dismiss`** — Dismiss a pending incoming message. Requires `task_id`.
- **`defer`** — Defer an incoming message for later ("not now"). Requires `task_id`. The message stays in your queue and can be responded to later.

### Managing Contacts
- **`approve`** — Approve a new agent requesting access. Requires `agent` (URL). Optional `tier` (default "known").
- **`block`** — Block a new agent requesting access. Requires `agent` (URL).
- **`contacts`** — List all known contacts with their trust tiers.
- **`trust`** — Change a contact's trust tier. Requires `agent` (URL) and `tier` ("trusted" or "known").

## How It Works

### Connecting Agents
The typical flow is: ask for your address with `whoami`, share it with the other person out-of-band (text, chat, verbal), then they use `add_contact` to connect. No YAML editing required.

### Configuration
Most users only need two config values:
- **`port`** (default 8222) — change if another instance is using the same port
- **`agent_name`** — defaults to your system username (e.g., "bkrabach's Agent")

### Live Message Delivery
Messages from remote agents appear automatically in your context during active sessions. You don't need to poll — incoming requests and responses are injected before each of your turns.

### Sending and Receiving Responses
1. Call `a2a(operation="agents")` to see available agents
2. Call `a2a(operation="send", agent="Agent Name", message="your question")` to communicate
3. If the response is immediate (COMPLETED), relay it to the user
4. If INPUT_REQUIRED, tell the user and check back later — or the response will appear automatically when it arrives

### Async vs Real-Time Agents
Some agents are **async** (e.g., CLI sessions) — they can only see your message when their user next interacts. The `send` operation detects this automatically from the remote agent's card and switches to non-blocking mode. When this happens, you'll see a `_note` field explaining the situation. Tell the user their message was delivered and the response will arrive when the other person is available — don't make them wait.

### Response Attribution
Responses include how they were generated:
- **"autonomous"** — The remote agent answered without human involvement
- **"user_response"** — The remote user answered directly
- **"escalated_user_response"** — The agent tried but couldn't answer; the user responded
- **"dismissed"** — The remote user declined to answer

Relay the attribution naturally: "Sarah's agent answered autonomously" or "Sarah replied personally."

### Handling Incoming Requests
- Pending messages and approval requests appear automatically in your context
- Use `respond` to reply, `dismiss` to reject, or `defer` to handle later
- Deferred messages stay in your queue — you can respond to them anytime

### Discovery
- Call `a2a(operation="discover")` to find agents on the local network
- Agents on other networks (Tailscale, VPN) must be connected via `add_contact` with their URL

## Important

- Messages are sent to remote agents on other devices — they may be controlled by other people
- Unknown agents must be approved before they can send you messages
- Trusted contacts get autonomous responses; known contacts require your input
- Blocking sends wait up to 30 seconds by default; use `blocking=false` for fire-and-forget
- Use `whoami` to find your address — share it out-of-band for cross-network connections
```

### Step 2: Update `behaviors/a2a.yaml`

In `behaviors/a2a.yaml`, find:

```yaml
    config:
      port: 8222
      agent_name: "Amplifier Agent"
      agent_description: "An Amplifier-powered agent"
      skills: []
      known_agents: []
```

Replace with:

```yaml
    config:
      port: 8222
      # agent_name defaults to "$USER's Agent" at runtime
      agent_description: "An Amplifier-powered agent"
      skills: []
      known_agents: []
```

### Step 3: Update `README.md` installation example

In `README.md`, find the installation example (lines 72–99):

```yaml
hooks:
  - module: hooks-a2a-server
    config:
      port: 8222
      agent_name: "My Agent"
      agent_description: "My personal assistant"
      discovery:
        mdns: true
      known_agents:
        - name: "Friend's Agent"
          url: "http://friend-laptop.local:8223"

tools:
  - module: tool-a2a
    config:
      sender_url: "http://127.0.0.1:8222"
      sender_name: "My Agent"
```

Replace with:

```yaml
hooks:
  - module: hooks-a2a-server
    config:
      port: 8222                        # Change if another instance uses this port
      agent_name: "My Agent"            # Optional — defaults to "$USER's Agent"
      agent_description: "My personal assistant"
      discovery:
        mdns: true

tools:
  - module: tool-a2a
    config: {}                          # No configuration needed — identity derived from hook
```

Also, find and add a new section after "### For local development" (around line 119). Add at the end of the README, before any existing "## Architecture" or similar section, a new operations section. In `README.md`, find:

```markdown
### For local development
```

Add after the local development section (after the `sources:` YAML block):

```markdown

### Connecting Two Agents

No YAML editing needed — do it in session:

1. **Find your address:** Ask your agent to run `whoami`
2. **Share your URL** with the other person (copy/paste, text, verbal)
3. **They add you:** `add_contact` with your URL
4. **You add them:** `add_contact` with their URL
5. **Start talking:** `send` messages back and forth

The only configs most users ever need:
- `port` — if default 8222 is already in use
- `agent_name` — if you don't like the auto-generated name
```

### Step 4: Run all tests to check for regressions

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest -v
```

Expected: All tests pass (content files don't affect unit tests).

### Step 5: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/context/a2a-instructions.md
git add amplifier-bundle-a2a/behaviors/a2a.yaml
git add amplifier-bundle-a2a/README.md
git commit -m "docs(a2a): add whoami/add_contact to instructions, simplify config docs"
```

---

## Task 9: Integration Tests

Test the key flows end-to-end: whoami returns correct info, add_contact fetches card and adds to contacts, sender identity derived from registry.card, port collision sets server_running=false.

**Files:**

- Create: `tests/test_config_improvements.py`

### Step 1: Write the integration tests

Create `tests/test_config_improvements.py`:

```python
"""Integration tests for A2A config improvements.

Tests the full connection flow: whoami → share URL → add_contact → send message.
Also tests port collision handling and sender identity derivation.
"""

from unittest.mock import AsyncMock, MagicMock, patch

from aiohttp.test_utils import TestClient as AioTestClient, TestServer as AioTestServer


def _make_mock_coordinator(parent_id=None):
    coordinator = MagicMock()
    coordinator.session_id = "test-session"
    coordinator.parent_id = parent_id
    coordinator.config = {
        "session": {"orchestrator": "loop-basic", "context": "context-simple"},
        "providers": [{"module": "provider-test", "config": {"model": "test"}}],
        "tools": [{"module": "tool-filesystem"}],
        "hooks": [{"module": "hooks-a2a-server", "config": {"port": 0}}],
    }
    coordinator.hooks = MagicMock()
    coordinator.register_capability = MagicMock()
    coordinator.register_cleanup = MagicMock()
    coordinator.mount_points = {"providers": []}
    return coordinator


class TestWhoamiIntegration:
    """Test whoami returns correct identity from a real mount()."""

    async def test_whoami_after_mount(self):
        from amplifier_module_hooks_a2a_server import mount as server_mount
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(parent_id=None)

        # Mount the server hook
        config = {"port": 0, "host": "127.0.0.1", "agent_name": "Integration Test Agent"}
        await server_mount(coordinator, config)

        # Get registry from the capability registration
        registry = coordinator.register_capability.call_args[0][1]

        # Create the tool with the same coordinator
        coordinator.get_capability = MagicMock(return_value=registry)
        tool = A2ATool(coordinator, {})

        # Call whoami
        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["name"] == "Integration Test Agent"
        assert result.output["server_running"] is True
        assert "url" in result.output

        # Clean up
        cleanup_fn = coordinator.register_cleanup.call_args[0][0]
        await cleanup_fn()


class TestAddContactIntegration:
    """Test add_contact fetches card from a real server and adds to contacts."""

    async def test_add_contact_to_running_server(self, tmp_path):
        from amplifier_module_hooks_a2a_server.card import build_agent_card
        from amplifier_module_hooks_a2a_server.contacts import ContactStore
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry
        from amplifier_module_hooks_a2a_server.server import A2AServer
        from amplifier_module_tool_a2a import A2ATool

        # Set up a "remote" server
        remote_config = {"port": 0, "host": "127.0.0.1", "agent_name": "Remote Agent"}
        remote_registry = A2ARegistry()
        remote_card = build_agent_card(remote_config)
        remote_coordinator = _make_mock_coordinator()
        remote_server = A2AServer(
            remote_registry, remote_card, remote_coordinator, remote_config
        )

        # Set up a "local" tool with a real contact store
        local_registry = A2ARegistry()
        local_registry.card = build_agent_card(
            {"port": 0, "host": "127.0.0.1", "agent_name": "Local Agent"}
        )
        local_registry.contact_store = ContactStore(base_dir=tmp_path)
        local_coordinator = _make_mock_coordinator()
        local_coordinator.get_capability = MagicMock(return_value=local_registry)
        tool = A2ATool(local_coordinator, {})

        # Use aiohttp test client to serve the remote agent
        async with AioTestClient(AioTestServer(remote_server.app)) as client:
            # Get the actual URL of the test server
            base_url = str(client.session._base_url).rstrip("/")

            result = await tool.execute(
                {"operation": "add_contact", "url": base_url}
            )

            assert result.success is True
            assert result.output is not None
            assert result.output["name"] == "Remote Agent"
            assert result.output["tier"] == "known"

            # Verify contact was actually persisted
            contact = await local_registry.contact_store.get_contact(base_url)
            assert contact is not None
            assert contact["name"] == "Remote Agent"
            assert contact["tier"] == "known"


class TestSenderIdentityIntegration:
    """Test that sender identity flows from hook card to tool send."""

    async def test_send_includes_identity_from_card(self, tmp_path):
        from amplifier_module_hooks_a2a_server import mount as server_mount
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(parent_id=None)

        # Mount the server hook
        config = {"port": 0, "host": "127.0.0.1", "agent_name": "Sender Agent"}
        await server_mount(coordinator, config)

        # Get registry
        registry = coordinator.register_capability.call_args[0][1]
        coordinator.get_capability = MagicMock(return_value=registry)

        # Create tool — note NO sender_url/sender_name in config
        tool = A2ATool(coordinator, {})
        tool.client = AsyncMock()
        tool.client.fetch_agent_card = AsyncMock(
            return_value={"name": "Remote", "capabilities": {"realtimeResponse": True}}
        )
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute(
            {"operation": "send", "agent": "http://remote:8222", "message": "Hello"}
        )

        # Verify the tool sent our identity derived from the card
        call_kwargs = tool.client.send_message.call_args
        assert call_kwargs[1]["sender_name"] == "Sender Agent"
        assert call_kwargs[1]["sender_url"] is not None
        assert "127.0.0.1" in call_kwargs[1]["sender_url"]

        # Clean up
        cleanup_fn = coordinator.register_cleanup.call_args[0][0]
        await cleanup_fn()


class TestPortCollisionIntegration:
    """Test that port collision properly sets server_running=False."""

    async def test_port_collision_whoami_reports_issue(self):
        from amplifier_module_hooks_a2a_server import mount as server_mount
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(parent_id=None)

        config = {"port": 8222, "host": "127.0.0.1", "agent_name": "Collision Agent"}

        with patch(
            "amplifier_module_hooks_a2a_server.server.A2AServer.start",
            side_effect=OSError("Address already in use"),
        ):
            await server_mount(coordinator, config)

        registry = coordinator.register_capability.call_args[0][1]
        coordinator.get_capability = MagicMock(return_value=registry)

        tool = A2ATool(coordinator, {})
        result = await tool.execute({"operation": "whoami"})

        assert result.success is True
        assert result.output is not None
        assert result.output["server_running"] is False
        assert "message" in result.output
        assert result.output["name"] == "Collision Agent"


class TestSmartDefaultIntegration:
    """Test smart agent name default derives from username."""

    async def test_default_name_from_username(self):
        from amplifier_module_hooks_a2a_server import mount as server_mount
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(parent_id=None)

        # No agent_name in config — should use username default
        config = {"port": 0, "host": "127.0.0.1"}

        with patch(
            "amplifier_module_hooks_a2a_server.card.getpass"
        ) as mock_getpass:
            mock_getpass.getuser.return_value = "testuser"
            await server_mount(coordinator, config)

        registry = coordinator.register_capability.call_args[0][1]
        coordinator.get_capability = MagicMock(return_value=registry)

        tool = A2ATool(coordinator, {})
        result = await tool.execute({"operation": "whoami"})

        assert result.success is True
        assert result.output is not None
        assert result.output["name"] == "testuser's Agent"

        # Clean up
        cleanup_fn = coordinator.register_cleanup.call_args[0][0]
        await cleanup_fn()
```

### Step 2: Run the integration tests

```bash
cd /home/bkrabach/dev/a2a-investigate/amplifier-bundle-a2a
pytest tests/test_config_improvements.py -v
```

Expected: All integration tests PASS.

### Step 3: Run all tests to check for regressions

```bash
pytest -v
```

Expected: All tests pass (previous tests + new integration tests).

### Step 4: Commit

```bash
cd /home/bkrabach/dev/a2a-investigate
git add amplifier-bundle-a2a/tests/test_config_improvements.py
git commit -m "test(a2a): add integration tests for config improvements"
```

---

## Summary

| Task | What | Files Changed | Tests Added |
|------|------|---------------|-------------|
| 1 | Add `card` and `server_running` to registry | `registry.py` | 4 |
| 2 | Smart default agent name from username | `card.py` | 3 (+ 1 updated) |
| 3 | Prominent port collision error | `__init__.py` (hook) | 3 |
| 4 | Store card on registry in mount() | `__init__.py` (hook) | 2 |
| 5 | Derive sender identity from card | `__init__.py` (tool) | 3 |
| 6 | `whoami` operation | `__init__.py` (tool) | 4 |
| 7 | `add_contact` operation | `__init__.py` (tool) | 5 |
| 8 | Update instructions, YAML, README | 3 content files | 0 |
| 9 | Integration tests | `test_config_improvements.py` | 5 |

**Total: ~29 new tests across 9 commits.**
