"""Web research helpers shared by tools and skill scripts."""

from __future__ import annotations

import asyncio
import html
import json
import os
import re
from typing import Any

import httpx
from loguru import logger

try:
    import anyio
except ImportError:
    anyio = None

try:
    import json_repair
except ImportError:
    json_repair = None


def normalize_text(text: str) -> str:
    """Normalize whitespace for snippets and passages."""
    text = re.sub(r"[ \t]+", " ", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def strip_tags(text: str) -> str:
    """Remove HTML tags and decode entities."""
    text = re.sub(r"<script[\s\S]*?</script>", "", text, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", "", text, flags=re.I)
    text = re.sub(r"<[^>]+>", "", text)
    return html.unescape(text).strip()


def missing_jina_api_key_message() -> str:
    """Return the standard missing-key error for Jina search."""
    try:
        from nanobot.config.loader import get_config_path

        config_path = get_config_path()
    except ModuleNotFoundError:
        config_path = "config.json"

    return (
        f"Error: Jina Search API key not configured. Configure tools.web.search.apiKey "
        f"in {config_path} (or export JINA_API_KEY), then restart the gateway."
    )


def resolve_jina_api_key(*, explicit_key: str | None = None, config: Any | None = None) -> str:
    """Resolve the Jina API key from explicit input, env, or config."""
    if explicit_key:
        return explicit_key

    env_key = os.environ.get("JINA_API_KEY", "")
    if env_key:
        return env_key

    if config is None:
        return ""

    tools = getattr(config, "tools", None)
    web = getattr(tools, "web", None)
    search = getattr(web, "search", None)
    return getattr(search, "api_key", "") or ""


async def search_jina_results(
    query: str,
    api_key: str,
    proxy: str | None = None,
    *,
    direct: bool = True,
) -> list[dict[str, Any]]:
    """Run a Jina search request and return raw result items."""
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {api_key}",
    }
    if direct:
        headers["X-Engine"] = "direct"
    else:
        headers["X-Respond-With"] = "no-content"

    async with httpx.AsyncClient(proxy=proxy) as client:
        response = await client.get(
            "https://s.jina.ai/",
            params={"q": query},
            headers=headers,
            timeout=30.0 if direct else 20.0,
        )
        response.raise_for_status()

    return response.json().get("data", [])


def format_search_results(
    query: str,
    results: list[dict[str, Any]],
    *,
    snippet_key: str = "content",
    allow_content_fallback: bool = True,
) -> str:
    """Format Jina search results for display."""
    if not results:
        return f"No results for: {query}"

    lines = [f"Results for: {query}\n"]
    for i, item in enumerate(results, 1):
        title = item.get("title", "")
        url = item.get("url", "")
        snippet = item.get(snippet_key, "") or item.get("description", "")
        if not snippet and allow_content_fallback:
            snippet = item.get("content", "")
        warning = item.get("warning", "")
        lines.append(f"{i}. {title}\n   {url}")
        if snippet:
            lines.append(f"   {normalize_text(snippet)}")
        if warning:
            lines.append(f"   Warning: {warning}")
    return "\n".join(lines)


async def run_web_research(
    *,
    query: str,
    focus: str,
    count: int,
    provider: Any,
    model: str,
    max_tokens: int = 4096,
    reasoning_effort: str | None = None,
    api_key: str,
    proxy: str | None = None,
) -> str:
    """Search with Jina and extract focused evidence with the configured LLM."""
    results = (await search_jina_results(query, api_key, proxy, direct=True))[:count]
    if not results:
        return f"No results for: {query}"

    analyses: list[dict[str, Any]] = [{} for _ in results]

    async def _worker(index: int, rank: int, item: dict[str, Any]) -> None:
        analyses[index] = await _analyze_result(
            rank=rank,
            query=query,
            focus=focus,
            item=item,
            provider=provider,
            model=model,
            max_tokens=max_tokens,
            reasoning_effort=reasoning_effort,
        )

    if anyio is None:
        await asyncio.gather(*[_worker(index, rank, item) for index, (rank, item) in enumerate(zip(range(1, len(results) + 1), results, strict=False))])
    else:
        async with anyio.create_task_group() as task_group:
            for index, (rank, item) in enumerate(zip(range(1, len(results) + 1), results, strict=False)):
                task_group.start_soon(_worker, index, rank, item)

    return _format_research(query, focus, analyses)


async def _analyze_result(
    *,
    rank: int,
    query: str,
    focus: str,
    item: dict[str, Any],
    provider: Any,
    model: str,
    max_tokens: int,
    reasoning_effort: str | None,
) -> dict[str, Any]:
    title = item.get("title", "")
    url = item.get("url", "")
    description = item.get("description", "")
    content = item.get("content", "")
    warning = item.get("warning", "")

    logger.info(
        "WebResearch subtask [{}] analyzing result: {} ({})",
        rank,
        title or "(untitled)",
        url or "no-url",
    )

    if warning:
        return {
            "rank": rank,
            "title": title,
            "url": url,
            "relevance": "none",
            "reason": warning,
            "answer": "",
            "passages": [],
        }

    source_text = content or description
    if not source_text:
        return {
            "rank": rank,
            "title": title,
            "url": url,
            "relevance": "none",
            "reason": "No page content returned by Jina.",
            "answer": "",
            "passages": [],
        }

    messages = [
        {
            "role": "system",
            "content": (
                "You are a focused research subagent. Read one search result and decide "
                "whether it is relevant to the focus question. Return JSON only with keys: "
                "relevance, reason, answer, passages. "
                "relevance must be one of high, medium, low, none. "
                "answer must be a one-sentence finding grounded only in the page. "
                "passages must be a list of up to 3 verbatim relevant excerpts from the page."
            ),
        },
        {
            "role": "user",
            "content": (
                f"Search query: {query}\n"
                f"Focus question: {focus}\n"
                f"Title: {title}\n"
                f"URL: {url}\n"
                f"Description: {description}\n\n"
                f"Page content:\n{source_text}"
            ),
        },
    ]

    try:
        response = await provider.chat(
            messages=messages,
            tools=None,
            model=model,
            max_tokens=min(max_tokens, 1200),
            temperature=0.0,
            reasoning_effort=reasoning_effort,
        )
        parsed = _parse_analysis(response.content or "")
    except Exception as exc:
        parsed = {
            "relevance": "none",
            "reason": f"Research subagent failed: {exc}",
            "answer": "",
            "passages": [],
        }

    return {
        "rank": rank,
        "title": title,
        "url": url,
        "relevance": parsed.get("relevance", "none"),
        "reason": parsed.get("reason", ""),
        "answer": parsed.get("answer", ""),
        "passages": parsed.get("passages", []),
    }


def _parse_analysis(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {"relevance": "none", "reason": "Empty subagent response.", "answer": "", "passages": []}

    try:
        data = json_repair.loads(raw) if json_repair is not None else json.loads(raw)
    except Exception:
        return {"relevance": "low", "reason": raw[:300], "answer": "", "passages": []}

    relevance = str(data.get("relevance", "none")).lower()
    if relevance not in {"high", "medium", "low", "none"}:
        relevance = "none"

    passages = data.get("passages", [])
    if not isinstance(passages, list):
        passages = [str(passages)]

    clean_passages = []
    for passage in passages[:3]:
        text = normalize_text(str(passage))
        if text:
            clean_passages.append(text)

    return {
        "relevance": relevance,
        "reason": normalize_text(str(data.get("reason", ""))),
        "answer": normalize_text(str(data.get("answer", ""))),
        "passages": clean_passages,
    }


def _format_research(query: str, focus: str, analyses: list[dict[str, Any]]) -> str:
    order = {"high": 0, "medium": 1, "low": 2, "none": 3}
    analyses = sorted(analyses, key=lambda item: (order.get(item["relevance"], 9), item["rank"]))

    lines = [f"Research results for: {query}", f"Focus: {focus}", ""]
    relevant_count = 0

    for i, item in enumerate(analyses, 1):
        relevance = item["relevance"].upper()
        lines.append(f"{i}. [{relevance}] {item['title']}")
        lines.append(f"   {item['url']}")
        if item["reason"]:
            lines.append(f"   Why: {item['reason']}")
        if item["answer"]:
            lines.append(f"   Finding: {item['answer']}")
        passages = item.get("passages", [])
        if passages:
            relevant_count += 1
            for idx, passage in enumerate(passages, 1):
                lines.append(f"   Passage {idx}: {passage}")

    if relevant_count == 0:
        lines.append("\nNo clearly relevant passages were found in the analyzed results.")

    return "\n".join(lines)
