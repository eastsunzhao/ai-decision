from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nanobot"))

from nanobot.agent.tools.shell import ExecTool


def test_scope_policy_no_longer_blocks_scope_scripts() -> None:
    commands = [
        "python skills_scope/query_mid_platform.py --keywords 测试",
        "python -m skills_scope.query_mid_platform --keywords 测试",
        "python skills_scope/query_forum.py --keyword 测试",
        "python -m skills_scope.query_news --q 测试",
    ]
    tool = ExecTool()
    for command in commands:
        blocked = tool._guard_command(command, cwd=str(Path.cwd()))
        assert blocked is None, f"command={command}, blocked={blocked}"


def test_skill_policy_no_longer_blocks_skill_pack_execution() -> None:
    commands = [
        "python runtime/workspaces/1/abc/skills/deep-research/scripts/runner.py",
        "python runtime/workspaces/1/abc/skills/simple/scripts/runner.py",
        "python runtime/workspaces/1/abc/skills/meritco-report-api/scripts/query_news.py",
        r'cd "C:\runtime\workspaces\1\abc\skills\deep-research" && python runner.py',
    ]
    tool = ExecTool()
    for command in commands:
        blocked = tool._guard_command(command, cwd=str(Path.cwd()))
        assert blocked is None, f"command={command}, blocked={blocked}"


def test_dangerous_command_safety_guard_still_blocks() -> None:
    tool = ExecTool()
    blocked = tool._guard_command("rm -rf important-dir", cwd=str(Path.cwd()))
    assert blocked is not None
    assert "dangerous pattern" in blocked
