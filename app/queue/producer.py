"""Redis Stream Job queue with consumer-group delivery and explicit ACK."""

import asyncio
import json
from datetime import datetime, timezone

from redis.asyncio import Redis
from redis.exceptions import ResponseError, WatchError

from app.config import Settings
from app.errors import QueueError
from app.queue.models import Job, JobStatus, StreamDelivery
from app.tracing.recorder import TraceRecorder
from app.tracing.span import trace_span


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RedisJobQueue:
    """Redis Stream work queue backed by Job Hashes and a consumer group."""

    def __init__(
        self,
        redis: Redis,
        settings: Settings,
        recorder: TraceRecorder | None = None,
    ) -> None:
        self._redis = redis
        self.stream_name = settings.queue_stream_name
        self.consumer_group = settings.queue_consumer_group
        self.claim_idle_ms = settings.queue_claim_idle_ms
        self.heartbeat_ms = settings.queue_heartbeat_ms
        self.read_block_ms = settings.queue_read_block_ms
        if self.heartbeat_ms <= 0 or self.heartbeat_ms >= self.claim_idle_ms:
            raise ValueError("queue heartbeat 必须大于 0 且小于 claim idle 时间")
        self.job_key_prefix = settings.job_key_prefix
        self.request_key_prefix = settings.request_key_prefix
        self.max_attempts = settings.max_attempts
        self.job_ttl = settings.job_ttl_seconds
        self.recorder = recorder
        self._group_ready = False
        self._group_lock = asyncio.Lock()
        self._claim_cursors: dict[str, str] = {}

    def _job_key(self, job_id: str) -> str:
        return f"{self.job_key_prefix}{job_id}"

    def _request_key(self, request_id: str) -> str:
        return f"{self.request_key_prefix}{request_id}"

    @staticmethod
    def _job_to_mapping(job: Job) -> dict:
        data = job.model_dump(mode="json")
        for key in ("input", "user", "result", "error", "trace_context"):
            value = data.get(key)
            data[key] = json.dumps(value, ensure_ascii=False) if value is not None else ""
        return data

    @staticmethod
    def _job_from_mapping(raw: dict) -> Job:
        data = dict(raw)
        for key in ("input", "user", "result", "error", "trace_context"):
            value = data.get(key)
            if value in (None, ""):
                data[key] = None if key in {"result", "error"} else {}
            else:
                try:
                    data[key] = json.loads(value)
                except (TypeError, json.JSONDecodeError):
                    data[key] = None if key in {"result", "error"} else {}
        return Job(**data)

    async def ensure_consumer_group(self) -> None:
        if self._group_ready:
            return
        async with self._group_lock:
            if self._group_ready:
                return
            try:
                await self._redis.xgroup_create(
                    self.stream_name,
                    self.consumer_group,
                    id="0-0",
                    mkstream=True,
                )
            except ResponseError as exc:
                if "BUSYGROUP" not in str(exc):
                    raise QueueError(f"创建 Redis Consumer Group 失败: {exc}") from exc
            self._group_ready = True

    async def save_job(self, job: Job) -> Job:
        await self._redis.hset(self._job_key(job.job_id), mapping=self._job_to_mapping(job))
        await self._redis.expire(self._job_key(job.job_id), self.job_ttl)
        return job

    async def get_job(self, job_id: str) -> Job | None:
        raw = await self._redis.hgetall(self._job_key(job_id))
        return self._job_from_mapping(raw) if raw else None

    async def enqueue(self, job: Job) -> Job:
        if self.recorder is None or not self.recorder.enabled:
            return await self._enqueue_impl(job)
        async with trace_span(
            "redis.enqueue",
            "queue",
            input={"job_id": job.job_id, "request_id": job.request_id},
            attributes={"job_id": job.job_id, "request_id": job.request_id},
            recorder=self.recorder,
        ) as span:
            enqueued = await self._enqueue_impl(job)
            span.output = {"job_id": enqueued.job_id, "status": enqueued.status.value}
            return enqueued

    async def _enqueue_impl(self, job: Job) -> Job:
        request_key = self._request_key(job.request_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(request_key)
                    existing = await pipe.get(request_key)
                    if existing is not None:
                        found = await self.get_job(existing)
                        if found is None:
                            raise QueueError(f"幂等映射指向不存在的 Job: {existing}")
                        return found

                    job.created_at = job.created_at or utc_now()
                    pipe.multi()
                    pipe.set(request_key, job.job_id, ex=self.job_ttl)
                    pipe.hset(self._job_key(job.job_id), mapping=self._job_to_mapping(job))
                    pipe.expire(self._job_key(job.job_id), self.job_ttl)
                    pipe.xadd(self.stream_name, {"job_id": job.job_id})
                    await pipe.execute()
                    return job
                except WatchError:
                    continue

    async def update_status(
        self,
        job_id: str,
        status: JobStatus,
        *,
        result: dict | None = None,
        error: dict | None = None,
    ) -> Job:
        job = await self.get_job(job_id)
        if job is None:
            raise QueueError(f"Job 不存在: {job_id}")
        job.status = status
        if result is not None:
            job.result = result
        if error is not None:
            job.error = error
        return await self.save_job(job)

    async def start(self, delivery: StreamDelivery) -> tuple[Job, bool]:
        """Start the delivered attempt unless it is terminal, stale, or duplicated."""
        job_key = self._job_key(delivery.job.job_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(job_key)
                    raw = await pipe.hgetall(job_key)
                    if not raw:
                        raise QueueError(f"Job 不存在: {delivery.job.job_id}")
                    current = self._job_from_mapping(raw)
                    terminal = current.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}
                    stale_attempt = current.attempt != delivery.job.attempt
                    duplicate_running = (
                        current.status == JobStatus.RUNNING and not delivery.claimed
                    )
                    if terminal or stale_attempt or duplicate_running:
                        pipe.multi()
                        pipe.xack(
                            self.stream_name,
                            self.consumer_group,
                            delivery.message_id,
                        )
                        pipe.xdel(self.stream_name, delivery.message_id)
                        await pipe.execute()
                        return current, False

                    current.status = JobStatus.RUNNING
                    pipe.multi()
                    pipe.hset(job_key, mapping=self._job_to_mapping(current))
                    pipe.expire(job_key, self.job_ttl)
                    await pipe.execute()
                    return current, True
                except WatchError:
                    continue

    async def pop(
        self,
        timeout: float = 0,
        *,
        consumer_name: str = "worker",
    ) -> StreamDelivery | None:
        await self.ensure_consumer_group()
        block_ms = None if timeout <= 0 else max(1, int(timeout * 1000))
        rows = await self._redis.xreadgroup(
            self.consumer_group,
            consumer_name,
            {self.stream_name: ">"},
            count=1,
            block=block_ms,
        )
        if not rows:
            return None
        message_id, fields = rows[0][1][0]
        return await self._delivery_from_message(
            message_id,
            fields,
            consumer_name=consumer_name,
            claimed=False,
        )

    async def _delivery_from_message(
        self,
        message_id: str,
        fields: dict,
        *,
        consumer_name: str,
        claimed: bool,
    ) -> StreamDelivery | None:
        job_id = fields.get("job_id")
        job = await self.get_job(job_id) if job_id else None
        delivery = (
            StreamDelivery(message_id, job, consumer_name, claimed)
            if job is not None
            else None
        )
        if delivery is None or job.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            await self._ack_message(message_id)
            return None
        return delivery

    async def claim_stale(
        self,
        *,
        consumer_name: str,
        min_idle_ms: int | None = None,
        count: int = 1,
    ) -> list[StreamDelivery]:
        await self.ensure_consumer_group()
        response = await self._redis.xautoclaim(
            self.stream_name,
            self.consumer_group,
            consumer_name,
            self.claim_idle_ms if min_idle_ms is None else min_idle_ms,
            start_id=self._claim_cursors.get(consumer_name, "0-0"),
            count=count,
        )
        self._claim_cursors[consumer_name] = response[0]
        messages = response[1] if len(response) > 1 else []
        deliveries: list[StreamDelivery] = []
        for message_id, fields in messages:
            delivery = await self._delivery_from_message(
                message_id,
                fields,
                consumer_name=consumer_name,
                claimed=True,
            )
            if delivery is not None:
                deliveries.append(delivery)
        return deliveries

    async def _ack_message(self, message_id: str) -> int:
        async with self._redis.pipeline(transaction=True) as pipe:
            pipe.xack(self.stream_name, self.consumer_group, message_id)
            pipe.xdel(self.stream_name, message_id)
            acknowledged, _ = await pipe.execute()
        return int(acknowledged)

    async def ack(self, delivery: StreamDelivery) -> int:
        return await self._ack_message(delivery.message_id)

    async def touch(self, delivery: StreamDelivery) -> bool:
        """Refresh a live delivery's PEL idle clock without changing its owner."""
        ids = await self._redis.xclaim(
            self.stream_name,
            self.consumer_group,
            delivery.consumer_name,
            min_idle_time=0,
            message_ids=[delivery.message_id],
            justid=True,
        )
        return delivery.message_id in ids

    async def finish(
        self,
        delivery: StreamDelivery,
        *,
        status: JobStatus,
        result: dict | None = None,
        error: dict | None = None,
    ) -> Job:
        if status not in {JobStatus.SUCCEEDED, JobStatus.FAILED}:
            raise QueueError(f"finish 只接受终态，收到: {status.value}")
        job_key = self._job_key(delivery.job.job_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(job_key)
                    raw = await pipe.hgetall(job_key)
                    if not raw:
                        raise QueueError(f"Job 不存在: {delivery.job.job_id}")
                    job = self._job_from_mapping(raw)
                    already_terminal = job.status in {
                        JobStatus.SUCCEEDED,
                        JobStatus.FAILED,
                    }
                    stale_attempt = job.attempt != delivery.job.attempt
                    if not already_terminal and not stale_attempt:
                        job.status = status
                        job.result = result
                        job.error = error

                    pipe.multi()
                    if not already_terminal and not stale_attempt:
                        pipe.hset(job_key, mapping=self._job_to_mapping(job))
                        pipe.expire(job_key, self.job_ttl)
                    pipe.xack(
                        self.stream_name,
                        self.consumer_group,
                        delivery.message_id,
                    )
                    pipe.xdel(self.stream_name, delivery.message_id)
                    await pipe.execute()
                    return job
                except WatchError:
                    continue

    async def retry(self, delivery: StreamDelivery, job: Job) -> Job:
        """Atomically replace a failed delivery with its next-attempt message."""
        if job.status != JobStatus.QUEUED:
            raise QueueError(f"retry 只接受 QUEUED Job，收到: {job.status.value}")
        job_key = self._job_key(job.job_id)
        while True:
            async with self._redis.pipeline(transaction=True) as pipe:
                try:
                    await pipe.watch(job_key)
                    raw = await pipe.hgetall(job_key)
                    if not raw:
                        raise QueueError(f"Job 不存在: {job.job_id}")
                    current = self._job_from_mapping(raw)
                    terminal = current.status in {JobStatus.SUCCEEDED, JobStatus.FAILED}
                    stale_attempt = current.attempt != job.attempt - 1

                    pipe.multi()
                    if not terminal and not stale_attempt:
                        pipe.hset(job_key, mapping=self._job_to_mapping(job))
                        pipe.expire(job_key, self.job_ttl)
                        pipe.xadd(self.stream_name, {"job_id": job.job_id})
                    pipe.xack(
                        self.stream_name,
                        self.consumer_group,
                        delivery.message_id,
                    )
                    pipe.xdel(self.stream_name, delivery.message_id)
                    await pipe.execute()
                    return current if terminal or stale_attempt else job
                except WatchError:
                    continue

    async def pending_count(self) -> int:
        await self.ensure_consumer_group()
        summary = await self._redis.xpending(self.stream_name, self.consumer_group)
        return int(summary.get("pending", 0))

    async def queue_length(self) -> int:
        return int(await self._redis.xlen(self.stream_name))
