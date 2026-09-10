"""SQLite 执行与事件仓库。

事件先落库再经 SSE 发出，因此刷新页面或重新订阅时可以从单调递增的 seq
继续回放，而不是依赖内存中的临时 UI 状态。
"""
import json
import os
import sqlite3
import threading
from datetime import datetime, timezone
from uuid import uuid4

from app.execution.models import ExecutionEvent, ExecutionOutput, ExecutionRecord, ExecutionStatus
from app.tracing.recorder import redact
from app.session.repository import sqlite_path_from_url


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SQLiteExecutionRepository:
    """保存 Web 运行快照与事件序列。"""

    def __init__(self, database_url: str, *, output_max_bytes: int = 262144) -> None:
        path = sqlite_path_from_url(database_url)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._lock = threading.Lock()
        # 详情不经 SSE 广播；仍需设置硬上限，防止单个工具结果无限增长。
        self.output_max_bytes = max(1, int(output_max_bytes))
        self._init_schema()

    def _init_schema(self) -> None:
        with self._lock:
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS executions (
                    execution_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    turn_id TEXT NOT NULL,
                    agent_mode TEXT NOT NULL,
                    status TEXT NOT NULL,
                    input_preview TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    last_seq INTEGER NOT NULL DEFAULT 0,
                    trace_id TEXT,
                    answer_preview TEXT NOT NULL DEFAULT '',
                    error_type TEXT NOT NULL DEFAULT '',
                    error_message TEXT NOT NULL DEFAULT ''
                );
                CREATE INDEX IF NOT EXISTS idx_executions_session
                    ON executions(session_id, created_at DESC);

                CREATE TABLE IF NOT EXISTS execution_events (
                    execution_id TEXT NOT NULL,
                    seq INTEGER NOT NULL,
                    event_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    PRIMARY KEY (execution_id, seq),
                    UNIQUE (event_id)
                );
                CREATE INDEX IF NOT EXISTS idx_execution_events_lookup
                    ON execution_events(execution_id, seq);

                CREATE TABLE IF NOT EXISTS execution_outputs (
                    output_id TEXT PRIMARY KEY,
                    execution_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    content_json TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    original_bytes INTEGER NOT NULL,
                    stored_bytes INTEGER NOT NULL,
                    truncated INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_execution_outputs_lookup
                    ON execution_outputs(execution_id, created_at DESC);
                """
            )
            self._conn.commit()

    def create(
        self,
        *,
        execution_id: str,
        session_id: str,
        turn_id: str,
        agent_mode: str,
        input_preview: str,
    ) -> ExecutionRecord:
        now = utc_now()
        record = ExecutionRecord(
            execution_id=execution_id,
            session_id=session_id,
            turn_id=turn_id,
            agent_mode=agent_mode,
            status=ExecutionStatus.QUEUED,
            input_preview=input_preview,
            created_at=now,
            started_at=None,
        )
        with self._lock:
            self._conn.execute(
                """
                INSERT INTO executions (
                    execution_id, session_id, turn_id, agent_mode, status,
                    input_preview, created_at, started_at, last_seq
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    record.execution_id, record.session_id, record.turn_id,
                    record.agent_mode, record.status.value, record.input_preview,
                    record.created_at, record.started_at,
                ),
            )
            self._conn.commit()
        return record

    def start(self, execution_id: str) -> ExecutionRecord:
        """把已受理的运行切换为真正开始执行。

        Web Runtime 目前为避免共享回合状态串扰而串行化执行；因此创建记录
        不等于已经拿到执行槽。这个转换把排队时间与模型/工具实际开始时间
        明确区分，并保持对旧的 RUNNING 记录幂等。
        """
        now = utc_now()
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if row is None:
                raise KeyError(f"execution 不存在: {execution_id}")
            if row["status"] == ExecutionStatus.QUEUED.value:
                self._conn.execute(
                    """
                    UPDATE executions
                    SET status = ?, started_at = ?
                    WHERE execution_id = ? AND status = ?
                    """,
                    (
                        ExecutionStatus.RUNNING.value,
                        now,
                        execution_id,
                        ExecutionStatus.QUEUED.value,
                    ),
                )
                self._conn.commit()
                row = self._conn.execute(
                    "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
                ).fetchone()
        return self._record_from_row(row)

    def get(self, execution_id: str) -> ExecutionRecord | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
        return self._record_from_row(row) if row is not None else None

    def list_for_session(self, session_id: str, limit: int = 50) -> list[ExecutionRecord]:
        limit = max(1, min(int(limit), 200))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT * FROM executions
                WHERE session_id = ?
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [self._record_from_row(row) for row in rows]

    def append_event(self, execution_id: str, event_type: str, payload: dict) -> ExecutionEvent:
        """事务内分配 seq：同一 execution 永远不会产生重复或乱序事件。"""
        now = utc_now()
        with self._lock:
            row = self._conn.execute(
                "SELECT last_seq FROM executions WHERE execution_id = ?",
                (execution_id,),
            ).fetchone()
            if row is None:
                raise KeyError(f"execution 不存在: {execution_id}")
            seq = int(row["last_seq"]) + 1
            event = ExecutionEvent(
                execution_id=execution_id,
                seq=seq,
                event_id=f"evt_{execution_id.removeprefix('exec_')}_{seq:06d}",
                event_type=event_type,
                timestamp=now,
                payload=dict(payload),
            )
            self._conn.execute(
                """
                INSERT INTO execution_events (
                    execution_id, seq, event_id, event_type, timestamp, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    event.execution_id, event.seq, event.event_id, event.event_type,
                    event.timestamp, json.dumps(event.payload, ensure_ascii=False, default=str),
                ),
            )
            self._conn.execute(
                "UPDATE executions SET last_seq = ? WHERE execution_id = ?",
                (seq, execution_id),
            )
            self._conn.commit()
        return event

    def store_output(self, execution_id: str, *, kind: str, content) -> ExecutionOutput:
        """保存一份按需加载的工具/子 Agent 输出。

        写入前先递归脱敏；超过 ``output_max_bytes`` 时保留受控预览并明确标记，
        不让详情表成为绕过 SSE 大小限制的无限存储通道。
        """
        with self._lock:
            exists = self._conn.execute(
                "SELECT 1 FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if exists is None:
                raise KeyError(f"execution 不存在: {execution_id}")

            safe_content = redact(content)
            original_json = json.dumps(safe_content, ensure_ascii=False, default=str)
            original_bytes = len(original_json.encode("utf-8"))
            truncated = original_bytes > self.output_max_bytes
            stored_content = safe_content
            if truncated:
                stored_content = {
                    "omitted": True,
                    "preview": _truncate_utf8(original_json, self.output_max_bytes),
                    "reason": "输出超过受控详情保留上限，以下为脱敏后的截断预览。",
                }
            stored_json = json.dumps(stored_content, ensure_ascii=False, default=str)
            output = ExecutionOutput(
                output_id=f"out_{uuid4().hex[:16]}",
                execution_id=execution_id,
                kind=kind,
                content=stored_content,
                content_type="application/json",
                original_bytes=original_bytes,
                stored_bytes=len(stored_json.encode("utf-8")),
                truncated=truncated,
                created_at=utc_now(),
            )
            self._conn.execute(
                """
                INSERT INTO execution_outputs (
                    output_id, execution_id, kind, content_json, content_type,
                    original_bytes, stored_bytes, truncated, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    output.output_id,
                    output.execution_id,
                    output.kind,
                    stored_json,
                    output.content_type,
                    output.original_bytes,
                    output.stored_bytes,
                    int(output.truncated),
                    output.created_at,
                ),
            )
            self._conn.commit()
        return output

    def get_output(self, execution_id: str, output_id: str) -> ExecutionOutput | None:
        """仅在父 execution 匹配时返回详情，避免不透明的跨运行引用。"""
        with self._lock:
            row = self._conn.execute(
                """
                SELECT output_id, execution_id, kind, content_json, content_type,
                       original_bytes, stored_bytes, truncated, created_at
                FROM execution_outputs
                WHERE execution_id = ? AND output_id = ?
                """,
                (execution_id, output_id),
            ).fetchone()
        return self._output_from_row(row) if row is not None else None

    def list_events(
        self, execution_id: str, *, after_seq: int = 0, limit: int = 500
    ) -> list[ExecutionEvent]:
        after_seq = max(0, int(after_seq))
        limit = max(1, min(int(limit), 1000))
        with self._lock:
            rows = self._conn.execute(
                """
                SELECT execution_id, seq, event_id, event_type, timestamp, payload_json
                FROM execution_events
                WHERE execution_id = ? AND seq > ?
                ORDER BY seq ASC
                LIMIT ?
                """,
                (execution_id, after_seq, limit),
            ).fetchall()
        return [
            ExecutionEvent(
                execution_id=row["execution_id"],
                seq=row["seq"],
                event_id=row["event_id"],
                event_type=row["event_type"],
                timestamp=row["timestamp"],
                payload=json.loads(row["payload_json"]),
            )
            for row in rows
        ]

    def finish(
        self,
        execution_id: str,
        *,
        status: ExecutionStatus,
        trace_id: str | None = None,
        answer_preview: str = "",
        error_type: str = "",
        error_message: str = "",
    ) -> ExecutionRecord:
        if status in {ExecutionStatus.QUEUED, ExecutionStatus.RUNNING}:
            raise ValueError("finish 只能写入终态")
        now = utc_now()
        with self._lock:
            existing = self._conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
            if existing is None:
                raise KeyError(f"execution 不存在: {execution_id}")
            if existing["status"] not in {
                ExecutionStatus.QUEUED.value,
                ExecutionStatus.RUNNING.value,
            }:
                return self._record_from_row(existing)

            self._conn.execute(
                """
                UPDATE executions
                SET status = ?, finished_at = ?, trace_id = ?,
                    answer_preview = ?, error_type = ?, error_message = ?
                WHERE execution_id = ?
                """,
                (
                    status.value, now, trace_id, answer_preview,
                    error_type, error_message, execution_id,
                ),
            )
            self._conn.commit()
            row = self._conn.execute(
                "SELECT * FROM executions WHERE execution_id = ?", (execution_id,)
            ).fetchone()
        return self._record_from_row(row)

    def mark_running_interrupted(self) -> int:
        """应用启动时标记前次进程异常退出留下的活动运行记录。"""
        now = utc_now()
        with self._lock:
            cursor = self._conn.execute(
                """
                UPDATE executions
                SET status = ?, finished_at = ?, error_type = ?, error_message = ?
                WHERE status IN (?, ?)
                """,
                (
                    ExecutionStatus.INTERRUPTED.value, now, "ProcessInterrupted",
                    "服务进程重启，执行未完成。",
                    ExecutionStatus.QUEUED.value,
                    ExecutionStatus.RUNNING.value,
                ),
            )
            self._conn.commit()
        return int(cursor.rowcount)

    @staticmethod
    def _output_from_row(row: sqlite3.Row) -> ExecutionOutput:
        return ExecutionOutput(
            output_id=row["output_id"],
            execution_id=row["execution_id"],
            kind=row["kind"],
            content=json.loads(row["content_json"]),
            content_type=row["content_type"],
            original_bytes=row["original_bytes"],
            stored_bytes=row["stored_bytes"],
            truncated=bool(row["truncated"]),
            created_at=row["created_at"],
        )

    @staticmethod
    def _record_from_row(row: sqlite3.Row) -> ExecutionRecord:
        return ExecutionRecord(
            execution_id=row["execution_id"],
            session_id=row["session_id"],
            turn_id=row["turn_id"],
            agent_mode=row["agent_mode"],
            status=ExecutionStatus(row["status"]),
            input_preview=row["input_preview"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            last_seq=row["last_seq"],
            trace_id=row["trace_id"],
            answer_preview=row["answer_preview"],
            error_type=row["error_type"],
            error_message=row["error_message"],
        )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _truncate_utf8(text: str, byte_limit: int) -> str:
    """按 UTF-8 字节边界截断，避免详情预览产生损坏字符。"""
    return text.encode("utf-8")[:byte_limit].decode("utf-8", errors="ignore")
