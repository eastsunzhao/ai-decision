from nanobot.agent.diagnostics import estimate_tokens, format_messages_stats, format_tool_payload


def test_format_tool_payload_short_text() -> None:
    text = "hello world"
    rendered = format_tool_payload("web_search", text, preview_chars=5)

    assert "[Tool payload] web_search:" in rendered
    assert "hello world" in rendered


def test_format_tool_payload_long_text_shows_head_and_tail() -> None:
    text = "a" * 350 + "b" * 350
    rendered = format_tool_payload("exec", text, preview_chars=10)

    assert "aaaaaaaaaa" in rendered
    assert "bbbbbbbbbb" in rendered
    assert "\n...\n" in rendered


def test_format_messages_stats_returns_summary() -> None:
    rendered = format_messages_stats([{"role": "user", "content": "hi"}], label="Prompt stats #1")

    assert rendered.startswith("[Prompt stats #1]")
    assert "messages" in rendered
    assert "tokens" in rendered


def test_estimate_tokens_empty() -> None:
    count, method = estimate_tokens("")

    assert count == 0
    assert method == "empty"
