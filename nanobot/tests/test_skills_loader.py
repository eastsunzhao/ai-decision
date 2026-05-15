from pathlib import Path

from nanobot.agent.skills import SkillsLoader


def test_disabled_skills_are_filtered_from_list_and_load(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    skill_dir = workspace / "skills" / "custom-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: custom-skill\n---\n\n# Custom\n", encoding="utf-8")

    loader = SkillsLoader(workspace, disabled_skills=["custom-skill", "memory"])

    names = [skill["name"] for skill in loader.list_skills(filter_unavailable=False)]

    assert "custom-skill" not in names
    assert "memory" not in names
    assert loader.load_skill("custom-skill") is None
    assert loader.load_skill("memory") is None


def test_enabled_builtin_skill_is_still_available(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir(parents=True)

    loader = SkillsLoader(workspace, disabled_skills=["memory"])

    names = [skill["name"] for skill in loader.list_skills(filter_unavailable=False)]

    assert "summarize" in names


def test_workspace_deep_research_skill_prefers_script_entrypoint() -> None:
    repo_root = Path(__file__).resolve().parents[2]
    skill_path = repo_root / "skills" / "deep-research" / "SKILL.md"

    content = skill_path.read_text(encoding="utf-8")

    assert "python skills/deep-research/scripts/web_research.py" in content
    assert "`web_fetch`" in content
    assert "`web_search`" in content
