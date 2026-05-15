from pathlib import Path

from nanobot.config.loader import get_config_path, get_data_dir
from nanobot.utils.helpers import get_data_path, get_project_root, get_workspace_path


def test_project_local_paths_stay_inside_repo():
    project_root = get_project_root().resolve()

    assert get_data_path().resolve() == project_root / ".nanobot"
    assert get_data_dir().resolve() == project_root / ".nanobot"
    assert get_config_path().resolve() == project_root / ".nanobot" / "config.json"
    assert get_workspace_path().resolve() == project_root


def test_explicit_workspace_path_is_preserved(tmp_path):
    custom = tmp_path / "workspace"

    assert get_workspace_path(str(custom)).resolve() == custom.resolve()
