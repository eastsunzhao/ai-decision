"""Persistent runtime state for the web UI.

This module owns the disk-backed session index and chat transcripts. Keeping it
outside ``web.app`` lets the Flask module focus on HTTP routing and streaming.
"""

from __future__ import annotations

import json
import re
import shutil
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request

if __package__:
    from .composer_prefs import normalize_composer_prefs
    from .session_workspace import prepare_session_workspace
    from .workbench import normalize_ui_state
else:
    from composer_prefs import normalize_composer_prefs
    from session_workspace import prepare_session_workspace
    from workbench import normalize_ui_state


DEFAULT_SESSION_TITLE = "New Chat"
CHAT_PREVIEW_LENGTH = 24
INFLIGHT_STALE_TTL = timedelta(days=1)


def utc_now_iso() -> str:
    """Return a UTC timestamp in a JSON-friendly format."""
    return datetime.now(timezone.utc).isoformat()


def derive_session_title(content: str) -> str:
    """Fallback title generation from the first user message."""
    normalized = " ".join(content.split())
    if not normalized:
        return DEFAULT_SESSION_TITLE
    if len(normalized) <= CHAT_PREVIEW_LENGTH:
        return normalized
    return normalized[:CHAT_PREVIEW_LENGTH] + "..."


def normalize_title(title: str) -> str:
    """Collapse whitespace and cap title length before persisting it."""
    collapsed = " ".join((title or "").replace("\n", " ").split()).strip().strip("\"'`")
    if not collapsed:
        return DEFAULT_SESSION_TITLE
    return collapsed[:60]


def is_default_session_title(title: Any) -> bool:
    """Return whether a session still has the placeholder title."""
    return normalize_title(str(title or "")) == DEFAULT_SESSION_TITLE


def resolve_generated_title(message: str, generated: str | None) -> str:
    """Use the model title only when it is meaningfully different from the placeholder."""
    title = normalize_title(generated or "")
    if is_default_session_title(title):
        return derive_session_title(message)
    return title


def _has_distinct_streamed_content(streamed: Any, final: Any) -> bool:
    """Return whether a streamed placeholder should be preserved separately."""
    streamed_text = str(streamed or "").strip()
    final_text = str(final or "").strip()
    return bool(streamed_text and final_text and streamed_text != final_text)


def _ui_state_default(composer_prefs: dict[str, Any] | None = None) -> dict[str, str]:
    """Build the default normalized UI state for a fresh session."""
    return normalize_ui_state({}, composer_prefs=composer_prefs)


class DeepSeekTitleGenerator:
    """Generate a concise title from the first user message using DeepSeek."""

    def generate(self, message: str) -> str | None:
        """Try to get a concise first-message title from DeepSeek."""
        try:
            from nanobot.config.loader import load_config
        except Exception:
            return None

        config = load_config()
        provider_cfg = getattr(config.providers, "deepseek", None)
        api_key = getattr(provider_cfg, "api_key", "") if provider_cfg else ""
        api_base = getattr(provider_cfg, "api_base", "") if provider_cfg else ""
        if not api_key:
            return None

        endpoint = (api_base or "https://api.deepseek.com").rstrip("/") + "/chat/completions"
        body = {
            "model": "deepseek-v4-flash",
            "reasoning_effort": "medium",
            "extra_body": {"thinking": {"type": "enabled"}},
            "temperature": 0.2,
            "max_tokens": 32,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Write a short chat session title based on the user's first message. "
                        "Return only the title. Keep it under 8 words. No quotes."
                    ),
                },
                {"role": "user", "content": message},
            ],
        }
        req = urllib_request.Request(
            endpoint,
            data=json.dumps(body).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with urllib_request.urlopen(req, timeout=6) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (TimeoutError, urllib_error.URLError, urllib_error.HTTPError, json.JSONDecodeError):
            return None

        try:
            content = payload["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            return None
        return normalize_title(content)


class RuntimeStore:
    """Persist session metadata and chat histories on disk.

    ``RuntimeStore`` is deliberately boring: it stores plain JSON in the runtime
    directory so the web app can restart without losing chat/session state.
    """

    def __init__(self, runtime_dir: Path):
        """Initialize runtime directories and migrate legacy chat locations."""
        self.runtime_dir = runtime_dir
        self.chats_dir = self.runtime_dir / "chats"
        self.users_dir = self.runtime_dir / "users"
        self.legacy_workspaces_dir = self.runtime_dir / "workspaces"
        self._lock = threading.RLock()
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        self.chats_dir.mkdir(parents=True, exist_ok=True)
        self.users_dir.mkdir(parents=True, exist_ok=True)
        self._migrate_legacy_workspace_paths(default_user_id="10001")
        self._migrate_legacy_chat_paths(default_user_id="10001")
        self._clear_orphaned_inflight()

    def _read_json(self, path: Path, default: Any) -> Any:
        """Read JSON from disk, returning ``default`` when the file is missing."""
        if not path.exists():
            return default
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)

    def _write_json(self, path: Path, payload: Any) -> None:
        """Persist JSON atomically enough for this lightweight local runtime."""
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)

    def _normalize_context_status(self, payload: dict[str, Any]) -> dict[str, Any] | None:
        """Return a compact, browser-safe context usage payload."""
        def safe_int(value: Any) -> int:
            try:
                return max(0, int(value or 0))
            except (TypeError, ValueError):
                return 0

        try:
            prompt_tokens = max(0, int(payload.get("prompt_tokens", 0) or 0))
            context_window_tokens = max(0, int(payload.get("context_window_tokens", 0) or 0))
        except (TypeError, ValueError):
            return None
        if prompt_tokens <= 0 or context_window_tokens <= 0:
            return None
        remaining_tokens = safe_int(payload.get("remaining_tokens", context_window_tokens - prompt_tokens))
        percent_used = min(100.0, round((prompt_tokens / context_window_tokens) * 100, 1))
        return {
            "prompt_tokens": prompt_tokens,
            "context_window_tokens": context_window_tokens,
            "remaining_tokens": remaining_tokens,
            "percent_used": percent_used,
            "source": str(payload.get("source") or "estimate"),
            "updated_at": utc_now_iso(),
            "iteration": safe_int(payload.get("iteration")),
            "completion_tokens": safe_int(payload.get("completion_tokens")),
            "max_completion_tokens": safe_int(payload.get("max_completion_tokens")),
        }

    @staticmethod
    def _safe_segment(value: str, fallback: str) -> str:
        """Sanitize user-controlled strings before turning them into directories."""
        cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("._")
        return cleaned or fallback

    def _session_workspace(self, user_id: str, session_id: str) -> Path:
        """Return the session temp directory."""
        return prepare_session_workspace(user_id, session_id, kind="temp")

    def _user_workspace(self, user_id: str) -> Path:
        """Return the shared workspace directory for one user."""
        return prepare_session_workspace(user_id, "default_session", kind="tool")

    def _user_root(self, user_id: str) -> Path:
        """Return the root runtime directory for one user."""
        safe_user = self._safe_segment(user_id, "10001")
        return self.users_dir / safe_user

    def _sessions_index_path(self, user_id: str) -> Path:
        """Return the per-user sessions index file."""
        return self._user_root(user_id) / "sessions.json"

    def _chat_path(self, user_id: str, session_id: str) -> Path:
        """Return the persisted chat transcript path for one session."""
        safe_session = self._safe_segment(session_id, "default_session")
        return self._user_root(user_id) / "sessions" / safe_session / "chat.json"

    def _inflight_dir(self, user_id: str, session_id: str) -> Path:
        """Return the sidecar directory for one session's running turns."""
        safe_session = self._safe_segment(session_id, "default_session")
        return self._user_root(user_id) / "sessions" / safe_session / "inflight"

    def _inflight_path(self, user_id: str, session_id: str, job_id: str) -> Path:
        """Return the sidecar path for a running assistant turn."""
        safe_job = self._safe_segment(job_id, "job")
        return self._inflight_dir(user_id, session_id) / f"{safe_job}.json"

    def _load_sessions(self, user_id: str) -> list[dict[str, Any]]:
        """Load all session rows for one user."""
        return self._read_json(self._sessions_index_path(user_id), [])

    def _load_all_sessions(self) -> list[dict[str, Any]]:
        """Load sessions across all users, mainly for startup/migration flows."""
        sessions: list[dict[str, Any]] = []
        for path in self.users_dir.glob("*/sessions.json"):
            data = self._read_json(path, [])
            if isinstance(data, list):
                sessions.extend(data)
        return sessions

    def _save_sessions(self, user_id: str, sessions: list[dict[str, Any]]) -> None:
        """Persist the per-user sessions index."""
        self._write_json(self._sessions_index_path(user_id), sessions)

    def _migrate_legacy_workspace_paths(self, default_user_id: str) -> None:
        """Move old runtime/workspaces user/session state into runtime/users."""
        legacy_root = self.legacy_workspaces_dir
        if not legacy_root.exists():
            return
        with self._lock:
            for legacy_user_dir in legacy_root.iterdir():
                if not legacy_user_dir.is_dir():
                    continue
                user_id = str(legacy_user_dir.name).strip() or default_user_id
                sessions_path = legacy_user_dir / "sessions.json"
                if sessions_path.exists():
                    target_sessions = self._sessions_index_path(user_id)
                    if not target_sessions.exists():
                        target_sessions.parent.mkdir(parents=True, exist_ok=True)
                        sessions_path.replace(target_sessions)
                for session_dir in legacy_user_dir.iterdir():
                    if not session_dir.is_dir():
                        continue
                    session_id = session_dir.name
                    chat_path = session_dir / "chat.json"
                    if chat_path.exists():
                        target_chat = self._chat_path(user_id, session_id)
                        if not target_chat.exists():
                            target_chat.parent.mkdir(parents=True, exist_ok=True)
                            chat_path.replace(target_chat)
                    temp_dir = prepare_session_workspace(user_id, session_id, kind="temp")
                    for item in list(session_dir.iterdir()):
                        if item.name == "chat.json":
                            continue
                        target = temp_dir / item.name
                        if target.exists():
                            continue
                        item.replace(target)

    def _migrate_legacy_chat_paths(self, default_user_id: str) -> None:
        """Move legacy runtime/chats files into session workspaces."""
        with self._lock:
            sessions = self._load_all_sessions()
            session_user = {
                item["id"]: str(item.get("user_id", default_user_id)).strip() or default_user_id
                for item in sessions
            }

            for user_dir in self.chats_dir.iterdir():
                if not user_dir.is_dir():
                    continue
                legacy_user_id = str(user_dir.name).strip() or default_user_id
                for legacy_path in user_dir.glob("*.json"):
                    session_id = legacy_path.stem
                    target = self._chat_path(legacy_user_id, session_id)
                    if target.exists():
                        legacy_path.unlink(missing_ok=True)
                        continue
                    target.parent.mkdir(parents=True, exist_ok=True)
                    legacy_path.replace(target)

            for legacy_path in self.chats_dir.glob("*.json"):
                session_id = legacy_path.stem
                user_id = session_user.get(session_id, default_user_id)
                target = self._chat_path(user_id, session_id)
                if target.exists():
                    legacy_path.unlink(missing_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                legacy_path.replace(target)

    def _clear_orphaned_inflight(self) -> None:
        """Drop running-turn sidecars from previous web processes."""
        with self._lock:
            for inflight_dir in self.users_dir.glob("*/sessions/*/inflight"):
                if inflight_dir.is_dir():
                    shutil.rmtree(inflight_dir, ignore_errors=True)
            for sessions_path in self.users_dir.glob("*/sessions.json"):
                sessions = self._read_json(sessions_path, [])
                if not isinstance(sessions, list):
                    continue
                changed = False
                for session in sessions:
                    if isinstance(session, dict) and str(session.get("status", "")).lower() == "running":
                        session["status"] = "idle"
                        changed = True
                if changed:
                    self._write_json(sessions_path, sessions)

    def list_sessions(self, user_id: str | None = None) -> list[dict[str, Any]]:
        """Return session rows ordered by recent activity."""
        with self._lock:
            sessions = self._load_sessions(user_id) if user_id else self._load_all_sessions()
            if user_id:
                self._backfill_default_titles(user_id, sessions)
            ordered = sorted(sessions, key=lambda item: item.get("updated_at", ""), reverse=True)
            return [self._with_message_count(s) for s in ordered]

    def _backfill_default_titles(self, user_id: str, sessions: list[dict[str, Any]]) -> None:
        """Recover titles for sessions that still have the placeholder."""
        changed = False
        for session in sessions:
            if not isinstance(session, dict) or not is_default_session_title(session.get("title")):
                continue
            session_id = str(session.get("id", "")).strip()
            if not session_id:
                continue
            messages = self._read_json(self._chat_path(user_id, session_id), [])
            if not isinstance(messages, list):
                continue
            first_user = next((item for item in messages if item.get("role") == "user"), None)
            if not first_user:
                continue
            title = derive_session_title(str(first_user.get("content", "")))
            if is_default_session_title(title):
                continue
            session["title"] = title
            changed = True
        if changed:
            self._save_sessions(user_id, sessions)

    def get_session(self, session_id: str, user_id: str | None = None) -> dict[str, Any] | None:
        """Look up one session, optionally enforcing user ownership."""
        with self._lock:
            sessions = self._load_sessions(user_id) if user_id else self._load_all_sessions()
            for session in sessions:
                if session["id"] != session_id:
                    continue
                if user_id is not None and session.get("user_id", "10001") != user_id:
                    continue
                return self._with_message_count(session)
            return None

    def create_session(self, user_id: str, title: str = DEFAULT_SESSION_TITLE) -> dict[str, Any]:
        """Create a new session row, transcript file, and workspace directory."""
        with self._lock:
            now = utc_now_iso()
            clean_user_id = str(user_id).strip() or "10001"
            composer_prefs = normalize_composer_prefs({})
            session = {
                "id": uuid.uuid4().hex,
                "user_id": clean_user_id,
                "title": normalize_title(title or DEFAULT_SESSION_TITLE),
                "created_at": now,
                "updated_at": now,
                "status": "idle",
                "composer_prefs": composer_prefs,
                "ui_state": _ui_state_default(composer_prefs),
            }
            sessions = self._load_sessions(clean_user_id)
            sessions.append(session)
            self._save_sessions(clean_user_id, sessions)
            self._write_json(self._chat_path(clean_user_id, session["id"]), [])
            prepare_session_workspace(clean_user_id, session["id"], kind="temp")
            return self._with_message_count(session)

    def history(self, session_id: str, user_id: str | None = None) -> list[dict[str, Any]]:
        """Return the persisted message history for one session."""
        with self._lock:
            session = self.get_session(session_id, user_id=user_id)
            if session is None:
                raise KeyError(session_id)
            resolved_user_id = str(session.get("user_id", "10001"))
            messages = self._read_json(self._chat_path(resolved_user_id, session_id), [])
            if not isinstance(messages, list):
                messages = []
            if str(session.get("status", "idle")).lower() == "running":
                messages = [*messages, *self._read_inflight_messages(resolved_user_id, session_id)]
            return messages

    def append_user_message(
        self,
        session_id: str,
        user_id: str,
        *,
        content: str,
        job_id: str,
        title: str | None = None,
    ) -> dict[str, Any]:
        """Append the user turn and mark the session as running."""
        with self._lock:
            messages = self._read_json(self._chat_path(user_id, session_id), [])
            first_user_message = not any(item.get("role") == "user" for item in messages)
            session = self.get_session(session_id, user_id=user_id)
            should_update_title = first_user_message or (
                session is not None and is_default_session_title(session.get("title"))
            )
            message = {
                "id": uuid.uuid4().hex,
                "role": "user",
                "content": content,
                "created_at": utc_now_iso(),
                "status": "complete",
                "job_id": job_id,
            }
            messages.append(message)
            self._write_json(self._chat_path(user_id, session_id), messages)
            self._update_session(
                session_id,
                user_id=user_id,
                updated_at=message["created_at"],
                status="running",
                title=resolve_generated_title(content, title) if should_update_title else None,
            )
            return message

    def create_inflight_assistant(self, session_id: str, user_id: str, *, job_id: str) -> dict[str, Any]:
        """Create the sidecar assistant placeholder for a running turn."""
        with self._lock:
            now = utc_now_iso()
            message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": "",
                "created_at": now,
                "updated_at": now,
                "status": "running",
                "job_id": job_id,
                "trace": [],
            }
            self._write_json(self._inflight_path(user_id, session_id, job_id), message)
            return dict(message)

    def append_inflight_trace(
        self,
        session_id: str,
        user_id: str,
        *,
        job_id: str,
        item: dict[str, Any],
    ) -> None:
        """Append one stable checkpoint to the running-turn sidecar."""
        with self._lock:
            path = self._inflight_path(user_id, session_id, job_id)
            message = self._read_json(path, None)
            if not isinstance(message, dict):
                return
            trace = message.get("trace")
            if not isinstance(trace, list):
                trace = []
            trace.append(item)
            message["trace"] = trace
            message["updated_at"] = utc_now_iso()
            self._write_json(path, message)

    def append_inflight_content(
        self,
        session_id: str,
        user_id: str,
        *,
        job_id: str,
        delta: str,
    ) -> None:
        """Append streamed assistant text to the running-turn sidecar."""
        if not delta:
            return
        with self._lock:
            path = self._inflight_path(user_id, session_id, job_id)
            message = self._read_json(path, None)
            if not isinstance(message, dict):
                return
            current = message.get("content")
            message["content"] = f"{current if isinstance(current, str) else ''}{delta}"
            message["updated_at"] = utc_now_iso()
            self._write_json(path, message)

    def update_context_status(
        self,
        session_id: str,
        user_id: str,
        payload: dict[str, Any],
        *,
        job_id: str | None = None,
    ) -> dict[str, Any] | None:
        """Persist latest context usage on the session and running assistant."""
        context_status = self._normalize_context_status(payload)
        if context_status is None:
            return None
        with self._lock:
            sessions = self._load_sessions(user_id)
            for session in sessions:
                if session["id"] == session_id and session.get("user_id", "10001") == user_id:
                    session["context_status"] = context_status
                    self._save_sessions(user_id, sessions)
                    break
            if job_id:
                path = self._inflight_path(user_id, session_id, job_id)
                message = self._read_json(path, None)
                if isinstance(message, dict):
                    message["context_status"] = context_status
                    message["updated_at"] = context_status["updated_at"]
                    self._write_json(path, message)
        return context_status

    def finalize_inflight_assistant(
        self,
        session_id: str,
        user_id: str,
        *,
        job_id: str,
        content: str,
        trace: list[dict[str, Any]],
        status: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Commit a completed sidecar assistant, or discard stopped turns."""
        with self._lock:
            path = self._inflight_path(user_id, session_id, job_id)
            message = self._read_json(path, None)
            if status == "stopped":
                path.unlink(missing_ok=True)
                self._update_session(
                    session_id,
                    user_id=user_id,
                    updated_at=utc_now_iso(),
                    status="idle",
                )
                return None

            if not isinstance(message, dict):
                return self.append_assistant_message(
                    session_id,
                    user_id,
                    content=content,
                    job_id=job_id,
                    trace=trace,
                    status=status,
                    meta=meta,
                )

            now = utc_now_iso()
            messages = self._read_json(self._chat_path(user_id, session_id), [])
            if not isinstance(messages, list):
                messages = []
            if _has_distinct_streamed_content(message.get("content"), content):
                preserved = dict(message)
                preserved["status"] = "complete"
                preserved["trace"] = trace
                preserved["updated_at"] = now
                if "context_status" in message:
                    preserved["context_status"] = message["context_status"]
                messages.append(preserved)

                final_message = {
                    "id": uuid.uuid4().hex,
                    "role": "assistant",
                    "content": content,
                    "created_at": now,
                    "updated_at": now,
                    "status": status,
                    "job_id": job_id,
                    "trace": [],
                }
                if isinstance(meta, dict) and meta:
                    final_message["meta"] = meta
                messages.append(final_message)
                committed = final_message
            else:
                message["content"] = content
                message["status"] = status
                message["trace"] = trace
                message["updated_at"] = now
                if isinstance(meta, dict) and meta:
                    message["meta"] = meta
                messages.append(message)
                committed = message
            self._write_json(self._chat_path(user_id, session_id), messages)
            path.unlink(missing_ok=True)
            self._update_session(
                session_id,
                user_id=user_id,
                updated_at=now,
                status="idle" if status in {"complete", "stopped"} else "error",
            )
            return dict(committed)

    def discard_inflight_assistant(self, session_id: str, user_id: str, *, job_id: str) -> None:
        """Remove a running-turn sidecar without committing it to chat history."""
        with self._lock:
            self._inflight_path(user_id, session_id, job_id).unlink(missing_ok=True)

    def clear_stale_running_session(self, session_id: str, user_id: str) -> bool:
        """Clear a persisted running state when no in-memory job owns it."""
        with self._lock:
            session = self.get_session(session_id, user_id=user_id)
            if session is None or str(session.get("status", "")).lower() != "running":
                return False
            inflight_dir = self._inflight_dir(user_id, session_id)
            if inflight_dir.exists():
                shutil.rmtree(inflight_dir, ignore_errors=True)
            self._update_session(
                session_id,
                user_id=user_id,
                updated_at=utc_now_iso(),
                status="idle",
            )
            return True

    def _read_inflight_messages(self, user_id: str, session_id: str) -> list[dict[str, Any]]:
        """Return non-stale running assistant sidecars for history merging."""
        inflight_dir = self._inflight_dir(user_id, session_id)
        if not inflight_dir.exists():
            return []
        now = datetime.now(timezone.utc)
        messages: list[dict[str, Any]] = []
        for path in sorted(inflight_dir.glob("*.json")):
            message = self._read_json(path, None)
            if not isinstance(message, dict):
                continue
            updated_raw = str(message.get("updated_at") or message.get("created_at") or "")
            try:
                updated_at = datetime.fromisoformat(updated_raw)
            except ValueError:
                updated_at = now
            if updated_at.tzinfo is None:
                updated_at = updated_at.replace(tzinfo=timezone.utc)
            if now - updated_at > INFLIGHT_STALE_TTL:
                path.unlink(missing_ok=True)
                continue
            if message.get("role") == "assistant" and str(message.get("status", "")).lower() == "running":
                messages.append(message)
        return messages

    def append_assistant_message(
        self,
        session_id: str,
        user_id: str,
        *,
        content: str,
        job_id: str,
        trace: list[dict[str, Any]],
        status: str,
        meta: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Append the assistant turn and transition the session back to idle/error."""
        with self._lock:
            message = {
                "id": uuid.uuid4().hex,
                "role": "assistant",
                "content": content,
                "created_at": utc_now_iso(),
                "status": status,
                "job_id": job_id,
                "trace": trace,
            }
            if isinstance(meta, dict) and meta:
                message["meta"] = meta
            messages = self._read_json(self._chat_path(user_id, session_id), [])
            messages.append(message)
            self._write_json(self._chat_path(user_id, session_id), messages)
            self._update_session(
                session_id,
                user_id=user_id,
                updated_at=message["created_at"],
                status="idle" if status in {"complete", "stopped"} else "error",
            )
            return message

    def set_session_status(self, session_id: str, user_id: str, status: str) -> None:
        """Update only the status field for a session row."""
        with self._lock:
            self._update_session(session_id, user_id=user_id, updated_at=utc_now_iso(), status=status)

    def rename_session(self, session_id: str, user_id: str, title: str) -> dict[str, Any]:
        """Compatibility wrapper around ``patch_session`` for title-only edits."""
        return self.patch_session(session_id, user_id, title=normalize_title(title))

    def patch_session(
        self,
        session_id: str,
        user_id: str,
        *,
        title: str | None = None,
        composer_prefs: dict[str, Any] | None = None,
        ui_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Apply partial updates to a persisted session row."""
        if title is None and composer_prefs is None and ui_state is None:
            raise ValueError("nothing to update")
        with self._lock:
            sessions = self._load_sessions(user_id)
            for session in sessions:
                if session["id"] != session_id:
                    continue
                if session.get("user_id", "10001") != user_id:
                    continue
                session["updated_at"] = utc_now_iso()
                if title is not None:
                    session["title"] = title
                if composer_prefs is not None:
                    session["composer_prefs"] = normalize_composer_prefs(composer_prefs)
                effective_prefs = session.get("composer_prefs") if isinstance(session.get("composer_prefs"), dict) else {}
                if ui_state is not None:
                    session["ui_state"] = normalize_ui_state(ui_state, composer_prefs=effective_prefs)
                elif composer_prefs is not None:
                    session["ui_state"] = normalize_ui_state(session.get("ui_state"), composer_prefs=effective_prefs)
                self._save_sessions(user_id, sessions)
                return self._with_message_count(dict(session))
            raise KeyError(session_id)

    def delete_session(self, session_id: str, user_id: str) -> None:
        """Delete one idle session row and its session-scoped files."""
        with self._lock:
            sessions = self._load_sessions(user_id)
            next_sessions = [
                session
                for session in sessions
                if not (session["id"] == session_id and session.get("user_id", "10001") == user_id)
            ]
            if len(next_sessions) == len(sessions):
                raise KeyError(session_id)
            self._save_sessions(user_id, next_sessions)
            safe_session = self._safe_segment(session_id, "default_session")
            shutil.rmtree(self._user_root(user_id) / "sessions" / safe_session, ignore_errors=True)
            shutil.rmtree(self._session_workspace(user_id, session_id), ignore_errors=True)

    def _with_message_count(self, session: dict[str, Any]) -> dict[str, Any]:
        """Normalize a session row and include the persisted transcript length."""
        out = self._normalize_session(session)
        session_id = str(out.get("id", ""))
        user_id = str(out.get("user_id", "10001"))
        messages = self._read_json(self._chat_path(user_id, session_id), [])
        count = len(messages) if isinstance(messages, list) else 0
        if str(out.get("status", "idle")).lower() == "running":
            count += len(self._read_inflight_messages(user_id, session_id))
        out["message_count"] = count
        return out

    @staticmethod
    def _normalize_session(session: dict[str, Any]) -> dict[str, Any]:
        """Normalize nested prefs/UI state before returning a session payload."""
        out = dict(session)
        raw = out.get("composer_prefs")
        normalized_prefs = normalize_composer_prefs(raw if isinstance(raw, dict) else {})
        out["composer_prefs"] = normalized_prefs
        raw_ui_state = out.get("ui_state")
        out["ui_state"] = normalize_ui_state(raw_ui_state, composer_prefs=normalized_prefs)
        return out

    def _update_session(
        self,
        session_id: str,
        user_id: str,
        *,
        updated_at: str | None = None,
        status: str | None = None,
        title: str | None = None,
    ) -> None:
        """Internal helper for small in-place session row updates."""
        sessions = self._load_sessions(user_id)
        for session in sessions:
            if session["id"] != session_id:
                continue
            if session.get("user_id", "10001") != user_id:
                continue
            if updated_at is not None:
                session["updated_at"] = updated_at
            if status is not None:
                session["status"] = status
            if title:
                session["title"] = title
            self._save_sessions(user_id, sessions)
            return
        raise KeyError(session_id)
