"""Redis Stream queue delivery, ACK, PEL recovery and idempotency contracts."""

import asyncio
from types import SimpleNamespace

import fakeredis.aioredis
import pytest

from app.config import Settings
from app.queue.consumer import process_job
from app.queue.models import Job, JobStatus
from app.queue.producer import RedisJobQueue, utc_now


def _job(request_id: str) -> Job:
    return Job(
        job_id=f"job_{request_id}",
        request_id=request_id,
        session_id=f"session_{request_id}",
        input={"message": "hello"},
        created_at=utc_now(),
    )


@pytest.fixture
async def stream_queue():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = Settings(
        queue_stream_name="agent:test:jobs:stream",
        queue_consumer_group="test-workers",
        queue_claim_idle_ms=50,
        queue_heartbeat_ms=5,
        trace_enabled=False,
    )
    queue = RedisJobQueue(redis, settings)
    yield queue
    await redis.aclose()


async def test_stream_delivery_stays_pending_until_ack(stream_queue):
    await stream_queue.enqueue(_job("pending"))

    delivery = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)

    assert delivery is not None
    assert delivery.job.job_id == "job_pending"
    assert delivery.message_id
    assert await stream_queue.pending_count() == 1
    assert await stream_queue.queue_length() == 1

    assert await stream_queue.ack(delivery) == 1
    assert await stream_queue.pending_count() == 0
    assert await stream_queue.queue_length() == 0


async def test_stale_pending_delivery_can_be_claimed_by_another_consumer(stream_queue):
    await stream_queue.enqueue(_job("claim"))
    original = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    assert original is not None

    claimed = await stream_queue.claim_stale(
        consumer_name="worker-b",
        min_idle_ms=0,
        count=1,
    )

    assert len(claimed) == 1
    assert claimed[0].message_id == original.message_id
    assert claimed[0].job.job_id == original.job.job_id
    assert claimed[0].claimed is True
    await stream_queue.ack(claimed[0])


async def test_stale_claim_advances_server_scan_cursor(stream_queue, monkeypatch):
    await stream_queue.ensure_consumer_group()
    starts = []

    async def fake_xautoclaim(*args, start_id, **kwargs):
        starts.append(start_id)
        return ["42-0", [], []] if start_id == "0-0" else ["0-0", [], []]

    monkeypatch.setattr(stream_queue._redis, "xautoclaim", fake_xautoclaim)

    await stream_queue.claim_stale(consumer_name="worker-scan")
    await stream_queue.claim_stale(consumer_name="worker-scan")

    assert starts == ["0-0", "42-0"]


async def test_duplicate_request_adds_one_stream_entry(stream_queue):
    first = await stream_queue.enqueue(_job("same"))
    second = await stream_queue.enqueue(_job("same"))

    assert second.job_id == first.job_id
    assert await stream_queue.queue_length() == 1
    assert await stream_queue._redis.type(stream_queue.stream_name) == "stream"


async def test_terminal_duplicate_is_acknowledged_without_becoming_work(stream_queue):
    job = await stream_queue.enqueue(_job("done"))
    delivery = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    assert delivery is not None
    await stream_queue.finish(
        delivery,
        status=JobStatus.SUCCEEDED,
        result={"answer": "done"},
    )

    # fakeredis may reuse the last ID when an empty Stream is refilled in the
    # same millisecond. Inject a strictly newer ID to model a real redelivery.
    milliseconds, sequence = delivery.message_id.split("-")
    await stream_queue._redis.xadd(
        stream_queue.stream_name,
        {"job_id": job.job_id},
        id=f"{milliseconds}-{int(sequence) + 1}",
    )
    duplicate = await stream_queue.pop(consumer_name="worker-b", timeout=0.1)

    assert duplicate is None
    assert await stream_queue.pending_count() == 0
    assert await stream_queue.queue_length() == 0


async def test_retry_handoff_acknowledges_old_delivery_and_adds_one_new(stream_queue):
    await stream_queue.enqueue(_job("retry"))
    original = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    assert original is not None
    retry_job = original.job.model_copy(
        update={"attempt": 1, "status": JobStatus.QUEUED, "error": {"type": "Retry"}}
    )

    await stream_queue.retry(original, retry_job)

    assert await stream_queue.pending_count() == 0
    assert await stream_queue.queue_length() == 1
    retried = await stream_queue.pop(consumer_name="worker-b", timeout=0.1)
    assert retried is not None
    assert retried.message_id != original.message_id
    assert retried.job.attempt == 1
    await stream_queue.ack(retried)


async def test_terminal_result_is_committed_only_once(stream_queue):
    await stream_queue.enqueue(_job("terminal-race"))
    delivery = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    assert delivery is not None

    first = await stream_queue.finish(
        delivery,
        status=JobStatus.SUCCEEDED,
        result={"answer": "first"},
    )
    second = await stream_queue.finish(
        delivery,
        status=JobStatus.FAILED,
        error={"type": "LateFailure"},
    )

    assert first.status == JobStatus.SUCCEEDED
    assert second.status == JobStatus.SUCCEEDED
    assert second.result == {"answer": "first"}
    assert second.error is None


async def test_repeated_retry_transition_does_not_add_duplicate_attempt(stream_queue):
    await stream_queue.enqueue(_job("retry-race"))
    delivery = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    assert delivery is not None
    await stream_queue.start(delivery)
    retry_job = delivery.job.model_copy(
        update={"attempt": 1, "status": JobStatus.QUEUED}
    )

    first = await stream_queue.retry(delivery, retry_job)
    second = await stream_queue.retry(delivery, retry_job)

    assert first.attempt == 1
    assert second.attempt == 1
    assert await stream_queue.queue_length() == 1


async def test_active_delivery_heartbeat_resets_pel_idle_time(stream_queue):
    await stream_queue.enqueue(_job("heartbeat"))
    delivery = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    assert delivery is not None
    await asyncio.sleep(0.02)

    assert await stream_queue.touch(delivery) is True
    claimed = await stream_queue.claim_stale(
        consumer_name="worker-b",
        min_idle_ms=10,
        count=1,
    )

    assert claimed == []
    await stream_queue.ack(delivery)


async def test_process_job_heartbeats_while_runtime_is_active(stream_queue):
    started = asyncio.Event()
    release = asyncio.Event()

    class SlowRuntime:
        async def run(self, message, *, session_id, user):
            started.set()
            await release.wait()
            return SimpleNamespace(answer="done", session_id=session_id, trace_id="trace-heartbeat")

    await stream_queue.enqueue(_job("slow"))
    delivery = await stream_queue.pop(consumer_name="worker-a", timeout=0.1)
    task = asyncio.create_task(process_job(stream_queue, SlowRuntime, delivery))
    await asyncio.wait_for(started.wait(), timeout=1)
    await asyncio.sleep(0.03)

    claimed = await stream_queue.claim_stale(
        consumer_name="worker-b",
        min_idle_ms=10,
        count=1,
    )
    release.set()
    done = await asyncio.wait_for(task, timeout=1)

    assert claimed == []
    assert done.status == JobStatus.SUCCEEDED


async def test_heartbeat_interval_must_be_shorter_than_claim_idle():
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    settings = Settings(
        queue_claim_idle_ms=1_000,
        queue_heartbeat_ms=1_000,
        trace_enabled=False,
    )
    try:
        with pytest.raises(ValueError, match="heartbeat"):
            RedisJobQueue(redis, settings)
    finally:
        await redis.aclose()
