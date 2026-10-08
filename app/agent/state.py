"""Agent 状态（AgentState）：Checkpoint 保存/恢复的最小状态机。"""
from pydantic import BaseModel, Field

from app.agent.models import PlanStep


class AgentState(BaseModel):
    """Agent 在某一瞬间的完整执行状态。

    状态机：
        RUNNING      初始 / 工具执行完成，等待下一次 LLM 决策
        PENDING_TOOL LLM 已决定调用工具、尚未执行（可恢复点）
        DONE         已给出最终回答
        FAILED       执行失败

    pending_tool_calls 保存"LLM 决策但尚未执行"的工具调用；
    从 PENDING_TOOL 恢复时，沿用已保存的 invocation_key 通过网关查询结果；
    已完成的调用复用信封，执行中或结果未知的调用拒绝自动重放。

    Stage 9 规划层：plan 保存当前计划与每步状态（Checkpoint 一并持久化，
    崩溃恢复后计划不丢失）；plan_revisions 记录反思触发的重规划次数。
    """

    session_id: str
    turn_id: str
    invocation_turn_id: str = ""
    tool_call_sequence: int = 0
    step: int = 0
    status: str = "RUNNING"  # RUNNING | PENDING_TOOL | DONE | FAILED
    messages: list[dict] = Field(default_factory=list, description="当前消息序列（OpenAI 风格）")
    pending_tool_calls: list[dict] = Field(
        default_factory=list, description="待执行工具调用 [{'id','name','arguments'}]"
    )
    last_tool_result: dict | None = Field(default=None, description="最近一次工具执行结果信封")
    # ---- Stage 9 规划层 ----
    plan: list[PlanStep] = Field(default_factory=list, description="当前计划（plan 模式）")
    plan_revisions: int = Field(default=0, description="反思触发的重规划次数")
    plan_step_messages: list[dict] = Field(default_factory=list)
    plan_step_id: str = ""
    plan_task: str = ""
    agent_mode: str = Field(default="react", description="react | plan（本回合执行模式）")
