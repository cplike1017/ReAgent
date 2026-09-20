# 通用科研基础：项目、资料与证据闭环

交付范围：改进计划第 18 节的 M0/R01 首个切片及 M1 的 Web Agent 工具接入。实现与算法无关的 Project → Artifact → PaperVersion → EvidenceSpan → Claim → Markdown Report，支持不同研究主题的本地归档、确定性查询和聊天工具调用。用户选择的 **PPO 单智能体复现与实验分析** 仅作为首个验收样例。

产品目标是通用 Agent 加上可复用的强化学习科研能力：根据用户问题选择资料、工具和工作流。PPO、其他算法及不同实验环境应作为项目输入或适配器配置，不能成为公共模型、路由或任务规划的固定前提。

这不是整个 M0–M6 路线图的完成状态。当前全文能力限于受控 arXiv PDF 快照和按需页提取；批量资料集、OCR、PDF 分块索引、ExperimentSpec/Run、方法卡、科研 UI、统计分析和训练 Runner 仍在后续工作包中。Web 聊天通过 ToolGateway 调用与 API 相同的 ResearchService，不绕经内部 HTTP。

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

开关默认关闭，关闭时科研接口返回 503/`research_disabled`，不创建科研表。更改开关或目录后重启 API；这些字段不在 Settings 页在线修改列表中。基础科研 API 和离线样例不使用 Redis 或模型；项目混合检索在 `semantic=true` 时使用配置的 embedding，失败会保留词法结果并记录降级模式。

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

Web 聊天工具用法与离线对话验收见[第 8 节](#research-chat-tools)。

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
| GET | `/literature/arxiv/search` | 结构化搜索明确 arXiv 版本 |
| POST | `/projects/{p}/artifacts/import` | 从沙箱相对路径归档文件 |
| GET | `/projects/{p}/artifacts/{id}/content` | 校验大小与 hash 后下载快照 |
| POST | `/projects/{p}/papers/import` | 导入版本元数据，可绑定资料 |
| POST | `/projects/{p}/papers/import/arxiv` | 核对精确版本并保存摘要快照 |
| POST | `/projects/{p}/papers/import/arxiv/full-text` | 核对精确版本并保存受限 PDF 快照 |
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
- 论文版本：arXiv ID 不含版本后缀，`version` 规范为 `vN`，URL 必须指向该版本；DOI 使用规范 ID 并转小写。版本首次导入后元数据不可覆盖，可从仅元数据状态补绑一次资料。专用 arXiv 全文入口允许把 metadata/abstract 提升为已校验 PDF，并保留 `abstract_artifact_id`；notes 或已有其他全文不会被替换。更改标题/作者等会返回冲突；没有通用编辑和删除接口。
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

M0/R01 历史验收（2026-09-20）：科研专项 38 passed；全量 443 passed、3 skipped，保留一个既有 Starlette/AnyIO 弃用警告。独立审查复核通过；CLI 样例、SQLite backup/restore、归档 hash 和报告一致性已验证。基线测试同时修正了一处 fakeredis 空 Stream 同毫秒复用 ID 的夹具问题，Redis 生产实现未改动。

M1 工具接入验收（2026-09-20）：新增工具/脚本对话测试 15 passed；全量 458 passed、3 skipped，跳过项为 Windows 符号链接权限限制，另有一个既有依赖弃用警告。独立审查发现的样例 Trace 模型标签已修正为 `scripted-research-fixture`，修正后 15 项专项测试再次通过。实际运行的 7 次工具调用产物保存在本地 `output/research-tools-20260920-verified/`，包括对话、报告与 Trace；这些结果不构成真实模型科研评测。

M1/R02 结构化文献验收（2026-09-20）：最终全量回归 472 passed、3 skipped，保留同一个依赖弃用警告。Docker Compose 使用临时测试覆盖启用科研服务和 Stub 模型，真实 arXiv 搜索与进程内缓存通过；`1707.06347v2` 的精确版本摘要导入、重复导入去重和读取 SHA-256 一致性通过。Redis Worker 的两个 Stub 队列任务均到达 `SUCCEEDED`。这些结果验证工程链路，不是全文核验、真实模型科研质量或训练结果。

M1/R03 全文快照验收（2026-09-20）：9 项下载/提升专项契约与 85 项科研组合回归通过；最终全量 481 passed、3 skipped，保留同一个依赖警告。Docker 实际导入 `1707.06347v2` 的 2,923,532-byte、12 页 PDF，重复调用复用同一 Artifact，页读取 SHA-256 一致；摘要提升路径保留原摘要并列出两份 Artifact。这些结果不表示已读完 12 页，也不构成语义或科学核验。

M1 固定证据工程门槛（2026-09-20）：`research-evidence-v1` 包含 10 份明确标注为 synthetic 的文本资料和 30 条 Claim。独立 CLI 运行验证 10 个 Artifact/版本重复导入复用、30 个定位全部可重开、所有 Artifact hash 一致、所有 Claim 保持 `unverified`，且 `model_calls=0`、`training_executed=false`。科研/评测组合回归 94 passed；最终全量 484 passed、3 skipped，保留同一个依赖警告。这满足规模与引用链的离线工程验收，不替代真实公开论文的语义标注集或真实模型任务。

M1 真实供应商验收脚本（2026-09-20）：新增隔离的公开 DQN `1312.5602v1` 任务，要求模型完成 `research_read_page`、`research_create_evidence`、`research_create_claim` 和 `research_export_report`，并从持久记录重新评分。实际诊断运行成功固定 483,443-byte、9 页 PDF，随后供应商在首次模型请求返回 HTTP 401 无效令牌；manifest 正确记录 `model_calls=1`、零工具调用和失败门槛，未生成 Evidence/Claim，也未执行训练。该结果只证明失败路径可审计；更换有效凭据并得到通过的独立运行前，真实模型任务验收仍未完成。新增验收后科研组合回归 83 passed；最终全量 488 passed、3 skipped，保留同一个依赖警告。

配置修正后的独立运行通过：DeepSeek 实际读取 page 1，第一次摘录因不在规范化页文本中被服务拒绝，随后重试并成功保存一个 EvidenceSpan、一个关联的 `unverified` Claim 和报告。运行共 6 次模型调用、5 次工具请求、59,893 tokens；最终答案和报告均包含持久化 ID，`training_executed=false`。Docker API/Worker 也已从临时 Stub 覆盖切换到 DeepSeek，健康检查和一个真实队列消息通过。该结果证明单个公开非 PPO 任务的工具/持久化闭环，不代表论文语义已经人工核验，也不代表多任务科研质量或成本达到发布标准。

工具白名单复测使用同一论文和验收标准，只暴露 4 个必需工具：5 次模型调用、4 次工具调用、18,143 tokens，一次通过，较前次总 token 减少约 69.7%。这说明工具 Schema 是主要上下文成本之一；该白名单仅用于固定验收运行，不改变 Web 默认工具集。

Web Agent 工具、R02 的结构化 arXiv 元数据/摘要导入、R03 的受控 PDF 快照/页级读取、fixed 10/30 工程门槛、一个真实模型非 PPO 任务、持久查询历史和项目混合检索已经落地。下一步补 Library/Evidence Inspector、比较矩阵与导出，再进入 R04 方法卡和 ExperimentSpec 校验。R05 实现按独立训练 seed 进行的统计分析。算法特定规则和执行命令通过适配器提供，验收必须同时覆盖非 PPO 任务和不涉及训练的文献任务。

M1 检索切片验收（2026-09-20）：6 项查询/迁移契约、90 项科研组合回归通过；最终全量 495 passed、3 skipped，保留同一个依赖警告。Docker 在持久 `/data` 上应用迁移 `[1,2]`，真实 `qwen3.7-text-embedding` 返回 1024 维向量；两篇 synthetic 元数据的重复混合查询保持同一首位结果、2 个候选缓存和 2 条查询历史，API 重启后历史仍可读取。该验收不表示 PDF 全文已建立向量索引或结果经过人工语义判定。

<a id="research-chat-tools"></a>

## 8. Web 聊天科研工具

启用 `RESEARCH_ENABLED=true` 并重启 API 后，Web 直连聊天的工具列表增加以下 16 个工具。主 Agent 使用现有模型选择工具；ReAct 和 Plan 执行均沿用 ToolGateway。关闭科研开关时不注册工具、不创建科研表。API 与工具共享服务、数据库和快照目录。

| 工具 | 用途 |
| --- | --- |
| `research_create_project` | 保存用户指定的问题与范围 |
| `research_list_projects` / `research_get_project` | 分页找项目、读取项目范围 |
| `research_import_artifact` | 从已配置沙箱的相对路径保存快照 |
| `research_import_paper` | 登记来源版本并可绑定项目资料 |
| `research_search_arxiv` | 结构化搜索明确版本、摘要、日期和分类 |
| `research_search_project` / `research_list_queries` | 检索项目已有记录并复查持久查询历史 |
| `research_import_arxiv` | 重新查询精确版本并保存摘要快照 |
| `research_import_arxiv_full_text` | 下载并校验精确版本 PDF，保存全文快照 |
| `research_list_records` / `research_get_record` | 查询项目的 artifacts/papers/evidence/claims |
| `research_read_page` | 返回页片段、来源 hash、范围及 next_offset |
| `research_create_evidence` | 校验摘录真实存在于指定资料页 |
| `research_create_claim` | 保存证据关联与待核验主张 |
| `research_export_report` | 返回含项目 ID 的 Markdown 正文 |

将资料放入 `SANDBOX_DIR` 后，可在 Web 聊天中输入：

> 列出已有研究项目。如果没有“DQN 评估协议比较”，按这个主题创建一个项目。将沙箱里的 notes.txt 归档为本地资料 v1，范围标为 notes。阅读资料，摘录一条确实存在的原文，保存为待核验主张并导出证据报告。这次只整理文献。

实际模型是否正确执行需要任务评测；Stub 不会自行理解并规划这个新流程。工具不绑定 PPO，也不自动选择训练任务。已有项目时最好在消息中给出目标 `project_id`；会话没有隐含的全局当前项目。除了创建/列出项目，每个工具都要求显式项目 ID，证据与资料关联由服务端校验。

分页列表默认 50、最多 200 条；页读取最多 20000 字符，`next_offset` 非空时继续读取。`resource` 只接受四种科研集合；额外输入字段和 `verification_status=verified` 被拒绝。错误保留 `research_not_found`、`research_invalid`、`research_conflict` 等领域代码，模型应先核对来源和参数。创建项目、证据与主张尚无业务幂等键；Gateway 不自动重试这些领域失败，超时后需先查询是否已经写入，避免重复创建。

同一链路保留工具结果、会话消息和现有 Trace（按 Trace 配置采集）。`locator_verified` 仅证明摘录定位；`verification_status` 固定为 `unverified`。导出报告返回正文，不创建额外文件；二进制资料下载仍走 API。

当前接入 Web 进程内运行时。Redis Worker 工厂尚未注入科研服务；Web 编排中的 researcher 档案可使用 `research_*`，其他专用档案保持原白名单。单用户项目关联校验不等同于多用户权限控制。

### 项目混合检索与查询历史

```http
POST /api/research/projects/{project_id}/search
Content-Type: application/json

{"query":"value learning control","resources":["papers","evidence","claims"],"semantic":true,"limit":10}
```

检索范围严格限制为一个项目已保存的 PaperVersion、EvidenceSpan 和 Claim。论文候选使用元数据与已保留的文本摘要/notes；Evidence 和 Claim 使用原记录文本。系统不会为检索遍历 PDF 全文，也不会把命中页写入 `read_pages`。词法分数始终可用；`semantic=true` 时批量生成候选向量并组合排序，候选向量按项目、资源、记录、文本 SHA-256 和 embedding 模型缓存。查询向量不缓存。

响应中的 `mode` 为 `hybrid`、`lexical` 或 `lexical_fallback`，`semantic_available` 明确说明本次是否使用了语义分数。Embedding 网络、模型或维度异常只降级本次排序；查询和返回的 `resource:record_id` 仍写入 SQLite v2，可通过 `GET /api/research/projects/{project_id}/queries` 或 `research_list_queries` 分页复查。命中结果不改变 Claim 的 `unverified` 状态，也不表示语义蕴含已由人工确认。每类候选首版最多读取 1000 条，单次返回最多 50 条。

### 结构化 arXiv 检索、摘要与全文导入

搜索是项目无关的只读操作；普通关键词取交集，显式字段、布尔表达式和引号原样交给 arXiv。结果包含带版本的 `arxiv_id`、规范 `source_id`/`version`、标题、作者、摘要、首次发布/最近更新日期、分类、版本页与 PDF URL。相同查询在单个 API 进程内缓存 24 小时，响应中的 `cached` 说明是否命中；缓存不跨进程或重启，也不是永久文献索引。

```http
GET /api/research/literature/arxiv/search?query=ti%3A%22multi-agent%20reinforcement%20learning%22&max_results=5&sort_by=relevance
```

选择结果后，用明确的带版本 ID 导入：

```http
POST /api/research/projects/{project_id}/papers/import/arxiv
Content-Type: application/json

{"arxiv_id":"2401.00001v2"}
```

服务端使用 arXiv 官方建议的 `id_list=2401.00001v2` 重新查询并校验返回版本，不接受客户端回传标题或摘要。导入保存 PaperVersion 和 UTF-8 摘要快照，记录首次发布、最近更新、分类、版本 URL 与 Artifact SHA-256；重复导入返回同一版本和快照。摘要可通过 `research_read_page` 读取并建立 EvidenceSpan。

这里的 `content_scope=abstract` 很关键：搜索结果、摘要快照和 PDF URL 都不表示系统下载、解析或读过全文，也不表示主张得到科学验证。上游错误、非法 Atom、缺少明确版本或版本不一致分别返回结构化 `literature_*` 错误，失败不会创建摘要资料。官方接口语义见 [arXiv API User's Manual](https://github.com/arXiv/arxiv-docs/blob/develop/source/help/api/user-manual.md)。

需要全文时调用专用入口：

```http
POST /api/research/projects/{project_id}/papers/import/arxiv/full-text
Content-Type: application/json

{"arxiv_id":"2401.00001v2"}
```

服务端只构造官方 arXiv PDF URL，跟随重定向后再次校验来源；使用 `RESEARCH_MAX_ARTIFACT_BYTES` 同时检查声明长度和实际流式字节数，再检查 MIME、`%PDF-` 标识、加密状态、页数和 PyMuPDF 可解析性。校验完成前不创建 Artifact 或改变论文绑定。摘要已存在时，PDF 成为活动资料并把旧快照保留在 `abstract_artifact_id`；重复调用返回同一 PDF Artifact。`content_scope=full_text` 仅说明完整 PDF 文件已归档，`read_pages` 才表示实际返回过的物理页；扫描页仍需要外部人工/OCR 处理。

### 离线对话链路验收

```bash
python -m demos.research_tools_demo --output data/research-tools-demo
python -m pytest -q tests/test_research_literature.py tests/test_research_full_text.py tests/test_research_tools.py tests/test_research_foundation.py
```

固定 10/30 工程证据集单独运行：

```bash
python -m evals.research_evidence_acceptance --output evals/runs/research-evidence-v1
python -m pytest -q tests/test_research_evidence_acceptance.py
```

输出目录包含 `dataset.json`、10 个 `sources/paper-*.txt`、SQLite、10 个不可变 Artifact、`report.md` 和 `manifest.json`。数据集中的每句话都声明为合成夹具；不得把报告中的 30 条定位当作 RL 文献结论。

真实供应商非 PPO 任务单独运行：

```bash
python -m evals.research_model_acceptance --output evals/runs/research-model-dqn-v1
python -m pytest -q tests/test_research_model_acceptance.py
```

运行器拒绝 Stub 和覆盖已有目录，使用独立数据库、Artifact、沙箱和 Trace。它固定公开 `1312.5602v1` PDF 后让模型完成四个指定科研工具动作，再从数据库检查 page 1 覆盖、逐字定位、Evidence→Claim 关联和 `unverified` 状态。失败运行同样保留 `task.json`、`answer.md`、`report.md`、`trace.jsonl`、`research.db` 和 `manifest.json`；认证失败、缺少工具调用或空答案都会失败并以非零状态退出。manifest 不保存 API Key 或 Base URL。

使用新的输出目录，重复路径会拒绝覆盖。该样例通过 AgentRuntime 真正执行 7 次工具调用，依次从返回值取得项目、资料、论文版本和证据 ID。产物包括 `conversation.json`、`report.md`、`trace.jsonl`、`manifest.json`、`research.db`、来源和快照。manifest 保存报告 hash，以及 scripted_model、synthetic_sources、training_executed 等明确标记。

样例采用非 PPO 主题的合成 DQN 笔记与固定脚本模型；为保留全部 fixture 消息，样例单独设置 32 条上下文窗口。它验证工具、存储、证据和消息链路，不代表真实模型自主科研质量、长对话恢复能力或算法性能。实现参见 [工具适配层](../app/research/tools.py) 和 [对话样例](../demos/research_tools_demo.py)。
