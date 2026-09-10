"""
Stage 12 测试：Web UI（进程内直连 + SSE 流式）。

覆盖：首页静态资源、工具/技能/MCP 列表、同步聊天、SSE 流式事件、会话历史。
使用 fakeredis + stub LLM + test 环境（跳过 MCP 连接），完全离线。
"""
import json

import fakeredis.aioredis
from fastapi.testclient import TestClient

from app.config import Settings
from app.execution.models import ExecutionStatus
from app.main import create_app


def _make_app(tmp_path):
    settings = Settings(
        environment="test",
        trace_enabled=False,
        memory_enabled=False,
        skills_enabled=True,
        agent_mode="react",
        llm_provider="stub",
        database_url=f"sqlite:///{tmp_path}/web.db",
        trace_file=str(tmp_path / "traces.jsonl"),
        eval_run_dir=str(tmp_path / "runs"),
    )
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return create_app(settings, redis=fake_redis)


def test_web_index(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "ReAgent" in r.text


def test_web_capabilities(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        tools = client.get("/api/web/tools").json()
        assert tools["count"] >= 10
        names = {t["name"] for t in tools["tools"]}
        assert {"calculator", "get_weather", "send_email"} <= names

        skills = client.get("/api/web/skills").json()
        assert skills["count"] >= 2

        mcp = client.get("/api/web/mcp").json()
        assert mcp["count"] == 0  # test 环境跳过 MCP


def test_web_chat_sync(tmp_path):
    """同步聊天：stub LLM 调用 get_weather。"""
    with TestClient(_make_app(tmp_path)) as client:
        r = client.post("/api/web/chat", json={"message": "查询北京天气", "agent_mode": "react"})
        assert r.status_code == 200
        data = r.json()
        assert data["answer"]
        assert any(t["name"] == "get_weather" for t in data["tool_calls"])
        assert data["session_id"]


def test_web_chat_returns_trace(tmp_path):
    """同步聊天响应应包含 Trace 树（工作流可视化）。"""
    with TestClient(_make_app(tmp_path)) as client:
        r = client.post("/api/web/chat", json={"message": "查询北京天气", "agent_mode": "react"})
        data = r.json()
        # test 环境 trace_enabled=False -> trace 为 None；单独启用 tracing 验证
        if data["trace"] is not None:
            assert "spans" in data["trace"]
        # 通过独立接口验证（显式启用 tracing 的 app）
        from app.main import create_app as _create
        from app.config import Settings as _S
        settings = _S(
            environment="test", trace_enabled=True, llm_provider="stub",
            database_url=f"sqlite:///{tmp_path}/trace.db",
            trace_file=str(tmp_path / "trace.jsonl"),
            skills_enabled=True,
            agent_mode="react",       # 显式 react：.env 的 AGENT_MODE=plan 不影响测试
            memory_enabled=False,     # 显式关闭 memory：.env 的 MEMORY_ENABLED=true 不影响
        )
        app2 = _create(settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
        with TestClient(app2) as client2:
            r2 = client2.post("/api/web/chat", json={"message": "查询北京天气", "agent_mode": "react"})
            data2 = r2.json()
            assert data2["trace"] is not None
            assert data2["trace"]["spans"]
            names = [s["name"] for s in data2["trace"]["spans"]]
            assert "agent.run" in names
            # 递归收集所有节点名
            def collect(nodes):
                out = []
                for n in nodes:
                    out.append(n["name"])
                    out.extend(collect(n.get("children", [])))
                return out
            all_names = collect(data2["trace"]["spans"])
            assert "llm_call" in all_names
            assert "tool.execute" in all_names
            # 独立接口
            r3 = client2.get(f"/api/web/traces/{data2['trace_id']}")
            assert r3.status_code == 200
            assert r3.json()["spans"]


def test_web_chat_plan_mode(tmp_path):
    """plan 模式聊天：结果含 plan 字段。"""
    with TestClient(_make_app(tmp_path)) as client:
        r = client.post("/api/web/chat", json={"message": "查询北京和上海天气", "agent_mode": "plan"})
        assert r.status_code == 200
        data = r.json()
        assert data["mode"] == "plan"
        assert data["answer"]


def test_web_chat_stream_sse(tmp_path):
    """SSE 流式：应包含 step / tool_result / done 事件。"""
    with TestClient(_make_app(tmp_path)) as client:
        with client.stream(
            "POST", "/api/web/chat/stream",
            json={"message": "计算 123 * 456", "agent_mode": "react"},
        ) as resp:
            assert resp.status_code == 200
            body = "".join(resp.iter_text())
            assert "event: step" in body
            assert "event: tool_result" in body
            assert "event: done" in body


def test_web_stream_persists_execution_events(tmp_path):
    """SSE 每个关键节点带稳定 execution/tool_call ID，且可从 SQLite 回放。"""
    with TestClient(_make_app(tmp_path)) as client:
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "计算 123 * 456", "agent_mode": "react"},
        ) as response:
            assert response.status_code == 200
            header_execution_id = response.headers["x-execution-id"]
            body = "".join(response.iter_text())

        frames = [frame for frame in body.split("\n\n") if frame.strip()]
        parsed = []
        for frame in frames:
            event_line = next((line for line in frame.split("\n") if line.startswith("event:")), "")
            data_line = next((line for line in frame.split("\n") if line.startswith("data:")), "")
            if event_line and data_line:
                parsed.append((
                    event_line.split(":", 1)[1].strip(),
                    json.loads(data_line.split(":", 1)[1].strip()),
                ))

        event_names = [name for name, _ in parsed]
        assert {"execution.started", "llm.started", "step", "tool.started", "tool_result", "done"} <= set(event_names)
        done = next(data for name, data in parsed if name == "done")
        assert done["execution_id"] == header_execution_id

        scheduled = next(data for name, data in parsed if name == "step" and data["tool_calls"])
        tool_started = next(data for name, data in parsed if name == "tool.started")
        tool_result = next(data for name, data in parsed if name == "tool_result")
        assert scheduled["tool_calls"][0]["tool_call_id"] == tool_started["tool_call_id"]
        assert tool_started["tool_call_id"] == tool_result["tool_call_id"]

        snapshot = client.get(f"/api/web/executions/{header_execution_id}")
        assert snapshot.status_code == 200
        assert snapshot.json()["status"] == "SUCCEEDED"

        events = client.get(f"/api/web/executions/{header_execution_id}/events").json()
        assert events["last_seq"] == len(events["events"])
        assert [event["seq"] for event in events["events"]] == list(range(1, events["last_seq"] + 1))

        session_runs = client.get(
            f"/api/web/sessions/{done['session_id']}/executions"
        ).json()
        assert any(run["execution_id"] == header_execution_id for run in session_runs["executions"])


def test_web_execution_stream_replays_all_terminal_events(tmp_path):
    """终态运行可从 SSE 回放端点按 seq 完整重放。"""
    with TestClient(_make_app(tmp_path)) as client:
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "计算 123 * 456", "agent_mode": "react"},
        ) as response:
            execution_id = response.headers["x-execution-id"]
            _ = "".join(response.iter_text())

        stored = client.get(f"/api/web/executions/{execution_id}/events").json()
        replay = client.get(f"/api/web/executions/{execution_id}/stream?after_seq=0")
        assert replay.status_code == 200
        assert "event: execution.started" in replay.text
        assert "event: execution.completed" in replay.text
        assert f"id: {stored['last_seq']}" in replay.text

        exhausted = client.get(
            f"/api/web/executions/{execution_id}/stream?after_seq={stored['last_seq']}"
        )
        assert exhausted.status_code == 200
        assert exhausted.text == ""


def test_web_execution_stream_replays_more_than_one_event_page(tmp_path):
    """终态记录超过默认单页上限时，SSE 回放不能遗漏后续事件。"""
    with TestClient(_make_app(tmp_path)) as client:
        repository = client.app.state.execution_repository
        repository.create(
            execution_id="exec_many_events",
            session_id="session_many_events",
            turn_id="turn_many_events",
            agent_mode="react",
            input_preview="多页回放",
        )
        for index in range(501):
            repository.append_event("exec_many_events", "progress", {"index": index})
        repository.finish("exec_many_events", status=ExecutionStatus.SUCCEEDED)

        replay = client.get("/api/web/executions/exec_many_events/stream")
        assert replay.status_code == 200
        assert replay.text.count("event: progress") == 501
        assert "id: 501" in replay.text


def test_web_execution_cancel_without_active_task_is_terminal_and_idempotent(tmp_path):
    """失去运行任务的记录可被明确取消，重复取消不新增状态转换。"""
    with TestClient(_make_app(tmp_path)) as client:
        repository = client.app.state.execution_repository
        repository.create(
            execution_id="exec_orphaned",
            session_id="session_orphaned",
            turn_id="turn_orphaned",
            agent_mode="react",
            input_preview="待取消任务",
        )

        cancelled = client.post("/api/web/executions/exec_orphaned/cancel")
        assert cancelled.status_code == 200
        assert cancelled.json() == {
            "execution_id": "exec_orphaned",
            "status": "CANCELLED",
            "cancel_requested": False,
        }
        snapshot = client.get("/api/web/executions/exec_orphaned").json()
        assert snapshot["status"] == "CANCELLED"
        event_types = [
            event["event_type"]
            for event in client.get("/api/web/executions/exec_orphaned/events").json()["events"]
        ]
        assert event_types == ["execution.cancel_requested", "execution.cancelled"]

        repeated = client.post("/api/web/executions/exec_orphaned/cancel")
        assert repeated.json()["cancel_requested"] is False
        assert repeated.json()["status"] == "CANCELLED"


def test_web_sessions(tmp_path):
    """会话历史：聊天后会话可查。"""
    with TestClient(_make_app(tmp_path)) as client:
        client.post("/api/web/chat", json={"message": "你好", "agent_mode": "react"})
        r = client.get("/api/web/sessions")
        assert r.status_code == 200
        assert len(r.json()["sessions"]) >= 1


def test_web_session_messages(tmp_path):
    """会话消息历史。"""
    with TestClient(_make_app(tmp_path)) as client:
        data = client.post("/api/web/chat", json={"message": "你好", "agent_mode": "react"}).json()
        sid = data["session_id"]
        r = client.get(f"/api/web/sessions/{sid}/messages")
        assert r.status_code == 200
        msgs = r.json()["messages"]
        assert any(m["role"] == "user" for m in msgs)


def test_web_session_delete(tmp_path):
    """会话删除：删除后会话与消息消失。"""
    with TestClient(_make_app(tmp_path)) as client:
        data = client.post("/api/web/chat", json={"message": "你好", "agent_mode": "react"}).json()
        sid = data["session_id"]
        r = client.delete(f"/api/web/sessions/{sid}")
        assert r.status_code == 200
        assert r.json()["deleted"] == sid
        # 会话消失
        sessions = client.get("/api/web/sessions").json()
        assert all(s["session_id"] != sid for s in sessions["sessions"])
        # 消息接口 404（会话已删，无消息返回）
        r2 = client.get(f"/api/web/sessions/{sid}/messages")
        assert r2.status_code == 200
        assert r2.json()["messages"] == []


def test_web_upload_and_files(tmp_path):
    """文件上传：写入沙箱，文件列表可见，file_read 可读。"""
    with TestClient(_make_app(tmp_path)) as client:
        # 上传
        r = client.post("/api/web/upload", files={"file": ("hello.txt", b"hello agent", "text/plain")})
        assert r.status_code == 200
        data = r.json()
        assert data["filename"] == "hello.txt"
        assert "file_read" in data["hint"]
        # 文件列表
        files = client.get("/api/web/files").json()
        assert any(f["name"] == "hello.txt" for f in files["files"])
        # file_read 能读（走沙箱）
        from app.tools.builtin.data import file_read_handler
        assert file_read_handler("hello.txt") == "hello agent"


def test_web_upload_traversal_blocked(tmp_path):
    """上传文件名含路径穿越应被拒绝。"""
    with TestClient(_make_app(tmp_path)) as client:
        r = client.post("/api/web/upload", files={"file": ("../evil.txt", b"x", "text/plain")})
        # 文件名被 Path().name 规范化，不报错但落在沙箱内
        assert r.status_code == 200
        assert r.json()["filename"] == "evil.txt"
