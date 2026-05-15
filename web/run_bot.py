"""Thin adapter that runs a single nanobot turn for the web UI.

Think of this file as the execution strategy layer for the browser app:

- choose whether to run inside WSL+bwrap, local POSIX+bwrap, or in-process
- bridge progress messages back to the Flask app
- keep stop/fallback behavior consistent across strategies
"""

from __future__ import annotations

import asyncio
import json
import os
import shlex
import subprocess
import sys
import threading
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Callable

if __package__:
    from .agent_runtime import (
        build_runtime_policy,
        classify_progress_event,
        is_stop_ack,
        status_from_outbound_metadata,
    )
    from .session_workspace import prepare_session_workspace, user_root_path
    from .turn_context import merge_turn_context_with_legacy
else:
    from agent_runtime import (
        build_runtime_policy,
        classify_progress_event,
        is_stop_ack,
        status_from_outbound_metadata,
    )
    from session_workspace import prepare_session_workspace, user_root_path
    from turn_context import merge_turn_context_with_legacy

TraceEmitter = Callable[[str, str, dict[str, Any] | None], None]
AssistantEmitter = Callable[[str, str, dict[str, Any] | None], None]


def _ensure_repo_import_paths() -> None:
    """Allow `python web/app.py` to import the vendored nanobot package."""
    repo_root = Path(__file__).resolve().parents[1]
    vendored_nanobot_root = repo_root / "nanobot"

    for candidate in (repo_root, vendored_nanobot_root):
        candidate_str = str(candidate)
        if candidate.exists() and candidate_str not in sys.path:
            sys.path.insert(0, candidate_str)


_ensure_repo_import_paths()


def _is_stop_ack(content: str) -> bool:
    """Return whether an outbound message is just acknowledging ``/stop``."""
    return is_stop_ack(content)


def _status_from_outbound_metadata(metadata: dict[str, object] | None) -> str:
    """Map Nanobot stop reasons onto Web run statuses."""
    return status_from_outbound_metadata(metadata)


@dataclass(frozen=True)
class RunOutcome:
    """Small transport object shared by all execution strategies."""
    status: str
    text: str


class BotRunHandle:
    """Blocking run handle with cooperative stop support."""

    def run(self) -> RunOutcome:
        raise NotImplementedError

    def stop(self) -> bool:
        raise NotImplementedError


class BotRunner:
    """Create a fresh run handle per web request.

    ``BotRunner`` only decides *how* to run. The returned handle owns the actual
    lifecycle of one chat turn.
    """

    def __init__(self) -> None:
        self._enable_wsl_bwrap = os.environ.get("WEB_ENABLE_WSL_BWRAP", "1") != "0"
        self._enable_posix_bwrap = os.environ.get("WEB_ENABLE_POSIX_BWRAP", "1") != "0"
        self._require_bwrap = os.environ.get("WEB_REQUIRE_BWRAP", "0") == "1"
        self._wsl_bwrap_supported: bool | None = None
        self._posix_bwrap_supported: bool | None = None

    def start(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        emit_trace: TraceEmitter,
        emit_assistant: AssistantEmitter,
        composer_prefs: dict[str, Any] | None = None,
        turn_context: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ) -> BotRunHandle:
        """Pick the best available runtime for this request."""
        if self._can_use_wsl_bwrap():
            return _WslBwrapRunHandle(
                user_id=user_id,
                session_id=session_id,
                message=message,
                emit_trace=emit_trace,
                emit_assistant=emit_assistant,
                composer_prefs=composer_prefs,
                turn_context=turn_context,
                context=context,
            )
        if self._can_use_posix_bwrap():
            return _PosixBwrapRunHandle(
                user_id=user_id,
                session_id=session_id,
                message=message,
                emit_trace=emit_trace,
                emit_assistant=emit_assistant,
                composer_prefs=composer_prefs,
                turn_context=turn_context,
                context=context,
            )
        if self._require_bwrap:
            raise RuntimeError("bwrap is required but not available on this platform.")
        return _AgentLoopRunHandle(
            user_id=user_id,
            session_id=session_id,
            message=message,
            emit_trace=emit_trace,
            emit_assistant=emit_assistant,
            composer_prefs=composer_prefs,
            turn_context=turn_context,
            context=context,
        )

    def _can_use_wsl_bwrap(self) -> bool:
        """Check once whether Windows+WSL can launch a ``bwrap`` worker."""
        if not self._enable_wsl_bwrap:
            return False
        if os.name != "nt":
            return False
        if self._wsl_bwrap_supported is not None:
            return self._wsl_bwrap_supported

        try:
            result = subprocess.run(
                ["wsl", "-e", "bash", "-lc", "command -v bwrap >/dev/null 2>&1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            self._wsl_bwrap_supported = result.returncode == 0
        except Exception:
            self._wsl_bwrap_supported = False
        return self._wsl_bwrap_supported

    def _can_use_posix_bwrap(self) -> bool:
        """Check once whether the current POSIX host can launch ``bwrap``."""
        if not self._enable_posix_bwrap:
            return False
        if os.name == "nt":
            return False
        if self._posix_bwrap_supported is not None:
            return self._posix_bwrap_supported
        try:
            result = subprocess.run(
                ["bash", "-lc", "command -v bwrap >/dev/null 2>&1"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                timeout=5,
                check=False,
            )
            self._posix_bwrap_supported = result.returncode == 0
        except Exception:
            self._posix_bwrap_supported = False
        return self._posix_bwrap_supported


class _WslBwrapRunHandle(BotRunHandle):
    """Run one turn inside WSL using bubblewrap isolation."""

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        emit_trace: TraceEmitter,
        emit_assistant: AssistantEmitter,
        composer_prefs: dict[str, Any] | None = None,
        turn_context: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ):
        self._user_id = user_id
        self._session_id = session_id
        self._message = message
        self._emit_trace = emit_trace
        self._emit_assistant = emit_assistant
        self._composer_prefs = composer_prefs
        self._turn_context = merge_turn_context_with_legacy(turn_context, composer_prefs)
        self._context = context
        active_skill = str((self._turn_context.get("skills") or [""])[0])
        self._strict_worker_env = active_skill == "deep-research"
        self._done = threading.Event()
        self._state_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._stop_requested = False

    def _was_stop_requested(self) -> bool:
        with self._state_lock:
            return self._stop_requested

    def run(self) -> RunOutcome:
        """Launch the worker, stream JSON events, and translate them into traces."""
        repo_root = Path(__file__).resolve().parents[1]
        wsl_repo = _to_wsl_path(repo_root)
        user_runtime = user_root_path(self._user_id)
        user_runtime.mkdir(parents=True, exist_ok=True)
        data_runtime = repo_root / ".nanobot"
        data_runtime.mkdir(parents=True, exist_ok=True)
        wsl_python_raw = os.environ.get("WEB_WSL_PYTHON", "/home/user1/.venvs/nanobot-web/bin/python")
        bwrap_mounts = _format_bwrap_mounts(
            read_only_dirs=_load_bwrap_profile_mounts("linux").get("read_only_dirs", []),
            read_only_files=_load_bwrap_profile_mounts("linux").get("read_only_files", []),
            repo_root=wsl_repo,
            isolated_user_runtime=_to_wsl_path(user_runtime),
            writable_dirs=[
                _to_wsl_path(data_runtime),
            ],
            python_roots=[str(PurePosixPath(wsl_python_raw).parent.parent)],
            check_exists=False,
        )
        worker_module = "web.run_bot_worker"
        sources = self._turn_context.get("preferred_data_sources") or []
        preferred_source = str(sources[0]) if len(sources) == 1 else "auto"
        keep_proxy_for_web = os.environ.get("WEB_WSL_KEEP_PROXY_FOR_WEB", "1") != "0"
        should_keep_proxy = keep_proxy_for_web and preferred_source in {"web", "auto"}
        proxy_unset_clause = (
            ""
            if should_keep_proxy
            else "env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy "
            "-u no_proxy -u NO_PROXY "
        )
        user_tag = shlex.quote(self._user_id)
        session_tag = shlex.quote(self._session_id)
        hold_seconds = shlex.quote(os.environ.get("WEB_BWRAP_DEBUG_HOLD_SECONDS", "0"))
        wsl_python = shlex.quote(wsl_python_raw)
        # The worker itself is a normal ``python -m web.run_bot_worker`` process.
        # ``bwrap`` only wraps that worker; the parent process remains outside and
        # relays JSON events between the sandbox and the browser.
        cmd = (
            f"cd {shlex.quote(wsl_repo)} && "
            f"PY_BIN={wsl_python} && "
            "[ -x \"$PY_BIN\" ] || PY_BIN=python3 && "
            "PY_DIR=$(dirname \"$PY_BIN\") && "
            "PATH=\"$PY_DIR:$PATH\" && "
            "export PYTHONDONTWRITEBYTECODE=1 && "
            f"NB_USER_ID={user_tag} NB_SESSION_ID={session_tag} NB_HOLD_SECONDS={hold_seconds} "
            "NB_PYTHON=\"$PY_BIN\" "
            f"{proxy_unset_clause}"
            "bwrap --unshare-user --uid \"$(id -u)\" --gid \"$(id -g)\" "
            f"{bwrap_mounts}"
            "--tmpfs /tmp --dev /dev --proc /proc --unshare-pid --die-with-parent "
            "\"$PY_BIN\" -m "
            f"{worker_module}"
        )
        payload = {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "message": self._message,
            "composer_prefs": self._composer_prefs or {},
            "turn_context": self._turn_context,
            "context": self._context or {},
        }
        try:
            proc = subprocess.Popen(
                ["wsl", "-e", "bash", "-lc", cmd],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
            )
        except Exception as exc:
            self._done.set()
            return RunOutcome(status="error", text=f"Failed to start WSL bwrap worker: {exc}")

        with self._state_lock:
            self._proc = proc

        try:
            if proc.stdin is not None:
                proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()
                proc.stdin.close()

            # Unless the worker emits a terminal ``done``/``error`` event we treat
            # the run as failed and decide whether to fall back.
            final_status = "error"
            final_text = "Worker exited without result."
            got_terminal_event = False
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = _decode_subprocess_bytes(raw).strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    event_type = event.get("type")
                    if event_type == "trace":
                        trace_meta = event.get("trace_meta")
                        if not isinstance(trace_meta, dict):
                            trace_meta = None
                        self._emit_trace(
                            str(event.get("event_type", "progress")),
                            str(event.get("text", "")),
                            trace_meta,
                        )
                    elif event_type == "stream":
                        stream_meta = event.get("stream_meta")
                        if not isinstance(stream_meta, dict):
                            stream_meta = None
                        self._emit_assistant(
                            str(event.get("event_type", "assistant_delta")),
                            str(event.get("text", "")),
                            stream_meta,
                        )
                    elif event_type == "done":
                        final_status = str(event.get("status", "complete"))
                        final_text = str(event.get("text", ""))
                        got_terminal_event = True
                    elif event_type == "error":
                        final_status = "error"
                        final_text = str(event.get("message", "Worker execution failed."))
                        got_terminal_event = True

            proc.wait(timeout=2)
            stderr_text = _decode_subprocess_bytes(proc.stderr.read() if proc.stderr else b"")
            cleaned_stderr = _sanitize_worker_stderr(stderr_text)
            if self._was_stop_requested():
                return RunOutcome(status="stopped", text="Stopped by user.")
            if not got_terminal_event:
                if cleaned_stderr:
                    self._emit_trace("progress", f"[worker stderr] {cleaned_stderr}", None)
                # Deep-research mode prefers a hard failure because mixing worker
                # and in-process paths can change available tools and filesystem
                # assumptions mid-workflow.
                if self._strict_worker_env:
                    return RunOutcome(
                        status="error",
                        text=(
                            "WSL bwrap worker failed in deep_research mode; "
                            "fallback to in-process is disabled to avoid mixed runtime.\n"
                            f"{cleaned_stderr or 'no worker stderr'}"
                        ),
                    )
                self._emit_trace("progress", "[Fallback] bwrap worker failed; switching to in-process runner.", None)
                return _AgentLoopRunHandle(
                    user_id=self._user_id,
                    session_id=self._session_id,
                    message=self._message,
                    emit_trace=self._emit_trace,
                    emit_assistant=self._emit_assistant,
                    composer_prefs=self._composer_prefs,
                    turn_context=self._turn_context,
                    context=self._context,
                ).run()
            if proc.returncode not in (0, None) and final_status != "stopped":
                if cleaned_stderr:
                    final_text = f"{final_text}\n{cleaned_stderr}"
                final_status = "error"
            return RunOutcome(status=final_status, text=final_text)
        except Exception as exc:
            if self._was_stop_requested():
                return RunOutcome(status="stopped", text="Stopped by user.")
            return RunOutcome(status="error", text=f"WSL bwrap worker error: {exc}")
        finally:
            self._done.set()
            with self._state_lock:
                self._proc = None

    def stop(self) -> bool:
        with self._state_lock:
            if self._done.is_set():
                return False
            self._stop_requested = True
            proc = self._proc
        if proc is None:
            return True
        with suppress(Exception):
            proc.terminate()
        return True


class _PosixBwrapRunHandle(BotRunHandle):
    """Run one turn inside local POSIX bubblewrap isolation."""

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        emit_trace: TraceEmitter,
        emit_assistant: AssistantEmitter,
        composer_prefs: dict[str, Any] | None = None,
        turn_context: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ):
        self._user_id = user_id
        self._session_id = session_id
        self._message = message
        self._emit_trace = emit_trace
        self._emit_assistant = emit_assistant
        self._composer_prefs = composer_prefs
        self._turn_context = merge_turn_context_with_legacy(turn_context, composer_prefs)
        self._context = context
        active_skill = str((self._turn_context.get("skills") or [""])[0])
        self._strict_worker_env = active_skill == "deep-research"
        self._done = threading.Event()
        self._state_lock = threading.Lock()
        self._proc: subprocess.Popen[str] | None = None
        self._stop_requested = False

    def _was_stop_requested(self) -> bool:
        with self._state_lock:
            return self._stop_requested

    def run(self) -> RunOutcome:
        """Launch the local worker and decode its JSON event stream."""
        repo_root = Path(__file__).resolve().parents[1]
        user_runtime = user_root_path(self._user_id)
        user_runtime.mkdir(parents=True, exist_ok=True)
        data_runtime = repo_root / ".nanobot"
        data_runtime.mkdir(parents=True, exist_ok=True)
        worker_module = "web.run_bot_worker"
        user_tag = shlex.quote(self._user_id)
        session_tag = shlex.quote(self._session_id)
        hold_seconds = shlex.quote(os.environ.get("WEB_BWRAP_DEBUG_HOLD_SECONDS", "0"))
        default_posix_python = os.environ.get("NB_PYTHON", sys.executable)
        py_bin_raw = os.environ.get("WEB_POSIX_PYTHON", default_posix_python)
        profile = _load_bwrap_profile_mounts(_current_bwrap_profile_name())
        bwrap_mounts = _format_bwrap_mounts(
            read_only_dirs=profile.get("read_only_dirs", []),
            read_only_files=profile.get("read_only_files", []),
            repo_root=str(repo_root),
            isolated_user_runtime=str(user_runtime),
            writable_dirs=[
                str(data_runtime),
            ],
            python_roots=_python_runtime_roots(py_bin_raw),
            check_exists=True,
        )
        py_bin = shlex.quote(py_bin_raw)
        cmd = (
            f"cd {shlex.quote(str(repo_root))} && "
            f"PY_BIN={py_bin} && "
            "[ -x \"$PY_BIN\" ] || PY_BIN=python3 && "
            "PY_DIR=$(dirname \"$PY_BIN\") && "
            "PATH=\"$PY_DIR:$PATH\" && "
            "export PYTHONDONTWRITEBYTECODE=1 && "
            f"NB_USER_ID={user_tag} NB_SESSION_ID={session_tag} NB_HOLD_SECONDS={hold_seconds} "
            "NB_PYTHON=\"$PY_BIN\" "
            "env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u all_proxy "
            "-u no_proxy -u NO_PROXY "
            "bwrap --unshare-user --uid \"$(id -u)\" --gid \"$(id -g)\" "
            f"{bwrap_mounts}"
            "--tmpfs /tmp --dev /dev --proc /proc --unshare-pid --die-with-parent "
            "\"$PY_BIN\" -m "
            f"{worker_module}"
        )
        payload = {
            "user_id": self._user_id,
            "session_id": self._session_id,
            "message": self._message,
            "composer_prefs": self._composer_prefs or {},
            "turn_context": self._turn_context,
            "context": self._context or {},
        }
        try:
            proc = subprocess.Popen(
                ["bash", "-lc", cmd],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
            )
        except Exception as exc:
            self._done.set()
            return RunOutcome(status="error", text=f"Failed to start POSIX bwrap worker: {exc}")

        with self._state_lock:
            self._proc = proc

        try:
            if proc.stdin is not None:
                proc.stdin.write((json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8"))
                proc.stdin.flush()
                proc.stdin.close()

            # Some worker failures show up as plain stdout/stderr instead of JSON;
            # we keep a sample so debugging from the web UI is less opaque.
            final_status = "error"
            final_text = "Worker exited without result."
            got_terminal_event = False
            non_json_stdout: list[str] = []
            if proc.stdout is not None:
                for raw in proc.stdout:
                    line = _decode_subprocess_bytes(raw).strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        if len(non_json_stdout) < 40:
                            non_json_stdout.append(line[:500])
                        continue
                    event_type = event.get("type")
                    if event_type == "trace":
                        trace_meta = event.get("trace_meta")
                        if not isinstance(trace_meta, dict):
                            trace_meta = None
                        self._emit_trace(
                            str(event.get("event_type", "progress")),
                            str(event.get("text", "")),
                            trace_meta,
                        )
                    elif event_type == "stream":
                        stream_meta = event.get("stream_meta")
                        if not isinstance(stream_meta, dict):
                            stream_meta = None
                        self._emit_assistant(
                            str(event.get("event_type", "assistant_delta")),
                            str(event.get("text", "")),
                            stream_meta,
                        )
                    elif event_type == "done":
                        final_status = str(event.get("status", "complete"))
                        final_text = str(event.get("text", ""))
                        got_terminal_event = True
                    elif event_type == "error":
                        final_status = "error"
                        final_text = str(event.get("message", "Worker execution failed."))
                        got_terminal_event = True

            proc.wait(timeout=2)
            stderr_text = _decode_subprocess_bytes(proc.stderr.read() if proc.stderr else b"")
            cleaned_stderr = _sanitize_worker_stderr(stderr_text)
            if self._was_stop_requested():
                return RunOutcome(status="stopped", text="Stopped by user.")
            if not got_terminal_event:
                if cleaned_stderr:
                    self._emit_trace("progress", f"[worker stderr] {cleaned_stderr}", None)
                extra = ""
                if non_json_stdout:
                    snippet = "\n".join(non_json_stdout)
                    if len(snippet) > 4000:
                        snippet = snippet[:4000] + "\n...(truncated)"
                    extra = f"\nnon-JSON stdout (first lines):\n{snippet}"
                rc = proc.returncode
                if self._strict_worker_env:
                    return RunOutcome(
                        status="error",
                        text=(
                            "POSIX bwrap worker failed in deep_research mode; "
                            "fallback to in-process is disabled to avoid mixed runtime.\n"
                            f"worker returncode={rc}\n"
                            f"{cleaned_stderr or 'no worker stderr'}"
                            f"{extra}"
                        ),
                    )
                self._emit_trace("progress", "[Fallback] bwrap worker failed; switching to in-process runner.", None)
                return _AgentLoopRunHandle(
                    user_id=self._user_id,
                    session_id=self._session_id,
                    message=self._message,
                    emit_trace=self._emit_trace,
                    emit_assistant=self._emit_assistant,
                    composer_prefs=self._composer_prefs,
                    turn_context=self._turn_context,
                    context=self._context,
                ).run()
            if proc.returncode not in (0, None) and final_status != "stopped":
                if cleaned_stderr:
                    final_text = f"{final_text}\n{cleaned_stderr}"
                final_status = "error"
            return RunOutcome(status=final_status, text=final_text)
        except Exception as exc:
            if self._was_stop_requested():
                return RunOutcome(status="stopped", text="Stopped by user.")
            return RunOutcome(status="error", text=f"POSIX bwrap worker error: {exc}")
        finally:
            self._done.set()
            with self._state_lock:
                self._proc = None

    def stop(self) -> bool:
        with self._state_lock:
            if self._done.is_set():
                return False
            self._stop_requested = True
            proc = self._proc
        if proc is None:
            return True
        with suppress(Exception):
            proc.terminate()
        return True


def _to_wsl_path(path: Path) -> str:
    """Convert a Windows path into its ``/mnt/<drive>/...`` WSL form."""
    resolved = path.resolve()
    drive = resolved.drive.rstrip(":").lower()
    tail = resolved.as_posix().split(":/", 1)[-1]
    return f"/mnt/{drive}/{tail}"


def _format_bwrap_mounts(
    *,
    writable_dirs: list[str],
    isolated_user_runtime: str | None = None,
    read_only_dirs: list[str] | None = None,
    read_only_files: list[str] | None = None,
    repo_root: str | None = None,
    python_roots: list[str] | None = None,
    check_exists: bool = False,
) -> str:
    """Return shell-quoted bwrap mounts for a minimal filesystem view.

    Only configured system directories are mounted read-only. Runtime state is
    added separately so sibling user directories stay hidden.
    """
    parts = []
    created_dirs: set[str] = set()

    def _exists(path: str) -> bool:
        return (not check_exists) or Path(path).exists()

    def _add_dir(path: str) -> None:
        if path and path != "/" and path not in created_dirs:
            parts.append(f"--dir {shlex.quote(path)} ")
            created_dirs.add(path)

    def _ensure_parents(path: str) -> None:
        parents = list(PurePosixPath(path).parents)
        for parent in reversed(parents):
            parent_s = parent.as_posix()
            if parent_s != "/":
                _add_dir(parent_s)

    def _add_ro_bind(path: str) -> None:
        clean = str(path or "").strip()
        if not clean or not _exists(clean):
            return
        _ensure_parents(clean)
        quoted = shlex.quote(clean)
        parts.append(f"--ro-bind {quoted} {quoted} ")

    precreate_paths = [
        *(read_only_dirs or []),
        *(read_only_files or []),
        *([repo_root] if repo_root else []),
        *(python_roots or []),
        *(writable_dirs or []),
    ]
    isolated = str(isolated_user_runtime or "").strip()
    if isolated:
        precreate_paths.append(PurePosixPath(isolated).parent.as_posix())
    for raw in precreate_paths:
        clean = str(raw or "").strip()
        if clean:
            _ensure_parents(clean)

    for raw in read_only_dirs or []:
        _add_ro_bind(raw)
    for raw in read_only_files or []:
        _add_ro_bind(raw)
    if repo_root:
        _add_ro_bind(repo_root)
    for raw in python_roots or []:
        _add_ro_bind(raw)

    if isolated:
        users_root = PurePosixPath(isolated).parent.as_posix()
        _ensure_parents(users_root)
        quoted_root = shlex.quote(users_root)
        quoted_user = shlex.quote(isolated)
        parts.append(f"--tmpfs {quoted_root} ")
        parts.append(f"--dir {quoted_user} ")
        parts.append(f"--bind {quoted_user} {quoted_user} ")

    for raw in writable_dirs:
        path = str(raw).strip()
        if not path or path == isolated or not _exists(path):
            continue
        _ensure_parents(path)
        quoted = shlex.quote(path)
        parts.append(f"--bind {quoted} {quoted} ")
    return "".join(parts)


def _current_bwrap_profile_name() -> str:
    """Return the configured bwrap profile name for the current host."""
    override = os.environ.get("WEB_BWRAP_PROFILE", "").strip().lower()
    if override:
        return override
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform.startswith("linux"):
        return "linux"
    return "linux"


def _bwrap_profiles_path() -> Path:
    override = os.environ.get("WEB_BWRAP_MOUNT_CONFIG", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return Path(__file__).resolve().with_name("bwrap_mount_profiles.json")


def _load_bwrap_profile_mounts(profile_name: str) -> dict[str, list[str]]:
    """Load read-only mount lists from the bwrap profile JSON file."""
    try:
        data = json.loads(_bwrap_profiles_path().read_text(encoding="utf-8"))
    except Exception:
        data = {}
    profiles = data.get("profiles") if isinstance(data, dict) else {}
    if not isinstance(profiles, dict):
        profiles = {}
    default_profile = str(data.get("default_profile", "linux")).strip() if isinstance(data, dict) else "linux"
    raw = profiles.get(profile_name) or profiles.get(default_profile) or {}
    if not isinstance(raw, dict):
        raw = {}

    def _strings(key: str) -> list[str]:
        values = raw.get(key, [])
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]

    return {
        "read_only_dirs": _strings("read_only_dirs"),
        "read_only_files": _strings("read_only_files"),
    }


def _python_runtime_roots(py_bin: str) -> list[str]:
    """Return Python runtime roots that may live outside the repo/venv."""
    roots: list[str] = []
    try:
        executable = Path(py_bin).expanduser().resolve(strict=False)
    except Exception:
        return roots
    candidates = [executable.parent.parent]
    try:
        result = subprocess.run(
            [str(executable), "-c", "import sys; print(sys.base_prefix); print(getattr(sys, '_base_executable', ''))"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
            check=False,
        )
        for line in result.stdout.splitlines():
            value = line.strip()
            if value:
                p = Path(value).expanduser()
                candidates.append(p.parent.parent if p.is_file() else p)
    except Exception:
        pass
    for candidate in candidates:
        try:
            resolved = candidate.resolve(strict=False)
        except Exception:
            continue
        value = str(resolved)
        if value not in roots:
            roots.append(value)
    return roots


def _decode_subprocess_bytes(data: bytes) -> str:
    """Best-effort decoder for mixed platform subprocess output."""
    if not data:
        return ""
    for encoding in ("utf-8", "utf-16le", "gbk"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    return data.decode("utf-8", errors="replace")


def _sanitize_worker_stderr(text: str) -> str:
    """Drop repetitive platform noise so real worker failures stand out."""
    if not text:
        return ""
    lines = []
    for raw in text.replace("\r\n", "\n").split("\n"):
        line = raw.replace("\x00", "").strip()
        if not line:
            continue
        if _is_benign_wsl_noise(line):
            continue
        lines.append(line)
    return "\n".join(lines)


def _is_benign_wsl_noise(line: str) -> bool:
    """Recognize known harmless WSL proxy/screen warnings."""
    lower = line.lower()
    if "wsl:" in lower and "localhost" in lower and "proxy" in lower:
        return True
    if "wsl:" in lower and "localhost" in lower and "代理" in line:
        return True
    if "wsl:" in lower and "nat" in lower and "wsl" in lower and "localhost" in lower:
        return True
    if "screen size is bogus" in lower:
        return True
    return False


class _AgentLoopRunHandle(BotRunHandle):
    """In-process fallback path when no bwrap worker is available."""

    def __init__(
        self,
        *,
        user_id: str,
        session_id: str,
        message: str,
        emit_trace: TraceEmitter,
        emit_assistant: AssistantEmitter,
        composer_prefs: dict[str, Any] | None = None,
        turn_context: dict[str, Any] | None = None,
        context: dict[str, Any] | None = None,
    ):
        self._user_id = user_id
        self._session_id = session_id
        self._message = message
        self._emit_trace = emit_trace
        self._emit_assistant = emit_assistant
        self._composer_prefs = composer_prefs
        self._turn_context = merge_turn_context_with_legacy(turn_context, composer_prefs)
        self._context = context
        self._ready = threading.Event()
        self._done = threading.Event()
        self._state_lock = threading.Lock()
        self._stop_requested = False
        self._stop_signal_sent = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._bus = None

    def run(self) -> RunOutcome:
        """Run the async agent loop in a private event loop on the caller thread."""
        try:
            return asyncio.run(self._run_once())
        finally:
            self._done.set()

    def stop(self) -> bool:
        with self._state_lock:
            if self._done.is_set():
                return False
            self._stop_requested = True

        if self._ready.wait(timeout=5) and self._loop is not None:
            future = asyncio.run_coroutine_threadsafe(self._send_stop_if_needed(), self._loop)
            with suppress(Exception):
                future.result(timeout=2)
        return True

    async def _run_once(self) -> RunOutcome:
        """Execute one turn directly in-process.

        This intentionally mirrors ``web.run_bot_worker._run_once`` so behavior
        stays aligned whether we are inside a worker or not.
        """
        from loguru import logger

        from nanobot.agent.loop import AgentLoop
        from nanobot.bus.events import InboundMessage
        from nanobot.bus.queue import MessageBus
        from nanobot.config.loader import load_config
        from nanobot.providers.factory import create_provider

        logger.disable("nanobot")

        config = load_config()
        tool_workspace = prepare_session_workspace(self._user_id, self._session_id, kind="tool")
        context_workspace = prepare_session_workspace(self._user_id, self._session_id, kind="context")
        session_temp = prepare_session_workspace(self._user_id, self._session_id, kind="temp")

        bus = MessageBus()
        provider = create_provider(config)
        policy = build_runtime_policy(
            composer_prefs=self._composer_prefs,
            turn_context=self._turn_context,
            context=self._context,
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
            self._emit_trace("progress", policy.policy_trace_text, None)

        reasoning_effort = config.agents.defaults.reasoning_effort
        if active_skill == "deep-research" and str(reasoning_effort or "").lower() not in {"medium", "high", "xhigh"}:
            reasoning_effort = "medium"

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

        loop_task: asyncio.Task[None] | None = None
        self._loop = asyncio.get_running_loop()
        self._bus = bus
        self._ready.set()

        try:
            loop_task = asyncio.create_task(agent_loop.run())
            await bus.publish_inbound(
                InboundMessage(
                    channel="web",
                    sender_id="user",
                    chat_id=f"{self._user_id}:{self._session_id}",
                    content=self._message,
                    metadata={
                        "composer_prefs_runtime": runtime_text,
                        "workbench_context": chat_context,
                        "_wants_stream": True,
                    },
                )
            )
            await self._send_stop_if_needed()

            # Keep consuming outbound events until the agent emits a final answer
            # or a stop acknowledgement.
            message_tool_contents: list[str] = []
            target_chat_id = f"{self._user_id}:{self._session_id}"
            while True:
                msg = await bus.consume_outbound()
                if msg.metadata.get("_retry_wait"):
                    self._emit_trace(
                        "retry_wait",
                        msg.content or "Model request failed, retrying.",
                        {"event_type": "retry_wait"},
                    )
                    continue
                if msg.metadata.get("_stream_delta"):
                    stream_meta = {
                        "stream_id": msg.metadata.get("_stream_id"),
                    }
                    self._emit_assistant("assistant_delta", msg.content or "", stream_meta)
                    continue
                if msg.metadata.get("_stream_end"):
                    stream_meta = {
                        "stream_id": msg.metadata.get("_stream_id"),
                        "resuming": bool(msg.metadata.get("_resuming", False)),
                    }
                    self._emit_assistant("assistant_end", "", stream_meta)
                    continue
                if msg.metadata.get("_message_tool_delivery"):
                    if msg.channel == "web" and msg.chat_id == target_chat_id:
                        content = msg.content or ""
                        if content:
                            message_tool_contents.append(content)
                            self._emit_assistant(
                                "assistant_delta",
                                content,
                                {
                                    "stream_id": f"message-tool:{len(message_tool_contents)}",
                                    "source": "message_tool",
                                },
                            )
                    continue
                if msg.metadata.get("_progress"):
                    trace_meta = msg.metadata.get("_trace_meta")
                    if not isinstance(trace_meta, dict):
                        trace_meta = None
                    self._emit_trace(
                        classify_progress_event(
                            msg.content,
                            tool_hint=bool(msg.metadata.get("_tool_hint", False)),
                            trace_meta=trace_meta,
                        ),
                        msg.content,
                        trace_meta,
                    )
                    continue

                if self._stop_requested and _is_stop_ack(msg.content):
                    return RunOutcome(status="stopped", text="Stopped by user.")
                content = msg.content or ""
                if msg.metadata.get("_suppressed_final") and not content and message_tool_contents:
                    content = "\n\n".join(message_tool_contents)
                return RunOutcome(
                    status=_status_from_outbound_metadata(msg.metadata),
                    text=content,
                )
        finally:
            agent_loop.stop()
            if loop_task is not None:
                loop_task.cancel()
                with suppress(asyncio.CancelledError):
                    await loop_task
            await agent_loop.close_mcp()
            self._loop = None
            self._bus = None

    async def _send_stop_if_needed(self) -> None:
        """Translate the web UI stop button into a ``/stop`` inbound message."""
        with self._state_lock:
            should_send = self._stop_requested and not self._stop_signal_sent
            if should_send:
                self._stop_signal_sent = True

        if not should_send or self._bus is None:
            return

        from nanobot.bus.events import InboundMessage

        await self._bus.publish_inbound(
            InboundMessage(
                channel="web",
                sender_id="user",
                chat_id=f"{self._user_id}:{self._session_id}",
                content="/stop",
            )
        )
