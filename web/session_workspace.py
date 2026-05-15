"""User-scoped workspace helpers for the web runtime.

Each user owns one shared workspace. Agent context lives under ``_agent``,
long-lived user files under ``permanent``, and session outputs under
``temp/<session_id>``.
"""

from __future__ import annotations

import os
import re
import shutil
from importlib.resources import files as pkg_files
from pathlib import Path


_SAFE_SEGMENT_RE = re.compile(r"[^A-Za-z0-9._-]+")
AGENT_DIR_NAME = "_agent"
PERMANENT_DIR_NAME = "permanent"
TEMP_DIR_NAME = "temp"
SESSION_SHARED_PERMANENT_LINK = "shared_permanent"
SESSION_SHARED_CONTEXT_LINK = "shared_context"


def repo_root() -> Path:
    """Return the repository root that owns the ``web`` package."""
    return Path(__file__).resolve().parents[1]


def runtime_root_dir() -> Path:
    """Return the root folder used for all web runtime artifacts."""
    override = os.environ.get("NANOBOT_WEB_RUNTIME_DIR", "").strip()
    if override:
        return Path(override).expanduser().resolve(strict=False)
    return repo_root() / "runtime"


def _safe_segment(value: str, *, fallback: str) -> str:
    """Sanitize user/session identifiers before using them as path segments."""
    cleaned = _SAFE_SEGMENT_RE.sub("_", (value or "").strip()).strip("._")
    return cleaned or fallback


def session_workspace_path(user_id: str, session_id: str) -> Path:
    """Backward-compatible alias for the shared user workspace path."""
    return user_workspace_path(user_id)


def _runtime_users_base_dir() -> Path:
    """Return the root directory for all user runtime state."""
    return runtime_root_dir() / "users"


def user_root_path(user_id: str) -> Path:
    """Return the runtime root for one user."""
    safe_user = _safe_segment(user_id, fallback="10001")
    return _runtime_users_base_dir() / safe_user


def user_workspace_path(user_id: str) -> Path:
    """Return the shared workspace for one user."""
    return user_root_path(user_id) / "workspace"


def runtime_session_workspace_path(user_id: str, session_id: str) -> Path:
    """Return the session temp directory used for default runtime output."""
    safe_session = _safe_segment(session_id, fallback="default_session")
    return user_workspace_path(user_id) / TEMP_DIR_NAME / safe_session


def context_session_workspace_path(user_id: str, session_id: str) -> Path:
    """Return the shared agent context directory for one user."""
    return agent_context_path(user_id)


def agent_context_path(user_id: str) -> Path:
    """Return the agent-only context directory for one user."""
    return user_workspace_path(user_id) / AGENT_DIR_NAME


def permanent_workspace_path(user_id: str) -> Path:
    """Return the user-visible permanent files directory."""
    return user_workspace_path(user_id) / PERMANENT_DIR_NAME


def _ensure_session_symlink(link_path: Path, target_dir: Path) -> None:
    """Create or refresh a session-local symlink to a shared runtime directory."""
    target_dir = target_dir.resolve(strict=False)
    expected_target = os.path.relpath(target_dir, start=link_path.parent)

    if link_path.is_symlink():
        try:
            current = os.readlink(link_path)
        except OSError:
            current = ""
        if current == expected_target or link_path.resolve(strict=False) == target_dir:
            return
        link_path.unlink()
    elif link_path.exists():
        return

    link_path.symlink_to(expected_target, target_is_directory=True)


def _ensure_session_shared_links(
    temp_workspace: Path,
    *,
    permanent_workspace: Path,
    context_workspace: Path,
) -> None:
    """Expose narrow shared-workspace entrypoints inside one session temp dir."""
    _ensure_session_symlink(
        temp_workspace / SESSION_SHARED_PERMANENT_LINK,
        permanent_workspace,
    )
    _ensure_session_symlink(
        temp_workspace / SESSION_SHARED_CONTEXT_LINK,
        context_workspace,
    )


def _sync_workspace_templates(workspace: Path) -> None:
    """Create missing bundled nanobot template files without importing heavy helpers."""
    try:
        tpl = pkg_files("nanobot") / "templates"
    except Exception:
        return
    if not tpl.is_dir():
        return

    def _write(src, dest: Path) -> None:
        if dest.exists():
            return
        if src and not src.is_file():
            return
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(src.read_text(encoding="utf-8") if src else "", encoding="utf-8")

    for item in tpl.iterdir():
        if item.name.endswith(".md") and not item.name.startswith("."):
            _write(item, workspace / item.name)
    _write(tpl / "memory" / "MEMORY.md", workspace / "memory" / "MEMORY.md")
    _write(None, workspace / "memory" / "HISTORY.md")
    (workspace / "skills").mkdir(exist_ok=True)


def _copy_workspace_skills(source_root: Path, target_workspace: Path) -> None:
    """Hydrate the workspace-local ``skills/`` tree from the repo source tree."""
    source_skills = source_root / "skills"
    target_skills = target_workspace / "skills"
    if not source_skills.exists() or not source_skills.is_dir():
        return
    target_skills.mkdir(parents=True, exist_ok=True)
    for item in source_skills.iterdir():
        if not item.is_dir():
            continue
        source_skill = item / "SKILL.md"
        if not source_skill.exists():
            continue
        target_skill_dir = target_skills / item.name
        if not target_skill_dir.exists():
            try:
                shutil.copytree(item, target_skill_dir)
                continue
            except FileExistsError:
                pass

        # Skill dir already exists in this session (likely because the skill ran
        # before). Only sync static entrypoints to avoid overwriting
        # runtime-generated artifacts like deep-research ``plan.md`` /
        # ``findings.md``.
        try:
            target_skill_dir.mkdir(parents=True, exist_ok=True)

            # Always overwrite SKILL.md
            shutil.copy2(source_skill, target_skill_dir / "SKILL.md")

            # Sync scripts/ (where executable helpers live)
            source_scripts = item / "scripts"
            if source_scripts.exists() and source_scripts.is_dir():
                target_scripts = target_skill_dir / "scripts"
                target_scripts.mkdir(parents=True, exist_ok=True)
                shutil.copytree(source_scripts, target_scripts, dirs_exist_ok=True)

            # Sync other static dirs if present.
            for static_dir_name in ("agents", "references", "backup_not_relevant_ignore"):
                source_static = item / static_dir_name
                if source_static.exists() and source_static.is_dir():
                    target_static = target_skill_dir / static_dir_name
                    target_static.mkdir(parents=True, exist_ok=True)
                    shutil.copytree(source_static, target_static, dirs_exist_ok=True)
        except Exception:
            # Best-effort sync; never fail session initialization because of sync.
            continue


def prepare_session_workspace(user_id: str, session_id: str, *, kind: str = "tool") -> Path:
    """Prepare runtime workspace paths for one session.

    `kind="tool"`: shared user workspace for file tools.
    `kind="context"`: shared agent context directory.
    `kind="temp"`: session output directory.
    """
    return prepare_session_workspace_kind(user_id, session_id, kind=kind)


def prepare_session_workspace_kind(user_id: str, session_id: str, *, kind: str) -> Path:
    """Create and hydrate the user workspace and requested runtime directory.

    The function is intentionally idempotent: callers can invoke it on every
    request without worrying about whether the workspace already exists.
    """
    root = repo_root()
    user_workspace = user_workspace_path(user_id)
    agent_workspace = agent_context_path(user_id)
    permanent_workspace = permanent_workspace_path(user_id)
    temp_workspace = runtime_session_workspace_path(user_id, session_id)

    user_workspace.mkdir(parents=True, exist_ok=True)
    agent_workspace.mkdir(parents=True, exist_ok=True)
    permanent_workspace.mkdir(parents=True, exist_ok=True)
    temp_workspace.mkdir(parents=True, exist_ok=True)
    _sync_workspace_templates(agent_workspace)
    _copy_workspace_skills(root, agent_workspace)
    _ensure_session_shared_links(
        temp_workspace,
        permanent_workspace=permanent_workspace,
        context_workspace=agent_workspace,
    )

    if kind == "tool":
        workspace = user_workspace
    elif kind == "context":
        workspace = agent_workspace
    elif kind == "temp":
        workspace = temp_workspace
    else:
        raise ValueError(f"Unknown workspace kind: {kind}")

    return workspace
