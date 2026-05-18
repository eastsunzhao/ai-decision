"""Minimal Flask app that wraps nanobot with a multi-session browser UI.

This file owns the browser-facing orchestration layer:

- persistent session/chat metadata on disk
- per-request job bookkeeping for streaming responses
- workspace browsing/editing endpoints
- the bridge from HTTP requests into ``web.run_bot.BotRunner``

The heavy agent logic still lives downstream in ``nanobot``; this module mostly
coordinates state and transport for the web product.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from flask import Flask, Response, jsonify, request, send_file

if __package__:
    from .logging_config import configure_logging
    from .composer_prefs import merge_composer_prefs_for_turn
    from .run_bot import BotRunner
    from .runtime_store import (
        DeepSeekTitleGenerator,
        RuntimeStore,
        resolve_generated_title,
        utc_now_iso,
    )
    from .session_workspace import agent_context_path, prepare_session_workspace
    from .turn_context import merge_turn_context_with_legacy
    from .workbench import (
        EDITABLE_TEXT_EXTENSIONS,
        HIDDEN_WORKSPACE_NAMES,
        INLINE_TEXT_SIZE_LIMIT,
        normalize_chat_context,
        normalize_relative_path,
        resolve_workspace_path,
    )
    from .workspace_files import (
        ArtifactSnapshot,
        build_artifact_meta,
        build_file_payload,
        build_workspace_tree_payload,
        copy_destination_path,
        list_workspace_skills,
        operation_payload,
        resolve_downloadable_path,
        resolve_permanent_path,
        resolve_session_temp_path,
        session_temp_root_relative_path,
        snapshot_artifacts,
        unique_child_path,
        valid_workspace_name,
    )
else:
    from logging_config import configure_logging
    from composer_prefs import merge_composer_prefs_for_turn
    from run_bot import BotRunner
    from runtime_store import (
        DeepSeekTitleGenerator,
        RuntimeStore,
        resolve_generated_title,
        utc_now_iso,
    )
    from session_workspace import agent_context_path, prepare_session_workspace
    from turn_context import merge_turn_context_with_legacy
    from workbench import (
        EDITABLE_TEXT_EXTENSIONS,
        HIDDEN_WORKSPACE_NAMES,
        INLINE_TEXT_SIZE_LIMIT,
        normalize_chat_context,
        normalize_relative_path,
        resolve_workspace_path,
    )
    from workspace_files import (
        ArtifactSnapshot,
        build_artifact_meta,
        build_file_payload,
        build_workspace_tree_payload,
        copy_destination_path,
        list_workspace_skills,
        operation_payload,
        resolve_downloadable_path,
        resolve_permanent_path,
        resolve_session_temp_path,
        session_temp_root_relative_path,
        snapshot_artifacts,
        unique_child_path,
        valid_workspace_name,
    )


APP_LOG_FILE = configure_logging(Path(__file__).resolve().parents[1])

# These constants shape how the browser UI stores and streams state.
JOB_TTL = timedelta(minutes=10)
TRACE_EVENT_TYPES = {
    "tool_hint",
    "progress",
    "tool_result",
    "tool_call_start",
    "tool_call_output",
    "tool_call_end",
    "retry_wait",
    "subagent_start",
    "subagent_progress",
    "subagent_tool_start",
    "subagent_tool_end",
    "subagent_done",
    "subagent_caveat",
    "subagent_error",
    "subagent_barrier",
    "context_status",
}
PERSISTED_TRACE_EVENT_TYPES = TRACE_EVENT_TYPES - {"tool_call_output", "context_status"}


def sse_message(event: str, payload: dict[str, Any]) -> str:
    """Encode one Server-Sent Event message for the browser stream endpoint."""
    return f"event: {event}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


@dataclass
class JobState:
    """Mutable in-memory state for one running or recently finished web job."""
    id: str
    user_id: str
    session_id: str
    created_at: datetime
    artifact_snapshot_before: ArtifactSnapshot = field(default_factory=dict)
    event_queue: queue.Queue[tuple[str, dict[str, Any]]] = field(default_factory=queue.Queue)
    trace: list[dict[str, Any]] = field(default_factory=list)
    runner_handle: Any | None = None
    status: str = "running"
    stop_requested: bool = False
    finished_at: datetime | None = None


def create_app(
    *,
    runtime_dir: Path | None = None,
    bot_runner: BotRunner | None = None,
    title_generator: DeepSeekTitleGenerator | None = None,
    max_workers: int = 4,
) -> Flask:
    """Create and configure the Flask app plus its in-memory job registry."""
    app = Flask(__name__, static_folder="static", static_url_path="/static")
    resolved_runtime_dir = (runtime_dir or Path(__file__).resolve().parents[1] / "runtime").resolve(strict=False)
    os.environ["NANOBOT_WEB_RUNTIME_DIR"] = str(resolved_runtime_dir)
    store = RuntimeStore(resolved_runtime_dir)
    runner = bot_runner or BotRunner()
    generator = title_generator or DeepSeekTitleGenerator()
    executor = ThreadPoolExecutor(max_workers=max_workers)
    jobs: dict[str, JobState] = {}
    active_session_jobs: dict[str, str] = {}
    jobs_lock = threading.RLock()

    def scoped_session_key(user_id: str, session_id: str) -> str:
        """Return the key used to enforce one active job per user/session."""
        return f"{user_id}:{session_id}"

    def request_user_id(default: str = "10001") -> str:
        """Extract the effective user id from JSON body or query string."""
        payload = request.get_json(silent=True) or {}
        return (
            str(payload.get("user_id", "")).strip()
            or str(request.args.get("user_id", "")).strip()
            or str(request.args.get("userId", "")).strip()
            or default
        )

    def cleanup_jobs() -> None:
        """Drop finished jobs after a short TTL to bound memory usage."""
        cutoff = datetime.now(timezone.utc) - JOB_TTL
        with jobs_lock:
            expired = [
                job_id
                for job_id, job in jobs.items()
                if job.finished_at is not None and job.finished_at < cutoff
            ]
            for job_id in expired:
                jobs.pop(job_id, None)

    def enqueue(job: JobState, event_type: str, payload: dict[str, Any]) -> None:
        """Push one event into the SSE queue for a job."""
        job.event_queue.put((event_type, payload))

    def make_emit_trace(job: JobState):
        """Build the callback that execution code uses to stream progress."""
        def emit_trace(event_type: str, text: str, trace_meta: dict[str, Any] | None = None) -> None:
            if event_type not in TRACE_EVENT_TYPES:
                return
            if event_type == "context_status":
                payload = trace_meta if isinstance(trace_meta, dict) else {}
                context_status = store.update_context_status(
                    job.session_id,
                    job.user_id,
                    payload,
                    job_id=job.id,
                )
                if context_status is not None:
                    enqueue(job, event_type, context_status)
                return
            item = {
                "type": event_type,
                "text": text,
                "created_at": utc_now_iso(),
            }
            if isinstance(trace_meta, dict):
                item["meta"] = trace_meta
            job.trace.append(item)
            if event_type in PERSISTED_TRACE_EVENT_TYPES and not job.stop_requested:
                store.append_inflight_trace(
                    job.session_id,
                    job.user_id,
                    job_id=job.id,
                    item=item,
                )
            payload = {"text": text, "created_at": item["created_at"]}
            if isinstance(trace_meta, dict):
                payload["meta"] = trace_meta
            enqueue(job, event_type, payload)

        return emit_trace

    def make_emit_assistant(job: JobState):
        """Build the callback that streams assistant text to the browser."""
        def emit_assistant(event_type: str, text: str, stream_meta: dict[str, Any] | None = None) -> None:
            if event_type not in {"assistant_delta", "assistant_end"}:
                return
            payload = {"text": text, "created_at": utc_now_iso()}
            if isinstance(stream_meta, dict):
                payload["meta"] = stream_meta
            if event_type == "assistant_delta" and text and not job.stop_requested:
                store.append_inflight_content(
                    job.session_id,
                    job.user_id,
                    job_id=job.id,
                    delta=text,
                )
            enqueue(job, event_type, payload)

        return emit_assistant

    def build_artifact_meta_for_job(job: JobState) -> dict[str, Any] | None:
        """Best-effort artifact metadata for files changed during one agent turn."""
        try:
            workspace_root = prepare_session_workspace(job.user_id, job.session_id, kind="tool")
            after = snapshot_artifacts(workspace_root, job.session_id)
            return build_artifact_meta(job.artifact_snapshot_before, after)
        except Exception:
            return None

    def run_job(job: JobState, *, user_id: str, session_id: str, message: str) -> None:
        """Background worker entrypoint that executes one queued chat job."""
        try:
            enqueue(job, "start", {"job_id": job.id, "session_id": session_id, "message": "started"})
            outcome = job.runner_handle.run()
            if outcome.status == "stopped" or job.stop_requested:
                store.finalize_inflight_assistant(
                    session_id,
                    user_id,
                    content="",
                    job_id=job.id,
                    trace=[],
                    status="stopped",
                )
                enqueue(job, "done", {"text": "已由用户停止。", "status": "stopped"})
                job.status = "stopped"
                return
            artifact_meta = build_artifact_meta_for_job(job)
            assistant_message = store.finalize_inflight_assistant(
                session_id,
                user_id,
                content=outcome.text,
                job_id=job.id,
                trace=job.trace,
                status=outcome.status,
                meta=artifact_meta,
            )
            done_payload = {"text": outcome.text, "status": outcome.status}
            if isinstance(assistant_message, dict) and isinstance(assistant_message.get("meta"), dict):
                done_payload["meta"] = assistant_message["meta"]
            enqueue(job, "done", done_payload)
            job.status = outcome.status
        except Exception as exc:
            if job.stop_requested:
                store.finalize_inflight_assistant(
                    session_id,
                    user_id,
                    content="",
                    job_id=job.id,
                    trace=[],
                    status="stopped",
                )
                enqueue(job, "done", {"text": "已由用户停止。", "status": "stopped"})
                job.status = "stopped"
                return
            error_message = str(exc) or "nanobot execution failed."
            artifact_meta = build_artifact_meta_for_job(job)
            assistant_message = store.finalize_inflight_assistant(
                session_id,
                user_id,
                content=error_message,
                job_id=job.id,
                trace=job.trace,
                status="error",
                meta=artifact_meta,
            )
            error_payload = {"message": error_message}
            if isinstance(assistant_message, dict) and isinstance(assistant_message.get("meta"), dict):
                error_payload["meta"] = assistant_message["meta"]
            enqueue(job, "error", error_payload)
            job.status = "error"
        finally:
            job.finished_at = datetime.now(timezone.utc)
            with jobs_lock:
                session_key = scoped_session_key(user_id, session_id)
                if active_session_jobs.get(session_key) == job.id:
                    active_session_jobs.pop(session_key, None)

    @app.get("/")
    def index() -> Response:
        """Serve the single-page web UI shell."""
        return app.send_static_file("index.html")

    @app.get("/api/sessions")
    def list_sessions() -> Response:
        """List persisted sessions for the current user."""
        cleanup_jobs()
        return jsonify(store.list_sessions(request_user_id()))

    @app.post("/api/sessions")
    def create_session() -> Response:
        """Create a fresh empty chat session."""
        cleanup_jobs()
        session = store.create_session(request_user_id())
        return jsonify(session)

    @app.delete("/api/sessions/<session_id>")
    def delete_session_route(session_id: str) -> Response:
        """Delete an idle chat session for the current user."""
        cleanup_jobs()
        user_id = request_user_id()
        session = store.get_session(session_id, user_id=user_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        if str(session.get("status", "idle")).lower() == "running":
            return jsonify({"error": "running session cannot be deleted"}), 409
        with jobs_lock:
            if scoped_session_key(user_id, session_id) in active_session_jobs:
                return jsonify({"error": "running session cannot be deleted"}), 409
        try:
            store.delete_session(session_id, user_id)
        except KeyError:
            return jsonify({"error": "session not found"}), 404
        return "", 204

    @app.patch("/api/sessions/<session_id>")
    def patch_session_route(session_id: str) -> Response:
        """Update session title, composer prefs, and/or UI state."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404

        payload = request.get_json(silent=True) or {}
        title: str | None = None
        if "title" in payload:
            title = str(payload.get("title", "")).strip()
            if not title:
                return jsonify({"error": "title is required"}), 400
        composer_prefs_payload: dict[str, Any] | None = None
        if "composer_prefs" in payload:
            cp = payload.get("composer_prefs")
            if not isinstance(cp, dict):
                return jsonify({"error": "composer_prefs must be an object"}), 400
            composer_prefs_payload = cp
        ui_state_payload: dict[str, Any] | None = None
        if "ui_state" in payload:
            raw_ui_state = payload.get("ui_state")
            if not isinstance(raw_ui_state, dict):
                return jsonify({"error": "ui_state must be an object"}), 400
            ui_state_payload = raw_ui_state

        if title is None and composer_prefs_payload is None and ui_state_payload is None:
            return jsonify({"error": "title, composer_prefs, or ui_state is required"}), 400

        try:
            updated = store.patch_session(
                session_id,
                user_id,
                title=title,
                composer_prefs=composer_prefs_payload,
                ui_state=ui_state_payload,
            )
        except KeyError:
            return jsonify({"error": "session not found"}), 404
        except ValueError as exc:
            return jsonify({"error": str(exc)}), 400
        return jsonify(updated)

    @app.get("/api/history/<session_id>")
    def get_history(session_id: str) -> Response:
        """Return persisted chat history for one session."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404
        return jsonify(store.history(session_id, user_id=user_id))

    @app.get("/api/workspace/<session_id>/tree")
    def get_workspace_tree(session_id: str) -> Response:
        """Return permanent and current-session files for the left file pane."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404
        workspace_root = prepare_session_workspace(user_id, session_id, kind="tool")
        return jsonify(build_workspace_tree_payload(workspace_root, session_id))

    @app.post("/api/workspace/<session_id>/upload")
    def post_workspace_upload(session_id: str) -> Response:
        """Upload browser-selected files into the permanent file tree."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404

        workspace_root = prepare_session_workspace(user_id, session_id, kind="tool")
        parent = resolve_permanent_path(workspace_root, request.form.get("target_parent", "permanent"))
        if parent is None:
            return jsonify({"error": "path is not accessible"}), 400
        parent_path = parent[1]
        if not parent_path.exists() or not parent_path.is_dir():
            return jsonify({"error": "target parent is not a directory"}), 400

        uploaded = []
        for storage in request.files.getlist("files"):
            raw_name = Path(str(storage.filename or "").replace("\\", "/")).name
            name = valid_workspace_name(raw_name)
            if name is None:
                return jsonify({"error": "invalid upload filename"}), 400
            destination = unique_child_path(parent_path, name)
            storage.save(destination)
            uploaded.append(
                {
                    "path": destination.relative_to(workspace_root).as_posix(),
                    "kind": "file",
                }
            )
        if not uploaded:
            return jsonify({"error": "no files uploaded"}), 400
        return jsonify({"tree": build_workspace_tree_payload(workspace_root, session_id), "items": uploaded})

    @app.get("/api/workspace/<session_id>/download")
    def get_workspace_download(session_id: str) -> Response:
        """Download one permanent or current-session temp file."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404

        workspace_root = prepare_session_workspace(user_id, session_id, kind="tool")
        target = resolve_downloadable_path(workspace_root, session_id, request.args.get("path", ""))
        if target is None:
            return jsonify({"error": "path is not accessible"}), 400
        target_path = target[1]
        if not target_path.exists() or not target_path.is_file():
            return jsonify({"error": "file not found"}), 404
        return send_file(target_path, as_attachment=True, download_name=target_path.name)

    @app.post("/api/workspace/<session_id>/fs")
    def post_workspace_fs(session_id: str) -> Response:
        """Run permanent-file operations from the browser file pane."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        operation = str(payload.get("operation", "")).strip().lower()
        if operation not in {"copy", "create_file", "create_dir", "rename", "delete", "move", "persist_temp"}:
            return jsonify({"error": "unsupported operation"}), 400

        workspace_root = prepare_session_workspace(user_id, session_id, kind="tool")
        if operation in {"create_file", "create_dir"}:
            parent = resolve_permanent_path(workspace_root, payload.get("parent", "permanent"))
            name = valid_workspace_name(payload.get("name", ""))
            if parent is None or name is None:
                return jsonify({"error": "path is not accessible"}), 400
            parent_path = parent[1]
            if not parent_path.exists() or not parent_path.is_dir():
                return jsonify({"error": "parent is not a directory"}), 400
            destination = unique_child_path(parent_path, name)
            if operation == "create_dir":
                destination.mkdir()
                return jsonify(operation_payload(workspace_root, session_id, destination, "directory"))
            destination.write_text("", encoding="utf-8")
            return jsonify(operation_payload(workspace_root, session_id, destination, "file"))

        if operation == "rename":
            source = resolve_permanent_path(workspace_root, payload.get("path", ""))
            name = valid_workspace_name(payload.get("name", ""))
            if source is None or name is None:
                return jsonify({"error": "path is not accessible"}), 400
            source_normalized, source_path = source
            if source_normalized == "permanent":
                return jsonify({"error": "cannot rename permanent root"}), 400
            if not source_path.exists():
                return jsonify({"error": "source not found"}), 404
            destination = unique_child_path(source_path.parent, name)
            source_path.rename(destination)
            return jsonify(
                {
                    **operation_payload(
                        workspace_root,
                        session_id,
                        destination,
                        "directory" if destination.is_dir() else "file",
                    ),
                    "old_path": source_normalized,
                }
            )

        if operation == "delete":
            target = resolve_permanent_path(workspace_root, payload.get("path", ""))
            if target is None:
                return jsonify({"error": "path is not accessible"}), 400
            target_normalized, target_path = target
            if target_normalized == "permanent":
                return jsonify({"error": "cannot delete permanent root"}), 400
            if not target_path.exists():
                return jsonify({"error": "target not found"}), 404
            target_kind = "directory" if target_path.is_dir() else "file"
            if target_path.is_dir():
                if payload.get("recursive") is not True:
                    return jsonify({"error": "recursive delete is required for directories"}), 400
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
            return jsonify(
                {
                    "tree": build_workspace_tree_payload(workspace_root, session_id),
                    "path": target_normalized,
                    "kind": target_kind,
                }
            )

        if operation == "move":
            source = resolve_permanent_path(workspace_root, payload.get("source", ""))
            target_parent = resolve_permanent_path(workspace_root, payload.get("target_parent", "permanent"))
            if source is None or target_parent is None:
                return jsonify({"error": "path is not accessible"}), 400
            source_normalized, source_path = source
            target_parent_path = target_parent[1]
            if source_normalized == "permanent":
                return jsonify({"error": "cannot move permanent root"}), 400
            if not source_path.exists():
                return jsonify({"error": "source not found"}), 404
            if not target_parent_path.exists() or not target_parent_path.is_dir():
                return jsonify({"error": "target parent is not a directory"}), 400
            if source_path.parent == target_parent_path:
                return jsonify({"error": "source is already in target directory"}), 400
            try:
                target_parent_path.relative_to(source_path)
            except ValueError:
                pass
            else:
                return jsonify({"error": "cannot move a directory into itself"}), 400
            destination = unique_child_path(target_parent_path, source_path.name)
            shutil.move(str(source_path), str(destination))
            return jsonify(
                {
                    **operation_payload(
                        workspace_root,
                        session_id,
                        destination,
                        "directory" if destination.is_dir() else "file",
                    ),
                    "old_path": source_normalized,
                }
            )

        if operation == "persist_temp":
            source = resolve_session_temp_path(workspace_root, session_id, payload.get("source", ""))
            if source is None:
                return jsonify({"error": "path is not accessible"}), 400
            source_normalized, source_path = source
            if source_normalized == session_temp_root_relative_path(session_id):
                return jsonify({"error": "cannot persist temp root"}), 400
            if not source_path.exists():
                return jsonify({"error": "source not found"}), 404
            permanent_root = workspace_root / "permanent"
            destination = copy_destination_path(permanent_root, source_path)
            if source_path.is_dir():
                shutil.copytree(source_path, destination, symlinks=True)
                copied_kind = "directory"
            else:
                shutil.copy2(source_path, destination)
                copied_kind = "file"
            return jsonify(operation_payload(workspace_root, session_id, destination, copied_kind))

        source = resolve_permanent_path(workspace_root, payload.get("source", ""))
        target_parent = resolve_permanent_path(workspace_root, payload.get("target_parent", "permanent"))
        if source is None or target_parent is None:
            return jsonify({"error": "path is not accessible"}), 400

        source_normalized, source_path = source
        target_parent_path = target_parent[1]
        if source_normalized == "permanent":
            return jsonify({"error": "cannot copy permanent root"}), 400
        if not source_path.exists():
            return jsonify({"error": "source not found"}), 404
        if not target_parent_path.exists() or not target_parent_path.is_dir():
            return jsonify({"error": "target parent is not a directory"}), 400
        try:
            target_parent_path.relative_to(source_path)
        except ValueError:
            pass
        else:
            return jsonify({"error": "cannot copy a directory into itself"}), 400

        destination = copy_destination_path(target_parent_path, source_path)
        if source_path.is_dir():
            shutil.copytree(source_path, destination, symlinks=True)
            copied_kind = "directory"
        else:
            shutil.copy2(source_path, destination)
            copied_kind = "file"

        return jsonify(operation_payload(workspace_root, session_id, destination, copied_kind))

    @app.get("/api/workspace/<session_id>/skills")
    def get_workspace_skills(session_id: str) -> Response:
        """Return available skills in the current session workspace."""
        cleanup_jobs()
        user_id = request_user_id()
        session = store.get_session(session_id, user_id=user_id)
        if session is None:
            return jsonify({"error": "session not found"}), 404
        prepare_session_workspace(user_id, session_id, kind="context")
        workspace_root = agent_context_path(user_id)
        return jsonify(
            {
                "items": list_workspace_skills(workspace_root, path_prefix="_agent"),
                "active_skill": session.get("ui_state", {}).get("active_skill", ""),
            }
        )

    @app.get("/api/workspace/<session_id>/file")
    def get_workspace_file(session_id: str) -> Response:
        """Read one workspace file for the center editor/viewer pane."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404
        relative_path = request.args.get("path", "")
        workspace_root = prepare_session_workspace(user_id, session_id, kind="tool")
        try:
            payload = build_file_payload(workspace_root, relative_path)
        except FileNotFoundError:
            return jsonify({"error": "file not found"}), 404
        except PermissionError:
            return jsonify({"error": "path is not accessible"}), 400
        return jsonify(payload)

    @app.put("/api/workspace/<session_id>/file")
    def put_workspace_file(session_id: str) -> Response:
        """Write one editable workspace file back from the browser."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404
        payload = request.get_json(silent=True) or {}
        relative_path = payload.get("path", "")
        content = payload.get("content", "")
        if not isinstance(content, str):
            return jsonify({"error": "content must be a string"}), 400

        workspace_root = prepare_session_workspace(user_id, session_id, kind="tool")
        resolved = resolve_workspace_path(workspace_root, relative_path)
        normalized_path = normalize_relative_path(relative_path)
        if resolved is None or not normalized_path:
            return jsonify({"error": "invalid path"}), 400
        if resolved.name in HIDDEN_WORKSPACE_NAMES:
            return jsonify({"error": "path is not writable"}), 400
        if resolved.suffix.lower() not in EDITABLE_TEXT_EXTENSIONS:
            return jsonify({"error": "file type is not editable"}), 400
        encoded = content.encode("utf-8")
        if len(encoded) > INLINE_TEXT_SIZE_LIMIT:
            return jsonify({"error": "file is too large to save from the web UI"}), 400

        resolved.parent.mkdir(parents=True, exist_ok=True)
        resolved.write_text(content, encoding="utf-8")
        return jsonify(build_file_payload(workspace_root, normalized_path))

    @app.post("/api/sessions/<session_id>/stop")
    def stop_session_job(session_id: str) -> Response:
        """Request cooperative cancellation of the active session job."""
        cleanup_jobs()
        user_id = request_user_id()
        if store.get_session(session_id, user_id=user_id) is None:
            return jsonify({"error": "session not found"}), 404

        with jobs_lock:
            session_key = scoped_session_key(user_id, session_id)
            active_job_id = active_session_jobs.get(session_key)
            job = jobs.get(active_job_id) if active_job_id else None
            if job is None or job.finished_at is not None or job.runner_handle is None:
                if store.clear_stale_running_session(session_id, user_id):
                    return jsonify({"stop_requested": False, "message": "已清除过期运行状态。"})
                return jsonify({"stop_requested": False, "message": "没有可停止的活动运行。"})
            if job.user_id != user_id or job.session_id != session_id:
                if store.clear_stale_running_session(session_id, user_id):
                    return jsonify({"stop_requested": False, "message": "已清除过期运行状态。"})
                return jsonify({"stop_requested": False, "message": "没有可停止的活动运行。"})
            stop_requested = bool(job.runner_handle.stop())
            if stop_requested:
                job.stop_requested = True

        if not stop_requested:
            return jsonify({"stop_requested": False, "message": "没有可停止的活动运行。"})
        store.discard_inflight_assistant(session_id, user_id, job_id=job.id)
        return jsonify({"stop_requested": True, "job_id": job.id, "message": "已请求停止。"})

    @app.post("/api/chat")
    def create_chat_job() -> Response:
        """Create a background job for one user message.

        The request thread persists the user turn and allocates the job; the
        actual agent run happens on the thread pool so the browser can attach to
        the SSE stream immediately.
        """
        cleanup_jobs()
        payload = request.get_json(silent=True) or {}
        user_id = request_user_id()
        session_id = str(payload.get("session_id", "")).strip()
        message = str(payload.get("message", "")).strip()

        if not session_id:
            return jsonify({"error": "session_id is required"}), 400
        if not message:
            return jsonify({"error": "message is required"}), 400
        session_row = store.get_session(session_id, user_id=user_id)
        if session_row is None:
            return jsonify({"error": "session not found"}), 404

        stored_prefs = session_row.get("composer_prefs") or {}
        request_prefs = payload.get("composer_prefs")
        request_prefs_dict = request_prefs if isinstance(request_prefs, dict) else None
        merged_prefs = merge_composer_prefs_for_turn(
            stored_prefs if isinstance(stored_prefs, dict) else {},
            request_prefs_dict,
        )
        tool_workspace = prepare_session_workspace(user_id, session_id, kind="tool")
        turn_context = merge_turn_context_with_legacy(
            payload.get("turn_context"),
            request_prefs_dict,
            workspace_root=tool_workspace,
        )
        try:
            artifact_snapshot_before = snapshot_artifacts(tool_workspace, session_id)
        except Exception:
            artifact_snapshot_before = {}
        session_ui_state = session_row.get("ui_state") if isinstance(session_row.get("ui_state"), dict) else {}
        chat_context = normalize_chat_context(
            payload.get("context"),
            composer_prefs=merged_prefs,
            ui_state=session_ui_state,
        )

        existing_history = store.history(session_id, user_id=user_id)
        first_user_turn = not any(item.get("role") == "user" for item in existing_history)
        generated_title = None
        if first_user_turn:
            try:
                model_title = generator.generate(message)
            except Exception:
                model_title = None
            generated_title = resolve_generated_title(message, model_title)

        with jobs_lock:
            session_key = scoped_session_key(user_id, session_id)
            active_job_id = active_session_jobs.get(session_key)
            if active_job_id and jobs.get(active_job_id, None) and jobs[active_job_id].finished_at is None:
                return jsonify({"error": "session already has a running job"}), 409

            job = JobState(
                id=uuid.uuid4().hex,
                user_id=user_id,
                session_id=session_id,
                created_at=datetime.now(timezone.utc),
                artifact_snapshot_before=artifact_snapshot_before,
            )
            job.runner_handle = runner.start(
                user_id=user_id,
                session_id=session_id,
                message=message,
                emit_trace=make_emit_trace(job),
                emit_assistant=make_emit_assistant(job),
                composer_prefs=merged_prefs,
                turn_context=turn_context,
                context=chat_context,
            )
            jobs[job.id] = job
            active_session_jobs[session_key] = job.id

        store.append_user_message(
            session_id,
            user_id,
            content=message,
            job_id=job.id,
            title=generated_title,
        )
        assistant_message = store.create_inflight_assistant(session_id, user_id, job_id=job.id)

        try:
            executor.submit(run_job, job, user_id=user_id, session_id=session_id, message=message)
        except Exception:
            with jobs_lock:
                active_session_jobs.pop(scoped_session_key(user_id, session_id), None)
                jobs.pop(job.id, None)
            store.discard_inflight_assistant(session_id, user_id, job_id=job.id)
            store.set_session_status(session_id, user_id, "idle")
            raise

        session = store.get_session(session_id, user_id=user_id)
        return jsonify({
            "job_id": job.id,
            "assistant_message_id": assistant_message["id"],
            "title": session["title"] if session else generated_title,
        })

    @app.get("/api/stream/<job_id>")
    def stream_job(job_id: str) -> Response:
        """Stream job progress and completion events as SSE."""
        cleanup_jobs()
        user_id = request_user_id()
        session_id = str(request.args.get("session_id", "")).strip()
        with jobs_lock:
            job = jobs.get(job_id)
        if job is None:
            return jsonify({"error": "job not found"}), 404
        if job.user_id != user_id:
            return jsonify({"error": "job not found"}), 404
        if session_id and job.session_id != session_id:
            return jsonify({"error": "job not found"}), 404

        def generate():
            """Yield queued SSE events until the job finishes."""
            while True:
                try:
                    event_type, payload = job.event_queue.get(timeout=15)
                except queue.Empty:
                    yield ": keep-alive\n\n"
                    if job.finished_at is not None:
                        break
                    continue

                yield sse_message(event_type, payload)
                if event_type in {"done", "error"}:
                    break

        headers = {
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        }
        return Response(generate(), mimetype="text/event-stream", headers=headers)

    app.config["RUNTIME_STORE"] = store
    app.config["BOT_RUNNER"] = runner
    app.config["TITLE_GENERATOR"] = generator
    app.config["JOB_EXECUTOR"] = executor
    return app


app = create_app()


if __name__ == "__main__":
    host = os.environ.get("WEB_HOST", "127.0.0.1")
    port = int(os.environ.get("WEB_PORT", "5005"))
    app.run(host=host, port=port, threaded=True, debug=False)
