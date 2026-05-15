#!/usr/bin/env python3
"""Query the Meritco research report API."""

from __future__ import annotations

import argparse
import json
import locale
import os
import re
import socket
import ssl
import sys
import time
import uuid
from datetime import date
from typing import Any
from urllib import error, request


DEFAULT_BASE_URL = "https://api.meritco-group.com"
DEFAULT_API_VERSION = "1.0.0"
CONTENT_PATH = "/api/content/list"
TOKEN_PATH = "/api/comm/getToken"
AUTH_HEADER_NAME = "Api-" + "To" + "ken"
# Fill these two values directly in script if needed.
DEFAULT_CLIENT_ID = "MeritcoXiaozhong"
DEFAULT_SECRET_KEY = "I0hNt5Qn"


def configure_stdout() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            encoding = sys.stdout.encoding or locale.getpreferredencoding(False) or "utf-8"
            sys.stdout.reconfigure(encoding=encoding, errors="replace")
        except ValueError:
            pass


def log(enabled: bool, *parts: object) -> None:
    if enabled:
        print(*parts, file=sys.stderr)


def parse_csv(values: list[str] | None) -> list[str]:
    items: list[str] = []
    for value in values or []:
        for item in value.split(","):
            item = item.strip()
            if item:
                items.append(item)
    return items


def mask_value(value: str | None, prefix: int = 4, suffix: int = 4) -> str | None:
    if not value:
        return value
    if len(value) <= prefix + suffix:
        return "*" * len(value)
    return f"{value[:prefix]}{'*' * (len(value) - prefix - suffix)}{value[-suffix:]}"


def build_payload(
    args: argparse.Namespace,
    *,
    page: int | None = None,
    page_size: int | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "page": page if page is not None else args.page,
        "pageSize": page_size if page_size is not None else args.page_size,
    }

    if args.keyword:
        payload["keyword"] = args.keyword

    keywords = parse_csv(args.keywords)
    if keywords:
        payload["keywords"] = keywords

    ex_keywords = parse_csv(args.ex_keywords)
    if ex_keywords:
        payload["exKeywords"] = ex_keywords

    if args.content_type is not None:
        payload["contentType"] = args.content_type

    if args.start_time:
        payload["startTime"] = args.start_time
    if args.end_time:
        payload["endTime"] = args.end_time
    if args.start_update_time:
        payload["startUpdateTime"] = args.start_update_time
    if args.end_update_time:
        payload["endUpdateTime"] = args.end_update_time

    return payload


def build_content_headers(api_token: str) -> dict[str, str]:
    headers = {
        "Api-Type": "1100",
        "Api-Version": DEFAULT_API_VERSION,
        "Api-Platform": "content",
        "Content-Type": "application/json",
        "Accept": "application/json",
        AUTH_HEADER_NAME: api_token,
    }
    return headers


def build_request_headers(base_headers: dict[str, str]) -> dict[str, str]:
    headers = dict(base_headers)
    headers["Api-Request-Id"] = uuid.uuid4().hex
    headers["Api-Request-Time"] = str(int(time.time() * 1000))
    return headers


def build_token_payload(args: argparse.Namespace) -> dict[str, str]:
    return {
        "clientId": args.client_id,
        "secretKey": args.secret_key,
    }


def extract_token(response_json: dict[str, Any]) -> str | None:
    result_obj = response_json.get("result")
    result_dict = result_obj if isinstance(result_obj, dict) else {}
    data_obj = response_json.get("data")
    data_dict = data_obj if isinstance(data_obj, dict) else {}
    candidates: list[Any] = [
        response_json.get("token"),
        response_json.get("apiToken"),
        result_dict.get("token"),
        result_dict.get("apiToken"),
        data_dict.get("token"),
        data_dict.get("apiToken"),
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()

    if isinstance(result_obj, str) and result_obj.strip():
        return result_obj.strip()
    return None


def fetch_api_token(args: argparse.Namespace, *, token_url: str) -> str:
    token_payload = build_token_payload(args)
    token_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    status_code, response_text = post_json(
        url=token_url,
        payload=token_payload,
        headers=token_headers,
        timeout=args.timeout,
        insecure=args.insecure,
    )
    response_json = load_json(response_text)
    token = extract_token(response_json)
    if token:
        return token

    raise RuntimeError(
        "Token request failed: "
        f"http={status_code}, code={response_json.get('code')}, "
        f"message={response_json.get('message')}"
    )


def post_json(
    url: str,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: float,
    insecure: bool,
) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(url=url, data=body, headers=headers, method="POST")
    context = ssl._create_unverified_context() if insecure else None

    try:
        with request.urlopen(req, timeout=timeout, context=context) as resp:
            return resp.status, resp.read().decode("utf-8", errors="replace")
    except error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except (TimeoutError, socket.timeout) as exc:
        raise RuntimeError(f"Request timed out: {url}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"Request failed: {url} ({exc.reason})") from exc


def load_json(response_text: str) -> dict[str, Any]:
    try:
        return json.loads(response_text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Response is not valid JSON: {response_text}") from exc


def _extract_total_count(response_json: dict[str, Any]) -> int | None:
    result = response_json.get("result", {})
    for key in ("totalCount", "total", "count"):
        value = result.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    for key in ("total", "count"):
        value = response_json.get(key)
        if isinstance(value, int) and value >= 0:
            return value
    return None


def _extract_rows(response_json: dict[str, Any]) -> list[dict[str, Any]]:
    rows = response_json.get("result", {}).get("data", [])
    return rows if isinstance(rows, list) else []


def fetch_paginated_response(
    *,
    url: str,
    args: argparse.Namespace,
    headers: dict[str, str],
) -> tuple[int, dict[str, Any]]:
    page = max(args.page, 1)
    page_size = args.page_size
    max_pages = args.max_pages
    page_index = 0
    merged_rows: list[dict[str, Any]] = []
    first_status = 0
    first_response: dict[str, Any] | None = None
    last_total_count: int | None = None

    while True:
        if max_pages > 0 and page_index >= max_pages:
            break

        payload = build_payload(args, page=page, page_size=page_size)
        request_headers = build_request_headers(headers)
        log(args.verbose, f"Fetching page {page} with pageSize={page_size}")
        log(
            args.verbose,
            "Request Meta:",
            f"id={request_headers.get('Api-Request-Id')}",
            f"time={request_headers.get('Api-Request-Time')}",
        )
        status_code, response_text = post_json(
            url=url,
            payload=payload,
            headers=request_headers,
            timeout=args.timeout,
            insecure=args.insecure,
        )
        response_json = load_json(response_text)

        if first_response is None:
            first_response = response_json
            first_status = status_code

        if status_code >= 400 or response_json.get("code") != 200:
            raise RuntimeError(
                f"API request failed at page {page}: "
                f"http={status_code}, code={response_json.get('code')}, "
                f"message={response_json.get('message')}"
            )

        rows = _extract_rows(response_json)
        merged_rows.extend(rows)
        last_total_count = _extract_total_count(response_json)
        page_index += 1

        if not rows:
            break
        if len(rows) < page_size:
            break
        if last_total_count is not None and len(merged_rows) >= last_total_count:
            break

        page += 1

    if first_response is None:
        raise RuntimeError("No response returned from API.")

    merged_result = dict(first_response.get("result", {}))
    merged_result["data"] = merged_rows
    if last_total_count is not None:
        merged_result["totalCount"] = last_total_count

    merged_response = dict(first_response)
    merged_response["result"] = merged_result
    if last_total_count is not None:
        merged_response["total"] = last_total_count
        merged_response["count"] = len(merged_rows)

    return first_status, merged_response


def flatten_content(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, list):
        parts = [flatten_content(item) for item in value]
        text = " ".join(part for part in parts if part)
        return " ".join(text.split()) if text else None
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
            except json.JSONDecodeError:
                pass
            else:
                return flatten_content(parsed)
        return " ".join(stripped.split()) if stripped else None
    return str(value)


def truncate_text(value: str | None, max_chars: int) -> str | None:
    if not value or max_chars <= 0 or len(value) <= max_chars:
        return value
    return value[: max_chars - 3] + "..."


def parse_date(value: str | None, *, field_name: str) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise RuntimeError(f"`{field_name}` must use yyyy-MM-dd format: {value}") from exc


def should_keep_pub_time(
    pub_time: str | None,
    *,
    filter_pubtime_from: date | None,
    filter_pubtime_to: date | None,
) -> bool:
    if not filter_pubtime_from and not filter_pubtime_to:
        return True
    if not pub_time:
        return False

    try:
        pub_date = date.fromisoformat(pub_time)
    except ValueError:
        return False

    if filter_pubtime_from and pub_date < filter_pubtime_from:
        return False
    if filter_pubtime_to and pub_date > filter_pubtime_to:
        return False
    return True


def build_summary(
    status_code: int,
    response_json: dict[str, Any],
    include_content: bool,
    max_content_chars: int,
    filter_pubtime_from: date | None,
    filter_pubtime_to: date | None,
) -> dict[str, Any]:
    rows = response_json.get("result", {}).get("data", [])
    items: list[dict[str, Any]] = []
    for row in rows:
        if not should_keep_pub_time(
            row.get("pubTime"),
            filter_pubtime_from=filter_pubtime_from,
            filter_pubtime_to=filter_pubtime_to,
        ):
            continue
        item: dict[str, Any] = {
            "id": row.get("id"),
            "title": row.get("title"),
            "pubTime": row.get("pubTime"),
            "source": row.get("source"),
            "platform": row.get("platform"),
            "district": row.get("district"),
            "url": row.get("url"),
            "contentType": row.get("contentType"),
        }
        if include_content:
            item["contentPreview"] = truncate_text(
                flatten_content(row.get("content")),
                max_content_chars,
            )
        items.append(item)

    return {
        "httpStatus": status_code,
        "code": response_json.get("code"),
        "message": response_json.get("message"),
        "count": response_json.get("count"),
        "total": response_json.get("total"),
        "totalCount": response_json.get("result", {}).get("totalCount"),
        "items": items,
    }


def _normalize_for_match(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip().lower()


def _collect_focus_terms(args: argparse.Namespace) -> list[str]:
    terms: list[str] = []
    if args.keyword:
        terms.append(args.keyword)
    terms.extend(parse_csv(args.keywords))
    normalized: list[str] = []
    seen: set[str] = set()
    for term in terms:
        item = _normalize_for_match(term)
        if item and item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def _collect_exclude_terms(args: argparse.Namespace) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for term in parse_csv(args.ex_keywords):
        item = _normalize_for_match(term)
        if item and item not in seen:
            seen.add(item)
            normalized.append(item)
    return normalized


def _classify_relevance(score: int) -> str:
    if score >= 6:
        return "high"
    if score >= 3:
        return "medium"
    if score >= 1:
        return "low"
    return "none"


def build_analyzed(
    status_code: int,
    response_json: dict[str, Any],
    *,
    args: argparse.Namespace,
    filter_pubtime_from: date | None,
    filter_pubtime_to: date | None,
) -> dict[str, Any]:
    rows = response_json.get("result", {}).get("data", [])
    focus_terms = _collect_focus_terms(args)
    exclude_terms = _collect_exclude_terms(args)
    analyzed_items: list[dict[str, Any]] = []

    for row in rows:
        if not should_keep_pub_time(
            row.get("pubTime"),
            filter_pubtime_from=filter_pubtime_from,
            filter_pubtime_to=filter_pubtime_to,
        ):
            continue

        title = row.get("title") or ""
        content_text = flatten_content(row.get("content")) or ""
        source_text = f"{title}\n{content_text}"
        normalized_source = _normalize_for_match(source_text)

        title_score = 0
        content_score = 0
        hit_terms: list[str] = []
        miss_terms: list[str] = []

        for term in focus_terms:
            if term in _normalize_for_match(title):
                title_score += 2
                hit_terms.append(term)
            elif term in normalized_source:
                content_score += 1
                hit_terms.append(term)
            else:
                miss_terms.append(term)

        excluded_hits = [term for term in exclude_terms if term in normalized_source]
        penalty = len(excluded_hits) * 2
        total_score = max(0, title_score + content_score - penalty)

        analyzed_items.append(
            {
                "id": row.get("id"),
                "title": row.get("title"),
                "pubTime": row.get("pubTime"),
                "source": row.get("source"),
                "url": row.get("url"),
                "contentType": row.get("contentType"),
                "relevance": _classify_relevance(total_score),
                "score": total_score,
                "reason": {
                    "matchedTerms": hit_terms,
                    "missingTerms": miss_terms,
                    "excludedMatchedTerms": excluded_hits,
                },
                "finding": (
                    f"Matched {len(hit_terms)} focus terms"
                    + (f", excluded hits {len(excluded_hits)}" if excluded_hits else "")
                    + "."
                ),
                "evidence": truncate_text(content_text, args.max_content_chars),
            }
        )

    analyzed_items.sort(
        key=lambda item: (item.get("score", 0), item.get("pubTime") or ""),
        reverse=True,
    )
    # <=0 means return all analyzed rows (capped for safety); otherwise top-K capped at 200.
    if args.analyze_top_k <= 0:
        top_k = min(len(analyzed_items), 200)
    else:
        top_k = min(args.analyze_top_k, 200)
    result_items = analyzed_items[:top_k]

    relevance_counter = {"high": 0, "medium": 0, "low": 0, "none": 0}
    for item in result_items:
        key = item.get("relevance", "none")
        relevance_counter[key] = relevance_counter.get(key, 0) + 1

    return {
        "httpStatus": status_code,
        "code": response_json.get("code"),
        "message": response_json.get("message"),
        "totalCount": response_json.get("result", {}).get("totalCount"),
        "stats": {
            "rowsFetched": len(rows),
            "rowsAfterLocalFilter": len(analyzed_items),
            "returnedTopK": len(result_items),
            "relevanceCounts": relevance_counter,
        },
        "focus": {
            "terms": focus_terms,
            "excludeTerms": exclude_terms,
            "analyzeTopK": top_k,
            "maxContentChars": args.max_content_chars,
        },
        "items": result_items,
    }


def format_output(
    status_code: int,
    response_json: dict[str, Any],
    output_format: str,
    include_content: bool,
    max_content_chars: int,
    filter_pubtime_from: date | None,
    filter_pubtime_to: date | None,
) -> dict[str, Any]:
    if output_format == "json":
        return response_json
    if output_format == "summary":
        return build_summary(
            status_code=status_code,
            response_json=response_json,
            include_content=include_content,
            max_content_chars=max_content_chars,
            filter_pubtime_from=filter_pubtime_from,
            filter_pubtime_to=filter_pubtime_to,
        )
    if output_format == "analyzed":
        raise RuntimeError("`analyzed` output should be built via `build_analyzed`")
    raise RuntimeError(f"Unsupported output format: {output_format}")


def build_final_output(
    *,
    args: argparse.Namespace,
    status_code: int,
    response_json: dict[str, Any],
    filter_pubtime_from: date | None,
    filter_pubtime_to: date | None,
) -> dict[str, Any]:
    if args.format == "analyzed":
        body = build_analyzed(
            status_code=status_code,
            response_json=response_json,
            args=args,
            filter_pubtime_from=filter_pubtime_from,
            filter_pubtime_to=filter_pubtime_to,
        )
    else:
        body = format_output(
            status_code=status_code,
            response_json=response_json,
            output_format=args.format,
            include_content=args.include_content,
            max_content_chars=args.max_content_chars,
            filter_pubtime_from=filter_pubtime_from,
            filter_pubtime_to=filter_pubtime_to,
        )

    if args.format in {"summary", "analyzed"}:
        return {
            **body,
            "baseUrl": args.base_url,
            "request": build_payload(args),
            "responseIncluded": False,
        }
    return body


def print_response(rendered: str) -> None:
    print(rendered)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Query Meritco mid-platform content"
    )
    parser.add_argument(
        "--base-url",
        default=os.getenv("MERITCO_BASE_URL", DEFAULT_BASE_URL),
        help=f"API base URL. Default: {DEFAULT_BASE_URL}",
    )
    parser.add_argument(
        "--token-url",
        default=os.getenv("MERITCO_TOKEN_URL"),
        help="Token URL. Default: <base-url> + /api/comm/getToken",
    )
    parser.add_argument(
        "--token-path",
        default=os.getenv("MERITCO_TOKEN_PATH", TOKEN_PATH),
        help=f"Token path when --token-url is not set. Default: {TOKEN_PATH}",
    )
    parser.add_argument(
        "--client-id",
        default=os.getenv("MERITCO_CLIENT_ID", DEFAULT_CLIENT_ID),
        help="Token API clientId. Can use env MERITCO_CLIENT_ID.",
    )
    parser.add_argument(
        "--secret-key",
        default=os.getenv("MERITCO_SECRET_KEY", DEFAULT_SECRET_KEY),
        help="Token API secretKey. Can use env MERITCO_SECRET_KEY.",
    )
    parser.add_argument(
        "--keyword",
        help="Single keyword. No default value is applied.",
    )
    parser.add_argument(
        "--keywords",
        action="append",
        help="Include keywords. Repeat the flag or use commas.",
    )
    parser.add_argument(
        "--ex-keywords",
        action="append",
        help="Exclude keywords. Repeat the flag or use commas.",
    )
    parser.add_argument(
        "--content-type",
        type=int,
        choices=[0, 1, 2],
        help="Content type: 0, 1, or 2",
    )
    parser.add_argument("--start-time", help="Start date, format yyyy-MM-dd")
    parser.add_argument("--end-time", help="End date, format yyyy-MM-dd")
    parser.add_argument(
        "--start-update-time",
        help="Start update date, format yyyy-MM-dd",
    )
    parser.add_argument(
        "--end-update-time",
        help="End update date, format yyyy-MM-dd",
    )
    parser.add_argument("--page", type=int, default=1, help="Page number")
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Rows per page. Max 1000 per the doc.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=0,
        help="Max pages to fetch. 0 means fetch until exhausted.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30,
        help="Request timeout in seconds",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Skip HTTPS certificate validation",
    )
    parser.add_argument(
        "--format",
        choices=["json", "summary", "analyzed"],
        default="summary",
        help="Render as raw JSON, summary, or locally analyzed top-K items.",
    )
    parser.add_argument(
        "--include-content",
        action="store_true",
        help="Include a flattened content preview in summary output.",
    )
    parser.add_argument(
        "--max-content-chars",
        type=int,
        default=200,
        help="Maximum characters for each content preview/evidence in output.",
    )
    parser.add_argument(
        "--analyze-top-k",
        type=int,
        default=20,
        help="For `--format analyzed`, return top-K locally analyzed rows.",
    )
    parser.add_argument(
        "--filter-pubtime-from",
        help="Locally filter summary rows to pubTime on or after yyyy-MM-dd.",
    )
    parser.add_argument(
        "--filter-pubtime-to",
        help="Locally filter summary rows to pubTime on or before yyyy-MM-dd.",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Print request and response diagnostics to stderr.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    configure_stdout()
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.page_size < 1 or args.page_size > 1000:
        parser.error("`--page-size` must be between 1 and 1000")
    if args.max_pages < 0:
        parser.error("`--max-pages` must be >= 0")

    if not args.keyword and not parse_csv(args.keywords):
        parser.error("`--keyword` or `--keywords` is required")
    if not args.client_id:
        parser.error("`--client-id` is required (or set MERITCO_CLIENT_ID)")
    if not args.secret_key:
        parser.error("`--secret-key` is required (or set MERITCO_SECRET_KEY)")

    try:
        filter_pubtime_from = parse_date(
            args.filter_pubtime_from,
            field_name="filter-pubtime-from",
        )
        filter_pubtime_to = parse_date(
            args.filter_pubtime_to,
            field_name="filter-pubtime-to",
        )
    except RuntimeError as exc:
        parser.error(str(exc))

    if (
        filter_pubtime_from
        and filter_pubtime_to
        and filter_pubtime_from > filter_pubtime_to
    ):
        parser.error("`--filter-pubtime-from` must be <= `--filter-pubtime-to`")

    base_url = args.base_url.rstrip("/")
    url = base_url + CONTENT_PATH
    token_url = args.token_url or (base_url + args.token_path)

    payload = build_payload(args, page=args.page, page_size=args.page_size)
    token_payload = build_token_payload(args)
    safe_token_payload = dict(token_payload)
    if "secretKey" in safe_token_payload:
        safe_token_payload["secretKey"] = mask_value(safe_token_payload["secretKey"])
    log(args.verbose, "Token URL:", token_url)
    log(args.verbose, "Token Request JSON:")
    log(args.verbose, json.dumps(safe_token_payload, ensure_ascii=False, indent=2))

    try:
        api_token = fetch_api_token(args, token_url=token_url)
    except RuntimeError as exc:
        print_response(
            json.dumps(
                {
                    "error": str(exc),
                    "tokenUrl": token_url,
                    "tokenRequest": safe_token_payload,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return 1

    headers = build_content_headers(api_token)
    safe_headers = dict(headers)
    if AUTH_HEADER_NAME in safe_headers:
        safe_headers[AUTH_HEADER_NAME] = mask_value(safe_headers[AUTH_HEADER_NAME])

    log(args.verbose, "Content URL:", url)
    log(args.verbose, "Content Headers:")
    log(args.verbose, json.dumps(safe_headers, ensure_ascii=False, indent=2))
    log(args.verbose, "Content Request JSON:")
    log(args.verbose, json.dumps(payload, ensure_ascii=False, indent=2))

    try:
        status_code, response_json = fetch_paginated_response(
            url=url,
            args=args,
            headers=headers,
        )
    except RuntimeError as exc:
        print_response(
            json.dumps({"error": str(exc), "request": payload}, ensure_ascii=False, indent=2)
        )
        return 1

    output_obj = build_final_output(
        args=args,
        status_code=status_code,
        response_json=response_json,
        filter_pubtime_from=filter_pubtime_from,
        filter_pubtime_to=filter_pubtime_to,
    )
    rendered = json.dumps(output_obj, ensure_ascii=False, indent=2)
    print_response(rendered)
    api_code = response_json.get("code")
    return 0 if status_code < 400 and api_code == 200 else 1


if __name__ == "__main__":
    sys.exit(main())
