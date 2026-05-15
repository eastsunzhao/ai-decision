from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills_scope.registry import list_scopes, run_scope


def test_registry_lists_expected_scopes():
    assert list_scopes() == ["forum", "mid_platform", "news"]


def test_registry_rejects_unsupported_scope():
    try:
        run_scope("invalid_scope", [])
    except ValueError as exc:
        assert "Unsupported scope" in str(exc)
    else:
        raise AssertionError("Expected ValueError for unsupported scope")
