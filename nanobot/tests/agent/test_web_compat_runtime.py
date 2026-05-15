from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.base import Tool
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig, WebSearchConfig
from nanobot.providers.base import LLMResponse, ToolCallRequest


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


def _provider() -> MagicMock:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation.max_tokens = 8192
    return provider


class _EchoTool(Tool):
    @property
    def name(self) -> str:
        return "echo_tool"

    @property
    def description(self) -> str:
        return "Echo test tool"

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {"value": {"type": "string"}},
            "required": ["value"],
        }

    async def execute(self, value: str) -> str:
        return f"echo-result:{value}"


class _LargeTool(Tool):
    @property
    def name(self) -> str:
        return "large_tool"

    @property
    def description(self) -> str:
        return "Return a large test payload"

    @property
    def parameters(self) -> dict:
        return {"type": "object", "properties": {}}

    async def execute(self) -> str:
        return "x" * 5_000


def test_web_compat_tool_profile_enables_grep_glob_only_for_new_search_tools(tmp_path: Path) -> None:
    tool_workspace = tmp_path / "workspace"
    context_workspace = tool_workspace / "_agent"
    permanent_workspace = tool_workspace / "permanent"
    session_temp = tool_workspace / "temp" / "session-a"
    session_temp.mkdir(parents=True)
    context_workspace.mkdir(parents=True)
    permanent_workspace.mkdir(parents=True)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=session_temp,
        context_workspace=context_workspace,
        exec_working_dir=session_temp,
        model="test-model",
        restrict_to_workspace=True,
    )

    names = set(loop.tools.tool_names)
    assert {"grep", "glob", "data_source_search"}.issubset(names)
    assert "ask_user" not in names
    assert "notebook_edit" not in names
    assert "my" not in names


def test_web_compat_empty_active_skill_disables_skill_context(tmp_path: Path) -> None:
    tool_workspace = tmp_path / "workspace"
    context_workspace = tool_workspace / "_agent"
    permanent_workspace = tool_workspace / "permanent"
    session_temp = tool_workspace / "temp" / "session-a"
    session_temp.mkdir(parents=True)
    context_workspace.mkdir(parents=True)
    permanent_workspace.mkdir(parents=True)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=session_temp,
        context_workspace=context_workspace,
        exec_working_dir=session_temp,
        model="test-model",
        restrict_to_workspace=True,
    )

    assert loop._active_skill_names() == []


def test_non_web_empty_active_skill_keeps_auto_skill_context(tmp_path: Path) -> None:
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=tmp_path,
        model="test-model",
    )

    assert loop._active_skill_names() is None


def test_web_compat_exec_uses_session_temp_and_jina_defaults(tmp_path: Path) -> None:
    tool_workspace = tmp_path / "workspace"
    context_workspace = tool_workspace / "_agent"
    permanent_workspace = tool_workspace / "permanent"
    session_temp = tool_workspace / "temp" / "session-a"
    session_temp.mkdir(parents=True)
    context_workspace.mkdir(parents=True)
    permanent_workspace.mkdir(parents=True)

    search_config = WebSearchConfig(api_key="jina-key")
    exec_config = ExecToolConfig(tool_call_exec_output_enabled=True)
    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=session_temp,
        context_workspace=context_workspace,
        exec_working_dir=session_temp,
        model="test-model",
        web_search_config=search_config,
        exec_config=exec_config,
        restrict_to_workspace=True,
    )

    exec_tool = loop.tools.get("exec")
    web_tool = loop.tools.get("web_search")

    assert getattr(exec_tool, "working_dir") == str(session_temp)
    assert getattr(exec_tool, "tool_call_exec_output_enabled") is True
    assert getattr(web_tool, "config").provider == "jina"
    assert getattr(web_tool, "config").api_key == "jina-key"


@pytest.mark.anyio
async def test_web_bus_progress_emits_tool_result_output(tmp_path: Path) -> None:
    bus = MessageBus()
    provider = _provider()
    responses = iter([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_echo",
                    name="echo_tool",
                    arguments={"value": "hello"},
                )
            ],
        ),
        LLMResponse(content="done"),
    ])
    provider.chat_with_retry = AsyncMock(side_effect=lambda *args, **kwargs: next(responses))

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        register_web_tools=False,
    )
    loop.tools.register(_EchoTool())

    final = await loop.process_direct("run echo", session_key="web:test", channel="web", chat_id="chat")

    assert final is not None
    assert final.content == "done"

    progress_messages = []
    while bus.outbound_size:
        progress_messages.append(await bus.consume_outbound())

    trace_items = [
        (msg.content, msg.metadata.get("_trace_meta"))
        for msg in progress_messages
        if isinstance(msg.metadata.get("_trace_meta"), dict)
    ]
    trace_types = [meta["event_type"] for _, meta in trace_items]
    assert trace_types == ["tool_call_start", "tool_call_output", "tool_call_end"]

    output_text, output_meta = trace_items[1]
    assert output_text == "echo-result:hello"
    assert output_meta["tool_call_id"] == "call_echo"
    assert output_meta["tool_name"] == "echo_tool"
    assert output_meta["stream"] == "result"

    _, end_meta = trace_items[2]
    assert end_meta["status"] == "ok"
    assert end_meta["result_preview"] == "echo-result:hello"


@pytest.mark.anyio
async def test_web_tool_result_files_are_saved_under_session_temp(tmp_path: Path) -> None:
    tool_workspace = tmp_path / "workspace"
    context_workspace = tool_workspace / "_agent"
    permanent_workspace = tool_workspace / "permanent"
    session_temp = tool_workspace / "temp" / "session-a"
    session_temp.mkdir(parents=True)
    context_workspace.mkdir(parents=True)
    permanent_workspace.mkdir(parents=True)

    bus = MessageBus()
    provider = _provider()
    responses = iter([
        LLMResponse(
            content="",
            tool_calls=[
                ToolCallRequest(
                    id="call_large",
                    name="large_tool",
                    arguments={},
                )
            ],
        ),
        LLMResponse(content="done"),
    ])
    provider.chat_with_retry = AsyncMock(side_effect=lambda *args, **kwargs: next(responses))

    loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=session_temp,
        context_workspace=context_workspace,
        exec_working_dir=session_temp,
        model="test-model",
        max_tool_result_chars=100,
        restrict_to_workspace=True,
        register_web_tools=False,
    )
    loop.tools.register(_LargeTool())

    final = await loop.process_direct("run large", session_key="web:test", channel="web", chat_id="chat")

    assert final is not None
    assert (session_temp / ".nanobot" / "tool-results" / "web_test" / "call_large.txt").exists()
    assert not (tool_workspace / ".nanobot" / "tool-results" / "web_test" / "call_large.txt").exists()


@pytest.mark.anyio
async def test_web_compat_write_file_can_export_via_shared_permanent_symlink(tmp_path: Path) -> None:
    tool_workspace = tmp_path / "workspace"
    context_workspace = tool_workspace / "_agent"
    permanent_workspace = tool_workspace / "permanent"
    session_temp = tool_workspace / "temp" / "session-a"
    session_temp.mkdir(parents=True)
    context_workspace.mkdir(parents=True)
    permanent_workspace.mkdir(parents=True)
    (session_temp / "shared_permanent").symlink_to("../../permanent", target_is_directory=True)

    loop = AgentLoop(
        bus=MessageBus(),
        provider=_provider(),
        workspace=session_temp,
        context_workspace=context_workspace,
        exec_working_dir=session_temp,
        model="test-model",
        restrict_to_workspace=True,
    )

    result = await loop.tools.get("write_file").execute(
        path="shared_permanent/export.md",
        content="# Exported",
    )

    assert result.startswith("Successfully wrote")
    assert (permanent_workspace / "export.md").read_text(encoding="utf-8") == "# Exported"


@pytest.mark.anyio
async def test_streaming_keeps_pre_tool_thought_progress(tmp_path: Path) -> None:
    provider = _provider()
    responses = iter([
        LLMResponse(
            content="I will first make a plan before using tools.",
            tool_calls=[
                ToolCallRequest(
                    id="call_echo",
                    name="echo_tool",
                    arguments={"value": "hello"},
                )
            ],
        ),
        LLMResponse(content="done"),
    ])
    provider.chat_stream_with_retry = AsyncMock(side_effect=lambda *args, **kwargs: next(responses))

    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
        register_web_tools=False,
    )
    loop.tools.register(_EchoTool())

    progress: list[tuple[str, bool]] = []
    deltas: list[str] = []

    async def on_progress(content: str, *, tool_hint: bool = False, **kwargs) -> None:
        progress.append((content, tool_hint))

    async def on_stream(delta: str) -> None:
        deltas.append(delta)

    async def on_stream_end(**kwargs) -> None:
        pass

    final, _, _ = await loop._run_agent_loop(
        [],
        on_progress=on_progress,
        on_stream=on_stream,
        on_stream_end=on_stream_end,
    )

    assert final == "done"
    assert ("I will first make a plan before using tools.", False) in progress
    assert deltas == []
