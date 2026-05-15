from pathlib import Path

from web.agent_runtime import (
    build_runtime_policy,
    classify_progress_event,
    status_from_outbound_metadata,
)


def test_build_runtime_policy_derives_prompt_and_routing_fields(tmp_path: Path) -> None:
    policy = build_runtime_policy(
        composer_prefs={"defaults": {"active_skill": "deep-research", "preferred_data_source": "web"}},
        turn_context={
            "skills": ["deep-research"],
            "preferred_data_sources": ["forum", "news"],
            "iteration_budget": "high",
            "files": ["permanent/report.md"],
        },
        context={
            "active_path": "permanent/report.md",
            "active_skill": "deep-research",
            "center_view": "file",
        },
        tool_workspace=tmp_path / "workspace",
        session_temp=tmp_path / "workspace" / "temp" / "session-1",
    )

    assert policy.active_skill_names == ["deep-research"]
    assert policy.active_skill == "deep-research"
    assert policy.preferred_sources == ["forum", "news"]
    assert policy.preferred_source == "forum,news"
    assert policy.iteration_budget == "high"
    assert policy.register_web is True
    assert policy.chat_context == {
        "active_path": "permanent/report.md",
        "active_skill": "deep-research",
        "center_view": "file",
    }
    assert "## Turn context" in policy.runtime_text
    assert "## Execution policy" in policy.runtime_text
    assert "- Preferred data sources: forum, news" in policy.runtime_text
    assert "- Preferred data sources: forum,news; use them as guidance, not a hard barrier." in policy.runtime_text
    assert policy.runtime_text.count("Allowed iterations this turn") == 1
    assert "- Allowed iterations this turn: 11" in policy.runtime_text
    assert str(tmp_path) not in policy.runtime_text
    assert "Workspace root:" not in policy.runtime_text
    assert "Agent file-tool workspace:" not in policy.runtime_text
    assert "Default session output directory:" not in policy.runtime_text
    assert "Current session output directory" not in policy.runtime_text
    assert "Active file runtime path" not in policy.runtime_text
    assert policy.allowed_iterations == 11
    assert policy.policy_trace_text == (
        "[Policy] skill=deep-research, preferred_data_sources=forum,news, allowed_iterations=11"
    )


def test_progress_event_classification_prefers_metadata() -> None:
    assert classify_progress_event("anything", trace_meta={"event_type": "retry_wait"}) == "retry_wait"
    assert classify_progress_event("search", tool_hint=True) == "tool_hint"
    assert classify_progress_event("[Tool payload] result") == "tool_result"
    assert classify_progress_event("working") == "progress"


def test_status_from_outbound_metadata_marks_failures_as_error() -> None:
    assert status_from_outbound_metadata({"_stop_reason": "max_iterations"}) == "error"
    assert status_from_outbound_metadata({"_stop_reason": "error"}) == "error"
    assert status_from_outbound_metadata({"_stop_reason": "completed"}) == "complete"
