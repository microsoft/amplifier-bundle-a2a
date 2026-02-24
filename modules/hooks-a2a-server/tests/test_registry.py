"""Tests for A2ARegistry shared state."""

import time


class TestKnownAgents:
    """Tests for the known agents list."""

    def test_empty_registry_has_no_agents(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.get_agents() == []

    def test_registry_loads_agents_from_config(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        agents = [
            {"name": "Alice", "url": "http://alice.local:8222"},
            {"name": "Bob", "url": "http://bob.local:8222"},
        ]
        registry = A2ARegistry(known_agents=agents)
        result = registry.get_agents()
        assert len(result) == 2
        assert result[0]["name"] == "Alice"
        assert result[0]["url"] == "http://alice.local:8222"
        assert result[1]["name"] == "Bob"

    def test_resolve_agent_url_by_name(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry(
            known_agents=[{"name": "Alice", "url": "http://alice.local:8222"}]
        )
        assert registry.resolve_agent_url("Alice") == "http://alice.local:8222"

    def test_resolve_agent_url_case_insensitive(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry(
            known_agents=[{"name": "Alice", "url": "http://alice.local:8222"}]
        )
        assert registry.resolve_agent_url("alice") == "http://alice.local:8222"
        assert registry.resolve_agent_url("ALICE") == "http://alice.local:8222"

    def test_resolve_agent_url_passthrough_for_urls(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.resolve_agent_url("http://custom:9999") == "http://custom:9999"
        assert (
            registry.resolve_agent_url("https://secure.host:8222")
            == "https://secure.host:8222"
        )

    def test_resolve_agent_url_returns_none_for_unknown(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.resolve_agent_url("nobody") is None


class TestTasks:
    """Tests for the task lifecycle."""

    def test_create_task_returns_id(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        message = {"role": "user", "parts": [{"text": "Hello"}]}
        task_id = registry.create_task(message)
        assert isinstance(task_id, str)
        assert len(task_id) > 0

    def test_create_task_stores_message_in_history(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        message = {"role": "user", "parts": [{"text": "Hello"}]}
        task_id = registry.create_task(message)
        task = registry.get_task(task_id)
        assert task is not None
        assert task["status"] == "SUBMITTED"
        assert task["history"] == [message]
        assert task["artifacts"] == []

    def test_update_task_status(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        registry.update_task(task_id, "WORKING")
        task = registry.get_task(task_id)
        assert task is not None
        assert task["status"] == "WORKING"

    def test_update_task_with_artifacts(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        artifacts = [{"parts": [{"text": "The answer is 42"}]}]
        registry.update_task(task_id, "COMPLETED", artifacts=artifacts)
        task = registry.get_task(task_id)
        assert task is not None
        assert task["status"] == "COMPLETED"
        assert task["artifacts"] == artifacts

    def test_update_task_with_error(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        registry.update_task(task_id, "FAILED", error="Something broke")
        task = registry.get_task(task_id)
        assert task is not None
        assert task["status"] == "FAILED"
        assert task["error"] == "Something broke"

    def test_get_task_returns_none_for_unknown_id(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.get_task("nonexistent-id") is None

    def test_task_ids_are_unique(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        msg = {"role": "user", "parts": [{"text": "Hi"}]}
        ids = {registry.create_task(msg) for _ in range(100)}
        assert len(ids) == 100


class TestCardCache:
    """Tests for the agent card cache."""

    def test_cache_and_retrieve_card(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        card = {"name": "Alice", "version": "1.0"}
        registry.cache_card("http://alice.local:8222", card, ttl=300)
        assert registry.get_cached_card("http://alice.local:8222") == card

    def test_cache_miss_returns_none(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.get_cached_card("http://unknown:8222") is None

    def test_expired_cache_returns_none(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        card = {"name": "Alice"}
        registry.cache_card("http://alice.local:8222", card, ttl=0)
        # TTL=0 means it expires immediately
        time.sleep(0.01)
        assert registry.get_cached_card("http://alice.local:8222") is None


class TestTaskAttribution:
    """Tests for the attribution field on tasks."""

    def test_task_has_no_attribution_by_default(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        task = registry.get_task(task_id)
        assert task is not None
        assert "attribution" not in task

    def test_update_task_with_attribution(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        registry.update_task(task_id, "WORKING", attribution="autonomous")
        task = registry.get_task(task_id)
        assert task is not None
        assert task["attribution"] == "autonomous"

    def test_attribution_survives_status_update(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        registry.update_task(task_id, "WORKING", attribution="autonomous")
        # Update status without passing attribution — it should be preserved
        registry.update_task(task_id, "COMPLETED")
        task = registry.get_task(task_id)
        assert task is not None
        assert task["attribution"] == "autonomous"

    def test_attribution_can_be_overwritten(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        task_id = registry.create_task({"role": "user", "parts": [{"text": "Hi"}]})
        registry.update_task(task_id, "WORKING", attribution="autonomous")
        registry.update_task(task_id, "INPUT_REQUIRED", attribution="user_response")
        task = registry.get_task(task_id)
        assert task is not None
        assert task["attribution"] == "user_response"

    def test_get_task_includes_attribution_only_when_set(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        # Task without attribution → no key
        tid1 = registry.create_task({"role": "user", "parts": [{"text": "A"}]})
        task1 = registry.get_task(tid1)
        assert task1 is not None
        assert "attribution" not in task1

        # Task with attribution → key present
        tid2 = registry.create_task({"role": "user", "parts": [{"text": "B"}]})
        registry.update_task(tid2, "WORKING", attribution="autonomous")
        task2 = registry.get_task(tid2)
        assert task2 is not None
        assert "attribution" in task2
        assert task2["attribution"] == "autonomous"


class TestCardAndServerRunningAttributes:
    """Tests for card and server_running attributes."""

    def test_registry_has_card_attribute_none_by_default(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.card is None

    def test_registry_card_can_be_set(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        card = {"name": "TestAgent", "version": "1.0", "skills": []}
        registry.card = card
        assert registry.card is card

    def test_registry_has_server_running_true_by_default(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.server_running is True

    def test_registry_server_running_can_be_set_false(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        registry.server_running = False
        assert registry.server_running is False


class TestDataLayerAttributes:
    """Tests for contact_store and pending_queue attributes."""

    def test_registry_has_contact_store_attribute_none_by_default(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.contact_store is None

    def test_registry_has_pending_queue_attribute_none_by_default(self):
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        assert registry.pending_queue is None

    def test_registry_contact_store_can_be_set(self):
        from amplifier_module_hooks_a2a_server.contacts import ContactStore
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        store = ContactStore(path=None)
        registry.contact_store = store
        assert registry.contact_store is store

    def test_registry_pending_queue_can_be_set(self):
        from amplifier_module_hooks_a2a_server.pending import PendingQueue
        from amplifier_module_hooks_a2a_server.registry import A2ARegistry

        registry = A2ARegistry()
        queue = PendingQueue(base_dir=None)
        registry.pending_queue = queue
        assert registry.pending_queue is queue
