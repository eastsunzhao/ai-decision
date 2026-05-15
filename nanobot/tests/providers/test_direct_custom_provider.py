"""Tests for direct OpenAI-compatible custom provider behavior."""

from types import SimpleNamespace
from unittest.mock import patch

from nanobot.providers.custom_provider import CustomProvider


def _provider(default_model: str = "deepseek-v4-flash") -> CustomProvider:
    with patch("nanobot.providers.custom_provider.AsyncOpenAI"):
        return CustomProvider(
            api_key="test-key",
            api_base="https://api.deepseek.com",
            default_model=default_model,
        )


def test_deepseek_v4_disables_thinking_without_reasoning_effort() -> None:
    provider = _provider()

    kwargs = provider._prepare_chat_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        reasoning_effort=None,
    )

    assert kwargs["model"] == "deepseek-v4-flash"
    assert kwargs["extra_body"] == {"thinking": {"type": "disabled"}}
    assert "reasoning_effort" not in kwargs


def test_deepseek_v4_enables_thinking_with_reasoning_effort() -> None:
    provider = _provider("deepseek-v4-pro")

    kwargs = provider._prepare_chat_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        reasoning_effort="high",
    )

    assert kwargs["model"] == "deepseek-v4-pro"
    assert kwargs["extra_body"] == {"thinking": {"type": "enabled"}}
    assert kwargs["reasoning_effort"] == "high"


def test_non_deepseek_v4_does_not_add_thinking_extra_body() -> None:
    provider = _provider("deepseek-chat")

    kwargs = provider._prepare_chat_kwargs(
        messages=[{"role": "user", "content": "hello"}],
        reasoning_effort=None,
    )

    assert "extra_body" not in kwargs


def test_parse_reads_reasoning_content_from_model_extra() -> None:
    provider = _provider()
    message = SimpleNamespace(
        content="final answer",
        tool_calls=None,
        model_extra={"reasoning_content": "hidden reasoning"},
    )
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=message, finish_reason="stop")],
        usage=SimpleNamespace(prompt_tokens=1, completion_tokens=2, total_tokens=3),
    )

    result = provider._parse(response)

    assert result.content == "final answer"
    assert result.reasoning_content == "hidden reasoning"
