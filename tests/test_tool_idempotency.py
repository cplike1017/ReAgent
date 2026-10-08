"""Tool invocations persist their result and never replay uncertain effects."""
import asyncio
import threading
import time

import pytest
from pydantic import BaseModel

from app.errors import ToolExecutionError
from app.mcp.bridge import MCPBridge
from app.tools.builtin import build_default_registry
from app.tools.gateway import ToolGateway
from app.tools.invocations import SQLiteToolInvocationRepository, invocation_fingerprint
from app.tools.registry import ToolDefinition, ToolRegistry
from app.tools.schemas import UserContext


class Args(BaseModel):
    value: int = 1


def registry_for(handler, *, timeout=1, side_effects=False, idempotent=True):
    tool = ToolDefinition(
        name="counter", description="test", input_model=Args,
        handler=handler, timeout_seconds=timeout,
        side_effects=side_effects, idempotent=idempotent,
    )
    return ToolRegistry().register(tool)


async def test_completed_invocation_replays_after_gateway_restart(settings):
    calls = []

    def handler(value):
        calls.append(value)
        return {"value": value}

    registry = registry_for(handler)
    first_gateway = ToolGateway(registry, settings=settings)
    first = await first_gateway.execute("counter", {"value": "2"}, invocation_key="call-1")
    first_gateway.close()
    restarted = ToolGateway(registry, settings=settings)
    try:
        replay = await restarted.execute("counter", {"value": 2}, invocation_key="call-1")
        assert first.success and replay.success
        assert replay.data == first.data
        assert replay.metadata["idempotency_replayed"] is True
        assert calls == [2]
    finally:
        restarted.close()


@pytest.mark.parametrize("changed_args,changed_user", [({"value": 2}, "u1"), ({"value": 1}, "u2")])
async def test_key_cannot_be_rebound_to_arguments_or_user(settings, changed_args, changed_user):
    calls = []
    gateway = ToolGateway(registry_for(lambda value: calls.append(value)), settings=settings)
    try:
        await gateway.execute("counter", {"value": 1}, UserContext(user_id="u1"), invocation_key="same")
        conflict = await gateway.execute("counter", changed_args, UserContext(user_id=changed_user), invocation_key="same")
        assert conflict.error.type == "ToolIdempotencyError"
        assert conflict.error.code == "IDEMPOTENCY_CONFLICT"
        assert calls == [1]
    finally:
        gateway.close()


async def test_concurrent_gateways_atomically_claim_one_invocation(settings):
    started = asyncio.Event()
    release = asyncio.Event()
    calls = []

    async def handler(value):
        calls.append(value)
        started.set()
        await release.wait()
        return value

    registry = registry_for(handler)
    first = ToolGateway(registry, settings=settings)
    second = ToolGateway(registry, settings=settings)
    task = asyncio.create_task(first.execute("counter", {}, invocation_key="race"))
    try:
        await started.wait()
        duplicate = await second.execute("counter", {}, invocation_key="race")
        assert duplicate.error.code == "INVOCATION_IN_PROGRESS"
        assert calls == [1]
        release.set()
        assert (await task).success
        assert (await second.execute("counter", {}, invocation_key="race")).success
        assert calls == [1]
    finally:
        release.set()
        await task
        first.close()
        second.close()


async def test_sync_timeout_never_replays_surviving_thread(settings):
    finished = threading.Event()
    calls = []

    def handler(value):
        calls.append(value)
        time.sleep(0.08)
        finished.set()
        return value

    gateway = ToolGateway(registry_for(handler, timeout=0.01, side_effects=True, idempotent=False), settings=settings)
    try:
        result = await gateway.execute("counter", {}, invocation_key="timed-out")
        assert result.error.type == "ToolTimeoutError"
        duplicate = await gateway.execute("counter", {}, invocation_key="timed-out")
        assert duplicate.error.code == "INVOCATION_OUTCOME_UNKNOWN"
        await asyncio.to_thread(finished.wait, 1)
        assert (await gateway.execute("counter", {}, invocation_key="timed-out")).error.code == "INVOCATION_OUTCOME_UNKNOWN"
        assert calls == [1]
    finally:
        gateway.close()


async def test_cancellation_marks_invocation_unknown(settings):
    started = asyncio.Event()
    calls = []

    async def handler(value):
        calls.append(value)
        started.set()
        await asyncio.Event().wait()

    gateway = ToolGateway(registry_for(handler), settings=settings)
    task = asyncio.create_task(gateway.execute("counter", {}, invocation_key="cancelled"))
    try:
        await started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert (await gateway.execute("counter", {}, invocation_key="cancelled")).error.code == "INVOCATION_OUTCOME_UNKNOWN"
        assert calls == [1]
    finally:
        gateway.close()


async def test_unsafe_transient_error_is_not_retried(settings):
    calls = []

    def handler(value):
        calls.append(value)
        raise ToolExecutionError("remote may have accepted request", transient=True)

    gateway = ToolGateway(registry_for(handler, side_effects=True, idempotent=False), settings=settings)
    try:
        result = await gateway.execute("counter", {}, invocation_key="unsafe")
        assert result.error.type == "ToolExecutionError"
        assert calls == [1]
        assert (await gateway.execute("counter", {}, invocation_key="unsafe")).error.code == "INVOCATION_OUTCOME_UNKNOWN"
    finally:
        gateway.close()


async def test_side_effect_retry_guarantee_must_be_explicit(settings):
    calls = []

    def handler(value):
        calls.append(value)
        raise ToolExecutionError("remote outcome uncertain", transient=True)

    registry = ToolRegistry().register(ToolDefinition(
        name="counter", description="write", input_model=Args, handler=handler, side_effects=True,
    ))
    gateway = ToolGateway(registry, settings=settings)
    try:
        await gateway.execute("counter", {}, invocation_key="write-without-guarantee")
        assert calls == [1]
    finally:
        gateway.close()


async def test_crash_without_recorded_outcome_remains_blocked_after_restart(settings):
    repository = SQLiteToolInvocationRepository(settings.database_url)
    repository.claim("crashed", invocation_fingerprint("counter", {"value": 1}, None))
    repository.close()
    calls = []
    restarted = ToolGateway(registry_for(lambda value: calls.append(value)), settings=settings)
    try:
        result = await restarted.execute("counter", {}, invocation_key="crashed")
        assert result.error.code == "INVOCATION_IN_PROGRESS"
        assert calls == []
    finally:
        restarted.close()


async def test_same_key_cannot_be_reused_for_a_different_tool(settings):
    calls = []
    registry = registry_for(lambda value: calls.append(value))
    registry.register(ToolDefinition(name="other", description="other", input_model=Args, handler=lambda value: calls.append(value)))
    gateway = ToolGateway(registry, settings=settings)
    try:
        await gateway.execute("counter", {}, invocation_key="bound-name")
        result = await gateway.execute("other", {}, invocation_key="bound-name")
        assert result.error.code == "IDEMPOTENCY_CONFLICT"
        assert calls == [1]
    finally:
        gateway.close()


async def test_unserializable_outcome_is_structured_and_never_reexecutes(settings):
    calls = []

    def handler(value):
        calls.append(value)
        return object()

    gateway = ToolGateway(registry_for(handler), settings=settings)
    try:
        result = await gateway.execute("counter", {}, invocation_key="unsupported-result")
        assert result.error.type == "ToolExecutionError"
        assert result.error.code == "INVOCATION_PERSISTENCE_FAILED"
        duplicate = await gateway.execute("counter", {}, invocation_key="unsupported-result")
        assert duplicate.error.code == "INVOCATION_OUTCOME_UNKNOWN"
        assert calls == [1]
    finally:
        gateway.close()


async def test_idempotent_side_effect_can_retry_transient_failure(settings):
    calls = []

    def handler(value):
        calls.append(value)
        if len(calls) == 1:
            raise ToolExecutionError("safe remote upsert", transient=True)
        return value

    gateway = ToolGateway(registry_for(handler, side_effects=True, idempotent=True), settings=settings)
    try:
        result = await gateway.execute("counter", {}, invocation_key="safe-upsert")
        assert result.success
        assert calls == [1, 1]
        assert (await gateway.execute("counter", {}, invocation_key="safe-upsert")).metadata["idempotency_replayed"]
        assert calls == [1, 1]
    finally:
        gateway.close()


async def test_explicit_false_mcp_hints_remain_unsafe(settings):
    class Client:
        def __init__(self):
            self.settings = settings

        def list_all_tools(self):
            return [("remote", "write", {"annotations": {"readOnlyHint": False, "idempotentHint": False}})]

    registry = ToolRegistry()
    MCPBridge(Client()).register_all(registry)
    assert registry.get("remote_write").side_effects is True
    assert registry.get("remote_write").idempotent is False


@pytest.mark.parametrize("decision", ["approved", "rejected"])
async def test_gateway_waits_for_approval_before_effects(settings, decision):
    from app.tools.approvals import ToolApprovalService

    calls = []
    events = []
    service = ToolApprovalService(settings.database_url, poll_seconds=0.01)
    gateway = ToolGateway(
        registry_for(lambda value: calls.append(value), side_effects=True, idempotent=False),
        settings=settings.model_copy(update={"policy_require_confirmation_risks": "low"}),
        approval_service=service,
    )

    async def on_approval(record):
        events.append(record["status"])
        if record["status"] == "pending":
            assert calls == []
            service.decide(record["approval_id"], decision, target_hash=record["target_hash"])

    try:
        result = await gateway.execute("counter", {}, invocation_key="confirmed", context={"session_id": "s"}, on_approval=on_approval)
        assert events == ["pending", decision]
        assert result.success is (decision == "approved")
        assert calls == ([1] if decision == "approved" else [])
    finally:
        gateway.close()
        service.close()


async def test_gateway_expired_approval_never_executes(settings):
    from app.tools.approvals import ToolApprovalService

    calls = []
    service = ToolApprovalService(settings.database_url, timeout_seconds=0.02, poll_seconds=0.005)
    gateway = ToolGateway(
        registry_for(lambda value: calls.append(value)),
        settings=settings.model_copy(update={"policy_require_confirmation_risks": "low"}),
        approval_service=service,
    )
    try:
        result = await gateway.execute("counter", {}, invocation_key="expired")
        assert result.error.type == "ToolPolicyError"
        assert "expired" in result.error.message
        assert calls == []
    finally:
        gateway.close()
        service.close()


async def test_cancellation_during_approval_notification_cancels_request(settings):
    from app.tools.approvals import ToolApprovalService

    notified = asyncio.Event()
    calls = []
    service = ToolApprovalService(settings.database_url)
    gateway = ToolGateway(
        registry_for(lambda value: calls.append(value)),
        settings=settings.model_copy(update={"policy_require_confirmation_risks": "low"}),
        approval_service=service,
    )

    async def on_approval(record):
        notified.set()
        await asyncio.Event().wait()

    task = asyncio.create_task(gateway.execute("counter", {}, invocation_key="cancel-notification", on_approval=on_approval))
    try:
        await notified.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert service.list_requests(status=None)[0].status == "cancelled"
        assert calls == []
    finally:
        gateway.close()
        service.close()


def test_new_gateway_exposes_persisted_approvals_before_first_execution(settings):
    from app.tools.approvals import ToolApprovalService

    service = ToolApprovalService(settings.database_url)
    requested = service.ensure_request("startup-pending", "counter", {"value": 1})
    service.close()
    configured = settings.model_copy(update={"tool_approval_timeout_seconds": 12.0, "tool_approval_poll_seconds": 0.02})
    gateway = ToolGateway(registry_for(lambda value: value), settings=configured)
    try:
        assert gateway.approval_service is not None
        assert gateway.approval_service.timeout_seconds == 12.0
        assert gateway.approval_service.poll_seconds == 0.02
        assert gateway.approval_service.list_requests()[0].id == requested.id
    finally:
        gateway.close()


def test_builtin_writing_tools_declare_unsafe_execution():
    registry = build_default_registry()
    for name in ("file_write", "append_note", "send_email", "run_code"):
        tool = registry.get(name)
        assert tool.side_effects is True
        assert tool.idempotent is False
    assert registry.get("calculator").side_effects is False


def test_mcp_annotations_control_retry_safety(settings):
    class Client:
        def __init__(self):
            self.settings = settings

        def list_all_tools(self):
            return [
                ("remote", "unknown", {"inputSchema": {}}),
                ("remote", "read", {"inputSchema": {}, "annotations": {"readOnlyHint": True}}),
                ("remote", "set", {"inputSchema": {}, "annotations": {"idempotentHint": True}}),
            ]

    registry = ToolRegistry()
    MCPBridge(Client()).register_all(registry)
    assert registry.get("remote_unknown").side_effects is True
    assert registry.get("remote_unknown").idempotent is False
    assert registry.get("remote_read").side_effects is False
    assert registry.get("remote_read").idempotent is True
    assert registry.get("remote_set").side_effects is True
    assert registry.get("remote_set").idempotent is True
