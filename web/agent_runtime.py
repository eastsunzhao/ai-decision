"""Shared runtime policy helpers for web agent execution."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

if __package__:
    from .composer_prefs import normalize_composer_prefs, should_disable_public_web_tools
    from .turn_context import allowed_iterations_for_runtime, format_turn_context_runtime, normalize_turn_context
    from .workbench import format_workbench_context_runtime, normalize_chat_context
else:
    from composer_prefs import normalize_composer_prefs, should_disable_public_web_tools
    from turn_context import allowed_iterations_for_runtime, format_turn_context_runtime, normalize_turn_context
    from workbench import format_workbench_context_runtime, normalize_chat_context


@dataclass(frozen=True)
class RuntimePolicy:
    """Prompt and routing policy derived for one web agent turn."""

    prefs: dict[str, Any]
    turn_context: dict[str, Any]
    active_skill_names: list[str]
    active_skill: str
    preferred_sources: list[str]
    preferred_source: str
    iteration_budget: str
    register_web: bool
    chat_context: dict[str, str]
    allowed_iterations: int
    runtime_text: str
    policy_trace_text: str


def build_runtime_policy(
    *,
    composer_prefs: dict[str, Any] | None,
    turn_context: dict[str, Any] | None,
    context: dict[str, Any] | None,
    tool_workspace: Path,
    session_temp: Path,
    max_tool_iterations: int = 15,
) -> RuntimePolicy:
    """Build the prompt-side policy shared by worker and in-process runners."""
    prefs = normalize_composer_prefs(composer_prefs)
    normalized_turn_context = normalize_turn_context(turn_context)
    active_skill_names = [
        str(name).strip()
        for name in (normalized_turn_context.get("skills") or [])
        if str(name).strip()
    ]
    active_skill = active_skill_names[0] if active_skill_names else ""
    preferred_sources = [
        str(source).strip()
        for source in (normalized_turn_context.get("preferred_data_sources") or [])
        if str(source).strip()
    ]
    preferred_source = ",".join(preferred_sources) if preferred_sources else "auto"
    iteration_budget = str(normalized_turn_context.get("iteration_budget") or "medium").strip().lower()
    allowed_iterations = allowed_iterations_for_runtime(
        iteration_budget,
        active_skill=active_skill,
        max_tool_iterations=max_tool_iterations,
    )
    register_web = not should_disable_public_web_tools(prefs)

    runtime_blocks = [format_turn_context_runtime(normalized_turn_context, max_tool_iterations=max_tool_iterations)]
    chat_context = normalize_chat_context(context, composer_prefs=prefs)
    workbench_context_runtime = format_workbench_context_runtime(
        chat_context,
        tool_workspace,
        session_temp=session_temp,
    )
    if workbench_context_runtime.strip():
        runtime_blocks.append(workbench_context_runtime)

    active_label = ", ".join(active_skill_names) if active_skill_names else "none"
    mode_policy_lines = [
        "## Execution policy",
        "- Structured retrieval: use `data_source_search` for `mid_platform`, `forum`, and `news` when it helps.",
        "- Open-web tools remain available: `web_search`, `web_fetch`.",
    ]
    has_explicit_active_skill = bool(active_skill_names) and active_label.lower() != "auto"
    if has_explicit_active_skill:
        mode_policy_lines.extend([
            f"- Active skill: {active_label}",
            "- You MUST strictly follow the active skill workflow for this turn.",
        ])
    if preferred_sources:
        mode_policy_lines.append(f"- Preferred data sources: {preferred_source}; use them as guidance, not a hard barrier.")
    runtime_blocks.append("\n".join(mode_policy_lines))
    policy_trace_text = ""
    if active_skill_names or preferred_sources or (normalized_turn_context.get("files") or []):
        policy_trace_text = (
            f"[Policy] skill={active_skill or 'none'}, "
            f"preferred_data_sources={preferred_source}, "
            f"allowed_iterations={allowed_iterations}"
        )

    return RuntimePolicy(
        prefs=prefs,
        turn_context=normalized_turn_context,
        active_skill_names=active_skill_names,
        active_skill=active_skill,
        preferred_sources=preferred_sources,
        preferred_source=preferred_source,
        iteration_budget=iteration_budget,
        register_web=register_web,
        chat_context=chat_context,
        allowed_iterations=allowed_iterations,
        runtime_text="\n\n".join(runtime_blocks),
        policy_trace_text=policy_trace_text,
    )


def classify_progress_event(
    content: str,
    *,
    tool_hint: bool = False,
    trace_meta: dict[str, Any] | None = None,
) -> str:
    """Map nanobot progress callbacks into a stable trace event type."""
    if isinstance(trace_meta, dict):
        meta_event = str(trace_meta.get("event_type", "")).strip()
        if meta_event:
            return meta_event
    if tool_hint:
        return "tool_hint"
    if content.startswith("[Tool payload] "):
        return "tool_result"
    return "progress"


def is_stop_ack(content: str) -> bool:
    """Return whether an outbound message is just acknowledging ``/stop``."""
    return content.startswith("Stopped ") or content == "No active task to stop."


def status_from_outbound_metadata(metadata: dict[str, object] | None) -> str:
    """Map Nanobot stop reasons onto Web run statuses."""
    stop_reason = str((metadata or {}).get("_stop_reason") or "").strip()
    if stop_reason in {"max_iterations", "error"}:
        return "error"
    return "complete"
