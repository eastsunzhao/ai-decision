from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.bus.events import InboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.providers.base import GenerationSettings, LLMResponse, ToolCallRequest


def _make_loop(tmp_path: Path) -> AgentLoop:
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"
    provider.generation = GenerationSettings(max_tokens=4096)
    provider.estimate_prompt_tokens.return_value = (1, "test-counter")
    loop = AgentLoop(
        bus=MessageBus(),
        provider=provider,
        workspace=tmp_path,
        model="test-model",
    )
    loop.tools.get_definitions = MagicMock(return_value=[])
    return loop


def test_cancelled_turn_persists_current_user_message(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    async def cancelled_chat(**_kwargs):
        raise asyncio.CancelledError()

    loop.provider.chat_with_retry = cancelled_chat
    msg = InboundMessage(
        channel="web",
        sender_id="user",
        chat_id="10001:s1",
        content="first question",
    )

    async def run_turn() -> None:
        await loop._process_message(msg)

    try:
        asyncio.run(run_turn())
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError")

    loop.sessions.invalidate(msg.session_key)
    session = loop.sessions.get_or_create(msg.session_key)
    assert [(m["role"], m["content"]) for m in session.messages] == [
        ("user", "first question"),
    ]


def test_completed_turn_does_not_duplicate_presaved_user_message(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)
    tool_call = ToolCallRequest(
        id="call_1",
        name="read_file",
        arguments={"path": "missing.txt"},
    )
    responses = iter([
        LLMResponse(content="checking", tool_calls=[tool_call]),
        LLMResponse(content="done", tool_calls=[]),
    ])
    loop.provider.chat_with_retry = AsyncMock(side_effect=lambda **_kwargs: next(responses))
    loop.tools.execute = AsyncMock(return_value="tool result")  # type: ignore[method-assign]

    msg = InboundMessage(
        channel="web",
        sender_id="user",
        chat_id="10001:s2",
        content="please inspect",
    )
    asyncio.run(loop._process_message(msg))

    session = loop.sessions.get_or_create(msg.session_key)
    assert [m["role"] for m in session.messages] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert [m["content"] for m in session.messages if m["role"] == "user"] == [
        "please inspect",
    ]
    assert session.messages[1]["tool_calls"][0]["id"] == "call_1"
    assert session.messages[2]["tool_call_id"] == "call_1"
    assert session.messages[3]["content"] == "done"


def test_followup_after_cancelled_turn_includes_interrupted_question(tmp_path: Path) -> None:
    loop = _make_loop(tmp_path)

    async def cancelled_chat(**_kwargs):
        raise asyncio.CancelledError()

    loop.provider.chat_with_retry = cancelled_chat
    first = InboundMessage(
        channel="web",
        sender_id="user",
        chat_id="10001:s3",
        content="what is CCF TF?",
    )

    async def run_first_turn() -> None:
        await loop._process_message(first)

    try:
        asyncio.run(run_first_turn())
    except asyncio.CancelledError:
        pass
    else:
        raise AssertionError("expected CancelledError")

    captured_messages: list[dict] = []

    async def capture_chat(*, messages, **_kwargs):
        captured_messages[:] = messages
        return LLMResponse(content="followup answer", tool_calls=[])

    loop.provider.chat_with_retry = capture_chat
    second = InboundMessage(
        channel="web",
        sender_id="user",
        chat_id="10001:s3",
        content="how are their leaders?",
    )
    asyncio.run(loop._process_message(second))

    user_payloads = [
        str(message.get("content", ""))
        for message in captured_messages
        if message.get("role") == "user"
    ]
    assert any("what is CCF TF?" in payload for payload in user_payloads)
    assert any("how are their leaders?" in payload for payload in user_payloads)

    session = loop.sessions.get_or_create(first.session_key)
    assert [m["role"] for m in session.messages] == ["user", "user", "assistant"]
    assert [m["content"] for m in session.messages[:2]] == [
        "what is CCF TF?",
        "how are their leaders?",
    ]
