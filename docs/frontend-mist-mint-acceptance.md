# Mist Mint × Developer Studio 验收记录

日期：2026-09-11  
实现分支：`codex/mist-mint-developer-studio`  
范围：仅 `app/static/` 前端呈现与测试；未修改 Web API、Runtime、持久化模型或用户数据。

## 已交付

- 雾白薄荷 light / 深绿灰 dark 语义 token、统一焦点与 reduced-motion 基础。
- Developer Studio 四区布局：全局顶栏、Rail、上下文侧栏、Chat/Resource workspace、两级 Inspector。
- Chat/Composer/工具卡/Workflow 摘要：发送、停止、复制、上传和工具跳转仍使用原有真实路径。
- Inspector 的 Timeline、Trace、Agents、Context 复用真实事件、执行记录和 Trace 接口；不会生成伪造依赖边。
- Tools、Skills、MCP、Agents、Files、Traces 与本地 Settings 资源视图；文件明确为全局沙箱。
- 本地会话别名、置顶、Recent/Pinned/All 筛选及 `Ctrl/Cmd+K` 已加载内容搜索。别名/置顶不会跨浏览器同步，搜索不读取历史消息或请求服务端全库。

## 验证结果

| 验证项 | 结果 |
| --- | --- |
| 初始完整回归 | `282 passed, 1 warning`（改动前基线） |
| 最终完整回归 | `299 passed, 5 third-party warnings in 132.13s` |
| 前端/API 聚焦回归 | `40 passed in 18.79s`：`tests/test_web.py tests/test_frontend_events.py` |
| JavaScript 语法 | `node --check app/static/app.js` 通过 |
| 补丁空白检查 | `git diff --check` 通过；仅 Windows CRLF 归一化警告 |
| 浏览器 | 本地 Stub 服务：完成 calculator 任务、Trace/Context、资源页、会话分组和 `Ctrl+K` 搜索均已核对；390px 下 `scrollWidth=clientWidth=390`，Composer 仍在视口内 |

## 明确边界

- Evals 保持禁用，因为 Web 未提供评测结果列表或启动接口。
- Files 没有假装成任务附件、下载、预览或删除能力；只展示全局沙箱列表与原有上传入口。
- 无全局 Trace 搜索、服务端会话重命名/置顶、模型选择、成本账单或 MCP 连接管理接口，因此前端未伪造这些能力。
- 浏览器验证使用本机 127.0.0.1 Stub 服务；未调用真实模型、未执行外部写操作，也未验证实体移动设备键盘。
