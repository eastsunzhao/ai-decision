"""Workspace file helpers for the browser workbench.

The Flask routes stay in ``web.app``; this module owns the filesystem boundary
logic those routes rely on.
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

if __package__:
    from .workbench import (
        ACTIVATABLE_SKILLS,
        EDITABLE_TEXT_EXTENSIONS,
        HIDDEN_WORKSPACE_NAMES,
        INLINE_TEXT_SIZE_LIMIT,
        normalize_relative_path,
        resolve_workspace_path,
    )
else:
    from workbench import (
        ACTIVATABLE_SKILLS,
        EDITABLE_TEXT_EXTENSIONS,
        HIDDEN_WORKSPACE_NAMES,
        INLINE_TEXT_SIZE_LIMIT,
        normalize_relative_path,
        resolve_workspace_path,
    )


ARTIFACT_LIMIT = 25
ArtifactSnapshot = dict[str, tuple[int, int]]


def _safe_segment(value: str, fallback: str) -> str:
    """Sanitize user-controlled strings before using them as path segments."""
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", (value or "").strip()).strip("._")
    return cleaned or fallback


def session_temp_root_relative_path(session_id: str) -> str:
    """Return the visible workspace path for a session's temp root."""
    return f"temp/{_safe_segment(session_id, 'default_session')}"


def _read_utf8_file(path: Path) -> str | None:
    """Return UTF-8 text or ``None`` for binary/non-UTF-8 content."""
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return None


def _timestamp_for_path(path: Path) -> str:
    """Serialize a file mtime for the browser workspace payload."""
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def _path_is_hidden(path: Path) -> bool:
    """Return whether a runtime bookkeeping file should be hidden from the UI."""
    return path.name in HIDDEN_WORKSPACE_NAMES


def _path_has_hidden_component(relative_path: str) -> bool:
    """Return whether a normalized workspace path contains hidden/system parts."""
    for part in Path(*normalize_relative_path(relative_path).split("/")).parts:
        if part in HIDDEN_WORKSPACE_NAMES or part.startswith("."):
            return True
    return False


def _sort_directory_entries(entries: list[Path]) -> list[Path]:
    """Sort directories first, then files, for a stable workbench tree."""
    return sorted(entries, key=lambda item: (not item.is_dir(), item.name.lower(), item.name))


def _build_tree_node(base: Path, current: Path) -> dict[str, Any]:
    """Recursively serialize a workspace subtree for the file browser."""
    rel_path = "" if current == base else current.relative_to(base).as_posix()
    # Never recurse through symlinks from the browser tree. A symlinked
    # directory can point outside the workspace and leak unrelated paths.
    if current.is_symlink():
        return {
            "name": current.name,
            "path": rel_path,
            "kind": "symlink_directory" if current.is_dir() else "symlink_file",
        }
    if current.is_dir() and not current.is_symlink():
        children = [
            _build_tree_node(base, item)
            for item in _sort_directory_entries(
                [entry for entry in current.iterdir() if not _path_is_hidden(entry) and not entry.is_symlink()]
            )
        ]
        return {
            "name": current.name if rel_path else "workspace",
            "path": rel_path,
            "kind": "directory",
            "children": children,
        }
    return {
        "name": current.name,
        "path": rel_path,
        "kind": "file",
    }


def _path_is_under_permanent(workspace_root: Path, resolved: Path) -> bool:
    """Return whether a resolved path is inside the visible permanent tree."""
    permanent_root = (workspace_root / "permanent").resolve(strict=False)
    try:
        resolved.resolve(strict=False).relative_to(permanent_root)
    except ValueError:
        return False
    return True


def resolve_permanent_path(workspace_root: Path, relative_path: Any) -> tuple[str, Path] | None:
    """Resolve a browser path and require it to live under ``permanent/``."""
    normalized = normalize_relative_path(relative_path)
    if normalized != "permanent" and not normalized.startswith("permanent/"):
        return None
    resolved = resolve_workspace_path(workspace_root, normalized)
    if resolved is None or not _path_is_under_permanent(workspace_root, resolved):
        return None
    if any(part in HIDDEN_WORKSPACE_NAMES for part in Path(*normalized.split("/")).parts):
        return None
    return normalized, resolved


def resolve_session_temp_path(workspace_root: Path, session_id: str, relative_path: Any) -> tuple[str, Path] | None:
    """Resolve a browser path and require it to live under the current session temp tree."""
    normalized = normalize_relative_path(relative_path)
    temp_prefix = session_temp_root_relative_path(session_id)
    if normalized != temp_prefix and not normalized.startswith(f"{temp_prefix}/"):
        return None
    resolved = resolve_workspace_path(workspace_root, normalized)
    temp_root = (workspace_root / temp_prefix).resolve(strict=False)
    if resolved is None:
        return None
    try:
        resolved.resolve(strict=False).relative_to(temp_root)
    except ValueError:
        return None
    if any(part in HIDDEN_WORKSPACE_NAMES for part in Path(*normalized.split("/")).parts):
        return None
    return normalized, resolved


def resolve_downloadable_path(workspace_root: Path, session_id: str, relative_path: Any) -> tuple[str, Path] | None:
    """Resolve a browser path that may be downloaded by the current session."""
    for resolver in (
        lambda: resolve_permanent_path(workspace_root, relative_path),
        lambda: resolve_session_temp_path(workspace_root, session_id, relative_path),
    ):
        target = resolver()
        if target is None:
            continue
        normalized, resolved = target
        if _path_has_hidden_component(normalized) or resolved.is_symlink():
            return None
        return target
    return None


def copy_destination_path(parent: Path, source: Path) -> Path:
    """Return a copy destination, suffixing only on target-directory conflicts."""
    candidate = parent / source.name
    if not candidate.exists():
        return candidate

    if source.is_dir():
        base_name = source.name
        suffix = ""
    else:
        base_name = source.stem
        suffix = source.suffix

    candidate = parent / f"{base_name} copy{suffix}"
    index = 2
    while candidate.exists():
        candidate = parent / f"{base_name} copy {index}{suffix}"
        index += 1
    return candidate


def valid_workspace_name(value: Any) -> str | None:
    """Return a safe single path segment for browser-created entries."""
    name = str(value or "").strip()
    if not name or name in {".", ".."}:
        return None
    if "/" in name or "\\" in name:
        return None
    if name in HIDDEN_WORKSPACE_NAMES:
        return None
    return name


def unique_child_path(parent: Path, name: str) -> Path:
    """Return a non-existing child path by appending numeric suffixes."""
    candidate = parent / name
    if not candidate.exists():
        return candidate
    path_name = Path(name)
    stem = path_name.stem if path_name.suffix else name
    suffix = path_name.suffix
    index = 2
    while True:
        candidate = parent / f"{stem} {index}{suffix}"
        if not candidate.exists():
            return candidate
        index += 1


def operation_payload(workspace_root: Path, session_id: str, path: Path, kind: str) -> dict[str, Any]:
    """Build a common filesystem operation response."""
    return {
        "tree": build_workspace_tree_payload(workspace_root, session_id),
        "path": path.relative_to(workspace_root).as_posix(),
        "kind": kind,
    }


def build_workspace_tree_payload(workspace_root: Path, session_id: str) -> dict[str, Any]:
    """Build the visible permanent and current-session temp tree payload."""
    temp_root = workspace_root / "temp" / _safe_segment(session_id, "default_session")
    return {
        "name": "workspace",
        "path": "",
        "kind": "directory",
        "children": [
            _build_tree_node(workspace_root, workspace_root / "permanent"),
            _build_tree_node(workspace_root, temp_root),
        ],
    }


def artifact_scan_roots(workspace_root: Path, session_id: str) -> list[Path]:
    """Return session output roots that can produce downloadable artifacts."""
    safe_session = _safe_segment(session_id, "default_session")
    return [
        workspace_root / "temp" / safe_session,
    ]


def iter_artifact_files(workspace_root: Path, session_id: str) -> list[Path]:
    """List ordinary files from visible artifact roots without following symlinks."""
    files: list[Path] = []
    for root in artifact_scan_roots(workspace_root, session_id):
        if not root.exists() or root.is_symlink():
            continue
        for current, dirnames, filenames in os.walk(root, followlinks=False):
            current_path = Path(current)
            try:
                relative_current = current_path.relative_to(workspace_root).as_posix()
            except ValueError:
                continue
            if _path_has_hidden_component(relative_current):
                dirnames[:] = []
                continue
            dirnames[:] = [
                name
                for name in dirnames
                if not _path_has_hidden_component(name) and not (current_path / name).is_symlink()
            ]
            for filename in filenames:
                candidate = current_path / filename
                if candidate.is_symlink():
                    continue
                try:
                    relative_path = candidate.relative_to(workspace_root).as_posix()
                except ValueError:
                    continue
                if _path_has_hidden_component(relative_path):
                    continue
                if candidate.is_file():
                    files.append(candidate)
    return files


def snapshot_artifacts(workspace_root: Path, session_id: str) -> ArtifactSnapshot:
    """Capture size and mtime for files that may be shown as turn artifacts."""
    snapshot: ArtifactSnapshot = {}
    for path in iter_artifact_files(workspace_root, session_id):
        try:
            stat = path.stat()
            relative_path = path.relative_to(workspace_root).as_posix()
        except OSError:
            continue
        snapshot[relative_path] = (stat.st_size, stat.st_mtime_ns)
    return snapshot


def _mtime_ns_to_iso(mtime_ns: int) -> str:
    """Convert an ns-resolution file timestamp to an API-friendly UTC string."""
    return datetime.fromtimestamp(mtime_ns / 1_000_000_000, tz=timezone.utc).isoformat()


def build_artifact_meta(before: ArtifactSnapshot, after: ArtifactSnapshot) -> dict[str, Any] | None:
    """Return assistant metadata for created/modified files, or None when unchanged."""
    artifacts: list[dict[str, Any]] = []
    for relative_path, stat in after.items():
        before_stat = before.get(relative_path)
        if before_stat is None:
            kind = "created"
        elif before_stat != stat:
            kind = "modified"
        else:
            continue
        size, mtime_ns = stat
        artifacts.append(
            {
                "label": Path(relative_path).name,
                "path": relative_path,
                "kind": kind,
                "size": size,
                "mtime": _mtime_ns_to_iso(mtime_ns),
            }
        )

    if not artifacts:
        return None

    artifacts.sort(key=lambda item: (0 if item["kind"] == "created" else 1, item["path"].lower()))
    truncated = len(artifacts) > ARTIFACT_LIMIT
    return {
        "artifacts": artifacts[:ARTIFACT_LIMIT],
        "artifacts_truncated": truncated,
    }


def _strip_frontmatter(content: str) -> str:
    """Remove simple YAML-style frontmatter before summarizing markdown."""
    if content.startswith("---"):
        parts = content.split("\n---", 1)
        if len(parts) == 2:
            remainder = parts[1]
            return remainder.lstrip("\n").strip()
    return content.strip()


def _summarize_markdown(content: str, fallback: str) -> str:
    """Extract a short one-line summary for a skill or markdown file."""
    stripped = _strip_frontmatter(content)
    for raw in stripped.splitlines():
        line = raw.strip().lstrip("#").strip()
        if not line:
            continue
        compact = " ".join(line.split())
        if compact:
            return compact[:180]
    return fallback


def build_file_payload(workspace_root: Path, relative_path: str) -> dict[str, Any]:
    """Build the browser payload for viewing/editing one workspace file.

    This function is the server-side gatekeeper for direct file reads from the
    web UI, so it validates the path, enforces hidden-file rules, and decides
    whether inline editing is safe.
    """
    normalized_path = normalize_relative_path(relative_path)
    resolved = resolve_workspace_path(workspace_root, normalized_path)
    if resolved is None or not normalized_path:
        raise PermissionError(relative_path)
    if not resolved.exists() or not resolved.is_file():
        raise FileNotFoundError(normalized_path or str(relative_path))
    if _path_is_hidden(resolved):
        raise PermissionError(normalized_path)

    size = resolved.stat().st_size
    suffix = resolved.suffix.lower()
    raw = resolved.read_bytes()
    text = None if size > INLINE_TEXT_SIZE_LIMIT else _read_utf8_file(resolved)
    kind = "text" if text is not None else "binary"
    too_large = size > INLINE_TEXT_SIZE_LIMIT
    editable = kind == "text" and not too_large and suffix in EDITABLE_TEXT_EXTENSIONS
    content = text if text is not None and not too_large else ""
    return {
        "path": normalized_path,
        "kind": kind,
        "editable": editable,
        "content": content,
        "size": size,
        "too_large": too_large,
        "mtime": _timestamp_for_path(resolved),
    }


def list_workspace_skills(workspace_root: Path, *, path_prefix: str = "") -> list[dict[str, Any]]:
    """Enumerate workspace-local skills for the workbench sidebar."""
    skills_root = workspace_root / "skills"
    if not skills_root.exists() or not skills_root.is_dir():
        return []

    records: list[dict[str, Any]] = []
    for skill_dir in sorted(skills_root.iterdir(), key=lambda item: item.name.lower()):
        if not skill_dir.is_dir():
            continue
        skill_file = skill_dir / "SKILL.md"
        if not skill_file.exists():
            continue
        content = skill_file.read_text(encoding="utf-8")
        summary = _summarize_markdown(content, skill_dir.name)
        records.append(
            {
                "name": skill_dir.name,
                "path": "/".join(
                    part
                    for part in (path_prefix.strip("/"), skill_file.relative_to(workspace_root).as_posix())
                    if part
                ),
                "summary": summary,
                "activatable": skill_dir.name in ACTIVATABLE_SKILLS,
            }
        )
    return records
