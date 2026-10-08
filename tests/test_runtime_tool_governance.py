"""Persistent invocation identity and partial-batch recovery contracts."""
import pytest
from pydantic import BaseModel

from app.agent.react_loop import LoopHooks
from app.agent.runtime import AgentRuntime
from app.checkpoint.repository import SQLiteCheckpointRepository
from app.llm.client import LLMResponse, ToolCallRequest
from app.session.repository import SQLiteSessionRepository
from app.tools.registry import ToolDefinition, ToolRegistry
from app.tools.schemas import ToolResult


class Crash(Exception):
    pass


class EmptyArgs(BaseModel):
    pass


class BatchLLM:
    def __init__(self):
        self.chats = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.chats += 1
        if any(m["role"] == "tool" for m in messages):
            return LLMResponse(content="done")
        return LLMResponse(tool_calls=[
            ToolCallRequest(id="first", name="write", arguments={}),
            ToolCallRequest(id="second", name="write", arguments={}),
        ])


class RecordingGateway:
    def __init__(self):
        self.calls = []

    async def execute(self, name, args, user=None, **kwargs):
        self.calls.append(kwargs)
        return ToolResult.ok(name, "written")


def make_runtime(settings, gateway):
    registry = ToolRegistry()
    registry.register(ToolDefinition("write", "test write", EmptyArgs, lambda: "written"))
    return AgentRuntime(
        llm=BatchLLM(), registry=registry, tool_gateway=gateway, settings=settings,
        session_repo=SQLiteSessionRepository(settings.database_url),
        checkpoint_repo=SQLiteCheckpointRepository(settings.database_url),
    )


async def crash(*args):
    raise Crash()


async def test_invocation_identity_is_saved_before_execute_and_survives_new_turn(settings):
    gateway = RecordingGateway()
    runtime = make_runtime(settings, gateway)
    with pytest.raises(Crash):
        await runtime.run("write twice", session_id="s", turn_id="original",
                          extra_hooks=LoopHooks(after_decision=crash))
    checkpoint = runtime.checkpoint_repo.load_latest("s")
    pending = checkpoint.state["pending_tool_calls"]
    assert all(p.get("invocation_key") for p in pending)
    assert pending[0]["invocation_key"] != pending[1]["invocation_key"]
    restarted = make_runtime(settings, gateway)
    await restarted.resume("s", turn_id="recovery")
    assert [c["invocation_key"] for c in gateway.calls] == [p["invocation_key"] for p in pending]
    assert gateway.calls[0]["context"]["session_id"] == "s"
    assert gateway.calls[0]["context"]["tool_call_id"] == "first"


async def test_partial_batch_recovery_executes_remaining_call_and_preserves_pairing(settings):
    gateway = RecordingGateway()
    runtime = make_runtime(settings, gateway)
    with pytest.raises(Crash):
        await runtime.run("write twice", session_id="s", extra_hooks=LoopHooks(after_tool=crash))
    checkpoint = runtime.checkpoint_repo.load_latest("s")
    assert checkpoint.state["status"] == "PENDING_TOOL"
    assert [p["id"] for p in checkpoint.state["pending_tool_calls"]] == ["second"]
    restarted = make_runtime(settings, gateway)
    result = await restarted.resume("s")
    assert len(gateway.calls) == 2
    assistants = [m for m in result.messages if m.get("tool_calls")]
    assert len(assistants) == 1
    assert [m["tool_call_id"] for m in result.messages if m["role"] == "tool"] == ["first", "second"]
    saved = restarted.session_repo.list_messages("s")
    assert [m["tool_call_id"] for m in saved if m["role"] == "tool"] == ["first", "second"]


async def test_resuming_done_checkpoint_returns_saved_answer_without_llm(settings):
    gateway = RecordingGateway()
    runtime = make_runtime(settings, gateway)
    result = await runtime.run("write twice", session_id="s")
    calls_before = runtime.llm.chats
    resumed = await runtime.resume("s")
    assert resumed.answer == result.answer
    assert runtime.llm.chats == calls_before
    assert len(gateway.calls) == 2


async def test_subagent_inherits_caller_and_approval_context(settings, monkeypatch):
    from app.orchestrator.executor import SubAgentExecutor
    from app.orchestrator.profiles import AgentProfile
    from app.tools.context import current_tool_context
    from app.tools.schemas import UserContext

    gateway = RecordingGateway()
    captured_users = []
    original_execute = gateway.execute

    async def execute(name, args, user=None, **kwargs):
        captured_users.append(user)
        return await original_execute(name, args, user, **kwargs)

    gateway.execute = execute
    gateway.close = lambda: None
    monkeypatch.setattr("app.orchestrator.executor.ToolGateway", lambda *a, **kw: gateway)
    registry = ToolRegistry()
    registry.register(ToolDefinition("write", "test write", EmptyArgs, lambda: "written"))
    executor = SubAgentExecutor(llm=BatchLLM(), master_registry=registry, settings=settings)
    user = UserContext(user_id="u", permissions=["admin"])
    token = current_tool_context.set({"session_id": "s", "turn_id": "t",
                                     "invocation_key": "parent", "user": user,
                                     "on_approval": crash})
    try:
        result = await executor.execute(AgentProfile(name="writer", description="writer",
                                      system_prompt="write", max_steps=3), "write", agent_instance_id="child")
    finally:
        current_tool_context.reset(token)
    assert result.status == "SUCCEEDED"
    assert captured_users == [user, user]
    assert len({c["invocation_key"] for c in gateway.calls}) == 2
    assert all(c["on_approval"] is crash for c in gateway.calls)
    assert all(c["context"]["session_id"] == "s" for c in gateway.calls)


def test_checkpoint_lookup_is_scoped_to_original_job(settings):
    repo = SQLiteCheckpointRepository(settings.database_url)
    first = repo.save(session_id="s", turn_id="job1", step=1, state={"status": "PENDING_TOOL"})
    repo.save(session_id="s", turn_id="job2", step=1, state={"status": "DONE"})
    assert repo.load_latest_for_turn("s", "job1").checkpoint_id == first.checkpoint_id
    recovered = repo.save(session_id="s", turn_id="recovery", step=2,
                          state={"invocation_turn_id": "job1", "status": "DONE"})
    assert repo.load_latest_for_turn("s", "job1").checkpoint_id == recovered.checkpoint_id
    assert repo.load_latest_for_turn("s", "unknown") is None


async def test_plan_tools_have_distinct_persisted_invocation_keys(settings, monkeypatch):
    from app.agent.models import PlanStep

    class FixedPlanner:
        def __init__(self, *args, **kwargs):
            pass

        async def plan(self, task, memory_context=None):
            return [PlanStep(step_id="p1", description="write", tools_hint=["write"])]

    monkeypatch.setattr("app.agent.runtime.Planner", FixedPlanner)
    gateway = RecordingGateway()
    runtime = make_runtime(settings.model_copy(update={"agent_mode": "plan"}), gateway)
    await runtime.run("write", session_id="s")
    assert all(c.get("invocation_key") for c in gateway.calls)
    assert len({c["invocation_key"] for c in gateway.calls}) == len(gateway.calls)


async def test_plan_recovery_continues_partial_step_without_repeating_writes(settings, monkeypatch):
    from app.agent.models import PlanStep

    class ProcessCrash(BaseException):
        pass

    class FixedPlanner:
        plans = 0

        def __init__(self, *args, **kwargs):
            pass

        async def plan(self, task, memory_context=None):
            FixedPlanner.plans += 1
            return [PlanStep(step_id="p1", description="write", tools_hint=["write"])]

    async def crash_after_first(*args):
        raise ProcessCrash()

    monkeypatch.setattr("app.agent.runtime.Planner", FixedPlanner)
    gateway = RecordingGateway()
    plan_settings = settings.model_copy(update={"agent_mode": "plan"})
    runtime = make_runtime(plan_settings, gateway)
    with pytest.raises(ProcessCrash):
        await runtime.run("write", session_id="s", extra_hooks=LoopHooks(after_tool=crash_after_first))
    checkpoint = runtime.checkpoint_repo.load_latest("s")
    assert checkpoint.state["status"] == "PENDING_TOOL"
    assert checkpoint.state["plan"][0]["status"] == "RUNNING"
    restarted = make_runtime(plan_settings, gateway)
    await restarted.resume("s")
    assert len(gateway.calls) == 2
    assert FixedPlanner.plans == 1
    assert all(m["role"] != "tool" for m in restarted.session_repo.list_messages("s"))


async def test_direct_subagent_uses_orchestrator_session_context(settings, monkeypatch):
    from app.orchestrator.context import current_session_id
    from app.orchestrator.executor import SubAgentExecutor
    from app.orchestrator.profiles import AgentProfile

    gateway = RecordingGateway()
    gateway.close = lambda: None
    monkeypatch.setattr("app.orchestrator.executor.ToolGateway", lambda *a, **kw: gateway)
    registry = ToolRegistry()
    registry.register(ToolDefinition("write", "test write", EmptyArgs, lambda: "written"))
    executor = SubAgentExecutor(llm=BatchLLM(), master_registry=registry, settings=settings)
    token = current_session_id.set("direct-session")
    try:
        await executor.execute(AgentProfile(name="writer", description="writer", system_prompt="write", max_steps=3),
                               "write", orchestration_run_id="direct-run")
    finally:
        current_session_id.reset(token)
    assert all(c["context"]["session_id"] == "direct-session" for c in gateway.calls)
    assert all(c["context"]["turn_id"] == "direct-run" for c in gateway.calls)


async def test_plan_recovery_preserves_requested_replan_task(settings, monkeypatch):
    from app.agent.models import PlanStep

    class ProcessCrash(BaseException):
        pass

    class RecordingPlanner:
        tasks = []

        def __init__(self, *args, **kwargs):
            pass

        async def plan(self, task, memory_context=None):
            self.tasks.append(task)
            return [PlanStep(step_id="retry-step", description="write", tools_hint=["write"])]

    class NoToolsLLM:
        async def chat(self, messages, tools=None, **kwargs):
            return LLMResponse(content="No tool used")

    async def crash_on_replan(*args):
        raise ProcessCrash()

    monkeypatch.setattr("app.agent.runtime.Planner", RecordingPlanner)
    plan_settings = settings.model_copy(update={"agent_mode": "plan", "max_plan_revisions": 1})
    gateway = RecordingGateway()
    runtime = make_runtime(plan_settings, gateway)
    runtime.llm = NoToolsLLM()
    with pytest.raises(ProcessCrash):
        await runtime.run("original task", session_id="s", extra_hooks=LoopHooks(plan_revised=crash_on_replan))
    restarted = make_runtime(plan_settings, gateway)
    await restarted.resume("s")
    assert len(RecordingPlanner.tasks) == 2
    assert "此前失败" in RecordingPlanner.tasks[1]
    assert len(gateway.calls) == 2


async def test_plan_second_crash_preserves_resumed_step_transcript(settings, monkeypatch):
    from app.agent.models import PlanStep

    class ProcessCrash(BaseException):
        pass

    class FixedPlanner:
        def __init__(self, *args, **kwargs):
            pass

        async def plan(self, task, memory_context=None):
            return [PlanStep(step_id="p1", description="write", tools_hint=["write"])]

    async def process_crash(*args):
        raise ProcessCrash()

    monkeypatch.setattr("app.agent.runtime.Planner", FixedPlanner)
    plan_settings = settings.model_copy(update={"agent_mode": "plan"})
    gateway = RecordingGateway()
    runtime = make_runtime(plan_settings, gateway)
    with pytest.raises(ProcessCrash):
        await runtime.run("write", session_id="s", extra_hooks=LoopHooks(after_tool=process_crash))
    restarted = make_runtime(plan_settings, gateway)
    with pytest.raises(ProcessCrash):
        await restarted.resume("s", extra_hooks=LoopHooks(plan_step_started=process_crash))
    final_restart = make_runtime(plan_settings, gateway)
    await final_restart.resume("s")
    assert len(gateway.calls) == 2
