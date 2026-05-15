from nanobot.agent.context import ContextBuilder


def test_add_tool_result_truncates_runtime_message() -> None:
    builder = ContextBuilder.__new__(ContextBuilder)
    builder._TOOL_RESULT_PROMPT_MAX_CHARS = ContextBuilder._TOOL_RESULT_PROMPT_MAX_CHARS
    messages: list[dict] = []
    content = "x" * (ContextBuilder._TOOL_RESULT_PROMPT_MAX_CHARS + 200)
    out = builder.add_tool_result(messages, "call_1", "read_file", content)
    assert out[0]["role"] == "tool"
    assert len(out[0]["content"]) < len(content)
    assert str(out[0]["content"]).endswith("... (truncated)")
