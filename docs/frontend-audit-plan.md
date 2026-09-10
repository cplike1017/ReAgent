# ReAgent 前端体验审查与改进计划

日期：2026-09-11（本机检查跨越 9 月 10 日午夜）
状态：**审查完成，改进待执行**。本轮未修改应用前端、后端或环境配置。

## 1. 结论与适用范围

当前界面的问题不只是颜色偏暗：**执行状态不准确、长会话内容被挤没、并行关系表达错误**，会直接影响用户判断 Agent 是否正常工作。建议先修复这些问题，再实施“暖白／薄荷底色＋深青绿主色＋天蓝点缀”的视觉改造。

值得保留：ReAct/Plan 模式入口、可展开的工具参数与结果、Trace 父子关系、独立的编排记录。无需为了换配色而整体重写框架。

- 检查对象：本地 `894140bc0bc90e2967263e42db2a74c26f15f497`，静态资源版本标记 v4；不是已确认同步的远端最新版。上轮 Git 拉取失败，此次没有重试或修改源码版本。
- 浏览器：本机有界面 Chromium；视口 1440×1000、1024×768、390×844。
- 8000：查看现有服务的首页、视觉和响应式布局；未读取既有文件内容或操作既有会话。
- 8001：临时隔离实例，使用同一套静态资源和后端，Stub 模型、独立数据库／Trace／沙箱，禁用真实天气、MCP 连接和记忆；用于安全的实际执行检查。
- 未检验：真实模型响应质量、长时外部 MCP 调用、真实邮件、真实移动设备软键盘、生产并发压力；不得把本报告当作这些能力已验证。
- 证据标记：**R**＝真实本地界面／隔离后端执行；**F**＝浏览器受控事件／响应复现；**C**＝代码证据或待验证风险；**D**＝设计判断／建议，不是功能缺陷。

## 2. 证据索引

截图保留当前实现，没有注入新主题。E08 为便于观察，将已存储的编排详情单独展示；数据来自真实隔离后端，不是虚构 DAG。

| 编号 | 证据与场景 | 类型 |
|---|---|---|
| E01 | [桌面首页](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/01-desktop-home.png)：1440×1000 | R |
| E02 | [窄屏首页](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/02-mobile-home.png)：390×844，输入框约 25px | R |
| E03 | [ReAct 计算完成](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/03-react-complete.png)：`计算 123 * 456` | R |
| E04 | [重新打开会话](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/04-session-replay.png)：工作流消失，参数丢失 | R |
| E05 | [同名工具调用](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/05-repeat-tool.png)：`同时查询北京和上海天气` | R |
| E06 | [工具失败](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/06-tool-error.png)：`计算 1 / 0`，红叉但详情仍等待 | R |
| E07 | [长会话与 Plan](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/07-plan-complete.png)：连续操作后工作流被压扁 | R |
| E08 | [并行编排详情](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/08-parallel-orchestration.png)：两个无依赖 Agent 被串行连线 | R |
| E09 | [等待中的工具](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/09-controlled-pending.png)：只有 step，没有结果 | F |
| E10 | [停止后的界面](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/10-controlled-stopped.png)：仍有流式光标和等待工具 | F |
| E11 | [HTTP 503](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/11-controlled-http503.png)：错误没有呈现 | F |
| E12 | [中等视口](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/12-tablet-home.png)：1024×768 | R |

可复用的受控诊断步骤：[controlled-states.js](D:/CodeSource/harness/agent-runtime/output/playwright/frontend-audit/controlled-states.js)。它只在专用浏览器中截获聊天响应，最后解除拦截并刷新；不是应用代码或已建立的回归测试套件。HTML 检查只设置本页 DOM 标记，不读取密钥、不向外网发送内容。

## 3. 问题清单

优先级：P0＝扩展使用范围前必须消除的安全风险；P1＝会误导用户或阻断主要操作；P2＝视觉、效率与体验优化。下面的“验收”是未来标准，不表示已经修复。

### F01 · P1 · 长会话把工作流压成细线【R+C】

- 现象：完成计算、两城市天气、除零、Plan 查询后，3 个 `.workflow` 实测高度均约 **1.14px**；工具卡片约 21px。E07 可以看到内容变成细条。
- 原因：`.messages` 是纵向 flex，工作流／工具卡片保留 `flex-shrink:1`，同时设置 `overflow:hidden`，空间不足时内容被裁掉。
- 改进：消息项、工作流容器禁止主轴收缩；滚动交给消息主区。限定长日志的局部滚动，不把整个运行详情挤扁。
- 验收：连续 20 轮或插入 100 条模拟步骤后，所有卡片标题、展开入口仍可见；仅滚动区增长，无 1px 面板、无不可达内容。

### F02 · P1 · 同名工具结果串位【R+C】

- 现象：北京、上海两个 `get_weather` 执行结束，北京卡片仍“等待执行”，结果依次覆盖最后一张上海卡片。E05；后端确实返回了两个结果。
- 原因：`updateToolCard` 倒序按工具名找卡片；SSE 的 step/tool_result 没传工具调用 ID，done 里的耗时也按遍历顺序配对。
- 改进：后端透传稳定的 `tool_call_id`，结合 `run_id/turn_id`、步骤与尝试 ID 做精确匹配；禁止把名称当实例身份。重复/乱序事件须幂等处理。
- 验收：同轮两个同名工具、跨轮重用名称、重试、嵌套委派和乱序结果均回填正确；前一张卡片不残留等待状态。

### F03 · P1 · 状态与错误详情互相矛盾【R+F+C】

- 现象：等待工具时已经显示绿色成功勾（E09）；除零失败显示红叉，但展开仍是“等待执行”，没有卡片错误详情（E06）。聊天回答能说明除零，不等于卡片正确。
- 原因：step 分支写死 `success:true`；结果更新被 `if(data.data)` 包住；假值／空结果与未完成混为一谈。历史回放还写死 `success:true`。
- 改进：显式区分 queued/running/succeeded/failed/cancelled/skipped；更新状态、错误、输出分别处理，使用字段存在性而非真假值判断。
- 验收：成功的 `0`、`false`、`""`、`null` 各有明确输出表示；失败即使无 data 也展示错误原因；历史不会将失败改为成功。

### F04 · P1 · Trace 子树折叠无效【R+C】

- 现象：点击根节点的 ▾ 后，12 个子节点仍显示、箭头不变。
- 原因：`bindTreeToggles` 只认紧邻的 `.tn-children`，但真实 DOM 中节点行后先插入了 `.tn-detail`。
- 改进：通过当前 `.trace-node` 的直接子元素定位子树，避免跨层误选；详情开关和子树开关分开管理。
- 验收：有／无详情、有／无错误、三层嵌套均可独立折叠，且不会折叠错误层级；支持键盘操作。

### F05 · P1 · 运行中缺少真正的执行视图【F+C】

- 现象：慢速 step 已到达但工作流数量仍为 0，右上运行状态为空，只见答案光标和工具卡。最终工作流在流结束后才创建。当前“Trace 树实时可见”的首页文案超出实际能力。
- 原因：`send()` 在完成后调用 `addWorkflowPanel`；现有事件只有 step/tool_result/final/done/error，没有计划产生、Agent 开始／结束等事件；普通模型文本也不是 token 级流式输出。
- 改进：发送即创建“本次执行”卡；等待模型、工具执行、计划变更、子 Agent 活动逐步更新。先提供诚实的阶段进度，不要求同时完成模型 token 流式改造。
- 验收：发送后立即可见运行卡；慢模型期间显示“等待模型响应＋已用时”；未生成计划时不显示虚假百分比；有计划后显示已完成/总步骤；done 只收尾同一张卡，不再新增重复流程。

### F06 · P1 · 并行 Agent 被画成串行依赖【R+C】

- 现象：真实编排中的 researcher、analyst 都是 `depends_on:[]`，页面仍显示 `researcher → analyst`（E08）。
- 原因：`renderOrchestrationBody` 无条件按数组顺序插入箭头；计划节点还全部使用 `.pf-step.ok`，没有依据运行结果。
- 改进：依据 `depends_on` 计算拓扑层，同层并列，只有真实依赖才画连线；节点状态来自对应 Agent 运行实例。先用分层卡片／小型 SVG 即可，不必引入大型画布库。
- 验收：独立双节点无相互箭头；A→B→C、A/B→C、跳过节点、失败分支、嵌套编排分别正确；“执行顺序”与“依赖关系”不能混用。

### F07 · P1 · 停止／异常结束没有统一收尾【F+C】

- 现象：停止后文本说已停止，但 `.msg.streaming` 仍存在，光标继续闪，工具仍绿色等待（E10）。受控 HTTP 503 JSON 被当成 SSE 读取，结果只剩复制按钮，没有错误提示（E11）。
- 原因：AbortError 分支不清理 streaming 类与子任务；`send()` 不检查 `resp.ok`、Content-Type 或是否收到 done；连接标签与单次运行失败没有分离。
- 改进：建立统一 finalize 路径；区分用户停止、网络中断、服务拒绝、异常 EOF。已完成部分保留，未完成部分标记 cancelled/unknown；不把“停止浏览器接收”等同于所有外部工具已停止。
- 验收：取消后所有相关动画停止、发送恢复；4xx/5xx、JSON错误、非SSE、断流无done均有明确提示与手动重试；不会显示空成功答案；错误不必把健康后端误标为离线。

### F08 · P1 · 历史回放丢失执行证据【R+C】

- 现象：刚完成的计算有 Trace 和耗时，点击同一会话后 `.workflow` 变为 0，工具参数显示 `{}`（E04）。
- 原因：`renderHistory` 只消费消息；跳过 assistant tool_calls，用空参数重建工具，未加载回合执行摘要。
- 改进：历史和实时共用运行视图模型；以 turn/run 为单位保存或检索摘要、调用 ID、结果、Trace 引用。独立“编排记录”保留，但不能代替普通回合历史。
- 验收：刷新／切换会话后的参数、状态、用时、计划和 Trace 与实时一致；旧数据无法还原时明确“历史未记录”，不能伪造成功或空参数。

### F09 · P1 · 窄屏布局不可用【R+C】

- 现象：390px 视口保留 280px 侧栏，输入框实测约 25px，欢迎文字竖排和裁切，发送文字换行（E02）。1024px 的输入框约 628px，可输入，但侧栏仍抢占空间（E12）。
- 原因：固定 sidebar 宽度、无响应式断点、100vh+页面 overflow hidden。
- 改进：小屏侧栏改抽屉；中屏可收起；输入区采用可收缩布局，检查 `min-width:0`、`100dvh` 回退和安全区。长表格／Trace 仅在自身区域横向滚动。
- 验收：390/768/1024/1440/1920px 和 200% 缩放可发消息、切会话、开详情；无页面级横向溢出，移动端补真实软键盘验证。

### F10 · P2 · 结构化卡片过高，长短反差异常【R+C】

- 现象：单条折叠工具卡约 105px；展开后少量参数占很大空白（E03/E09），多轮后又被压成细条。
- 原因：`.msg {white-space:pre-wrap}` 传给模板中的空白文本和结构化卡片；与 F01 的 flex 收缩叠加。
- 改进：卡片和 Markdown 容器恢复 normal，仅用户纯文本、代码／日志保留必要的换行；标题行与详情区有独立排版规格。
- 验收：折叠标题目标约 40–48px；两行 JSON 不产生大空洞；Markdown 段落/列表/代码换行仍正确。

### F11 · P1 · 实时回答的复制按钮复制空字符串【R+C】

- 现象：天气答案可见，点击对应复制按钮后，专用浏览器剪贴板读回空字符串。
- 原因：`addMessage('assistant','')` 先建立绑定初始空内容的按钮；`ensureCopyButton` 发现按钮已存在便直接返回。
- 改进：复制读取当前回合最终文本或更新按钮数据源；空答案禁用复制，失败时展示提示。
- 验收：实时完成、历史回放、多段 Markdown 的复制内容均与答案源文本一致；不意外覆盖为空。

### F12 · P0 · Markdown 内容可执行 HTML 事件处理器【F+C】

- 现象：浏览器截获的 assistant Markdown 仅含本地测试图片和无害 DOM 标记，图片错误事件被执行。未使用真实模型或外部地址。
- 原因：多个入口直接 `marked.parse(...) → innerHTML`，没有清洗边界；包括实时回答、历史、子 Agent 答案与最终合成内容。
- 改进：集中定义安全 Markdown 渲染；使用受维护的 HTML 清洗方案／严格允许列表，限制链接协议和远程资源；保持工具输出的转义。CSP 可补充，但不能代替清洗。
- 验收：脚本、事件属性、危险URL不执行；正常代码块／表格／链接保留；所有渲染入口通过同一恶意样例测试。远程部署／接入真实不可信检索内容前完成。

### F13 · P2 · 信息组织更像日志控制台【R+D】

- E03 同一调用出现于独立工具卡、Trace 和“工具调用流程”，答案先于工具卡；用户没有统一的“本次执行”范围。Trace 默认展开 checkpoint/context 等内部噪声，10–12px 小字和多重滚动增加阅读成本。
- 首页中央只列能力词，没有任务范例；发消息后欢迎块仍在，挤占内容。侧栏把 21 个工具放在会话之前，会话标题为截断 ID，难找历史。
- 改进：任务级执行摘要在先、最终答案在后；默认展示用户关心的 Agent／工具步骤，内部 Trace 放“调试详情”；会话优先、能力分组收起，欢迎页给 3 个明确样例；首条消息后移除欢迎块。将界面里的 React 改为 ReAct，避免与前端框架混淆。
- 验收：用户能在一屏回答“谁在做、做到哪、是否失败、结果在哪”；不用读原始 JSON 才能判断状态；老 Trace 仍可完整访问。

### F14 · P2 · 深蓝面积过大，缺少清新感与视觉主次【R+D】

- E01 主背景 `#0F172A`、侧栏 `#1E293B`、回答 `#334155` 都属于低明度蓝灰；主体主要依靠亮青和亮蓝制造差别。暗色本身不是错误，但不符合本次“清新有活力”的目标。
- 次级面板、状态和操作重用 accent；白色原生滚动条在深色背景上很突兀；emoji、等宽字体、小胶囊和密集边框缺少一致规范。
- 改进：使用第 5 节语义 token 统一重配色，白／薄荷层次为主，深青绿用于行动，少量蓝色用于运行；不是只替换 `--bg`。整理硬编码的 error/code 背景、hover RGBA、深色文字和滚动条。
- 验收：浅色主题覆盖聊天、工具、Trace、编排、表单与所有状态，不残留深蓝孤岛；品牌色与成功色不混淆，状态也有文字／图标。

### F15 · P1/P2 · 键盘可达性不足【R+C】

- Trace 箭头实测 tabIndex=-1、无 role；可点击的 div/span 和历史 li 没有完整键盘语义；部分微按钮只有 20px；停止按钮的可访问名称是 `■` 而非动作名称。
- 改进：真正的按钮／列表控件、`aria-expanded`、清晰可访问名称、统一 focus-visible；状态采用克制的 aria-live 汇总，不播报每个 token。
- 验收：只用 Tab/Enter/Space/Escape 能完成发送、停止、切会话、展开/折叠；抽屉关闭后焦点回原处；动态图标支持减少动画偏好。功能不可达作为 P1，小字／点击面积等视觉规范并入 P2。

### F16 · 待补验证的相关风险【C】

以下不能写成“已全面实测”，作为实现时的测试条目：

- 每次新增内容强制 `scrollToBottom()`，阅读旧记录可能被拉回底部；需要“用户接近底部时自动跟随＋新内容提示”。
- 运行中仍允许新建／切换会话和模式，异步回调可能落到另一会话；绑定 run/turn 与目标容器，禁止依赖全局当前会话兜底。共享 runtime/settings 的并发隔离需单独核查。
- `init()` 在能力读取失败后仍可能执行 `setConnStatus(true)`；应区分 API 健康、当前运行错误、MCP 部分不可用。
- Trace 耗时条没有共享起点，宽度又被截顶，不能用于判断真实重叠／关键路径；要么标注“耗时占比”，要么按时间区间实现真正时间轴。
- 输出多处截断为 120/200/300/600 字符，且后端对工具结果 `str(... )[:300]` 会破坏 delegate JSON；“Trace 未启用”还混淆了无数据／读取失败。先定义结构化摘要和截断标识，受控加载详情，**不为显示完整而关闭脱敏或默认采集全部提示词**。
- 中文输入法 Enter 没检查 `isComposing`；需要真实 IME 测试，避免选词即发送。favicon 404 为低优先级收尾，不影响本次 Agent 执行。

## 4. 目标执行体验（后续实现规格）

### 4.1 一个回合一张运行卡，结果与调试分层

桌面：左侧会话导航，中间对话，运行详情按需打开右侧面板；1024px 优先单列内联详情，窄屏用抽屉／全宽详情。不要一开始就固定三列挤压聊天。

每个回合包含：

1. 用户任务。
2. 本次执行卡：模式、状态文字、已用时、当前 Agent/工具、取消入口。
3. 用户可理解的步骤列表；有委派时显示 Agent 拓扑分层和依赖。
4. 完成后的摘要和最终答案，保留复制／产物入口。
5. 折叠的“Trace 调试”：原始树、属性、错误、时间信息，允许筛选内部节点。

不默认展示隐含思维链；“执行解释”只使用任务计划、工具事件和模型主动提供的公开摘要，不把内部推理内容当必需数据。

### 4.2 先打通事件，再谈“实时可视化”

后续约定的建议字段：`event_id/seq`、`run_id`、`turn_id`、`parent_run_id`、`step_id`、`tool_call_id`、`attempt_id`、`timestamp`、`status`、`payload`。

- 基础事件：run_started、plan_created/updated、agent_started/finished、tool_started/finished、run_finished/failed/cancelled。命名可沿用现有风格，关键是身份和生命周期完整。
- 工具结果包含结构化摘要、错误、耗时与截断标识。Trace 的 OK/ERROR 是完成后的 span 结果，不能直接当作运行中完整状态枚举。
- 总步骤未知时显示阶段和已用时；固定计划才显示已完成 N/M，重规划重新说明分母，不能硬编码 10%→90% 假进度。
- `done` 与实时事件由同一视图模型归并；最终数据校正状态和统计，但不复制出第二张步骤清单。
- 历史记录复用同一模型；缺少旧数据时明确降级。新增事件须有兼容策略，避免新前端配旧后端直接失效。
- SSE 身份透传与 Agent 生命周期事件会涉及后端，不能只改 CSS/JS 就宣称完成多 Agent 实时追踪。

## 5. 清新配色与视觉规范提案

建议采用“薄荷白工作台”：大面积近白、少量薄荷底；深墨色文本保证阅读；青绿主按钮带来活力，蓝色仅点缀运行状态。保留暗色可作为后续选项，不要求第一版同时维护两套全新主题。

### 5.1 可执行 token 草案

| 语义 token | 建议值 | 用途 |
|---|---|---|
| `canvas` | `#F5FAF8` | 页面底色，带极轻薄荷倾向 |
| `surface` | `#FFFFFF` | 对话、运行卡、详情 |
| `surface-muted` | `#EDF6F2` | 侧栏、辅助区域 |
| `border-subtle` | `#D9E7E1` | 装饰分隔，不单独承担控件识别 |
| `text-primary` | `#182D36` | 正文、标题 |
| `text-secondary` | `#536873` | 时间、说明、元信息 |
| `primary` / `primary-hover` | `#0F766E` / `#115E59` | 发送、新建等主动作；按钮文字白色 |
| `primary-soft` | `#E7F6F1` | 用户消息／选中态，使用深青文字 |
| `running-fg` / `running-bg` | `#1D4ED8` / `#EFF6FF` | 正在运行，配旋转图标与文字 |
| `success-fg` / `success-bg` | `#15803D` / `#F0FDF4` | 已完成，不能用于尚未执行 |
| `warning-fg` / `warning-bg` | `#B45309` / `#FFFBEB` | 待确认／部分成功 |
| `error-fg` / `error-bg` | `#B91C1C` / `#FEF2F2` | 失败及重试提醒 |
| `neutral-fg` / `neutral-bg` | `#536873` / `#F1F5F4` | 排队、跳过、取消（文字必须不同） |
| `accent-decoration` | `#38BDF8` | 小面积装饰，不当作白底小字颜色 |
| `focus-ring` | `#1D4ED8` | 键盘焦点描边，保留 offset |

按 sRGB 公式计算的不透明色对：白字/primary **5.47:1**；primary/primary-soft **4.91:1**；secondary/白 **5.84:1**；正文/canvas **13.57:1**；running **6.16:1**、success **4.79:1**、warning **4.84:1**、error **5.91:1**。这些只是列出的色对，不替代实现后的 hover、透明叠层、链接和禁用态检查。

现有 `#94A3B8` 叠 `#334155` 为 4.04:1，仅说明这个组合不适合普通小字，不意味着整个暗色界面都不达标。主背景与侧栏分离度约 1.22:1 是层级观感问题，不能误用文字对比度规范来判定。

普通文本至少 4.5:1，大号文本至少 3:1，依据 [W3C 文字对比度说明](https://www.w3.org/WAI/WCAG22/Understanding/contrast-minimum.html)。需要依靠边界识别的控件另做非文本对比检查，不能把 `border-subtle` 当所有控件唯一边界。

### 5.2 排版、图标与动效

- 正文 15–16px、辅助文字 12–13px；10px 不承担关键状态。代码才使用等宽字体，普通 Agent/工具标题使用一致 UI 字体。
- 4/8px 间距体系；卡片内部 12–16px，主要区域 20–24px；圆角约 12–16px，轻微边框／阴影，不堆叠大面积渐变和玻璃效果。
- 图标统一尺寸与笔画，逐步替换工具区混杂 emoji；品牌插画/图标可以保留少量亲和感。
- 状态用“文字＋图标＋色彩”，用户一眼能区分进行中、成功、失败、取消、跳过。
- 动画只反馈真正状态变化；150–200ms 的展开／hover 即可，尊重减少动画偏好，不让完成／取消状态继续闪烁。
- 交互目标本项目建议 40–44px；至少满足 [W3C 目标尺寸要求及例外](https://www.w3.org/WAI/WCAG22/Understanding/target-size-minimum.html)。24px 是该 AA 条目的基础值，44px 是这里的舒适度目标。
- 所有操作有可见焦点，参照 [W3C Focus Visible](https://www.w3.org/WAI/WCAG22/Understanding/focus-visible.html)。

## 6. 实施顺序与任务拆分

以下清单全部待执行；每项先增加复现测试，再修改，再用对应证据场景复查。不需要先迁移 React、引入状态库或大型 DAG 编辑器。

| 顺序 | 任务与边界 | 覆盖问题 | 依赖与完成条件 |
|---|---|---|---|
| T0 | 核对实施时版本，保存本轮证据；若拉远端先检查工作区，重新定位函数 | 基线 | 不覆盖现有 requirements 改动和配置；未同步不冒充最新版 |
| T1 | 安全 Markdown 渲染入口收敛与清洗 | F12 | 所有回答入口的安全样例通过，普通 Markdown 保持可用 |
| T2 | 修复 flex 收缩、卡片空白、树折叠与复制 | F01/F04/F10/F11 | 20轮场景不裁切、树可操作、复制文本一致；不等新事件协议即可实施 |
| T3 | 运行／工具事件身份与状态收尾；HTTP/SSE错误处理 | F02/F03/F07 | 后端携带调用身份，前端精确归并；假值、取消、异常EOF、4xx/5xx均正确 |
| T4 | 任务级实时运行卡、真实依赖关系、历史执行摘要 | F05/F06/F08/F13 | 依赖T3；实时／历史同源，独立Agent不串连，Trace作为调试详情 |
| T5 | 薄荷白主题、导航层级、响应式、可访问交互 | F09/F13/F14/F15 | 依赖T2结构稳定；按token草案实施，各视口与键盘路径通过 |
| T6 | 全场景回归与交付文档 | 全部及F16 | 自动测试、同场景前后截图、异常与长会话验收、记录剩余限制 |

T1/T2/T5 中纯 token 设计可以独立准备，但合入应保持小改动可回退；不要把全部 JS/CSS 重写打包成一个无法定位回归的大提交。本轮没有估算确定工期，T4 的历史存储和事件协议范围需先确认。

### 6.1 修改位置导航（当前基线行号）

| 文件入口 | 后续重点 |
|---|---|
| [index.html](D:/CodeSource/harness/agent-runtime/app/static/index.html:1) | 侧栏与会话导航、欢迎页、composer、运行面板挂载点、控件语义 |
| [style.css](D:/CodeSource/harness/agent-runtime/app/static/style.css:3) | token；`.messages`约316行；`.msg`约328行；`.workflow`约355行；详情与composer、媒体查询 |
| [app.js](D:/CodeSource/harness/agent-runtime/app/static/app.js:247) | 编排247、历史337、消息382、工具419/506、workflow574、Trace708/791、send821、copy894、SSE911、事件绑定961 |
| [Web API](D:/CodeSource/harness/agent-runtime/app/api/web.py:101) | `_tool_calls_with_duration`按序匹配、`web_chat_stream`事件、结果序列化、历史／编排接口 |
| [Agent runtime](D:/CodeSource/harness/agent-runtime/app/agent/runtime.py:196) | lifecycle hooks、turn身份、Plan生命周期桥接；审慎核查并发共享状态 |
| [Plan loop](D:/CodeSource/harness/agent-runtime/app/agent/plan_loop.py:1) | 计划产生、步骤开始／结束／重规划通知 |
| [Orchestrator runner](D:/CodeSource/harness/agent-runtime/app/orchestrator/runner.py:182) | `depends_on`/并行组，Agent与嵌套运行生命周期 |
| [Orchestrator executor](D:/CodeSource/harness/agent-runtime/app/orchestrator/executor.py:67) | 子Agent执行与工具事件关联 |
| [Trace model](D:/CodeSource/harness/agent-runtime/app/tracing/models.py:1) | 完成span与运行态区别；需要稳定的关联字段 |
| [Trace recorder](D:/CodeSource/harness/agent-runtime/app/tracing/recorder.py:69) | 保留脱敏和内容省略策略，按需提供安全摘要 |

## 7. 验收矩阵与执行交接

| 测试场景 | 必须观察到的结果 |
|---|---|
| 首次访问、第一条消息 | API/能力加载状态真实；欢迎块不占用已开始的对话；主要任务入口清楚 |
| ReAct单工具＋同名双工具 | 每个调用身份稳定、参数／结果对应、无重复成功／等待卡 |
| 慢模型／长工具／Plan重规划 | 运行卡及时出现；阶段与用时真实；计划更新不抹掉之前发生的事件 |
| 零值／空值／工具失败 | 合法假值不是等待；错误有原因、保留已完成部分 |
| 取消／中断／无done／HTTP错误 | 统一停止动画和输入禁用；不留下假成功；可明确手动重试 |
| 串行／并行／汇聚／嵌套Agent | 连线与depends_on一致，状态与对应运行一致；局部失败可定位 |
| Trace有详情／无详情／错误节点 | 正确折叠、键盘可达、不跨层选错子树 |
| 刷新、历史、运行中切换会话 | 已保存执行证据一致，不出现另一会话的回答／状态 |
| 20轮／100步骤／长JSON与表格 | 卡片不收缩消失，详情可达；浏览旧内容不强制跳底 |
| 桌面／中屏／窄屏／200%缩放 | 可输入、可停止、可打开详情；无页面级横向溢出 |
| 复制／中文IME／键盘遍历 | 复制最终文本；候选词确认不误发；无键盘死路 |
| 恶意Markdown＋普通Markdown | 不执行脚本／事件，不允许危险URL；正常格式不回退成一团纯文本 |
| 颜色／焦点／状态图标 | 实际组合对比度合格，状态不是只靠颜色区分 |

执行建议：保留现有 Python 后端测试；另建立可重复的浏览器回归场景与测试 fixture。上轮的 258 个 Python 测试通过不等于前端这些问题不存在，本轮也未重跑该套件。基准截图与修复后截图必须使用相同尺寸、相同假数据、相同折叠状态。

后续交付至少包含：修复后的浏览器用例、接口事件示例、主题 token、响应式对照截图、历史兼容说明、仍未覆盖的真实模型／外部工具限制。不得仅凭首页 HTTP 200 宣称“执行可视化已修复”。

**推荐先执行 T1–T3，随后 T4–T5；本文件是执行依据，不代表授权本轮立即修改。**
