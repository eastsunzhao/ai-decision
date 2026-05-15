import json

from nanobot.config.loader import save_config
from nanobot.config.schema import Config


def test_save_config_uses_snake_case_keys(tmp_path):
    config = Config()
    config.agents.defaults.disabled_skills = ["tmux"]
    config.providers.azure_openai.api_key = "test-key"
    config.tools.web.search.api_key = "search-key"
    config.tools.sync_workspace_skills = True
    path = tmp_path / "config.json"

    save_config(config, path)

    data = json.loads(path.read_text(encoding="utf-8"))
    assert "disabled_skills" in data["agents"]["defaults"]
    assert "disabledSkills" not in data["agents"]["defaults"]
    assert "azure_openai" in data["providers"]
    assert "azureOpenai" not in data["providers"]
    assert "api_key" in data["providers"]["azure_openai"]
    assert "send_tool_hints" in data["channels"]
    assert data["tools"]["sync_workspace_skills"] is True
