from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nanobot"))

from nanobot.agent.context import ContextBuilder


def _write_skill(workspace: Path, name: str, body: str) -> None:
    skill_dir = workspace / "skills" / name
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")


def test_context_prompt_simple_mode_exposes_only_simple_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "simple", "simple content")
    _write_skill(tmp_path, "deep-research", "deep content")
    _write_skill(tmp_path, "other-skill", "other content")
    ctx = ContextBuilder(tmp_path)

    prompt = ctx.build_system_prompt(skill_names=["simple"])

    assert "# Active Skills" in prompt
    assert "<name>simple</name>" in prompt
    assert "simple content" in prompt
    assert "deep content" not in prompt
    assert "other content" not in prompt


def test_context_prompt_deep_mode_only_exposes_deep_skill(tmp_path: Path) -> None:
    _write_skill(tmp_path, "deep-research", "deep content")
    _write_skill(tmp_path, "other-skill", "other content")
    ctx = ContextBuilder(tmp_path)

    prompt = ctx.build_system_prompt(skill_names=["deep-research"])

    assert "# Active Skills" in prompt
    assert "deep content" in prompt
    assert "<name>deep-research</name>" in prompt
    assert "other content" not in prompt
    assert "<name>other-skill</name>" not in prompt


def test_context_prompt_places_active_skill_before_bootstrap(tmp_path: Path) -> None:
    _write_skill(tmp_path, "deep-research", "deep content")
    (tmp_path / "AGENTS.md").write_text("agent bootstrap content", encoding="utf-8")
    ctx = ContextBuilder(tmp_path)

    prompt = ctx.build_system_prompt(skill_names=["deep-research"])

    assert prompt.index("# Active Skills") < prompt.index("## AGENTS.md")
    assert prompt.index("deep content") < prompt.index("agent bootstrap content")
