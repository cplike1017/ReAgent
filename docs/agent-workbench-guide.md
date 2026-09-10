# ReAgent Agent 工作台与执行透明化指南

## 目的与边界

新版 Web 工作台把一次用户提交保存为独立的 **execution**，并把真实发生的执行事实写入事件序列。它帮助使用者回答“任务现在到哪一步、为什么在等待、谁调用了什么、是否重试、结果是否可回放”。

透明化不等于暴露模型的隐含思维链：界面只展示实际采集到的生命周期、显式计划、工具/Agent 结果摘要、模型用量和错误事实。Prompt、原始记忆正文及已脱敏字段不会因为开启工作台而自动公开。

## 启动与进入工作台

```bash
.venv/bin/python -m uvicorn app.main:app --port 8000
```

打开 `http://127.0.0.1:8000/`。没有配置真实 LLM 时可使用默认 Stub 完成离线演示；配置真实模型前请按 `.env.example` 设置 LLM 相关变量。

工作台由三部分构成：

- 左侧：会话、工具、技能、MCP 与 Agent 档案入口；窄屏时可切换为导航抽屉。
- 中间：请求、回答和工具卡片；工具卡片以稳定 `tool_call_id` 对应结果，避免同名调用互相覆盖。
- 右侧：选中的 execution 的时间线、Plan、子 Agent、上下文事实、Trace 与执行历史；可按需收起或在窄屏打开检查器。

## 一次执行的真实状态

| 状态 / 事件 | 含义 | 界面行为 |
| --- | --- | --- |
| `execution.queued` / `QUEUED` | 服务已受理，但尚未取得单进程 Runtime 的执行槽 | 显示“排队中”；如果已有任务占用 Runtime，会明确说明正在等待另一项任务，而不假称模型已开始。 |
| `execution.started` / `RUNNING` | 已取得执行槽，Runtime 即将准备上下文 | 切换为“执行中”，随后出现上下文、模型、工具等真实事件。 |
| `execution.completed` / `SUCCEEDED` | 回答和执行记录已归档 | 显示完成状态、工具数、步骤数和可用 Trace。 |
| `execution.failed` / `FAILED` | 运行未能完成 | 保留失败前已采集的事件，并显示错误类型和信息。 |
| `execution.cancel_requested` | 客户端已请求停止 | 只显示“等待服务端确认”，不会提前伪造已取消。 |
| `execution.cancelled` / `CANCELLED` | 服务端确认任务已停止 | 保留已完成节点，终止计时。 |
| `INTERRUPTED` | 服务重启时发现上次有活动记录 | 历史页明确标为中断，不把它显示为成功。 |

直连 Web Runtime 为防止不同会话的可变 Agent 状态串扰而串行执行。因此排队是可观察的、可取消的正常状态，不是无响应。

## 时间线中的执行事实

| 类别 | 关键事件 / 字段 |
| --- | --- |
| 上下文 | `memory.retrieved`、`skill.selected`、`context.completed`、`checkpoint.saved/restored`、`memory.stored`；展示计数、标识和估算 token，不展示原文。 |
| Plan | `plan.created/revised`、`plan_step.started/completed/failed`、`plan.summarize_started`、`reflection.completed`；保留计划版本和步骤状态。 |
| 模型 | `llm.started`、`llm.retry_scheduled`、`llm.failed`、`step`；`step` 包含模型名及提供方返回的 prompt/completion/total token 用量。未返回用量时界面明确显示“用量未采集”。 |
| 工具 | `tool.started`、`tool.retry_scheduled`、`tool_result`；展示调用 ID、参数预览、持续时间、重试次数和结果摘要。 |
| 编排 | `orchestration.*`、`agent.*`、`agent.llm.*`、`agent.tool.*`；以 `run_id` 和 `agent_instance_id` 区分实际子 Agent 实例。 |

时间线可按状态筛选为“全部”“进行中”“完成”和“需关注”。右上角计数会在筛选生效时显示“当前可见 / 总事件数”；该选择仅影响本地阅读视图，不会删除或改变服务端记录。长错误、重试理由或上下文摘要会以单行预览显示，点击“查看完整详情”可在原事件内展开已采集的安全文本。

重试事件只在客户端或工具网关确实计划下一次尝试时发出。最终成功不会抹去先前的失败尝试；最终失败会同时保留对应轮次的 `llm.failed` 或工具错误。

## 历史、续接、停止与详情

execution 的事件先写入 SQLite，再通过 SSE 发布。断开当前浏览器不会自动取消后台任务；重新打开执行历史会按单调 `seq` 回放，并对仍在活动的 `QUEUED` / `RUNNING` 任务继续订阅。

| 接口 | 用途 |
| --- | --- |
| `GET /api/web/sessions/{session_id}/executions` | 获取会话的执行历史。 |
| `GET /api/web/executions/{execution_id}` | 获取当前状态、开始/结束时间和摘要。 |
| `GET /api/web/executions/{execution_id}/events?after_seq=N` | 分页读取持久化事实。 |
| `GET /api/web/executions/{execution_id}/stream?after_seq=N` | 从指定序号回放并续接活动执行。 |
| `POST /api/web/executions/{execution_id}/cancel` | 幂等请求取消排队或运行中的直连任务。 |
| `GET /api/web/executions/{execution_id}/outputs/{output_id}` | 按需读取经过脱敏和大小限制后的完整工具/子 Agent 输出。 |

实时事件只携带安全预览；完整输出走最后一个按需接口。输出在存储前递归脱敏，并受 `EXECUTION_OUTPUT_MAX_BYTES` 上限控制，超过上限时会明确标记截断。

## 验收与回归

后端回归覆盖 Web SSE、事件持久化/回放、取消、Plan、子 Agent、上下文事实、工具与模型重试、输出详情及排队状态：

```bash
.venv/bin/python -m pytest -q
```

手动验收建议至少覆盖：普通问答、工具调用与重试、Plan、委派子 Agent、失败、取消、刷新后重放，以及 1440×900、1024×768、390×844 三种视口。验收时以时间线的实际事件和持久化记录为准，不以动画或伪进度代替事实。

## 当前限制

- 直连 Web execution 的实时任务管理是单进程实现；Redis/Worker 的跨进程事件订阅、取消路由和统一序号尚未接入。
- 页面展示整段最终回答；没有真实提供方增量时不会伪造逐字流式输出。
- 没有模型提供方 usage 时，无法推导成本或编造 token；界面仅显示实际返回的字段。
- execution 和输出查询沿用当前单用户部署边界；多租户部署前需要补充身份认证、授权范围和保留策略。
