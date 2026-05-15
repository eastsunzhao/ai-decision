"""Subagent manager for background task execution."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger

from nanobot.agent.hook import AgentHook, AgentHookContext
from nanobot.agent.runner import AgentRunner, AgentRunSpec
from nanobot.agent.skills import BUILTIN_SKILLS_DIR
from nanobot.agent.tools.data_source import DataSourceSearchTool
from nanobot.agent.tools.filesystem import EditFileTool, ListDirTool, ReadFileTool, WriteFileTool
from nanobot.agent.tools.registry import ToolRegistry
from nanobot.agent.tools.search import GlobTool, GrepTool
from nanobot.agent.tools.shell import ExecTool
from nanobot.agent.tools.web import WebFetchTool, WebSearchTool
from nanobot.bus.events import InboundMessage, OutboundMessage
from nanobot.bus.queue import MessageBus
from nanobot.config.schema import ExecToolConfig, WebToolsConfig
from nanobot.providers.base import LLMProvider
from nanobot.utils.helpers import truncate_text
from nanobot.utils.prompt_templates import render_template
from nanobot.utils.runtime import is_blank_text


@dataclass(slots=True)
class SubagentStatus:
    """Real-time status of a running subagent."""

    task_id: str
    label: str
    task_description: str
    started_at: float          # time.monotonic()
    last_activity_at: float    # time.monotonic(); updated when visible progress is published
    phase: str = "initializing"  # initializing | awaiting_tools | tools_completed | final_response | done | error
    iteration: int = 0
    tool_events: list = field(default_factory=list)   # [{name, status, detail}, ...]
    usage: dict = field(default_factory=dict)          # token usage
    stop_reason: str | None = None
    error: str | None = None


class _SubagentHook(AgentHook):
    """Hook for subagent execution — logs tool calls and updates status."""

    def __init__(
        self,
        task_id: str,
        status: SubagentStatus | None = None,
        emit_event: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> None:
        super().__init__()
        self._task_id = task_id
        self._status = status
        self._emit_event = emit_event

    @staticmethod
    def _preview(value: Any, limit: int = 1200) -> str:
        if value is None:
            return ""
        if isinstance(value, str):
            text = value
        else:
            try:
                text = json.dumps(value, ensure_ascii=False, default=str)
            except Exception:
                text = str(value)
        return truncate_text(text, limit)

    async def before_execute_tools(self, context: AgentHookContext) -> None:
        if self._status is not None:
            self._status.phase = "awaiting_tools"
            self._status.iteration = context.iteration
        for tool_call in context.tool_calls:
            args_str = json.dumps(tool_call.arguments, ensure_ascii=False)
            logger.debug(
                "Subagent [{}] executing: {} with arguments: {}",
                self._task_id, tool_call.name, args_str,
            )
            if self._emit_event is not None:
                await self._emit_event(
                    "subagent_tool_start",
                    f"{tool_call.name} started",
                    {
                        "iteration": context.iteration,
                        "tool_call_id": str(getattr(tool_call, "id", "") or ""),
                        "tool_name": str(getattr(tool_call, "name", "") or ""),
                        "phase": "start",
                        "status": "running",
                        "args": getattr(tool_call, "arguments", {}) or {},
                        "args_preview": self._preview(getattr(tool_call, "arguments", {}) or {}, 2000),
                    },
                )

    async def after_iteration(self, context: AgentHookContext) -> None:
        if self._status is None:
            return
        self._status.iteration = context.iteration
        self._status.tool_events = list(context.tool_events)
        self._status.usage = dict(context.usage)
        if context.error:
            self._status.error = str(context.error)
        if context.tool_calls:
            self._status.phase = "tools_completed"
        if self._emit_event is None:
            return
        count = min(len(context.tool_calls), len(context.tool_results), len(context.tool_events))
        for idx in range(count):
            tool_call = context.tool_calls[idx]
            result = context.tool_results[idx]
            event = context.tool_events[idx] if isinstance(context.tool_events[idx], dict) else {}
            ok = event.get("status") == "ok"
            detail = str(event.get("detail") or "")
            text = detail or self._preview(result)
            await self._emit_event(
                "subagent_tool_end",
                text,
                {
                    "iteration": context.iteration,
                    "tool_call_id": str(getattr(tool_call, "id", "") or ""),
                    "tool_name": str(getattr(tool_call, "name", "") or ""),
                    "phase": "end" if ok else "error",
                    "status": "ok" if ok else "error",
                    "result_preview": self._preview(result),
                    "error": "" if ok else (detail or self._preview(result)),
                },
            )


class SubagentManager:
    """Manages background subagent execution."""

    def __init__(
        self,
        provider: LLMProvider,
        workspace: Path,
        bus: MessageBus,
        max_tool_result_chars: int = 50000,
        model: str | None = None,
        web_config: "WebToolsConfig | None" = None,
        exec_config: "ExecToolConfig | None" = None,
        restrict_to_workspace: bool = False,
        disabled_skills: list[str] | None = None,
        exec_working_dir: Path | None = None,
        context_workspace: Path | None = None,
        preferred_data_source: str = "auto",
    ):
        self.provider = provider
        self.workspace = workspace
        self.bus = bus
        self.model = model or provider.get_default_model()
        self.preferred_data_source = str(preferred_data_source or "auto").strip().lower() or "auto"
        self.web_config = web_config or WebToolsConfig()
        self.max_tool_result_chars = max_tool_result_chars
        self.exec_config = exec_config or ExecToolConfig()
        self.exec_working_dir = exec_working_dir or workspace
        self.context_workspace = context_workspace or workspace
        self.restrict_to_workspace = restrict_to_workspace
        self.disabled_skills = set(disabled_skills or [])
        self.runner = AgentRunner(provider)
        self._running_tasks: dict[str, asyncio.Task[None]] = {}
        self._task_statuses: dict[str, SubagentStatus] = {}
        self._session_tasks: dict[str, set[str]] = {}  # session_key -> {task_id, ...}

    def set_provider(self, provider: LLMProvider, model: str) -> None:
        self.provider = provider
        self.model = model
        self.runner.provider = provider

    @staticmethod
    def _classify_result(result) -> str:
        """Collapse runner stop reasons into orchestration-facing states."""
        if result.stop_reason in {"tool_error", "error"}:
            return "failed"
        if result.stop_reason == "completed" and not is_blank_text(result.final_content):
            return "completed"
        return "completed_with_caveats"

    @staticmethod
    def _status_text(status: str, stop_reason: str | None = None) -> str:
        if status == "completed":
            return "completed"
        if status == "completed_with_caveats":
            suffix = f": {stop_reason}" if stop_reason else ""
            return f"completed with caveats{suffix}"
        return "failed"

    async def spawn(
        self,
        task: str,
        label: str | None = None,
        max_iterations: int | None = None,
        origin_channel: str = "cli",
        origin_chat_id: str = "direct",
        session_key: str | None = None,
    ) -> str:
        """Spawn a subagent to execute a task in the background."""
        iteration_cap = max(1, min(100, int(max_iterations or 30)))
        task_id = str(uuid.uuid4())[:8]
        display_label = label or task[:30] + ("..." if len(task) > 30 else "")
        origin = {"channel": origin_channel, "chat_id": origin_chat_id, "session_key": session_key}

        status = SubagentStatus(
            task_id=task_id,
            label=display_label,
            task_description=task,
            started_at=time.monotonic(),
            last_activity_at=time.monotonic(),
        )
        self._task_statuses[task_id] = status

        bg_task = asyncio.create_task(
            self._run_subagent(task_id, task, display_label, origin, status, iteration_cap)
        )
        self._running_tasks[task_id] = bg_task
        if session_key:
            self._session_tasks.setdefault(session_key, set()).add(task_id)

        def _cleanup(_: asyncio.Task) -> None:
            self._running_tasks.pop(task_id, None)
            self._task_statuses.pop(task_id, None)
            if session_key and (ids := self._session_tasks.get(session_key)):
                ids.discard(task_id)
                if not ids:
                    del self._session_tasks[session_key]

        bg_task.add_done_callback(_cleanup)

        logger.info("Spawned subagent [{}]: {}", task_id, display_label)
        await self._publish_progress(
            origin,
            "subagent_start",
            f"Subagent [{display_label}] started.",
            {
                "subagent_id": task_id,
                "label": display_label,
                "phase": status.phase,
                "status": "running",
                "iteration": 0,
                "task_preview": truncate_text(task, 500),
            },
        )
        return f"Subagent [{display_label}] started (id: {task_id}). I'll notify you when it completes."

    async def _publish_progress(
        self,
        origin: dict[str, str],
        event_type: str,
        text: str,
        trace_meta: dict[str, Any],
    ) -> None:
        """Publish subagent progress through the same outbound trace channel as tools."""
        task_id = str(trace_meta.get("subagent_id") or "")
        if task_id and (status := self._task_statuses.get(task_id)) is not None:
            status.last_activity_at = time.monotonic()
        metadata = {
            "_progress": True,
            "_trace_meta": {
                **trace_meta,
                "event_type": event_type,
            },
        }
        await self.bus.publish_outbound(
            OutboundMessage(
                channel=origin.get("channel", "web"),
                chat_id=origin.get("chat_id", "direct"),
                content=text,
                metadata=metadata,
            )
        )

    async def _run_subagent(
        self,
        task_id: str,
        task: str,
        label: str,
        origin: dict[str, str],
        status: SubagentStatus | None = None,
        max_iterations: int = 30,
    ) -> None:
        """Execute the subagent task and announce the result."""
        logger.info("Subagent [{}] starting task: {}", task_id, label)
        if status is None:
            status = SubagentStatus(
                task_id=task_id,
                label=label,
                task_description=task,
                started_at=time.monotonic(),
                last_activity_at=time.monotonic(),
            )

        async def _on_checkpoint(payload: dict) -> None:
            old_phase = status.phase
            status.phase = payload.get("phase", status.phase)
            status.iteration = payload.get("iteration", status.iteration)
            if status.phase != old_phase:
                await self._publish_progress(
                    origin,
                    "subagent_progress",
                    f"Subagent [{label}] phase: {status.phase}",
                    {
                        "subagent_id": task_id,
                        "label": label,
                        "phase": status.phase,
                        "status": "running",
                        "iteration": status.iteration,
                    },
                )

        async def _emit_event(event_type: str, text: str, meta: dict[str, Any]) -> None:
            await self._publish_progress(
                origin,
                event_type,
                text,
                {
                    "subagent_id": task_id,
                    "label": label,
                    "phase": status.phase,
                    "status": meta.get("status", "running"),
                    **meta,
                },
            )

        try:
            # Build subagent tools (no message tool, no spawn tool)
            tools = ToolRegistry()
            allowed_dir = self.workspace if (self.restrict_to_workspace or self.exec_config.sandbox) else None
            extra_read = [BUILTIN_SKILLS_DIR] if allowed_dir else []
            extra_write: list[Path] = []
            if allowed_dir and self.context_workspace != self.workspace:
                extra_read.append(self.context_workspace)
                shared_workspace = self.context_workspace.parent
                extra_read.append(shared_workspace / "permanent")
                extra_write.append(shared_workspace / "permanent")
            tools.register(
                ReadFileTool(
                    workspace=self.workspace,
                    allowed_dir=allowed_dir,
                    extra_allowed_dirs=extra_read or None,
                )
            )
            tools.register(
                WriteFileTool(
                    workspace=self.workspace,
                    allowed_dir=allowed_dir,
                    extra_allowed_dirs=extra_write or None,
                )
            )
            tools.register(
                EditFileTool(
                    workspace=self.workspace,
                    allowed_dir=allowed_dir,
                    extra_allowed_dirs=extra_write or None,
                )
            )
            tools.register(
                ListDirTool(
                    workspace=self.workspace,
                    allowed_dir=allowed_dir,
                    extra_allowed_dirs=extra_read or None,
                )
            )
            tools.register(
                GlobTool(
                    workspace=self.workspace,
                    allowed_dir=allowed_dir,
                    extra_allowed_dirs=extra_read or None,
                )
            )
            tools.register(
                GrepTool(
                    workspace=self.workspace,
                    allowed_dir=allowed_dir,
                    extra_allowed_dirs=extra_read or None,
                )
            )
            if self.exec_config.enable:
                tools.register(ExecTool(
                    working_dir=str(self.exec_working_dir),
                    timeout=self.exec_config.timeout,
                    restrict_to_workspace=self.restrict_to_workspace,
                    sandbox=self.exec_config.sandbox,
                    path_append=self.exec_config.path_append,
                    allowed_env_keys=self.exec_config.allowed_env_keys,
                    tool_call_exec_output_enabled=getattr(
                        self.exec_config,
                        "tool_call_exec_output_enabled",
                        False,
                    ),
                ))
            if self.web_config.enable:
                tools.register(
                    WebSearchTool(
                        config=self.web_config.search,
                        proxy=self.web_config.proxy,
                        user_agent=self.web_config.user_agent,
                    )
                )
                tools.register(
                    WebFetchTool(
                        config=self.web_config.fetch,
                        proxy=self.web_config.proxy,
                        user_agent=self.web_config.user_agent,
                    )
                )
            tools.register(DataSourceSearchTool(workspace=self.workspace))
            system_prompt = self._build_subagent_prompt()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": task},
            ]

            result = await self.runner.run(AgentRunSpec(
                initial_messages=messages,
                tools=tools,
                model=self.model,
                max_iterations=max_iterations,
                soft_iteration_budget=max(1, max_iterations // 2),
                max_tool_result_chars=self.max_tool_result_chars,
                hook=_SubagentHook(task_id, status, _emit_event),
                max_iterations_message="Task completed but no final response was generated.",
                error_message=None,
                fail_on_tool_error=False,
                checkpoint_callback=_on_checkpoint,
            ))
            status.phase = "done"
            status.stop_reason = result.stop_reason

            if result.stop_reason == "tool_error":
                status.tool_events = list(result.tool_events)
                await self._publish_progress(
                    origin,
                    "subagent_error",
                    f"Subagent [{label}] failed.",
                    {
                        "subagent_id": task_id,
                        "label": label,
                        "phase": status.phase,
                        "status": "error",
                        "iteration": status.iteration,
                        "error": self._format_partial_progress(result),
                    },
                )
                await self._announce_result(
                    task_id, label, task,
                    self._format_partial_progress(result),
                    origin, "failed",
                    stop_reason=result.stop_reason,
                    final_response_present=not is_blank_text(result.final_content),
                )
            elif result.stop_reason == "error":
                await self._publish_progress(
                    origin,
                    "subagent_error",
                    f"Subagent [{label}] failed.",
                    {
                        "subagent_id": task_id,
                        "label": label,
                        "phase": status.phase,
                        "status": "error",
                        "iteration": status.iteration,
                        "error": result.error or "Error: subagent execution failed.",
                    },
                )
                await self._announce_result(
                    task_id, label, task,
                    result.error or "Error: subagent execution failed.",
                    origin, "failed",
                    stop_reason=result.stop_reason,
                    final_response_present=not is_blank_text(result.final_content),
                )
            else:
                final_result = result.final_content or "Task completed but no final response was generated."
                announce_status = self._classify_result(result)
                progress_type = (
                    "subagent_done"
                    if announce_status == "completed"
                    else "subagent_caveat"
                )
                progress_text = (
                    f"Subagent [{label}] completed."
                    if announce_status == "completed"
                    else f"Subagent [{label}] completed with caveats ({result.stop_reason})."
                )
                logger.info(
                    "Subagent [{}] finished with status={} stop_reason={}",
                    task_id,
                    announce_status,
                    result.stop_reason,
                )
                await self._publish_progress(
                    origin,
                    progress_type,
                    progress_text,
                    {
                        "subagent_id": task_id,
                        "label": label,
                        "phase": status.phase,
                        "status": announce_status,
                        "stop_reason": result.stop_reason,
                        "final_response_present": not is_blank_text(result.final_content),
                        "iteration": status.iteration,
                        "result_preview": truncate_text(final_result, 1200),
                    },
                )
                await self._announce_result(
                    task_id,
                    label,
                    task,
                    final_result,
                    origin,
                    announce_status,
                    stop_reason=result.stop_reason,
                    final_response_present=not is_blank_text(result.final_content),
                )

        except Exception as e:
            status.phase = "error"
            status.error = str(e)
            logger.error("Subagent [{}] failed: {}", task_id, e)
            await self._publish_progress(
                origin,
                "subagent_error",
                f"Subagent [{label}] failed.",
                {
                    "subagent_id": task_id,
                    "label": label,
                    "phase": status.phase,
                    "status": "error",
                    "iteration": status.iteration,
                    "error": str(e),
                },
            )
            await self._announce_result(
                task_id,
                label,
                task,
                f"Error: {e}",
                origin,
                "failed",
                stop_reason="exception",
                final_response_present=True,
            )

    async def _announce_result(
        self,
        task_id: str,
        label: str,
        task: str,
        result: str,
        origin: dict[str, str],
        status: str,
        *,
        stop_reason: str | None = None,
        final_response_present: bool | None = None,
    ) -> None:
        """Announce the subagent result to the main agent via the message bus."""
        status_text = self._status_text(status, stop_reason)
        if final_response_present is None:
            final_response_present_text = "unknown"
        else:
            final_response_present_text = "yes" if final_response_present else "no"

        announce_content = render_template(
            "agent/subagent_announce.md",
            label=label,
            status_text=status_text,
            status=status,
            stop_reason=stop_reason or "unknown",
            final_response_present=final_response_present_text,
            task=task,
            result=result,
        )

        # Inject as system message to trigger main agent.
        # Use session_key_override to align with the main agent's effective
        # session key (which accounts for unified sessions) so the result is
        # routed to the correct pending queue (mid-turn injection) instead of
        # being dispatched as a competing independent task.
        override = origin.get("session_key") or f"{origin['channel']}:{origin['chat_id']}"
        msg = InboundMessage(
            channel="system",
            sender_id="subagent",
            chat_id=f"{origin['channel']}:{origin['chat_id']}",
            content=announce_content,
            session_key_override=override,
            metadata={
                "injected_event": "subagent_result",
                "subagent_task_id": task_id,
                "subagent_status": status,
                "subagent_stop_reason": stop_reason,
                "subagent_final_response_present": final_response_present,
            },
        )

        await self.bus.publish_inbound(msg)
        logger.debug("Subagent [{}] announced result to {}:{}", task_id, origin['channel'], origin['chat_id'])

    @staticmethod
    def _format_partial_progress(result) -> str:
        completed = [e for e in result.tool_events if e["status"] == "ok"]
        failure = next((e for e in reversed(result.tool_events) if e["status"] == "error"), None)
        lines: list[str] = []
        if completed:
            lines.append("Completed steps:")
            for event in completed[-3:]:
                lines.append(f"- {event['name']}: {event['detail']}")
        if failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {failure['name']}: {failure['detail']}")
        if result.error and not failure:
            if lines:
                lines.append("")
            lines.append("Failure:")
            lines.append(f"- {result.error}")
        return "\n".join(lines) or (result.error or "Error: subagent execution failed.")

    def _build_subagent_prompt(self) -> str:
        """Build a focused system prompt for the subagent."""
        from nanobot.agent.context import ContextBuilder
        from nanobot.agent.skills import SkillsLoader

        time_ctx = ContextBuilder._build_runtime_context(None, None)
        skills_summary = SkillsLoader(
            self.context_workspace,
            disabled_skills=self.disabled_skills,
        ).build_skills_summary()
        return render_template(
            "agent/subagent_system.md",
            time_ctx=time_ctx,
            workspace=str(self.workspace),
            skills_summary=skills_summary or "",
            preferred_data_source=self.preferred_data_source,
        )

    async def cancel_by_session(self, session_key: str) -> int:
        """Cancel all subagents for the given session. Returns count cancelled."""
        tasks = [self._running_tasks[tid] for tid in self._session_tasks.get(session_key, [])
                 if tid in self._running_tasks and not self._running_tasks[tid].done()]
        for t in tasks:
            t.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        return len(tasks)

    def get_running_count(self) -> int:
        """Return the number of currently running subagents."""
        return len(self._running_tasks)

    def get_running_count_by_session(self, session_key: str) -> int:
        """Return the number of currently running subagents for a session."""
        tids = self._session_tasks.get(session_key, set())
        return sum(
            1 for tid in tids
            if tid in self._running_tasks and not self._running_tasks[tid].done()
        )

    def get_running_statuses_by_session(self, session_key: str) -> list[SubagentStatus]:
        """Return status snapshots for currently running subagents in a session."""
        tids = self._session_tasks.get(session_key, set())
        return [
            self._task_statuses[tid]
            for tid in tids
            if tid in self._running_tasks
            and not self._running_tasks[tid].done()
            and tid in self._task_statuses
        ]
