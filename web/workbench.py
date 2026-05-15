"""Shared helpers for the three-pane web workbench.

These helpers do two jobs:

- normalize browser UI state into a small, stable backend shape
- safely map relative paths from the browser back into the session workspace

The functions are intentionally small because they sit on the trust boundary
between the web UI and file access.
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .composer_prefs import normalize_composer_prefs
    from .session_workspace import (
        SESSION_SHARED_CONTEXT_LINK,
        SESSION_SHARED_PERMANENT_LINK,
    )
else:
    from composer_prefs import normalize_composer_prefs
    from session_workspace import (
        SESSION_SHARED_CONTEXT_LINK,
        SESSION_SHARED_PERMANENT_LINK,
    )


# Files that exist for runtime bookkeeping but should not show up as regular
# user-editable workspace content in the browser.
HIDDEN_WORKSPACE_NAMES = {"_agent", "chat.json", "sessions", "sessions.json", ".DS_Store"}
EDITABLE_TEXT_EXTENSIONS = {
    ".md",
    ".txt",
    ".json",
    ".py",
    ".js",
    ".ts",
    ".css",
    ".html",
    ".yaml",
    ".yml",
    ".csv",
}
INLINE_TEXT_SIZE_LIMIT = 200_000
ACTIVATABLE_SKILLS = {"simple", "deep-research", "industry-overview"}
DEFAULT_LEFT_TAB = "files"
DEFAULT_CENTER_VIEW = "empty"


def normalize_relative_path(value: Any) -> str:
    """Convert a user-supplied path into a safe workspace-relative POSIX path."""
    raw = str(value or "").strip().replace("\\", "/")
    if not raw:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(raw).parts:
        if part in {"", ".", "/"}:
            continue
        if part == "..":
            return ""
        parts.append(part)
    return "/".join(parts)


def skill_name_from_prefs(prefs: dict[str, Any] | None) -> str:
    """Map normalized composer prefs back to the skill tab selection."""
    normalized = normalize_composer_prefs(prefs or {})
    return str(normalized.get("defaults", {}).get("active_skill", "")).strip()


def normalize_ui_state(
    raw: Any,
    *,
    composer_prefs: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Normalize persisted UI state for storage and server-side reuse.

    The browser may send partial or stale UI state. This function keeps it
    internally consistent so later prompt construction can trust it.
    """
    input_state = raw if isinstance(raw, dict) else {}
    default_skill = skill_name_from_prefs(composer_prefs)
    left_tab = str(input_state.get("left_tab", DEFAULT_LEFT_TAB)).strip().lower()
    if left_tab not in {"files", "skills"}:
        left_tab = DEFAULT_LEFT_TAB

    active_path = normalize_relative_path(input_state.get("active_path", ""))
    active_skill = str(input_state.get("active_skill", default_skill)).strip()
    if active_skill not in ACTIVATABLE_SKILLS:
        active_skill = default_skill if default_skill in ACTIVATABLE_SKILLS else ""

    selected_skill = str(input_state.get("selected_skill", active_skill)).strip()
    if not selected_skill:
        selected_skill = active_skill

    center_view = str(input_state.get("center_view", DEFAULT_CENTER_VIEW)).strip().lower()
    if center_view not in {"file", "skill", "empty"}:
        center_view = DEFAULT_CENTER_VIEW
    if center_view == "file" and not active_path:
        center_view = "empty"
    if center_view == "skill" and not selected_skill:
        center_view = "empty"

    return {
        "left_tab": left_tab,
        "active_path": active_path,
        "active_skill": active_skill,
        "selected_skill": selected_skill,
        "center_view": center_view,
    }


def normalize_chat_context(
    raw: Any,
    *,
    composer_prefs: dict[str, Any] | None = None,
    ui_state: dict[str, Any] | None = None,
) -> dict[str, str]:
    """Normalize the narrower context payload that accompanies one chat turn.

    ``ui_state`` represents what the whole page is showing; ``chat_context`` is
    the distilled subset that matters to one model turn.
    """
    input_ctx = raw if isinstance(raw, dict) else {}
    base = normalize_ui_state(ui_state or {}, composer_prefs=composer_prefs)
    active_path = normalize_relative_path(input_ctx.get("active_path", base["active_path"]))
    if "active_skill" in input_ctx:
        active_skill = str(input_ctx.get("active_skill", "")).strip()
        if active_skill and active_skill not in ACTIVATABLE_SKILLS:
            active_skill = ""
    else:
        active_skill = base["active_skill"]
    center_view = str(input_ctx.get("center_view", base["center_view"])).strip().lower()
    if center_view not in {"file", "skill", "empty"}:
        center_view = base["center_view"]
    if center_view == "file" and not active_path:
        center_view = "empty"
    return {
        "active_path": active_path,
        "active_skill": active_skill,
        "center_view": center_view,
    }


def format_workbench_context_runtime(
    context: dict[str, Any],
    workspace_root: Path,
    *,
    session_temp: Path | None = None,
) -> str:
    """Render a prompt-facing summary of what the user is currently focused on."""
    active_path = normalize_relative_path(context.get("active_path", ""))
    active_skill = str(context.get("active_skill", "")).strip() or "none"
    center_view = str(context.get("center_view", "empty")).strip() or "empty"
    lines = ["## User focus"]
    if center_view != "empty":
        lines.append(f"- Center view: {center_view}")
    if active_path:
        lines.append(f"- Active file: {active_path}")
    if active_skill != "none":
        lines.append(f"- Active skill: {active_skill}")
    if len(lines) == 1:
        return ""
    if active_skill == "deep-research" and session_temp is not None:
        lines.extend([
            "- Deep-research runtime artifacts must live in a new `deep_research_N/` directory inside the current session output directory.",
            "- Choose the smallest unused numeric suffix N by checking existing `deep_research_N/` directories, starting at 0.",
            "- Write deep-research plan to: `deep_research_N/plan.md`.",
            "- Write merged deep-research findings to: `deep_research_N/findings.md`.",
            "- Write per-step deep-research findings under: `deep_research_N/findings/`.",
            "- Do not prepend `temp/<session_id>/` when writing from the current session output directory.",
            "- Never write deep-research runtime artifacts under: `skills/deep-research/`.",
            f"- Never write deep-research runtime artifacts under: `{SESSION_SHARED_CONTEXT_LINK}/skills/deep-research/`.",
        ])
    return "\n".join(lines)


def resolve_workspace_path(workspace_root: Path, relative_path: Any) -> Path | None:
    """Resolve a normalized path and reject anything escaping the workspace root."""
    normalized = normalize_relative_path(relative_path)
    if not normalized:
        return None
    candidate = (workspace_root / Path(*normalized.split("/"))).resolve(strict=False)
    try:
        candidate.relative_to(workspace_root.resolve(strict=False))
    except ValueError:
        return None
    return candidate
