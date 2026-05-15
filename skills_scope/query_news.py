#!/usr/bin/env python3
"""Query Meritco news/articles from the agent articles full endpoint."""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import sys
import uuid
from typing import Any
from urllib import error as urllib_error
from urllib import parse as urllib_parse
from urllib import request as urllib_request


DEFAULT_BASE_URL = "http://192.168.0.13:5112"
API_PATH = "/api/v1/agent/articles/full"
AUTH_HEADER_NAME = "Api-" + "To" + "ken"
AUTH_HEADER_VALUE = "ecafcd7427bf4f1c90dba0b8ed16acc9"


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            encoding = sys.stdout.encoding or locale.getpreferredencoding(False) or "utf-8"
            sys.stdout.reconfigure(encoding=encoding, errors="replace")
        except Exception:
            pass


def mask_value(value: str | None, prefix: int = 4, suffix: int = 4) -> str | None:
    if not value:
        return value
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}{'*' * (len(value) - prefix - suffix)}{value[-suffix:]}"


def parse_include_full_text(value: str | None) -> bool:
    if value is None:
        return False
    s = str(value).strip().lower()
    return s in {"yes", "true", "1", "y", "t"}


def contains_cjk(text: str | None) -> bool:
    if not text:
        return False
    return bool(re.search(r"[\u4e00-\u9fff]", text))


def normalize_text(value: str | None) -> str:
    if not value:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def translate_zh_to_en_if_needed(keyword: str | None, *, timeout: float) -> tuple[str, str | None]:
    raw = normalize_text(keyword)
    if not raw:
        return "", None
    if not contains_cjk(raw):
        return raw, None

    params = urllib_parse.urlencode({"q": raw, "langpair": "zh-CN|en"})
    url = f"https://api.mymemory.translated.net/get?{params}"
    req = urllib_request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": f"nanobot-news-translate/1.0 ({uuid.uuid4().hex[:8]})",
        },
        method="GET",
    )
    try:
        with urllib_request.urlopen(req, timeout=max(3.0, min(timeout, 15.0))) as resp:
            text = resp.read().decode("utf-8", errors="replace")
        payload = json.loads(text)
        translated = normalize_text((payload.get("responseData") or {}).get("translatedText"))
        if translated:
            return translated, raw
    except Exception:
        pass
    return raw, raw


def build_query_params(
    *,
    since: str,
    cursor: str | None,
    q: str | None,
    limit: int,
    include_full_text: bool,
) -> dict[str, str]:
    params: dict[str, str] = {
        "since": since,
        "cursor": cursor or "",
        "q": normalize_text(q),
        "limit": str(limit),
        "include_full_text": "yes" if include_full_text else "no",
    }
    if not cursor:
        params.pop("cursor", None)
    if not normalize_text(q):
        params.pop("q", None)
    return params


def build_headers() -> dict[str, str]:
    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "X-Request-Id": uuid.uuid4().hex,
        AUTH_HEADER_NAME: AUTH_HEADER_VALUE,
        "User-Agent": f"nanobot-meritco-agent/1.0 ({uuid.uuid4().hex[:8]})",
    }


def serialize_article(article: dict[str, Any], *, include_full_text: bool) -> dict[str, Any]:
    out = dict(article or {})

    if "article_id" not in out:
        for k in ("id", "articleId", "articleID"):
            if k in out:
                out["article_id"] = out.get(k)
                break

    if not include_full_text:
        for key in list(out.keys()):
            lk = str(key).lower()
            if "full_text" in lk or "fulltext" in lk:
                out.pop(key, None)
    return out


def extract_next_cursor(payload: dict[str, Any]) -> str | None:
    candidates = [
        payload.get("next_cursor"),
        payload.get("nextCursor"),
        (payload.get("result") or {}).get("next_cursor") if isinstance(payload.get("result"), dict) else None,
        (payload.get("result") or {}).get("nextCursor") if isinstance(payload.get("result"), dict) else None,
    ]
    for item in candidates:
        text = normalize_text(str(item)) if item is not None else ""
        if text:
            return text
    return None


def article_text_for_match(article: dict[str, Any]) -> str:
    parts = [
        article.get("title"),
        article.get("summary"),
        article.get("description"),
        article.get("content"),
        article.get("full_text"),
        article.get("fullText"),
        article.get("text"),
    ]
    return normalize_text("\n".join(str(p) for p in parts if p))


def extract_match_terms(keyword: str | None) -> list[str]:
    text = normalize_text(keyword).lower()
    if not text:
        return []

    terms: list[str] = []

    def add_term(term: str) -> None:
        t = normalize_text(term).lower()
        if not t:
            return
        if t not in terms:
            terms.append(t)

    # Keep full phrase for strict matches when it exists naturally.
    if len(text) <= 80:
        add_term(text)

    en_stop = {
        "what",
        "which",
        "how",
        "when",
        "where",
        "who",
        "why",
        "the",
        "and",
        "for",
        "with",
        "from",
        "into",
        "that",
        "this",
        "these",
        "those",
        "recent",
        "latest",
    }
    for token in re.findall(r"[a-z0-9][a-z0-9+._/-]{1,}", text):
        if len(token) >= 3 and token not in en_stop:
            add_term(token)

    zh_noise = {
        "哪些",
        "如何",
        "什么",
        "还有",
        "近期",
        "主要",
        "因素",
        "影响",
        "时间线",
        "关键",
        "事件",
    }
    for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", text):
        if len(chunk) <= 4:
            if chunk not in zh_noise:
                add_term(chunk)
            continue
        # Use CJK bigrams so long questions can still match article text.
        for i in range(0, len(chunk) - 1):
            bg = chunk[i : i + 2]
            if bg not in zh_noise:
                add_term(bg)
            if len(terms) >= 40:
                break
        if len(terms) >= 40:
            break

    return terms[:40]


def analyze_articles(
    articles: list[dict[str, Any]],
    *,
    keyword_original: str | None,
    keyword_search: str | None,
    top_k: int,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    terms: list[str] = []
    for t in [keyword_original, keyword_search]:
        for term in extract_match_terms(t):
            if term not in terms:
                terms.append(term)

    analyzed: list[dict[str, Any]] = []
    for idx, article in enumerate(articles, 1):
        text = article_text_for_match(article)
        low = text.lower()
        matched = [term for term in terms if term and term in low]
        score = len(matched)
        relevance = "high" if score >= 2 else "medium" if score == 1 else "none"
        analyzed.append(
            {
                "rank": idx,
                "article_id": article.get("article_id") or article.get("id") or article.get("articleId"),
                "title": article.get("title"),
                "url": article.get("url"),
                "pub_time": article.get("pub_time") or article.get("pubTime") or article.get("publish_time"),
                "relevance": relevance,
                "score": score,
                "matched_terms": matched,
                "finding": (
                    f"Matched {len(matched)} term(s): {', '.join(matched)}."
                    if matched
                    else "No keyword hit in title/content."
                ),
                "evidence": text[:800],
                "raw": article,
            }
        )

    analyzed.sort(key=lambda x: (x.get("score", 0), str(x.get("pub_time") or "")), reverse=True)
    selected = analyzed if top_k <= 0 else analyzed[:top_k]

    context: list[dict[str, Any]] = []
    for item in selected:
        context.append(
            {
                "title": item.get("title"),
                "url": item.get("url"),
                "pub_time": item.get("pub_time"),
                "relevance": item.get("relevance"),
                "finding": item.get("finding"),
                "evidence": item.get("evidence"),
            }
        )
    return analyzed, context


def fetch_page(
    *,
    base_url: str,
    since: str,
    cursor: str | None,
    q: str | None,
    limit: int,
    include_full_text: bool,
    timeout: float,
    headers: dict[str, str],
) -> tuple[str, dict[str, Any], dict[str, str]]:
    params = build_query_params(
        since=since,
        cursor=cursor,
        q=q,
        limit=limit,
        include_full_text=include_full_text,
    )
    url = base_url.rstrip("/") + API_PATH
    query = urllib_parse.urlencode(params, safe="| :T-")
    full_url = f"{url}?{query}"
    req = urllib_request.Request(full_url, headers=headers, method="GET")
    with urllib_request.urlopen(req, timeout=timeout) as resp:
        text = resp.read().decode("utf-8", errors="replace")
    payload = json.loads(text)
    return full_url, payload, params


def print_response(payload: Any) -> None:
    print(json.dumps(payload, ensure_ascii=False, indent=2))


def main(argv: list[str] | None = None) -> int:
    configure_stdout()

    parser = argparse.ArgumentParser(description="Query Meritco news/articles (full text optional)")
    parser.add_argument(
        "--base-url",
        default=os.getenv("MERITCO_AGENT_BASE_URL", DEFAULT_BASE_URL),
        help=f"Base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument("--since", default="2000-01-01T00:00:00Z", help="Start time (ISO or yyyy-MM-dd HH:MM:SS)")
    parser.add_argument("--cursor", default=None, help="Pagination cursor: <cursor_time>|<last_article_id>")
    parser.add_argument("--limit", type=int, default=100, help="Max results per page: 1..100 (default 100)")
    parser.add_argument("--q", default="", help="Server-side query keyword for API param `q`")
    parser.add_argument("--keyword", default="", help="Keyword for local analysis/filtering")
    parser.add_argument("--max-pages", type=int, default=0, help="Max pages to fetch; 0 means fetch all pages")
    parser.add_argument(
        "--analyze-top-k",
        type=int,
        default=20,
        help="Top-K analyzed rows to include in context; <=0 means all",
    )
    parser.add_argument(
        "--include-full-text",
        default="true",
        help="Include full_text: yes/true/1 (otherwise no). Default: true",
    )
    parser.add_argument("--timeout", type=float, default=30, help="Request timeout seconds")
    args = parser.parse_args(argv)

    limit = args.limit
    if limit < 1:
        parser.error("`--limit` must be >= 1")
    if limit > 100:
        limit = 100

    include_full_text = parse_include_full_text(args.include_full_text)
    analysis_keyword = normalize_text(args.keyword) or normalize_text(args.q)
    translated_keyword, original_keyword = translate_zh_to_en_if_needed(analysis_keyword, timeout=args.timeout)

    headers = build_headers()
    safe_headers = dict(headers)
    if AUTH_HEADER_NAME in safe_headers:
        safe_headers[AUTH_HEADER_NAME] = mask_value(safe_headers.get(AUTH_HEADER_NAME))

    all_articles: list[dict[str, Any]] = []
    current_cursor = args.cursor
    pages = 0
    first_payload: dict[str, Any] | None = None
    last_url = ""
    last_params: dict[str, str] = {}
    next_cursor: str | None = current_cursor

    try:
        while True:
            if args.max_pages > 0 and pages >= args.max_pages:
                break
            full_url, payload, params = fetch_page(
                base_url=args.base_url,
                since=args.since,
                cursor=current_cursor,
                q=args.q,
                limit=limit,
                include_full_text=include_full_text,
                timeout=args.timeout,
                headers=headers,
            )
            last_url = full_url
            last_params = params
            if first_payload is None:
                first_payload = payload

            articles_in = payload.get("articles") or []
            page_items = [
                serialize_article(a, include_full_text=include_full_text)
                for a in (articles_in if isinstance(articles_in, list) else [])
            ]
            all_articles.extend(page_items)
            pages += 1

            next_cursor = extract_next_cursor(payload)
            if not page_items or not next_cursor or next_cursor == current_cursor:
                break
            current_cursor = next_cursor
    except urllib_error.HTTPError as exc:
        err_body = exc.read().decode("utf-8", errors="replace")
        print_response(
            {
                "error": f"HTTPError: {exc.code}",
                "message": str(exc),
                "request": {"url": last_url or (args.base_url.rstrip('/') + API_PATH), "headers": safe_headers, "query_params": last_params},
                "response": err_body,
            }
        )
        return 1
    except (TimeoutError, urllib_error.URLError) as exc:
        print_response(
            {
                "error": "Request failed",
                "message": str(exc),
                "request": {"url": last_url or (args.base_url.rstrip('/') + API_PATH), "headers": safe_headers, "query_params": last_params},
            }
        )
        return 1
    except json.JSONDecodeError:
        print_response(
            {
                "error": "Response is not JSON",
                "request": {"url": last_url or (args.base_url.rstrip('/') + API_PATH), "headers": safe_headers, "query_params": last_params},
            }
        )
        return 1

    analyzed, context_items = analyze_articles(
        all_articles,
        keyword_original=original_keyword or analysis_keyword,
        keyword_search=translated_keyword,
        top_k=args.analyze_top_k,
    )

    out: dict[str, Any] = {
        "code": (first_payload or {}).get("code"),
        "message": (first_payload or {}).get("message"),
        "since": args.since,
        "q": normalize_text(args.q),
        "include_full_text": include_full_text,
        "limit": limit,
        "pages_fetched": pages,
        "rows_fetched": len(all_articles),
        "next_cursor": next_cursor,
        "keyword": {
            "input": analysis_keyword,
            "translated_en": translated_keyword if analysis_keyword else "",
            "is_chinese": contains_cjk(analysis_keyword),
        },
        "stats": {
            "high": sum(1 for x in analyzed if x.get("relevance") == "high"),
            "medium": sum(1 for x in analyzed if x.get("relevance") == "medium"),
            "none": sum(1 for x in analyzed if x.get("relevance") == "none"),
        },
        "items": analyzed,
        "context": context_items,
    }

    print_response(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
