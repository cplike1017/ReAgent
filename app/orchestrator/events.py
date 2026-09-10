"""多 Agent 编排的实时观察事件。

编排器不依赖 Web 层；它只在真实生命周期节点调用可选钩子。Web SSE、
CLI 或测试可各自订阅同一事实。ContextVar 让 delegate 触发的嵌套编排自动
继承当前观察器，同时保持并行 asyncio 任务之间的上下文隔离。
"""
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Awaitable, Callable


EventHook = Callable[..., Awaitable[None]]


@dataclass
class OrchestrationHooks:
    """编排与子 Agent 生命周期的可选观察钩子。

    回调参数保持为运行时实体（Plan、SubTask、LLMResponse、ToolResult），由
    边缘适配层负责脱敏、预览与协议序列化，避免编排核心耦合某个传输格式。
    """

    # 编排运行与计划：run_id, parent_run_id, depth, task, agents / plan
    orchestration_started: EventHook | None = None
    orchestration_plan_created: EventHook | None = None
    # 子 Agent 调度：run_id, agent_instance_id, step_index, SubTask
    agent_scheduled: EventHook | None = None
    agent_started: EventHook | None = None
    # 子 Agent 内部真实 ReAct 生命周期：run_id, instance_id, profile, ...
    agent_llm_started: EventHook | None = None
    agent_decision: EventHook | None = None
    agent_tool_started: EventHook | None = None
    agent_tool_completed: EventHook | None = None
    # 子 Agent 终态：run_id, agent_instance_id, step_index, AgentRunResult
    agent_completed: EventHook | None = None
    agent_failed: EventHook | None = None
    agent_skipped: EventHook | None = None
    # 汇总与编排终态：run_id, ...
    orchestration_synthesis_started: EventHook | None = None
    orchestration_completed: EventHook | None = None
    orchestration_failed: EventHook | None = None


# 根 Agent 进入一次运行时写入。delegate 内部和嵌套 delegate 会继承它；
# ContextVar 随 asyncio task 复制，因此并行子 Agent 互不串扰。
current_orchestration_hooks: ContextVar[OrchestrationHooks | None] = ContextVar(
    "current_orchestration_hooks", default=None
)


async def notify(hooks: OrchestrationHooks | None, name: str, *args: Any) -> None:
    """若订阅者实现了事件，则在实际节点调用它。"""
    callback = getattr(hooks, name, None) if hooks is not None else None
    if callback is not None:
        await callback(*args)
