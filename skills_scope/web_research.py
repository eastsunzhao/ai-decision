"""CLI entrypoint for focused web research flow."""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


def _ensure_repo_import_path() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    candidate = repo_root / "nanobot"
    if candidate.exists():
        sys.path.insert(0, str(candidate))


_ensure_repo_import_path()


def _configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _configure_logging() -> None:
    try:
        from loguru import logger
    except Exception:
        return
    try:
        logger.remove()
    except Exception:
        pass
    try:
        logger.add(sys.stderr, level="ERROR")
    except Exception:
        pass


def _write_line(text: str) -> None:
    data = f"{text}\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        try:
            buffer.write(data.encode("utf-8", errors="replace"))
            buffer.flush()
            return
        except Exception:
            pass
    print(text)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run focused web research.")
    parser.add_argument("--query", required=True, help="Search query")
    parser.add_argument("--focus", required=True, help="Exact question to extract evidence for")
    parser.add_argument("--count", type=int, default=5, help="Results to analyze (1-10)")
    return parser.parse_args(argv)


def _load_config():
    from nanobot.config.loader import load_config
    return load_config()


def _create_provider(config):
    from nanobot.providers.factory import create_provider
    return create_provider(config)


def _resolve_jina_api_key(config):
    from nanobot.research.web import resolve_jina_api_key
    return resolve_jina_api_key(config=config)


def _missing_jina_api_key_message() -> str:
    from nanobot.research.web import missing_jina_api_key_message
    return missing_jina_api_key_message()


async def _run_web_research(**kwargs):
    from nanobot.research.web import run_web_research
    return await run_web_research(**kwargs)


async def run(argv: list[str] | None = None) -> int:
    _configure_stdio()
    _configure_logging()

    args = parse_args(argv)
    count = min(max(args.count, 1), 10)

    config = _load_config()
    api_key = _resolve_jina_api_key(config)
    if not api_key:
        _write_line(_missing_jina_api_key_message())
        return 1

    try:
        provider = _create_provider(config)
    except Exception as exc:
        _write_line(f"Error: {exc}")
        return 1

    result = await _run_web_research(
        query=args.query,
        focus=args.focus,
        count=count,
        provider=provider,
        model=config.agents.defaults.model,
        max_tokens=config.agents.defaults.max_tokens,
        reasoning_effort=config.agents.defaults.reasoning_effort,
        api_key=api_key,
        proxy=config.tools.web.proxy or None,
    )
    _write_line(result)
    return 0


def main(argv: list[str] | None = None) -> int:
    return asyncio.run(run(argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
