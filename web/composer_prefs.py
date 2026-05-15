"""Normalize and format web UI composer preferences."""

from __future__ import annotations

import json
from typing import Any

# These enums are the contract between the web UI and backend runtime.
# Anything outside them is coerced back to a safe default.
ACTIVE_SKILLS = ("simple", "deep-research", "industry-overview")
PREFERRED_DATA_SOURCES = ("web", "mid_platform", "forum", "news")
ITERATION_BUDGETS = ("low", "medium", "high", "extra_high")
DEFAULT_COMPOSER_PREFS: dict[str, Any] = {
    "defaults": {
        "active_skill": "",
        "preferred_data_source": "web",
        "iteration_budget": "medium",
    },
    "skills": [],
}


def _coerce_active_skill(value: Any) -> str:
    """Normalize a skill value into a canonical skill name."""
    s = str(value or "").strip().lower()
    return s if s in ACTIVE_SKILLS else ""


def _coerce_preferred_data_source(value: Any) -> str:
    """Normalize a preferred data source label."""
    s = str(value or "").strip().lower()
    return s if s in PREFERRED_DATA_SOURCES else "web"


def _coerce_iteration_budget(value: Any) -> str:
    """Normalize a soft iteration budget tier."""
    s = str(value or "medium").strip().lower().replace("-", "_")
    return s if s in ITERATION_BUDGETS else "medium"


def normalize_composer_prefs(raw: Any) -> dict[str, Any]:
    """Return a deep-copied, validated composer prefs dict."""

    if not isinstance(raw, dict):
        return json.loads(json.dumps(DEFAULT_COMPOSER_PREFS))

    out: dict[str, Any] = json.loads(json.dumps(DEFAULT_COMPOSER_PREFS))
    defaults = raw.get("defaults")
    d = out["defaults"]
    source = defaults if isinstance(defaults, dict) else {}

    if isinstance(source, dict):
        d["active_skill"] = _coerce_active_skill(source.get("active_skill"))
        d["preferred_data_source"] = _coerce_preferred_data_source(
            source.get("preferred_data_source")
        )
        d["iteration_budget"] = _coerce_iteration_budget(source.get("iteration_budget"))

    active_skill = d["active_skill"]
    out["skills"] = [{"name": active_skill}] if active_skill else []
    return out


def merge_composer_prefs_for_turn(
    stored: dict[str, Any] | None,
    request: dict[str, Any] | None,
) -> dict[str, Any]:
    """Merge stored prefs with request-time overrides."""

    base = normalize_composer_prefs(stored)
    if not request or not isinstance(request, dict):
        return base
    merged: dict[str, Any] = json.loads(json.dumps(base))
    if "defaults" in request and isinstance(request["defaults"], dict):
        merged["defaults"] = {**merged["defaults"], **request["defaults"]}
    return normalize_composer_prefs(merged)


def format_composer_prefs_runtime(prefs: dict[str, Any]) -> str:
    """Render a human-readable runtime block for prompt injection."""

    p = normalize_composer_prefs(prefs)
    d = p["defaults"]
    active_skill = d["active_skill"]
    preferred_data_source = d["preferred_data_source"]
    iteration_budget = d["iteration_budget"]
    if not active_skill:
        allowed_iterations = 6
    elif active_skill == "simple":
        allowed_iterations = 10
    else:
        allowed_iterations = {
            "low": 3,
            "medium": 7,
            "high": 11,
            "extra_high": 15,
        }.get(iteration_budget, 7)
    active_skill_label = active_skill or "none"
    lines = [
        "## Composer preferences (apply this turn)",
        f"- Active skill: {active_skill_label} — {_skill_hint(active_skill)}",
        f"- Preferred data source: {preferred_data_source} — {_preferred_data_source_hint(preferred_data_source)}",
        f"- Allowed iterations this turn: {allowed_iterations}",
        "- Structured source retrieval tool: `data_source_search` (`mid_platform`, `forum`, `news` only)",
        "- Generic open-web tools remain available: `web_search`, `web_fetch`",
    ]
    skills = p.get("skills") or []
    if skills:
        skill_list = ", ".join(str(s.get("name", "")).strip() for s in skills if isinstance(s, dict)).strip(", ")
        if skill_list:
            lines.append(f"- Active skills: {skill_list}")
    if active_skill == "simple" and skills:
        lines.append(
            "- **Follow the `simple` skill** for this turn: read `skills/simple/SKILL.md` and keep the answer concise, practical, and evidence-aware."
        )
    if active_skill == "deep-research" and skills:
        lines.append(
            "- **Follow the `deep-research` skill** for this turn: read `skills/deep-research/SKILL.md` and execute its workflow (planning, evidence gathering, structured output)."
        )
    if active_skill == "industry-overview" and skills:
        lines.append(
            "- **Follow the `industry-overview` skill** for this turn: read `skills/industry-overview/SKILL.md` and produce a structured industry overview."
        )
    return "\n".join(lines)


def _skill_hint(active_skill: str) -> str:
    return {
        "": "no skill is active by default; infer whether a listed skill is useful from the request",
        "simple": "use the **simple** skill (workspace `skills/simple/SKILL.md`)",
        "deep-research": "use the **deep-research** skill (workspace `skills/deep-research/SKILL.md`)",
        "industry-overview": "use the **industry-overview** skill (workspace `skills/industry-overview/SKILL.md`)",
    }.get(active_skill, "")


def _preferred_data_source_hint(source: str) -> str:
    return {
        "web": "open web discovery via `web_search` / `web_fetch`",
        "mid_platform": "internal/Meritco report-style retrieval",
        "forum": "forum-style discussion and meeting-note retrieval",
        "news": "news/article retrieval with recency-sensitive evidence",
    }.get(source, "")


def should_disable_public_web_tools(prefs: dict[str, Any]) -> bool:
    """Return whether generic public web tools should be hidden from the agent."""
    normalize_composer_prefs(prefs)
    return False
