# 科研基础：PPO 资料与证据闭环

交付范围：改进计划第 18 节的 M0/R01 首个切片。首个方向已确定为 **PPO 单智能体复现与实验分析**。本次实现 Project → Artifact → PaperVersion → EvidenceSpan → Claim → Markdown Report，支持本地归档和确定性查询。

这不是整个 M0–M6 路线图的完成状态。ExperimentSpec/Run、方法卡、文献联网检索、科研 UI、统计分析、训练 Runner 和模型科研评测仍在后续工作包中；当前聊天 Agent 尚未调用这些新 API。

## 1. 启用与存储

在项目根目录安装现有 `requirements.txt`，不需要增加依赖。在 `.env` 配置：

```env
RESEARCH_ENABLED=true
DATABASE_URL=sqlite:///./data/agent.db
RESEARCH_ARTIFACT_DIR=./data/research-artifacts
RESEARCH_MAX_ARTIFACT_BYTES=20971520
SANDBOX_DIR=./data/mcp_fs
```

```bash
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

开关默认关闭，关闭时科研接口返回 503/`research_disabled`，不创建科研表。更改开关或目录后重启 API；这些字段不在 Settings 页在线修改列表中。科研 API 和离线样例不使用 Redis、模型或 embedding 服务。

数据库沿用 `DATABASE_URL`，使用独立的 `research_*` 表与 `research_schema_migrations` 版本表。迁移以 SQLite 事务执行，失败整批回滚，未知迁移版本会拒绝启动科研模块。原 Session/Execution 表及 `user_version` 保持原有语义。

Artifact 存放于 `RESEARCH_ARTIFACT_DIR/<project_id>/<sha256>`。先落盘再写元数据；异常中断可能留下未引用的 hash 文件，重试可复用。没有自动垃圾回收，也不应在未检查数据库引用前删除文件。

Docker Compose 已将 API 的 Artifact 目录设为 `/data/research-artifacts`，保存在 `agent_data` 卷内。容器的 `SANDBOX_DIR` 是容器路径；建议在容器环境中设置为 `/data/research-imports`，再把资料放入该目录。不要直接沿用 Windows 主机绝对路径。

当前使用固定 `owner=local`；项目关联隔离可以防止错引资料，但不是用户认证或租户权限控制。沿用本地单用户部署方式。

## 2. 离线 PPO 样例

```bash
python -m demos.research_foundation_demo --output data/research-demo
```

该命令创建新目录，不覆盖既有样例。输出包括：

| 文件/目录 | 用途 |
| --- | --- |
| `research.db` | 可重新打开的科研记录数据库 |
| `sources/` | 固定论文元数据、摘要短摘录、合成指标及来源说明 |
| `artifacts/` | 按项目和 SHA-256 归档的不可变快照 |
| `report.md` | 来源范围、证据定位、待核验主张与限制 |
| `manifest.json` | 项目 ID、来源与报告 hash、未训练和合成数据标记 |

Demo 登记 3 篇固定版本论文。其中 PPO v2 绑定一个摘要短摘录；另外两篇只有元数据。合成 CSV 只用于展示 Artifact 归档，未计算均值、置信区间或算法排名。样例的来源和核查日期见 [fixture 说明](../demos/fixtures/research/README.md)，一次实际生成的报告见 [示例报告](examples/ppo-research-foundation-report.md)。

同一数据库状态反复导出的报告内容相同；新建 Demo 会得到不同记录 ID 和时间。需要在 API 中浏览 Demo 时，将 `DATABASE_URL` 和 `RESEARCH_ARTIFACT_DIR` 改为该输出目录中的数据库与快照目录，然后重启服务。

## 3. 最小 API 流程

将 `demos/fixtures/research/ppo_abstract_excerpt.txt` 复制到已配置的 `SANDBOX_DIR`。服务启动后，可执行：

```python
from pathlib import Path
import httpx

with httpx.Client(base_url="http://127.0.0.1:8000/api/research", timeout=30) as client:
    def post(path, payload):
        response = client.post(path, json=payload)
        response.raise_for_status()
        return response.json()

    project = post("/projects", {
        "title": "PPO 单智能体复现",
        "question": "复现 PPO 前，哪些实现与评估细节需要核对？",
    })
    base = f"/projects/{project['project_id']}"
    artifact = post(base + "/artifacts/import", {"path": "ppo_abstract_excerpt.txt"})
    paper = post(base + "/papers/import", {
        "source": "arxiv",
        "source_id": "1707.06347",
        "version": "v2",
        "title": "Proximal Policy Optimization Algorithms",
        "artifact_id": artifact["artifact_id"],
        "content_scope": "abstract",
    })
    text_response = client.get(base + f"/papers/{paper['paper_version_id']}/text")
    text_response.raise_for_status()
    span = post(base + "/evidence", {
        "paper_version_id": paper["paper_version_id"],
        "page": 1,
        "quote": text_response.json()["text"],
    })
    post(base + "/claims", {
        "text": "摘要提到多轮小批量更新，具体实现细节仍需核对全文。",
        "kind": "fact",
        "evidence_links": [{"evidence_id": span["evidence_id"], "relation": "supports"}],
    })
    report = client.get(base + "/report")
    report.raise_for_status()
    Path("ppo-report.md").write_text(report.text, encoding="utf-8")
    print(project["project_id"])
```

每次创建项目都产生新 ID；论文导入的唯一键是同一项目中的 `source + source_id + version`。上面的来源信息由调用方声明，服务不联网核验论文作者、标题或文件出处。

## 4. 接口目录

所有路径以 `/api/research` 开头；`p` 是项目 ID，`v` 是论文版本 ID。

| 方法 | 路径 | 行为 |
| --- | --- | --- |
| POST / GET | `/projects` | 创建 / 分页列出项目 |
| GET | `/projects/{p}` | 项目详情 |
| POST | `/projects/{p}/artifacts/import` | 从沙箱相对路径归档文件 |
| GET | `/projects/{p}/artifacts/{id}/content` | 校验大小与 hash 后下载快照 |
| POST | `/projects/{p}/papers/import` | 导入版本元数据，可绑定资料 |
| GET | `/projects/{p}/papers/{v}/text` | 提取指定页的文本片段 |
| POST | `/projects/{p}/evidence` | 匹配指定页摘录并保存定位 |
| POST | `/projects/{p}/claims` | 登记主张，可关联已有证据 |
| GET | `/projects/{p}/{resource}` | 列出 artifacts/papers/evidence/claims |
| GET | `/projects/{p}/{resource}/{id}` | 查询同项目记录 |
| GET | `/projects/{p}/report` | 下载确定性 Markdown 报告 |

列表参数 `limit=1..200`（默认 50）、`offset=0..2^63-1`。文本参数 `page>=1`、`offset=0..2^63-1`、`limit=1..20000`（默认 20000）；返回 `next_offset`、`truncated`、内容范围及解析器版本。报告在单个数据库快照中读取关联记录，每类最多 1000 条，超限返回 422，不静默漏掉后续条目。

领域错误格式是 `{"detail":{"code":"...","message":"..."}}`。不存在或跨项目引用返回 404；来源变更/资料校验失败返回 409；单文件超限返回 413；资料不可解析、摘录不存在等返回 422。请求 Schema 校验错误沿用 FastAPI 的 422 detail 列表格式。

## 5. 数据与证据契约

- 项目隔离：所有详情、下载和关联都带 `project_id`，SQLite 复合外键约束关联必须来自同一项目。数据库事务保护多表写入和并发版本导入。
- 资料导入：只接受沙箱内相对路径，拒绝绝对路径、越界与指向沙箱外的符号链接。支持 PDF、UTF-8 TXT/Markdown/CSV/JSON/JSONL；默认文件上限 20 MiB。文件类型依据后缀声明，解析在读取时验证。
- 快照：同一项目中相同 hash 返回同一 Artifact，保留首次文件名和 MIME 类型。修改源文件会产生新快照；读取或下载前校验归档文件的大小与 hash。
- 论文版本：arXiv ID 不含版本后缀，`version` 规范为 `vN`，URL 必须指向该版本；DOI 使用规范 ID 并转小写。版本首次导入后元数据不可覆盖，可从仅元数据状态补绑一次资料。更改标题/作者等会返回冲突；没有编辑和删除接口。
- 内容范围：绑定资料必须同时声明 `abstract/full_text/notes`；这是导入者声明，不是自动判断。`read_scope=metadata` 表示尚未提取；`selected_pages` 和 `read_pages` 只表示返回过这些页的片段，不代表全文阅读。
- 定位：PDF 使用从 1 开始的物理页号；文本文件只有逻辑页 1。空白统一为单个空格后精确匹配，记录规范文本中的 Unicode 字符偏移（左闭右开）、源文件 hash、摘录 hash 和解析器版本。重复摘录默认取该页首次匹配位置。
- 核验：`locator_verified=true` 仅证明摘录在导入资料中。Claim 的 `fact/inference/hypothesis` 是调用方分类，`supports/refutes/background` 是声明的关联；`verification_status` 始终为 `unverified`，API 拒绝伪造已核验状态。没有证据的主张会在报告中注明。
- 解析限制：不支持 OCR、密码 PDF 或结构化表格识别，解析失败不产生证据；资料导入不执行文件内容。当前无后台解析任务或沙箱进程，适合有限大小的本地可信资料。

## 6. 备份、恢复与回滚

升级前停止 API 和所有共用该 SQLite 的 Worker，备份数据库及整个 Artifact 目录到一个新目录。使用 SQLite backup API 生成一致的数据库副本，避免在 WAL 模式下只复制主文件而漏掉已提交数据：

```python
import shutil
import sqlite3
from pathlib import Path

backup = Path("data/research-backup-001")
backup.mkdir(parents=True, exist_ok=False)
with sqlite3.connect("data/agent.db") as source:
    with sqlite3.connect(backup / "agent.db") as target:
        source.backup(target)
shutil.copytree("data/research-artifacts", backup / "research-artifacts")
```

路径需替换成实际配置。备份期间保持服务停止，保证数据库与文件副本属于同一状态。恢复时在新目录复制备份、把两个配置指向恢复副本，然后启用科研 API 检查项目数、论文关联、证据、报告以及每份文件 hash，再恢复写入。

功能回滚可设置 `RESEARCH_ENABLED=false` 后重启，保留新增表和资料，不自动反向删表。旧版本程序不要修改较新科研 schema；要回退数据版本时使用经过验证的完整备份。

## 7. 验收与接续工作

```bash
python -m pytest -q tests/test_research_foundation.py
python -m pytest -q
```

当前验收覆盖持久化重启、迁移回滚、并发去重、项目隔离、路径和大小限制、损坏资料、版本绑定、PDF 页定位、未核验状态与 PPO 样例。测试只证明工程契约；不证明模型科研质量、训练可复现性或 PPO 性能。

2026-09-20 本地 Windows 验收：科研专项 38 passed；全量 443 passed、3 skipped，保留一个既有 Starlette/AnyIO 弃用警告。独立审查复核通过；CLI 样例、SQLite backup/restore、归档 hash 和报告一致性已验证。基线测试同时修正了一处 fakeredis 空 Stream 同毫秒复用 ID 的夹具问题，Redis 生产实现未改动。

下一工作包继续 R02/R03 的文献元数据检索、可追溯全文导入和资料查看，随后进入 R04 的 PPO 方法卡与 ExperimentSpec 校验、R05 的按独立训练 seed 进行统计分析。先固定协议和数据单位，再接入真实训练执行。
