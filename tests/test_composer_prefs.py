from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.composer_prefs import normalize_composer_prefs, should_disable_public_web_tools


def test_public_web_tools_remain_enabled_for_forum_source() -> None:
    prefs = {"defaults": {"active_skill": "simple", "preferred_data_source": "forum"}}
    assert should_disable_public_web_tools(prefs) is False


def test_public_web_tools_remain_enabled_for_web_source() -> None:
    prefs = {"defaults": {"active_skill": "simple", "preferred_data_source": "web"}}
    assert should_disable_public_web_tools(prefs) is False


def test_default_prefs_use_no_skill_and_web() -> None:
    prefs = normalize_composer_prefs({})
    assert prefs["defaults"]["active_skill"] == ""
    assert prefs["defaults"]["preferred_data_source"] == "web"
    assert prefs["defaults"]["iteration_budget"] == "medium"
    assert prefs["skills"] == []
    assert should_disable_public_web_tools(prefs) is False


def test_unknown_skill_maps_to_no_skill() -> None:
    prefs = normalize_composer_prefs(
        {"defaults": {"active_skill": "unknown", "preferred_data_source": "web"}}
    )
    assert prefs["defaults"]["active_skill"] == ""
    assert prefs["skills"] == []


def test_simple_skill_maps_to_simple_skill_record() -> None:
    prefs = normalize_composer_prefs(
        {"defaults": {"active_skill": "simple", "preferred_data_source": "forum"}}
    )
    assert [s["name"] for s in prefs["skills"]] == ["simple"]
    assert prefs["defaults"]["preferred_data_source"] == "forum"


def test_deep_research_skill_maps_to_deep_research_skill_record() -> None:
    prefs = normalize_composer_prefs(
        {"defaults": {"active_skill": "deep-research", "preferred_data_source": "web"}}
    )
    assert [s["name"] for s in prefs["skills"]] == ["deep-research"]
    assert prefs["defaults"]["preferred_data_source"] == "web"


def test_industry_overview_skill_maps_to_industry_overview_skill_record() -> None:
    prefs = normalize_composer_prefs(
        {"defaults": {"active_skill": "industry-overview", "preferred_data_source": "web"}}
    )
    assert [s["name"] for s in prefs["skills"]] == ["industry-overview"]
    assert prefs["defaults"]["preferred_data_source"] == "web"


def test_iteration_budget_normalizes_to_known_tiers() -> None:
    prefs = normalize_composer_prefs(
        {"defaults": {"active_skill": "deep-research", "iteration_budget": "extra-high"}}
    )

    assert prefs["defaults"]["iteration_budget"] == "extra_high"
