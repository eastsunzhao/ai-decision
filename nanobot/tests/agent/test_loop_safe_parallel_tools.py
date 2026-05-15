import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.agent.tools.filesystem import ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import WebSearchConfig
from nanobot.providers.base import LLMResponse, ToolCallRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


class _DelayTool(Tool):
    def __init__(
        self,
        name: str,
        *,
        delay: float,
        read_only: bool,
        events: list[str],
    ):
        self._name = name
        self._delay = delay
        self._read_only = read_only
        self._events = events

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return self._name

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}, "required": []}

    @property
    def read_only(self) -> bool:
        return self._read_only

    async def execute(self, **kwargs):
        self._events.append(f"start:{self.name}")
        await asyncio.sleep(self._delay)
        self._events.append(f"end:{self.name}")
        return f"result:{self.name}"


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    return AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        register_web_tools=False,
    )


def test_read_only_tools_are_concurrency_safe(tmp_path: Path) -> None:
    assert ReadFileTool(workspace=tmp_path).concurrency_safe is True
    assert ListDirTool(workspace=tmp_path).concurrency_safe is True
    assert WriteFileTool(workspace=tmp_path).concurrency_safe is False

    jina_search = WebSearchTool(config=WebSearchConfig(provider="jina", api_key="jina-key"))
    assert jina_search.concurrency_safe is True
    assert WebFetchTool().concurrency_safe is True

    assert WebSearchTool(config=WebSearchConfig(provider="jina", api_key="")).concurrency_safe is False
    assert WebSearchTool(config=WebSearchConfig(provider="duckduckgo")).concurrency_safe is False


@pytest.mark.anyio
async def test_loop_batches_only_consecutive_safe_tools(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    events: list[str] = []
    for tool in (
        _DelayTool("safe_a", delay=0.05, read_only=True, events=events),
        _DelayTool("safe_b", delay=0.05, read_only=True, events=events),
        _DelayTool("unsafe", delay=0.01, read_only=False, events=events),
        _DelayTool("safe_c", delay=0.01, read_only=True, events=events),
    ):
        loop.tools.register(tool)

    tool_calls = [
        ToolCallRequest(id="call_a", name="safe_a", arguments={}),
        ToolCallRequest(id="call_b", name="safe_b", arguments={}),
        ToolCallRequest(id="call_unsafe", name="unsafe", arguments={}),
        ToolCallRequest(id="call_c", name="safe_c", arguments={}),
    ]
    responses = iter([
        LLMResponse(content="", tool_calls=tool_calls),
        LLMResponse(content="done", tool_calls=[]),
    ])
    loop.provider.chat_with_retry = AsyncMock(side_effect=lambda *a, **kw: next(responses))
    loop.tools.get_definitions = MagicMock(return_value=[])

    final_content, tools_used, _ = await loop._run_agent_loop([])

    assert final_content == "done"
    assert tools_used == ["safe_a", "safe_b", "unsafe", "safe_c"]
    assert events[0:2] == ["start:safe_a", "start:safe_b"]
    assert events.index("end:safe_a") < events.index("start:unsafe")
    assert events.index("end:safe_b") < events.index("start:unsafe")
    assert events.index("end:unsafe") < events.index("start:safe_c")

    second_prompt = loop.provider.chat_with_retry.await_args_list[1].kwargs["messages"]
    tool_messages = [msg for msg in second_prompt if msg.get("role") == "tool"]
    assert [msg["name"] for msg in tool_messages] == [
        "safe_a",
        "safe_b",
        "unsafe",
        "safe_c",
    ]
    assert [msg["content"] for msg in tool_messages] == [
        "result:safe_a",
        "result:safe_b",
        "result:unsafe",
        "result:safe_c",
    ]
