from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web import run_bot_worker


def test_pin_python_for_subprocesses_uses_nb_python_parent_without_resolve(monkeypatch) -> None:
    nb_python = "/opt/work/current/../.venv/bin/python"
    expected_dir = str(Path(nb_python).expanduser().parent)

    monkeypatch.setenv("NB_PYTHON", nb_python)
    monkeypatch.setenv("PATH", f"base{os.pathsep}tail")

    run_bot_worker._pin_python_for_subprocesses()

    assert os.environ["NB_PYTHON"] == nb_python
    assert os.environ["PATH"].split(os.pathsep)[0] == expected_dir


def test_status_from_outbound_metadata_marks_max_iterations_as_error() -> None:
    assert run_bot_worker._status_from_outbound_metadata({"_stop_reason": "max_iterations"}) == "error"
    assert run_bot_worker._status_from_outbound_metadata({}) == "complete"
