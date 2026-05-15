from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.session_workspace import (
    SESSION_SHARED_CONTEXT_LINK,
    SESSION_SHARED_PERMANENT_LINK,
    prepare_session_workspace,
)
from web.workbench import format_workbench_context_runtime


def test_deep_research_runtime_uses_static_numbered_directory_rule(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    session_temp = workspace / "temp" / "session-a"

    text = format_workbench_context_runtime(
        {"active_skill": "deep-research", "active_path": "", "center_view": "empty"},
        workspace,
        session_temp=session_temp,
    )

    assert "deep_research_N/" in text
    assert "`deep_research_N/plan.md`" in text
    assert "`deep_research_N/findings.md`" in text
    assert "`deep_research_N/findings/`" in text
    assert "smallest unused numeric suffix" in text
    assert str(workspace) not in text
    assert str(session_temp) not in text
    assert "Workspace root:" not in text
    assert "Agent file-tool workspace:" not in text
    assert "Default session output directory:" not in text
    assert "Current session output directory" not in text
    assert "Active file runtime path" not in text
    assert f"Deep-research runtime directory: {session_temp / 'skills' / 'deep-research'}" not in text
    assert f"Write deep-research plan to: {session_temp / 'skills' / 'deep-research' / 'plan.md'}" not in text
    assert f"`{SESSION_SHARED_CONTEXT_LINK}/skills/deep-research/`" in text
    assert f"`{SESSION_SHARED_PERMANENT_LINK}/`" not in text
    assert f"`{SESSION_SHARED_CONTEXT_LINK}/`" not in text


def test_prepare_session_workspace_creates_session_shared_symlinks(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("NANOBOT_WEB_RUNTIME_DIR", str(tmp_path / "runtime"))

    session_temp = prepare_session_workspace("10001", "session-a", kind="temp")

    shared_permanent = session_temp / SESSION_SHARED_PERMANENT_LINK
    shared_context = session_temp / SESSION_SHARED_CONTEXT_LINK

    assert shared_permanent.is_symlink()
    assert shared_context.is_symlink()
    assert shared_permanent.resolve() == session_temp.parent.parent / "permanent"
    assert shared_context.resolve() == session_temp.parent.parent / "_agent"
