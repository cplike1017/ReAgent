"""Queue redelivery keeps the original turn and durable tool outcomes."""
import sqlite3

import fakeredis.aioredis
import pytest
from pydantic import BaseModel

from app.agent.runtime import AgentRuntime
from app.agent.state import AgentState
from app.checkpoint.repository import SQLiteCheckpointRepository
from app.llm.client import LLMResponse, ToolCallRequest
from app.queue.consumer import process_job
from app.queue.models import Job, JobStatus, StreamDelivery
from app.queue.producer import RedisJobQueue
from app.session.repository import SQLiteSessionRepository
from app.tools.registry import ToolDefinition, ToolRegistry
from app.worker.worker import build_runtime_factory


@pytest.fixture
async def governance_queue(settings):
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    yield RedisJobQueue(redis, settings)
    await redis.aclose()


@pytest.fixture
def durable_dependencies(settings):
    sessions = SQLiteSessionRepository(settings.database_url)
    checkpoints = SQLiteCheckpointRepository(settings.database_url)
    yield sessions, checkpoints
    checkpoints.close()
    sessions.close()


class EmptyInput(BaseModel):
    pass


class EffectLLM:
    def __init__(self):
        self.calls = 0

    async def chat(self, messages, tools=None, **kwargs):
        self.calls += 1
        if messages[-1]["role"] == "tool":
            return LLMResponse(content="effect completed")
        return LLMResponse(tool_calls=[ToolCallRequest(
            id=f"effect_{self.calls}", name="effect", arguments={},
        )])


def effect_factory(settings, durable_dependencies, *, crash_after_effect=False):
    sessions, checkpoints = durable_dependencies
    effects = []
    runtimes = []
    llm = EffectLLM()

    async def effect():
        effects.append("executed")
        return {"value": len(effects)}

    registry = ToolRegistry().register(ToolDefinition(
        name="effect", description="record an irreversible effect",
        input_model=EmptyInput, handler=effect, side_effects=True,
        idempotent=False,
    ))

    class CrashOnceRuntime(AgentRuntime):
        async def _hook_after_tool(self, tool_call, result, step):
            nonlocal crash_after_effect
            if crash_after_effect:
                crash_after_effect = False
                raise RuntimeError("crashed after effect before checkpoint")
            await super()._hook_after_tool(tool_call, result, step)

    def factory():
        runtime = CrashOnceRuntime(
            llm=llm, registry=registry, settings=settings,
            session_repo=sessions, checkpoint_repo=checkpoints,
        )
        runtimes.append(runtime)
        return runtime

    return factory, effects, llm, runtimes


def job(name, *, session_id="governance_session"):
    return Job(job_id=f"job_{name}", request_id=name, session_id=session_id,
               input={"message": "perform effect"})


async def deliver(queue, factory):
    delivery = await queue.pop(consumer_name="governance_worker", timeout=0.1)
    assert delivery is not None
    return await process_job(queue, factory, delivery)


async def test_queue_turn_identity_is_the_job_id(
    governance_queue, settings, durable_dependencies,
):
    factory, _, _, runtimes = effect_factory(settings, durable_dependencies)
    submitted = job("stable_turn")
    await governance_queue.enqueue(submitted)
    try:
        done = await deliver(governance_queue, factory)
        checkpoint = durable_dependencies[1].load_latest(submitted.session_id)
        assert done.status == JobStatus.SUCCEEDED
        assert checkpoint.turn_id == submitted.job_id
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


async def test_retry_replays_completed_tool_without_duplicate_effect(
    governance_queue, settings, durable_dependencies,
):
    factory, effects, _, runtimes = effect_factory(
        settings, durable_dependencies, crash_after_effect=True,
    )
    submitted = job("after_effect")
    await governance_queue.enqueue(submitted)
    try:
        first = await deliver(governance_queue, factory)
        assert first.status == JobStatus.QUEUED
        assert effects == ["executed"]

        done = await deliver(governance_queue, factory)
        assert done.status == JobStatus.SUCCEEDED
        assert effects == ["executed"]
        messages = durable_dependencies[0].list_messages(submitted.session_id)
        assert sum(m["role"] == "user" for m in messages) == 1
        assert done.result["answer"] == "effect completed"
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


async def test_retry_after_done_checkpoint_skips_llm_and_tools(
    governance_queue, settings, durable_dependencies, monkeypatch,
):
    factory, effects, llm, runtimes = effect_factory(settings, durable_dependencies)
    finish = governance_queue.finish
    fail_once = True

    async def lose_completion(delivery, **kwargs):
        nonlocal fail_once
        if fail_once:
            fail_once = False
            raise RuntimeError("Redis unavailable after terminal checkpoint")
        return await finish(delivery, **kwargs)

    monkeypatch.setattr(governance_queue, "finish", lose_completion)
    await governance_queue.enqueue(job("terminal"))
    try:
        assert (await deliver(governance_queue, factory)).status == JobStatus.QUEUED
        assert llm.calls == 2

        done = await deliver(governance_queue, factory)
        assert done.status == JobStatus.SUCCEEDED
        assert done.result["answer"] == "effect completed"
        assert llm.calls == 2
        assert effects == ["executed"]
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


async def test_recovery_does_not_resume_another_job_in_same_session(
    governance_queue, settings, durable_dependencies,
):
    factory, effects, _, runtimes = effect_factory(settings, durable_dependencies)
    submitted = job("new_job")
    state = AgentState(session_id=submitted.session_id, turn_id="job_other",
                       status="DONE", messages=[{"role": "assistant", "content": "other result"}])
    durable_dependencies[1].save(session_id=state.session_id, turn_id=state.turn_id,
                                 step=0, state=state.model_dump(mode="json"))
    await governance_queue.enqueue(submitted)
    try:
        done = await deliver(governance_queue, factory)
        assert done.status == JobStatus.SUCCEEDED
        assert done.result["answer"] == "effect completed"
        assert effects == ["executed"]
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


async def test_claimed_delivery_recovers_done_even_on_first_attempt(
    governance_queue, settings, durable_dependencies,
):
    factory, effects, llm, runtimes = effect_factory(settings, durable_dependencies)
    submitted = job("claimed")
    state = AgentState(session_id=submitted.session_id, turn_id=submitted.job_id,
                       status="DONE", messages=[{"role": "assistant", "content": "already done"}])
    durable_dependencies[1].save(session_id=state.session_id, turn_id=state.turn_id,
                                 step=0, state=state.model_dump(mode="json"))
    await governance_queue.enqueue(submitted)
    delivery = await governance_queue.pop(consumer_name="crashed_worker", timeout=0.1)
    running, started = await governance_queue.start(delivery)
    assert started
    claimed = StreamDelivery(delivery.message_id, running, "replacement_worker", claimed=True)
    try:
        done = await process_job(governance_queue, factory, claimed)
        assert done.status == JobStatus.SUCCEEDED
        assert done.result["answer"] == "already done"
        assert llm.calls == 0
        assert effects == []
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


async def test_job_without_session_id_still_recovers_original_effect(
    governance_queue, settings, durable_dependencies,
):
    factory, effects, _, runtimes = effect_factory(
        settings, durable_dependencies, crash_after_effect=True,
    )
    await governance_queue.enqueue(job("missing_session", session_id=""))
    try:
        assert (await deliver(governance_queue, factory)).status == JobStatus.QUEUED
        done = await deliver(governance_queue, factory)
        assert done.status == JobStatus.SUCCEEDED
        assert done.result["session_id"] == "session_job_missing_session"
        assert effects == ["executed"]
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


async def test_consumer_closes_worker_owned_llm(
    governance_queue, settings, durable_dependencies,
):
    factory, _, llm, runtimes = effect_factory(settings, durable_dependencies)
    closed = False

    async def close_llm():
        nonlocal closed
        closed = True

    llm.aclose = close_llm
    factory.owns_runtime = True
    await governance_queue.enqueue(job("resources"))
    try:
        assert (await deliver(governance_queue, factory)).status == JobStatus.SUCCEEDED
        assert closed
    finally:
        for runtime in runtimes:
            runtime.tool_gateway.close()


def test_worker_factory_shares_and_closes_persistence(settings):
    factory = build_runtime_factory(settings)
    first, second = factory(), factory()
    try:
        assert first.tool_gateway.invocation_repo is second.tool_gateway.invocation_repo
        assert first.tool_gateway.approval_service is second.tool_gateway.approval_service
        first.tool_gateway.close()
        assert second.tool_gateway.invocation_repo.claim("live", "fingerprint").claimed
        factory.close()
        with pytest.raises(sqlite3.ProgrammingError):
            second.tool_gateway.invocation_repo.claim("closed", "fingerprint")
        with pytest.raises(sqlite3.ProgrammingError):
            second.session_repo.list_messages("closed")
    finally:
        if not hasattr(factory, "close"):
            first.tool_gateway.close()
            second.tool_gateway.close()
            first.session_repo.close()
            first.checkpoint_repo.close()
