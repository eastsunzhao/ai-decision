from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from typing import Any, Dict, List, Optional

from app.core.config import settings

logger = logging.getLogger("ai_decision.core.embeddings")


def embed_query(text: str) -> Optional[List[float]]:
    query = (text or "").strip()
    if not query:
        return None

    provider = os.getenv("EMBEDDING_PROVIDER", settings.embedding_provider).lower()
    logger.info("embedding_started provider=%s query_len=%s", provider, len(query))
    if provider == "none":
        logger.info("embedding_skipped provider=none")
        return None
    if provider == "mock":
        vector = _mock_embedding(query, settings.embedding_dims or 1024)
        logger.info("embedding_completed provider=mock dims=%s", len(vector))
        return vector
    if provider in {"openai", "azure_openai"}:
        return _openai_embedding(query)

    logger.warning("embedding_provider_not_supported provider=%s", provider)
    return None


def _openai_embedding(text: str) -> Optional[List[float]]:
    if not settings.embedding_api_key:
        logger.warning("embedding_api_key_missing provider=openai")
        return None
    if not settings.embedding_model:
        logger.warning("embedding_model_missing provider=openai")
        return None

    try:
        from openai import OpenAI

        start = time.perf_counter()
        base_url = _normalize_openai_base_url(settings.embedding_endpoint or settings.llm_endpoint)
        client_kwargs: Dict[str, Any] = {
            "api_key": settings.embedding_api_key,
            "timeout": settings.llm_timeout_seconds,
        }
        if base_url:
            client_kwargs["base_url"] = base_url
        client = OpenAI(**client_kwargs)
        request_payload: Dict[str, Any] = {
            "model": settings.embedding_model,
            "input": text,
        }
        if settings.embedding_request_dimensions and settings.embedding_dims > 0:
            request_payload["dimensions"] = settings.embedding_dims

        logger.info(
            "embedding_request provider=openai endpoint=%s base_url=%s model=%s dimensions=%s input_len=%s",
            settings.embedding_endpoint or settings.llm_endpoint,
            base_url or "default",
            settings.embedding_model,
            request_payload.get("dimensions", "model_default"),
            len(text),
        )
        response = client.embeddings.create(**request_payload)
        vector = list(response.data[0].embedding) if response.data else []
        logger.info(
            "embedding_completed provider=openai model=%s dims=%s elapsed_ms=%.2f",
            settings.embedding_model,
            len(vector),
            (time.perf_counter() - start) * 1000,
        )
        return vector or None
    except Exception:
        logger.exception("embedding_failed provider=openai model=%s", settings.embedding_model)
        return None


def _mock_embedding(text: str, dims: int) -> List[float]:
    values = []
    seed = hashlib.sha256(text.encode("utf-8")).digest()
    while len(values) < dims:
        seed = hashlib.sha256(seed).digest()
        values.extend((byte / 127.5) - 1.0 for byte in seed)
    vector = values[:dims]
    norm = math.sqrt(sum(item * item for item in vector)) or 1.0
    return [item / norm for item in vector]


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
