# Stage 4：Redis Queue + Worker

> 阶段目标：把"HTTP 请求直接执行 Agent"改成"HTTP Gateway → Redis Queue → Worker → Agent Runtime"，
> 理解异步执行与并发扩展。

---

## 解决什么问题

Stage 3 之前，Agent 执行是同步的：一个 HTTP 请求进来，进程阻塞着跑完整个 ReAct 循环才返回。

两个致命问题：

1. **并发能力差**：慢任务（多次 LLM 调用 + 工具调用，秒级~十秒级）会占满进程，请求互相排队；
2. **耦合**：API 进程一旦崩溃，正在执行的 Agent 直接丢失；API 也无法横向扩容而不重复执行。

Stage 4 引入**消息队列**解耦：

```text
Client
  ↓ POST /api/chat
FastAPI Gateway（只接单：校验 + 幂等 + 入队，立即返回 job_id）
  ↓ XADD
Redis Stream（agent:jobs:stream）
  ↓ XREADGROUP / XAUTOCLAIM / XACK
Worker × N（Consumer Group：取 Job → 执行 Agent → 写结果并确认）
  ↓
Agent Runtime
```

- 为什么能提升并发？—— Gateway 只做毫秒级入队；真正的执行放到任意多个 Worker 上并行；
  Consumer Group 把新 Job 分发给不同 Worker，并把未确认消息保留在 PEL。
- 为什么 Gateway 和 Worker 要拆开？—— 各自可以独立扩容、独立部署、独立故障；
  Worker 崩溃不阻塞 API，Job 还可以重试。

## 上一阶段有什么缺陷

Stage 3 的 `runtime.run()` 仍是同步阻塞调用。文档虽然介绍了 `resume()`，
但没有任何机制让"一个请求"在进程间流转 —— 并发只能靠手动开多个进程，
没有统一的任务形态（Job）、没有状态查询、没有失败重试。

## 本阶段新增什么组件

| 组件 | 文件 | 职责 |
|---|---|---|
| Job 模型 | `app/queue/models.py` | `Job` / `JobStatus`（QUEUED/RUNNING/SUCCEEDED/FAILED） |
| Redis 队列 | `app/queue/producer.py` | Stream + Consumer Group + Job Hash：幂等入队、ACK、PEL 接管与原子重试 |
| 消费者 | `app/queue/consumer.py` | `process_job`：RUNNING → heartbeat → 成功 ACK / 重试 / 失败 ACK |
| Worker | `app/worker/worker.py` | 独立进程主循环：`XAUTOCLAIM` / `XREADGROUP` → 处理 → 循环 |
| HTTP Gateway | `app/api/routes.py` | `POST /api/chat`、`GET /api/jobs/{id}`、`GET /health` |
| 应用入口 | `app/main.py` | FastAPI + lifespan 连接 Redis |
| Docker | `Dockerfile` / `docker-compose.yml` | api / redis / worker 三服务，支持 `--scale worker=N` |

## 数据如何流动

```text
POST /api/chat {message, session_id?, idempotency_key?}
  → 生成 request_id / job_id
  → 幂等检查：request_id 已存在？返回已有 Job
  → Redis 事务：写 request 映射 + Job Hash + XADD Stream
  → 返回 {request_id, job_id, session_id, status: "QUEUED"}

Worker 循环：
  → 优先 XAUTOCLAIM 超时 Pending；否则 XREADGROUP 读取新消息
  → update_status(RUNNING)
  → heartbeat 刷新 PEL idle，防止长任务被误接管
  → 加载 Session → AgentRuntime.run(message, session_id)
  → Redis 事务：保存 SUCCEEDED + XACK + XDEL
  → 异常：attempt+1 < max_attempts ? 原子重试交接 : 保存 FAILED + XACK
```

## 核心数据结构

```python
class Job(BaseModel):
    job_id: str
    request_id: str            # 幂等键
    session_id: str
    input: dict                # {"message": "..."}
    attempt: int               # 已尝试次数
    created_at: str
    status: JobStatus          # QUEUED | RUNNING | SUCCEEDED | FAILED
    result: dict | None        # {"answer": ..., "trace_id": ...}
    error: dict | None         # {"type": ..., "message": ..., "code": ...}
    trace_context: dict        # Stage 6: {trace_id, parent_span_id}

# Redis 键布局
agent:jobs:stream           Stream # Consumer Group 工作队列
agent:jobs:{job_id}         Hash   # Job 全量字段
agent:requests:{request_id} String # request -> job 幂等映射
```

## 关键代码

```python
# 入队事务（producer.py）
WATCH agent:requests:{request_id}
MULTI
  SET agent:requests:{request_id} {job_id} EX {ttl}
  HSET agent:jobs:{job_id} ...
  XADD agent:jobs:stream * job_id {job_id}
EXEC

# 成功事务
HSET agent:jobs:{job_id} status SUCCEEDED ...
XACK agent:jobs:stream agent-workers {message_id}
XDEL agent:jobs:stream {message_id}

# 重试事务
HSET agent:jobs:{job_id} attempt {next_attempt} status QUEUED ...
XADD agent:jobs:stream * job_id {job_id}
XACK agent:jobs:stream agent-workers {old_message_id}
XDEL agent:jobs:stream {old_message_id}
```

## 输入示例

```bash
curl -X POST http://localhost:8000/api/chat \
  -H "Content-Type: application/json" \
  -d '{"message": "查询北京天气", "idempotency_key": "demo-1"}'
```

## 输出示例

```json
{"request_id": "demo-1", "job_id": "job_abc123", "session_id": "session_xxx", "status": "QUEUED"}
```

```bash
curl http://localhost:8000/api/jobs/job_abc123
```

```json
{"job_id": "job_abc123", "status": "SUCCEEDED",
 "result": {"answer": "北京天气：晴，25°C，微风。", "session_id": "session_xxx", "trace_id": null}}
```

## 如何运行

```bash
# 1) 启动整套服务（api + redis + worker）
docker compose up --build

# 2) 扩容 Worker
docker compose up --scale worker=3

# 3) 本地只跑队列 Demo（需要 Redis）
docker compose up -d redis
python -m demos.stage4_demo
```

## 如何测试

```bash
pytest tests/test_queue_worker.py -v
```

覆盖：幂等 XADD、Consumer Group 分发、ACK、PEL、`XAUTOCLAIM`、heartbeat、
终态重复 delivery 抑制、原子重试交接、Worker 成功处理与失败上限、
并发消费者、Trace 传播和 HTTP API（入队/查询/幂等/404/健康检查）。

## 常见错误

| 错误 | 原因 | 修复 |
|---|---|---|
| 幂等失效 | request 映射与 XADD 分两步，崩溃后状态不一致 | 用 WATCH/MULTI 原子写映射、Hash 与 Stream |
| 无限重试 | 重试逻辑没有上限 | `attempt+1 < max_attempts` 才重入队 |
| Pending 永久堆积 | Worker 崩溃后没人接管 PEL | 定期 `XAUTOCLAIM` 超时消息 |
| 长任务被重复接管 | 处理时间超过 claim idle | heartbeat 必须短于 claim idle |
| 外部副作用重复 | 工具成功但 Redis 终态提交前崩溃 | 工具端业务幂等键或事务 outbox/inbox |
| 队列积压 | Worker 太少 / 处理太慢 | `--scale worker=N` |
| 跨进程 SQLite 锁 | 多 Worker 同时写 | WAL 模式 + busy_timeout（已内置） |

## 面试如何表达

> "Stage 4 我把执行从 API 进程剥离开：API 只做参数校验、生成 request_id/job_id、
> 用 Redis 事务把 request 幂等映射、Job Hash 和 XADD 一起提交，然后立即返回；Worker
> 通过 Consumer Group 消费，成功后保存终态并 ACK。崩溃任务留在 PEL，由其他 Worker
> 在 idle 超时后 XAUTOCLAIM；活动任务用 heartbeat 防止误接管。重试交接同样在一个 Redis
> 事务内完成。Redis Job 状态可以做到 effectively-once，但跨外部系统的副作用仍需工具端
> 幂等键或 outbox/inbox，不能把 at-least-once 投递宣传成无条件 exactly-once。"

---

下一阶段：Stage 5 Tool Gateway + Policy —— Tool 怎么统一治理。
