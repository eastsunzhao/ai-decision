"""Diagnostics helpers for prompt and tool payload size reporting."""

from __future__ import annotations

import json
import math
from typing import Any


def _serialize(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return repr(value)


def estimate_tokens(value: Any) -> tuple[int, str]:
    """Estimate tokens for debug output."""
    text = _serialize(value)
    if not text:
        return 0, "empty"

    try:
        import tiktoken  # type: ignore

        for encoding_name in ("o200k_base", "cl100k_base"):
            try:
                encoding = tiktoken.get_encoding(encoding_name)
                return len(encoding.encode(text)), f"tiktoken/{encoding_name}"
            except Exception:
                continue
    except Exception:
        pass

    return max(1, math.ceil(len(text) / 4)), "chars/4-estimate"


def format_tool_payload(tool_name: str, payload: Any, *, preview_chars: int = 300) -> str:
    """Format a tool payload preview with estimated token count."""
    text = _serialize(payload)
    tokens, method = estimate_tokens(text)

    if len(text) <= preview_chars * 2:
        preview = text
    else:
        preview = f"{text[:preview_chars]}\n...\n{text[-preview_chars:]}"

    return (
        f"[Tool payload] {tool_name}: {len(text)} chars, ~{tokens} tokens ({method})\n"
        f"{preview}"
    )


def format_messages_stats(messages: list[dict[str, Any]], *, label: str = "Prompt stats") -> str:
    """Format prompt size diagnostics."""
    serialized = _serialize(messages)
    tokens, method = estimate_tokens(serialized)
    return f"[{label}] {len(messages)} messages, {len(serialized)} chars, ~{tokens} tokens ({method})"
