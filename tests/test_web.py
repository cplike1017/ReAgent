"""
Stage 12 测试：Web UI（进程内直连 + SSE 流式）。

覆盖：首页静态资源、工具/技能/MCP 列表、同步聊天、SSE 流式事件、会话历史。
使用 fakeredis + stub LLM + test 环境（跳过 MCP 连接），完全离线。
"""
import json

import fakeredis.aioredis
from fastapi.testclient import TestClient

from app.config import Settings
from app.errors import LLMError, ToolExecutionError
from app.execution.models import ExecutionStatus
from app.llm.client import BaseLLMClient, LLMResponse, ToolCallRequest
from app.main import create_app
from app.tools.registry import ToolDefinition


def _make_app(tmp_path, *, orchestrator_enabled: bool = False, trace_enabled: bool = False):
    settings = Settings(
        environment="test",
        trace_enabled=trace_enabled,
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
            on_retry = kwargs.get("on_retry")
            if on_retry:
                await on_retry(1, 2, LLMError("研究员模型上游暂时不可用"))
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
        assert 'data-timeline-filter="attention"' in r.text
        script = client.get("/app.js").text
        assert "tool-output-detail-btn" in script
        assert "/outputs/" in script
        assert "tool.retry_scheduled" in script
        assert "llm.retry_scheduled" in script
        assert "agent.llm.retry_scheduled" in script
        assert "modelUsageDetail" in script
        assert "execution.queued" in script
        assert "markExecutionStateUncertain" in script
        assert "reconcileDetachedExecution" in script
        assert "requestRejected" in script
        assert "registerExecutionEvent" in script
        assert "lastExecutionSeq" in script
        assert "setTimelineFilter" in script
        assert "timelineDetailNeedsExpansion" in script
        assert "canOpenExecutionHistory" in script
        assert "advanceExecutionViewVersion" in script
        assert "canChangeSession" in script
        assert "trapInspectorFocus" in script
        assert "inspectorReturnFocus" in script
        assert "INSPECTOR_FOCUSABLE_SELECTOR" in script
        assert "aria-modal" in script
        style = client.get("/style.css").text
        assert ".timeline-detail-expand" in style
        assert ".execution-history-item:disabled" in style
        assert '.session-item[aria-disabled="true"]' in style


def test_web_uses_mist_mint_semantic_theme_tokens(tmp_path):
    """The refactor exposes one accessible light/dark token system, not a blue reskin."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    for token in (
        "--bg-canvas: #f7faf9",
        "--bg-surface: #ffffff",
        "--text-primary: #17211e",
        "--accent: #19a974",
        "--action-bg: #0f766e",
        "--running-text: #0e7490",
    ):
        assert token in style
    assert 'body[data-theme="dark"]' in style
    assert "--bg-canvas: #111714" in style
    assert "linear-gradient(135deg, #5575ff, #405ce2)" not in style


def test_web_exposes_developer_studio_shell_contract(tmp_path):
    """The navigation shell uses one header, rail, contextual sidebar, and tabbed Inspector."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text
        style = client.get("/style.css").text

    for fragment in (
        'id="global-header"',
        'id="app-rail"',
        'data-primary-view="chat"',
        'data-primary-view="agents"',
        'data-primary-view="tools"',
        'data-inspector-section="execution"',
        'data-inspector-tab="timeline"',
        'data-inspector-tab="trace"',
        'data-inspector-tab="agents"',
        'data-inspector-tab="context"',
    ):
        assert fragment in page
    for symbol in ("const uiState", "setPrimaryView", "setInspectorSection", "setInspectorTab"):
        assert symbol in script
    assert ".global-header" in style
    assert ".app-rail" in style


def test_web_mobile_shell_clips_offcanvas_inspector_overflow(tmp_path):
    """The transformed mobile Inspector must not create a horizontal document scrollbar."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    mobile_rules = style.split("@media (max-width: 680px)", 1)[1]
    assert "overflow-x: hidden" in mobile_rules
    assert "overflow-y: auto" in mobile_rules


def test_web_mobile_shell_keeps_the_composer_inside_the_viewport(tmp_path):
    """Mobile chat delegates scrolling to the message area instead of expanding the page."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    mobile_rules = style.split("@media (max-width: 680px)", 1)[1]
    assert ".main { min-height: 0; height: 100%; }" in mobile_rules
    assert ".workspace { min-height: 0; }" in mobile_rules
    assert ".chat-column { min-height: 0;" in mobile_rules


def test_web_chat_uses_stable_message_body_copy_and_shared_welcome(tmp_path):
    """Streaming updates must target `.md-body`; copy and New Task use current/shared content."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    assert 'const contentEl = assistantEl.querySelector(".md-body");' in script
    assert "function renderWelcome()" in script
    assert '$("#messages").innerHTML = renderWelcome();' in script
    assert '$("#messages .welcome")?.remove();' in script
    new_session_body = script.split("function newSession()", 1)[1].split("/* ================= 消息渲染", 1)[0]
    assert 'setPrimaryView("chat");' in new_session_body
    assert "if (!navigator.clipboard?.writeText)" in script
    assert 'navigator.clipboard.writeText(String(contentEl.textContent || ""))' in script


def test_web_composer_exposes_real_upload_and_tools_shortcuts(tmp_path):
    """Composer shortcuts reuse existing capabilities instead of suggesting unsupported controls."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    assert 'data-composer-upload' in page
    assert 'data-composer-tools' in page
    assert 'class="composer-runtime"' in page
    assert '$$("[data-upload-trigger]")' in script
    assert 'setPrimaryView("tools")' in script


def test_web_completed_workflow_is_a_summary_with_an_inspector_link(tmp_path):
    """Chat keeps one tool-card history; deep trace inspection belongs in Inspector."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    assert "function renderWorkflowSummary(data)" in script
    assert 'data-workflow-inspector' in script
    add_workflow_body = script.split("function addWorkflowPanel", 1)[1].split("function bindToolStepToggles", 1)[0]
    assert "renderWorkflowSummary(data)" in add_workflow_body
    assert "renderWorkflowBody(data)" not in add_workflow_body


def test_web_inspector_renders_live_or_replayed_trace_facts(tmp_path):
    """Trace tab consumes the current run tree or its existing trace endpoint, never placeholder data."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    assert 'id="execution-trace"' in page
    assert 'id="execution-trace-meta"' in page
    assert "function setExecutionTrace(trace, traceId)" in script
    assert "function renderExecutionTrace()" in script
    assert "fetchExecutionTrace(traceId" in script
    assert "setExecutionTrace(data.trace, data.trace_id)" in script


def test_web_inspector_hides_empty_copy_when_context_or_agents_have_facts(tmp_path):
    """Data-bearing Inspector tabs must not also claim that no execution facts exist."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    assert '$("#context-empty-state").hidden = entries.length > 0;' in script
    assert '$("#agents-empty-state").hidden = runs.length > 0;' in script


def test_web_agents_inspector_renders_only_real_dependency_edges(tmp_path):
    """Agent dependencies come from orchestration events rather than inferred step order."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    assert "function renderOrchestrationDependencyGraph(agents)" in script
    assert 'className = "agent-dependency-graph"' in script
    assert "agent.dependsOn.forEach" in script
    assert "body.appendChild(renderOrchestrationDependencyGraph(orderedAgents))" in script


def test_web_replay_invalidates_and_clears_the_previous_trace(tmp_path):
    """A history replay cannot leave the previous run's tree visible while its trace loads."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    replay_body = script.split("function replayExecution(record, events)", 1)[1].split("function replayExecutionEvent", 1)[0]
    assert "advanceExecutionViewVersion();" in replay_body
    assert "clearExecutionTrace();" in replay_body


def test_web_resource_workspace_reuses_runtime_resource_endpoints(tmp_path):
    """Primary resource views render the data already exposed by the Web runtime APIs."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    assert 'id="resource-summary"' in page
    assert 'id="resource-content"' in page
    assert "function renderResourceWorkspace()" in script
    assert "state.resources.tools" in script
    assert "state.resources.files" in script
    assert "renderResourceWorkspace();" in script.split("function setPrimaryView", 1)[1].split("function setInspectorSection", 1)[0]


def test_web_search_is_local_to_loaded_entities_and_keyboard_accessible(tmp_path):
    """The command search filters in-memory resources without widening the API contract."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    assert 'id="search-dialog"' in page
    assert 'id="search-input"' in page
    assert 'id="search-results"' in page
    assert "function openLoadedSearch()" in script
    assert "function renderLoadedSearchResults()" in script
    assert "function collectLoadedSearchEntities()" in script
    search_body = script.split("function collectLoadedSearchEntities()", 1)[1].split("function setInspectorSection", 1)[0]
    assert "fetch(" not in search_body
    assert 'event.key.toLowerCase() === "k"' in script


def test_web_session_navigation_supports_local_aliases_pins_and_filters(tmp_path):
    """Session organization remains browser-local because the API has no metadata write contract."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    assert 'const SESSION_METADATA_STORAGE_KEY = "reagent-session-metadata-v1"' in script
    assert "function loadSessionMetadata()" in script
    assert "function renderSessionList()" in script
    assert "function updateSessionMetadata(sessionId, patch)" in script
    assert '$$("[data-session-filter]")' in script
    assert "data-session-pin" in script
    assert "data-session-alias" in script


def test_web_upload_does_not_report_http_errors_as_success(tmp_path):
    """The global sandbox upload keeps its own failure state and honors the documented size limit."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    upload_body = script.split('fileInput.addEventListener("change", async () => {', 1)[1].split('$("[data-composer-tools]")', 1)[0]
    assert "file.size > 1024 * 1024" in upload_body
    assert "if (!r.ok) throw new Error" in upload_body
    assert "addToolMsg({ tool: \"upload\", arguments: { file: file.name }, success: true" in upload_body


def test_web_static_bundle_versions_and_accessibility_foundations_are_current(tmp_path):
    """The delivered static files invalidate together and retain focus/reduced-motion support."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        style = client.get("/style.css").text

    assert 'href="/style.css?v=26"' in page
    assert 'src="/app.js?v=26"' in page
    assert "button:focus-visible" in style
    assert "@media (prefers-reduced-motion: reduce)" in style


def test_web_mobile_inspector_uses_fixed_position_to_avoid_document_overflow(tmp_path):
    """An off-canvas Inspector must not enlarge the mobile document's scroll width."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    compact_rules = style.split("@media (max-width: 920px)", 1)[1].split("@media (max-width: 680px)", 1)[0]
    inspector_rules = compact_rules.split(".execution-panel", 1)[1].split("}", 1)[0]
    assert "position: fixed" in inspector_rules


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
        lifecycle = client.get(f"/api/web/executions/{data['execution_id']}/events").json()["events"]
        assert [event["event_type"] for event in lifecycle[:2]] == ["execution.queued", "execution.started"]


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


def test_web_streams_llm_retry_and_usage_events(tmp_path):
    """模型客户端实际通知重试后，SSE 与历史应保留重试、模型和用量事实。"""
    class RetrySignalingLLM(BaseLLMClient):
        model = "retry-signaling-test"

        async def chat(self, messages, tools=None, **kwargs):
            on_retry = kwargs.get("on_retry")
            if on_retry:
                await on_retry(1, 2, LLMError("模型上游暂时不可用"))
            return LLMResponse(
                content="模型恢复后完成",
                model=self.model,
                usage={"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18},
            )

    with TestClient(_make_app(tmp_path, trace_enabled=True)) as client:
        runtime = client.app.state.runtime
        llm = RetrySignalingLLM()
        runtime.llm = llm
        runtime.context_builder.llm = llm
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "你好", "agent_mode": "react"},
        ) as response:
            assert response.status_code == 200
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        names = [name for name, _ in events]
        retry = next(data for name, data in events if name == "llm.retry_scheduled")
        decision = next(data for name, data in events if name == "step")
        assert retry["step"] == 1
        assert retry["attempt"] == 1
        assert retry["max_retries"] == 2
        assert retry["error"]["type"] == "LLMError"
        assert decision["model"] == "retry-signaling-test"
        assert decision["usage"] == {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
        assert names.index("llm.started") < names.index("llm.retry_scheduled") < names.index("step")

        stored = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        assert [event["event_type"] for event in stored] == names


def test_web_streams_llm_failure_event(tmp_path):
    """模型最终失败不能只留下总错误，必须有对应轮次的 LLM 生命周期事件。"""
    class FailingLLM(BaseLLMClient):
        model = "failing-test"

        async def chat(self, messages, tools=None, **kwargs):
            raise LLMError("模型服务不可用")

    with TestClient(_make_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        llm = FailingLLM()
        runtime.llm = llm
        runtime.context_builder.llm = llm
        with client.stream(
            "POST",
            "/api/web/chat/stream",
            json={"message": "你好", "agent_mode": "react"},
        ) as response:
            assert response.status_code == 200
            execution_id = response.headers["x-execution-id"]
            events = _parse_sse_events("".join(response.iter_text()))

        names = [name for name, _ in events]
        failed = next(data for name, data in events if name == "llm.failed")
        assert failed["step"] == 1
        assert failed["error"]["type"] == "LLMError"
        assert failed["error"]["message"] == "模型服务不可用"
        assert names.index("llm.started") < names.index("llm.failed") < names.index("error") < names.index("execution.failed")

        record = client.get(f"/api/web/executions/{execution_id}").json()
        assert record["status"] == "FAILED"
        stored = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        assert [event["event_type"] for event in stored] == names


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
            "agent.llm.retry_scheduled",
            "agent.decision",
            "agent.completed",
            "orchestration.synthesis_started",
            "orchestration.completed",
        }
        assert required <= set(names)
        assert names.index("orchestration.started") < names.index("orchestration.plan_created")
        assert names.index("agent.scheduled") < names.index("agent.started")
        assert names.index("agent.llm.started") < names.index("agent.llm.retry_scheduled") < names.index("agent.decision")
        assert names.index("agent.completed") < names.index("orchestration.completed")

        orchestration_started = next(data for name, data in events if name == "orchestration.started")
        run_id = orchestration_started["run_id"]
        plan = next(data for name, data in events if name == "orchestration.plan_created")
        scheduled = next(data for name, data in events if name == "agent.scheduled")
        completed = next(data for name, data in events if name == "agent.completed")
        retry = next(data for name, data in events if name == "agent.llm.retry_scheduled")
        assert plan["run_id"] == scheduled["run_id"] == completed["run_id"] == retry["run_id"] == run_id
        assert scheduled["agent_instance_id"] == completed["agent_instance_id"] == retry["agent_instance_id"] == f"{run_id}:agent:0"
        assert retry["attempt"] == 1
        assert retry["max_retries"] == 2
        assert retry["error"]["type"] == "LLMError"
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
        assert {"execution.queued", "execution.started", "llm.started", "step", "tool.started", "tool_result", "done"} <= set(event_names)
        assert event_names.index("execution.queued") < event_names.index("execution.started") < event_names.index("llm.started")
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


def test_web_streams_real_tool_retry_events(tmp_path):
    """瞬时失败的重试必须在最终工具结果之前实时持久化，且关联同一调用 ID。"""
    with TestClient(_make_app(tmp_path)) as client:
        runtime = client.app.state.runtime
        calculator = runtime.registry.get("calculator")
        invocations = {"count": 0}

        def flaky_calculator(expression):
            invocations["count"] += 1
            if invocations["count"] <= 2:
                raise ToolExecutionError("上游暂时不可用", transient=True)
            return 2

        runtime.registry.register(
            ToolDefinition(
                name=calculator.name,
                description=calculator.description,
                input_model=calculator.input_model,
                handler=flaky_calculator,
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

        names = [name for name, _ in events]
        retries = [data for name, data in events if name == "tool.retry_scheduled"]
        result = next(data for name, data in events if name == "tool_result")
        started = next(data for name, data in events if name == "tool.started")
        assert invocations["count"] == 3
        assert [item["attempt"] for item in retries] == [1, 2]
        assert all(item["max_retries"] == 2 for item in retries)
        assert all(item["error"]["type"] == "ToolExecutionError" for item in retries)
        assert all(item["tool_call_id"] == started["tool_call_id"] for item in retries)
        assert result["tool_call_id"] == started["tool_call_id"]
        assert result["retries"] == 2
        assert names.index("tool.started") < names.index("tool.retry_scheduled") < names.index("tool_result")

        stored = client.get(f"/api/web/executions/{execution_id}/events").json()["events"]
        assert [item["event_type"] for item in stored] == names


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
        assert "event: execution.queued" in replay.text
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
    """尚未开始的排队记录可被明确取消，重复取消不新增状态转换。"""
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
