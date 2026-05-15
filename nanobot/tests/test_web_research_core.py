import pytest

from nanobot.research.web import run_web_research


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


@pytest.mark.anyio
async def test_run_web_research_uses_parallel_analysis(monkeypatch):
    captured = {}

    class FakeResponse:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": [
                    {"title": "Result A", "url": "https://a.example", "content": "Revenue grew 20% in 2025."},
                    {"title": "Result B", "url": "https://b.example", "content": "This page is unrelated."},
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
            return FakeResponse()

    monkeypatch.setattr("nanobot.research.web.httpx.AsyncClient", FakeClient)

    provider = FakeProvider()
    result = await run_web_research(
        query="nvidia revenue",
        focus="Find revenue growth",
        count=2,
        provider=provider,
        model="test-model",
        api_key="jina-test",
    )

    assert captured["headers"]["X-Engine"] == "direct"
    assert len(provider.calls) == 2
    assert "Research results for: nvidia revenue" in result
    assert "[HIGH] Result A" in result
    assert "Revenue grew 20%." in result
    assert "Contains the target answer." in result
