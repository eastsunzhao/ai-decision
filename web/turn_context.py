"""Normalize one-turn composer context from the web UI."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import Any

if __package__:
    from .composer_prefs import (
        ACTIVE_SKILLS,
        ITERATION_BUDGETS,
        PREFERRED_DATA_SOURCES,
        normalize_composer_prefs,
    )
else:
    from composer_prefs import (
        ACTIVE_SKILLS,
        ITERATION_BUDGETS,
        PREFERRED_DATA_SOURCES,
        normalize_composer_prefs,
    )


DEFAULT_TURN_CONTEXT: dict[str, Any] = {
    "skills": [],
    "preferred_data_sources": [],
    "iteration_budget": "medium",
    "files": [],
}

DEFAULT_MAX_TOOL_ITERATIONS = 15


def allowed_iterations_for_runtime(
    iteration_budget: str,
    *,
    active_skill: str = "",
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
) -> int:
    """Return the iteration count the agent should treat as its turn budget."""
    skill = str(active_skill or "").strip().lower()
    if not skill:
        return 6
    if skill == "simple":
        return 10
    fractions = {
        "low": 0.25,
        "medium": 0.50,
        "high": 0.75,
        "extra_high": 1.00,
    }
    budget = str(iteration_budget or "medium").strip().lower().replace("-", "_")
    fraction = fractions.get(budget, fractions["medium"])
    return max(1, int(max_tool_iterations * fraction))


def _copy_default() -> dict[str, Any]:
    return {
        "skills": [],
        "preferred_data_sources": [],
        "iteration_budget": "medium",
        "files": [],
    }


def _normalize_file_path(value: Any, workspace_root: Path | None = None) -> str:
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
    normalized = "/".join(parts)
    if not normalized:
        return ""
    if workspace_root is not None:
        candidate = (workspace_root / Path(*normalized.split("/"))).resolve(strict=False)
        try:
            candidate.relative_to(workspace_root.resolve(strict=False))
        except ValueError:
            return ""
    return normalized


def normalize_turn_context(
    raw: Any,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Return validated one-turn context for skill/source/file mentions."""

    if not isinstance(raw, dict):
        return _copy_default()

    out = _copy_default()
    seen_skills: set[str] = set()
    for item in raw.get("skills") or []:
        name = str(item or "").strip().lower()
        if name in ACTIVE_SKILLS and name not in seen_skills:
            out["skills"].append(name)
            seen_skills.add(name)
        if out["skills"]:
            break

    seen_sources: set[str] = set()
    for item in raw.get("preferred_data_sources") or []:
        source = str(item or "").strip().lower()
        if source in PREFERRED_DATA_SOURCES and source not in seen_sources:
            out["preferred_data_sources"].append(source)
            seen_sources.add(source)

    budget = str(raw.get("iteration_budget") or "medium").strip().lower().replace("-", "_")
    if budget in ITERATION_BUDGETS:
        out["iteration_budget"] = budget

    seen_files: set[str] = set()
    for item in raw.get("files") or []:
        path = _normalize_file_path(item, workspace_root=workspace_root)
        if path and path not in seen_files:
            out["files"].append(path)
            seen_files.add(path)

    return out


def turn_context_from_legacy_prefs(prefs: dict[str, Any] | None) -> dict[str, Any]:
    """Translate old composer prefs into the new one-turn shape."""

    normalized = normalize_composer_prefs(prefs or {})
    defaults = normalized.get("defaults", {})
    skill = str(defaults.get("active_skill", "")).strip().lower()
    source = str(defaults.get("preferred_data_source", "")).strip().lower()
    budget = str(defaults.get("iteration_budget", "medium")).strip().lower().replace("-", "_")
    return {
        "skills": [skill] if skill in ACTIVE_SKILLS else [],
        "preferred_data_sources": [source] if source in PREFERRED_DATA_SOURCES else [],
        "iteration_budget": budget if budget in ITERATION_BUDGETS else "medium",
        "files": [],
    }


def merge_turn_context_with_legacy(
    raw_turn_context: Any,
    legacy_prefs: dict[str, Any] | None,
    *,
    workspace_root: Path | None = None,
) -> dict[str, Any]:
    """Prefer explicit turn context and fall back to legacy prefs.

    An explicit but empty turn_context means the user did not attach any
    skill/source for this turn. Do not reinterpret that as old persisted prefs.
    """

    turn_context = normalize_turn_context(raw_turn_context, workspace_root=workspace_root)
    if isinstance(raw_turn_context, dict):
        return turn_context
    if turn_context["skills"] or turn_context["preferred_data_sources"] or turn_context["files"]:
        return turn_context
    if legacy_prefs:
        return normalize_turn_context(
            turn_context_from_legacy_prefs(legacy_prefs),
            workspace_root=workspace_root,
        )
    return turn_context


def format_turn_context_runtime(
    turn_context: dict[str, Any],
    *,
    max_tool_iterations: int = DEFAULT_MAX_TOOL_ITERATIONS,
) -> str:
    """Render prompt-facing one-turn context."""

    ctx = normalize_turn_context(turn_context)
    active_skill = ctx["skills"][0] if ctx["skills"] else ""
    allowed_iterations = allowed_iterations_for_runtime(
        ctx["iteration_budget"],
        active_skill=active_skill,
        max_tool_iterations=max_tool_iterations,
    )
    lines = [
        "## Turn context",
        f"- Allowed iterations this turn: {allowed_iterations}",
    ]
    if ctx["skills"]:
        lines.append(f"- Attached skill: {', '.join(ctx['skills'])}")
    if ctx["preferred_data_sources"]:
        lines.append(f"- Preferred data sources: {', '.join(ctx['preferred_data_sources'])}")
    if ctx["files"]:
        lines.append(f"- Mentioned files: {', '.join(ctx['files'])}")
    return "\n".join(lines)
