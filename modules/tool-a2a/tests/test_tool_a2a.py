"""Tests for A2ATool — agents and card operations."""

from unittest.mock import AsyncMock, MagicMock

import pytest


def _make_mock_coordinator(registry=None):
    """Create a mock coordinator that returns the given registry."""
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=registry)
    coordinator.mount = AsyncMock()
    coordinator.register_cleanup = MagicMock()
    return coordinator


def _make_mock_registry(agents=None):
    """Create a mock registry with the given agents."""
    registry = MagicMock()
    registry.get_agents = MagicMock(return_value=agents or [])
    registry.resolve_agent_url = MagicMock(return_value=None)
    registry.get_cached_card = MagicMock(return_value=None)
    registry.cache_card = MagicMock()
    registry.card = None  # Matches real A2ARegistry default
    return registry


class TestToolProtocol:
    """Verify the tool satisfies the Amplifier Tool protocol."""

    def test_tool_has_name(self):
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator()
        tool = A2ATool(coordinator, {})
        assert tool.name == "a2a"

    def test_tool_has_description(self):
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator()
        tool = A2ATool(coordinator, {})
        assert isinstance(tool.description, str)
        assert len(tool.description) > 0

    def test_tool_has_input_schema(self):
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator()
        tool = A2ATool(coordinator, {})
        schema = tool.input_schema
        assert schema["type"] == "object"
        assert "operation" in schema["properties"]
        assert "operation" in schema["required"]

    @pytest.mark.asyncio
    async def test_mount_registers_tool(self):
        from amplifier_module_tool_a2a import mount

        coordinator = _make_mock_coordinator()
        await mount(coordinator, {})
        coordinator.mount.assert_called_once()
        call_args = coordinator.mount.call_args
        assert call_args[0][0] == "tools"
        assert call_args[1]["name"] == "a2a"


class TestAgentsOperation:
    @pytest.mark.asyncio
    async def test_returns_known_agents(self):
        from amplifier_module_tool_a2a import A2ATool

        agents = [
            {"name": "Alice", "url": "http://alice:8222"},
            {"name": "Bob", "url": "http://bob:8222"},
        ]
        registry = _make_mock_registry(agents=agents)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is True
        assert result.output is not None
        assert len(result.output) == 2
        assert result.output[0]["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_returns_message_when_no_agents(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry(agents=[])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is True
        assert "No known agents" in str(result.output)

    @pytest.mark.asyncio
    async def test_returns_error_when_registry_unavailable(self):
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(registry=None)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "agents"})
        assert result.success is False
        assert result.error is not None
        assert "registry" in result.error["message"].lower()


class TestCardOperation:
    @pytest.mark.asyncio
    async def test_fetches_card_from_remote(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        # Mock the HTTP client
        mock_card = {"name": "Alice", "version": "1.0"}
        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(return_value=mock_card)

        result = await tool.execute({"operation": "card", "agent": "Alice"})
        assert result.success is True
        assert result.output is not None
        assert result.output["name"] == "Alice"
        registry.cache_card.assert_called_once()

    @pytest.mark.asyncio
    async def test_returns_cached_card(self):
        from amplifier_module_tool_a2a import A2ATool

        cached_card = {"name": "Alice", "version": "1.0"}
        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(return_value=cached_card)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        # Client should NOT be called
        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock()

        result = await tool.execute({"operation": "card", "agent": "Alice"})
        assert result.success is True
        assert result.output == cached_card
        tool.client.fetch_agent_card.assert_not_called()

    @pytest.mark.asyncio
    async def test_requires_agent_parameter(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "card"})
        assert result.success is False
        assert result.error is not None
        assert "required" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_agent(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value=None)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "card", "agent": "Nobody"})
        assert result.success is False
        assert result.error is not None
        assert "Unknown agent" in result.error["message"]


class TestSendOperation:
    @pytest.mark.asyncio
    async def test_sends_message_and_returns_result(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        # Mock the client
        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(return_value={"name": "Alice"})
        tool.client.send_message = AsyncMock(
            return_value={
                "id": "task-1",
                "status": "COMPLETED",
                "artifacts": [{"parts": [{"text": "42"}]}],
            }
        )

        result = await tool.execute(
            {"operation": "send", "agent": "Alice", "message": "What is 6*7?"}
        )
        assert result.success is True
        assert result.output is not None
        assert result.output["status"] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_send_uses_cached_card(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute({"operation": "send", "agent": "Alice", "message": "Hi"})
        # Should NOT fetch card — it's cached
        tool.client.fetch_agent_card.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_fetches_card_if_not_cached(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(return_value=None)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(return_value={"name": "Alice"})
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute({"operation": "send", "agent": "Alice", "message": "Hi"})
        tool.client.fetch_agent_card.assert_called_once()
        registry.cache_card.assert_called_once()

    @pytest.mark.asyncio
    async def test_send_requires_agent(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "send", "message": "Hi"})
        assert result.success is False
        assert result.error is not None
        assert "required" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_send_requires_message(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "send", "agent": "Alice"})
        assert result.success is False
        assert result.error is not None
        assert "required" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_send_returns_error_for_unknown_agent(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value=None)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "send", "agent": "Nobody", "message": "Hi"}
        )
        assert result.success is False
        assert result.error is not None
        assert "Unknown agent" in result.error["message"]

    @pytest.mark.asyncio
    async def test_send_passes_custom_timeout(self):
        """Timeout controls the polling loop, not the HTTP send."""
        from unittest.mock import patch

        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "WORKING"}
        )
        # Always returns WORKING so we hit timeout
        tool.client.get_task_status = AsyncMock(
            return_value={"id": "t1", "status": "WORKING"}
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await tool.execute(
                {"operation": "send", "agent": "Alice", "message": "Hi", "timeout": 3}
            )
        # Should have timed out after ~3 polls (3s / 1s interval)
        assert result.success is True
        assert result.output is not None
        assert "timeout" in result.output.get("message", "").lower()
        assert tool.client.get_task_status.call_count == 3

    @pytest.mark.asyncio
    async def test_send_handles_connection_error(self):
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            side_effect=ConnectionError("Agent unreachable")
        )

        result = await tool.execute(
            {"operation": "send", "agent": "Alice", "message": "Hi"}
        )
        assert result.success is False
        assert result.error is not None
        assert "unreachable" in result.error["message"].lower()


class TestUnknownOperation:
    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_operation(self):
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator()
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "bogus"})
        assert result.success is False
        assert result.error is not None
        assert "Unknown operation" in result.error["message"]


# --- Helpers for Phase 2 tests ---


def _make_registry_with_stores(
    agents=None,
    pending_approvals=None,
    pending_messages=None,
    contacts=None,
):
    """Create a mock registry with contact_store and pending_queue."""
    registry = _make_mock_registry(agents=agents)

    # contact_store mock
    contact_store = MagicMock()
    contact_store.list_contacts = MagicMock(return_value=contacts or [])
    contact_store.get_contact = MagicMock(return_value=None)
    contact_store.add_contact = AsyncMock()
    contact_store.update_tier = AsyncMock()
    registry.contact_store = contact_store

    # pending_queue mock
    pending_queue = MagicMock()
    pending_queue.get_pending_approvals = MagicMock(
        return_value=pending_approvals or []
    )
    pending_queue.update_approval_status = AsyncMock()

    # Message lookup: build a dict for get_message
    _msg_map = {m["task_id"]: m for m in (pending_messages or [])}
    pending_queue.get_message = MagicMock(side_effect=lambda tid: _msg_map.get(tid))
    pending_queue.update_message_status = AsyncMock()

    registry.pending_queue = pending_queue

    # update_task on the registry itself
    registry.update_task = MagicMock()

    return registry


class TestApproveOperation:
    @pytest.mark.asyncio
    async def test_approve_adds_contact_and_updates_approval(self):
        """Happy path: approve adds sender to contacts and updates approval."""
        from amplifier_module_tool_a2a import A2ATool

        approval = {
            "task_id": "task-123",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello"},
            "status": "pending",
        }
        registry = _make_registry_with_stores(pending_approvals=[approval])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "approve", "agent": "http://alice:8222"}
        )
        assert result.success is True
        registry.contact_store.add_contact.assert_awaited_once_with(
            "http://alice:8222", "Alice", "known"
        )
        registry.pending_queue.update_approval_status.assert_awaited_once_with(
            "task-123", "approved"
        )
        registry.update_task.assert_called_once()
        call_kwargs = registry.update_task.call_args
        assert call_kwargs[0][0] == "task-123"
        assert call_kwargs[0][1] == "COMPLETED"

    @pytest.mark.asyncio
    async def test_approve_requires_agent(self):
        """Missing agent param returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "approve"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_approve_error_when_no_pending_approval(self):
        """No matching approval returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores(pending_approvals=[])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "approve", "agent": "http://nobody:8222"}
        )
        assert result.success is False
        assert result.error is not None
        assert "pending" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_approve_with_custom_tier(self):
        """tier='trusted' sets trusted tier on the new contact."""
        from amplifier_module_tool_a2a import A2ATool

        approval = {
            "task_id": "task-456",
            "sender_url": "http://bob:8222",
            "sender_name": "Bob",
            "message": {"text": "Hi"},
            "status": "pending",
        }
        registry = _make_registry_with_stores(pending_approvals=[approval])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "approve", "agent": "http://bob:8222", "tier": "trusted"}
        )
        assert result.success is True
        registry.contact_store.add_contact.assert_awaited_once_with(
            "http://bob:8222", "Bob", "trusted"
        )

    @pytest.mark.asyncio
    async def test_approve_error_when_stores_unavailable(self):
        """Returns error when contact_store/pending_queue not set."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = None
        registry.pending_queue = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "approve", "agent": "http://alice:8222"}
        )
        assert result.success is False
        assert result.error is not None


class TestBlockOperation:
    @pytest.mark.asyncio
    async def test_block_rejects_task(self):
        """Block updates approval to 'blocked' and task to FAILED."""
        from amplifier_module_tool_a2a import A2ATool

        approval = {
            "task_id": "task-789",
            "sender_url": "http://eve:8222",
            "sender_name": "Eve",
            "message": {"text": "Let me in"},
            "status": "pending",
        }
        registry = _make_registry_with_stores(pending_approvals=[approval])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "block", "agent": "http://eve:8222"})
        assert result.success is True
        registry.pending_queue.update_approval_status.assert_awaited_once_with(
            "task-789", "blocked"
        )
        registry.update_task.assert_called_once()
        call_args = registry.update_task.call_args
        assert call_args[0][0] == "task-789"
        assert call_args[0][1] == "FAILED"

    @pytest.mark.asyncio
    async def test_block_requires_agent(self):
        """Missing agent param returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "block"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_block_error_when_no_pending_approval(self):
        """No matching approval returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores(pending_approvals=[])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "block", "agent": "http://nobody:8222"}
        )
        assert result.success is False
        assert result.error is not None
        assert "pending" in result.error["message"].lower()


class TestContactsOperation:
    @pytest.mark.asyncio
    async def test_contacts_lists_all(self):
        """Returns all contacts from contact_store."""
        from amplifier_module_tool_a2a import A2ATool

        contacts = [
            {"url": "http://alice:8222", "name": "Alice", "tier": "known"},
            {"url": "http://bob:8222", "name": "Bob", "tier": "trusted"},
        ]
        registry = _make_registry_with_stores(contacts=contacts)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "contacts"})
        assert result.success is True
        assert result.output is not None
        assert len(result.output) == 2
        assert result.output[0]["name"] == "Alice"
        assert result.output[1]["tier"] == "trusted"

    @pytest.mark.asyncio
    async def test_contacts_empty_when_no_contacts(self):
        """Returns message when no contacts exist."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores(contacts=[])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "contacts"})
        assert result.success is True
        assert "no contacts" in str(result.output).lower()

    @pytest.mark.asyncio
    async def test_contacts_error_when_stores_unavailable(self):
        """Returns error when contact_store not set."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = None
        registry.pending_queue = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "contacts"})
        assert result.success is False
        assert result.error is not None


class TestTrustOperation:
    @pytest.mark.asyncio
    async def test_trust_updates_tier(self):
        """Changes an existing contact's tier."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        registry.contact_store.get_contact = MagicMock(
            return_value={"url": "http://alice:8222", "name": "Alice", "tier": "known"}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "trust", "agent": "http://alice:8222", "tier": "trusted"}
        )
        assert result.success is True
        registry.contact_store.update_tier.assert_awaited_once_with(
            "http://alice:8222", "trusted"
        )

    @pytest.mark.asyncio
    async def test_trust_requires_agent_and_tier(self):
        """Missing agent or tier returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        # Missing agent
        result = await tool.execute({"operation": "trust", "tier": "trusted"})
        assert result.success is False

        # Missing tier
        result = await tool.execute(
            {"operation": "trust", "agent": "http://alice:8222"}
        )
        assert result.success is False

    @pytest.mark.asyncio
    async def test_trust_error_for_unknown_contact(self):
        """Returns error when contact not found."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        registry.contact_store.get_contact = MagicMock(return_value=None)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "trust", "agent": "http://nobody:8222", "tier": "trusted"}
        )
        assert result.success is False
        assert result.error is not None


class TestRespondOperation:
    @pytest.mark.asyncio
    async def test_respond_updates_task_and_pending(self):
        """Happy path: respond updates task to COMPLETED with artifact, marks pending as 'responded'."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-1",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello, can you help?"},
            "status": "pending",
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "task-msg-1",
                "message": "Sure, happy to help!",
            }
        )
        assert result.success is True

        # Verify task updated to COMPLETED with artifact containing the reply
        registry.update_task.assert_called_once()
        call_args = registry.update_task.call_args
        assert call_args[0][0] == "task-msg-1"
        assert call_args[0][1] == "COMPLETED"
        artifacts = call_args[1]["artifacts"]
        assert artifacts[0]["parts"][0]["text"] == "Sure, happy to help!"

        # Verify pending entry marked as responded
        registry.pending_queue.update_message_status.assert_awaited_once_with(
            "task-msg-1", "responded"
        )

    @pytest.mark.asyncio
    async def test_respond_requires_task_id(self):
        """Missing task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "respond", "message": "My reply"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_respond_requires_message(self):
        """Missing message returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "respond", "task_id": "task-msg-1"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_respond_error_when_no_pending_message(self):
        """Unknown task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores(pending_messages=[])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "nonexistent",
                "message": "My reply",
            }
        )
        assert result.success is False
        assert result.error is not None
        assert "pending message" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_respond_error_when_stores_unavailable(self):
        """No pending_queue returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = None
        registry.pending_queue = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "task-msg-1",
                "message": "My reply",
            }
        )
        assert result.success is False
        assert result.error is not None


class TestDismissOperation:
    @pytest.mark.asyncio
    async def test_dismiss_updates_task_and_pending(self):
        """Happy path: dismiss updates task to REJECTED, marks pending as 'dismissed'."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-2",
            "sender_url": "http://bob:8222",
            "sender_name": "Bob",
            "message": {"text": "Can we chat?"},
            "status": "pending",
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "dismiss", "task_id": "task-msg-2"})
        assert result.success is True

        # Verify task updated to REJECTED with error message
        registry.update_task.assert_called_once()
        call_args = registry.update_task.call_args
        assert call_args[0][0] == "task-msg-2"
        assert call_args[0][1] == "REJECTED"
        assert "error" in call_args[1]

        # Verify pending entry marked as dismissed
        registry.pending_queue.update_message_status.assert_awaited_once_with(
            "task-msg-2", "dismissed"
        )

    @pytest.mark.asyncio
    async def test_dismiss_requires_task_id(self):
        """Missing task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "dismiss"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_dismiss_error_when_no_pending_message(self):
        """Unknown task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores(pending_messages=[])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "dismiss", "task_id": "nonexistent"})
        assert result.success is False
        assert result.error is not None
        assert "pending message" in result.error["message"].lower()


class TestDeferOperation:
    @pytest.mark.asyncio
    async def test_defer_marks_message_deferred(self):
        """Happy path: updates status to 'deferred', adds to deferred_ids."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-1",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello, can you help?"},
            "status": "pending",
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        registry.deferred_ids = set()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "defer", "task_id": "task-msg-1"})
        assert result.success is True

        # Verify pending entry marked as deferred
        registry.pending_queue.update_message_status.assert_awaited_once_with(
            "task-msg-1", "deferred"
        )
        # Verify task_id added to deferred_ids
        assert "task-msg-1" in registry.deferred_ids

    @pytest.mark.asyncio
    async def test_defer_requires_task_id(self):
        """Missing task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        registry.deferred_ids = set()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "defer"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_defer_error_when_no_pending_message(self):
        """Unknown task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores(pending_messages=[])
        registry.deferred_ids = set()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "defer", "task_id": "nonexistent"})
        assert result.success is False
        assert result.error is not None
        assert "pending message" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_defer_error_when_stores_unavailable(self):
        """No pending_queue returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.contact_store = None
        registry.pending_queue = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "defer", "task_id": "task-msg-1"})
        assert result.success is False
        assert result.error is not None

    @pytest.mark.asyncio
    async def test_deferred_item_not_reinjected(self, tmp_path):
        """After defer, injection handler skips the item."""
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler  # type: ignore[import-not-found]
        from amplifier_module_hooks_a2a_server.pending import PendingQueue  # type: ignore[import-not-found]
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry  # type: ignore[import-not-found]

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_message(
            task_id="task-msg-1",
            sender_url="http://alice:8222",
            sender_name="Alice",
            message={"role": "user", "parts": [{"text": "Hello"}]},
        )

        registry = A2ARegistry()
        registry.pending_queue = queue
        handler = A2AInjectionHandler(queue, registry)

        # First call: injects the message
        result1 = await handler("provider:request", {})
        assert result1.action == "inject_context"

        # Now defer the message: update status + add to deferred_ids
        await queue.update_message_status("task-msg-1", "deferred")
        registry.deferred_ids.add("task-msg-1")

        # Add a NEW message to prove handler still works for non-deferred items
        await queue.add_message(
            task_id="task-msg-2",
            sender_url="http://bob:8222",
            sender_name="Bob",
            message={"role": "user", "parts": [{"text": "Hey"}]},
        )

        # Second call: should inject task-msg-2 but NOT task-msg-1
        result2 = await handler("provider:request", {})
        assert result2.action == "inject_context"
        assert "Bob" in result2.context_injection
        assert "Alice" not in result2.context_injection

    @pytest.mark.asyncio
    async def test_deferred_item_still_respondable(self):
        """After defer, respond still works for the deferred task_id."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-1",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello, can you help?"},
            "status": "deferred",
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        registry.deferred_ids = {"task-msg-1"}
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "task-msg-1",
                "message": "OK, I'll help now!",
            }
        )
        assert result.success is True
        registry.update_task.assert_called_once()
        registry.pending_queue.update_message_status.assert_awaited_once_with(
            "task-msg-1", "responded"
        )


class TestRespondAttribution:
    """Task 6: respond sets attribution based on escalated flag."""

    @pytest.mark.asyncio
    async def test_respond_sets_user_response_attribution(self):
        """Non-escalated pending → attribution 'user_response'."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-1",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello, can you help?"},
            "status": "pending",
            "escalated": False,
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "task-msg-1",
                "message": "Sure, happy to help!",
            }
        )
        assert result.success is True

        # Verify attribution is "user_response" for non-escalated
        call_kwargs = registry.update_task.call_args
        assert call_kwargs[1]["attribution"] == "user_response"

    @pytest.mark.asyncio
    async def test_respond_sets_escalated_user_response_attribution(self):
        """Escalated pending → attribution 'escalated_user_response'."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-1",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello, can you help?"},
            "status": "pending",
            "escalated": True,
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "task-msg-1",
                "message": "Sure, happy to help!",
            }
        )
        assert result.success is True

        # Verify attribution is "escalated_user_response" for escalated
        call_kwargs = registry.update_task.call_args
        assert call_kwargs[1]["attribution"] == "escalated_user_response"

    @pytest.mark.asyncio
    async def test_respond_attribution_in_task(self):
        """After respond, get_task shows correct attribution (integration-style with real registry)."""
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry, _Task  # type: ignore[import-not-found]
        from amplifier_module_tool_a2a import A2ATool

        # Use real registry so get_task reflects the update
        registry = A2ARegistry()
        pq = MagicMock()
        pending_entry = {
            "task_id": "task-r-1",
            "sender_url": "http://alice:8222",
            "sender_name": "Alice",
            "message": {"text": "Hello"},
            "status": "pending",
            "escalated": False,
        }
        pq.get_message = MagicMock(return_value=pending_entry)
        pq.update_message_status = AsyncMock()
        registry.pending_queue = pq

        cs = MagicMock()
        registry.contact_store = cs

        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        # Pre-create the task in the real registry
        registry._tasks["task-r-1"] = _Task(
            id="task-r-1",
            status="INPUT_REQUIRED",
            history=[{"role": "user", "parts": [{"text": "Hello"}]}],
        )

        result = await tool.execute(
            {
                "operation": "respond",
                "task_id": "task-r-1",
                "message": "My response",
            }
        )
        assert result.success is True

        task = registry.get_task("task-r-1")
        assert task is not None
        assert task["attribution"] == "user_response"


class TestDismissAttribution:
    """Task 6: dismiss sets attribution='dismissed'."""

    @pytest.mark.asyncio
    async def test_dismiss_sets_dismissed_attribution(self):
        """Dismiss → attribution 'dismissed'."""
        from amplifier_module_tool_a2a import A2ATool

        pending_msg = {
            "task_id": "task-msg-2",
            "sender_url": "http://bob:8222",
            "sender_name": "Bob",
            "message": {"text": "Can we chat?"},
            "status": "pending",
            "escalated": False,
        }
        registry = _make_registry_with_stores(pending_messages=[pending_msg])
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "dismiss", "task_id": "task-msg-2"})
        assert result.success is True

        # Verify attribution is "dismissed"
        call_kwargs = registry.update_task.call_args
        assert call_kwargs[1]["attribution"] == "dismissed"

    @pytest.mark.asyncio
    async def test_dismiss_attribution_in_task(self):
        """After dismiss, get_task shows correct attribution (integration-style with real registry)."""
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry, _Task  # type: ignore[import-not-found]
        from amplifier_module_tool_a2a import A2ATool

        # Use real registry so get_task reflects the update
        registry = A2ARegistry()
        pq = MagicMock()
        pending_entry = {
            "task_id": "task-d-1",
            "sender_url": "http://bob:8222",
            "sender_name": "Bob",
            "message": {"text": "Can we chat?"},
            "status": "pending",
            "escalated": False,
        }
        pq.get_message = MagicMock(return_value=pending_entry)
        pq.update_message_status = AsyncMock()
        registry.pending_queue = pq

        cs = MagicMock()
        registry.contact_store = cs

        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        # Pre-create the task in the real registry
        registry._tasks["task-d-1"] = _Task(
            id="task-d-1",
            status="INPUT_REQUIRED",
            history=[{"role": "user", "parts": [{"text": "Can we chat?"}]}],
        )

        result = await tool.execute({"operation": "dismiss", "task_id": "task-d-1"})
        assert result.success is True

        task = registry.get_task("task-d-1")
        assert task is not None
        assert task["attribution"] == "dismissed"


class TestSendBlockingFalse:
    """Tests for non-blocking send (blocking=false)."""

    @pytest.mark.asyncio
    async def test_send_blocking_false_returns_immediately(self):
        """blocking=false returns initial task response without polling."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "WORKING"}
        )
        tool.client.get_task_status = AsyncMock()

        result = await tool.execute(
            {
                "operation": "send",
                "agent": "Alice",
                "message": "Hi",
                "blocking": False,
            }
        )
        assert result.success is True
        assert result.output is not None
        assert result.output["status"] == "WORKING"
        # Must NOT have polled
        tool.client.get_task_status.assert_not_called()


class TestSendPolling:
    """Tests for blocking send with client-side polling."""

    @pytest.mark.asyncio
    async def test_send_blocking_true_polls_until_completed(self):
        """Blocking send polls get_task_status until terminal state."""
        from unittest.mock import patch

        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "WORKING"}
        )
        # First poll: still WORKING, second poll: COMPLETED
        tool.client.get_task_status = AsyncMock(
            side_effect=[
                {"id": "task-1", "status": "WORKING"},
                {
                    "id": "task-1",
                    "status": "COMPLETED",
                    "artifacts": [{"parts": [{"text": "done"}]}],
                },
            ]
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await tool.execute(
                {
                    "operation": "send",
                    "agent": "Alice",
                    "message": "Hi",
                    "timeout": 10,
                }
            )
        assert result.success is True
        assert result.output is not None
        assert result.output["status"] == "COMPLETED"
        assert tool.client.get_task_status.call_count == 2

    @pytest.mark.asyncio
    async def test_send_blocking_true_returns_on_timeout(self):
        """Blocking send returns timeout message when task stays non-terminal."""
        from unittest.mock import patch

        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-1", "status": "WORKING"}
        )
        # Always returns WORKING
        tool.client.get_task_status = AsyncMock(
            return_value={"id": "task-1", "status": "WORKING"}
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await tool.execute(
                {
                    "operation": "send",
                    "agent": "Alice",
                    "message": "Hi",
                    "timeout": 2,
                }
            )
        assert result.success is True
        assert result.output is not None
        assert result.output["id"] == "task-1"
        assert "timeout" in result.output.get("message", "").lower()
        assert "status" in result.output

    @pytest.mark.asyncio
    async def test_send_blocking_true_already_terminal(self):
        """If send_message returns terminal state, no polling happens."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={
                "id": "task-1",
                "status": "COMPLETED",
                "artifacts": [{"parts": [{"text": "42"}]}],
            }
        )
        tool.client.get_task_status = AsyncMock()

        result = await tool.execute(
            {"operation": "send", "agent": "Alice", "message": "What is 6*7?"}
        )
        assert result.success is True
        assert result.output is not None
        assert result.output["status"] == "COMPLETED"
        # Must NOT have polled since already terminal
        tool.client.get_task_status.assert_not_called()


class TestStatusOperation:
    """Tests for the status operation."""

    @pytest.mark.asyncio
    async def test_status_returns_task_state(self):
        """Happy path: status calls get_task_status and returns result."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.get_task_status = AsyncMock(
            return_value={"id": "task-1", "status": "COMPLETED"}
        )

        result = await tool.execute(
            {"operation": "status", "agent": "Alice", "task_id": "task-1"}
        )
        assert result.success is True
        assert result.output is not None
        assert result.output["status"] == "COMPLETED"
        tool.client.get_task_status.assert_called_once_with(
            "http://alice:8222", "task-1"
        )

    @pytest.mark.asyncio
    async def test_status_requires_agent(self):
        """Missing agent returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "status", "task_id": "task-1"})
        assert result.success is False
        assert result.error is not None
        assert "agent" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_status_requires_task_id(self):
        """Missing task_id returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "status", "agent": "Alice"})
        assert result.success is False
        assert result.error is not None
        assert "task" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_status_error_for_unknown_agent(self):
        """Unknown agent returns error."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value=None)
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute(
            {"operation": "status", "agent": "Nobody", "task_id": "task-1"}
        )
        assert result.success is False
        assert result.error is not None
        assert "unknown agent" in result.error["message"].lower()


class TestOutgoingTracking:
    """Task 7: Sender-side outgoing task tracking."""

    @pytest.mark.asyncio
    async def test_nonblocking_send_tracks_outgoing(self):
        """blocking=false with non-terminal response → task_id added to _pending_outgoing."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-nb-1", "status": "WORKING"}
        )

        result = await tool.execute(
            {
                "operation": "send",
                "agent": "Alice",
                "message": "Hi",
                "blocking": False,
            }
        )
        assert result.success is True
        assert "task-nb-1" in tool._pending_outgoing

    @pytest.mark.asyncio
    async def test_blocking_send_timeout_tracks_outgoing(self):
        """blocking=true that times out → task_id added to _pending_outgoing."""
        from unittest.mock import patch

        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-to-1", "status": "WORKING"}
        )
        tool.client.get_task_status = AsyncMock(
            return_value={"id": "task-to-1", "status": "WORKING"}
        )

        with patch("asyncio.sleep", new_callable=AsyncMock):
            result = await tool.execute(
                {
                    "operation": "send",
                    "agent": "Alice",
                    "message": "Hi",
                    "timeout": 2,
                }
            )
        assert result.success is True
        assert "task-to-1" in tool._pending_outgoing

    @pytest.mark.asyncio
    async def test_blocking_send_completed_not_tracked(self):
        """blocking=true that returns COMPLETED → NOT tracked."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={
                "id": "task-c-1",
                "status": "COMPLETED",
                "artifacts": [{"parts": [{"text": "done"}]}],
            }
        )

        result = await tool.execute(
            {"operation": "send", "agent": "Alice", "message": "Hi"}
        )
        assert result.success is True
        assert "task-c-1" not in tool._pending_outgoing

    @pytest.mark.asyncio
    async def test_track_outgoing_stores_correct_fields(self):
        """Verify stored dict has task_id, agent_url, agent_name, submitted_at."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-f-1", "status": "WORKING"}
        )

        await tool.execute(
            {
                "operation": "send",
                "agent": "Alice",
                "message": "Hi",
                "blocking": False,
            }
        )

        entry = tool._pending_outgoing["task-f-1"]
        assert entry["task_id"] == "task-f-1"
        assert entry["agent_url"] == "http://alice:8222"
        assert entry["agent_name"] == "Alice"
        assert "submitted_at" in entry
        # Verify it's an ISO format timestamp
        assert "T" in entry["submitted_at"]

    def test_collect_completed_drains_list(self):
        """Add items to _completed_outgoing, call _collect_completed → returns items and clears list."""
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator()
        tool = A2ATool(coordinator, {})

        tool._completed_outgoing.append({"task_id": "t1", "status": "COMPLETED"})
        tool._completed_outgoing.append({"task_id": "t2", "status": "FAILED"})

        collected = tool._collect_completed()
        assert len(collected) == 2
        assert collected[0]["task_id"] == "t1"
        assert collected[1]["task_id"] == "t2"
        # List should be drained
        assert len(tool._completed_outgoing) == 0

    def test_collect_completed_empty_when_nothing(self):
        """_collect_completed on empty list → empty list."""
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator()
        tool = A2ATool(coordinator, {})

        collected = tool._collect_completed()
        assert collected == []
        assert len(tool._completed_outgoing) == 0

    @pytest.mark.asyncio
    async def test_nonblocking_send_completed_not_tracked(self):
        """blocking=false with terminal response → NOT tracked."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={
                "id": "task-nc-1",
                "status": "COMPLETED",
                "artifacts": [{"parts": [{"text": "instant"}]}],
            }
        )

        await tool.execute(
            {
                "operation": "send",
                "agent": "Alice",
                "message": "Hi",
                "blocking": False,
            }
        )
        assert "task-nc-1" not in tool._pending_outgoing

    @pytest.mark.asyncio
    async def test_tracking_uses_original_agent_name(self):
        """When input is a name, agent_name in tracking uses that name."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "task-n-1", "status": "WORKING"}
        )

        # Send using URL directly as agent param
        registry.resolve_agent_url = MagicMock(return_value="http://remote-agent:8222")
        await tool.execute(
            {
                "operation": "send",
                "agent": "http://remote-agent:8222",
                "message": "Hi",
                "blocking": False,
            }
        )

        entry = tool._pending_outgoing["task-n-1"]
        assert entry["agent_name"] == "http://remote-agent:8222"


class TestSendSenderIdentity:
    """Task 5: _op_send derives sender identity from registry.card, config as fallback."""

    @pytest.mark.asyncio
    async def test_send_derives_sender_url_from_registry_card(self):
        """registry.card has url/name → send_message receives card's values."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        registry.card = {"name": "Test Agent", "url": "http://test:8222"}
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute({"operation": "send", "agent": "Alice", "message": "Hi"})
        tool.client.send_message.assert_called_once_with(
            "http://alice:8222",
            "Hi",
            sender_url="http://test:8222",
            sender_name="Test Agent",
        )

    @pytest.mark.asyncio
    async def test_send_config_overrides_registry_card(self):
        """Config sender_url/sender_name take precedence over registry.card."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        registry.card = {"name": "Test Agent", "url": "http://test:8222"}
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(
            coordinator,
            {
                "sender_url": "http://proxy:9000",
                "sender_name": "Proxy Agent",
            },
        )

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute({"operation": "send", "agent": "Alice", "message": "Hi"})
        tool.client.send_message.assert_called_once_with(
            "http://alice:8222",
            "Hi",
            sender_url="http://proxy:9000",
            sender_name="Proxy Agent",
        )

    @pytest.mark.asyncio
    async def test_send_works_without_card_on_registry(self):
        """registry.card is None, config has sender_url → config fallback works."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        registry.card = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(
            coordinator,
            {"sender_url": "http://fallback:8222", "sender_name": "Fallback"},
        )

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute({"operation": "send", "agent": "Alice", "message": "Hi"})
        tool.client.send_message.assert_called_once_with(
            "http://alice:8222",
            "Hi",
            sender_url="http://fallback:8222",
            sender_name="Fallback",
        )

    @pytest.mark.asyncio
    async def test_send_works_without_any_sender_identity(self):
        """registry.card is None, no config → sender_url/sender_name are None."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.resolve_agent_url = MagicMock(return_value="http://alice:8222")
        registry.get_cached_card = MagicMock(
            return_value={"name": "Alice", "capabilities": {"realtimeResponse": True}}
        )
        registry.card = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.send_message = AsyncMock(
            return_value={"id": "t1", "status": "COMPLETED", "artifacts": []}
        )

        await tool.execute({"operation": "send", "agent": "Alice", "message": "Hi"})
        tool.client.send_message.assert_called_once_with(
            "http://alice:8222",
            "Hi",
            sender_url=None,
            sender_name=None,
        )


class TestWhoamiOperation:
    """Task 6: whoami returns agent identity and server status."""

    @pytest.mark.asyncio
    async def test_whoami_returns_agent_identity(self):
        """Registry has card → returns name, url, server_running=True."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "My Agent",
            "url": "http://myagent:8222",
            "description": "A helpful agent",
            "capabilities": {"realtimeResponse": True},
            "skills": [{"id": "chat", "name": "Chat"}],
        }
        registry.server_running = True
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["name"] == "My Agent"
        assert result.output["url"] == "http://myagent:8222"
        assert result.output["server_running"] is True

    @pytest.mark.asyncio
    async def test_whoami_includes_realtime_response(self):
        """Card has realtimeResponse capability → included in output."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "Realtime Agent",
            "url": "http://rt:8222",
            "description": "",
            "capabilities": {"realtimeResponse": True},
            "skills": [],
        }
        registry.server_running = True
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["realtimeResponse"] is True

    @pytest.mark.asyncio
    async def test_whoami_server_not_running(self):
        """Registry with card but server_running=False → output includes server_running=False."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = {
            "name": "Offline Agent",
            "url": "http://offline:8222",
            "description": "Not running",
            "capabilities": {},
            "skills": [],
        }
        registry.server_running = False
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["server_running"] is False
        assert result.output["name"] == "Offline Agent"

    @pytest.mark.asyncio
    async def test_whoami_no_registry(self):
        """No registry → returns server_running=False with message."""
        from amplifier_module_tool_a2a import A2ATool

        coordinator = _make_mock_coordinator(registry=None)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["server_running"] is False
        assert "message" in result.output

    @pytest.mark.asyncio
    async def test_whoami_no_card(self):
        """Registry exists but card=None → returns server_running=False with message."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_mock_registry()
        registry.card = None
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "whoami"})
        assert result.success is True
        assert result.output is not None
        assert result.output["server_running"] is False
        assert "message" in result.output


class TestAddContactOperation:
    """Task 7: add_contact fetches remote card, adds contact, caches card."""

    @pytest.mark.asyncio
    async def test_add_contact_fetches_card_and_adds_to_contacts(self):
        """Happy path: fetch card → add contact with correct name/tier."""
        from amplifier_module_tool_a2a import A2ATool

        remote_card = {
            "name": "Remote Agent",
            "url": "http://remote:8222",
            "capabilities": {"realtimeResponse": True},
            "skills": [{"id": "chat", "name": "Chat"}],
        }
        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(return_value=remote_card)

        result = await tool.execute(
            {"operation": "add_contact", "url": "http://remote:8222"}
        )
        assert result.success is True
        assert result.output["name"] == "Remote Agent"
        assert result.output["url"] == "http://remote:8222"
        assert result.output["tier"] == "known"
        registry.contact_store.add_contact.assert_awaited_once_with(
            "http://remote:8222", "Remote Agent", "known"
        )

    @pytest.mark.asyncio
    async def test_add_contact_caches_card(self):
        """After add_contact, registry.cache_card was called with the fetched card."""
        from amplifier_module_tool_a2a import A2ATool

        remote_card = {
            "name": "Remote Agent",
            "url": "http://remote:8222",
            "capabilities": {},
            "skills": [],
        }
        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(return_value=remote_card)

        await tool.execute({"operation": "add_contact", "url": "http://remote:8222"})
        registry.cache_card.assert_called_once_with("http://remote:8222", remote_card)

    @pytest.mark.asyncio
    async def test_add_contact_requires_url(self):
        """Missing url → error with helpful message."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        result = await tool.execute({"operation": "add_contact"})
        assert result.success is False
        assert result.error is not None
        assert "url" in result.error["message"].lower()

    @pytest.mark.asyncio
    async def test_add_contact_with_custom_tier(self):
        """tier='trusted' → contact added with trusted tier."""
        from amplifier_module_tool_a2a import A2ATool

        remote_card = {
            "name": "Trusted Agent",
            "url": "http://trusted:8222",
            "capabilities": {},
            "skills": [],
        }
        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(return_value=remote_card)

        result = await tool.execute(
            {
                "operation": "add_contact",
                "url": "http://trusted:8222",
                "tier": "trusted",
            }
        )
        assert result.success is True
        assert result.output["tier"] == "trusted"
        registry.contact_store.add_contact.assert_awaited_once_with(
            "http://trusted:8222", "Trusted Agent", "trusted"
        )

    @pytest.mark.asyncio
    async def test_add_contact_handles_unreachable_agent(self):
        """client.fetch_agent_card raises ConnectionError → error with helpful message."""
        from amplifier_module_tool_a2a import A2ATool

        registry = _make_registry_with_stores()
        coordinator = _make_mock_coordinator(registry=registry)
        tool = A2ATool(coordinator, {})

        tool.client = MagicMock()
        tool.client.fetch_agent_card = AsyncMock(
            side_effect=ConnectionError("Connection refused")
        )

        result = await tool.execute(
            {"operation": "add_contact", "url": "http://unreachable:8222"}
        )
        assert result.success is False
        assert result.error is not None
        assert "unreachable:8222" in result.error["message"]
