"""Session themes are derived from durable messages without a model call."""
import pytest
from fastapi.testclient import TestClient

from tests.test_web import _make_app


def test_session_list_exposes_persisted_question_theme_and_reply_preview(tmp_path):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        repo = app.state.runtime.session_repo
        repo.create_session("opaque-id")
        repo.add_message("opaque-id", {"role": "system", "content": "Not the title"})
        repo.add_message("opaque-id", {"role": "user", "content": "  如何改进\n  当前前端会话体验？ "})
        repo.add_message("opaque-id", {"role": "assistant", "content": "先增加主题标题。"})
        repo.add_message("opaque-id", {"role": "user", "content": "这是后续问题"})
        repo.add_message("opaque-id", {"role": "assistant", "content": "最新进展：标题支持刷新恢复。"})

    # New process-style app construction; no browser cache or extra inference.
    with TestClient(_make_app(tmp_path)) as client:
        item = client.get("/api/web/sessions").json()["sessions"][0]
    assert item["session_id"] == "opaque-id"
    assert item["title"] == "如何改进 当前前端会话体验？"
    assert item["preview"] == "最新进展：标题支持刷新恢复。"


@pytest.mark.parametrize("content, expected", [
    (None, "新会话"),
    ("", "新会话"),
    ("问题" * 100, "问题" * 24 + "…"),
    ([{"type": "text", "text": "解读这张图片"}, {"type": "image_url", "image_url": {"url": "data:image/png;base64,private"}}], "解读这张图片"),
])
def test_session_summary_handles_empty_long_and_structured_content(tmp_path, content, expected):
    app = _make_app(tmp_path)
    with TestClient(app) as client:
        repo = app.state.runtime.session_repo
        repo.create_session("opaque-id")
        if content is not None:
            repo.add_message("opaque-id", {"role": "user", "content": content})
        response = client.get("/api/web/sessions")
        item = response.json()["sessions"][0]
    assert item["title"] == expected
    assert "private" not in response.text


def test_session_list_storage_failure_is_not_an_empty_success(tmp_path, monkeypatch):
    app = _make_app(tmp_path)
    with TestClient(app, raise_server_exceptions=False) as client:
        # Exercise the existing failure path without damaging a user database.
        app.state.runtime.session_repo._conn.execute("DROP TABLE sessions")
        response = client.get("/api/web/sessions")
    assert response.status_code == 503
    assert "sessions" not in response.json()
