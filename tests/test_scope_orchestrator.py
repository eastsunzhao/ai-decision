from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from skills_scope.orchestrator import (
    build_broad_query,
    build_scope_command,
    evidence_needs_fallback,
    evidence_is_satisfactory,
    format_scope_evidence_runtime,
    plan_scope_query,
    query_rewriter,
    run_scope_query,
    run_scope_query_adaptive,
    run_scope_query_smart,
)


def test_build_scope_command_rejects_removed_web_scope():
    try:
        build_scope_command("web", "锂电行业")
    except ValueError as exc:
        assert "Unsupported scope" in str(exc)
    else:
        raise AssertionError("Expected ValueError for removed web scope")


def test_build_scope_command_mid_platform():
    cmd = build_scope_command("mid_platform", "宁德时代")
    joined = " ".join(cmd)
    assert "query_mid_platform.py" in joined
    assert "--keyword" in cmd
    assert "--format" in cmd
    assert "--include-content" in cmd
    assert cmd[cmd.index("--max-content-chars") + 1] == "1000"


def test_build_scope_command_news_contains_q():
    cmd = build_scope_command("news", "NAND")
    joined = " ".join(cmd)
    assert "query_news.py" in joined
    assert "--q" in cmd
    assert "NAND" in cmd


def test_build_scope_command_prefers_web_posix_python_over_nb_python(monkeypatch):
    web_python = str((Path.cwd() / "fake_web_python").resolve())
    nb_python = str((Path.cwd() / "fake_nb_python").resolve())
    monkeypatch.setenv("WEB_POSIX_PYTHON", web_python)
    monkeypatch.setenv("NB_PYTHON", nb_python)
    monkeypatch.setattr(
        "skills_scope.orchestrator.os.path.isfile",
        lambda p: p in {web_python, nb_python},
    )

    cmd = build_scope_command("mid_platform", "test")
    assert cmd[0] == web_python


def test_build_scope_command_uses_virtual_env_python_when_env_present(monkeypatch):
    virtual_env = (Path.cwd() / "fake_venv").resolve()
    venv_python = str((virtual_env / "bin" / "python").resolve())
    monkeypatch.delenv("WEB_POSIX_PYTHON", raising=False)
    monkeypatch.delenv("NB_PYTHON", raising=False)
    monkeypatch.setenv("VIRTUAL_ENV", str(virtual_env))
    monkeypatch.setattr("skills_scope.orchestrator.os.path.isfile", lambda p: p == venv_python)

    cmd = build_scope_command("mid_platform", "test")
    assert cmd[0] == venv_python


def test_run_scope_query_normalizes_items(monkeypatch):
    fake_stdout = json.dumps(
        {
            "items": [
                {
                    "title": "A",
                    "source": "S",
                    "url": "https://x",
                    "pubTime": "2026-01-01",
                    "contentPreview": "P",
                }
            ]
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query("mid_platform", "测试")
    assert result["exit_code"] == 0
    assert result["parse_error"] is None
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "A"


def test_run_scope_query_emits_subprocess_output_with_300_char_cap(monkeypatch):
    long_stdout = "A" * 800
    long_stderr = "B" * 800
    captured: list[dict[str, object]] = []

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=long_stdout, stderr=long_stderr)

    def _on_subprocess(payload: dict[str, object]):
        captured.append(payload)

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    run_scope_query(
        "mid_platform",
        "test query",
        on_subprocess_output=_on_subprocess,
        round_id="R2.1",
    )

    assert len(captured) == 1
    payload = captured[0]
    assert payload["round"] == "R2.1"
    assert isinstance(payload["stdout"], str)
    assert isinstance(payload["stderr"], str)
    assert len(payload["stdout"]) <= 300
    assert len(payload["stderr"]) <= 300
    assert str(payload["stdout"]).endswith("...[truncated]")
    assert str(payload["stderr"]).endswith("...[truncated]")


def test_run_scope_query_normalizes_forum_result_forum_list(monkeypatch):
    fake_stdout = json.dumps(
        {
            "result": {
                "forumList": [
                    {
                        "title": "海力士调研",
                        "platform": "论坛",
                        "summary": "摘要内容",
                    }
                ]
            }
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query("forum", "海力士")
    assert result["exit_code"] == 0
    assert result["parse_error"] is None
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "海力士调研"
    assert result["items"][0]["source"] == "论坛"


def test_run_scope_query_normalizes_news_items(monkeypatch):
    fake_stdout = json.dumps(
        {
            "items": [
                {
                    "title": "NAND market update",
                    "url": "https://news.example/a",
                    "pub_time": "2026-03-31T00:00:00Z",
                    "finding": "Matched 1 term(s): nand.",
                    "raw": {
                        "site_name": "Example News",
                        "summary": "NAND pricing trend ...",
                    },
                }
            ]
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query("news", "NAND")
    assert result["exit_code"] == 0
    assert result["parse_error"] is None
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "NAND market update"
    assert result["items"][0]["source"] == "Example News"


def test_run_scope_query_news_zero_rows_not_data_satisfactory(monkeypatch):
    fake_stdout = json.dumps(
        {
            "rows_fetched": 0,
            "items": [],
            "q": "test query",
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query("news", "anything")
    assert result["exit_code"] == 0
    assert len(result["items"]) == 1
    assert result["items"][0]["title"] == "No news articles returned"
    assert result["items"][0]["raw"]["kind"] == "no_hits"
    assert result["data_satisfactory"] is False
    assert result["data_failure_reason"] == "no_hits"
    assert evidence_is_satisfactory(result) is False


def test_run_scope_query_news_all_relevance_none_not_satisfactory(monkeypatch):
    fake_stdout = json.dumps(
        {
            "items": [
                {
                    "title": "Irrelevant hit",
                    "url": "https://news.example/a",
                    "pub_time": "2026-03-31T00:00:00Z",
                    "finding": "Matched year 2018 only.",
                    "relevance": "none",
                    "raw": {"site_name": "Example News"},
                },
                {
                    "title": "Another miss",
                    "url": "https://news.example/b",
                    "relevance": "none",
                    "raw": {"site_name": "Example News"},
                },
            ]
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query("news", "US Iran 2018")
    assert result["data_satisfactory"] is False
    assert result["data_failure_reason"] == "all_irrelevant"
    assert result["relevance_summary"]["none"] >= 2


def test_format_scope_evidence_runtime_contains_policy_fields():
    text = format_scope_evidence_runtime(
        {
            "scope": "forum",
            "query_text": "测试",
            "exit_code": 0,
            "items": [{"title": "T", "source": "SRC", "url": "https://u", "snippet": "abc"}],
            "stderr": "",
            "parse_error": None,
        }
    )
    assert "Scope evidence" in text
    assert "forum" in text
    assert "items=1" in text


def test_plan_scope_query_reuse_for_follow_up():
    state = {
        "evidence_by_scope": {
            "news": {
                "scope": "news",
                "query_text": "nand",
                "exit_code": 0,
                "items": [{"title": "A"}],
            }
        },
        "last_topic_key": "news:nand:stable",
        "cache_strict": {},
        "cache_topic": {},
    }
    plan = plan_scope_query("news", "继续展开一下", state)
    assert plan["strategy"] == "reuse_context"


def test_run_scope_query_smart_reuses_state_without_second_subprocess(monkeypatch, tmp_path):
    calls = {"n": 0}
    fake_stdout = json.dumps(
        {
            "items": [
                {
                    "title": "NAND market update",
                    "source": "S",
                    "url": "https://news.example/a",
                    "pub_time": "2026-03-31T00:00:00Z",
                    "finding": "Matched 1 term(s): nand.",
                    "raw": {"site_name": "Example News"},
                }
            ]
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    state_path = tmp_path / "scope_state.json"
    first = run_scope_query_smart("news", "NAND", state_path=state_path)
    second = run_scope_query_smart("news", "继续", state_path=state_path)
    assert first["meta"]["strategy"] in {"full_search", "partial_search"}
    assert second["meta"]["strategy"] == "reuse_context"
    assert calls["n"] == 1


def test_run_scope_query_smart_topic_cache_hit_on_rephrase(monkeypatch, tmp_path):
    calls = {"n": 0}
    fake_stdout = json.dumps(
        {
            "items": [
                {
                    "title": "SK hynix update",
                    "source": "S",
                    "url": "https://news.example/hynix",
                    "pub_time": "2026-03-31T00:00:00Z",
                    "raw": {"site_name": "Example News"},
                }
            ]
        },
        ensure_ascii=False,
    )

    def _fake_run(*args, **kwargs):
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    state_path = tmp_path / "scope_state.json"
    run_scope_query_smart("news", "海力士进展", state_path=state_path)
    second = run_scope_query_smart("news", "海力士进展怎么看", state_path=state_path)
    assert second["meta"]["strategy"] in {"cache_hit", "reuse_context"}
    assert calls["n"] == 1


def test_run_scope_query_partial_mode_news_command(monkeypatch):
    captured: dict[str, object] = {}
    fake_stdout = json.dumps({"items": []}, ensure_ascii=False)

    def _fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        return SimpleNamespace(returncode=0, stdout=fake_stdout, stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    run_scope_query("news", "NAND", query_mode="partial")
    cmd = captured["cmd"]
    assert isinstance(cmd, list)
    assert "--max-pages" in cmd
    assert "--include-full-text" in cmd


def test_query_rewriter_returns_multiple_variants():
    variants = query_rewriter("请分析储能市场份额、供应链风险与未来趋势", "default", k=3)
    assert isinstance(variants, list)
    assert 1 <= len(variants) <= 3
    assert len(set(v.lower() for v in variants)) == len(variants)


def test_build_broad_query_not_empty():
    broad = build_broad_query("2024-2026 中国储能电芯供应链与市场份额", "default")
    assert isinstance(broad, str)
    assert broad.strip() != ""


def test_evidence_needs_fallback_when_domain_count_low():
    evidence = {
        "exit_code": 0,
        "items": [
            {"title": "A", "url": "https://same.example/a", "relevance": "high"},
            {"title": "B", "url": "https://same.example/b", "relevance": "high"},
            {"title": "C", "url": "https://same.example/c", "relevance": "medium"},
        ],
    }
    assert evidence_needs_fallback(evidence, min_results=3, min_domains=2) is True


def test_run_scope_query_adaptive_hits_rewrite_round(monkeypatch):
    outputs = [
        json.dumps({"items": []}, ensure_ascii=False),
        json.dumps(
            {
                "items": [
                    {"title": "A", "source": "S1", "url": "https://a.example/1", "summary": "x", "relevance": "high"},
                    {"title": "B", "source": "S2", "url": "https://b.example/1", "summary": "y", "relevance": "medium"},
                    {"title": "C", "source": "S3", "url": "https://c.example/1", "summary": "z", "relevance": "medium"},
                ]
            },
            ensure_ascii=False,
        ),
    ]
    calls = {"n": 0}

    def _fake_run(*args, **kwargs):
        idx = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout=outputs[idx], stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query_adaptive("mid_platform", "储能供应链风险", rewrite_count=3, min_results=3, min_domains=2)
    assert result["query_round"] in {"R2", "R3"}
    assert isinstance(result.get("adaptive_rounds"), list)
    assert len(result["adaptive_rounds"]) >= 2
    first_round = result["adaptive_rounds"][0]
    assert isinstance(first_round, dict)
    assert "evidence" not in first_round
    assert "item_count" in first_round
    assert "top_urls" in first_round


def test_run_scope_query_adaptive_plan_first_consumes_planned_rewrites_in_order(monkeypatch):
    outputs = [
        json.dumps({"items": []}, ensure_ascii=False),
        json.dumps({"items": []}, ensure_ascii=False),
        json.dumps(
            {
                "items": [
                    {"title": "A", "source": "S1", "url": "https://a.example/1", "summary": "x", "relevance": "high"},
                    {"title": "B", "source": "S2", "url": "https://b.example/1", "summary": "y", "relevance": "medium"},
                    {"title": "C", "source": "S3", "url": "https://c.example/1", "summary": "z", "relevance": "medium"},
                ]
            },
            ensure_ascii=False,
        ),
    ]
    calls = {"n": 0}
    queries: list[str] = []

    def _fake_run(cmd, **kwargs):
        if "--keyword" in cmd:
            queries.append(str(cmd[cmd.index("--keyword") + 1]))
        idx = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout=outputs[idx], stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query_adaptive(
        "mid_platform",
        "base message",
        rewrite_count=3,
        min_results=3,
        min_domains=2,
        rewrite_policy="plan_first",
        planned_primary="primary query",
        planned_rewrites=["relax one", "relax two"],
    )

    assert queries[:3] == ["primary query", "relax one", "relax two"]
    assert result["query_round"] == "R2"
    assert result["query_used"] == "relax two"
    assert result["planned_rewrites_used"] == 2


def test_run_scope_query_adaptive_plan_only_respects_must_keep_terms(monkeypatch):
    outputs = [
        json.dumps({"items": []}, ensure_ascii=False),
        json.dumps(
            {
                "items": [
                    {"title": "A", "source": "S1", "url": "https://a.example/1", "summary": "x", "relevance": "high"},
                ]
            },
            ensure_ascii=False,
        ),
    ]
    calls = {"n": 0}
    queries: list[str] = []

    def _fake_run(cmd, **kwargs):
        if "--keyword" in cmd:
            queries.append(str(cmd[cmd.index("--keyword") + 1]))
        idx = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout=outputs[idx], stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query_adaptive(
        "mid_platform",
        "fallback message",
        rewrite_count=3,
        min_results=1,
        min_domains=1,
        rewrite_policy="plan_only",
        planned_primary="nand price trend",
        planned_rewrites=["storage price trend", "nand spot trend"],
        must_keep_terms=["nand"],
    )

    assert queries == ["nand price trend", "nand spot trend"]
    assert result["query_used"] == "nand spot trend"
    skips = result.get("rewrite_skips")
    assert isinstance(skips, list)
    assert any((row.get("query") == "storage price trend") for row in skips if isinstance(row, dict))


def test_run_scope_query_adaptive_without_planned_primary_uses_message_query(monkeypatch):
    outputs = [
        json.dumps(
            {
                "items": [
                    {"title": "A", "source": "S1", "url": "https://a.example/1", "summary": "x", "relevance": "high"},
                    {"title": "B", "source": "S2", "url": "https://b.example/1", "summary": "y", "relevance": "medium"},
                    {"title": "C", "source": "S3", "url": "https://c.example/1", "summary": "z", "relevance": "medium"},
                ]
            },
            ensure_ascii=False,
        )
    ]
    calls = {"n": 0}
    queries: list[str] = []

    def _fake_run(cmd, **kwargs):
        if "--keyword" in cmd:
            queries.append(str(cmd[cmd.index("--keyword") + 1]))
        idx = min(calls["n"], len(outputs) - 1)
        calls["n"] += 1
        return SimpleNamespace(returncode=0, stdout=outputs[idx], stderr="")

    monkeypatch.setattr("skills_scope.orchestrator.subprocess.run", _fake_run)
    result = run_scope_query_adaptive(
        "mid_platform",
        "specific hvdc query",
        rewrite_count=3,
        min_results=3,
        min_domains=2,
        rewrite_policy="plan_first",
        planned_primary=None,
        planned_rewrites=[],
    )

    assert queries == ["specific hvdc query"]
    assert result["query_round"] == "R1"
    assert result["query_used"] == "specific hvdc query"
