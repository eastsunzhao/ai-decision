"""Structured data source search tool."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from nanobot.agent.tools.base import Tool
from skills_scope.orchestrator import run_scope_query_smart


_FORMATTED_EVIDENCE_MAX_ROWS = 20


class DataSourceSearchTool(Tool):
    """Query a structured data source through the scope orchestrator."""

    name = "data_source_search"
    description = (
        "Search a structured non-web data source such as A) forum, B) news, or C) platform data. "
        "A) forum is a stock and industry analysis report database created and maintained by the "
        "Jiuqian secondary-market team. It includes: 1) investment analysis and forward-looking "
        "reports: deep and timely investment analysis and outlook reports created by leading equity "
        "research teams together with invited vertical-industry experts through interviews and "
        "discussions; 2) deep expert interviews: in-depth interviews with senior experts in vertical "
        "industries to obtain frontier knowledge, deep insight, and market outlook judgments. "
        "B) news is the Jiuqian secondary-market team's aggregation of global macro, geopolitical, "
        "political, industry, market, sector, and single-stock news. It mainly includes news summaries "
        "and titles from all major international financial media, with latency under 10 minutes, and "
        "is very suitable for real-time news tracking. "
        "C) platform is a collection of deep industry research reports from Jiuqian's primary-market "
        "and consulting teams. It contains many brokerage reports, industry reports, and deep expert "
        "interviews across many years of data, and is an excellent source for industry, secondary-market "
        "company, and primary-market investment/financing analysis. "
        "If possible, prefer searching this structured data source before open web retrieval."
    )
    parameters = {
        "type": "object",
        "properties": {
            "source": {
                "type": "string",
                "enum": ["mid_platform", "forum", "news"],
                "description": "The structured data source to query.",
            },
            "query": {
                "type": "string",
                "description": "The search query to run against the selected source.",
                "minLength": 1,
            },
        },
        "required": ["source", "query"],
    }

    def __init__(self, workspace: Path):
        self.workspace = Path(workspace)

    async def execute(self, source: str, query: str, **kwargs: Any) -> str:
        normalized_source = str(source or "").strip().lower()
        if normalized_source == "web":
            return (
                "Data source search no longer supports source=web. "
                "Use web_search for open web discovery and web_fetch for page reading."
            )
        state_path = self.workspace / "_agent" / "data_source_state.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        evidence = await asyncio.to_thread(
            run_scope_query_smart,
            normalized_source,
            query,
            state_path=state_path,
        )
        return self._format_evidence(normalized_source, str(query or "").strip(), evidence)

    @staticmethod
    def _format_evidence(source: str, query: str, evidence: dict[str, Any] | None) -> str:
        if not isinstance(evidence, dict):
            return f"Data source search failed for {source}: no structured result returned."

        items = evidence.get("items")
        rows = items if isinstance(items, list) else []
        meta = evidence.get("meta") if isinstance(evidence.get("meta"), dict) else {}
        lines = [f"Results from {source} for: {query}"]

        strategy = str(meta.get("strategy") or "").strip()
        reason = str(meta.get("reason") or "").strip()
        if strategy:
            lines.append(f"Strategy: {strategy}" + (f" ({reason})" if reason else ""))

        data_satisfactory = evidence.get("data_satisfactory")
        if data_satisfactory is False:
            reason_label = str(evidence.get("data_failure_reason") or "weak_evidence").strip()
            lines.append(f"Data quality note: {reason_label}")

        parse_error = str(evidence.get("parse_error") or "").strip()
        if parse_error:
            lines.append(f"Parse warning: {parse_error}")

        stderr = str(evidence.get("stderr") or "").strip()
        if stderr:
            lines.append(f"Query stderr: {stderr[:300]}")

        visible_rows = [row for row in rows if isinstance(row, dict)]
        if not visible_rows:
            lines.append("No usable results returned.")
            return "\n".join(lines)

        for idx, row in enumerate(visible_rows[:_FORMATTED_EVIDENCE_MAX_ROWS], 1):
            title = str(row.get("title") or "(untitled)").strip()
            row_source = str(row.get("source") or source).strip()
            timestamp = str(row.get("time") or row.get("pub_time") or "").strip()
            url = str(row.get("url") or "").strip()
            snippet = str(row.get("snippet") or "").strip()

            lines.append(f"{idx}. {title}")
            detail_parts = [part for part in (row_source, timestamp, url) if part]
            if detail_parts:
                lines.append("   " + " | ".join(detail_parts))
            if snippet:
                lines.append(f"   {snippet}")

        return "\n".join(lines)
