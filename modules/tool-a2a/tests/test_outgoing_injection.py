"""Tests for sender-side response injection via provider:request hook."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from amplifier_module_tool_a2a import A2ATool


def _make_tool() -> A2ATool:
    """Create an A2ATool with mocked coordinator and client."""
    coordinator = MagicMock()
    coordinator.get_capability = MagicMock(return_value=None)
    coordinator.mount = AsyncMock()
    coordinator.register_cleanup = MagicMock()
    config: dict = {}
    tool = A2ATool(coordinator, config)
    tool.client = MagicMock()
    tool.client.close = AsyncMock()
    return tool


def _completed_item(
    task_id: str = "abc123def456",
    agent_name: str = "TestAgent",
    status: str = "COMPLETED",
    text: str = "Here is my answer",
    attribution: str = "autonomous",
) -> dict:
    """Build a completed outgoing item for testing."""
    return {
        "task_id": task_id,
        "agent_url": "http://remote:8222",
        "agent_name": agent_name,
        "result": {
            "status": status,
            "artifacts": [{"parts": [{"text": text}]}],
            "attribution": attribution,
        },
    }


class TestNoInjectionWhenNoCompleted:
    """Empty _completed_outgoing returns HookResult(action='continue')."""

    @pytest.mark.asyncio
    async def test_no_injection_when_no_completed(self):
        tool = _make_tool()
        # _completed_outgoing is empty by default

        result = await tool._handle_outgoing_responses("provider:request", {})

        assert result.action == "continue"


class TestInjectsCompletedResponse:
    """One completed task produces inject_context with response text."""

    @pytest.mark.asyncio
    async def test_injects_completed_response(self):
        tool = _make_tool()
        tool._completed_outgoing.append(
            _completed_item(text="Here is my answer", status="COMPLETED")
        )

        result = await tool._handle_outgoing_responses("provider:request", {})

        assert result.action == "inject_context"
        assert "Here is my answer" in result.context_injection
        assert "COMPLETED" in result.context_injection


class TestInjectionIncludesAttribution:
    """Completed task attribution is displayed in injection text."""

    @pytest.mark.asyncio
    async def test_injection_includes_attribution(self):
        tool = _make_tool()
        tool._completed_outgoing.append(_completed_item(attribution="user_response"))

        result = await tool._handle_outgoing_responses("provider:request", {})

        assert result.action == "inject_context"
        assert "Answered by the user directly" in result.context_injection


class TestInjectionIsEphemeral:
    """HookResult has ephemeral=True."""

    @pytest.mark.asyncio
    async def test_injection_is_ephemeral(self):
        tool = _make_tool()
        tool._completed_outgoing.append(_completed_item())

        result = await tool._handle_outgoing_responses("provider:request", {})

        assert result.ephemeral is True


class TestInjectionClearsCompleted:
    """After injection, _completed_outgoing is empty."""

    @pytest.mark.asyncio
    async def test_injection_clears_completed(self):
        tool = _make_tool()
        tool._completed_outgoing.append(_completed_item())

        await tool._handle_outgoing_responses("provider:request", {})

        assert len(tool._completed_outgoing) == 0


class TestMultipleResponsesInjected:
    """Two completed tasks both appear in injection text."""

    @pytest.mark.asyncio
    async def test_multiple_responses_injected(self):
        tool = _make_tool()
        tool._completed_outgoing.append(
            _completed_item(
                task_id="task-aaa111",
                agent_name="AlphaAgent",
                text="First answer",
            )
        )
        tool._completed_outgoing.append(
            _completed_item(
                task_id="task-bbb222",
                agent_name="BetaAgent",
                text="Second answer",
            )
        )

        result = await tool._handle_outgoing_responses("provider:request", {})

        assert result.action == "inject_context"
        assert "AlphaAgent" in result.context_injection
        assert "First answer" in result.context_injection
        assert "BetaAgent" in result.context_injection
        assert "Second answer" in result.context_injection


class TestInjectionWrappedInSystemReminder:
    """The injected text must be wrapped in <system-reminder source="..."> tags
    per the ecosystem convention (see hooks-status-context / hooks-todo-reminder
    for the reference shape), so models can distinguish this system-injected
    content from an actual user request."""

    @pytest.mark.asyncio
    async def test_injection_wrapped_in_system_reminder(self):
        tool = _make_tool()
        tool._completed_outgoing.append(
            _completed_item(text="Here is my answer", status="COMPLETED")
        )

        result = await tool._handle_outgoing_responses("provider:request", {})

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert result.context_injection.startswith(
            '<system-reminder source="tool-a2a">'
        ), (
            f"Injection should open with the system-reminder wrapper; got: {result.context_injection!r}"
        )
        assert result.context_injection.endswith("</system-reminder>"), (
            f"Injection should close with the system-reminder wrapper; got: {result.context_injection!r}"
        )
        # Original content survives byte-identical inside the wrapper, including
        # its own inner <a2a-response> tags.
        assert "<a2a-response>" in result.context_injection
        assert "Here is my answer" in result.context_injection
        assert "COMPLETED" in result.context_injection
