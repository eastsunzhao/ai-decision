from __future__ import annotations

from io import BytesIO
import sys
import threading
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from web.app import create_app


def wait_until(predicate, timeout: float = 2.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return predicate()


class FakeRunHandle:
    def __init__(self, *, session_id: str, message: str, emit_trace):
        self.session_id = session_id
        self.message = message
        self.emit_trace = emit_trace

    def run(self):
        self.emit_trace("tool_hint", f'web_search("{self.message}")', None)
        self.emit_trace("progress", "Collecting sources", None)
        self.emit_trace("tool_result", "[Tool payload] web_search: 12 chars, ~3 tokens (chars/4-estimate)", None)
        return type("RunOutcome", (), {"status": "complete", "text": f"done:{self.session_id}:{self.message}"})()

    def stop(self) -> bool:
        return False


class FakeBotRunner:
    def __init__(self):
        self.calls: list[dict] = []

    def start(self, **kwargs):
        self.calls.append(kwargs)
        return FakeRunHandle(
            session_id=kwargs["session_id"],
            message=kwargs["message"],
            emit_trace=kwargs["emit_trace"],
        )


class StreamingRunHandle:
    def __init__(self, *, emit_assistant):
        self.emit_assistant = emit_assistant

    def run(self):
        self.emit_assistant("assistant_delta", "hello ", {"stream_id": "s1"})
        self.emit_assistant("assistant_delta", "world", {"stream_id": "s1"})
        self.emit_assistant("assistant_end", "", {"stream_id": "s1", "resuming": False})
        return type("RunOutcome", (), {"status": "complete", "text": "hello world"})()

    def stop(self) -> bool:
        return False


class StreamingBotRunner:
    def start(self, **kwargs):
        return StreamingRunHandle(emit_assistant=kwargs["emit_assistant"])


class StreamingThenFinalRunHandle:
    def __init__(self, *, emit_assistant):
        self.emit_assistant = emit_assistant

    def run(self):
        self.emit_assistant("assistant_delta", "Starting S1 and S2", {"stream_id": "s2"})
        self.emit_assistant("assistant_end", "", {"stream_id": "s2", "resuming": False})
        return type("RunOutcome", (), {"status": "complete", "text": "Final synthesized answer"})()

    def stop(self) -> bool:
        return False


class StreamingThenFinalBotRunner:
    def start(self, **kwargs):
        return StreamingThenFinalRunHandle(emit_assistant=kwargs["emit_assistant"])


class ErrorRunHandle:
    def run(self):
        return type("RunOutcome", (), {"status": "error", "text": "Reached max tool iterations."})()

    def stop(self) -> bool:
        return False


class ErrorBotRunner:
    def start(self, **kwargs):
        return ErrorRunHandle()


class WorkspaceMutatingRunHandle:
    def __init__(self, *, user_id: str, session_id: str, action):
        self.user_id = user_id
        self.session_id = session_id
        self.action = action

    def run(self):
        self.action(self.user_id, self.session_id)
        return type("RunOutcome", (), {"status": "complete", "text": "mutated workspace"})()

    def stop(self) -> bool:
        return False


class WorkspaceMutatingBotRunner:
    def __init__(self, action):
        self.action = action

    def start(self, **kwargs):
        return WorkspaceMutatingRunHandle(
            user_id=kwargs["user_id"],
            session_id=kwargs["session_id"],
            action=self.action,
        )


class FakeTitleGenerator:
    def generate(self, message: str) -> str | None:
        return f"title:{message}"


class PlaceholderTitleGenerator:
    def generate(self, message: str) -> str | None:
        return "New Chat"


class BlockingRunHandle:
    def __init__(self, *, session_id: str, message: str, emit_trace, owner):
        self.session_id = session_id
        self.message = message
        self.emit_trace = emit_trace
        self.owner = owner
        self._stopped = threading.Event()

    def run(self):
        self.owner.started.set()
        self.emit_trace("progress", f"running:{self.session_id}", None)
        while True:
            if self._stopped.is_set():
                return type("RunOutcome", (), {"status": "stopped", "text": "Stopped by user."})()
            if self.owner.release.wait(timeout=0.05):
                return type("RunOutcome", (), {"status": "complete", "text": f"finished:{self.message}"})()

    def stop(self) -> bool:
        self._stopped.set()
        return True


class BlockingBotRunner:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.calls: list[dict] = []

    def start(self, **kwargs):
        self.calls.append(kwargs)
        return BlockingRunHandle(
            session_id=kwargs["session_id"],
            message=kwargs["message"],
            emit_trace=kwargs["emit_trace"],
            owner=self,
        )


@pytest.fixture()
def app_factory(tmp_path: Path):
    def _make(bot_runner, max_workers=4, title_generator=None):
        app = create_app(
            runtime_dir=tmp_path / "runtime",
            bot_runner=bot_runner,
            title_generator=title_generator or FakeTitleGenerator(),
            max_workers=max_workers,
        )
        app.config.update(TESTING=True)
        return app

    return _make


def _session_workspace(app, session_id: str, user_id: str = "10001") -> Path:
    store = app.config["RUNTIME_STORE"]
    return store._session_workspace(user_id, session_id)


def test_chat_stream_persists_trace_and_forwards_workbench_context(app_factory):
    runner = FakeBotRunner()
    app = app_factory(runner)
    client = app.test_client()

    session = client.post("/api/sessions", json={}).get_json()
    session_id = session["id"]

    chat_response = client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "deep research",
            "context": {
                "active_path": "AGENTS.md",
                "active_skill": "deep-research",
                "center_view": "file",
            },
        },
    )
    assert chat_response.status_code == 200
    payload = chat_response.get_json()
    job_id = payload["job_id"]
    assert payload["assistant_message_id"]
    assert payload["title"] == "title:deep research"

    stream = client.get(f"/api/stream/{job_id}")
    body = b"".join(stream.response).decode("utf-8")
    assert "event: start" in body
    assert "event: tool_hint" in body
    assert "event: progress" in body
    assert "event: tool_result" in body
    assert "event: done" in body

    history = client.get(f"/api/history/{session_id}").get_json()
    assert len(history) == 2
    assert history[1]["content"] == f"done:{session_id}:deep research"
    assert [item["type"] for item in history[1]["trace"]] == ["tool_hint", "progress", "tool_result"]

    assert len(runner.calls) == 1
    call = runner.calls[0]
    assert call["context"] == {
        "active_path": "AGENTS.md",
        "active_skill": "deep-research",
        "center_view": "file",
    }


def test_chat_stream_forwards_assistant_deltas(app_factory):
    app = app_factory(StreamingBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    chat_response = client.post("/api/chat", json={"session_id": session_id, "message": "stream"})
    job_id = chat_response.get_json()["job_id"]
    body = b"".join(client.get(f"/api/stream/{job_id}").response).decode("utf-8")

    assert body.count("event: assistant_delta") == 2
    assert "hello " in body
    assert "world" in body
    assert "event: assistant_end" in body
    assert "event: done" in body

    history = client.get(f"/api/history/{session_id}").get_json()
    assert history[-1]["content"] == "hello world"


def test_chat_stream_preserves_streamed_assistant_message_before_distinct_final(app_factory):
    app = app_factory(StreamingThenFinalBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    chat_response = client.post("/api/chat", json={"session_id": session_id, "message": "stream"})
    job_id = chat_response.get_json()["job_id"]
    body = b"".join(client.get(f"/api/stream/{job_id}").response).decode("utf-8")

    assert "event: assistant_delta" in body
    assert "event: done" in body

    history = client.get(f"/api/history/{session_id}").get_json()
    assert history[-2]["content"] == "Starting S1 and S2"
    assert history[-2]["status"] == "complete"
    assert history[-1]["content"] == "Final synthesized answer"
    assert history[-1]["trace"] == []


def test_chat_turn_records_created_temp_file_artifact(app_factory):
    def write_report(user_id: str, session_id: str) -> None:
        from web.session_workspace import prepare_session_workspace

        temp = prepare_session_workspace(user_id, session_id, kind="temp")
        (temp / "report.md").write_text("# Report", encoding="utf-8")

    app = app_factory(WorkspaceMutatingBotRunner(write_report))
    client = app.test_client()
    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    job_id = client.post("/api/chat", json={"session_id": session_id, "message": "write report"}).get_json()["job_id"]
    body = b"".join(client.get(f"/api/stream/{job_id}").response).decode("utf-8")
    assert "event: done" in body
    assert '"meta"' in body

    history = client.get(f"/api/history/{session_id}").get_json()
    artifacts = history[-1]["meta"]["artifacts"]
    assert artifacts == [
        {
            "label": "report.md",
            "path": f"temp/{session_id}/report.md",
            "kind": "created",
            "size": len("# Report"),
            "mtime": artifacts[0]["mtime"],
        }
    ]
    assert history[-1]["meta"]["artifacts_truncated"] is False


def test_chat_turn_records_modified_file_artifact(app_factory):
    def update_report(user_id: str, session_id: str) -> None:
        from web.session_workspace import prepare_session_workspace

        temp = prepare_session_workspace(user_id, session_id, kind="temp")
        (temp / "report.md").write_text("# Updated report", encoding="utf-8")

    app = app_factory(WorkspaceMutatingBotRunner(update_report))
    client = app.test_client()
    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    (_session_workspace(app, session_id) / "report.md").write_text("# Draft", encoding="utf-8")

    job_id = client.post("/api/chat", json={"session_id": session_id, "message": "update report"}).get_json()["job_id"]
    _ = b"".join(client.get(f"/api/stream/{job_id}").response)

    history = client.get(f"/api/history/{session_id}").get_json()
    artifacts = history[-1]["meta"]["artifacts"]
    assert len(artifacts) == 1
    assert artifacts[0]["path"] == f"temp/{session_id}/report.md"
    assert artifacts[0]["kind"] == "modified"
    assert artifacts[0]["size"] == len("# Updated report")


def test_chat_turn_without_file_changes_has_no_artifact_meta(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()
    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    job_id = client.post("/api/chat", json={"session_id": session_id, "message": "no files"}).get_json()["job_id"]
    _ = b"".join(client.get(f"/api/stream/{job_id}").response)

    history = client.get(f"/api/history/{session_id}").get_json()
    assert "meta" not in history[-1]


def test_chat_title_falls_back_when_generator_returns_placeholder(app_factory):
    app = app_factory(FakeBotRunner(), title_generator=PlaceholderTitleGenerator())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    response = client.post("/api/chat", json={"session_id": session_id, "message": "minimax的业务中心是什么？"})

    assert response.status_code == 200
    assert response.get_json()["title"] == "minimax的业务中心是什么？"
    assert client.get("/api/sessions").get_json()[0]["title"] == "minimax的业务中心是什么？"


def test_list_sessions_backfills_default_title_from_first_user_message(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()
    store = app.config["RUNTIME_STORE"]

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store._write_json(
        store._chat_path("10001", session_id),
        [
            {
                "id": "msg-1",
                "role": "user",
                "content": "智谱目前最大的客户来源是什么？",
                "created_at": "2026-04-29T12:12:52+00:00",
                "status": "complete",
            }
        ],
    )

    sessions = client.get("/api/sessions").get_json()

    assert sessions[0]["title"] == "智谱目前最大的客户来源是什么？"


def test_running_history_includes_inflight_trace_checkpoints(app_factory):
    runner = BlockingBotRunner()
    app = app_factory(runner)
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    chat_response = client.post("/api/chat", json={"session_id": session_id, "message": "run"})
    payload = chat_response.get_json()
    assistant_message_id = payload["assistant_message_id"]

    assert runner.started.wait(timeout=2)
    assert wait_until(
        lambda: len(client.get(f"/api/history/{session_id}").get_json()[-1].get("trace", [])) == 1
    )

    history = client.get(f"/api/history/{session_id}").get_json()
    assert len(history) == 2
    assert history[0]["role"] == "user"
    assert history[1]["id"] == assistant_message_id
    assert history[1]["status"] == "running"
    assert history[1]["trace"][0]["type"] == "progress"

    runner.release.set()
    assert wait_until(lambda: client.get("/api/sessions").get_json()[0]["status"] == "idle")


def test_stop_discards_inflight_trace_without_committing_assistant(app_factory):
    runner = BlockingBotRunner()
    app = app_factory(runner)
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    chat_response = client.post("/api/chat", json={"session_id": session_id, "message": "cancel me"})
    job_id = chat_response.get_json()["job_id"]

    assert runner.started.wait(timeout=2)
    assert len(client.get(f"/api/history/{session_id}").get_json()) == 2

    stop_response = client.post(f"/api/sessions/{session_id}/stop")
    assert stop_response.status_code == 200
    assert stop_response.get_json()["stop_requested"] is True
    assert wait_until(lambda: client.get("/api/sessions").get_json()[0]["status"] == "idle")

    history = client.get(f"/api/history/{session_id}").get_json()
    assert [message["role"] for message in history] == ["user"]
    store = app.config["RUNTIME_STORE"]
    assert not store._inflight_path("10001", session_id, job_id).exists()


def test_new_session_defaults_to_no_skill_and_web_source(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session = client.post("/api/sessions", json={}).get_json()

    assert session["composer_prefs"]["defaults"] == {
        "active_skill": "",
        "preferred_data_source": "web",
        "iteration_budget": "medium",
    }
    assert session["ui_state"]["active_skill"] == ""


def test_chat_turn_context_is_forwarded_without_persisting_prefs(app_factory):
    runner = FakeBotRunner()
    app = app_factory(runner)
    client = app.test_client()

    session = client.post("/api/sessions", json={}).get_json()
    session_id = session["id"]
    response = client.post(
        "/api/chat",
        json={
            "session_id": session_id,
            "message": "compare supply chain updates",
            "turn_context": {
                "skills": ["deep-research"],
                "preferred_data_sources": ["forum", "news"],
                "iteration_budget": "high",
                "files": ["permanent/notes.md"],
            },
        },
    )

    assert response.status_code == 200
    assert runner.calls[0]["turn_context"] == {
        "skills": ["deep-research"],
        "preferred_data_sources": ["forum", "news"],
        "iteration_budget": "high",
        "files": ["permanent/notes.md"],
    }
    persisted = client.get("/api/sessions").get_json()[0]
    assert persisted["composer_prefs"]["defaults"] == {
        "active_skill": "",
        "preferred_data_source": "web",
        "iteration_budget": "medium",
    }


def test_chat_without_turn_context_defaults_to_auto_context(app_factory):
    runner = FakeBotRunner()
    app = app_factory(runner)
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    response = client.post("/api/chat", json={"session_id": session_id, "message": "hello"})

    assert response.status_code == 200
    assert runner.calls[0]["turn_context"] == {
        "skills": [],
        "preferred_data_sources": [],
        "iteration_budget": "medium",
        "files": [],
    }


def test_patch_session_accepts_ui_state(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    response = client.patch(
        f"/api/sessions/{session_id}",
        json={
            "ui_state": {
                "left_tab": "skills",
                "active_path": "skills/deep-research/SKILL.md",
                "active_skill": "deep-research",
                "selected_skill": "deep-research",
                "center_view": "skill",
            }
        },
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["ui_state"]["left_tab"] == "skills"
    assert payload["ui_state"]["active_skill"] == "deep-research"
    assert payload["ui_state"]["selected_skill"] == "deep-research"
    assert payload["ui_state"]["center_view"] == "skill"


def test_delete_session_removes_row_chat_and_temp_workspace(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()
    store = app.config["RUNTIME_STORE"]

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    workspace = _session_workspace(app, session_id)
    (workspace / "notes.md").write_text("temporary note", encoding="utf-8")

    response = client.delete(f"/api/sessions/{session_id}")

    assert response.status_code == 204
    assert client.get("/api/sessions").get_json() == []
    assert client.get(f"/api/history/{session_id}").status_code == 404
    assert not store._chat_path("10001", session_id).exists()
    assert not workspace.exists()


def test_index_includes_three_pane_workbench_shell(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    response = client.get("/")
    body = response.get_data(as_text=True)

    assert response.status_code == 200
    assert 'id="file-tree"' in body
    assert 'id="file-new-file-button"' in body
    assert 'id="file-new-folder-button"' in body
    assert 'id="file-upload-button"' in body
    assert 'id="file-download-button"' in body
    assert 'id="file-persist-button"' in body
    assert 'id="file-copy-button"' in body
    assert 'id="file-upload-input"' in body
    assert 'id="file-paste-button"' in body
    assert 'id="file-rename-button"' in body
    assert 'id="file-delete-button"' in body
    assert 'id="persist-file-button"' in body
    assert 'id="skills-list"' in body
    assert 'id="center-view"' in body
    assert 'id="context-chips"' in body
    assert 'id="show-chat-button"' in body
    assert "/static/app.js" not in body
    for script in (
        "core.js",
        "layout.js",
        "workspace.js",
        "center.js",
        "composer_state.js",
        "chat_trace.js",
        "composer_mentions.js",
        "bootstrap.js",
    ):
        assert f'/static/js/{script}' in body


def test_workspace_tree_lists_permanent_and_current_session_files(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    chat_path = store._chat_path("10001", session_id)
    assert chat_path.parts[-3:] == ("sessions", session_id, "chat.json")
    assert _session_workspace(app, session_id).parts[-2:] == ("temp", session_id)
    workspace = store._user_workspace("10001")
    (workspace / "permanent" / "visible.md").write_text("# Visible", encoding="utf-8")
    (workspace / "root.md").write_text("# Hidden root file", encoding="utf-8")
    (_session_workspace(app, session_id) / "hidden.md").write_text("# Hidden temp file", encoding="utf-8")

    tree = client.get(f"/api/workspace/{session_id}/tree").get_json()

    roots = {item["path"]: item for item in tree["children"]}
    assert tree["name"] == "workspace"
    assert set(roots) == {"permanent", f"temp/{session_id}"}
    permanent_names = {item["name"] for item in roots["permanent"]["children"]}
    permanent_paths = {item["path"] for item in roots["permanent"]["children"]}
    session_names = {item["name"] for item in roots[f"temp/{session_id}"]["children"]}
    session_paths = {item["path"] for item in roots[f"temp/{session_id}"]["children"]}
    assert "visible.md" in permanent_names
    assert "permanent/visible.md" in permanent_paths
    assert "hidden.md" in session_names
    assert f"temp/{session_id}/hidden.md" in session_paths
    assert "shared_permanent" not in session_names
    assert "shared_context" not in session_names
    assert "root.md" not in permanent_names
    assert "_agent" not in roots
    assert "chat.json" not in permanent_names
    assert "chat.json" not in session_names


def test_workspace_file_read_save_and_path_guard(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    workspace = _session_workspace(app, session_id)
    note_path = workspace / "notes.md"
    note_path.write_text("# Notes\n\nhello", encoding="utf-8")
    big_path = workspace / "big.txt"
    big_path.write_text("x" * 210_000, encoding="utf-8")
    note_rel = f"temp/{session_id}/notes.md"
    big_rel = f"temp/{session_id}/big.txt"

    read_response = client.get(f"/api/workspace/{session_id}/file", query_string={"path": note_rel})
    read_payload = read_response.get_json()
    assert read_response.status_code == 200
    assert read_payload["editable"] is True
    assert read_payload["content"] == "# Notes\n\nhello"

    save_response = client.put(
        f"/api/workspace/{session_id}/file",
        json={"path": note_rel, "content": "# Notes\n\nupdated"},
    )
    save_payload = save_response.get_json()
    assert save_response.status_code == 200
    assert save_payload["content"] == "# Notes\n\nupdated"
    assert note_path.read_text(encoding="utf-8") == "# Notes\n\nupdated"

    big_response = client.get(f"/api/workspace/{session_id}/file", query_string={"path": big_rel})
    big_payload = big_response.get_json()
    assert big_response.status_code == 200
    assert big_payload["too_large"] is True
    assert big_payload["editable"] is False

    guarded = client.put(
        f"/api/workspace/{session_id}/file",
        json={"path": "../oops.md", "content": "blocked"},
    )
    assert guarded.status_code == 400


def test_workspace_fs_copy_pastes_permanent_files_and_directories(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    workspace = store._user_workspace("10001")
    permanent = workspace / "permanent"
    source_file = permanent / "report.md"
    source_file.write_text("# Report", encoding="utf-8")
    target_dir = permanent / "reports"
    target_dir.mkdir()
    source_dir = permanent / "bundle"
    source_dir.mkdir()
    (source_dir / "nested.txt").write_text("nested", encoding="utf-8")

    root_response = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent/report.md", "target_parent": "permanent"},
    )
    assert root_response.status_code == 200
    assert root_response.get_json()["path"] == "permanent/report copy.md"
    assert (permanent / "report copy.md").read_text(encoding="utf-8") == "# Report"

    file_response = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent/report.md", "target_parent": "permanent/reports"},
    )
    assert file_response.status_code == 200
    file_payload = file_response.get_json()
    assert file_payload["path"] == "permanent/reports/report.md"
    assert file_payload["kind"] == "file"
    assert (target_dir / "report.md").read_text(encoding="utf-8") == "# Report"

    duplicate_response = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent/report.md", "target_parent": "permanent/reports"},
    )
    assert duplicate_response.status_code == 200
    assert duplicate_response.get_json()["path"] == "permanent/reports/report copy.md"
    assert (target_dir / "report copy.md").exists()

    dir_response = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent/bundle", "target_parent": "permanent/reports"},
    )
    assert dir_response.status_code == 200
    dir_payload = dir_response.get_json()
    assert dir_payload["path"] == "permanent/reports/bundle"
    assert dir_payload["kind"] == "directory"
    assert (target_dir / "bundle" / "nested.txt").read_text(encoding="utf-8") == "nested"


def test_workspace_fs_copy_rejects_paths_outside_permanent(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    workspace = store._user_workspace("10001")
    (workspace / "permanent" / "report.md").write_text("# Report", encoding="utf-8")
    (_session_workspace(app, session_id) / "hidden.md").write_text("# Hidden", encoding="utf-8")

    outside_source = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": f"temp/{session_id}/hidden.md", "target_parent": "permanent"},
    )
    assert outside_source.status_code == 400

    outside_target = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent/report.md", "target_parent": f"temp/{session_id}"},
    )
    assert outside_target.status_code == 400


def test_workspace_fs_persist_temp_copies_into_permanent(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    workspace = store._user_workspace("10001")
    temp_note = _session_workspace(app, session_id) / "draft.md"
    temp_note.write_text("# Draft", encoding="utf-8")
    (workspace / "permanent" / "draft.md").write_text("# Existing", encoding="utf-8")

    response = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "persist_temp", "source": f"temp/{session_id}/draft.md"},
    )

    assert response.status_code == 200
    payload = response.get_json()
    assert payload["path"] == "permanent/draft copy.md"
    assert payload["kind"] == "file"
    assert (workspace / "permanent" / "draft copy.md").read_text(encoding="utf-8") == "# Draft"
    assert temp_note.read_text(encoding="utf-8") == "# Draft"


def test_workspace_fs_persist_temp_rejects_non_session_paths(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    workspace = store._user_workspace("10001")
    (workspace / "permanent" / "report.md").write_text("# Report", encoding="utf-8")

    response = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "persist_temp", "source": "permanent/report.md"},
    )

    assert response.status_code == 400


def test_workspace_upload_and_download_permanent_files(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    permanent = store._user_workspace("10001") / "permanent"
    uploads = permanent / "Uploads"
    uploads.mkdir()
    (uploads / "report.md").write_text("existing", encoding="utf-8")

    upload_response = client.post(
        f"/api/workspace/{session_id}/upload",
        data={
            "target_parent": "permanent/Uploads",
            "files": [
                (BytesIO(b"# Uploaded"), "report.md"),
                (BytesIO(b"plain"), "notes.txt"),
            ],
        },
        content_type="multipart/form-data",
    )
    assert upload_response.status_code == 200
    upload_payload = upload_response.get_json()
    paths = {item["path"] for item in upload_payload["items"]}
    assert paths == {"permanent/Uploads/report 2.md", "permanent/Uploads/notes.txt"}
    assert (uploads / "report 2.md").read_text(encoding="utf-8") == "# Uploaded"
    assert (uploads / "notes.txt").read_text(encoding="utf-8") == "plain"

    download_response = client.get(
        f"/api/workspace/{session_id}/download",
        query_string={"path": "permanent/Uploads/report 2.md"},
    )
    assert download_response.status_code == 200
    assert download_response.get_data() == b"# Uploaded"
    assert "attachment" in download_response.headers["Content-Disposition"]
    assert "report 2.md" in download_response.headers["Content-Disposition"]


def test_workspace_download_allows_current_session_temp_file(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    temp_file = _session_workspace(app, session_id) / "report.md"
    temp_file.write_text("# Temp Report", encoding="utf-8")

    download_response = client.get(
        f"/api/workspace/{session_id}/download",
        query_string={"path": f"temp/{session_id}/report.md"},
    )

    assert download_response.status_code == 200
    assert download_response.get_data() == b"# Temp Report"
    assert "attachment" in download_response.headers["Content-Disposition"]
    assert "report.md" in download_response.headers["Content-Disposition"]


def test_workspace_upload_download_reject_outside_or_directories(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    other_session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    permanent = store._user_workspace("10001") / "permanent"
    (permanent / "Folder").mkdir()
    (_session_workspace(app, other_session_id) / "hidden.md").write_text("hidden", encoding="utf-8")
    (_session_workspace(app, session_id) / ".hidden.md").write_text("hidden", encoding="utf-8")

    outside_upload = client.post(
        f"/api/workspace/{session_id}/upload",
        data={
            "target_parent": f"temp/{session_id}",
            "files": [(BytesIO(b"x"), "x.txt")],
        },
        content_type="multipart/form-data",
    )
    assert outside_upload.status_code == 400

    outside_download = client.get(
        f"/api/workspace/{session_id}/download",
        query_string={"path": f"temp/{other_session_id}/hidden.md"},
    )
    assert outside_download.status_code == 400

    hidden_download = client.get(
        f"/api/workspace/{session_id}/download",
        query_string={"path": f"temp/{session_id}/.hidden.md"},
    )
    assert hidden_download.status_code == 400

    directory_download = client.get(
        f"/api/workspace/{session_id}/download",
        query_string={"path": "permanent/Folder"},
    )
    assert directory_download.status_code == 404


def test_workspace_fs_create_rename_and_delete_permanent_entries(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    permanent = store._user_workspace("10001") / "permanent"

    created_dir = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "create_dir", "parent": "permanent", "name": "Reports"},
    )
    assert created_dir.status_code == 200
    assert created_dir.get_json()["path"] == "permanent/Reports"
    assert (permanent / "Reports").is_dir()

    created_file = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "create_file", "parent": "permanent/Reports", "name": "note.md"},
    )
    assert created_file.status_code == 200
    assert created_file.get_json()["path"] == "permanent/Reports/note.md"
    assert (permanent / "Reports" / "note.md").read_text(encoding="utf-8") == ""

    duplicate_file = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "create_file", "parent": "permanent/Reports", "name": "note.md"},
    )
    assert duplicate_file.status_code == 200
    assert duplicate_file.get_json()["path"] == "permanent/Reports/note 2.md"
    assert (permanent / "Reports" / "note 2.md").exists()

    renamed = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "rename", "path": "permanent/Reports/note.md", "name": "summary.md"},
    )
    assert renamed.status_code == 200
    rename_payload = renamed.get_json()
    assert rename_payload["old_path"] == "permanent/Reports/note.md"
    assert rename_payload["path"] == "permanent/Reports/summary.md"
    assert not (permanent / "Reports" / "note.md").exists()
    assert (permanent / "Reports" / "summary.md").exists()

    delete_file = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "delete", "path": "permanent/Reports/summary.md"},
    )
    assert delete_file.status_code == 200
    assert not (permanent / "Reports" / "summary.md").exists()

    delete_dir_without_recursive = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "delete", "path": "permanent/Reports"},
    )
    assert delete_dir_without_recursive.status_code == 400

    delete_dir = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "delete", "path": "permanent/Reports", "recursive": True},
    )
    assert delete_dir.status_code == 200
    assert not (permanent / "Reports").exists()


def test_workspace_fs_moves_permanent_entries(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    permanent = store._user_workspace("10001") / "permanent"
    (permanent / "source.md").write_text("# Source", encoding="utf-8")
    target_dir = permanent / "Target"
    target_dir.mkdir()
    (target_dir / "source.md").write_text("# Existing", encoding="utf-8")
    bundle = permanent / "Bundle"
    bundle.mkdir()
    (bundle / "nested.txt").write_text("nested", encoding="utf-8")

    moved_file = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "move", "source": "permanent/source.md", "target_parent": "permanent/Target"},
    )
    assert moved_file.status_code == 200
    file_payload = moved_file.get_json()
    assert file_payload["old_path"] == "permanent/source.md"
    assert file_payload["path"] == "permanent/Target/source 2.md"
    assert not (permanent / "source.md").exists()
    assert (target_dir / "source 2.md").read_text(encoding="utf-8") == "# Source"

    moved_dir = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "move", "source": "permanent/Bundle", "target_parent": "permanent/Target"},
    )
    assert moved_dir.status_code == 200
    assert moved_dir.get_json()["path"] == "permanent/Target/Bundle"
    assert not bundle.exists()
    assert (target_dir / "Bundle" / "nested.txt").read_text(encoding="utf-8") == "nested"

    move_into_self = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "move", "source": "permanent/Target", "target_parent": "permanent/Target/Bundle"},
    )
    assert move_into_self.status_code == 400


def test_workspace_fs_rejects_root_and_invalid_names(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    invalid_name = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "create_dir", "parent": "permanent", "name": "../bad"},
    )
    assert invalid_name.status_code == 400

    rename_root = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "rename", "path": "permanent", "name": "other"},
    )
    assert rename_root.status_code == 400

    delete_root = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "delete", "path": "permanent", "recursive": True},
    )
    assert delete_root.status_code == 400

    copy_root = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent", "target_parent": "permanent"},
    )
    assert copy_root.status_code == 400


def test_workspace_tree_does_not_follow_symlinked_directories_outside_workspace(app_factory, tmp_path: Path):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    permanent = store._user_workspace("10001") / "permanent"

    outside_dir = tmp_path / "outside"
    outside_dir.mkdir()
    (outside_dir / "secret.txt").write_text("top secret", encoding="utf-8")
    (permanent / "outside-link").symlink_to(outside_dir, target_is_directory=True)

    tree = client.get(f"/api/workspace/{session_id}/tree").get_json()

    permanent_node = next(item for item in tree["children"] if item["path"] == "permanent")
    assert "outside-link" not in {item["name"] for item in permanent_node["children"]}


def test_workspace_endpoints_reject_symlinks_that_escape_workspace(app_factory, tmp_path: Path):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    store = app.config["RUNTIME_STORE"]
    workspace = store._user_workspace("10001")
    permanent = workspace / "permanent"

    outside_file = tmp_path / "secret.txt"
    outside_file.write_text("top secret", encoding="utf-8")
    (permanent / "outside.txt").symlink_to(outside_file)

    file_read = client.get(
        f"/api/workspace/{session_id}/file",
        query_string={"path": "permanent/outside.txt"},
    )
    assert file_read.status_code == 400

    download = client.get(
        f"/api/workspace/{session_id}/download",
        query_string={"path": "permanent/outside.txt"},
    )
    assert download.status_code == 400

    delete = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "delete", "path": "permanent/outside.txt"},
    )
    assert delete.status_code == 400

    rename = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "rename", "path": "permanent/outside.txt", "name": "renamed.txt"},
    )
    assert rename.status_code == 400

    move = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "move", "source": "permanent/outside.txt", "target_parent": "permanent"},
    )
    assert move.status_code == 400

    copy = client.post(
        f"/api/workspace/{session_id}/fs",
        json={"operation": "copy", "source": "permanent/outside.txt", "target_parent": "permanent"},
    )
    assert copy.status_code == 400


def test_workspace_skills_identify_supported_modes(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    payload = client.get(f"/api/workspace/{session_id}/skills").get_json()
    items = {item["name"]: item for item in payload["items"]}

    assert items["simple"]["activatable"] is True
    assert items["deep-research"]["activatable"] is True
    assert items["industry-overview"]["activatable"] is True
    assert payload["active_skill"] == ""


def test_same_session_rejects_second_running_job(app_factory):
    runner = BlockingBotRunner()
    app = app_factory(runner, max_workers=2)
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    first = client.post("/api/chat", json={"session_id": session_id, "message": "first"})
    assert first.status_code == 200
    assert runner.started.wait(timeout=2)

    second = client.post("/api/chat", json={"session_id": session_id, "message": "second"})
    assert second.status_code == 409

    runner.release.set()
    stream = client.get(f"/api/stream/{first.get_json()['job_id']}")
    _ = b"".join(stream.response)


def test_different_sessions_can_run_in_parallel(app_factory):
    runner = BlockingBotRunner()
    app = app_factory(runner, max_workers=2)
    client = app.test_client()

    session_a = client.post("/api/sessions", json={}).get_json()["id"]
    session_b = client.post("/api/sessions", json={}).get_json()["id"]

    first = client.post("/api/chat", json={"session_id": session_a, "message": "alpha"})
    assert first.status_code == 200
    assert runner.started.wait(timeout=2)

    runner.started.clear()
    second = client.post("/api/chat", json={"session_id": session_b, "message": "beta"})
    assert second.status_code == 200

    runner.release.set()
    body_a = b"".join(client.get(f"/api/stream/{first.get_json()['job_id']}").response).decode("utf-8")
    body_b = b"".join(client.get(f"/api/stream/{second.get_json()['job_id']}").response).decode("utf-8")

    assert "event: done" in body_a
    assert "event: done" in body_b


def test_stop_running_session_discards_inflight_assistant(app_factory):
    runner = BlockingBotRunner()
    app = app_factory(runner, max_workers=1)
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    first = client.post("/api/chat", json={"session_id": session_id, "message": "first"})
    assert first.status_code == 200
    assert runner.started.wait(timeout=2)

    stop_response = client.post(f"/api/sessions/{session_id}/stop")
    assert stop_response.status_code == 200
    assert stop_response.get_json()["stop_requested"] is True

    body = b"".join(client.get(f"/api/stream/{first.get_json()['job_id']}").response).decode("utf-8")
    assert "event: done" in body
    assert '"status": "stopped"' in body

    assert wait_until(lambda: client.get("/api/sessions").get_json()[0]["status"] == "idle")
    history = client.get(f"/api/history/{session_id}").get_json()
    assert [message["role"] for message in history] == ["user"]

    sessions = client.get("/api/sessions").get_json()
    assert sessions[0]["status"] == "idle"


def test_stop_stale_running_session_clears_persisted_state(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()
    store = app.config["RUNTIME_STORE"]

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    stale_job_id = "stale-job"
    store.create_inflight_assistant(session_id, "10001", job_id=stale_job_id)
    store._update_session(
        session_id,
        user_id="10001",
        updated_at="2026-04-29T12:00:00+00:00",
        status="running",
    )

    response = client.post(f"/api/sessions/{session_id}/stop")

    assert response.status_code == 200
    assert response.get_json()["stop_requested"] is False
    assert response.get_json()["message"] == "Cleared stale run state."
    assert client.get("/api/sessions").get_json()[0]["status"] == "idle"
    assert not store._inflight_dir("10001", session_id).exists()


def test_chat_stream_persists_error_status(app_factory):
    app = app_factory(ErrorBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]
    response = client.post("/api/chat", json={"session_id": session_id, "message": "too long"})
    assert response.status_code == 200

    body = b"".join(client.get(f"/api/stream/{response.get_json()['job_id']}").response).decode("utf-8")

    assert "event: done" in body
    assert '"status": "error"' in body
    history = client.get(f"/api/history/{session_id}").get_json()
    assert history[-1]["status"] == "error"
    assert history[-1]["content"] == "Reached max tool iterations."
    assert client.get("/api/sessions").get_json()[0]["status"] == "error"


def test_stop_session_noop_and_missing(app_factory):
    app = app_factory(FakeBotRunner())
    client = app.test_client()

    session_id = client.post("/api/sessions", json={}).get_json()["id"]

    noop = client.post(f"/api/sessions/{session_id}/stop")
    assert noop.status_code == 200
    assert noop.get_json()["stop_requested"] is False

    missing = client.post("/api/sessions/does-not-exist/stop")
    assert missing.status_code == 404
