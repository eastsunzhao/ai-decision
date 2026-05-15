from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest


def test_iteration_budget_tiers_compute_soft_limits(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    cases = {
        "low": 25,
        "medium": 50,
        "high": 75,
        "extra_high": 100,
    }
    for tier, expected in cases.items():
        loop = AgentLoop(
            bus=MessageBus(),
            provider=provider,
            workspace=tmp_path,
            model="test-model",
            max_iterations=100,
            active_skill="deep-research",
            iteration_budget=tier,
        )
        assert loop.soft_iteration_budget == expected


def test_no_active_skill_uses_short_default_caps(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=100,
    )

    assert loop.max_iterations == 8
    assert loop.soft_iteration_budget == 6


def test_simple_skill_caps_iterations_and_sets_soft_budget(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=100,
        active_skill="simple",
    )

    assert loop.max_iterations == 12
    assert loop.soft_iteration_budget == 10


def test_other_active_skills_keep_configured_budget(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        max_iterations=100,
        active_skill="deep-research",
        iteration_budget="medium",
    )

    assert loop.max_iterations == 100
    assert loop.soft_iteration_budget == 50


def test_iteration_budget_reminder_is_transient():
    from nanobot.agent.runner import AgentRunner

    assert AgentRunner._iteration_budget_message(4, 20, 10) is None
    assert "5 iterations" in AgentRunner._iteration_budget_message(5, 20, 10)
    assert "3 iterations" in AgentRunner._iteration_budget_message(7, 20, 10)
    assert "2 iterations" in AgentRunner._iteration_budget_message(8, 20, 10)
    assert "final allowed iteration" in AgentRunner._iteration_budget_message(9, 20, 10)
    assert "Do not call any more tools" in AgentRunner._iteration_budget_message(9, 20, 10)
    assert "exceeded the allowed tool-call limit" in AgentRunner._iteration_budget_message(11, 20, 10)
    assert "STOP calling tools now" in AgentRunner._iteration_budget_message(11, 20, 10)
    assert "exceeded the allowed tool-call limit" in AgentRunner._iteration_budget_message(12, 20, 10)

    original = [{"role": "user", "content": "work"}]
    with_reminder = AgentRunner._with_iteration_budget_reminder(
        original,
        iteration=5,
        max_iterations=20,
        soft_budget=10,
    )

    assert len(original) == 1
    assert len(with_reminder) == 2
    assert "5 iterations" in with_reminder[-1]["content"]


@pytest.mark.asyncio
async def test_spawn_tool_forwards_max_iterations():
    from nanobot.agent.tools.spawn import SpawnTool

    manager = MagicMock()
    manager.spawn = AsyncMock(return_value="started")
    tool = SpawnTool(manager=manager)

    result = await tool.execute("do task", label="S1", max_iterations=12)

    assert result == "started"
    manager.spawn.assert_awaited_once()
    assert manager.spawn.await_args.kwargs["max_iterations"] == 12


@pytest.mark.asyncio
async def test_spawn_max_iterations_reaches_subagent_runner(tmp_path):
    from nanobot.agent.subagent import SubagentManager, SubagentStatus
    from nanobot.bus.queue import MessageBus

    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path,
        bus=bus,
        preferred_data_source="forum",
    )
    mgr._announce_result = AsyncMock()

    async def fake_run(spec):
        assert spec.max_iterations == 12
        assert spec.tools.has("data_source_search")
        system_prompt = spec.initial_messages[0]["content"]
        assert "Preferred data sources: forum." in system_prompt
        assert "Use `data_source_search`" in system_prompt
        return SimpleNamespace(
            stop_reason="completed",
            final_content="done",
            error=None,
            tool_events=[],
        )

    mgr.runner.run = AsyncMock(side_effect=fake_run)

    status = SubagentStatus(
        task_id="sub-1",
        label="label",
        task_description="do task",
        started_at=0.0,
        last_activity_at=0.0,
    )
    await mgr._run_subagent(
        "sub-1",
        "do task",
        "label",
        {"channel": "test", "chat_id": "c1"},
        status,
        max_iterations=12,
    )

    mgr.runner.run.assert_awaited_once()


def test_subagent_system_prompt_uses_relative_workspace_text(tmp_path):
    from nanobot.agent.subagent import SubagentManager
    from nanobot.bus.queue import MessageBus

    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    mgr = SubagentManager(
        provider=provider,
        workspace=tmp_path / "workspace" / "temp" / "session-1",
        context_workspace=tmp_path / "workspace" / "_agent",
        bus=MessageBus(),
    )

    prompt = mgr._build_subagent_prompt()

    assert str(tmp_path) not in prompt
    assert "File and shell tools run relative to the current session workspace." in prompt
    assert "- `.`: session workspace for current run outputs" in prompt
    assert "- `shared_permanent/`:" in prompt
    assert "- `shared_context/`:" in prompt
