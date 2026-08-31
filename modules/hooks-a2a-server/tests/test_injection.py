"""Tests for A2A approval injection into active session via provider:request hook."""

import pytest


class TestNoInjectionWhenEmpty:
    """When there are no pending approvals, handler returns continue."""

    @pytest.mark.asyncio
    async def test_no_injection_when_no_pending_approvals(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.action == "continue"


class TestInjectsPendingApproval:
    """When there is a pending approval, handler injects context."""

    @pytest.mark.asyncio
    async def test_injects_pending_approval(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://ben-laptop.local:8222",
            sender_name="Ben's Agent",
            message={
                "role": "user",
                "parts": [{"text": "What restaurant do you want tonight?"}],
            },
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "Ben's Agent" in result.context_injection
        assert "http://ben-laptop.local:8222" in result.context_injection

    @pytest.mark.asyncio
    async def test_injects_multiple_approvals(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://agent-a:8222",
            sender_name="Agent A",
            message={"role": "user", "parts": [{"text": "Hello from A"}]},
        )
        await queue.add_approval(
            task_id="task-2",
            sender_url="http://agent-b:9000",
            sender_name="Agent B",
            message={"role": "user", "parts": [{"text": "Hello from B"}]},
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "Agent A" in result.context_injection
        assert "Agent B" in result.context_injection
        assert "http://agent-a:8222" in result.context_injection
        assert "http://agent-b:9000" in result.context_injection


class TestInjectionIsEphemeral:
    """The HookResult must have ephemeral=True."""

    @pytest.mark.asyncio
    async def test_injection_is_ephemeral(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://ben:8222",
            sender_name="Ben's Agent",
            message={"role": "user", "parts": [{"text": "Hi"}]},
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.ephemeral is True


class TestInjectionSkipsAlreadyInjectedIds:
    """Already-injected items are not re-injected, but new items ARE."""

    @pytest.mark.asyncio
    async def test_injection_skips_already_injected_ids(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://ben:8222",
            sender_name="Ben's Agent",
            message={"role": "user", "parts": [{"text": "Hi"}]},
        )
        handler = A2AInjectionHandler(queue)

        # First call: should inject
        result1 = await handler("provider:request", {})
        assert result1.action == "inject_context"

        # Second call: same item, no new items → continue
        result2 = await handler("provider:request", {})
        assert result2.action == "continue"


class TestInjectionTextContent:
    """Injection text contains expected sender info and instructions."""

    @pytest.mark.asyncio
    async def test_injection_text_contains_sender_info(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://ben-laptop.local:8222",
            sender_name="Ben's Agent",
            message={
                "role": "user",
                "parts": [{"text": "What restaurant do you want tonight?"}],
            },
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        text = result.context_injection
        assert text is not None
        # Contains sender name and URL
        assert "Ben's Agent" in text
        assert "http://ben-laptop.local:8222" in text
        # Contains the message text
        assert "What restaurant do you want tonight?" in text
        # Contains approve/block instructions
        assert 'operation="approve"' in text
        assert 'operation="block"' in text
        # Wrapped in XML-like tags
        assert "<a2a-approval-request>" in text
        assert "</a2a-approval-request>" in text


class TestInjectsPendingMessage:
    """When there is a pending message from a known contact, handler injects it."""

    @pytest.mark.asyncio
    async def test_injects_pending_message(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_message(
            task_id="abc-123",
            sender_url="http://ben-laptop.local:8222",
            sender_name="Ben's Agent",
            message={
                "role": "user",
                "parts": [{"text": "What restaurant do you want tonight?"}],
            },
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert "Ben's Agent" in result.context_injection
        assert "http://ben-laptop.local:8222" in result.context_injection
        assert "<a2a-pending-messages>" in result.context_injection
        assert "</a2a-pending-messages>" in result.context_injection


class TestInjectsBothApprovalsAndMessages:
    """When both pending approvals and messages exist, a single injection has both."""

    @pytest.mark.asyncio
    async def test_injects_both_approvals_and_messages(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-approval",
            sender_url="http://unknown-agent:9000",
            sender_name="Unknown Agent",
            message={"role": "user", "parts": [{"text": "Let me in"}]},
        )
        await queue.add_message(
            task_id="task-msg",
            sender_url="http://ben-laptop.local:8222",
            sender_name="Ben's Agent",
            message={"role": "user", "parts": [{"text": "Hello friend"}]},
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.action == "inject_context"
        text = result.context_injection
        assert text is not None
        # Both sections present
        assert "<a2a-approval-request>" in text
        assert "</a2a-approval-request>" in text
        assert "<a2a-pending-messages>" in text
        assert "</a2a-pending-messages>" in text
        # Both agents mentioned
        assert "Unknown Agent" in text
        assert "Ben's Agent" in text


class TestMessageInjectionTextContent:
    """Message injection text contains respond/dismiss instructions with task_id."""

    @pytest.mark.asyncio
    async def test_message_injection_text_contains_respond_instructions(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_message(
            task_id="abc-123",
            sender_url="http://ben-laptop.local:8222",
            sender_name="Ben's Agent",
            message={
                "role": "user",
                "parts": [{"text": "What restaurant do you want tonight?"}],
            },
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        text = result.context_injection
        assert text is not None
        # Contains sender info
        assert "Ben's Agent" in text
        assert "http://ben-laptop.local:8222" in text
        # Contains the message text
        assert "What restaurant do you want tonight?" in text
        # Contains respond and dismiss instructions with task_id
        assert 'operation="respond"' in text
        assert 'task_id="abc-123"' in text
        assert 'operation="dismiss"' in text


class TestInjectionTracksBothTypes:
    """Approvals and messages are tracked by separate task_ids; new items still inject."""

    @pytest.mark.asyncio
    async def test_new_message_injected_after_approval(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        # Add an approval — this will trigger injection on first call
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://agent:8222",
            sender_name="Agent",
            message={"role": "user", "parts": [{"text": "Hi"}]},
        )
        handler = A2AInjectionHandler(queue)

        # First call: injects the approval
        result1 = await handler("provider:request", {})
        assert result1.action == "inject_context"

        # Now add a message AFTER first injection
        await queue.add_message(
            task_id="task-2",
            sender_url="http://ben:8222",
            sender_name="Ben",
            message={"role": "user", "parts": [{"text": "Hey"}]},
        )

        # Second call: SHOULD inject the new message (different task_id)
        result2 = await handler("provider:request", {})
        assert result2.action == "inject_context"
        assert result2.context_injection is not None
        assert "Ben" in result2.context_injection


class TestLiveInjection:
    """Tests for continuous injection (Mode B) — injects new items on every call."""

    async def test_injects_new_message_on_second_call(self):
        """After first injection, a new message arriving should be injected on the next call."""
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue.__new__(PendingQueue)
        queue._messages = [
            {
                "task_id": "task-1",
                "sender_url": "http://alice:8222",
                "sender_name": "Alice",
                "message": {"parts": [{"text": "First message"}]},
                "status": "pending",
            }
        ]
        queue._approvals = []

        handler = A2AInjectionHandler(queue)

        # First call — injects task-1
        result1 = await handler("provider:request", {})
        assert result1.action == "inject_context"

        # Add a new message
        queue._messages.append(
            {
                "task_id": "task-2",
                "sender_url": "http://bob:8222",
                "sender_name": "Bob",
                "message": {"parts": [{"text": "Second message"}]},
                "status": "pending",
            }
        )

        # Second call — should inject task-2 (but not task-1 again)
        result2 = await handler("provider:request", {})
        assert result2.action == "inject_context"
        assert result2.context_injection is not None
        assert "Bob" in result2.context_injection
        assert "Alice" not in result2.context_injection

    async def test_no_injection_when_no_new_items(self):
        """After injecting all items, next call with no new items returns continue."""
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue.__new__(PendingQueue)
        queue._messages = [
            {
                "task_id": "task-1",
                "sender_url": "http://alice:8222",
                "sender_name": "Alice",
                "message": {"parts": [{"text": "Hello"}]},
                "status": "pending",
            }
        ]
        queue._approvals = []

        handler = A2AInjectionHandler(queue)

        # First call — injects
        result1 = await handler("provider:request", {})
        assert result1.action == "inject_context"

        # Second call — nothing new
        result2 = await handler("provider:request", {})
        assert result2.action == "continue"

    async def test_tracks_injected_ids(self):
        """The handler exposes _injected_ids for use by defer operation."""
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue.__new__(PendingQueue)
        queue._messages = [
            {
                "task_id": "task-1",
                "sender_url": "http://alice:8222",
                "sender_name": "Alice",
                "message": {"parts": [{"text": "Hello"}]},
                "status": "pending",
            }
        ]
        queue._approvals = []

        handler = A2AInjectionHandler(queue)
        await handler("provider:request", {})

        assert "task-1" in handler._injected_ids

    async def test_skips_deferred_items(self):
        """Items with status='deferred' are never injected."""
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue.__new__(PendingQueue)
        queue._messages = [
            {
                "task_id": "task-1",
                "sender_url": "http://alice:8222",
                "sender_name": "Alice",
                "message": {"parts": [{"text": "Hello"}]},
                "status": "deferred",
            }
        ]
        queue._approvals = []

        handler = A2AInjectionHandler(queue)
        result = await handler("provider:request", {})
        assert result.action == "continue"

    async def test_new_approval_injected_on_later_call(self):
        """A new approval arriving after first injection is injected on next call."""
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue.__new__(PendingQueue)
        queue._messages = []
        queue._approvals = [
            {
                "task_id": "approval-1",
                "sender_url": "http://alice:8222",
                "sender_name": "Alice",
                "message": {"parts": [{"text": "Hi"}]},
                "status": "pending",
            }
        ]

        handler = A2AInjectionHandler(queue)

        # First call — injects approval-1
        result1 = await handler("provider:request", {})
        assert result1.action == "inject_context"

        # Add a new approval
        queue._approvals.append(
            {
                "task_id": "approval-2",
                "sender_url": "http://bob:8222",
                "sender_name": "Bob",
                "message": {"parts": [{"text": "Hey"}]},
                "status": "pending",
            }
        )

        # Second call — should inject approval-2
        result2 = await handler("provider:request", {})
        assert result2.action == "inject_context"
        assert result2.context_injection is not None
        assert "Bob" in result2.context_injection


class TestInjectionWrappedInSystemReminder:
    """The injected text must be wrapped in <system-reminder source="..."> tags
    per the ecosystem convention (see hooks-status-context / hooks-todo-reminder
    for the reference shape), so models can distinguish this system-injected
    content from an actual user request."""

    @pytest.mark.asyncio
    async def test_injection_wrapped_in_system_reminder(self, tmp_path):
        from amplifier_module_hooks_a2a_server.injection import A2AInjectionHandler
        from amplifier_module_hooks_a2a_server.pending import PendingQueue

        queue = PendingQueue(base_dir=tmp_path)
        await queue.add_approval(
            task_id="task-1",
            sender_url="http://ben-laptop.local:8222",
            sender_name="Ben's Agent",
            message={
                "role": "user",
                "parts": [{"text": "What restaurant do you want tonight?"}],
            },
        )
        handler = A2AInjectionHandler(queue)

        result = await handler("provider:request", {})

        assert result.action == "inject_context"
        assert result.context_injection is not None
        assert result.context_injection.startswith(
            '<system-reminder source="hooks-a2a-server">'
        ), (
            f"Injection should open with the system-reminder wrapper; got: {result.context_injection!r}"
        )
        assert result.context_injection.endswith("</system-reminder>"), (
            f"Injection should close with the system-reminder wrapper; got: {result.context_injection!r}"
        )
        # Original content survives byte-identical inside the wrapper, including
        # its own inner <a2a-approval-request> tags.
        assert "<a2a-approval-request>" in result.context_injection
        assert "Ben's Agent" in result.context_injection
