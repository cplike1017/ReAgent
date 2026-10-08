"""Durable, target-bound human decisions across restart and concurrency."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest


def service_type():
    from app.tools import approvals
    assert hasattr(approvals, "ToolApprovalService"), "durable approval service is missing"
    return approvals.ToolApprovalService


def request(service, **overrides):
    params = dict(invocation_key="session:turn:call", tool_name="write_file",
                  arguments={"path": "a.txt", "content": "hello", "api_key": "secret"},
                  user_id="alice", context={"session_id": "s1", "turn_id": "t1", "tool_call_id": "c1"},
                  reason="High risk write", policy_name="risk_level_confirmation")
    params.update(overrides)
    return service.ensure_request(**params)


def test_pending_and_approved_survive_restart_and_bind_validated_target(tmp_path):
    cls = service_type()
    first = cls(tmp_path / "approvals.db")
    record = request(first)
    assert record.status == "pending"
    assert record.public_dict()["arguments"].get("api_key") != "secret"
    first.close()
    restored = cls(tmp_path / "approvals.db")
    assert request(restored).approval_id == record.approval_id
    approved = restored.decide(record.approval_id, "approved", target_hash=record.target_hash, actor="reviewer")
    restored.close()
    final = cls(tmp_path / "approvals.db")
    assert request(final).status == approved.status == "approved"
    from app.tools.approvals import ApprovalConflictError
    for overrides in ({"tool_name": "delete_file"}, {"arguments": {"path": "b.txt"}}, {"user_id": "bob"}):
        with pytest.raises(ApprovalConflictError):
            request(final, **overrides)
    final.close()


def test_atomic_decisions_are_idempotent_and_conflicts_never_overwrite(tmp_path):
    cls = service_type()
    services = [cls(tmp_path / "approvals.db") for _ in range(2)]
    record = request(services[0])
    from app.tools.approvals import ApprovalConflictError
    with pytest.raises(ApprovalConflictError):
        services[0].decide(record.id, "approved", target_hash="0" * 64)
    def decide(pair):
        svc, decision = pair
        try:
            return svc.decide(record.id, decision, target_hash=record.target_hash).status
        except ApprovalConflictError:
            return "conflict"
    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(decide, zip(services, ["approved", "rejected"])))
    assert outcomes.count("conflict") == 1
    winner = services[0].get(record.id)
    again = services[1].decide(record.id, winner.status, target_hash=record.target_hash, actor="another")
    assert again.decided_at == winner.decided_at
    assert again.actor == winner.actor
    for svc in services:
        svc.close()


@pytest.mark.asyncio
async def test_wait_observes_other_connection_decision_and_expiry(tmp_path):
    cls = service_type()
    first = cls(tmp_path / "approvals.db", timeout_seconds=1, poll_seconds=0.002)
    second = cls(tmp_path / "approvals.db")
    record = request(first)
    waiting = asyncio.create_task(first.wait_for_decision(record.id))
    second.decide(record.id, "rejected", target_hash=record.target_hash)
    assert (await asyncio.wait_for(waiting, 1)).status == "rejected"
    expiring = cls(tmp_path / "expire.db", timeout_seconds=0.01, poll_seconds=0.002)
    expired = await expiring.wait_for_decision(request(expiring).id)
    assert expired.status == "expired"
    from app.tools.approvals import ApprovalConflictError
    with pytest.raises(ApprovalConflictError):
        expiring.decide(expired.id, "approved", target_hash=expired.target_hash)
    for svc in (first, second, expiring):
        svc.close()


@pytest.mark.asyncio
async def test_cancelled_waiter_durably_cancels_approval(tmp_path):
    service = service_type()(tmp_path / "approvals.db", poll_seconds=0.002)
    record = request(service)
    waiting = asyncio.create_task(service.wait_for_decision(record.id))
    await asyncio.sleep(0.01)
    waiting.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting
    assert service.get(record.id).status == "cancelled"
    service.close()


def test_web_stream_persists_and_emits_approval_lifecycle(tmp_path):
    from app.api.web import router as web_router
    from app.execution.repository import SQLiteExecutionRepository
    repository = SQLiteExecutionRepository(f"sqlite:///{tmp_path}/web.db")
    async def run(message, *, session_id, turn_id, extra_hooks, **kwargs):
        assert extra_hooks.approval_requested is not None, "Web must connect HITL request events"
        record = {"approval_id": "approval_1", "tool_name": "write_file", "status": "pending", "arguments": {"path": "safe.txt"}}
        await extra_hooks.approval_requested(record)
        await extra_hooks.approval_resolved({**record, "status": "approved"})
        return SimpleNamespace(session_id=session_id, answer="done", trace_id=None, plan=[], plan_revisions=0, tool_calls=[], steps=1)
    app = FastAPI()
    app.include_router(web_router)
    app.state.runtime = SimpleNamespace(run=run)
    app.state.execution_repository = repository
    app.state.settings = SimpleNamespace(agent_mode="react")
    app.state.web_runtime_lock = asyncio.Lock()
    app.state.web_execution_tasks = {}
    with TestClient(app) as client:
        response = client.post("/api/web/chat/stream", json={"message": "write", "session_id": "s1"})
        assert "event: approval.requested" in response.text
        assert "event: approval.resolved" in response.text
        events = repository.list_events(response.headers["X-Execution-ID"])
        assert [event.event_type for event in events if event.event_type.startswith("approval.")] == ["approval.requested", "approval.resolved"]
    repository.close()


def test_approval_routes_filter_validate_and_protect_target(tmp_path):
    from app.api import approvals
    assert hasattr(approvals, "router"), "approval API is missing"
    service = service_type()(tmp_path / "approvals.db")
    record = request(service)
    app = FastAPI()
    app.include_router(approvals.router)
    app.state.runtime = SimpleNamespace(tool_gateway=SimpleNamespace(approval_service=service))
    with TestClient(app) as client:
        response = client.get("/api/web/approvals", params={"session_id": "s1", "turn_id": "t1", "status": "pending"})
        assert response.status_code == 200
        assert response.json()["approvals"][0]["approval_id"] == record.id
        assert "secret" not in response.text
        assert client.get("/api/web/approvals?session_id=other").json()["count"] == 0
        assert client.get("/api/web/approvals?limit=1001").status_code == 422
        assert client.get("/api/web/approvals?status=unknown").status_code == 422
        assert client.get("/api/web/approvals/missing").status_code == 404
        url = f"/api/web/approvals/{record.id}/decision"
        assert client.post(url, json={"decision": "approved", "target_hash": "0" * 64}).status_code == 409
        assert client.post(url, json={"decision": "pending", "target_hash": record.target_hash}).status_code == 422
        body = {"decision": "approved", "target_hash": record.target_hash, "actor": "local-reviewer", "reason": "reviewed"}
        assert client.post(url, json=body).json()["status"] == "approved"
        assert client.post(url, json=body).status_code == 200
        body["decision"] = "rejected"
        assert client.post(url, json=body).status_code == 409
        assert client.get(f"/api/web/approvals/{record.id}").json()["actor"] == "local-reviewer"
    service.close()


def test_application_approval_routes_available_before_calls_and_after_restart(tmp_path):
    import fakeredis.aioredis
    from app.config import Settings
    from app.main import create_app
    settings = Settings(environment="test", llm_provider="stub", memory_enabled=False,
                        skills_enabled=False, trace_enabled=False,
                        database_url=f"sqlite:///{tmp_path}/app.db")
    def make_app():
        return create_app(settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    with TestClient(make_app()) as client:
        assert client.get("/api/web/approvals").json() == {"approvals": [], "count": 0}
        record = request(client.app.state.runtime.tool_gateway.approval_service)
    with TestClient(make_app()) as client:
        response = client.get("/api/web/approvals?session_id=s1&turn_id=t1")
        assert response.status_code == 200
        assert response.json()["approvals"][0]["approval_id"] == record.id
