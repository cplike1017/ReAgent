"""
队列消费者（Consumer 侧）：处理 Stream delivery -> 执行 Agent -> ACK / 重试。

重试（Retry）语义：
    max_attempts 默认 3。每次失败 attempt += 1 并重新入队；
    达到上限后标记 FAILED —— 避免无限重试。

Redis Job 终态与 ACK 在同一事务提交；外部工具副作用仍需工具自身支持幂等键。
"""
import asyncio
from typing import Awaitable, Callable

from app.errors import error_to_dict
from app.queue.models import Job, JobStatus, StreamDelivery
from app.queue.producer import RedisJobQueue
from app.tools.schemas import UserContext
from app.tracing.context import current_span_id, current_trace_id, set_trace_context
from app.tracing.recorder import TraceRecorder
from app.tracing.span import trace_span

# 运行时工厂：() -> AgentRuntime（由 Worker 提供，负责组装 Session/Checkpoint 等依赖）
RuntimeFactory = Callable[[], Awaitable]


async def _heartbeat_delivery(
    queue: RedisJobQueue,
    delivery: StreamDelivery,
    stop: asyncio.Event,
) -> None:
    interval = max(queue.heartbeat_ms / 1000, 0.001)
    while True:
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
            return
        except TimeoutError:
            if not await queue.touch(delivery):
                return


async def process_job(
    queue: RedisJobQueue,
    runtime_factory: Callable[[], "object"],
    delivery: StreamDelivery,
    recorder: TraceRecorder | None = None,
) -> Job:
    """
    消费一个 Job 的完整流程：
        QUEUED -> RUNNING -> SUCCEEDED
                        \\-> (重试) QUEUED (attempt+1)
                        \\-> FAILED (达到 max_attempts)

    Trace 传播：从 job.trace_context 恢复 trace_id / parent_span_id，
    让 worker.process 及其子 Span 与 Gateway 属于同一条 Trace。
    """
    job, started = await queue.start(delivery)
    if not started:
        return job
    recorder = recorder or getattr(queue, "recorder", None)
    heartbeat_stop = asyncio.Event()
    heartbeat_task = asyncio.create_task(
        _heartbeat_delivery(queue, delivery, heartbeat_stop)
    )

    async def stop_heartbeat() -> None:
        if not heartbeat_stop.is_set():
            heartbeat_stop.set()
        await heartbeat_task

    # ---- 跨进程 Trace 传播：保存旧上下文，处理完恢复 ----
    old_trace, old_span = current_trace_id.get(), current_span_id.get()
    ctx = job.trace_context or {}
    set_trace_context(ctx.get("trace_id"), ctx.get("parent_span_id"))
    try:
        message = (job.input or {}).get("message", "")
        # 从 Job 恢复调用方身份（Stage 5 权限校验用）
        user = UserContext(**job.user) if job.user else None
        runtime = runtime_factory()

        traced = recorder is not None and recorder.enabled
        if traced:
            async with trace_span(
                "worker.process",
                "worker",
                input={"job_id": job.job_id, "session_id": job.session_id},
                attributes={"job_id": job.job_id, "attempt": job.attempt},
                recorder=recorder,
            ) as span:
                result = await runtime.run(message, session_id=job.session_id, user=user)
                span.output = {
                    "session_id": result.session_id,
                    "steps": result.steps,
                    "tool_calls": len(result.tool_calls),
                }
        else:
            result = await runtime.run(message, session_id=job.session_id, user=user)

        payload = {
            "answer": result.answer,
            "session_id": result.session_id,
            "trace_id": result.trace_id,
        }
        return await queue.finish(
            delivery,
            status=JobStatus.SUCCEEDED,
            result=payload,
        )
    except Exception as exc:  # 任何异常都走重试 / 失败路径
        error = error_to_dict(exc)
        fresh = await queue.get_job(job.job_id)
        if fresh is None:
            raise
        if fresh.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            await queue.ack(delivery)
            return fresh
        if fresh.attempt + 1 < queue.max_attempts:
            # 还有重试额度：attempt + 1，重新入队
            fresh.attempt += 1
            fresh.status = JobStatus.QUEUED
            fresh.error = error
            return await queue.retry(delivery, fresh)
        # 用尽重试次数：最终失败
        return await queue.finish(
            delivery,
            status=JobStatus.FAILED,
            error=error,
        )
    finally:
        await stop_heartbeat()
        # 恢复调用前的 Trace 上下文，避免污染下一个 Job
        current_trace_id.set(old_trace)
        current_span_id.set(old_span)
