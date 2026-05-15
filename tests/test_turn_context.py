from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.turn_context import merge_turn_context_with_legacy


def test_explicit_empty_turn_context_does_not_fall_back_to_legacy_prefs() -> None:
    ctx = merge_turn_context_with_legacy(
        {"skills": [], "preferred_data_sources": [], "files": []},
        {"defaults": {"active_skill": "simple", "preferred_data_source": "web"}},
    )

    assert ctx == {
        "skills": [],
        "preferred_data_sources": [],
        "iteration_budget": "medium",
        "files": [],
    }


def test_missing_turn_context_can_still_use_legacy_prefs_for_compatibility() -> None:
    ctx = merge_turn_context_with_legacy(
        None,
        {
            "defaults": {
                "active_skill": "deep-research",
                "preferred_data_source": "forum",
                "iteration_budget": "high",
            }
        },
    )

    assert ctx == {
        "skills": ["deep-research"],
        "preferred_data_sources": ["forum"],
        "iteration_budget": "high",
        "files": [],
    }


def test_turn_context_normalizes_iteration_budget() -> None:
    ctx = merge_turn_context_with_legacy(
        {
            "skills": ["industry-overview"],
            "preferred_data_sources": [],
            "iteration_budget": "extra-high",
            "files": [],
        },
        None,
    )

    assert ctx["iteration_budget"] == "extra_high"
