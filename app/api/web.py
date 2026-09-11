"""
Web UI 路由（Stage 12）。

提供进程内直连模式（不依赖 Redis 队列 / Worker，单进程演示友好）：
    POST /api/web/chat        同步聊天（完整结果：answer + plan + tool_calls + trace）
    POST /api/web/chat/stream SSE 流式（每步决策 / 工具调用实时推送）
    GET  /api/web/tools       工具列表（内置 + MCP）
    GET  /api/web/skills      技能列表
    GET  /api/web/mcp         MCP server 状态
    GET  /api/web/sessions    会话历史

运行时从 request.app.state.runtime 取（main.py lifespan 构建的 AppRuntime 容器）。
"""
import asyncio
import json
from typing import AsyncIterator, Literal
from uuid import uuid4

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from app.agent.react_loop import LoopHooks
from app.config import RUNTIME_EDITABLE_FIELDS, load_runtime_settings, save_runtime_settings
from app.errors import AgentError
from app.execution.models import ExecutionStatus
from app.orchestrator.events import OrchestrationHooks
from app.tools.schemas import ToolResult
from app.tracing.recorder import redact

router = APIRouter(prefix="/api/web", tags=["web"])


# QUEUED 表示服务已受理但尚未取得串行 Runtime 的执行槽；两者都可取消、续接。
_ACTIVE_EXECUTION_STATUSES = {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}


class WebChatRequest(BaseModel):
    """Web 聊天请求。"""

    message: str = Field(description="用户消息")
    session_id: str | None = Field(default=None, description="会话 ID；缺省自动创建")
    agent_mode: str | None = Field(default=None, description="react | plan；缺省用配置")


def _get_runtime(request: Request):
    runtime = getattr(request.app.state, "runtime", None)
    if runtime is None:
        raise HTTPException(status_code=503, detail="运行时未初始化")
    return runtime


def _get_execution_repository(request: Request):
    repository = getattr(request.app.state, "execution_repository", None)
    if repository is None:
        raise HTTPException(status_code=503, detail="执行记录未初始化")
    return repository


def _preview(value, limit: int = 800) -> tuple[str, bool]:
    """把事件输出限制为可读预览，保留 falsy 值并避免 SSE 传输无界膨胀。"""
    if isinstance(value, str):
        text = value
    else:
        text = json.dumps(value, ensure_ascii=False, default=str)
    return text[:limit], len(text) > limit


def _event_data(event, *, session_id: str, turn_id: str, payload: dict) -> dict:
    data = dict(payload)
    data.update(
        {
            "execution_id": event.execution_id,
            "session_id": session_id,
            "turn_id": turn_id,
            "seq": event.seq,
            "event_id": event.event_id,
            "timestamp": event.timestamp,
        }
    )
    return data


def _sse(event_type: str, payload: dict, seq: int | None = None) -> str:
    event_id = f"id: {seq}\n" if seq is not None else ""
    return f"{event_id}event: {event_type}\ndata: {json.dumps(payload, ensure_ascii=False)}\n\n"


# ---------------------------------------------------------------------------
# 同步聊天
# ---------------------------------------------------------------------------
@router.post("/chat")
async def web_chat(req: WebChatRequest, request: Request) -> dict:
    app = request.app.state
    runtime = _get_runtime(request)
    repository = _get_execution_repository(request)
    settings = app.settings
    agent_mode = req.agent_mode or settings.agent_mode
    session_id = req.session_id or f"session_{uuid4().hex[:12]}"
    turn_id = f"turn_{uuid4().hex[:12]}"
    execution_id = f"exec_{uuid4().hex[:12]}"

    input_preview = _preview(req.message, 240)[0]
    repository.create(
        execution_id=execution_id,
        session_id=session_id,
        turn_id=turn_id,
        agent_mode=agent_mode,
        input_preview=input_preview,
    )
    repository.append_event(
        execution_id,
        "execution.queued",
        {
            "mode": agent_mode,
            "message_preview": input_preview,
            "queue_reason": "runtime_busy" if app.web_runtime_lock.locked() else "dispatching",
        },
    )

    try:
        async with app.web_runtime_lock:
            started = repository.start(execution_id)
            if started.status != ExecutionStatus.RUNNING:
                raise AgentError("执行在启动前已终止", code="EXECUTION_NOT_ACTIVE")
            repository.append_event(
                execution_id,
                "execution.started",
                {"mode": agent_mode, "message_preview": input_preview},
            )
            old_mode = settings.agent_mode
            settings.agent_mode = agent_mode
            try:
                result = await runtime.run(
                    req.message, session_id=session_id, turn_id=turn_id
                )
            finally:
                settings.agent_mode = old_mode
    except AgentError as exc:
        repository.finish(
            execution_id,
            status=ExecutionStatus.FAILED,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        repository.append_event(
            execution_id,
            "execution.failed",
            {"type": type(exc).__name__, "message": str(exc)},
        )
        raise HTTPException(status_code=502, detail={"type": type(exc).__name__, "message": str(exc)})
    except Exception as exc:
        repository.finish(
            execution_id,
            status=ExecutionStatus.FAILED,
            error_type=type(exc).__name__,
            error_message=str(exc),
        )
        repository.append_event(
            execution_id,
            "execution.failed",
            {"type": type(exc).__name__, "message": str(exc)},
        )
        raise HTTPException(status_code=500, detail={"type": type(exc).__name__, "message": str(exc)})

    repository.finish(
        execution_id,
        status=ExecutionStatus.SUCCEEDED,
        trace_id=result.trace_id,
        answer_preview=_preview(result.answer, 400)[0],
    )
    repository.append_event(
        execution_id,
        "execution.completed",
        {
            "steps": result.steps,
            "tool_calls": len(result.tool_calls),
            "trace_id": result.trace_id,
        },
    )

    return {
        "execution_id": execution_id,
        "session_id": result.session_id,
        "answer": result.answer,
        "steps": result.steps,
        "plan": [step.model_dump() for step in result.plan],
        "plan_revisions": result.plan_revisions,
        "tool_calls": [
            {"tool_call_id": call.id, "name": call.name, "arguments": redact(call.arguments)}
            for call in result.tool_calls
        ],
        "trace_id": result.trace_id,
        "checkpoint_id": result.checkpoint_id,
        "mode": agent_mode,
        "trace": _build_trace_tree(request, result.trace_id),
    }


def _build_trace_tree(request: Request, trace_id: str | None) -> dict | None:
    """构建 Trace 调用树（供前端渲染 Agent 工作流）。"""
    if not trace_id:
        return None
    recorder = getattr(request.app.state, "recorder", None)
    if recorder is None or not recorder.enabled:
        return None
    try:
        tree = recorder.build_tree(trace_id)
        return tree if tree.get("spans") else None
    except Exception:
        return None


def _tool_calls_with_duration(result, trace_tree: dict | None) -> list[dict]:
    """把工具调用列表与 Trace 中的耗时关联（按顺序配对 tool.execute span）。"""
    calls = [{"tool_call_id": tc.id, "name": tc.name, "arguments": redact(tc.arguments)} for tc in result.tool_calls]
    if not trace_tree or not calls:
        return calls
    # 收集 trace 树中所有 tool.execute span 的耗时（按出现顺序）
    durations: list[dict] = []

    def walk(nodes):
        for n in nodes:
            if n.get("name") == "tool.execute":
                durations.append({
                    "duration_ms": n.get("duration_ms"),
                    "status": n.get("status"),
                    "error": n.get("error"),
                })
            walk(n.get("children", []))

    walk(trace_tree.get("spans", []))
    for i, call in enumerate(calls):
        if i < len(durations):
            call.update(durations[i])
    return calls


# ---------------------------------------------------------------------------
# SSE 流式聊天（复用 LoopHooks 推送事件）
# ---------------------------------------------------------------------------
def _plan_step_payload(step, *, plan_version: int, total_steps: int) -> dict:
    """Plan 步骤事件的稳定关联字段；步骤快照保留真实状态和结果。"""
    data = step.model_dump(mode="json")
    return {
        "plan_version": plan_version,
        "plan_step_id": data["step_id"],
        "total_steps": total_steps,
        "step": data,
    }


def _orchestration_step_payload(run_id: str, agent_instance_id: str, step_index: int, step) -> dict:
    """子 Agent 调度事件的稳定关联字段；依赖边来自真实编排计划。"""
    data = step.model_dump(mode="json")
    return {
        "run_id": run_id,
        "agent_instance_id": agent_instance_id,
        "step_index": step_index,
        "agent_profile": data["agent"],
        "task_preview": _preview(data["task"], 240)[0],
        "depends_on": data.get("depends_on") or [],
    }


@router.post("/chat/stream")
async def web_chat_stream(req: WebChatRequest, request: Request) -> StreamingResponse:
    runtime = _get_runtime(request)
    app = request.app.state
    settings = app.settings
    repository = _get_execution_repository(request)
    agent_mode = req.agent_mode or settings.agent_mode
    session_id = req.session_id or f"session_{uuid4().hex[:12]}"
    turn_id = f"turn_{uuid4().hex[:12]}"
    execution_id = f"exec_{uuid4().hex[:12]}"

    repository.create(
        execution_id=execution_id,
        session_id=session_id,
        turn_id=turn_id,
        agent_mode=agent_mode,
        input_preview=_preview(req.message, 240)[0],
    )

    async def event_gen() -> AsyncIterator[str]:
        queue: asyncio.Queue = asyncio.Queue()
        client_connected = True

        async def _emit(event_type: str, payload: dict) -> None:
            recorded = repository.append_event(execution_id, event_type, payload)
            data = _event_data(
                recorded,
                session_id=session_id,
                turn_id=turn_id,
                payload=payload,
            )
            if client_connected:
                await queue.put(_sse(event_type, data, recorded.seq))

        async def _hook_before_llm(step: int, messages: list[dict]) -> None:
            await _emit(
                "llm.started",
                {"step": step, "message_count": len(messages)},
            )

        async def _hook_llm_retry_scheduled(step: int, attempt: int, max_retries: int, error) -> None:
            await _emit(
                "llm.retry_scheduled",
                {
                    "step": step,
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "error": _retry_error_payload(error),
                },
            )

        async def _hook_llm_failed(step: int, error) -> None:
            await _emit(
                "llm.failed",
                {"step": step, "error": _retry_error_payload(error)},
            )

        def _usage_payload(response) -> dict:
            usage = response.usage or {}
            return {
                key: usage[key]
                for key in ("prompt_tokens", "completion_tokens", "total_tokens")
                if isinstance(usage.get(key), (int, float))
            }

        async def _hook_after_decision(response, step: int) -> None:
            await _emit(
                "step",
                {
                    "step": step,
                    "tool_calls": [
                        {
                            "id": tool_call.id,
                            "tool_call_id": tool_call.id,
                            "name": tool_call.name,
                            "arguments": redact(tool_call.arguments),
                        }
                        for tool_call in response.tool_calls
                    ],
                    "content_preview": _preview(response.content or "", 200)[0],
                    "is_final": response.is_final_answer,
                    "model": response.model or settings.llm_model,
                    "usage": _usage_payload(response),
                },
            )

        async def _hook_before_tool(tc, step: int) -> None:
            await _emit(
                "tool.started",
                {
                    "step": step,
                    "tool": tc.name,
                    "tool_call_id": tc.id,
                    "arguments": redact(tc.arguments),
                },
            )

        def _retry_error_payload(error) -> dict:
            if hasattr(error, "model_dump"):
                return error.model_dump(mode="json")
            return {"type": type(error).__name__, "message": str(error)}

        async def _hook_tool_retry_scheduled(tc, step: int, attempt: int, max_retries: int, error) -> None:
            await _emit(
                "tool.retry_scheduled",
                {
                    "step": step,
                    "tool": tc.name,
                    "tool_call_id": tc.id,
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "error": _retry_error_payload(error),
                },
            )

        def _tool_output_payload(envelope: ToolResult, *, kind: str) -> dict:
            """为 SSE 生成受限预览，并把完整脱敏值留在按需详情存储中。"""
            # 数据边界：实时事件与详情存储均使用相同的脱敏值；不让详情接口
            # 成为 SSE 预览之外的敏感数据旁路。
            safe_output = redact(envelope.data)
            output, truncated = _preview(safe_output, 1000)
            has_output = envelope.success or envelope.data is not None
            detail = None
            detail_unavailable = False
            if has_output:
                try:
                    detail = repository.store_output(
                        execution_id,
                        kind=kind,
                        content=envelope.data,
                    )
                except Exception:
                    # 详情采集失败不应掩盖真实工具结果；前端会明确没有可加载详情。
                    detail_unavailable = True
            return {
                # None 可能是成功工具的真实返回值；失败信封的默认 None 则不应
                # 被前端误展示为“null 输出”。用独立字段保留这个事实边界。
                "has_output": has_output,
                "data": output,
                "output_truncated": truncated,
                "output_type": type(envelope.data).__name__,
                "output_id": detail.output_id if detail is not None else None,
                "output_available": detail is not None,
                "output_detail_unavailable": detail_unavailable,
                "output_detail_truncated": detail.truncated if detail is not None else False,
                "output_bytes": detail.original_bytes if detail is not None else None,
            }

        async def _hook_after_tool(tc, envelope: ToolResult, step: int) -> None:
            payload = {
                "step": step,
                "tool": tc.name,
                "tool_call_id": tc.id,
                "arguments": redact(tc.arguments),
                "success": envelope.success,
                "error": envelope.error.model_dump() if envelope.error else None,
                "duration_ms": (envelope.metadata or {}).get("duration_ms"),
                "retries": (envelope.metadata or {}).get("retries", 0),
            }
            payload.update(_tool_output_payload(envelope, kind="tool.result"))
            await _emit("tool_result", payload)

        async def _hook_before_final(response, step: int) -> None:
            await _emit(
                "final",
                {"step": step, "content": response.content or ""},
            )

        async def _hook_context_built(built, step: int) -> None:
            await _emit(
                "context.completed",
                {
                    "step": step,
                    "total_history": built.total_history,
                    "selected_messages": built.selected,
                    "estimated_tokens": built.estimated_tokens,
                    "tool_schema_count": len(built.tools),
                    "retrieved_documents": built.retrieved_documents,
                    "has_summary": built.summary is not None,
                    "summary_length": len(built.summary or ""),
                },
            )

        async def _hook_memory_retrieved(_query: str, docs: list[str], purpose: str) -> None:
            # 事件只记录命中规模和用途；查询与记忆正文不因透明化而额外落库。
            await _emit(
                "memory.retrieved",
                {
                    "purpose": purpose,
                    "hit_count": len(docs),
                },
            )

        async def _hook_skills_selected(skills: list) -> None:
            await _emit(
                "skill.selected",
                {
                    "count": len(skills),
                    "skills": [skill.name for skill in skills],
                },
            )

        async def _hook_checkpoint_saved(checkpoint, point: str) -> None:
            await _emit(
                "checkpoint.saved",
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "checkpoint_version": checkpoint.version,
                    "state_step": checkpoint.step,
                    "state_status": (checkpoint.state or {}).get("status", ""),
                    "point": point,
                },
            )

        async def _hook_checkpoint_restored(checkpoint) -> None:
            await _emit(
                "checkpoint.restored",
                {
                    "checkpoint_id": checkpoint.checkpoint_id,
                    "checkpoint_version": checkpoint.version,
                    "state_step": checkpoint.step,
                    "state_status": (checkpoint.state or {}).get("status", ""),
                },
            )

        async def _hook_memory_stored(count: int) -> None:
            await _emit("memory.stored", {"stored_count": count})

        async def _hook_plan_created(plan, plan_version: int, task: str) -> None:
            await _emit(
                "plan.created",
                {
                    "plan_version": plan_version,
                    "total_steps": len(plan),
                    "task_preview": _preview(task, 240)[0],
                    "steps": [step.model_dump(mode="json") for step in plan],
                },
            )

        async def _hook_plan_degraded(plan_version: int, task: str, reason: str) -> None:
            await _emit(
                "plan.degraded",
                {
                    "plan_version": plan_version,
                    "task_preview": _preview(task, 240)[0],
                    "reason": reason,
                },
            )

        async def _hook_plan_step_started(step, plan_version: int, total_steps: int) -> None:
            await _emit(
                "plan_step.started",
                _plan_step_payload(step, plan_version=plan_version, total_steps=total_steps),
            )

        async def _hook_plan_step_completed(step, plan_version: int, total_steps: int) -> None:
            await _emit(
                "plan_step.completed",
                _plan_step_payload(step, plan_version=plan_version, total_steps=total_steps),
            )

        async def _hook_plan_step_failed(step, plan_version: int, total_steps: int) -> None:
            await _emit(
                "plan_step.failed",
                _plan_step_payload(step, plan_version=plan_version, total_steps=total_steps),
            )

        async def _hook_plan_summarize_started(plan, plan_version: int) -> None:
            await _emit(
                "plan.summarize_started",
                {
                    "plan_version": plan_version,
                    "total_steps": len(plan),
                    "succeeded_steps": sum(step.status == "SUCCEEDED" for step in plan),
                    "failed_steps": sum(step.status == "FAILED" for step in plan),
                },
            )

        async def _hook_reflection_completed(decision, plan_version: int) -> None:
            await _emit(
                "reflection.completed",
                {
                    "plan_version": plan_version,
                    "need_replan": decision.need_replan,
                    "reason": decision.reason,
                    "revised_task_preview": _preview(decision.revised_task, 240)[0]
                    if decision.need_replan and decision.revised_task
                    else "",
                },
            )

        async def _hook_plan_revised(previous_plan, previous_version: int, next_version: int, decision) -> None:
            await _emit(
                "plan.revised",
                {
                    "previous_plan_version": previous_version,
                    "next_plan_version": next_version,
                    "reason": decision.reason,
                    "previous_steps": [step.model_dump(mode="json") for step in previous_plan],
                },
            )

        async def _hook_orchestration_started(run_id, parent_run_id, depth: int, task: str, agents) -> None:
            await _emit(
                "orchestration.started",
                {
                    "run_id": run_id,
                    "parent_run_id": parent_run_id,
                    "depth": depth,
                    "task_preview": _preview(task, 240)[0],
                    "requested_agents": agents or [],
                },
            )

        async def _hook_orchestration_plan_created(run_id, plan) -> None:
            await _emit(
                "orchestration.plan_created",
                {
                    "run_id": run_id,
                    "rationale_preview": _preview(plan.rationale, 360)[0],
                    "total_agents": len(plan.steps),
                    "steps": [
                        _orchestration_step_payload(
                            run_id,
                            f"{run_id}:agent:{index}",
                            index,
                            step,
                        )
                        for index, step in enumerate(plan.steps)
                    ],
                },
            )

        async def _hook_agent_scheduled(run_id, agent_instance_id, step_index: int, step) -> None:
            await _emit(
                "agent.scheduled",
                _orchestration_step_payload(run_id, agent_instance_id, step_index, step),
            )

        async def _hook_agent_started(run_id, agent_instance_id, step_index: int, step) -> None:
            await _emit(
                "agent.started",
                _orchestration_step_payload(run_id, agent_instance_id, step_index, step),
            )

        async def _hook_agent_llm_started(run_id, agent_instance_id, profile: str, step: int) -> None:
            await _emit(
                "agent.llm.started",
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "agent_profile": profile,
                    "step": step,
                },
            )

        async def _hook_agent_llm_retry_scheduled(
            run_id,
            agent_instance_id,
            profile: str,
            step: int,
            attempt: int,
            max_retries: int,
            error,
        ) -> None:
            await _emit(
                "agent.llm.retry_scheduled",
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "agent_profile": profile,
                    "step": step,
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "error": _retry_error_payload(error),
                },
            )

        async def _hook_agent_llm_failed(run_id, agent_instance_id, profile: str, step: int, error) -> None:
            await _emit(
                "agent.llm.failed",
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "agent_profile": profile,
                    "step": step,
                    "error": _retry_error_payload(error),
                },
            )

        async def _hook_agent_decision(run_id, agent_instance_id, profile: str, response, step: int) -> None:
            await _emit(
                "agent.decision",
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "agent_profile": profile,
                    "step": step,
                    "is_final": response.is_final_answer,
                    "content_preview": _preview(response.content or "", 360)[0] if response.is_final_answer else "",
                    "model": response.model or settings.llm_model,
                    "usage": _usage_payload(response),
                    "tool_calls": [
                        {"tool_call_id": tc.id, "name": tc.name, "arguments": redact(tc.arguments)}
                        for tc in response.tool_calls
                    ],
                },
            )

        async def _hook_agent_tool_started(run_id, agent_instance_id, profile: str, tc, step: int) -> None:
            await _emit(
                "agent.tool.started",
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "agent_profile": profile,
                    "step": step,
                    "tool_call_id": tc.id,
                    "tool": tc.name,
                    "arguments": redact(tc.arguments),
                },
            )

        async def _hook_agent_tool_retry_scheduled(
            run_id,
            agent_instance_id,
            profile: str,
            tc,
            step: int,
            attempt: int,
            max_retries: int,
            error,
        ) -> None:
            await _emit(
                "agent.tool.retry_scheduled",
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "agent_profile": profile,
                    "step": step,
                    "tool_call_id": tc.id,
                    "tool": tc.name,
                    "attempt": attempt,
                    "max_retries": max_retries,
                    "error": _retry_error_payload(error),
                },
            )

        async def _hook_agent_tool_completed(run_id, agent_instance_id, profile: str, tc, envelope: ToolResult, step: int) -> None:
            payload = {
                "run_id": run_id,
                "agent_instance_id": agent_instance_id,
                "agent_profile": profile,
                "step": step,
                "tool_call_id": tc.id,
                "tool": tc.name,
                "arguments": redact(tc.arguments),
                "success": envelope.success,
                "error": envelope.error.model_dump() if envelope.error else None,
                "duration_ms": (envelope.metadata or {}).get("duration_ms"),
                "retries": (envelope.metadata or {}).get("retries", 0),
            }
            payload.update(_tool_output_payload(envelope, kind="agent.tool.result"))
            await _emit("agent.tool.completed", payload)

        async def _hook_agent_finished(event_name: str, run_id, agent_instance_id, step_index: int, result) -> None:
            await _emit(
                event_name,
                {
                    "run_id": run_id,
                    "agent_instance_id": agent_instance_id,
                    "step_index": step_index,
                    "agent_profile": result.agent,
                    "task_preview": _preview(result.task, 240)[0],
                    "status": result.status,
                    "answer_preview": _preview(result.answer, 600)[0] if result.answer else "",
                    "error": result.error,
                    "steps": result.steps,
                    "tool_calls": len(result.tool_calls),
                    "duration_ms": result.duration_ms,
                },
            )

        async def _hook_agent_completed(run_id, agent_instance_id, step_index: int, result) -> None:
            await _hook_agent_finished("agent.completed", run_id, agent_instance_id, step_index, result)

        async def _hook_agent_failed(run_id, agent_instance_id, step_index: int, result) -> None:
            await _hook_agent_finished("agent.failed", run_id, agent_instance_id, step_index, result)

        async def _hook_agent_skipped(run_id, agent_instance_id, step_index: int, result) -> None:
            await _hook_agent_finished("agent.skipped", run_id, agent_instance_id, step_index, result)

        async def _hook_orchestration_synthesis_started(run_id, results) -> None:
            await _emit(
                "orchestration.synthesis_started",
                {
                    "run_id": run_id,
                    "total_agents": len(results),
                    "succeeded_agents": sum(result.status == "SUCCEEDED" for result in results),
                    "failed_agents": sum(result.status == "FAILED" for result in results),
                    "skipped_agents": sum(result.status == "SKIPPED" for result in results),
                },
            )

        async def _hook_orchestration_completed(run_id, result) -> None:
            await _emit(
                "orchestration.completed",
                {
                    "run_id": run_id,
                    "status": result.status,
                    "duration_ms": result.duration_ms,
                    "total_agents": len(result.agent_results),
                    "succeeded_agents": sum(item.status == "SUCCEEDED" for item in result.agent_results),
                    "failed_agents": sum(item.status == "FAILED" for item in result.agent_results),
                    "skipped_agents": sum(item.status == "SKIPPED" for item in result.agent_results),
                },
            )

        async def _hook_orchestration_failed(run_id, parent_run_id, depth: int, error: str) -> None:
            await _emit(
                "orchestration.failed",
                {
                    "run_id": run_id,
                    "parent_run_id": parent_run_id,
                    "depth": depth,
                    "error": error,
                },
            )

        orchestration_hooks = OrchestrationHooks(
            orchestration_started=_hook_orchestration_started,
            orchestration_plan_created=_hook_orchestration_plan_created,
            agent_scheduled=_hook_agent_scheduled,
            agent_started=_hook_agent_started,
            agent_llm_started=_hook_agent_llm_started,
            agent_llm_retry_scheduled=_hook_agent_llm_retry_scheduled,
            agent_llm_failed=_hook_agent_llm_failed,
            agent_decision=_hook_agent_decision,
            agent_tool_started=_hook_agent_tool_started,
            agent_tool_retry_scheduled=_hook_agent_tool_retry_scheduled,
            agent_tool_completed=_hook_agent_tool_completed,
            agent_completed=_hook_agent_completed,
            agent_failed=_hook_agent_failed,
            agent_skipped=_hook_agent_skipped,
            orchestration_synthesis_started=_hook_orchestration_synthesis_started,
            orchestration_completed=_hook_orchestration_completed,
            orchestration_failed=_hook_orchestration_failed,
        )

        hooks = LoopHooks(
            before_llm=_hook_before_llm,
            after_decision=_hook_after_decision,
            llm_retry_scheduled=_hook_llm_retry_scheduled,
            llm_failed=_hook_llm_failed,
            before_tool=_hook_before_tool,
            after_tool=_hook_after_tool,
            tool_retry_scheduled=_hook_tool_retry_scheduled,
            before_final=_hook_before_final,
            context_built=_hook_context_built,
            memory_retrieved=_hook_memory_retrieved,
            skills_selected=_hook_skills_selected,
            checkpoint_saved=_hook_checkpoint_saved,
            checkpoint_restored=_hook_checkpoint_restored,
            memory_stored=_hook_memory_stored,
            plan_created=_hook_plan_created,
            plan_degraded=_hook_plan_degraded,
            plan_step_started=_hook_plan_step_started,
            plan_step_completed=_hook_plan_step_completed,
            plan_step_failed=_hook_plan_step_failed,
            plan_summarize_started=_hook_plan_summarize_started,
            reflection_completed=_hook_reflection_completed,
            plan_revised=_hook_plan_revised,
        )

        async def _run() -> None:
            try:
                current = repository.get(execution_id)
                if current is None or current.status not in _ACTIVE_EXECUTION_STATUSES:
                    return
                await _emit(
                    "execution.queued",
                    {
                        "mode": agent_mode,
                        "message_preview": _preview(req.message, 240)[0],
                        "queue_reason": "runtime_busy" if app.web_runtime_lock.locked() else "dispatching",
                    },
                )
                async with app.web_runtime_lock:
                    started = repository.start(execution_id)
                    if started.status != ExecutionStatus.RUNNING:
                        return
                    await _emit(
                        "execution.started",
                        {
                            "mode": agent_mode,
                            "message_preview": _preview(req.message, 240)[0],
                        },
                    )
                    old_mode = settings.agent_mode
                    settings.agent_mode = agent_mode
                    try:
                        result = await runtime.run(
                            req.message,
                            session_id=session_id,
                            turn_id=turn_id,
                            extra_hooks=hooks,
                            orchestration_hooks=orchestration_hooks,
                        )
                    finally:
                        settings.agent_mode = old_mode

                trace_tree = _build_trace_tree(request, result.trace_id)
                repository.finish(
                    execution_id,
                    status=ExecutionStatus.SUCCEEDED,
                    trace_id=result.trace_id,
                    answer_preview=_preview(result.answer, 400)[0],
                )
                await _emit(
                    "done",
                    {
                        "session_id": result.session_id,
                        "answer": result.answer,
                        "tool_calls": _tool_calls_with_duration(result, trace_tree),
                        "plan": [step.model_dump() for step in result.plan],
                        "plan_revisions": result.plan_revisions,
                        "plan_version": result.plan_revisions + 1 if result.plan else None,
                        "trace_id": result.trace_id,
                        "trace": trace_tree,
                    },
                )
                await _emit(
                    "execution.completed",
                    {
                        "steps": result.steps,
                        "tool_calls": len(result.tool_calls),
                        "trace_id": result.trace_id,
                    },
                )
            except asyncio.CancelledError:
                repository.finish(
                    execution_id,
                    status=ExecutionStatus.CANCELLED,
                    error_type="CancelledError",
                    error_message="用户取消了执行。",
                )
                await _emit(
                    "execution.cancelled",
                    {"message": "服务端已确认取消执行。"},
                )
                raise
            except AgentError as exc:
                repository.finish(
                    execution_id,
                    status=ExecutionStatus.FAILED,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                await _emit("error", {"type": type(exc).__name__, "message": str(exc)})
                await _emit("execution.failed", {"type": type(exc).__name__, "message": str(exc)})
            except Exception as exc:
                repository.finish(
                    execution_id,
                    status=ExecutionStatus.FAILED,
                    error_type=type(exc).__name__,
                    error_message=str(exc),
                )
                await _emit("error", {"type": type(exc).__name__, "message": str(exc)})
                await _emit("execution.failed", {"type": type(exc).__name__, "message": str(exc)})
            finally:
                app.web_execution_tasks.pop(execution_id, None)
                if client_connected:
                    await queue.put(None)

        async def _confirm_cancelled_before_start() -> None:
            """覆盖 task.cancel() 发生在协程首次调度之前的极早取消窗口。"""
            record = repository.get(execution_id)
            if record is None or record.status not in _ACTIVE_EXECUTION_STATUSES:
                return
            finished = repository.finish(
                execution_id,
                status=ExecutionStatus.CANCELLED,
                error_type="CancelledBeforeStart",
                error_message="用户在执行任务启动前取消了请求。",
            )
            if finished.status != ExecutionStatus.CANCELLED:
                return
            await _emit(
                "execution.cancelled",
                {"message": "服务端已确认取消执行。"},
            )
            app.web_execution_tasks.pop(execution_id, None)
            if client_connected:
                await queue.put(None)

        task = asyncio.create_task(_run())
        app.web_execution_tasks[execution_id] = task

        def _handle_task_done(completed_task: asyncio.Task) -> None:
            # _run 捕获取消时会自行记账；若它从未获得运行机会，这里补写终态。
            if completed_task.cancelled():
                asyncio.create_task(_confirm_cancelled_before_start())

        task.add_done_callback(_handle_task_done)
        try:
            while True:
                item = await queue.get()
                if item is None:
                    break
                yield item
        finally:
            # 断开 SSE 仅停止当前订阅；任务继续运行并持续写入 SQLite，
            # 用户可通过 execution_id 从事件序列重新接续。
            client_connected = False
            if task.done():
                await asyncio.gather(task, return_exceptions=True)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={"X-Execution-ID": execution_id, "Cache-Control": "no-cache"},
    )


@router.get("/executions/{execution_id}")
async def web_execution(execution_id: str, request: Request) -> dict:
    record = _get_execution_repository(request).get(execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"执行不存在: {execution_id}")
    return record.model_dump(mode="json")


@router.get("/executions/{execution_id}/events")
async def web_execution_events(
    execution_id: str,
    request: Request,
    after_seq: int = 0,
    limit: int = 500,
) -> dict:
    repository = _get_execution_repository(request)
    record = repository.get(execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"执行不存在: {execution_id}")
    events = repository.list_events(execution_id, after_seq=after_seq, limit=limit)
    return {
        "execution_id": execution_id,
        "last_seq": record.last_seq,
        "events": [event.model_dump(mode="json") for event in events],
    }


@router.get("/executions/{execution_id}/outputs/{output_id}")
async def web_execution_output(execution_id: str, output_id: str, request: Request) -> dict:
    """按需读取与本次运行关联的受控工具输出详情。"""
    repository = _get_execution_repository(request)
    if repository.get(execution_id) is None:
        raise HTTPException(status_code=404, detail=f"执行不存在: {execution_id}")
    output = repository.get_output(execution_id, output_id)
    if output is None:
        raise HTTPException(status_code=404, detail=f"执行输出不存在: {output_id}")
    return output.model_dump(mode="json")


@router.get("/sessions/{session_id}/executions")
async def web_session_executions(
    session_id: str,
    request: Request,
    limit: int = 50,
) -> dict:
    records = _get_execution_repository(request).list_for_session(session_id, limit=limit)
    return {
        "session_id": session_id,
        "executions": [record.model_dump(mode="json") for record in records],
    }


@router.get("/executions/{execution_id}/stream")
async def web_execution_stream(
    execution_id: str,
    request: Request,
    after_seq: int = 0,
) -> StreamingResponse:
    """从持久化事件序列回放，并在运行中以短轮询方式续接新事件。"""
    repository = _get_execution_repository(request)
    initial = repository.get(execution_id)
    if initial is None:
        raise HTTPException(status_code=404, detail=f"执行不存在: {execution_id}")

    async def replay() -> AsyncIterator[str]:
        current_seq = max(0, after_seq)
        while True:
            record = repository.get(execution_id)
            if record is None:
                return
            events = repository.list_events(execution_id, after_seq=current_seq)
            for event in events:
                current_seq = event.seq
                payload = _event_data(
                    event,
                    session_id=record.session_id,
                    turn_id=record.turn_id,
                    payload=event.payload,
                )
                yield _sse(event.event_type, payload, event.seq)

            record = repository.get(execution_id)
            if record is None:
                return
            # 终态也可能有多页事件：先完整吐完 seq，再结束流。
            if current_seq < record.last_seq:
                continue
            if record.status not in _ACTIVE_EXECUTION_STATUSES:
                return
            yield ": keep-alive\n\n"
            await asyncio.sleep(0.35)

    return StreamingResponse(
        replay(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache"},
    )


@router.post("/executions/{execution_id}/cancel")
async def web_execution_cancel(execution_id: str, request: Request) -> dict:
    """请求取消直连 Web 执行；终态记录保持幂等。"""
    repository = _get_execution_repository(request)
    record = repository.get(execution_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"执行不存在: {execution_id}")
    if record.status not in _ACTIVE_EXECUTION_STATUSES:
        return {
            "execution_id": execution_id,
            "status": record.status.value,
            "cancel_requested": False,
        }

    repository.append_event(execution_id, "execution.cancel_requested", {})
    task = getattr(request.app.state, "web_execution_tasks", {}).get(execution_id)
    if task is not None and not task.done():
        task.cancel()
        return {
            "execution_id": execution_id,
            "status": record.status.value,
            "cancel_requested": True,
        }

    # 没有活动任务时不能继续执行；直接落入已取消终态，避免永久显示运行中。
    repository.finish(
        execution_id,
        status=ExecutionStatus.CANCELLED,
        error_type="NoActiveTask",
        error_message="未找到活动执行任务。",
    )
    repository.append_event(
        execution_id,
        "execution.cancelled",
        {"message": "未找到活动任务，已结束该执行记录。"},
    )
    return {
        "execution_id": execution_id,
        "status": ExecutionStatus.CANCELLED.value,
        "cancel_requested": False,
    }


@router.get("/tools")
async def web_tools(request: Request) -> dict:
    runtime = _get_runtime(request)
    tools = []
    for t in runtime.registry.all():
        tools.append({
            "name": t.name,
            "description": t.description,
            "risk_level": t.risk_level,
            "required_permission": t.required_permission,
        })
    return {"tools": tools, "count": len(tools)}


# ---------------------------------------------------------------------------
# 运行时配置：密钥仅可写，保存后由 API / Worker 重启加载
# ---------------------------------------------------------------------------
class WebSettingsUpdate(BaseModel):
    """设置页允许修改的受控配置项。未提交字段保持原值，空密钥表示清除。"""

    llm_provider: Literal["auto", "openai", "stub"] | None = None
    llm_base_url: str | None = Field(default=None, max_length=2048)
    llm_api_key: str | None = Field(default=None, max_length=8192)
    llm_model: str | None = Field(default=None, min_length=1, max_length=200)
    embedding_provider: Literal["auto", "openai", "stub"] | None = None
    embedding_base_url: str | None = Field(default=None, max_length=2048)
    embedding_api_key: str | None = Field(default=None, max_length=8192)
    embedding_model: str | None = Field(default=None, min_length=1, max_length=200)
    tavily_api_key: str | None = Field(default=None, max_length=8192)
    github_token: str | None = Field(default=None, max_length=8192)
    orchestrator_enabled: bool | None = None
    orchestrator_planner_strategy: Literal["llm", "stub"] | None = None
    orchestrator_max_parallel: int | None = Field(default=None, ge=1, le=16)
    orchestrator_max_depth: int | None = Field(default=None, ge=1, le=8)


def _settings_response(settings, runtime, *, restart_required: bool) -> dict:
    orchestrator = runtime.orchestrator
    profile_count = len(orchestrator.profile_registry.all()) if orchestrator is not None else 0
    return {
        "settings": {
            "llm": {
                "provider": settings.llm_provider,
                "base_url": settings.llm_base_url,
                "model": settings.llm_model,
                "api_key_configured": bool(settings.llm_api_key),
            },
            "embedding": {
                "provider": settings.embedding_provider,
                "base_url": settings.embedding_base_url,
                "model": settings.embedding_model,
                "api_key_configured": bool(settings.embedding_api_key),
            },
            "tools": {
                "tavily_api_key_configured": bool(settings.tavily_api_key),
                "github_token_configured": bool(settings.github_token),
            },
            "orchestration": {
                "enabled": settings.orchestrator_enabled,
                "planner_strategy": settings.orchestrator_planner_strategy,
                "max_parallel": settings.orchestrator_max_parallel,
                "max_depth": settings.orchestrator_max_depth,
            },
        },
        "runtime": {
            "active_model": getattr(runtime.llm, "model", settings.llm_model),
            "orchestrator_available": orchestrator is not None,
            "agent_profile_count": profile_count,
        },
        "restart_required": restart_required,
    }


@router.get("/settings")
async def web_settings(request: Request) -> dict:
    """读取待生效配置和当前运行时状态，永不返回密钥原文。"""
    runtime = _get_runtime(request)
    active = request.app.state.settings
    desired = load_runtime_settings(active)
    restart_required = any(
        getattr(desired, field) != getattr(active, field)
        for field in RUNTIME_EDITABLE_FIELDS
    )
    return _settings_response(desired, runtime, restart_required=restart_required)


@router.patch("/settings")
async def web_settings_update(req: WebSettingsUpdate, request: Request) -> dict:
    """持久化设置覆盖；为避免中断在途任务，统一在服务重启后应用。"""
    updates = {
        field: getattr(req, field)
        for field in req.model_fields_set
        if field in RUNTIME_EDITABLE_FIELDS and getattr(req, field) is not None
    }
    if not updates:
        raise HTTPException(status_code=400, detail="没有可保存的配置项")
    try:
        desired = save_runtime_settings(request.app.state.settings, updates)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"配置保存失败: {exc}") from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _settings_response(desired, _get_runtime(request), restart_required=True)


# ---------------------------------------------------------------------------
# 多 Agent 编排（Stage 12）：直接编排入口 + 子 Agent 档案列表
# ---------------------------------------------------------------------------
class WebOrchestrateRequest(BaseModel):
    """编排请求。"""

    task: str = Field(description="要编排的任务")
    agents: list[str] | None = Field(default=None, description="指定子 Agent 名单；缺省自动分工")
    context: str = Field(default="", description="背景信息")
    session_id: str | None = Field(default=None, description="关联会话（编排结果持久化挂到该会话）")


@router.post("/orchestrate")
async def web_orchestrate(req: WebOrchestrateRequest, request: Request) -> dict:
    runtime = _get_runtime(request)
    if runtime.orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器未启用（ORCHESTRATOR_ENABLED=false）")
    result = await runtime.orchestrator.run(
        req.task, agents=req.agents, context=req.context, session_id=req.session_id or ""
    )
    trace_tree = None
    if result.trace_id:
        try:
            trace_tree = request.app.state.recorder.build_tree(result.trace_id)
        except Exception:
            trace_tree = None
    return {
        "run_id": result.run_id,
        "task": result.task,
        "plan": result.plan.model_dump(),
        "agent_results": [r.model_dump() for r in result.agent_results],
        "final_answer": result.final_answer,
        "status": result.status,
        "duration_ms": result.duration_ms,
        "trace_id": result.trace_id,
        "trace": trace_tree,
    }


@router.get("/agents")
async def web_agents(request: Request) -> dict:
    """列出可用的子 Agent 档案（内置 + 动态注册）。"""
    runtime = _get_runtime(request)
    if runtime.orchestrator is None:
        return {
            "agents": [],
            "count": 0,
            "enabled": False,
            "reason": "多 Agent 编排已关闭，请在设置中启用后重启服务。",
        }
    reg = runtime.orchestrator.profile_registry
    return {
        "agents": [
            {
                "name": p.name,
                "description": p.description,
                "allowed_tools": p.allowed_tools,
                "max_steps": p.max_steps,
                "builtin": reg.is_builtin(p.name),
            }
            for p in reg.all()
        ],
        "count": len(reg.all()),
        "enabled": True,
        "reason": "",
    }


class WebAgentRegisterRequest(BaseModel):
    """动态注册子 Agent 档案。"""

    name: str = Field(description="档案名（不可与内置档案同名）")
    description: str = Field(default="", description="职责说明（给编排规划器看）")
    system_prompt: str = Field(description="子 agent 系统提示（人设 + 工作规范）")
    allowed_tools: list[str] | None = Field(default=None, description="工具白名单；None = 全部工具")
    max_steps: int = Field(default=6, description="子 agent 循环步数上限")


@router.post("/agents")
async def web_agents_register(req: WebAgentRegisterRequest, request: Request) -> dict:
    """动态注册子 Agent 档案（持久化到 agent_profiles_file，重启后仍可用）。"""
    from app.orchestrator.profiles import AgentProfile
    from app.orchestrator.registry import ProfileRegistryError

    runtime = _get_runtime(request)
    if runtime.orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器未启用")
    profile = AgentProfile(
        name=req.name.strip(),
        description=req.description,
        system_prompt=req.system_prompt,
        allowed_tools=req.allowed_tools,
        max_steps=max(1, min(30, req.max_steps)),
    )
    try:
        runtime.orchestrator.profile_registry.register(profile)
    except ProfileRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    return {
        "registered": profile.name,
        "agents": [p.name for p in runtime.orchestrator.profile_registry.all()],
    }


@router.delete("/agents/{name}")
async def web_agents_unregister(name: str, request: Request) -> dict:
    """注销动态注册的档案（内置档案不可注销）。"""
    from app.orchestrator.registry import ProfileRegistryError

    runtime = _get_runtime(request)
    if runtime.orchestrator is None:
        raise HTTPException(status_code=503, detail="编排器未启用")
    try:
        removed = runtime.orchestrator.profile_registry.unregister(name)
    except ProfileRegistryError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    if not removed:
        raise HTTPException(status_code=404, detail=f"档案不存在: {name}")
    return {"unregistered": name}


# ---------------------------------------------------------------------------
# 编排结果查询（委派结果持久化到会话后的回放入口）
# ---------------------------------------------------------------------------
@router.get("/orchestrations")
async def web_orchestrations(request: Request, session_id: str | None = None) -> dict:
    """查询编排记录：?session_id=xxx 按会话查；缺省返回最近记录。"""
    runtime = _get_runtime(request)
    if runtime.orchestrator is None or runtime.orchestrator.repository is None:
        return {"runs": [], "count": 0}
    repo = runtime.orchestrator.repository
    runs = repo.list_by_session(session_id, limit=50) if session_id else repo.list_recent(limit=20)
    return {
        "runs": [
            {
                "run_id": r.run_id,
                "session_id": r.session_id,
                "parent_run_id": r.parent_run_id,
                "depth": r.depth,
                "task": r.task,
                "status": r.status,
                "final_answer": r.final_answer[:200],
                "duration_ms": r.duration_ms,
                "trace_id": r.trace_id,
                "created_at": r.created_at,
                "agent_count": len(r.agent_results),
            }
            for r in runs
        ],
        "count": len(runs),
    }


@router.get("/orchestrations/{run_id}")
async def web_orchestration_detail(run_id: str, request: Request) -> dict:
    """单次编排详情（计划 + 子 agent 结果 + 嵌套子编排 + Trace 树）。"""
    runtime = _get_runtime(request)
    if runtime.orchestrator is None or runtime.orchestrator.repository is None:
        raise HTTPException(status_code=404, detail="编排存储未启用")
    record = runtime.orchestrator.repository.get(run_id)
    if record is None:
        raise HTTPException(status_code=404, detail=f"编排记录不存在: {run_id}")
    children = runtime.orchestrator.repository.list_children(run_id)
    trace_tree = None
    if record.trace_id:
        try:
            trace_tree = request.app.state.recorder.build_tree(record.trace_id)
        except Exception:
            trace_tree = None
    return {
        "run_id": record.run_id,
        "session_id": record.session_id,
        "parent_run_id": record.parent_run_id,
        "depth": record.depth,
        "task": record.task,
        "status": record.status,
        "plan": record.plan.model_dump(),
        "agent_results": [r.model_dump() for r in record.agent_results],
        "final_answer": record.final_answer,
        "duration_ms": record.duration_ms,
        "trace_id": record.trace_id,
        "trace": trace_tree,
        "created_at": record.created_at,
        "children": [
            {"run_id": c.run_id, "depth": c.depth, "status": c.status, "task": c.task[:100]}
            for c in children
        ],
    }


@router.get("/skills")
async def web_skills(request: Request) -> dict:
    runtime = _get_runtime(request)
    skills = []
    if runtime.skill_manager is not None:
        skills = [
            {"name": s.name, "description": s.description, "triggers": s.triggers, "version": s.version}
            for s in runtime.skill_manager.all_skills()
        ]
    return {"skills": skills, "count": len(skills)}


@router.get("/mcp")
async def web_mcp(request: Request) -> dict:
    runtime = _get_runtime(request)
    servers = []
    if runtime.mcp_client is not None:
        servers = [
            {"name": c.name, "transport": c.transport, "tool_count": len(c.tools)}
            for c in runtime.mcp_client.connections
        ]
    return {"servers": servers, "count": len(servers)}


# ---------------------------------------------------------------------------
# Trace 树（Agent 工作流可视化）
# ---------------------------------------------------------------------------
@router.get("/traces/{trace_id}")
async def web_trace(trace_id: str, request: Request) -> dict:
    recorder = getattr(request.app.state, "recorder", None)
    if recorder is None or not recorder.enabled:
        raise HTTPException(status_code=404, detail="Tracing 未启用")
    tree = recorder.build_tree(trace_id)
    if not tree["spans"]:
        raise HTTPException(status_code=404, detail=f"Trace 不存在: {trace_id}")
    return tree


# ---------------------------------------------------------------------------
# 会话历史
# ---------------------------------------------------------------------------
@router.get("/sessions")
async def web_sessions(request: Request) -> dict:
    runtime = _get_runtime(request)
    if runtime.session_repo is None:
        return {"sessions": []}
    # 复用 SQLite 查询：列出会话（按 updated_at 排序）
    try:
        conn = runtime.session_repo._conn
        rows = conn.execute(
            "SELECT session_id, created_at, updated_at FROM sessions ORDER BY updated_at DESC LIMIT 50"
        ).fetchall()
        sessions = [dict(r) for r in rows]
    except Exception:
        sessions = []
    return {"sessions": sessions}


@router.get("/sessions/{session_id}/messages")
async def web_session_messages(session_id: str, request: Request) -> dict:
    runtime = _get_runtime(request)
    if runtime.session_repo is None:
        raise HTTPException(status_code=404, detail="会话存储未启用")
    messages = runtime.session_repo.list_messages(session_id)
    return {"session_id": session_id, "messages": messages}


# ---------------------------------------------------------------------------
# 会话删除（含关联消息与检查点）
# ---------------------------------------------------------------------------
@router.delete("/sessions/{session_id}")
async def web_session_delete(session_id: str, request: Request) -> dict:
    runtime = _get_runtime(request)
    if runtime.session_repo is None:
        raise HTTPException(status_code=404, detail="会话存储未启用")
    # session 与 checkpoint 同库：统一用 session_repo 连接删除（避免多连接 WAL 锁）
    try:
        conn = runtime.session_repo._conn
        conn.execute("DELETE FROM checkpoints WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM messages WHERE session_id = ?", (session_id,))
        conn.execute("DELETE FROM sessions WHERE session_id = ?", (session_id,))
        conn.commit()
        # 编排记录级联删除（独立连接，同样在 orchestrations 表上删）
        if runtime.orchestrator is not None and runtime.orchestrator.repository is not None:
            runtime.orchestrator.repository.delete_by_session(session_id)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"删除失败: {exc}")
    return {"deleted": session_id}


# ---------------------------------------------------------------------------
# 文件上传（写入沙箱目录，供 file_read 工具使用）
# ---------------------------------------------------------------------------
@router.post("/upload")
async def web_upload(request: Request) -> dict:
    from pathlib import Path

    runtime = _get_runtime(request)
    settings = request.app.state.settings
    sandbox = Path(settings.sandbox_dir).resolve()
    sandbox.mkdir(parents=True, exist_ok=True)

    form = await request.form()
    file = form.get("file")
    if file is None or not getattr(file, "filename", None):
        raise HTTPException(status_code=400, detail="缺少文件")

    filename = getattr(file, "filename", "upload.txt")
    # 安全：只保留文件名（防路径穿越）
    safe_name = Path(filename).name
    target = (sandbox / safe_name).resolve()
    if not str(target).startswith(str(sandbox)):
        raise HTTPException(status_code=400, detail="非法文件名")

    try:
        content = await file.read()
        # 限 1MB
        if len(content) > 1024 * 1024:
            raise HTTPException(status_code=400, detail="文件超过 1MB 限制")
        target.write_bytes(content)
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"保存失败: {exc}")

    return {
        "filename": safe_name,
        "path": str(target),
        "size": len(content),
        "hint": f"已上传到沙箱，可用 file_read 读取 {safe_name}",
    }


# ---------------------------------------------------------------------------
# 沙箱文件列表（前端"已上传文件"面板）
# ---------------------------------------------------------------------------
@router.get("/files")
async def web_files(request: Request) -> dict:
    from pathlib import Path

    settings = request.app.state.settings
    sandbox = Path(settings.sandbox_dir).resolve()
    files = []
    if sandbox.exists():
        for p in sorted(sandbox.iterdir()):
            if p.is_file():
                files.append({"name": p.name, "size": p.stat().st_size})
    return {"files": files, "sandbox": str(sandbox)}
