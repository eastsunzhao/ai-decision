from pathlib import Path
import asyncio

from nanobot.agent.tools.data_source import DataSourceSearchTool


def test_data_source_tool_formats_ranked_results(tmp_path: Path) -> None:
    tool = DataSourceSearchTool(workspace=tmp_path)
    result = tool._format_evidence(
        "forum",
        "battery supply chain",
        {
            "meta": {"strategy": "forum_search", "reason": "preferred source"},
            "items": [
                {
                    "title": "Battery demand update",
                    "source": "Meritco Forum",
                    "time": "2026-04-20",
                    "url": "https://example.com/forum/1",
                    "snippet": "Demand is improving in Q2.",
                }
            ],
        },
    )

    assert "Results from forum for: battery supply chain" in result
    assert "Strategy: forum_search (preferred source)" in result
    assert "1. Battery demand update" in result
    assert "Meritco Forum | 2026-04-20 | https://example.com/forum/1" in result
    assert "Demand is improving in Q2." in result


def test_data_source_tool_reports_weak_or_empty_results(tmp_path: Path) -> None:
    tool = DataSourceSearchTool(workspace=tmp_path)
    result = tool._format_evidence(
        "news",
        "robotics funding",
        {
            "data_satisfactory": False,
            "data_failure_reason": "low_relevance",
            "parse_error": "partial parse",
            "items": [],
        },
    )

    assert "Results from news for: robotics funding" in result
    assert "Data quality note: low_relevance" in result
    assert "Parse warning: partial parse" in result
    assert "No usable results returned." in result


def test_data_source_tool_rejects_web_source(tmp_path: Path) -> None:
    tool = DataSourceSearchTool(workspace=tmp_path)

    result = asyncio.run(tool.execute("web", "battery supply chain"))

    assert "no longer supports source=web" in result
    assert "web_search" in result
