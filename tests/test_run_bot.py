from __future__ import annotations

from web.run_bot import _format_bwrap_mounts, _status_from_outbound_metadata


def test_bwrap_mounts_shadow_other_user_runtime_dirs() -> None:
    mounts = _format_bwrap_mounts(
        isolated_user_runtime="/repo/runtime/users/alice",
        writable_dirs=["/repo/.nanobot"],
    )

    assert "--tmpfs /repo/runtime/users " in mounts
    assert "--dir /repo/runtime/users/alice " in mounts
    assert "--bind /repo/runtime/users/alice /repo/runtime/users/alice " in mounts
    assert "--bind /repo/.nanobot /repo/.nanobot " in mounts
    assert mounts.index("--tmpfs /repo/runtime/users") < mounts.index("--dir /repo/runtime/users/alice")


def test_bwrap_mounts_do_not_bind_root_by_default() -> None:
    mounts = _format_bwrap_mounts(
        read_only_dirs=["/usr/bin"],
        repo_root="/repo",
        isolated_user_runtime="/repo/runtime/users/alice",
        writable_dirs=["/repo/.nanobot"],
    )

    assert "--ro-bind / /" not in mounts
    assert "--bind / /" not in mounts
    assert "--ro-bind /usr/bin /usr/bin " in mounts


def test_status_from_outbound_metadata_marks_max_iterations_as_error() -> None:
    assert _status_from_outbound_metadata({"_stop_reason": "max_iterations"}) == "error"
    assert _status_from_outbound_metadata({"_stop_reason": "completed"}) == "complete"
