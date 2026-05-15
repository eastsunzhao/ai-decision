#!/usr/bin/env python3
"""Query the Meritco forum recall endpoint (POST)."""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import sys
import time
import uuid
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


DEFAULT_URL = "https://research.meritco-group.com/matrix-search/forum/open/recall"
AUTH_HEADER_NAME = "Api-Key"
AUTH_HEADER_VALUE = "9bddaffe7c5a0b9d5cec252b09c48040529260902b1a4166a10301d5510bd1e8"


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            encoding = sys.stdout.encoding or locale.getpreferredencoding(False) or "utf-8"
            sys.stdout.reconfigure(encoding=encoding, errors="replace")
        except Exception:
            pass


def parse_csv(values: list[str] | str | None) -> list[str]:
    if isinstance(values, str):
        values = [values]
    items: list[str] = []
    for value in values or []:
        for item in value.split(","):
            s = item.strip()
            if s:
                items.append(s)
    return items


def mask_value(value: str | None, prefix: int = 4, suffix: int = 4) -> str | None:
    if not value:
        return value
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}{'*' * (len(value) - prefix - suffix)}{value[-suffix:]}"


def parse_extra_headers(values: list[str] | None) -> dict[str, str]:
    headers: dict[str, str] = {}
    for value in values or []:
        for pair in value.split(","):
            s = pair.strip()
            if not s:
                continue
            if "=" in s:
                key, raw_val = s.split("=", 1)
            elif ":" in s:
                key, raw_val = s.split(":", 1)
            else:
                raise ValueError(f"Invalid header format: {s}. Use key=value or Key: value.")
            key = key.strip()
            raw_val = raw_val.strip()
            if not key:
                raise ValueError(f"Header key cannot be empty in: {s}")
            headers[key] = raw_val
    return headers


def build_headers(args: argparse.Namespace) -> dict[str, str]:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        AUTH_HEADER_NAME: args.token,
    }
    if args.authorization:
        headers["Authorization"] = args.authorization
    if args.cookie:
        headers["Cookie"] = args.cookie
    if args.user_agent:
        headers["User-Agent"] = args.user_agent
    if args.origin:
        headers["Origin"] = args.origin
    if args.referer:
        headers["Referer"] = args.referer
    headers.update(parse_extra_headers(args.header))
    return headers


def safe_headers(headers: dict[str, str]) -> dict[str, str]:
    redacted = dict(headers)
    secret_key_pattern = re.compile(r"(token|authorization|cookie|secret|key)", re.IGNORECASE)
    for k, v in list(redacted.items()):
        if secret_key_pattern.search(k):
            redacted[k] = mask_value(v)
    return redacted


def decode_bytes(raw: bytes, *preferred_encodings: str | None) -> str:
    tried: list[str] = []
    for enc in [*preferred_encodings, "utf-8", "gb18030", "gbk"]:
        if not enc:
            continue
        norm = enc.strip().strip('"').strip("'").lower()
        if not norm or norm in tried:
            continue
        tried.append(norm)
        try:
            return raw.decode(norm)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def build_trace_id() -> str:
    return f"{int(time.time() * 1000)}{uuid.uuid4().hex}"


def build_payload(args: argparse.Namespace) -> dict[str, Any]:
    query = (args.query or args.keyword or "").strip()
    return {
        "query": query,
        "start": args.start,
        "end": args.end,
    }


def _to_int(value: Any) -> int | None:
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit() or (text.startswith("-") and text[1:].isdigit()):
            return int(text)
    return None


def _extract_rows(data: dict[str, Any]) -> list[dict[str, Any]]:
    # Top-level list payloads.
    if isinstance(data.get("items"), list):
        return [row for row in data.get("items", []) if isinstance(row, dict)]
    if isinstance(data.get("data"), list):
        return [row for row in data.get("data", []) if isinstance(row, dict)]
    if isinstance(data.get("result"), list):
        return [row for row in data.get("result", []) if isinstance(row, dict)]

    # Common nested containers.
    root_data = data.get("data")
    if isinstance(root_data, dict):
        for key in ("items", "list", "rows", "records", "hits"):
            rows = root_data.get(key)
            if isinstance(rows, list):
                return [row for row in rows if isinstance(row, dict)]

    result = data.get("result")
    if not isinstance(result, dict):
        return []
    for key in ("forumList", "data", "items", "list", "rows", "records", "hits", "docs"):
        rows = result.get(key)
        if isinstance(rows, list):
            return [row for row in rows if isinstance(row, dict)]
        if isinstance(rows, dict):
            for inner_key in ("items", "list", "rows", "records", "hits", "docs"):
                inner_rows = rows.get(inner_key)
                if isinstance(inner_rows, list):
                    return [row for row in inner_rows if isinstance(row, dict)]
    return []


def _extract_total_count(data: dict[str, Any], rows: list[dict[str, Any]]) -> int:
    parsed_values: list[int] = []

    result = data.get("result")
    if isinstance(result, dict):
        for key in ("totalCount", "total", "count"):
            value = _to_int(result.get(key))
            if value is not None:
                parsed_values.append(value)
    for key in ("totalCount", "total", "count"):
        value = _to_int(data.get(key))
        if value is not None:
            parsed_values.append(value)

    if parsed_values:
        # Some API versions return count=0 even when current page has rows.
        return max(max(parsed_values), len(rows))

    return len(rows)


def _normalize_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        parts = [_normalize_text(item) for item in value]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, dict):
        parts = [_normalize_text(v) for v in value.values()]
        return " ".join(part for part in parts if part).strip()
    if isinstance(value, str):
        return " ".join(value.split())
    return str(value)


def _truncate(value: str, max_chars: int) -> str:
    if max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def _collect_focus_terms(args: argparse.Namespace) -> list[str]:
    raw_terms: list[str] = []
    raw_terms.extend(parse_csv(args.focus))
    raw_terms.extend(parse_csv(args.keyword))
    items: list[str] = []
    seen: set[str] = set()
    for term in raw_terms:
        normalized = term.strip().lower()
        if normalized and normalized not in seen:
            seen.add(normalized)
            items.append(normalized)
    return items


def _build_item(row: dict[str, Any], max_content_chars: int) -> dict[str, Any]:
    title = _normalize_text(row.get("title") or row.get("articleTitle") or row.get("name"))
    summary_text = _normalize_text(row.get("summary") or row.get("articleAbstract") or row.get("abstract"))
    content_hits = row.get("contentHits") if row.get("contentHits") is not None else row.get("hits")
    snippet = _normalize_text(
        row.get("summary")
        or row.get("contentPreview")
        or row.get("content")
        or row.get("articleAbstract")
        or row.get("abstract")
        or content_hits
        or row.get("highlight")
    )
    truncated_summary = _truncate(summary_text, max_content_chars) if summary_text else None
    truncated_snippet = _truncate(snippet, max_content_chars) if snippet else None
    return {
        "id": row.get("id") or row.get("forumId") or row.get("articleId") or row.get("docId"),
        "type": row.get("type"),
        "title": title or None,
        "summary": truncated_summary,
        "contentHits": content_hits,
        "source": row.get("source") or row.get("platform"),
        "url": row.get("url") or row.get("articleUrl") or row.get("link"),
        "pubTime": (
            row.get("pubTime")
            or row.get("articleDate")
            or row.get("date")
            or row.get("publishTime")
            or row.get("publishedAt")
            or row.get("createTime")
            or row.get("updateTime")
        ),
        "snippet": truncated_snippet,
        "raw": row,
    }


def _analyze_items(items: list[dict[str, Any]], focus_terms: list[str]) -> list[dict[str, Any]]:
    analyzed: list[dict[str, Any]] = []
    for item in items:
        text = f"{item.get('title') or ''}\n{item.get('snippet') or ''}".lower()
        score = sum(1 for term in focus_terms if term in text) if focus_terms else 0
        analyzed.append(
            {
                **item,
                "score": score,
                "relevance": "high" if score >= 2 else "medium" if score == 1 else "none",
                "matchedTerms": [term for term in focus_terms if term in text] if focus_terms else [],
            }
        )
    analyzed.sort(key=lambda x: (x.get("score", 0), x.get("pubTime") or ""), reverse=True)
    return analyzed


def _post_once(args: argparse.Namespace, headers: dict[str, str], payload: dict[str, Any]) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(args.url, data=body, headers=headers, method="POST")
    with urllib_request.urlopen(req, timeout=args.timeout) as resp:
        charset = resp.headers.get_content_charset() if getattr(resp, "headers", None) else None
        text = decode_bytes(resp.read(), charset)
        status = getattr(resp, "status", 200)
    return status, text


def _fetch_once(args: argparse.Namespace, headers: dict[str, str]) -> tuple[int, dict[str, Any], dict[str, Any]]:
    payload = build_payload(args)
    status, text = _post_once(args, headers, payload)
    data = json.loads(text)
    return status, data, payload


def print_response(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    configure_stdout()

    parser = argparse.ArgumentParser(description="Query Meritco forum recall")
    parser.add_argument("--url", default=os.getenv("MERITCO_FORUM_URL", DEFAULT_URL), help=f"Forum API URL. Default: {DEFAULT_URL}")
    parser.add_argument("--token", default=os.getenv("MERITCO_FORUM_TOKEN", AUTH_HEADER_VALUE), help=f"Value for `{AUTH_HEADER_NAME}` header")
    parser.add_argument("--authorization", default=os.getenv("MERITCO_FORUM_AUTHORIZATION"), help="Optional Authorization header (e.g. Bearer xxx)")
    parser.add_argument("--cookie", default=os.getenv("MERITCO_FORUM_COOKIE"), help="Optional Cookie header for logged-in session")
    parser.add_argument("--header", action="append", default=[], help="Extra headers: key=value or Key: value; repeatable or comma-separated")
    parser.add_argument("--origin", default=os.getenv("MERITCO_FORUM_ORIGIN"), help="Optional Origin header")
    parser.add_argument("--referer", default=os.getenv("MERITCO_FORUM_REFERER"), help="Optional Referer header")
    parser.add_argument("--user-agent", default=os.getenv("MERITCO_FORUM_USER_AGENT"), help="Optional User-Agent header")
    parser.add_argument("--keyword", default="", help="Compatibility alias for query terms")
    parser.add_argument("--query", default="", help="Recall query text")
    parser.add_argument("--start", default=os.getenv("MERITCO_FORUM_START", ""), help="Start time: yyyy-MM-dd HH:mm:ss")
    parser.add_argument("--end", default=os.getenv("MERITCO_FORUM_END", ""), help="End time: yyyy-MM-dd HH:mm:ss")
    parser.add_argument("--focus", action="append", default=[], help="Local analysis focus terms, comma-separated or repeatable")
    parser.add_argument("--timeout", type=float, default=30, help="Request timeout seconds")
    parser.add_argument("--format", choices=["json", "summary", "analyzed"], default="analyzed")
    parser.add_argument("--max-content-chars", type=int, default=240)
    parser.add_argument("--analyze-top-k", type=int, default=0, help="Top K analyzed rows; 0 means return all analyzed rows")
    args = parser.parse_args(argv)
    if not (args.query or args.keyword):
        parser.error("`--query` is required (or use `--keyword` for compatibility)")
    if not args.start:
        parser.error("`--start` is required")
    if not args.end:
        parser.error("`--end` is required")
    try:
        headers = build_headers(args)
    except ValueError as exc:
        parser.error(str(exc))
    masked_headers = safe_headers(headers)

    try:
        status, data, payload = _fetch_once(args, headers)
    except urllib_error.HTTPError as exc:
        charset = exc.headers.get_content_charset() if getattr(exc, "headers", None) else None
        err_body = decode_bytes(exc.read(), charset)
        fail_payload = build_payload(args)
        print_response(
            {
                "error": f"HTTPError: {exc.code}",
                "message": str(exc),
                "request": {"url": args.url, "headers": masked_headers, "payload": fail_payload},
                "response": err_body,
            }
        )
        return 1
    except (TimeoutError, urllib_error.URLError) as exc:
        fail_payload = build_payload(args)
        print_response(
            {
                "error": "Request failed",
                "message": str(exc),
                "request": {"url": args.url, "headers": masked_headers, "payload": fail_payload},
            }
        )
        return 1
    except json.JSONDecodeError:
        fail_payload = build_payload(args)
        print_response(
            {
                "error": "Response is not JSON",
                "request": {"url": args.url, "headers": masked_headers, "payload": fail_payload},
            }
        )
        return 1
    except RuntimeError as exc:
        fail_payload = build_payload(args)
        print_response(
            {
                "error": "Request failed",
                "message": str(exc),
                "request": {"url": args.url, "headers": masked_headers, "payload": fail_payload},
            }
        )
        return 1
    rows = _extract_rows(data)
    items = [_build_item(row, args.max_content_chars) for row in rows]
    focus_terms = _collect_focus_terms(args)
    analyzed_items = _analyze_items(items, focus_terms)
    top_k = 0 if args.analyze_top_k <= 0 else min(args.analyze_top_k, 2000)
    selected_items = analyzed_items if args.format == "analyzed" and top_k == 0 else (
        analyzed_items[:top_k] if args.format == "analyzed" else items
    )

    if args.format == "json":
        output: dict[str, Any] = dict(data)
    else:
        output = {
            "httpStatus": status,
            "code": data.get("code"),
            "message": data.get("message"),
            "totalCount": _extract_total_count(data, rows),
            "stats": {
                "rowsFetched": len(rows),
                "rowsReturned": len(selected_items),
            },
            "focus": {
                "terms": focus_terms,
                "analyzeTopK": top_k,
            },
            "items": selected_items,
        }

    data["httpStatus"] = status
    output["requestEcho"] = {
        "requestQuery": payload.get("query", ""),
        "focusTerms": focus_terms,
        "start": payload.get("start"),
        "end": payload.get("end"),
        "format": args.format,
    }
    print_response(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
