"""Scope routing registry."""

from __future__ import annotations

from collections.abc import Callable

from skills_scope import query_forum, query_mid_platform, query_news

ScopeHandler = Callable[[list[str] | None], int]

_HANDLERS: dict[str, ScopeHandler] = {
    "mid_platform": query_mid_platform.main,
    "news": query_news.main,
    "forum": query_forum.main,
}


def list_scopes() -> list[str]:
    return sorted(_HANDLERS.keys())


def run_scope(scope: str, argv: list[str] | None = None) -> int:
    handler = _HANDLERS.get(scope)
    if handler is None:
        raise ValueError(f"Unsupported scope: {scope}")
    return int(handler(argv))
