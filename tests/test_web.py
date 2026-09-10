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
from app.llm.client import BaseLLMClient, LLMResponse, ToolCallRequest
from app.main import create_app
from app.tools.registry import ToolDefinition


def _make_app(tmp_path, *, orchestrator_enabled: bool = False):
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
        orchestrator_enabled=orchestrator_enabled,
        orchestrator_planner_strategy="llm",
        agent_profiles_file=str(tmp_path / "profiles.json"),
    )
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return create_app(settings, redis=fake_redis)


def _parse_sse_events(body: str) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    for frame in (frame for frame in body.split("\n\n") if frame.strip()):
        event_line = next((line for line in frame.split("\n") if line.startswith("event:")), "")
        data_line = next((line for line in frame.split("\n") if line.startswith("data:")), "")
        if event_line and data_line:
            events.append((
                event_line.split(":", 1)[1].strip(),
                json.loads(data_line.split(":", 1)[1].strip()),
            ))
    return events


class _DelegatingLLM(BaseLLMClient):
    """用于 Web SSE 集成测试：根 Agent 调 delegate，子 Agent 直接完成。"""

    model = "delegating-test"

    async def chat(self, messages, tools=None, **kwargs):
        text = "\n".join(str(message.get("content") or "") for message in messages)
        if "可用子 Agent 档案" in text:
            return LLMResponse(
                content=(
                    '{"rationale":"由研究员完成资料核验",'
                    '"steps":[{"agent":"researcher","task":"核验资料","depends_on":[]}]}'
                ),
                model=self.model,
            )
        if any(message.get("role") == "tool" for message in messages):
            return LLMResponse(content="主任务已完成", model=self.model)
        if "资深研究员" in text:
            return LLMResponse(content="研究员已完成核验", model=self.model)
        tool_names = {
            item.get("function", {}).get("name")
            for item in (tools or [])
            if isinstance(item, dict)
        }
        if "delegate" in tool_names:
            return LLMResponse(
                tool_calls=[
                    ToolCallRequest(
                        id="call_delegate_web",
                        name="delegate",
                        arguments={"task":"核验资料", "agents":["researcher"]},
                    )
                ],
                finish_reason="tool_calls",
                model=self.model,
            )
        return LLMResponse(content="普通回答", model=self.model)


def _install_delegating_llm(client) -> None:
    """让 Web Runtime 与其编排器共享同一确定性 LLM。"""
    llm = _DelegatingLLM()
    runtime = client.app.state.runtime
    runtime.llm = llm
    runtime.context_builder.llm = llm
    runtime.orchestrator.llm = llm
    runtime.orchestrator.planner.llm = llm
    runtime.orchestrator.executor.llm = llm


def test_web_index(tmp_path):
    with TestClient(_make_app(tmp_path)) as client:
        r = client.get("/")
        assert r.status_code == 200
        assert "ReAgent" in r.text
        assert 'id="open-inspector"' in r.text
        assert 'id="toggle-navigation"' in r.text
        assert 'id="jump-latest"' in r.text
        assert 'id="live-orchestration-section"' in r.text
        assert 'id="execution-context-section"' in r.text
        script = client.get("/app.js").text
        assert "tool-output-detail-btn" in script
        assert "/outputs/" in script


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


def test_web_streams_context_memory_skill_and_checkpoint_events(tmp_path):
    """透明化事件只公开实际统计与标识，不泄漏记忆正文或 Prompt。"""
    class EventMemory:
        async def retrieve(self, query, *, session_id=None):
            return ["不应通过 SSE 公开的记忆正文"]

        async def remember(self, messages, *, session_id="", turn_id=""):
            return [object(), object()]

        def close(self):
            pass

    with TestClient(_make_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        runtime.memory = EventMemory()
        runtime.settings.memory_auto_extract = True
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "请进行数据分析", "agent_mode": "react"},
        ) as response:
            assert response.status_code == 200
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        names = [name for name, _ in events]
        assert {"memory.retrieved", "skill.selected", "context.completed", "checkpoint.saved", "memory.stored"} <= set(names)
        assert names.index("memory.retrieved") < names.index("context.completed")
        assert names.index("skill.selected") < names.index("context.completed")
        assert names.index("checkpoint.saved") < names.index("llm.started")

        memory = next(data for name, data in events if name == "memory.retrieved")
        skills = next(data for name, data in events if name == "skill.selected")
        context = next(data for name, data in events if name == "context.completed")
        checkpoints = [data for name, data in events if name == "checkpoint.saved"]
        stored = next(data for name, data in events if name == "memory.stored")
        assert memory["hit_count"] == 1
        assert skills["count"] == 1
        assert skills["skills"] == ["data_analysis"]
        assert context["retrieved_documents"] >= 2  # 记忆 + 选中的技能指令
        assert context["selected_messages"] >= 1
        assert context["estimated_tokens"] > 0
        assert checkpoints[0]["point"] == "before_llm"
        assert [item["checkpoint_version"] for item in checkpoints] == sorted(item["checkpoint_version"] for item in checkpoints)
        assert stored["stored_count"] == 2

        stored_events = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        serialized = json.dumps([event["payload"] for event in stored_events], ensure_ascii=False)
        assert "不应通过 SSE 公开的记忆正文" not in serialized
        assert [event["event_type"] for event in stored_events] == names



def test_web_plan_streams_lifecycle_events(tmp_path):
    """Plan 的生成、步骤与反思在最终回答前按稳定版本/步骤 ID 发出。"""
    with TestClient(_make_app(tmp_path)) as client:
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "查询北京和上海天气", "agent_mode": "plan"},
        ) as response:
            assert response.status_code == 200
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        names = [name for name, _ in events]
        assert {
            "plan.created",
            "plan_step.started",
            "plan_step.completed",
            "plan.summarize_started",
            "reflection.completed",
            "done",
        } <= set(names)
        assert names.index("plan.created") < names.index("plan_step.started")
        assert names.index("plan.summarize_started") < names.index("reflection.completed") < names.index("done")
        # 子步骤是计划详情，不应把局部回答当作整轮最终回答事件。
        assert "final" not in names

        created = next(data for name, data in events if name == "plan.created")
        assert created["plan_version"] == 1
        assert created["total_steps"] == 2
        step_ids = {step["step_id"] for step in created["steps"]}

        started = [data for name, data in events if name == "plan_step.started"]
        completed = [data for name, data in events if name == "plan_step.completed"]
        assert {data["plan_step_id"] for data in started} == step_ids
        assert {data["plan_step_id"] for data in completed} == step_ids
        assert all(data["plan_version"] == 1 for data in started + completed)
        assert all(data["step"]["status"] == "SUCCEEDED" for data in completed)

        reflection = next(data for name, data in events if name == "reflection.completed")
        assert reflection["need_replan"] is False
        assert reflection["revised_task_preview"] == ""

        stored = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        assert [event["event_type"] for event in stored] == names


def test_web_streams_and_persists_subagent_lifecycle_events(tmp_path):
    """delegate 内部的编排/子 Agent 过程必须进入同一 SSE 与历史事件流。"""
    with TestClient(_make_app(tmp_path, orchestrator_enabled=True)) as client:
        _install_delegating_llm(client)
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "请委派一名研究员核验资料", "agent_mode": "react"},
        ) as response:
            assert response.status_code == 200
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        names = [name for name, _ in events]
        required = {
            "orchestration.started",
            "orchestration.plan_created",
            "agent.scheduled",
            "agent.started",
            "agent.llm.started",
            "agent.decision",
            "agent.completed",
            "orchestration.synthesis_started",
            "orchestration.completed",
        }
        assert required <= set(names)
        assert names.index("orchestration.started") < names.index("orchestration.plan_created")
        assert names.index("agent.scheduled") < names.index("agent.started")
        assert names.index("agent.completed") < names.index("orchestration.completed")

        orchestration_started = next(data for name, data in events if name == "orchestration.started")
        run_id = orchestration_started["run_id"]
        plan = next(data for name, data in events if name == "orchestration.plan_created")
        scheduled = next(data for name, data in events if name == "agent.scheduled")
        completed = next(data for name, data in events if name == "agent.completed")
        assert plan["run_id"] == scheduled["run_id"] == completed["run_id"] == run_id
        assert scheduled["agent_instance_id"] == completed["agent_instance_id"] == f"{run_id}:agent:0"
        assert scheduled["depends_on"] == []
        assert completed["status"] == "SUCCEEDED"

        stored = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        assert [event["event_type"] for event in stored] == names



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
        assert tool_result["has_output"] is True
        assert tool_result["data"] not in (None, "")

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


def test_web_loads_tool_output_details_on_demand(tmp_path):
    """SSE 只保留预览，完整脱敏结果通过 execution/output 关联按需读取。"""
    with TestClient(_make_app(tmp_path)) as client:
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "计算 123 * 456", "agent_mode": "react"},
        ) as response:
            assert response.status_code == 200
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        tool_result = next(data for name, data in events if name == "tool_result")
        assert tool_result["output_available"] is True
        assert tool_result["output_id"]
        detail = client.get(
            f"/api/web/executions/{execution_id}/outputs/{tool_result['output_id']}"
        )
        assert detail.status_code == 200
        payload = detail.json()
        assert payload["execution_id"] == execution_id
        assert payload["kind"] == "tool.result"
        assert payload["truncated"] is False
        # 详情保留 JSON 原始类型，事件预览则保持适合时间线展示的字符串。
        assert str(payload["content"]) == tool_result["data"]

        other = client.get(
            f"/api/web/executions/exec_not_the_owner/outputs/{tool_result['output_id']}"
        )
        assert other.status_code == 404


def test_web_redacts_tool_output_before_events_and_details(tmp_path):
    """敏感键不能因新增详情接口绕过 SSE 或 SQLite 的脱敏边界。"""
    with TestClient(_make_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        calculator = runtime.registry.get("calculator")
        runtime.registry.register(
            ToolDefinition(
                name=calculator.name,
                description=calculator.description,
                input_model=calculator.input_model,
                handler=lambda expression: {"token": "do-not-expose", "value": False},
                timeout_seconds=calculator.timeout_seconds,
                risk_level=calculator.risk_level,
                required_permission=calculator.required_permission,
                output_model=calculator.output_model,
                extra=calculator.extra,
            ),
            overwrite=True,
        )
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "计算 1 + 1", "agent_mode": "react"},
        ) as response:
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        tool_result = next(data for name, data in events if name == "tool_result")
        # 最终回答仍可引用工具结果；这里验证新增的工具事件与详情存储边界。
        assert "do-not-expose" not in json.dumps(tool_result, ensure_ascii=False)
        assert "[REDACTED]" in tool_result["data"]
        persisted = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        stored_tool = next(item["payload"] for item in persisted if item["event_type"] == "tool_result")
        assert "do-not-expose" not in json.dumps(stored_tool, ensure_ascii=False)
        detail = client.get(
            f"/api/web/executions/{execution_id}/outputs/{tool_result['output_id']}"
        ).json()
        assert detail["content"] == {"token_redacted": "[REDACTED]", "value": False}


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
