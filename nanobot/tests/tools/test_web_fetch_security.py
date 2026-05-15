"""Tests for web_fetch SSRF protection and untrusted content marking."""

from __future__ import annotations

import asyncio
import json
import socket
from types import SimpleNamespace
from unittest.mock import patch

from nanobot.agent.tools import web as web_tools
from nanobot.agent.tools.web import WebFetchTool


def _fake_resolve_private(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("169.254.169.254", 0))]


def _fake_resolve_public(hostname, port, family=0, type_=0):
    return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("93.184.216.34", 0))]


def test_web_fetch_blocks_private_ip():
    tool = WebFetchTool()
    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_private):
        result = asyncio.run(tool.execute(url="http://169.254.169.254/computeMetadata/v1/"))
    data = json.loads(result)
    assert "error" in data
    assert "private" in data["error"].lower() or "blocked" in data["error"].lower()


def test_web_fetch_blocks_localhost():
    tool = WebFetchTool()
    def _resolve_localhost(hostname, port, family=0, type_=0):
        return [(socket.AF_INET, socket.SOCK_STREAM, 0, "", ("127.0.0.1", 0))]
    with patch("nanobot.security.network.socket.getaddrinfo", _resolve_localhost):
        result = asyncio.run(tool.execute(url="http://localhost/admin"))
    data = json.loads(result)
    assert "error" in data


def test_web_fetch_result_contains_untrusted_flag():
    """When fetch succeeds, result JSON must include untrusted=True and the banner."""
    tool = WebFetchTool()

    fake_html = "<html><head><title>Test</title></head><body><p>Hello world</p></body></html>"

    import httpx

    class FakeResponse:
        status_code = 200
        url = "https://example.com/page"
        text = fake_html
        headers = {"content-type": "text/html"}
        def raise_for_status(self): pass
        def json(self): return {}

    async def _fake_get(self, url, **kwargs):
        return FakeResponse()

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public), \
         patch("httpx.AsyncClient.get", _fake_get):
        result = asyncio.run(tool.execute(url="https://example.com/page"))

    data = json.loads(result)
    assert data.get("untrusted") is True
    assert "[External content" in data.get("text", "")
    assert data.get("dirtyChecked") is True
    assert data.get("llmCleaned") is False
    assert data.get("sourceExtractor") == data.get("extractor")


def test_web_fetch_jina_dirty_content_uses_llm_cleaner(monkeypatch):
    tool = WebFetchTool()

    class FakeJinaResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "url": "https://example.com/page",
                    "title": "Messy Page",
                    "content": (
                        "<div class='nav'>Menu Subscribe Cookie Privacy Policy</div>"
                        "<span style='display:none'>ad</span>"
                        "<script>tracking()</script>"
                        "<article>Important fact: revenue was 123 million in 2026.</article>"
                    ),
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            assert "r.jina.ai" in url
            return FakeJinaResponse()

    async def fake_cleaner(raw_text, **kwargs):
        assert kwargs["url"] == "https://example.com/page"
        assert kwargs["title"] == "Messy Page"
        assert "Important fact" in raw_text
        return "Important fact: revenue was 123 million in 2026."

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("nanobot.agent.tools.web.llm_based_html_readability", fake_cleaner)

    result = asyncio.run(tool._fetch_jina("https://example.com/page", max_chars=5000))
    data = json.loads(result)

    assert data["sourceExtractor"] == "jina"
    assert data["extractor"] == "jina+llm_readability"
    assert data["dirtyChecked"] is True
    assert data["dirty"] is True
    assert "html_tags" in data["dirtySignals"]
    assert data["llmCleaned"] is True
    assert data["lengthBeforeClean"] > data["lengthAfterClean"]
    assert "Important fact: revenue was 123 million in 2026." in data["text"]


def test_web_fetch_jina_markdown_link_clutter_uses_llm_cleaner(monkeypatch):
    tool = WebFetchTool()

    link_nav = "\n".join(f"* [Menu {i}](https://example.com/nav/{i})" for i in range(45))
    image_nav = "\n".join(f"![Image {i}](https://example.com/image-{i}.png)" for i in range(4))

    class FakeJinaResponse:
        status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            return {
                "data": {
                    "url": "https://example.com/page",
                    "title": "Link Dense Page",
                    "content": (
                        f"{image_nav}\n{link_nav}\n\n"
                        "## Real Article\n\n"
                        "Important fact: the article body survived cleanup."
                    ),
                }
            }

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url, **kwargs):
            assert "r.jina.ai" in url
            return FakeJinaResponse()

    async def fake_cleaner(raw_text, **kwargs):
        assert "Important fact" in raw_text
        return "Important fact: the article body survived cleanup."

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr("nanobot.agent.tools.web.llm_based_html_readability", fake_cleaner)

    result = asyncio.run(tool._fetch_jina("https://example.com/page", max_chars=5000))
    data = json.loads(result)

    assert data["sourceExtractor"] == "jina"
    assert data["extractor"] == "jina+llm_readability"
    assert data["dirty"] is True
    assert "image_markdown" in data["dirtySignals"]
    assert "link_dense" in data["dirtySignals"]
    assert data["llmCleaned"] is True
    assert "Important fact: the article body survived cleanup." in data["text"]


def test_llm_cleaner_uses_deepseek_v4_flash_without_thinking(monkeypatch):
    captured: dict = {}

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def post(self, endpoint, **kwargs):
            captured["endpoint"] = endpoint
            captured["json"] = kwargs["json"]

            class Response:
                def raise_for_status(self):
                    return None

                def json(self):
                    return {"choices": [{"message": {"content": "Cleaned content with enough length to pass validation."}}]}

            return Response()

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)
    monkeypatch.setattr(
        "nanobot.agent.tools.web.load_config",
        lambda: SimpleNamespace(
            providers=SimpleNamespace(
                deepseek=SimpleNamespace(api_key="deepseek-key", api_base="https://api.deepseek.com")
            )
        ),
        raising=False,
    )

    result = asyncio.run(
        web_tools.llm_based_html_readability(
            "short raw text",
            url="https://example.com/page",
            title="Example",
        )
    )

    assert result == "Cleaned content with enough length to pass validation."
    assert captured["json"]["model"] == "deepseek-v4-flash"
    assert "extra_body" not in captured["json"]
    assert "thinking" not in captured["json"]
    assert "reasoning_effort" not in captured["json"]


def test_web_fetch_blocks_private_redirect_before_returning_image(monkeypatch):
    tool = WebFetchTool()

    class FakeStreamResponse:
        headers = {"content-type": "image/png"}
        url = "http://127.0.0.1/secret.png"
        content = b"\x89PNG\r\n\x1a\n"

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def aread(self):
            return self.content

        def raise_for_status(self):
            return None

    class FakeClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        def stream(self, method, url, headers=None):
            return FakeStreamResponse()

    monkeypatch.setattr("nanobot.agent.tools.web.httpx.AsyncClient", FakeClient)

    with patch("nanobot.security.network.socket.getaddrinfo", _fake_resolve_public):
        result = asyncio.run(tool.execute(url="https://example.com/image.png"))

    data = json.loads(result)
    assert "error" in data
    assert "redirect blocked" in data["error"].lower()
