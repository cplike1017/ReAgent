from app.execution.models import ExecutionStatus
from app.execution.repository import SQLiteExecutionRepository


def test_execution_events_are_ordered_and_queryable(tmp_path):
    repository = SQLiteExecutionRepository(f"sqlite:///{tmp_path}/events.db")
    repository.create(
        execution_id="exec_demo",
        session_id="session_demo",
        turn_id="turn_demo",
        agent_mode="react",
        input_preview="计算 1 + 1",
    )

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


def test_running_execution_is_marked_interrupted_on_restart(tmp_path):
    repository = SQLiteExecutionRepository(f"sqlite:///{tmp_path}/events.db")
    repository.create(
        execution_id="exec_running",
        session_id="session_demo",
        turn_id="turn_demo",
        agent_mode="plan",
        input_preview="测试",
    )

    assert repository.mark_running_interrupted() == 1
    record = repository.get("exec_running")
    assert record is not None
    assert record.status == ExecutionStatus.INTERRUPTED
    repository.close()
