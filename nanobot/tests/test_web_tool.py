import pytest
from unittest.mock import MagicMock

from nanobot.agent.loop import AgentLoop
from nanobot.agent.tools.web import WebSearchTool
from nanobot.bus.queue import MessageBus


@pytest.mark.anyio
async def test_web_search_jina_missing_key(monkeypatch):
    monkeypatch.delenv("JINA_API_KEY", raising=False)

    tool = WebSearchTool()
    result = await tool.execute("nvidia")

    assert "Jina Search API key not configured" in result
    assert "tools.web.search.apiKey" in result


@pytest.mark.anyio
async def test_web_search_jina_formats_results(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"title": "Result A", "url": "https://a.example", "description": "Alpha snippet", "content": "Alpha body"},
                    {"title": "Result B", "url": "https://b.example", "description": "Beta snippet"},
                ]
            }

    class FakeClient:
        def __init__(self, proxy=None):
            self.proxy = proxy

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, params=None, headers=None, timeout=None):
            captured["url"] = url
            captured["params"] = params
            captured["headers"] = headers
            captured["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setenv("JINA_API_KEY", "jina-test")
    monkeypatch.setattr("nanobot.research.web.httpx.AsyncClient", FakeClient)

    tool = WebSearchTool(max_results=2)
    result = await tool.execute("nvidia")

    assert captured["url"] == "https://s.jina.ai/"
    assert captured["params"] == {"q": "nvidia"}
    assert captured["headers"]["Authorization"] == "Bearer jina-test"
    assert captured["headers"]["X-Respond-With"] == "no-content"
    assert "Results for: nvidia" in result
    assert "Result A" in result
    assert "https://a.example" in result
    assert "Alpha snippet" in result
    assert "Beta snippet" in result
    assert "Alpha body" not in result

class FakeLLMResponse:
    def __init__(self, content):
        self.content = content
        self.tool_calls = []
        self.finish_reason = "stop"
        self.reasoning_content = None
        self.thinking_blocks = None

    @property
    def has_tool_calls(self):
        return False


class FakeProvider:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, tools=None, model=None, max_tokens=4096, temperature=0.7, reasoning_effort=None):
        self.calls.append(messages)
        page = messages[-1]["content"]
        if "Result A" in page:
            return FakeLLMResponse(
                '{"relevance":"high","reason":"Contains the target answer.","answer":"A is directly relevant.","passages":["Revenue grew 20%."]}'
            )
        return FakeLLMResponse(
            '{"relevance":"none","reason":"Not relevant.","answer":"","passages":[]}'
        )


def test_agent_loop_does_not_register_web_research(tmp_path):
    bus = MessageBus()
    provider = MagicMock()
    provider.get_default_model.return_value = "test-model"

    loop = AgentLoop(bus=bus, provider=provider, workspace=tmp_path, model="test-model")

    assert "web_search" in loop.tools.tool_names
    assert "web_fetch" in loop.tools.tool_names
    assert "web_research" not in loop.tools.tool_names
