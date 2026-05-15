"""Scope-first data query orchestrator for web runtime."""

from __future__ import annotations

import json
import asyncio
import re
import subprocess
import sys
import time
import os
from pathlib import Path
from typing import Any

from skills_scope.intent_router import classify_turn_intent


_SCOPE_SCRIPT_MAP: dict[str, str] = {
    "mid_platform": "query_mid_platform.py",
    "forum": "query_forum.py",
    "news": "query_news.py",
}
_SEMANTIC_SNIPPET_MAX_CHARS = 120
_SUBPROCESS_TRACE_MAX_CHARS = 300


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _scope_dir() -> Path:
    return Path(__file__).resolve().parent


def _safe_query_text(message: str) -> str:
    text = (message or "").strip()
    return text if text else "latest market updates"


def _extract_domains(items: list[dict[str, Any]]) -> set[str]:
    domains: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip().lower()
        if not url:
            continue
        host = re.sub(r"^https?://", "", url).split("/", 1)[0]
        host = host.removeprefix("www.").strip()
        if host:
            domains.add(host)
    return domains


def _shorten_query_text(message: str) -> str:
    text = re.sub(r"\s+", " ", (message or "").strip())
    if not text:
        return ""
    parts = re.split(r"[，。；;,.!?！？]", text)
    cleaned = [p.strip() for p in parts if p.strip()]
    if cleaned:
        return cleaned[0]
    return text


def _replace_synonyms(text: str) -> str:
    pairs = [
        ("市场份额", "市场占比"),
        ("市场规模", "市场容量"),
        ("供应链", "产业链"),
        ("趋势", "走向"),
        ("风险", "不确定性"),
        ("合作关系", "合作模式"),
        ("comparison", "benchmark"),
        ("market share", "market ranking"),
        ("supply chain", "value chain"),
        ("risk", "uncertainty"),
    ]
    out = text
    for src, dst in pairs:
        if src in out:
            out = out.replace(src, dst)
            break
    return out


def build_broad_query(message: str, scope: str) -> str:
    text = re.sub(r"\s+", " ", (message or "").strip())
    if not text:
        return "latest market updates"
    zh_terms = re.findall(r"[\u4e00-\u9fff]{2,}", text)
    en_terms = re.findall(r"[A-Za-z][A-Za-z0-9._/-]{1,}", text)
    core_terms = zh_terms[:3] + en_terms[:3]
    scope_hint = {
        "news": "latest trend analysis",
        "forum": "discussion insights",
        "mid_platform": "report summary",
        "default": "latest updates",
    }.get(str(scope or "").strip().lower(), "latest updates")
    if core_terms:
        return " ".join(core_terms + [scope_hint])
    return _shorten_query_text(text) or "latest market updates"


def query_rewriter(message: str, scope: str, *, k: int = 3) -> list[str]:
    text = re.sub(r"\s+", " ", (message or "").strip())
    if not text:
        return []
    variants: list[str] = []
    shortened = _shorten_query_text(text)
    if shortened and shortened != text:
        variants.append(shortened)
    synonym = _replace_synonyms(text)
    if synonym and synonym != text:
        variants.append(synonym)
    broad = build_broad_query(text, scope)
    if broad and broad != text:
        variants.append(broad)
    # Deduplicate while preserving order.
    out: list[str] = []
    seen: set[str] = set()
    for item in variants:
        key = item.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(item.strip())
        if len(out) >= max(0, int(k)):
            break
    return out


def _normalize_candidate_queries(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = _safe_query_text(str(raw or ""))
        key = text.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _normalize_must_keep_terms(values: list[str] | None) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for raw in values or []:
        text = re.sub(r"\s+", " ", str(raw or "")).strip()
        key = text.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(text)
    return out


def _normalize_match_text(text: str) -> str:
    norm = _normalize_query_text(text)
    norm = re.sub(r"[-_/.,:;|]+", " ", norm)
    norm = re.sub(r"\s+", " ", norm).strip()
    return norm


def _query_satisfies_must_keep_terms(query: str, must_keep_terms: list[str]) -> bool:
    terms = _normalize_must_keep_terms(must_keep_terms)
    if not terms:
        return True
    q_norm = _normalize_match_text(query)
    if not q_norm:
        return False
    for term in terms:
        t_norm = _normalize_match_text(term)
        if not t_norm:
            continue
        if t_norm in q_norm:
            continue
        parts = [part for part in t_norm.split(" ") if part]
        if parts and all(part in q_norm for part in parts):
            continue
        return False
    return True


def evidence_needs_fallback(
    evidence: dict[str, Any] | None,
    *,
    min_results: int = 3,
    min_domains: int = 2,
) -> bool:
    if not isinstance(evidence, dict):
        return True
    items = evidence.get("items")
    if not isinstance(items, list):
        return True
    real_items = [r for r in items if isinstance(r, dict) and not _is_placeholder_evidence_item(r)]
    domains = _extract_domains(real_items)
    if len(real_items) < max(1, int(min_results)):
        return True
    if len(domains) < max(1, int(min_domains)):
        return True
    return not evidence_is_satisfactory(evidence)


def _resolve_forum_time_range() -> tuple[str, str]:
    """Resolve forum query time range from env or safe defaults."""
    start = (os.getenv("MERITCO_FORUM_START", "") or "").strip()
    end = (os.getenv("MERITCO_FORUM_END", "") or "").strip()
    if not start:
        start = "2021-01-01 00:00:00"
    if not end:
        end = "2030-12-31 23:59:59"
    return start, end


def _resolve_scope_python() -> str:
    """Resolve python executable for scope subprocesses with env-first priority."""
    candidates: list[str] = []

    web_posix_python = (os.getenv("WEB_POSIX_PYTHON", "") or "").strip()
    if web_posix_python:
        candidates.append(web_posix_python)

    nb_python = (os.getenv("NB_PYTHON", "") or "").strip()
    if nb_python:
        candidates.append(nb_python)

    virtual_env = (os.getenv("VIRTUAL_ENV", "") or "").strip()
    if virtual_env:
        venv = Path(virtual_env)
        candidates.extend(
            [
                str(venv / "bin" / "python"),
                str(venv / "Scripts" / "python.exe"),
            ]
        )

    candidates.append(sys.executable)

    for item in candidates:
        if not item:
            continue
        # Keep the caller-provided interpreter path (especially venv symlinks)
        # and avoid resolving to the underlying uv/base runtime binary.
        expanded = os.path.expandvars(str(Path(item).expanduser()))
        if os.path.isfile(expanded):
            return expanded

    return sys.executable


def build_scope_command(scope: str, message: str) -> list[str]:
    scope_key = str(scope or "").strip().lower()
    script = _SCOPE_SCRIPT_MAP.get(scope_key)
    if not script:
        raise ValueError(f"Unsupported scope: {scope}")

    query_text = _safe_query_text(message)
    script_path = str(_scope_dir() / script)
    cmd = [_resolve_scope_python(), script_path]

    if scope_key == "web":
        cmd.extend(["--query", query_text, "--focus", query_text, "--count", "5"])
    elif scope_key == "mid_platform":
        cmd.extend(
            [
                "--keyword",
                query_text,
                "--format",
                "summary",
                "--page-size",
                "5",
                "--include-content",
                "--max-content-chars",
                "1000",
            ]
        )
    elif scope_key == "forum":
        start, end = _resolve_forum_time_range()
        cmd.extend(
            [
                "--query",
                query_text,
                "--start",
                start,
                "--end",
                end,
                "--focus",
                query_text,
                "--format",
                "analyzed",
                "--analyze-top-k",
                "50",
            ]
        )
    elif scope_key == "news":
        cmd.extend(
            [
                "--since",
                "1970-01-01T00:00:00Z",
                "--limit",
                "100",
                "--max-pages",
                "1",
                "--q",
                query_text,
                "--keyword",
                query_text,
                "--include-full-text",
                "false",
            ]
        )
    return cmd


def _parse_json_output(raw: str) -> tuple[dict[str, Any] | None, str | None]:
    text = (raw or "").strip()
    if not text:
        return None, "empty output"
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload, None
        return {"value": payload}, None
    except json.JSONDecodeError:
        return None, "non-json output"


def _truncate_subprocess_text(text: Any, max_chars: int = _SUBPROCESS_TRACE_MAX_CHARS) -> str:
    normalized = re.sub(r"\s+", " ", str(text or "")).strip()
    if not normalized:
        return ""
    max_len = max(8, int(max_chars))
    if len(normalized) <= max_len:
        return normalized
    suffix = " ...[truncated]"
    keep = max(1, max_len - len(suffix))
    return normalized[:keep].rstrip() + suffix


def _emit_subprocess_output(
    callback: Any,
    *,
    scope: str,
    round_id: str,
    query_text: str,
    command: list[str],
    exit_code: int,
    stdout_text: str,
    stderr_text: str,
) -> None:
    if not callable(callback):
        return
    payload = {
        "scope": str(scope or "").strip().lower(),
        "round": str(round_id or "").strip() or "R1",
        "query_text": _safe_query_text(query_text),
        "command": [str(part) for part in command],
        "exit_code": int(exit_code),
        "stdout": _truncate_subprocess_text(stdout_text),
        "stderr": _truncate_subprocess_text(stderr_text),
    }
    try:
        callback(payload)
    except Exception:
        # Diagnostics should never break main evidence flow.
        pass


def _query_terms(query: str | None) -> list[str]:
    text = re.sub(r"\s+", " ", str(query or "")).strip().lower()
    if not text:
        return []
    terms: list[str] = []
    for token in re.findall(r"[\u4e00-\u9fff]{2,}|[a-z0-9][a-z0-9._/-]{1,}", text):
        item = token.strip().lower()
        if len(item) < 2:
            continue
        if item not in terms:
            terms.append(item)
    return terms[:8]


def semantic_compress_text(
    text: Any,
    *,
    query: str | None = None,
    max_chars: int = _SEMANTIC_SNIPPET_MAX_CHARS,
) -> str:
    raw = re.sub(r"\s+", " ", str(text or "")).strip()
    if not raw:
        return ""

    cleaned = re.sub(r"\[([^\]]+)\]\((https?://[^)]+)\)", r"\1", raw)
    cleaned = re.sub(r"https?://\S+", " ", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip() or raw

    parts = [
        seg.strip(" -|")
        for seg in re.split(r"(?<=[。！？!?；;])\s+|(?<=\.)\s+(?=[A-Z0-9])", cleaned)
        if seg and seg.strip(" -|")
    ]
    if not parts:
        parts = [cleaned]

    terms = _query_terms(query)

    def _score(segment: str) -> tuple[int, int]:
        lower = segment.lower()
        hits = sum(1 for term in terms if term in lower)
        num_bonus = 1 if re.search(r"\d", segment) else 0
        return hits * 2 + num_bonus, -abs(len(segment) - min(max_chars, 80))

    if terms:
        best = max(parts, key=_score)
    else:
        best = next((seg for seg in parts if len(seg) >= 24), parts[0])

    if len(best) <= max_chars:
        return best
    trimmed = best[:max_chars].rstrip(" ,;:")
    if len(trimmed) < len(best):
        return trimmed + "..."
    return trimmed


_RE_WEB_RESULT_LINE = re.compile(
    r"^(?P<num>\d+)\.\s+\[(?P<relevance>[^\]]+)\]\s*(?P<title>.*)$"
)
_RE_WEB_NEXT_RESULT = re.compile(r"^\d+\.\s+\[[^\]]+\]")


def _normalize_web_text_items(raw_text: str) -> list[dict[str, Any]]:
    """Parse `nanobot.research.web._format_research` text output into evidence rows."""
    items: list[dict[str, Any]] = []
    lines = (raw_text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        match = _RE_WEB_RESULT_LINE.match(line)
        if not match:
            i += 1
            continue

        title = (match.group("title") or "").strip()
        relevance = (match.group("relevance") or "").strip().lower()
        url = ""
        snippet = ""
        raw_block = [line]

        j = i + 1
        while j < len(lines):
            current = lines[j].rstrip()
            stripped = current.strip()
            if stripped and _RE_WEB_NEXT_RESULT.match(stripped):
                break
            if stripped:
                raw_block.append(current)
                if not url and re.match(r"^https?://", stripped):
                    url = stripped
                elif stripped.startswith("Finding:") and not snippet:
                    snippet = stripped.removeprefix("Finding:").strip()
            j += 1

        items.append(
            {
                "title": title or "(untitled)",
                "source": "web_research",
                "url": url,
                "time": None,
                "snippet": semantic_compress_text(snippet or "\n".join(raw_block), query=title),
                "relevance": relevance,
                "raw": {
                    "relevance": relevance,
                    "block": "\n".join(raw_block),
                },
            }
        )
        i = j
    return items


def _parse_web_search_lines(raw_text: str) -> list[dict[str, Any]]:
    """Parse native web_search plaintext into candidate rows."""
    items: list[dict[str, Any]] = []
    lines = (raw_text or "").splitlines()
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        m = re.match(r"^(?P<num>\d+)\.\s+(?P<title>.+)$", line)
        if not m:
            i += 1
            continue
        title = (m.group("title") or "").strip()
        url = ""
        snippet = ""
        j = i + 1
        while j < len(lines):
            cur = lines[j].strip()
            if re.match(r"^\d+\.\s+", cur):
                break
            if cur and re.match(r"^https?://", cur) and not url:
                url = cur
            elif cur and not snippet and not cur.lower().startswith("results for:"):
                snippet = cur
            j += 1
        items.append(
            {
                "title": title or "(untitled)",
                "source": "web_search",
                "url": url,
                "time": None,
                "snippet": semantic_compress_text(snippet or title, query=title),
                "relevance": "medium",
                "raw": {"rank": int(m.group("num")), "title": title, "url": url, "snippet": snippet},
            }
        )
        i = j
    return items


def _extract_fetch_text(raw_fetch: Any) -> str:
    """Extract readable text from native web_fetch output payload."""
    if isinstance(raw_fetch, str):
        text = raw_fetch.strip()
        if not text:
            return ""
        try:
            payload = json.loads(text)
        except Exception:
            return text
        if isinstance(payload, dict):
            if payload.get("error"):
                return ""
            return str(payload.get("text") or "").strip()
    return ""


def _run_default_native_query(message: str, *, timeout_seconds: float) -> dict[str, Any]:
    """Use native web_search + web_fetch path for default scope."""
    from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
    from nanobot.config.loader import load_config

    config = load_config()
    web_search_config = config.tools.web.search
    web_proxy = config.tools.web.proxy or None

    async def _exec() -> tuple[str, list[tuple[str, str]]]:
        search_tool = WebSearchTool(config=web_search_config, proxy=web_proxy)
        fetch_tool = WebFetchTool(max_chars=12000, proxy=web_proxy)
        search_out = await search_tool.execute(query=message, count=5)
        candidates = _parse_web_search_lines(search_out)
        fetched_rows: list[tuple[str, str]] = []
        for row in candidates[:2]:
            url = str(row.get("url") or "").strip()
            if not url:
                continue
            fetched = await fetch_tool.execute(url=url, extractMode="text", maxChars=8000)
            fetched_rows.append((url, _extract_fetch_text(fetched)))
        return search_out, fetched_rows

    search_text, fetched_rows = asyncio.run(
        asyncio.wait_for(_exec(), timeout=max(10.0, float(timeout_seconds)))
    )
    items = _parse_web_search_lines(search_text)
    fetch_by_url = {url: text for url, text in fetched_rows if url and text}
    for row in items:
        url = str(row.get("url") or "").strip()
        fetched_text = fetch_by_url.get(url, "")
        if fetched_text:
            row["source"] = "web_fetch"
            row["snippet"] = semantic_compress_text(
                fetched_text,
                query=str(row.get("title") or message),
            )
            row["relevance"] = "high"
            row["raw"] = {"verified": True, "url": url}
    return {
        "scope": "default",
        "query_text": _safe_query_text(message),
        "query_mode": "full",
        "command": ["native:web_search", "native:web_fetch(top2)"],
        "exit_code": 0,
        "stderr": "",
        "parse_error": None,
        "raw_payload": {"search_text": search_text},
        "items": items,
    }


def _normalize_web_fallback_items(raw_text: str) -> list[dict[str, Any]]:
    """When structured lines are missing, still surface web stdout so Policy/evidence are not empty."""
    text = (raw_text or "").strip()
    if not text:
        return []

    lower = text.lower()
    if "no results for:" in lower:
        line = ""
        for ln in text.splitlines():
            if "no results for:" in ln.lower():
                line = ln.strip()
                break
        return [
            {
                "title": "No search results",
                "source": "web_research",
                "url": "",
                "time": None,
                "snippet": semantic_compress_text(line or text),
                "raw": {"kind": "no_hits", "text": text[:2000]},
            }
        ]

    # Loose fallback: any http(s) URL with optional nearby title line above
    urls = re.findall(r"https?://[^\s\]>\"')]+", text)
    if not urls:
        return [
            {
                "title": "Web research output",
                "source": "web_research",
                "url": "",
                "time": None,
                "snippet": semantic_compress_text(text),
                "raw": {"kind": "unparsed_text", "text": text[:2000]},
            }
        ]

    out: list[dict[str, Any]] = []
    for u in urls[:10]:
        out.append(
            {
                "title": "(from URL)",
                "source": "web_research",
                "url": u,
                "time": None,
                "snippet": "",
                "raw": {"kind": "url_only", "text": text[:2000]},
            }
        )
    return out


def _is_placeholder_evidence_item(item: dict[str, Any]) -> bool:
    raw = item.get("raw")
    if isinstance(raw, dict) and raw.get("kind") == "no_hits":
        return True
    return False


def _extract_item_relevance(item: dict[str, Any]) -> str | None:
    """Return normalized relevance label, or None if unknown / missing."""
    rel = item.get("relevance")
    if isinstance(rel, str) and rel.strip():
        return rel.strip().lower()
    raw = item.get("raw")
    if isinstance(raw, dict):
        inner = raw.get("relevance")
        if isinstance(inner, str) and inner.strip():
            return inner.strip().lower()
    return None


def summarize_relevance_counts(items: list[dict[str, Any]]) -> dict[str, int]:
    counts = {"high": 0, "medium": 0, "low": 0, "none": 0, "unknown": 0}
    for item in items:
        if not isinstance(item, dict):
            counts["unknown"] += 1
            continue
        rel = _extract_item_relevance(item)
        if rel is None:
            counts["unknown"] += 1
        elif rel in counts:
            counts[rel] += 1
        else:
            counts["unknown"] += 1
    return counts


def compute_evidence_quality(evidence: dict[str, Any]) -> tuple[bool, str | None, dict[str, int]]:
    """
    Decide whether evidence is satisfactory for research use (not just technical success).

    Hybrid rule: if no item carries a relevance label, fall back to legacy
    (non-empty, non-placeholder items). If any item is labeled, require at least
    one high/medium (or low when EVIDENCE_COUNT_LOW_AS_RELEVANT=1); all explicit
    'none' with no high/medium/low fails.
    """
    empty_summary: dict[str, int] = {"high": 0, "medium": 0, "low": 0, "none": 0, "unknown": 0}
    if os.getenv("EVIDENCE_STRICT_QUALITY", "1").strip().lower() in ("0", "false", "no"):
        if int(evidence.get("exit_code", 1)) != 0:
            return False, "bad_exit", empty_summary
        rows = evidence.get("items")
        if not isinstance(rows, list) or len(rows) == 0:
            return False, "empty", empty_summary
        summary = summarize_relevance_counts(rows)
        return True, None, summary

    if int(evidence.get("exit_code", 1)) != 0:
        return False, "bad_exit", empty_summary
    rows = evidence.get("items")
    if not isinstance(rows, list) or len(rows) == 0:
        return False, "empty", empty_summary

    real = [r for r in rows if isinstance(r, dict) and not _is_placeholder_evidence_item(r)]
    summary = summarize_relevance_counts(real)
    if not real:
        return False, "no_hits", summary

    relevances = [_extract_item_relevance(r) for r in real]
    has_any_label = any(x is not None for x in relevances)
    if not has_any_label:
        return True, None, summary

    if any(x in ("high", "medium") for x in relevances if x is not None):
        return True, None, summary
    if (
        os.getenv("EVIDENCE_COUNT_LOW_AS_RELEVANT", "0").strip().lower() in ("1", "true", "yes")
        and any(x == "low" for x in relevances if x is not None)
    ):
        return True, None, summary

    if any(x == "none" for x in relevances if x is not None) and not any(
        x in ("high", "medium", "low") for x in relevances if x is not None
    ):
        if all(x is None or x == "none" for x in relevances):
            return False, "all_irrelevant", summary

    if all(x is None for x in relevances):
        return True, None, summary

    return False, "low_quality", summary


def evidence_is_satisfactory(evidence: dict[str, Any] | None) -> bool:
    if not isinstance(evidence, dict):
        return False
    ok, _, _ = compute_evidence_quality(evidence)
    return ok


def _normalize_items(scope: str, payload: dict[str, Any], *, raw_stdout: str = "") -> list[dict[str, Any]]:
    if scope == "news":
        # query_news.py emits analyzed rows in `items`; keep backward compatibility
        # with any older payloads that still expose `articles`.
        rows = payload.get("items")
        if not isinstance(rows, list):
            rows = payload.get("articles") or []
        if not isinstance(rows, list):
            return []
        out: list[dict[str, Any]] = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            raw = row.get("raw") if isinstance(row.get("raw"), dict) else row
            rel = row.get("relevance")
            if isinstance(rel, str):
                rel = rel.strip().lower()
            else:
                rel = None
            out.append(
                {
                    "title": row.get("title") or raw.get("title") or raw.get("article_title"),
                    "source": row.get("source") or raw.get("source") or raw.get("site_name"),
                    "url": row.get("url") or raw.get("url") or raw.get("link"),
                    "time": row.get("pub_time") or raw.get("pub_time") or raw.get("published_at") or raw.get("publish_time"),
                    "snippet": semantic_compress_text(
                        row.get("finding")
                        or row.get("summary")
                        or raw.get("summary")
                        or raw.get("content")
                        or raw.get("abstract"),
                        query=row.get("title") or raw.get("title") or raw.get("article_title"),
                    ),
                    "relevance": rel,
                    "raw": raw,
                }
            )
        rf = payload.get("rows_fetched")
        if (
            not out
            and rf is not None
            and int(rf or 0) == 0
            and not payload.get("error")
        ):
            q = str(payload.get("q") or "").strip()
            q_preview = (q[:160] + "…") if len(q) > 160 else q
            return [
                {
                    "title": "No news articles returned",
                    "source": "news",
                    "url": "",
                    "time": None,
                    "snippet": semantic_compress_text(
                        (
                            f"Meritco news API returned rows_fetched=0 for q={q_preview!r}."
                            if q
                            else "Meritco news API returned rows_fetched=0."
                        ),
                        query=q,
                    ),
                    "raw": {"kind": "no_hits", "rows_fetched": 0},
                }
            ]
        return out

    rows = payload.get("items")
    if scope == "forum" and not isinstance(rows, list):
        result = payload.get("result")
        if isinstance(result, dict):
            rows = result.get("forumList")
    if isinstance(rows, list):
        out = []
        for row in rows:
            if not isinstance(row, dict):
                continue
            rel = row.get("relevance")
            if isinstance(rel, str):
                rel = rel.strip().lower()
            else:
                rel = None
            out.append(
                {
                    "title": row.get("title"),
                    "source": row.get("source") or row.get("platform"),
                    "url": row.get("url"),
                    "time": row.get("pubTime"),
                    "snippet": semantic_compress_text(
                        row.get("contentPreview") or row.get("summary"),
                        query=row.get("title"),
                    ),
                    "relevance": rel,
                    "raw": row,
                }
            )
        return out
    if scope == "web":
        text_items = _normalize_web_text_items(raw_stdout)
        if text_items:
            return text_items
        return _normalize_web_fallback_items(raw_stdout)
    return []


def run_scope_query(
    scope: str,
    message: str,
    *,
    timeout_seconds: float = 60.0,
    query_mode: str = "full",
    on_subprocess_output: Any | None = None,
    round_id: str = "R1",
) -> dict[str, Any]:
    """Run selected scope query first and return normalized evidence payload."""
    if str(scope or "").strip().lower() == "default":
        out = _run_default_native_query(message, timeout_seconds=timeout_seconds)
        sat, reason, summary = compute_evidence_quality(out)
        out["relevance_summary"] = summary
        out["data_satisfactory"] = sat
        out["data_failure_reason"] = reason if not sat else None
        return out

    cmd = build_scope_command(scope, message)
    mode = str(query_mode or "full").strip().lower()
    if mode == "partial":
        if scope == "web":
            cmd = [*cmd[:-1], "3"] if cmd and cmd[-2:] == ["--count", "5"] else cmd
        elif scope == "mid_platform":
            cmd = [*cmd[:-1], "3"] if cmd and cmd[-2:] == ["--page-size", "5"] else cmd
        elif scope == "forum":
            cmd.extend(["--analyze-top-k", "20"])
        elif scope == "news":
            cmd.extend(["--limit", "80", "--max-pages", "1", "--include-full-text", "false"])
    completed = subprocess.run(
        cmd,
        cwd=str(_repo_root()),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
        check=False,
    )
    _emit_subprocess_output(
        on_subprocess_output,
        scope=scope,
        round_id=round_id,
        query_text=message,
        command=cmd,
        exit_code=int(completed.returncode),
        stdout_text=completed.stdout or "",
        stderr_text=completed.stderr or "",
    )
    payload, parse_error = _parse_json_output(completed.stdout)
    data = payload or {}
    stdout_text = completed.stdout or ""
    items = _normalize_items(scope, data, raw_stdout=stdout_text) if (data or stdout_text.strip()) else []
    out: dict[str, Any] = {
        "scope": scope,
        "query_text": _safe_query_text(message),
        "query_mode": mode,
        "command": cmd,
        "exit_code": int(completed.returncode),
        "stderr": (completed.stderr or "").strip(),
        "parse_error": parse_error,
        "raw_payload": data,
        "items": items,
    }
    sat, reason, summary = compute_evidence_quality(out)
    out["relevance_summary"] = summary
    out["data_satisfactory"] = sat
    out["data_failure_reason"] = reason if not sat else None
    return out


def run_scope_query_adaptive(
    scope: str,
    message: str,
    *,
    timeout_seconds: float = 60.0,
    rewrite_count: int = 3,
    min_results: int = 3,
    min_domains: int = 2,
    enable_broad_fallback: bool = True,
    conservative_for_default: bool = False,
    on_subprocess_output: Any | None = None,
    planned_primary: str | None = None,
    planned_rewrites: list[str] | None = None,
    must_keep_terms: list[str] | None = None,
    rewrite_policy: str = "auto_only",
) -> dict[str, Any]:
    scope_norm = str(scope or "").strip().lower()
    base_mode = "partial" if scope_norm == "news" else "full"
    policy = str(rewrite_policy or "").strip().lower()
    if policy not in {"auto_only", "plan_first", "plan_only"}:
        policy = "auto_only"
    must_keep = _normalize_must_keep_terms(must_keep_terms)
    planned_rewrite_list = _normalize_candidate_queries(planned_rewrites)
    requested_primary = re.sub(r"\s+", " ", str(planned_primary or "")).strip()
    message_query = _safe_query_text(message)
    primary_query = requested_primary or message_query
    if requested_primary and must_keep and not _query_satisfies_must_keep_terms(requested_primary, must_keep):
        primary_query = message_query

    # default scope: keep adaptive fan-out conservative to control latency/token usage.
    effective_rewrite_count = max(1, int(rewrite_count))
    effective_enable_broad_fallback = bool(enable_broad_fallback)
    if scope_norm == "default" and bool(conservative_for_default):
        effective_rewrite_count = 1
        effective_enable_broad_fallback = False
    primary = run_scope_query(
        scope=scope,
        message=primary_query,
        timeout_seconds=timeout_seconds,
        query_mode=base_mode,
        on_subprocess_output=on_subprocess_output,
        round_id="R1",
    )
    rounds: list[dict[str, Any]] = [_compact_adaptive_round(round_id="R1", query_used=primary_query, evidence=primary)]
    rewrite_skips: list[dict[str, Any]] = []
    planned_rewrites_used = 0
    metadata = {
        "rewrite_policy": policy,
        "planned_rewrites_total": len(planned_rewrite_list),
        "planned_rewrites_used": planned_rewrites_used,
        "rewrite_skips": rewrite_skips,
        "must_keep_terms": must_keep,
        "planned_primary_used": bool(requested_primary and primary_query == requested_primary),
    }

    if not evidence_needs_fallback(primary, min_results=min_results, min_domains=min_domains):
        out = dict(primary)
        out["query_round"] = "R1"
        out["query_used"] = _safe_query_text(primary_query)
        out["adaptive_rounds"] = rounds
        out.update(metadata)
        return out

    rewrites_auto = query_rewriter(message_query, scope, k=effective_rewrite_count)
    rewrite_candidates: list[tuple[str, str]] = []
    seen_candidates: set[str] = {primary_query.strip().lower()}

    def _append_candidates(candidates: list[str], source: str) -> None:
        for raw in candidates:
            query = _safe_query_text(raw)
            key = query.strip().lower()
            if not key or key in seen_candidates:
                continue
            if must_keep and not _query_satisfies_must_keep_terms(query, must_keep):
                rewrite_skips.append(
                    {
                        "query": query,
                        "source": source,
                        "reason": "missing_must_keep_terms",
                    }
                )
                continue
            seen_candidates.add(key)
            rewrite_candidates.append((query, source))

    if policy == "plan_only":
        _append_candidates(planned_rewrite_list, "plan")
    elif policy == "plan_first":
        _append_candidates(planned_rewrite_list, "plan")
        _append_candidates(rewrites_auto, "auto")
    else:
        _append_candidates(rewrites_auto, "auto")

    rewrite_candidates = rewrite_candidates[: max(0, int(effective_rewrite_count))]
    best = primary
    best_round = "R1"
    best_query = _safe_query_text(primary_query)
    for idx, (rw, source) in enumerate(rewrite_candidates, start=1):
        if source == "plan":
            planned_rewrites_used += 1
        ev = run_scope_query(
            scope=scope,
            message=rw,
            timeout_seconds=timeout_seconds,
            query_mode=base_mode,
            on_subprocess_output=on_subprocess_output,
            round_id=f"R2.{idx}",
        )
        rounds.append(_compact_adaptive_round(round_id="R2", query_used=rw, evidence=ev))
        if not evidence_needs_fallback(ev, min_results=min_results, min_domains=min_domains):
            out = dict(ev)
            out["query_round"] = "R2"
            out["query_used"] = rw
            out["adaptive_rounds"] = rounds
            metadata["planned_rewrites_used"] = planned_rewrites_used
            out.update(metadata)
            return out
        if len(ev.get("items") or []) > len(best.get("items") or []):
            best = ev
            best_round = "R2"
            best_query = rw

    if effective_enable_broad_fallback and policy != "plan_only":
        broad = build_broad_query(message_query, scope)
        ev = run_scope_query(
            scope=scope,
            message=broad,
            timeout_seconds=timeout_seconds,
            query_mode=base_mode,
            on_subprocess_output=on_subprocess_output,
            round_id="R3",
        )
        rounds.append(_compact_adaptive_round(round_id="R3", query_used=broad, evidence=ev))
        if len(ev.get("items") or []) >= len(best.get("items") or []):
            best = ev
            best_round = "R3"
            best_query = broad

    out = dict(best)
    out["query_round"] = best_round
    out["query_used"] = best_query
    out["adaptive_rounds"] = rounds
    metadata["planned_rewrites_used"] = planned_rewrites_used
    out.update(metadata)
    return out


def _normalize_query_text(message: str) -> str:
    return re.sub(r"\s+", " ", (message or "").strip()).lower()


def _is_time_sensitive_query(message: str) -> bool:
    text = _normalize_query_text(message)
    if not text:
        return False
    markers = (
        "最新",
        "今天",
        "本周",
        "刚刚",
        "实时",
        "latest",
        "today",
        "this week",
        "real-time",
        "realtime",
        "breaking",
    )
    return any(marker in text for marker in markers)


def _is_follow_up_message(message: str) -> bool:
    text = _normalize_query_text(message)
    if not text:
        return False
    followup_markers = (
        "继续",
        "展开",
        "细说",
        "详细",
        "上面",
        "刚才",
        "这个",
        "那个",
        "再说",
        "接着",
        "continue",
        "expand",
        "more detail",
        "based on that",
        "然后呢",
        "那",
        "这个呢",
        "依据",
        "影响",
        "结论",
    )
    return len(text) <= 80 and any(marker in text for marker in followup_markers)


def _needs_citation_emphasis(message: str) -> bool:
    text = _normalize_query_text(message)
    markers = ("引用", "引文", "来源", "出处", "链接", "cite", "citation", "source", "url")
    return any(marker in text for marker in markers)


def _topic_key(scope: str, message: str) -> str:
    norm = _normalize_query_text(message)
    zh_chunks = re.findall(r"[\u4e00-\u9fff]{2,}", norm)
    zh_tokens: list[str] = []
    for chunk in zh_chunks:
        if len(chunk) <= 3:
            zh_tokens.append(chunk)
            continue
        # Use CJK bigrams so rephrased long sentences can still map to same topic.
        for idx in range(0, len(chunk) - 1):
            zh_tokens.append(chunk[idx : idx + 2])
    en_tokens = re.findall(r"[a-z0-9][a-z0-9._/-]{1,}", norm)
    noise = {
        "继续",
        "展开",
        "详细",
        "上面",
        "这个",
        "那个",
        "然后",
        "结论",
        "依据",
        "影响",
        "分析",
        "怎么看",
        "什么",
        "如何",
        "the",
        "and",
        "what",
        "how",
        "about",
        "more",
        "detail",
        "continue",
    }
    tokens = []
    for token in [*zh_tokens, *en_tokens]:
        t = token.strip().lower()
        if len(t) < 2 or t in noise:
            continue
        if t not in tokens:
            tokens.append(t)
    core = "|".join(tokens[:5]) if tokens else norm[:24]
    time_tag = "time_sensitive" if _is_time_sensitive_query(message) else "stable"
    return f"{scope}:{core}:{time_tag}"


def _strict_key(scope: str, message: str) -> str:
    return f"{scope}:{_normalize_query_text(message)[:160]}"


def _topic_token_set(topic_key: str) -> set[str]:
    parts = str(topic_key or "").split(":")
    if len(parts) < 3:
        return set()
    core = parts[1]
    return {token for token in core.split("|") if token}


def _find_similar_topic_key(
    *,
    topic_cache: dict[str, Any],
    scope: str,
    topic_key: str,
    now_ts: int,
) -> str | None:
    target_tokens = _topic_token_set(topic_key)
    if len(target_tokens) < 2:
        return None
    best_key = None
    best_overlap = 0
    for key, value in topic_cache.items():
        if not isinstance(value, dict):
            continue
        if not str(key).startswith(f"{scope}:"):
            continue
        if int(value.get("expires_at", 0)) <= now_ts:
            continue
        tokens = _topic_token_set(str(key))
        overlap = len(target_tokens & tokens)
        if overlap > best_overlap:
            best_overlap = overlap
            best_key = str(key)
    if best_overlap >= 2:
        return best_key
    return None


def _load_state(state_path: str | Path | None) -> dict[str, Any]:
    if not state_path:
        return {}
    path = Path(state_path)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _save_state(state_path: str | Path | None, state: dict[str, Any]) -> None:
    if not state_path:
        return
    path = Path(state_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _extract_citations(items: list[dict[str, Any]], *, max_count: int = 8) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if not url:
            continue
        out.append(
            {
                "title": item.get("title"),
                "url": url,
                "source": item.get("source"),
                "retrieved_at": int(time.time()),
            }
        )
        if len(out) >= max_count:
            break
    return out


def _has_successful_evidence(evidence: dict[str, Any] | None) -> bool:
    return evidence_is_satisfactory(evidence)


def _trim_evidence_for_partial(evidence: dict[str, Any], *, top_k: int = 6) -> dict[str, Any]:
    rows = evidence.get("items")
    if not isinstance(rows, list):
        return evidence
    trimmed = dict(evidence)
    trimmed["items"] = rows[: max(1, top_k)]
    return trimmed


def _compact_adaptive_round(*, round_id: str, query_used: str, evidence: dict[str, Any]) -> dict[str, Any]:
    rows = evidence.get("items")
    items = rows if isinstance(rows, list) else []
    top_urls: list[str] = []
    seen: set[str] = set()
    for row in items:
        if not isinstance(row, dict):
            continue
        url = str(row.get("url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        top_urls.append(url)
        if len(top_urls) >= 3:
            break
    return {
        "round": str(round_id or ""),
        "query_used": _safe_query_text(query_used),
        "exit_code": int(evidence.get("exit_code", 1)),
        "item_count": len(items),
        "data_satisfactory": bool(evidence.get("data_satisfactory", False)),
        "data_failure_reason": evidence.get("data_failure_reason"),
        "relevance_summary": evidence.get("relevance_summary")
        if isinstance(evidence.get("relevance_summary"), dict)
        else {},
        "top_urls": top_urls,
    }


def plan_scope_query(scope: str, message: str, session_state: dict[str, Any] | None) -> dict[str, Any]:
    state = session_state if isinstance(session_state, dict) else {}
    strict_cache = state.get("cache_strict") if isinstance(state.get("cache_strict"), dict) else {}
    topic_cache = state.get("cache_topic") if isinstance(state.get("cache_topic"), dict) else {}
    evidence_by_scope = state.get("evidence_by_scope") if isinstance(state.get("evidence_by_scope"), dict) else {}
    topic = _topic_key(scope, message)
    strict_key = _strict_key(scope, message)
    now = int(time.time())
    time_sensitive = _is_time_sensitive_query(message)
    needs_citation = _needs_citation_emphasis(message)
    strict_cached = strict_cache.get(strict_key) if isinstance(strict_cache.get(strict_key), dict) else None
    topic_cached = topic_cache.get(topic) if isinstance(topic_cache.get(topic), dict) else None
    strict_valid = bool(
        strict_cached and int(strict_cached.get("expires_at", 0)) > now and isinstance(strict_cached.get("evidence"), dict)
    )
    topic_valid = bool(
        topic_cached and int(topic_cached.get("expires_at", 0)) > now and isinstance(topic_cached.get("evidence"), dict)
    )
    similar_topic_key = _find_similar_topic_key(topic_cache=topic_cache, scope=scope, topic_key=topic, now_ts=now)
    last_evidence = evidence_by_scope.get(scope) if isinstance(evidence_by_scope.get(scope), dict) else None
    last_topic = str(state.get("last_topic_key") or "")
    cooldown_map = state.get("failure_cooldown") if isinstance(state.get("failure_cooldown"), dict) else {}
    cooldown_until = int(cooldown_map.get(topic, 0) or 0)
    cooldown_active = cooldown_until > now
    pending_continue = bool(state.get("pending_continue", False))
    has_last_success = _has_successful_evidence(last_evidence)
    intent = classify_turn_intent(
        message=message,
        topic_key=topic,
        has_successful_evidence=has_last_success,
        pending_continue=pending_continue,
    )

    if time_sensitive:
        return {
            "strategy": "full_search",
            "reason": "time-sensitive query requires fresh retrieval",
            "strict_key": strict_key,
            "topic_key": topic,
            "time_sensitive": True,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    if cooldown_active and _has_successful_evidence(last_evidence):
        return {
            "strategy": "reuse_context",
            "reason": "search cooldown active after recent failure",
            "strict_key": strict_key,
            "topic_key": topic,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    if intent.intent == "confirm_continue" and has_last_success:
        return {
            "strategy": "reuse_context",
            "reason": "intent router chose continue with previous evidence",
            "strict_key": strict_key,
            "topic_key": topic,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    if strict_valid:
        return {
            "strategy": "cache_hit",
            "reason": "strict cache hit",
            "strict_key": strict_key,
            "topic_key": topic,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    if topic_valid:
        return {
            "strategy": "cache_hit",
            "reason": "topic cache hit",
            "strict_key": strict_key,
            "topic_key": topic,
            "topic_hit_key": topic,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }
    if similar_topic_key:
        return {
            "strategy": "cache_hit",
            "reason": "similar topic cache hit",
            "strict_key": strict_key,
            "topic_key": topic,
            "topic_hit_key": similar_topic_key,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    if last_topic == topic and isinstance(last_evidence, dict):
        if needs_citation:
            item_rows = last_evidence.get("items") or []
            citation_rows = [r for r in item_rows if isinstance(r, dict) and str(r.get("url") or "").strip()]
            if len(citation_rows) >= 2:
                return {
                    "strategy": "reuse_context",
                    "reason": "existing evidence already has enough citations",
                    "strict_key": strict_key,
                    "topic_key": topic,
                    "time_sensitive": False,
                    "intent": intent.intent,
                    "intent_confidence": intent.confidence,
                    "intent_reason": intent.reason,
                    "intent_slots": intent.slots,
                }
            return {
                "strategy": "partial_search",
                "reason": "need more citations for same topic",
                "strict_key": strict_key,
                "topic_key": topic,
                "time_sensitive": False,
                "intent": intent.intent,
                "intent_confidence": intent.confidence,
                "intent_reason": intent.reason,
                "intent_slots": intent.slots,
            }
        return {
            "strategy": "reuse_context",
            "reason": "same topic in current session",
            "strict_key": strict_key,
            "topic_key": topic,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    if intent.intent == "clarify" and has_last_success:
        return {
            "strategy": "reuse_context",
            "reason": "intent router marked request as clarification",
            "strict_key": strict_key,
            "topic_key": topic,
            "time_sensitive": False,
            "intent": intent.intent,
            "intent_confidence": intent.confidence,
            "intent_reason": intent.reason,
            "intent_slots": intent.slots,
        }

    return {
        "strategy": "full_search",
        "reason": "no reusable evidence found",
        "strict_key": strict_key,
        "topic_key": topic,
        "topic_hit_key": None,
        "time_sensitive": False,
        "intent": intent.intent,
        "intent_confidence": intent.confidence,
        "intent_reason": intent.reason,
        "intent_slots": intent.slots,
    }


def run_scope_query_smart(
    scope: str,
    message: str,
    *,
    session_state: dict[str, Any] | None = None,
    state_path: str | Path | None = None,
    timeout_seconds: float = 60.0,
    ttl_seconds: int = 1800,
) -> dict[str, Any]:
    state = dict(session_state or {})
    if state_path:
        loaded = _load_state(state_path)
        if loaded:
            state = loaded
    state.setdefault("cache_strict", {})
    state.setdefault("cache_topic", {})
    state.setdefault("evidence_by_scope", {})
    state.setdefault("citations_by_topic", {})
    state.setdefault("failure_cooldown", {})
    state.setdefault("history", [])
    state.setdefault("intent_history", [])
    state.setdefault("pending_continue", False)

    plan = plan_scope_query(scope, message, state)
    strategy = plan["strategy"]
    strict_key = str(plan["strict_key"])
    topic = str(plan["topic_key"])
    topic_hit_key = str(plan.get("topic_hit_key") or topic)
    now = int(time.time())

    reused: dict[str, Any] | None = None
    if strategy == "reuse_context":
        candidate = state["evidence_by_scope"].get(scope)
        if isinstance(candidate, dict):
            reused = dict(candidate)
    elif strategy == "cache_hit":
        strict_cached = state["cache_strict"].get(strict_key)
        topic_cached = state["cache_topic"].get(topic_hit_key)
        cached = strict_cached if isinstance(strict_cached, dict) else topic_cached
        if isinstance(cached, dict) and isinstance(cached.get("evidence"), dict):
            reused = dict(cached["evidence"])

    if reused is None:
        query_mode = "partial" if strategy == "partial_search" else "full"
        evidence = run_scope_query(scope=scope, message=message, timeout_seconds=timeout_seconds, query_mode=query_mode)
        if strategy == "partial_search" and _has_successful_evidence(evidence):
            evidence = _trim_evidence_for_partial(evidence)
        expires_at = now + max(int(ttl_seconds), 30)
        state["cache_strict"][strict_key] = {"expires_at": expires_at, "evidence": evidence}
        state["cache_topic"][topic] = {"expires_at": expires_at, "evidence": evidence}
        if _has_successful_evidence(evidence):
            state["evidence_by_scope"][scope] = evidence
            state["citations_by_topic"][topic] = _extract_citations(evidence.get("items") or [])
            state["failure_cooldown"].pop(topic, None)
        else:
            # Avoid hammering unavailable data source for same topic.
            state["failure_cooldown"][topic] = now + 120
            fallback = state["evidence_by_scope"].get(scope)
            if _has_successful_evidence(fallback):
                evidence = dict(fallback)
                strategy = "reuse_context"
    else:
        evidence = reused

    state["last_topic_key"] = topic
    state["last_intent"] = str(plan.get("intent") or "other")
    state["last_intent_confidence"] = float(plan.get("intent_confidence") or 0.0)
    if state["last_intent"] == "confirm_continue":
        state["pending_continue"] = False
    state["history"].append(
        {
            "scope": scope,
            "message": _safe_query_text(message),
            "timestamp": now,
            "strategy": strategy,
            "reason": plan.get("reason"),
        }
    )
    state["intent_history"].append(
        {
            "scope": scope,
            "message": _safe_query_text(message),
            "timestamp": now,
            "intent": plan.get("intent"),
            "confidence": plan.get("intent_confidence"),
            "reason": plan.get("intent_reason"),
        }
    )
    if len(state["history"]) > 30:
        state["history"] = state["history"][-30:]
    if len(state["intent_history"]) > 50:
        state["intent_history"] = state["intent_history"][-50:]
    _save_state(state_path, state)

    result = dict(evidence)
    result["meta"] = {
        "strategy": strategy,
        "reason": plan.get("reason"),
        "strict_key": strict_key,
        "topic_key": topic,
        "time_sensitive": bool(plan.get("time_sensitive")),
        "citation_count": len(state["citations_by_topic"].get(topic) or []),
        "intent": str(plan.get("intent") or "other"),
        "intent_confidence": float(plan.get("intent_confidence") or 0.0),
        "intent_reason": str(plan.get("intent_reason") or ""),
        "intent_slots": plan.get("intent_slots") if isinstance(plan.get("intent_slots"), dict) else {},
    }
    return result


def format_scope_evidence_runtime(evidence: dict[str, Any]) -> str:
    """Render compact scope evidence into runtime context text."""
    scope = str(evidence.get("scope", "web"))
    query_text = str(evidence.get("query_text", ""))
    exit_code = int(evidence.get("exit_code", 1))
    parse_error = evidence.get("parse_error")
    stderr = str(evidence.get("stderr") or "")
    items = evidence.get("items") or []
    meta = evidence.get("meta") if isinstance(evidence.get("meta"), dict) else {}
    count = len(items) if isinstance(items, list) else 0

    lines = [
        "## Scope evidence (must use first)",
        f"- Scope query executed before analysis: {scope}",
        f"- Query text: {query_text}",
        f"- Query status: exit_code={exit_code}, items={count}",
    ]
    if "data_satisfactory" in evidence:
        lines.append(f"- Data satisfactory: {evidence.get('data_satisfactory')}")
        if evidence.get("data_failure_reason"):
            lines.append(f"- Data quality note: {evidence.get('data_failure_reason')}")
    if isinstance(evidence.get("relevance_summary"), dict) and evidence.get("relevance_summary"):
        lines.append(f"- Relevance summary: {evidence.get('relevance_summary')}")
    if meta:
        lines.append(
            "- Scope strategy: "
            + f"{meta.get('strategy', 'unknown')} ({str(meta.get('reason') or '').strip()})"
        )
    if parse_error:
        lines.append(f"- Query parse warning: {parse_error}")
    if stderr:
        lines.append(f"- Query stderr: {stderr[:500]}")

    preview = []
    for item in (items[:5] if isinstance(items, list) else []):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip()
        source = str(item.get("source") or "").strip()
        url = str(item.get("url") or "").strip()
        snippet = str(item.get("snippet") or "").strip()
        row = " | ".join(part for part in [title, source, url] if part)
        if snippet:
            row = f"{row} | {snippet[:180]}" if row else snippet[:180]
        if row:
            preview.append(f"  - {row}")
    if preview:
        lines.append("- Top evidence preview:")
        lines.extend(preview)
    lines.append("- If evidence is insufficient, state missing data explicitly before concluding.")
    return "\n".join(lines)
