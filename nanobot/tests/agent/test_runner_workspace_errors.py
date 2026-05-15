from unittest.mock import MagicMock

import pytest

from nanobot.agent.runner import AgentRunSpec, AgentRunner
from nanobot.providers.base import ToolCallRequest


class _WorkspaceErrorTools:
    async def execute(self, name: str, params: dict) -> str:
        return "Error: working_dir is outside the configured workspace"


@pytest.mark.anyio
async def test_workspace_guard_error_is_returned_to_agent_as_recoverable() -> None:
    runner = AgentRunner(provider=MagicMock())
    spec = AgentRunSpec(
        initial_messages=[],
        tools=_WorkspaceErrorTools(),  # type: ignore[arg-type]
        model="test-model",
        max_iterations=3,
        max_tool_result_chars=10_000,
        fail_on_tool_error=True,
    )
    tool_call = ToolCallRequest(id="call_1", name="exec", arguments={})

    result, event, exc = await runner._run_tool(spec, tool_call, {})

    assert exc is None
    assert event["status"] == "error"
    assert event["detail"].startswith("workspace_violation:")
    assert result.startswith("Error: working_dir is outside the configured workspace")
    assert "[Analyze the error above and try a different approach.]" in result
