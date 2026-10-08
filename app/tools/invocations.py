"""Durable claims for keyed tool calls; uncertain outcomes are never retried.

The claim and result are separate transactions. A crash between them leaves a
RUNNING record, deliberately requiring reconciliation instead of repeating effects.
"""
import hashlib
import json
import os
import sqlite3
import threading
from dataclasses import dataclass

from app.errors import ToolIdempotencyError
from app.session.repository import sqlite_path_from_url, utc_now
from app.tools.schemas import ToolResult


def invocation_fingerprint(tool_name: str, arguments: dict, user_id: str | None) -> str:
    canonical = json.dumps(
        {"tool_name": tool_name, "arguments": arguments, "user_id": user_id},
        sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class InvocationClaim:
    claimed: bool
    status: str
    result: ToolResult | None = None


class SQLiteToolInvocationRepository:
    """Atomic cross-worker claim and persisted result replay for invocation keys."""

    def __init__(self, database_url: str) -> None:
        path = sqlite_path_from_url(database_url)
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        self._conn = sqlite3.connect(path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.execute(
            """CREATE TABLE IF NOT EXISTS tool_invocations (
                invocation_key TEXT PRIMARY KEY,
                fingerprint TEXT NOT NULL,
                status TEXT NOT NULL,
                result TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )"""
        )
        self._conn.commit()

    def claim(self, invocation_key: str, fingerprint: str) -> InvocationClaim:
        with self._lock:
            try:
                self._conn.execute("BEGIN IMMEDIATE")
                row = self._conn.execute(
                    "SELECT * FROM tool_invocations WHERE invocation_key = ?", (invocation_key,)
                ).fetchone()
                if row is None:
                    now = utc_now()
                    self._conn.execute(
                        "INSERT INTO tool_invocations VALUES (?, ?, 'RUNNING', NULL, ?, ?)",
                        (invocation_key, fingerprint, now, now),
                    )
                    self._conn.commit()
                    return InvocationClaim(True, "RUNNING")
                self._conn.commit()
            except BaseException:
                self._conn.rollback()
                raise
        if row["fingerprint"] != fingerprint:
            raise ToolIdempotencyError(
                "同一工具调用标识已绑定到不同工具、参数或用户", code="IDEMPOTENCY_CONFLICT"
            )
        result = ToolResult.from_json(row["result"]) if row["result"] else None
        return InvocationClaim(False, row["status"], result)

    def complete(self, invocation_key: str, result: ToolResult) -> None:
        # Serialize before opening a transaction; unsupported results stay uncertain.
        payload = result.to_json()
        self._set_outcome(invocation_key, "COMPLETED", payload)

    def mark_unknown(self, invocation_key: str) -> None:
        self._set_outcome(invocation_key, "UNKNOWN", None)

    def _set_outcome(self, invocation_key: str, status: str, result: str | None) -> None:
        with self._lock:
            self._conn.execute(
                "UPDATE tool_invocations SET status = ?, result = ?, updated_at = ? "
                "WHERE invocation_key = ? AND status = 'RUNNING'",
                (status, result, utc_now(), invocation_key),
            )
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()
