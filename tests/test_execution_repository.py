from app.execution.models import ExecutionStatus
from app.execution.repository import SQLiteExecutionRepository


def test_execution_events_are_ordered_and_queryable(tmp_path):
    repository = SQLiteExecutionRepository(f"sqlite:///{tmp_path}/events.db")
    queued = repository.create(
        execution_id="exec_demo",
        session_id="session_demo",
        turn_id="turn_demo",
        agent_mode="react",
        input_preview="计算 1 + 1",
    )
    assert queued.status == ExecutionStatus.QUEUED
    assert queued.started_at is None

    started = repository.start("exec_demo")
    assert started.status == ExecutionStatus.RUNNING
    assert started.started_at is not None
    # 重复启动不应覆盖真实开始时间或重新切换状态。
    assert repository.start("exec_demo").started_at == started.started_at

    first = repository.append_event("exec_demo", "execution.started", {"mode": "react"})
    second = repository.append_event(
        "exec_demo",
        "tool.started",
        {"tool_call_id": "call_1", "tool": "calculator"},
    )
    assert [first.seq, second.seq] == [1, 2]
    assert second.event_id.endswith("000002")

    repository.finish(
        "exec_demo",
        status=ExecutionStatus.SUCCEEDED,
        answer_preview="结果是 2",
    )
    record = repository.get("exec_demo")
    assert record is not None
    assert record.status == ExecutionStatus.SUCCEEDED
    assert record.last_seq == 2

    events = repository.list_events("exec_demo", after_seq=1)
    assert [event.event_type for event in events] == ["tool.started"]
    assert repository.list_for_session("session_demo")[0].execution_id == "exec_demo"
    repository.close()


def test_active_execution_is_marked_interrupted_on_restart(tmp_path):
    repository = SQLiteExecutionRepository(f"sqlite:///{tmp_path}/events.db")
    repository.create(
        execution_id="exec_queued",
        session_id="session_demo",
        turn_id="turn_queued",
        agent_mode="plan",
        input_preview="等待测试",
    )
    repository.create(
        execution_id="exec_running",
        session_id="session_demo",
        turn_id="turn_running",
        agent_mode="plan",
        input_preview="运行测试",
    )
    repository.start("exec_running")

    assert repository.mark_running_interrupted() == 2
    for execution_id in ("exec_queued", "exec_running"):
        record = repository.get(execution_id)
        assert record is not None
        assert record.status == ExecutionStatus.INTERRUPTED
    repository.close()


def test_execution_outputs_are_redacted_bounded_and_isolated(tmp_path):
    repository = SQLiteExecutionRepository(
        f"sqlite:///{tmp_path}/outputs.db",
        output_max_bytes=64,
    )
    for execution_id in ("exec_output", "exec_other"):
        repository.create(
            execution_id=execution_id,
            session_id="session_output",
            turn_id=f"turn_{execution_id}",
            agent_mode="react",
            input_preview="输出详情",
        )

    short = repository.store_output(
        "exec_output",
        kind="tool.result",
        content={"answer": False, "api_key": "should-not-persist"},
    )
    loaded = repository.get_output("exec_output", short.output_id)
    assert loaded is not None
    assert loaded.content == {"answer": False, "api_key_redacted": "[REDACTED]"}
    assert loaded.truncated is False
    # output_id 不能脱离其所属 execution 被读取。
    assert repository.get_output("exec_other", short.output_id) is None

    large = repository.store_output(
        "exec_output",
        kind="tool.result",
        content={"payload": "x" * 300},
    )
    assert large.truncated is True
    assert large.original_bytes > 64
    assert large.content["omitted"] is True
    assert len(large.content["preview"].encode("utf-8")) <= 64
    repository.close()
