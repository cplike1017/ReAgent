"""Durable human decisions bound to one validated tool invocation.

SQLite is the authority: polling observes decisions from other processes and a
restart reuses the same request. Public payloads redact arguments; their hash
still binds the complete validated input. The local UI's actor is an audit label.
"""
import asyncio
import hashlib
import json
import math
from pathlib import Path
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, Field

from app.session.repository import sqlite_path_from_url
from app.tracing.recorder import redact


ApprovalStatus = Literal["pending", "approved", "rejected", "expired", "cancelled"]


class ApprovalConflictError(ValueError):
    code = "APPROVAL_CONFLICT"


class ApprovalNotFoundError(KeyError):
    code = "APPROVAL_NOT_FOUND"


class ToolApprovalRecord(BaseModel):
    approval_id: str
    invocation_key: str
    target_hash: str
    tool_name: str
    arguments: dict
    user_id: str | None = None
    context: dict = Field(default_factory=dict)
    reason: str = ""
    policy_name: str = ""
    status: ApprovalStatus = "pending"
    created_at: str
    expires_at: str
    decided_at: str | None = None
    actor: str | None = None
    decision_reason: str = ""

    @property
    def id(self) -> str:
        return self.approval_id

    def public_dict(self) -> dict:
        payload = self.model_dump(mode="json")
        payload["arguments"] = redact(payload["arguments"])
        return payload


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


class ToolApprovalService:
    """Atomic request creation and compare-and-set terminal decisions."""

    def __init__(self, db_path: str | Path, *, timeout_seconds: float = 300, poll_seconds: float = 0.1):
        if not all(math.isfinite(value) and value > 0 for value in (timeout_seconds, poll_seconds)):
            raise ValueError("Approval timeout and polling interval must be positive and finite")
        path = str(db_path)
        if path.startswith("sqlite:///"):
            path = sqlite_path_from_url(path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.timeout_seconds = timeout_seconds
        self.poll_seconds = poll_seconds
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript("""
            CREATE TABLE IF NOT EXISTS tool_approvals (
                approval_id TEXT PRIMARY KEY,
                invocation_key TEXT NOT NULL UNIQUE,
                target_hash TEXT NOT NULL,
                session_id TEXT,
                turn_id TEXT,
                status TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                record_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_tool_approvals_context
                ON tool_approvals(session_id, turn_id, status);
        """)
        self._conn.commit()

    def ensure_request(self, invocation_key: str, tool_name: str, arguments: dict,
                       user_id: str | None = None, context: dict | None = None,
                       reason: str = "", policy_name: str = "") -> ToolApprovalRecord:
        if not invocation_key or not tool_name:
            raise ValueError("Approval invocation key and tool name are required")
        context = dict(context or {})
        bound_context = {key: context.get(key) for key in ("session_id", "turn_id", "tool_call_id")}
        target = {"invocation_key": invocation_key, "tool_name": tool_name,
                  "arguments": arguments, "user_id": user_id, "context": bound_context}
        encoded = json.dumps(target, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        target_hash = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
        now = _now()
        record = ToolApprovalRecord(
            approval_id=f"approval_{uuid4().hex}", invocation_key=invocation_key,
            target_hash=target_hash, tool_name=tool_name, arguments=redact(arguments),
            user_id=user_id, context=context, reason=reason, policy_name=policy_name,
            created_at=now,
            expires_at=(datetime.fromisoformat(now) + timedelta(seconds=self.timeout_seconds)).isoformat(timespec="microseconds"),
        )
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT * FROM tool_approvals WHERE invocation_key = ?", (invocation_key,)).fetchone()
                if row is not None:
                    record = self._decode(row)
                    if record.target_hash != target_hash:
                        raise ApprovalConflictError("Invocation key is already bound to a different approval target")
                    record = self._expire(record, now)
                else:
                    self._conn.execute("""INSERT INTO tool_approvals
                        (approval_id, invocation_key, target_hash, session_id, turn_id, status, expires_at, record_json)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (record.id, invocation_key, target_hash, context.get("session_id"), context.get("turn_id"),
                         record.status, record.expires_at, record.model_dump_json()))
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        return record

    @staticmethod
    def _decode(row) -> ToolApprovalRecord:
        return ToolApprovalRecord.model_validate_json(row["record_json"])

    def _save_terminal(self, record: ToolApprovalRecord) -> None:
        self._conn.execute("""UPDATE tool_approvals SET status = ?, record_json = ?
            WHERE approval_id = ? AND status = 'pending'""", (record.status, record.model_dump_json(), record.id))

    def _expire(self, record: ToolApprovalRecord, now: str) -> ToolApprovalRecord:
        if record.status == "pending" and record.expires_at <= now:
            record.status = "expired"
            record.decided_at = now
            record.decision_reason = "Approval deadline elapsed"
            self._save_terminal(record)
        return record

    def get(self, approval_id: str) -> ToolApprovalRecord:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT * FROM tool_approvals WHERE approval_id = ?", (approval_id,)).fetchone()
                if row is None:
                    raise ApprovalNotFoundError(approval_id)
                record = self._expire(self._decode(row), _now())
                self._conn.commit()
                return record
            except BaseException:
                self._conn.rollback()
                raise

    def list_requests(self, *, session_id: str | None = None, turn_id: str | None = None,
                      status: ApprovalStatus | None = "pending", limit: int = 100) -> list[ToolApprovalRecord]:
        if not 1 <= limit <= 1000:
            raise ValueError("Approval list limit must be between 1 and 1000")
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                now = _now()
                expired = self._conn.execute("SELECT * FROM tool_approvals WHERE status = 'pending' AND expires_at <= ?", (now,)).fetchall()
                for row in expired:
                    self._expire(self._decode(row), now)
                clauses, values = [], []
                for column, value in (("session_id", session_id), ("turn_id", turn_id), ("status", status)):
                    if value is not None:
                        clauses.append(column + " = ?")
                        values.append(value)
                where = " WHERE " + " AND ".join(clauses) if clauses else ""
                rows = self._conn.execute("SELECT * FROM tool_approvals" + where + " ORDER BY rowid DESC LIMIT ?", (*values, limit)).fetchall()
                self._conn.commit()
                return [self._decode(row) for row in rows]
            except BaseException:
                self._conn.rollback()
                raise

    def decide(self, approval_id: str, decision: Literal["approved", "rejected"], *,
               target_hash: str, actor: str = "local-user", reason: str = "") -> ToolApprovalRecord:
        if decision not in {"approved", "rejected"}:
            raise ValueError("Decision must be approved or rejected")
        return self._resolve(approval_id, decision, target_hash=target_hash, actor=actor, reason=reason)

    def cancel(self, approval_id: str, reason: str = "") -> ToolApprovalRecord:
        return self._resolve(approval_id, "cancelled", reason=reason, allow_existing=True)

    def _resolve(self, approval_id: str, status: str, *, target_hash: str | None = None,
                 actor: str | None = None, reason: str = "", allow_existing: bool = False) -> ToolApprovalRecord:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                row = self._conn.execute("SELECT * FROM tool_approvals WHERE approval_id = ?", (approval_id,)).fetchone()
                if row is None:
                    raise ApprovalNotFoundError(approval_id)
                record = self._decode(row)
                if target_hash is not None and target_hash != record.target_hash:
                    raise ApprovalConflictError("Decision target hash does not match the reviewed invocation")
                now = _now()
                record = self._expire(record, now)
                if record.status != "pending":
                    if record.status != status and not allow_existing:
                        # Preserve expiry even when this late decision is rejected.
                        self._conn.commit()
                        raise ApprovalConflictError(f"Approval already resolved as {record.status}")
                else:
                    record.status = status
                    record.decided_at = now
                    record.actor = actor
                    record.decision_reason = reason
                    self._save_terminal(record)
                self._conn.commit()
                return record
            except BaseException:
                self._conn.rollback()
                raise

    async def wait_for_decision(self, approval_id: str) -> ToolApprovalRecord:
        try:
            while True:
                record = self.get(approval_id)
                if record.status != "pending":
                    return record
                await asyncio.sleep(self.poll_seconds)
        except asyncio.CancelledError:
            self.cancel(approval_id, reason="Waiting execution was cancelled")
            raise

    def close(self) -> None:
        with self._lock:
            self._conn.close()
