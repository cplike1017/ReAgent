"""Web Agent 执行记录的共享模型。"""
from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class ExecutionStatus(str, Enum):
    """一次用户任务的生命周期状态。"""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    INTERRUPTED = "INTERRUPTED"


class ExecutionRecord(BaseModel):
    """一次 Web Agent 运行的稳定快照。"""

    execution_id: str
    session_id: str
    turn_id: str
    agent_mode: str
    status: ExecutionStatus = ExecutionStatus.QUEUED
    input_preview: str = ""
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    last_seq: int = 0
    trace_id: str | None = None
    answer_preview: str = ""
    error_type: str = ""
    error_message: str = ""


class ExecutionEvent(BaseModel):
    """可按序重放的一条执行事实。"""

    execution_id: str
    seq: int
    event_id: str
    event_type: str
    timestamp: str
    payload: dict[str, Any] = Field(default_factory=dict)


class ExecutionOutput(BaseModel):
    """执行事件关联的按需加载详情。

    SSE 仅携带预览和 ``output_id``；受控后的完整值保存在这里，避免长输出
    无限放大事件流，同时让历史执行仍可核对实际结果。
    """

    output_id: str
    execution_id: str
    kind: str
    content: Any = None
    content_type: str = "application/json"
    original_bytes: int = 0
    stored_bytes: int = 0
    truncated: bool = False
    created_at: str
