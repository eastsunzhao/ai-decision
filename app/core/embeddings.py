from __future__ import annotations

import hashlib
import logging
import math
import os
import time
from functools import lru_cache
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
    if provider in {"local", "sentence_transformers", "sentence-transformers"}:
        return _local_embedding(query)
    if provider in {"openai", "azure_openai"}:
        return _openai_embedding(query)

    logger.warning("embedding_provider_not_supported provider=%s", provider)
    return None


def _local_embedding(text: str) -> Optional[List[float]]:
    if not settings.embedding_model:
        logger.warning("embedding_model_missing provider=local")
        return None

    try:
        start = time.perf_counter()
        model = _load_sentence_transformer(settings.embedding_model, settings.embedding_device)
        vector_data = model.encode(
            text,
            normalize_embeddings=settings.embedding_normalize,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        vector = [float(item) for item in vector_data.tolist()]
        if not _validate_embedding_dims(vector, provider="local"):
            return None
        logger.info(
            "embedding_completed provider=local model=%s device=%s normalize=%s dims=%s elapsed_ms=%.2f",
            settings.embedding_model,
            settings.embedding_device or "auto",
            settings.embedding_normalize,
            len(vector),
            (time.perf_counter() - start) * 1000,
        )
        return vector
    except ImportError:
        logger.error(
            "embedding_local_dependency_missing provider=local model=%s hint=%s",
            settings.embedding_model,
            "Install sentence-transformers, for example: pip install sentence-transformers",
        )
        return None
    except Exception as exc:
        _log_local_embedding_failure(exc)
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
        request_model = settings.embedding_deployment or settings.embedding_model
        request_payload: Dict[str, Any] = {
            "model": request_model,
            "input": text,
        }
        if settings.embedding_request_dimensions and settings.embedding_dims > 0:
            request_payload["dimensions"] = settings.embedding_dims

        logger.info(
            "embedding_request provider=openai endpoint=%s base_url=%s request_model=%s configured_model=%s dimensions=%s input_len=%s",
            settings.embedding_endpoint or settings.llm_endpoint,
            base_url or "default",
            request_model,
            settings.embedding_model,
            request_payload.get("dimensions", "model_default"),
            len(text),
        )
        response = client.embeddings.create(**request_payload)
        vector = list(response.data[0].embedding) if response.data else []
        if not _validate_embedding_dims(vector, provider="openai"):
            return None
        logger.info(
            "embedding_completed provider=openai request_model=%s configured_model=%s dims=%s elapsed_ms=%.2f",
            request_model,
            settings.embedding_model,
            len(vector),
            (time.perf_counter() - start) * 1000,
        )
        return vector or None
    except Exception as exc:
        _log_embedding_failure(exc, request_model=settings.embedding_deployment or settings.embedding_model)
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


@lru_cache(maxsize=4)
def _load_sentence_transformer(model_name: str, device: Optional[str]) -> Any:
    _configure_huggingface_env()

    from sentence_transformers import SentenceTransformer

    kwargs: Dict[str, Any] = {}
    if device:
        kwargs["device"] = device
    logger.info("embedding_local_model_loading model=%s device=%s", model_name, device or "auto")
    model = SentenceTransformer(model_name, **kwargs)
    model_dims = model.get_sentence_embedding_dimension()
    if model_dims and settings.embedding_dims > 0 and model_dims != settings.embedding_dims:
        logger.warning(
            "embedding_local_model_dims_mismatch model=%s configured_dims=%s actual_dims=%s action=%s",
            model_name,
            settings.embedding_dims,
            model_dims,
            "using_actual_model_dims",
        )
    logger.info("embedding_local_model_loaded model=%s dims=%s", model_name, model_dims or "unknown")
    return model


def _configure_huggingface_env() -> None:
    _set_env_if_configured("HF_ENDPOINT", settings.hf_endpoint)
    _set_env_if_configured("HF_HUB_CONNECT_TIMEOUT", settings.hf_hub_connect_timeout)
    _set_env_if_configured("HF_HUB_DOWNLOAD_TIMEOUT", settings.hf_hub_download_timeout)


def _set_env_if_configured(name: str, value: Optional[Any]) -> None:
    if value is not None and os.getenv(name) in {None, ""}:
        os.environ[name] = str(value)


def _validate_embedding_dims(vector: List[float], provider: str) -> bool:
    expected_dims = settings.embedding_dims
    if expected_dims <= 0 or len(vector) == expected_dims:
        return True
    logger.error(
        "embedding_dims_mismatch provider=%s model=%s expected_dims=%s actual_dims=%s hint=%s",
        provider,
        settings.embedding_model,
        expected_dims,
        len(vector),
        "Set EMBEDDING_DIMS to match the ES embedding field, or use the same embedding model used when indexing chunks.",
    )
    return False


def _log_local_embedding_failure(exc: Exception) -> None:
    message = str(exc)
    if "huggingface.co" in message or "ConnectionError" in exc.__class__.__name__:
        logger.error(
            "embedding_local_model_unavailable provider=local model=%s error=%s hint=%s",
            settings.embedding_model,
            message,
            "Ensure the sentence-transformers model is downloaded, or set EMBEDDING_MODEL to a local model directory.",
        )
        logger.debug("embedding_local_model_unavailable_traceback", exc_info=True)
        return

    logger.exception("embedding_failed provider=local model=%s", settings.embedding_model)


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


def _log_embedding_failure(exc: Exception, request_model: Optional[str]) -> None:
    error_code, error_message = _extract_openai_error(exc)
    if error_code == "unavailable_model":
        logger.error(
            "embedding_unavailable_model provider=openai request_model=%s configured_model=%s endpoint=%s error=%s hint=%s",
            request_model,
            settings.embedding_model,
            settings.embedding_endpoint or settings.llm_endpoint,
            error_message or exc,
            "For Azure OpenAI, set EMBEDDING_DEPLOYMENT to the exact embedding deployment name in this Azure resource. "
            "The deployment name may differ from the base model name such as text-embedding-3-small.",
        )
        logger.debug("embedding_failed_traceback provider=openai", exc_info=True)
        return

    logger.exception(
        "embedding_failed provider=openai request_model=%s configured_model=%s error_code=%s",
        request_model,
        settings.embedding_model,
        error_code or "unknown",
    )


def _extract_openai_error(exc: Exception) -> tuple[Optional[str], Optional[str]]:
    body = getattr(exc, "body", None)
    if isinstance(body, dict):
        error = body.get("error")
        if isinstance(error, dict):
            return _as_optional_str(error.get("code")), _as_optional_str(error.get("message"))

    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except Exception:
            payload = None
        if isinstance(payload, dict):
            error = payload.get("error")
            if isinstance(error, dict):
                return _as_optional_str(error.get("code")), _as_optional_str(error.get("message"))

    return _as_optional_str(getattr(exc, "code", None)), None


def _as_optional_str(value: Any) -> Optional[str]:
    return str(value) if value is not None else None
