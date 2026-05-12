from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Iterable, List, Optional

from app.core.config import settings

logger = logging.getLogger("ai_decision.core.llm")


def call_llm_json(prompt: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
    """LLM abstraction point.

    MVP defaults to deterministic mock output so the API remains runnable before
    a real model gateway is connected.
    """
    params = params or {}
    provider = os.getenv("LLM_PROVIDER", "mock").lower()
    task = params.get("task", "unknown")
    logger.info("llm_call_started provider=%s task=%s prompt_len=%s", provider, task, len(prompt or ""))
    if provider == "mock":
        result = _mock_llm(params)
        logger.info("llm_call_completed provider=mock task=%s model=%s", task, result.get("metadata", {}).get("model"))
        return result
    if provider == "openai":
        return _call_openai(prompt, params)

    logger.warning("llm_provider_not_supported provider=%s task=%s", provider, task)
    return {
        "text": "当前 LLM_PROVIDER 尚未接入真实模型网关，请在 app/core/llm.py 中实现调用逻辑。",
        "metadata": {"model": provider, "confidence": 0.45},
    }


def _call_openai(prompt: str, params: Dict[str, Any]) -> Dict[str, Any]:
    if not settings.openai_api_key:
        logger.warning("openai_api_key_missing fallback=mock task=%s", params.get("task", "unknown"))
        fallback = _mock_llm(params)
        fallback["metadata"]["model"] = "mock:no_openai_api_key"
        return fallback

    try:
        from openai import OpenAI

        start = time.perf_counter()
        base_url = _normalize_openai_base_url(settings.llm_endpoint)
        client_kwargs: Dict[str, Any] = {
            "api_key": settings.openai_api_key,
            "timeout": settings.llm_timeout_seconds,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        model = params.get("model") or settings.openai_model
        request_payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt or json.dumps(params, ensure_ascii=False)}],
        }
        _log_llm_payload(
            "openai_request_payload",
            {
                "task": params.get("task", "unknown"),
                "endpoint": settings.llm_endpoint,
                "base_url": base_url or "default",
                "timeout_seconds": settings.llm_timeout_seconds,
                "payload": request_payload,
            },
        )
        response = client.chat.completions.create(**request_payload)
        text = response.choices[0].message.content if response.choices else ""
        response_payload = response.model_dump(mode="json") if hasattr(response, "model_dump") else str(response)
        _log_llm_payload(
            "openai_response_payload",
            {
                "task": params.get("task", "unknown"),
                "model": model,
                "elapsed_ms": round((time.perf_counter() - start) * 1000, 2),
                "response": response_payload,
            },
        )
        logger.info(
            "openai_call_completed model=%s task=%s base_url=%s timeout_seconds=%.1f output_len=%s elapsed_ms=%.2f",
            model,
            params.get("task", "unknown"),
            base_url or "default",
            settings.llm_timeout_seconds,
            len(text),
            (time.perf_counter() - start) * 1000,
        )
        return {"text": text, "metadata": {"model": model, "confidence": 0.72}}
    except Exception as exc:
        logger.exception("openai_call_failed task=%s endpoint=%s fallback=mock", params.get("task", "unknown"), settings.llm_endpoint)
        fallback = _mock_llm(params)
        fallback["metadata"]["model"] = f"mock:openai_error:{exc.__class__.__name__}"
        return fallback


def _normalize_openai_base_url(endpoint: Optional[str]) -> Optional[str]:
    if not endpoint:
        return None

    base_url = endpoint.rstrip("/")
    known_resource_suffixes = ("/models", "/responses", "/chat/completions", "/completions", "/embeddings")
    for suffix in known_resource_suffixes:
        if base_url.endswith(suffix):
            base_url = base_url[: -len(suffix)]
            break
    return base_url or None


def _log_llm_payload(event: str, payload: Any) -> None:
    if not settings.llm_log_payloads:
        return
    logger.info("%s=%s", event, _json_for_log(payload))


def _json_for_log(payload: Any) -> str:
    text = json.dumps(payload, ensure_ascii=False, default=str)
    limit = settings.llm_log_max_chars
    if limit <= 0 or len(text) <= limit:
        return text
    omitted = len(text) - limit
    return f"{text[:limit]}...[truncated {omitted} chars]"


def _mock_llm(params: Dict[str, Any]) -> Dict[str, Any]:
    task = params.get("task")
    if task == "query_rewrite":
        query = str(params.get("query", "")).strip()
        terms = ["新品", "上市", "市场表现", "销售", "增长", "渠道", "近5年"]
        return {"text": f"{query} {' '.join(terms)}", "metadata": {"model": "mock", "confidence": 0.6}}

    if task == "market_new_product":
        docs: List[Dict[str, Any]] = params.get("documents", [])
        company = params.get("company") or "目标公司"
        snippets = _top_snippets(docs)
        evidence = "；".join(snippets[:3]) if snippets else "当前检索结果中没有足够的新品事实。"
        text = (
            f"基于已检索资料，{company}近5年新品表现需要从新品清单、上市节奏、市场反馈和增长原因四条线判断。"
            f"目前证据显示：{evidence}。"
            "第一版 MVP 未接入真实 LLM，因此这里先给出证据驱动的结构化摘要；接入模型后可进一步输出更完整的时序、归因和竞争判断。"
        )
        return {"text": text, "metadata": {"model": "mock", "confidence": 0.58}}

    return {"text": json.dumps(params, ensure_ascii=False), "metadata": {"model": "mock", "confidence": 0.5}}


def _top_snippets(docs: Iterable[Dict[str, Any]], limit: int = 160) -> List[str]:
    snippets = []
    for doc in docs:
        text = str(doc.get("text", "")).replace("\n", " ").strip()
        if text:
            snippets.append(text[:limit])
    return snippets
