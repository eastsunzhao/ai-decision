import os
from functools import lru_cache
from pathlib import Path
from typing import Optional

from pydantic import BaseModel


def _load_dotenv(path: str = ".env") -> None:
    env_path = Path(path)
    if not env_path.exists():
        return

    for line in env_path.read_text(encoding="utf-8").splitlines():
        raw = line.strip()
        if not raw or raw.startswith("#") or "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def _get_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return int(value)


def _get_optional_int(name: str) -> Optional[int]:
    value = os.getenv(name)
    if value is None or value == "":
        return None
    return int(value)


def _get_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return float(value)


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or value == "":
        return default
    return value.strip().lower() in {"1", "true", "yes", "y", "on"}


class Settings(BaseModel):
    es_url: str
    es_username: str
    es_password: str
    es_index: str

    llm_provider: str = "openai"
    llm_endpoint: Optional[str] = "https://gz-eastus2.openai.azure.com/openai/v1/chat/completions"
    openai_api_key: Optional[str] = None
    openai_model: str = "gpt-5.4-2026-03-05"
    llm_timeout_seconds: float = 45.0
    llm_log_payloads: bool = True
    llm_log_max_chars: int = 20000

    embedding_provider: str = "none"
    embedding_endpoint: Optional[str] = None
    embedding_api_key: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_deployment: Optional[str] = None
    embedding_dims: int = 1024
    embedding_request_dimensions: bool = False
    embedding_device: Optional[str] = None
    embedding_normalize: bool = True
    hf_endpoint: Optional[str] = None
    hf_hub_connect_timeout: Optional[int] = None
    hf_hub_download_timeout: Optional[int] = None

    retrieval_size: int = 8
    retrieval_rrf_k: int = 60
    retrieval_candidates_size: int = 24
    retrieval_rerank_enabled: bool = True


@lru_cache
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        es_url=os.getenv("ES_URL", "http://120.26.113.122:9200"),
        es_username=os.getenv("ES_USERNAME", "elastic"),
        es_password=os.getenv("ES_PASSWORD", "Meritco"),
        es_index=os.getenv("ES_INDEX", "medical_chunks-prod"),
        llm_provider=os.getenv("LLM_PROVIDER", "openai"),
        llm_endpoint=os.getenv("LLM_ENDPOINT", "https://gz-eastus2.openai.azure.com/openai/v1/chat/completions") or None,
        openai_api_key=os.getenv("OPENAI_API_KEY") or None,
        openai_model=os.getenv("OPENAI_MODEL", "gpt-5.4-2026-03-05"),
        llm_timeout_seconds=_get_float("LLM_TIMEOUT_SECONDS", 45.0),
        llm_log_payloads=_get_bool("LLM_LOG_PAYLOADS", True),
        llm_log_max_chars=_get_int("LLM_LOG_MAX_CHARS", 20000),
        embedding_provider=os.getenv("EMBEDDING_PROVIDER", "none"),
        embedding_endpoint=os.getenv("EMBEDDING_ENDPOINT") or None,
        embedding_api_key=os.getenv("EMBEDDING_API_KEY") or os.getenv("OPENAI_API_KEY") or None,
        embedding_model=os.getenv("EMBEDDING_MODEL") or None,
        embedding_deployment=os.getenv("EMBEDDING_DEPLOYMENT") or None,
        embedding_dims=_get_int("EMBEDDING_DIMS", 1024),
        embedding_request_dimensions=_get_bool("EMBEDDING_REQUEST_DIMENSIONS", False),
        embedding_device=os.getenv("EMBEDDING_DEVICE") or None,
        embedding_normalize=_get_bool("EMBEDDING_NORMALIZE", True),
        hf_endpoint=os.getenv("HF_ENDPOINT") or None,
        hf_hub_connect_timeout=_get_optional_int("HF_HUB_CONNECT_TIMEOUT"),
        hf_hub_download_timeout=_get_optional_int("HF_HUB_DOWNLOAD_TIMEOUT"),
        retrieval_size=_get_int("RETRIEVAL_SIZE", 8),
        retrieval_rrf_k=_get_int("RETRIEVAL_RRF_K", 60),
        retrieval_candidates_size=_get_int("RETRIEVAL_CANDIDATES_SIZE", 24),
        retrieval_rerank_enabled=_get_bool("RETRIEVAL_RERANK_ENABLED", True),
    )


settings = get_settings()
