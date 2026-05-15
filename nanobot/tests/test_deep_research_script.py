import importlib.util
from pathlib import Path
from types import SimpleNamespace

def _load_script_module():
    repo_root = Path(__file__).resolve().parents[2]
    script_path = repo_root / "skills" / "deep-research" / "scripts" / "web_research.py"
    spec = importlib.util.spec_from_file_location("deep_research_web_research_script", script_path)
    module = importlib.util.module_from_spec(spec)
    assert spec is not None
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_script_prefers_env_jina_key(monkeypatch, capsys):
    import asyncio

    module = _load_script_module()
    config = SimpleNamespace(
        tools=SimpleNamespace(web=SimpleNamespace(proxy="http://proxy.example")),
        agents=SimpleNamespace(defaults=SimpleNamespace(model="test-model", max_tokens=2048, reasoning_effort=None)),
    )

    monkeypatch.setenv("JINA_API_KEY", "env-key")
    monkeypatch.setattr(module, "_load_config", lambda: config)
    monkeypatch.setattr(module, "_create_provider", lambda cfg: SimpleNamespace(name="provider"))
    monkeypatch.setattr(module, "_resolve_jina_api_key", lambda cfg: "env-key")

    captured = {}

    async def fake_run_web_research(**kwargs):
        captured.update(kwargs)
        return "Research results for: nvidia"

    monkeypatch.setattr(module, "_run_web_research", fake_run_web_research)

    exit_code = asyncio.run(module.run(["--query", "nvidia", "--focus", "Find revenue"]))

    assert exit_code == 0
    assert captured["api_key"] == "env-key"
    assert captured["proxy"] == "http://proxy.example"
    assert captured["model"] == "test-model"
    assert "Research results for: nvidia" in capsys.readouterr().out


def test_script_falls_back_to_config_jina_key(monkeypatch, capsys):
    import asyncio

    module = _load_script_module()
    config = SimpleNamespace(
        tools=SimpleNamespace(web=SimpleNamespace(proxy=None)),
        agents=SimpleNamespace(defaults=SimpleNamespace(model="test-model", max_tokens=2048, reasoning_effort=None)),
    )

    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.setattr(module, "_load_config", lambda: config)
    monkeypatch.setattr(module, "_create_provider", lambda cfg: SimpleNamespace(name="provider"))
    monkeypatch.setattr(module, "_resolve_jina_api_key", lambda cfg: "config-key")

    captured = {}

    async def fake_run_web_research(**kwargs):
        captured.update(kwargs)
        return "Research results for: fallback"

    monkeypatch.setattr(module, "_run_web_research", fake_run_web_research)

    exit_code = asyncio.run(module.run(["--query", "nvidia", "--focus", "Find revenue"]))

    assert exit_code == 0
    assert captured["api_key"] == "config-key"
    assert "Research results for: fallback" in capsys.readouterr().out


def test_script_errors_when_jina_key_missing(monkeypatch, capsys):
    import asyncio

    module = _load_script_module()
    config = SimpleNamespace()

    monkeypatch.delenv("JINA_API_KEY", raising=False)
    monkeypatch.setattr(module, "_load_config", lambda: config)
    monkeypatch.setattr(module, "_resolve_jina_api_key", lambda cfg: "")
    monkeypatch.setattr(module, "_missing_jina_api_key_message", lambda: "Error: Jina Search API key not configured. Configure tools.web.search.apiKey.")

    exit_code = asyncio.run(module.run(["--query", "nvidia", "--focus", "Find revenue"]))
    output = capsys.readouterr().out

    assert exit_code == 1
    assert "Jina Search API key not configured" in output
    assert "tools.web.search.apiKey" in output


def test_configure_stdio_uses_utf8_when_supported():
    module = _load_script_module()

    class FakeStream:
        def __init__(self):
            self.calls = []

        def reconfigure(self, **kwargs):
            self.calls.append(kwargs)

    old_stdout = module.sys.stdout
    old_stderr = module.sys.stderr
    try:
        stdout = FakeStream()
        stderr = FakeStream()
        module.sys.stdout = stdout
        module.sys.stderr = stderr

        module._configure_stdio()

        assert stdout.calls == [{"encoding": "utf-8", "errors": "replace"}]
        assert stderr.calls == [{"encoding": "utf-8", "errors": "replace"}]
    finally:
        module.sys.stdout = old_stdout
        module.sys.stderr = old_stderr


def test_configure_logging_reduces_loguru_to_errors(monkeypatch):
    module = _load_script_module()

    class FakeLogger:
        def __init__(self):
            self.removed = 0
            self.add_calls = []

        def remove(self):
            self.removed += 1

        def add(self, sink, level):
            self.add_calls.append((sink, level))

    fake_logger = FakeLogger()

    import types
    import sys

    old_loguru = sys.modules.get("loguru")
    sys.modules["loguru"] = types.SimpleNamespace(logger=fake_logger)
    try:
        module._configure_logging()
    finally:
        if old_loguru is not None:
            sys.modules["loguru"] = old_loguru
        else:
            del sys.modules["loguru"]

    assert fake_logger.removed == 1
    assert fake_logger.add_calls == [(module.sys.stderr, "ERROR")]


def test_write_line_prefers_utf8_buffer():
    module = _load_script_module()

    class FakeBuffer:
        def __init__(self):
            self.data = b""
            self.flushed = False

        def write(self, value):
            self.data += value

        def flush(self):
            self.flushed = True

    class FakeStdout:
        def __init__(self):
            self.buffer = FakeBuffer()

    old_stdout = module.sys.stdout
    try:
        stdout = FakeStdout()
        module.sys.stdout = stdout

        module._write_line("Bullet: • 中文 OK")

        assert stdout.buffer.data == "Bullet: • 中文 OK\n".encode("utf-8")
        assert stdout.buffer.flushed is True
    finally:
        module.sys.stdout = old_stdout
