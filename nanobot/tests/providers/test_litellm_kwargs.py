"""Tests for OpenAICompatProvider spec-driven behavior.

Validates that:
- OpenRouter (no strip) keeps model names intact.
- AiHubMix (strip_model_prefix=True) strips provider prefixes.
- Standard providers pass model names through as-is.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from nanobot.providers.openai_compat_provider import OpenAICompatProvider
from nanobot.providers import openai_compat_provider
from nanobot.providers.base import llm_session_context
from nanobot.providers.registry import find_by_name


def _fake_chat_response(content: str = "ok") -> SimpleNamespace:
    """Build a minimal OpenAI chat completion response."""
    message = SimpleNamespace(
        content=content,
        tool_calls=None,
        reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


class _AsyncStream:
    def __init__(self, chunks: list[SimpleNamespace]):
        self._chunks = list(chunks)

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._chunks:
            raise StopAsyncIteration
        return self._chunks.pop(0)


def _fake_stream_chunk(content: str | None = None, finish_reason: str | None = None) -> SimpleNamespace:
    delta = SimpleNamespace(content=content, reasoning_content=None, reasoning=None, tool_calls=None)
    choice = SimpleNamespace(delta=delta, finish_reason=finish_reason)
    return SimpleNamespace(choices=[choice], usage=None)


def _fake_tool_call_response() -> SimpleNamespace:
    """Build a minimal chat response that includes Gemini-style extra_content."""
    function = SimpleNamespace(
        name="exec",
        arguments='{"cmd":"ls"}',
        provider_specific_fields={"inner": "value"},
    )
    tool_call = SimpleNamespace(
        id="call_123",
        index=0,
        type="function",
        function=function,
        extra_content={"google": {"thought_signature": "signed-token"}},
    )
    message = SimpleNamespace(
        content=None,
        tool_calls=[tool_call],
        reasoning_content=None,
    )
    choice = SimpleNamespace(message=message, finish_reason="tool_calls")
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage)


def test_openrouter_spec_is_gateway() -> None:
    spec = find_by_name("openrouter")
    assert spec is not None
    assert spec.is_gateway is True
    assert spec.default_api_base == "https://openrouter.ai/api/v1"


@pytest.mark.asyncio
async def test_openrouter_keeps_model_name_intact() -> None:
    """OpenRouter gateway keeps the full model name (gateway does its own routing)."""
    mock_create = AsyncMock(return_value=_fake_chat_response())
    spec = find_by_name("openrouter")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        client_instance = MockClient.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="sk-or-test-key",
            api_base="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-sonnet-4-5",
            spec=spec,
        )
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="anthropic/claude-sonnet-4-5",
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "anthropic/claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_aihubmix_strips_model_prefix() -> None:
    """AiHubMix strips the provider prefix (strip_model_prefix=True)."""
    mock_create = AsyncMock(return_value=_fake_chat_response())
    spec = find_by_name("aihubmix")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        client_instance = MockClient.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="sk-aihub-test-key",
            api_base="https://aihubmix.com/v1",
            default_model="claude-sonnet-4-5",
            spec=spec,
        )
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="anthropic/claude-sonnet-4-5",
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "claude-sonnet-4-5"


@pytest.mark.asyncio
async def test_standard_provider_passes_model_through() -> None:
    """Standard provider (e.g. deepseek) passes model name through as-is."""
    mock_create = AsyncMock(return_value=_fake_chat_response())
    spec = find_by_name("deepseek")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        client_instance = MockClient.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        await provider.chat(
            messages=[{"role": "user", "content": "hello"}],
            model="deepseek-chat",
        )

    call_kwargs = mock_create.call_args.kwargs
    assert call_kwargs["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_deepseek_trace_disabled_does_not_create_log(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "deepseek_trace.jsonl"
    monkeypatch.delenv("NANOBOT_DEEPSEEK_TRACE", raising=False)
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE_PATH", str(trace_path))
    mock_create = AsyncMock(return_value=_fake_chat_response("ok"))
    spec = find_by_name("deepseek")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = mock_create
        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert not trace_path.exists()


@pytest.mark.asyncio
async def test_deepseek_trace_records_chat_payload_and_response(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "deepseek_trace.jsonl"
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE", "true")
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE_PATH", str(trace_path))
    mock_create = AsyncMock(return_value=_fake_chat_response("deepseek says ok"))
    spec = find_by_name("deepseek")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = mock_create
        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        await provider.chat(
            messages=[
                {"role": "system", "content": "system prompt"},
                {"role": "user", "content": "hello"},
            ],
        )

    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    record = records[0]
    assert record["provider"] == "deepseek"
    assert record["mode"] == "chat"
    assert record["model"] == "deepseek-chat"
    assert record["payload"]["messages"][0] == {"role": "system", "content": "system prompt"}
    assert record["payload"]["messages"][1] == {"role": "user", "content": "hello"}
    assert record["response"]["content"] == "deepseek says ok"
    assert record["response"]["finish_reason"] == "stop"
    assert record["response"]["usage"]["total_tokens"] == 15


@pytest.mark.asyncio
async def test_deepseek_trace_defaults_to_debug_directory(tmp_path, monkeypatch) -> None:
    trace_dir = tmp_path / "nanobot_deepseek_trace" / "tracelog"
    monkeypatch.setattr(openai_compat_provider, "_DEEPSEEK_TRACE_DIR", trace_dir)
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE", "1")
    monkeypatch.delenv("NANOBOT_DEEPSEEK_TRACE_PATH", raising=False)
    mock_create = AsyncMock(return_value=_fake_chat_response("ok"))
    spec = find_by_name("deepseek")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = mock_create
        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        with llm_session_context("web:10001:abcd1234efgh"):
            await provider.chat(messages=[{"role": "user", "content": "hello"}])

    matches = list(trace_dir.glob("deepseek_trace_*_abcd.jsonl"))
    assert len(matches) == 1
    trace_path = matches[0]
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["payload"]["model"] == "deepseek-chat"


@pytest.mark.asyncio
async def test_deepseek_trace_records_stream_after_completion(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "deepseek_trace.jsonl"
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE", "1")
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE_PATH", str(trace_path))
    stream = _AsyncStream([
        _fake_stream_chunk("hello "),
        _fake_stream_chunk("world", finish_reason="stop"),
    ])
    mock_create = AsyncMock(return_value=stream)
    spec = find_by_name("deepseek")
    deltas: list[str] = []

    async def on_delta(text: str) -> None:
        deltas.append(text)

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = mock_create
        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        result = await provider.chat_stream(
            messages=[{"role": "user", "content": "hello"}],
            on_content_delta=on_delta,
        )

    assert result.content == "hello world"
    assert deltas == ["hello ", "world"]
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["mode"] == "stream"
    assert records[0]["payload"]["stream"] is True
    assert records[0]["payload"]["stream_options"] == {"include_usage": True}
    assert records[0]["response"]["content"] == "hello world"


@pytest.mark.asyncio
async def test_deepseek_trace_ignores_non_deepseek_provider(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "deepseek_trace.jsonl"
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE", "true")
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE_PATH", str(trace_path))
    mock_create = AsyncMock(return_value=_fake_chat_response("ok"))
    spec = find_by_name("openrouter")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = mock_create
        provider = OpenAICompatProvider(
            api_key="sk-or-test-key",
            api_base="https://openrouter.ai/api/v1",
            default_model="anthropic/claude-sonnet-4-5",
            spec=spec,
        )
        await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert not trace_path.exists()


@pytest.mark.asyncio
async def test_deepseek_trace_records_error_response(tmp_path, monkeypatch) -> None:
    trace_path = tmp_path / "deepseek_trace.jsonl"
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE", "yes")
    monkeypatch.setenv("NANOBOT_DEEPSEEK_TRACE_PATH", str(trace_path))
    mock_create = AsyncMock(side_effect=RuntimeError("boom"))
    spec = find_by_name("deepseek")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        MockClient.return_value.chat.completions.create = mock_create
        provider = OpenAICompatProvider(
            api_key="sk-deepseek-test-key",
            default_model="deepseek-chat",
            spec=spec,
        )
        result = await provider.chat(messages=[{"role": "user", "content": "hello"}])

    assert result.finish_reason == "error"
    records = [json.loads(line) for line in trace_path.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["error"]["finish_reason"] == "error"
    assert "boom" in records[0]["error"]["content"]


@pytest.mark.asyncio
async def test_openai_compat_preserves_extra_content_on_tool_calls() -> None:
    """Gemini extra_content (thought signatures) must survive parse→serialize round-trip."""
    mock_create = AsyncMock(return_value=_fake_tool_call_response())
    spec = find_by_name("gemini")

    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI") as MockClient:
        client_instance = MockClient.return_value
        client_instance.chat.completions.create = mock_create

        provider = OpenAICompatProvider(
            api_key="test-key",
            api_base="https://generativelanguage.googleapis.com/v1beta/openai/",
            default_model="google/gemini-3.1-pro-preview",
            spec=spec,
        )
        result = await provider.chat(
            messages=[{"role": "user", "content": "run exec"}],
            model="google/gemini-3.1-pro-preview",
        )

    assert len(result.tool_calls) == 1
    tool_call = result.tool_calls[0]
    assert tool_call.extra_content == {"google": {"thought_signature": "signed-token"}}
    assert tool_call.function_provider_specific_fields == {"inner": "value"}

    serialized = tool_call.to_openai_tool_call()
    assert serialized["extra_content"] == {"google": {"thought_signature": "signed-token"}}
    assert serialized["function"]["provider_specific_fields"] == {"inner": "value"}


def test_openai_model_passthrough() -> None:
    """OpenAI models pass through unchanged."""
    spec = find_by_name("openai")
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            default_model="gpt-4o",
            spec=spec,
        )
    assert provider.get_default_model() == "gpt-4o"


def test_custom_gpt5_uses_max_completion_tokens() -> None:
    """GPT-5 style custom endpoints require max_completion_tokens."""
    spec = find_by_name("custom")
    with patch("nanobot.providers.openai_compat_provider.AsyncOpenAI"):
        provider = OpenAICompatProvider(
            api_key="sk-test-key",
            api_base="https://example.test/openai/v1",
            default_model="gpt-5.4-2026-03-05",
            spec=spec,
        )

    kwargs = provider._build_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        tools=None,
        model=None,
        max_tokens=1234,
        temperature=0.1,
        reasoning_effort=None,
        tool_choice=None,
    )

    assert kwargs["max_completion_tokens"] == 1234
    assert "max_tokens" not in kwargs
