"""One-shot worker process for WSL/bwrap execution.

This module is the child process launched by :mod:`web.run_bot`. It exists so
the web app can run one chat turn inside an isolated subprocess and speak to it
through a tiny JSON-over-stdin/stdout protocol:

- parent sends one JSON payload on stdin
- worker emits progress/done/error JSON lines on stdout
- parent converts those lines into SSE events for the browser
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from contextlib import suppress
from pathlib import Path


def _ensure_repo_import_paths() -> None:
    """Make repo-local imports work when this module is launched via ``python -m``."""
    repo_root = Path(__file__).resolve().parents[1]
    vendored_nanobot_root = repo_root / "nanobot"
    for candidate in (repo_root, vendored_nanobot_root):
        candidate_str = str(candidate)
        if candidate.exists() and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)


_ensure_repo_import_paths()


def _pin_python_for_subprocesses() -> None:
    """Keep exec subprocesses on the same interpreter family as this worker."""
    nb_python = os.environ.get("NB_PYTHON") or sys.executable
    os.environ["NB_PYTHON"] = nb_python

    expanded_python = os.path.expandvars(str(Path(nb_python).expanduser()))
    python_dir = os.path.dirname(expanded_python)
    current_path = os.environ.get("PATH", "")
    path_parts = [part for part in current_path.split(os.pathsep) if part]
    if python_dir and python_dir not in path_parts:
        os.environ["PATH"] = python_dir + (os.pathsep + current_path if current_path else "")


_pin_python_for_subprocesses()


from web.agent_runtime import (
    build_runtime_policy,
    classify_progress_event,
    status_from_outbound_metadata,
)
from web.session_workspace import prepare_session_workspace
from web.turn_context import merge_turn_context_with_legacy


def _status_from_outbound_metadata(metadata: dict[str, object] | None) -> str:
    """Map Nanobot stop reasons onto Web run statuses."""
    return status_from_outbound_metadata(metadata)


def _emit(payload: dict) -> None:
    """Write one JSON event line for the parent process to consume."""
    print(json.dumps(payload, ensure_ascii=False), flush=True)


async def _run_once(
    *,
    user_id: str,
    session_id: str,
    message: str,
    composer_prefs: dict | None,
    turn_context: dict | None,
    context: dict | None,
) -> tuple[str, str]:
    """Execute a single agent turn inside the worker process.

    The structure mirrors the in-process fallback in ``web.run_bot`` so the two
    execution paths behave the same from the browser's perspective.
    """
    from loguru import logger

    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage
    from nanobot.bus.queue import MessageBus
    from nanobot.config.loader import load_config
    from nanobot.providers.factory import create_provider
    # The web layer already streams user-facing progress separately; turning down
    # normal nanobot logging keeps stdout reserved for machine-readable events.
    logger.disable("nanobot")
    config = load_config()
    tool_workspace = prepare_session_workspace(user_id, session_id, kind="tool")
    context_workspace = prepare_session_workspace(user_id, session_id, kind="context")
    session_temp = prepare_session_workspace(user_id, session_id, kind="temp")

    bus = MessageBus()
    provider = create_provider(config)
    merged_turn_context = merge_turn_context_with_legacy(turn_context, composer_prefs)
    policy = build_runtime_policy(
        composer_prefs=composer_prefs,
        turn_context=merged_turn_context,
        context=context,
        tool_workspace=tool_workspace,
        session_temp=session_temp,
        max_tool_iterations=config.agents.defaults.max_tool_iterations,
    )
    active_skill = policy.active_skill
    preferred_source = policy.preferred_source
    iteration_budget = policy.iteration_budget
    os.environ["NB_REPO_ROOT"] = str(Path(__file__).resolve().parents[1])
    os.environ["NB_ACTIVE_SKILL"] = active_skill or "auto"
    os.environ["NB_PREFERRED_DATA_SOURCE"] = preferred_source
    register_web = policy.register_web
    runtime_text = policy.runtime_text
    chat_context = policy.chat_context
    if policy.policy_trace_text:
        _emit({
            "type": "trace",
            "event_type": "progress",
            "text": policy.policy_trace_text,
        })

    reasoning_effort = config.agents.defaults.reasoning_effort
    if active_skill == "deep-research" and str(reasoning_effort or "").lower() not in {"medium", "high", "xhigh"}:
        reasoning_effort = "medium"

    # Web owns the subagent finalization policy; Nanobot only enforces the
    # in-loop safety check before accepting a final answer.
    os.environ.setdefault(
        "NANOBOT_SUBAGENT_IDLE_TIMEOUT_S",
        os.environ.get("WEB_SUBAGENT_IDLE_TIMEOUT_S", "400"),
    )

    agent_loop = AgentLoop(
        bus=bus,
        provider=provider,
        workspace=session_temp,
        context_workspace=context_workspace,
        exec_working_dir=session_temp,
        active_skill=active_skill,
        preferred_data_source=preferred_source,
        iteration_budget=iteration_budget,
        model=provider.get_default_model(),
        temperature=config.agents.defaults.temperature,
        max_tokens=config.agents.defaults.max_tokens,
        context_window_tokens=config.agents.defaults.context_window_tokens,
        max_iterations=config.agents.defaults.max_tool_iterations,
        memory_window=getattr(config.agents.defaults, "memory_window", 100),
        reasoning_effort=reasoning_effort,
        web_search_config=config.tools.web.search,
        web_search_api_key=config.tools.web.search.api_key or None,
        web_search_max_results=config.tools.web.search.max_results,
        web_proxy=config.tools.web.proxy or None,
        exec_config=config.tools.exec,
        cron_service=None,
        restrict_to_workspace=True,
        mcp_servers=config.tools.mcp_servers,
        channels_config=config.channels,
        disabled_skills=config.agents.defaults.disabled_skills,
        system_prompt_files=config.agents.defaults.system_prompt_files,
        register_web_tools=register_web,
        tools_config=config.tools,
    )

    loop_task = None
    try:
        loop_task = asyncio.create_task(agent_loop.run())
        await bus.publish_inbound(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id=f"{user_id}:{session_id}",
                content=message,
                metadata={
                    "composer_prefs_runtime": runtime_text,
                    "workbench_context": chat_context,
                    "_wants_stream": True,
                },
            )
        )
        # Consume outbound bus messages until the agent produces a final answer.
        message_tool_contents: list[str] = []
        target_chat_id = f"{user_id}:{session_id}"
        while True:
            outbound = await bus.consume_outbound()
            if outbound.metadata.get("_retry_wait"):
                _emit({
                    "type": "trace",
                    "event_type": "retry_wait",
                    "text": outbound.content or "Model request failed, retrying.",
                    "trace_meta": {"event_type": "retry_wait"},
                })
                continue
            if outbound.metadata.get("_message_tool_delivery"):
                if outbound.channel == "web" and outbound.chat_id == target_chat_id:
                    content = outbound.content or ""
                    if content:
                        message_tool_contents.append(content)
                        _emit({
                            "type": "stream",
                            "event_type": "assistant_delta",
                            "text": content,
                            "stream_meta": {
                                "stream_id": f"message-tool:{len(message_tool_contents)}",
                                "source": "message_tool",
                            },
                        })
                continue
            if outbound.metadata.get("_stream_delta"):
                _emit({
                    "type": "stream",
                    "event_type": "assistant_delta",
                    "text": outbound.content,
                    "stream_meta": {
                        "stream_id": outbound.metadata.get("_stream_id"),
                    },
                })
                continue
            if outbound.metadata.get("_stream_end"):
                _emit({
                    "type": "stream",
                    "event_type": "assistant_end",
                    "text": "",
                    "stream_meta": {
                        "stream_id": outbound.metadata.get("_stream_id"),
                        "resuming": bool(outbound.metadata.get("_resuming", False)),
                    },
                })
                continue
            if outbound.metadata.get("_progress"):
                trace_meta = outbound.metadata.get("_trace_meta")
                if not isinstance(trace_meta, dict):
                    trace_meta = None
                _emit({
                    "type": "trace",
                    "event_type": classify_progress_event(
                        outbound.content,
                        tool_hint=bool(outbound.metadata.get("_tool_hint")),
                        trace_meta=trace_meta,
                    ),
                    "text": outbound.content,
                    "trace_meta": trace_meta,
                })
                continue
            content = outbound.content or ""
            if outbound.metadata.get("_suppressed_final") and not content and message_tool_contents:
                content = "\n\n".join(message_tool_contents)
            return _status_from_outbound_metadata(outbound.metadata), content
    finally:
        agent_loop.stop()
        if loop_task is not None:
            loop_task.cancel()
            with suppress(asyncio.CancelledError):
                await loop_task
        await agent_loop.close_mcp()


def main() -> int:
    """CLI entrypoint for the one-shot worker protocol."""
    raw = sys.stdin.readline().strip()
    if not raw:
        _emit({"type": "error", "message": "Missing worker input."})
        return 1
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        _emit({"type": "error", "message": "Invalid worker input."})
        return 1

    user_id = str(payload.get("user_id", "")).strip() or "10001"
    session_id = str(payload.get("session_id", "")).strip()
    message = str(payload.get("message", "")).strip()
    if not session_id or not message:
        _emit({"type": "error", "message": "session_id and message are required."})
        return 1

    raw_prefs = payload.get("composer_prefs")
    composer_prefs = raw_prefs if isinstance(raw_prefs, dict) else None
    raw_turn_context = payload.get("turn_context")
    turn_context = raw_turn_context if isinstance(raw_turn_context, dict) else None
    raw_context = payload.get("context")
    context = raw_context if isinstance(raw_context, dict) else None

    try:
        status, text = asyncio.run(
            _run_once(
                user_id=user_id,
                session_id=session_id,
                message=message,
                composer_prefs=composer_prefs,
                turn_context=turn_context,
                context=context,
            )
        )
    except Exception as exc:
        _emit({"type": "error", "message": str(exc) or "worker failed"})
        return 1

    # Debug only: hold process alive so bwrap state is easier to observe.
    hold_seconds_raw = os.environ.get("NB_HOLD_SECONDS", "0").strip()
    try:
        hold_seconds = max(0.0, float(hold_seconds_raw))
    except ValueError:
        hold_seconds = 0.0
    if hold_seconds > 0:
        time.sleep(hold_seconds)

    _emit({"type": "done", "status": status, "text": text})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
