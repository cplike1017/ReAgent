# HITL 审批与工具调用幂等

工具治理现已接入 ReAct、Plan 子步骤、委派 Agent 和 Redis Worker。审批与调用账本使用 `DATABASE_URL` 指定的 SQLite，新增表自动创建，不改变既有会话、检查点或科研表。

## 启用人工审批

保留现有放行默认值。配置需要审批的工具风险等级并重启 API / Worker：

```env
POLICY_REQUIRE_CONFIRMATION_RISKS=high,medium
TOOL_APPROVAL_TIMEOUT_SECONDS=300
TOOL_APPROVAL_POLL_SECONDS=0.1
```

命中确认策略后，工具停在执行前。Web 流式会话显示工具名、参数、策略、有效期和批准/拒绝按钮；`approval.requested` / `approval.resolved` 同时写入执行事件。审批参数中的凭据字段会脱敏，目标哈希仍绑定完整校验后参数、工具、调用者、调用标识与所属会话。模型没有批准工具。

审批状态为 `pending`、`approved`、`rejected`、`expired` 或 `cancelled`。待审批超时、拒绝或取消均不会执行工具；重启后可读取原审批记录。历史事件先按只读展示，再查询当前状态决定能否操作，旧 pending 事件不能重新激活终态请求。同步聊天和独立编排调用可通过以下 API 审核；审批 SSE 生命周期由流式聊天提供。

| 方法与路径 | 用途 |
| --- | --- |
| `GET /api/web/approvals?session_id=...&turn_id=...&status=pending` | 查看待审批列表；过滤条件可省略，`limit` 为 1–1000。 |
| `GET /api/web/approvals/{approval_id}` | 读取当前审批事实。 |
| `POST /api/web/approvals/{approval_id}/decision` | 提交明确决定，绑定实际读取的 `target_hash`。 |

决定请求示例：

```json
{
  "decision": "approved",
  "target_hash": "从审批详情读取的64位SHA256",
  "actor": "local-user",
  "reason": "已核对本次调用参数"
}
```

重复同一决定返回已保存记录；不同决定或目标哈希冲突返回 HTTP 409，未知审批返回 404，非法输入返回 422。审批仍沿用项目的本地单用户 API 部署边界；`actor` 是审计标签，不提供身份认证或多人角色授权。

## 调用账本与恢复

运行时在模型决定调用后，为每个调用保存 `invocation_key`，再执行工具。标识由会话、原始回合及调用序号确定，避免模型复用工具调用 ID 导致误合并。恢复或换一个恢复回合 ID 时，仍使用原标识。相同标识绑定不同工具、校验后参数或调用者时，网关返回 `IDEMPOTENCY_CONFLICT`。

SQLite 事务保证跨进程同标识只有一个调用取得执行权。已保存的 `COMPLETED` 结果直接复用，`metadata.idempotency_replayed=true` 表示复用；不再次执行处理器。`RUNNING` 返回 `INVOCATION_IN_PROGRESS`，`UNKNOWN` 返回 `INVOCATION_OUTCOME_UNKNOWN`。这两种状态不会自动到期后重新执行，必须先核对外部实际结果。

工具超时、取消、未保存结果前进程崩溃，或不可确认的写操作失败，可能已经产生副作用。网关将它们保留为执行中/未知结果；同步线程超时后继续运行也不会导致同标识重放。写入账本失败会阻断执行或返回 `INVOCATION_PERSISTENCE_FAILED`。外部写入和本地结果落库之间不具备原子事务，不能承诺无条件 exactly-once。

`ToolDefinition.side_effects` 标记副作用，`idempotent` 表示处理器自身具有重复执行保障。只读工具仍可重试瞬时错误；副作用工具只有明确声明 `idempotent=True` 才允许自动重试。文件写入、追加笔记、代码执行、邮件和委派默认禁止此类重试。MCP 使用 `readOnlyHint` / `idempotentHint`；缺失的声明按可能产生副作用处理。

直接调用 `gateway.execute(..., invocation_key="稳定业务标识")` 可使用同一账本。为了兼容原有直接调用，未提供标识的 ALLOW 调用保持原执行行为，不获得调用去重保障；REQUIRE_CONFIRMATION 缺少标识时仍拒绝。新模型调用、新回合或不同 key 是新调用；业务端仍应提供自身幂等键，不能把参数相同理解为同一次业务操作。

恢复还包括：多工具批次逐个保存剩余调用；Plan 保存独立子步骤消息、活动步骤和重规划任务，已完成步骤不重跑，临时工具消息不写入主会话；DONE 检查点直接返回保存的答案。Worker 用 `job_id` 固定原始回合，按同一任务检索检查点，不恢复另一个任务的状态。Worker 共享 SQLite 治理服务并在退出时关闭连接，任务拥有的网关与模型连接在完成后释放。

## 验证

新增契约覆盖审批目标绑定、决定并发/重复/过期/取消、重启列表、浏览器事件回放；调用并发争用、键冲突、结果复用、未知结果阻断、同步线程超时、安全重试声明；批次、Plan、重复崩溃与 Worker 完成提交失败恢复。

```powershell
.\.venv\Scripts\python.exe -m pytest -q
node --check app/static/app.js
```

本地浏览器验收使用独立 SQLite 和合成写入工具，验证审批前无写入、批准后单次写入、拒绝不写入与页面刷新后的审批状态。未调用真实模型、发送邮件或执行训练。
