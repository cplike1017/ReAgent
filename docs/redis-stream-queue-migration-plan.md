# Redis Stream 任务队列替换执行计划

## 目标与边界

将 `/api/chat` 到 Worker 的任务传输从 Redis List（`RPUSH` / `BLPOP`）替换为 Redis Stream Consumer Group，并保留现有 Job Hash、HTTP 提交接口和查询接口。

本次实现提供：

- `XADD` 入队与 `XREADGROUP` 消费；
- Stream message ID 作为显式 delivery receipt；
- 成功或终态失败后的 `XACK`；
- PEL 检查与 `XAUTOCLAIM` 超时接管；
- `request_id` 幂等入队；
- 终态 Job 的重复投递抑制；
- 基于 Job attempt/终态的乐观锁，阻止迟到 Worker 覆盖已提交结果或重复创建 retry；
- Redis 事务内的重试交接：保存新 attempt、追加新消息、ACK/删除旧消息。

Redis Stream 的基础投递语义仍是 at-least-once。这里的 exactly-once 指 Redis Job 状态与结果只提交一次，并阻止已完成 Job 被重复执行业务处理。若 Agent 调用的外部工具产生不可回滚副作用，则还需要工具端接受 `job_id`/业务幂等键，或采用事务 outbox/inbox；单靠 Redis ACK 无法跨系统实现严格 exactly-once。

## 目标数据模型

| 键 | 类型 | 作用 |
| --- | --- | --- |
| `agent:jobs:stream` | Stream | 工作队列，每条消息至少包含 `job_id`。 |
| `agent:jobs:{job_id}` | Hash | Job 输入、attempt、状态、结果和错误。 |
| `agent:requests:{request_id}` | String | request 到 job 的幂等映射。 |

Consumer Group 默认名为 `agent-workers`。每个 Worker 使用稳定的进程级 consumer name。ACK 后删除队列 Stream 条目，Job Hash 按现有 TTL 继续保留查询结果。

## 执行阶段

### M1：契约与配置

1. 增加 Stream 名、Consumer Group、PEL claim idle 时间和读取阻塞时间配置。
2. 用失败测试定义幂等 XADD、group read、ACK、PEL claim、重试事务和终态去重。
3. 保持 `/api/chat` 和 `/api/jobs/{job_id}` 返回结构不变。

### M2：队列核心

1. 启动时幂等创建 Consumer Group（`MKSTREAM`）。
2. 入队时原子写入 request 映射、Job Hash、TTL 和 Stream 消息。
3. 消费返回 Job 与 Stream message ID；ACK 必须携带该 receipt。
4. 实现 PEL 统计与带扫描游标的 `XAUTOCLAIM`，允许其他 Worker 接管超时消息，并避免大量活跃 Pending 遮挡后续陈旧消息。
5. 将 retry 的状态保存、下一条 `XADD`、旧条目 `XACK`/删除放进一个 Redis 事务。

### M3：Worker 集成

1. Worker 创建唯一 consumer name，并优先处理可接管的陈旧 Pending 消息。
2. Agent 成功后原子提交结果并 ACK；达到最大次数的失败同样提交终态并 ACK。
3. 用乐观锁校验 attempt 和终态：首次终态提交获胜，迟到结果不能覆盖；重复 retry 不能追加第二条消息。
4. 如果收到已是 `SUCCEEDED`/`FAILED` 的重复 delivery，只 ACK，不再调用 Agent。
5. Worker 的阻塞读取同时监听退出信号，使空队列时也能及时优雅退出。
6. 更新 Stage 4 demo、README、`.env.example` 和 Docker 配置说明。

### M4：验证与上线

1. 运行 queue/worker、tracing、API 聚焦测试和全量离线回归。
2. 在 Redis 7 上做真实集成验收：两 Worker、杀死持有 Pending 的 Worker、等待 idle、确认另一 Worker claim 并完成。
3. 部署前停止旧 Worker；旧 List 与新 Stream 使用不同键，避免 Redis `WRONGTYPE`。
4. 部署新 API/Worker 后观察 PEL、失败率、处理延迟和重复抑制计数，再决定是否清理旧 List。

## 回滚

旧 List 键不会被原地改型。回滚应用版本即可恢复 List 消费；迁移窗口内进入新 Stream 但未处理的任务需要先导出 `job_id` 并重新提交到旧入口。生产切换前应短暂停写或由运维脚本完成双向任务盘点，避免静默遗留。
