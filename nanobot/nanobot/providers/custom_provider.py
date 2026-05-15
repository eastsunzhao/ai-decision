"""Direct OpenAI-compatible provider — bypasses LiteLLM."""

from __future__ import annotations

import os
import uuid
from typing import Any

import httpx
import json_repair
from openai import AsyncOpenAI

from nanobot.providers.base import LLMProvider, LLMResponse, ToolCallRequest


class CustomProvider(LLMProvider):

    def __init__(
        self,
        api_key: str = "no-key",
        api_base: str = "http://localhost:8000/v1",
        default_model: str = "default",
        extra_headers: dict[str, str] | None = None,
        proxy: str | None = None,
    ):
        super().__init__(api_key, api_base)
        self.default_model = default_model
        self.proxy = proxy or os.getenv("HTTPS_PROXY") or os.getenv("HTTP_PROXY") or os.getenv("ALL_PROXY")
        # Keep affinity stable for this provider instance to improve backend cache locality.
        headers = {"x-session-affinity": uuid.uuid4().hex}
        if extra_headers:
            headers.update(extra_headers)
        client_kwargs: dict[str, Any] = {}
        if self.proxy:
            client_kwargs["proxy"] = self.proxy
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=api_base,
            default_headers=headers,
            http_client=httpx.AsyncClient(**client_kwargs),
        )

    async def chat(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None,
                   model: str | None = None, max_tokens: int = 4096, temperature: float = 0.7,
                   reasoning_effort: str | None = None,
                   tool_choice: str | dict[str, Any] | None = None) -> LLMResponse:
        kwargs = self._prepare_chat_kwargs(
            messages=messages,
            tools=tools,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            reasoning_effort=reasoning_effort,
            tool_choice=tool_choice,
        )
        try:
            return self._parse(await self._client.chat.completions.create(**kwargs))
        except Exception as e:
            return LLMResponse(content=f"Error: {e}", finish_reason="error")

    def _prepare_chat_kwargs(
        self,
        *,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        max_tokens: int = 4096,
        temperature: float = 0.7,
        reasoning_effort: str | None = None,
        tool_choice: str | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        effective_model = model or self.default_model
        kwargs: dict[str, Any] = {
            "model": effective_model,
            "messages": self._sanitize_empty_content(messages),
            "max_tokens": max(1, max_tokens),
            "temperature": temperature,
        }
        if reasoning_effort:
            kwargs["reasoning_effort"] = reasoning_effort
        extra_body = self._deepseek_v4_extra_body(effective_model, reasoning_effort)
        if extra_body:
            kwargs["extra_body"] = extra_body
        if tools:
            kwargs["tools"] = tools
            kwargs["tool_choice"] = tool_choice or "auto"
        return kwargs

    def _deepseek_v4_extra_body(
        self,
        model: str,
        reasoning_effort: str | None,
    ) -> dict[str, Any] | None:
        if not str(model or "").startswith("deepseek-v4-"):
            return None
        return {
            "thinking": {
                "type": "enabled" if reasoning_effort else "disabled",
            },
        }

    @staticmethod
    def _response_field(obj: Any, key: str) -> Any:
        if isinstance(obj, dict):
            return obj.get(key)
        value = getattr(obj, key, None)
        if value is not None:
            return value
        extra = getattr(obj, "model_extra", None)
        if isinstance(extra, dict):
            value = extra.get(key)
            if value is not None:
                return value
        model_dump = getattr(obj, "model_dump", None)
        if callable(model_dump):
            try:
                dumped = model_dump()
            except Exception:
                dumped = None
            if isinstance(dumped, dict):
                return dumped.get(key)
        return None

    def _parse(self, response: Any) -> LLMResponse:
        choice = response.choices[0]
        msg = choice.message
        tool_calls = [
            ToolCallRequest(id=tc.id, name=tc.function.name,
                            arguments=json_repair.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else tc.function.arguments)
            for tc in (msg.tool_calls or [])
        ]
        u = response.usage
        return LLMResponse(
            content=msg.content, tool_calls=tool_calls, finish_reason=choice.finish_reason or "stop",
            usage={"prompt_tokens": u.prompt_tokens, "completion_tokens": u.completion_tokens, "total_tokens": u.total_tokens} if u else {},
            reasoning_content=self._response_field(msg, "reasoning_content") or None,
        )

    def get_default_model(self) -> str:
        return self.default_model
