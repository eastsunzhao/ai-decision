from pathlib import Path
import logging
import time

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from app.core.logging_config import setup_logging

setup_logging()

from app.api.chat import router as chat_router
from app.core.config import settings

logger = logging.getLogger("ai_decision.main")

app = FastAPI(
    title="AI Decision Agent OS",
    description="企业级行业分析问答系统，基于 FastAPI + LangGraph + Elasticsearch Hybrid RAG。",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api")

static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=static_dir), name="static")


@app.get("/")
def workspace() -> FileResponse:
    logger.info("workspace_page_requested")
    return FileResponse(static_dir / "index.html")


@app.get("/health")
def health_check() -> dict:
    logger.info("health_check es_index=%s llm_provider=%s", settings.es_index, settings.llm_provider)
    return {
        "status": "ok",
        "es_url": settings.es_url,
        "es_index": settings.es_index,
        "llm_provider": settings.llm_provider,
    }


@app.middleware("http")
async def request_logging_middleware(request: Request, call_next):
    start = time.perf_counter()
    logger.info("request_started method=%s path=%s", request.method, request.url.path)
    try:
        response = await call_next(request)
    except Exception:
        elapsed_ms = (time.perf_counter() - start) * 1000
        logger.exception("request_failed method=%s path=%s elapsed_ms=%.2f", request.method, request.url.path, elapsed_ms)
        raise
    elapsed_ms = (time.perf_counter() - start) * 1000
    logger.info(
        "request_finished method=%s path=%s status_code=%s elapsed_ms=%.2f",
        request.method,
        request.url.path,
        response.status_code,
        elapsed_ms,
    )
    return response
