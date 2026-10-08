"""Tool governance inherited by delegated tasks through their async context."""
from contextvars import ContextVar

current_tool_context: ContextVar[dict | None] = ContextVar("current_tool_context", default=None)
