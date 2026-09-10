/* ReAgent Web UI 前端逻辑 v4 */
"use strict";

const state = {
  sessionId: null,
  agentMode: "react",
  streaming: false,
  abortCtrl: null, // 当前 SSE 的 AbortController（用于停止）
  activeRun: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

/* ================= 初始化 ================= */
async function init() {
  const [capabilitiesLoaded] = await Promise.all([loadCapabilities(), loadSessions(), loadAgents()]);
  setConnStatus(Boolean(capabilitiesLoaded));
  bindEvents();
}

function setConnStatus(online) {
  $("#conn-status").className = "status-dot" + (online ? " online" : "");
  $("#conn-text").textContent = online ? "已连接" : "连接失败";
}

/* ================= 能力列表 ================= */
async function loadCapabilities() {
  try {
    const [tools, skills, mcp] = await Promise.all([
      fetch("/api/web/tools").then((r) => r.json()),
      fetch("/api/web/skills").then((r) => r.json()),
      fetch("/api/web/mcp").then((r) => r.json()),
    ]);
    renderList("#tool-list", tools.tools.map((t) => ({
      text: t.name + (t.risk_level !== "low" ? ` [${t.risk_level}]` : ""),
      title: t.description,
    })));
    $("#tool-count").textContent = tools.count;
    renderList("#skill-list", skills.skills.map((s) => ({
      text: s.name, title: s.description,
    })));
    $("#skill-count").textContent = skills.count;
    renderList("#mcp-list", mcp.servers.map((s) => ({
      text: `${s.name} (${s.tool_count})`, title: `transport: ${s.transport}`,
    })));
    $("#mcp-count").textContent = mcp.count;
    return true;
  } catch (e) {
    setConnStatus(false);
    return false;
  }
}

/* ================= 子 Agent 档案（动态注册） ================= */
async function loadAgents() {
  try {
    const data = await fetch("/api/web/agents").then((r) => r.json());
    const ul = $("#agent-list");
    ul.innerHTML = "";
    (data.agents || []).forEach((a) => {
      const li = document.createElement("li");
      li.className = "agent-item" + (a.builtin ? " builtin" : "");
      li.title = (a.description || "") + (a.allowed_tools ? `\n工具: ${a.allowed_tools.join(", ")}` : "\n工具: 全部");
      li.innerHTML = `
        <span class="si-icon">${a.builtin ? "📦" : "🧩"}</span>
        <span class="si-name">${esc(a.name)}</span>
        <span class="si-badge">${a.builtin ? "内置" : "自定义"}</span>
        ${a.builtin ? "" : `<span class="si-del" data-unregister="${esc(a.name)}" title="注销档案">✕</span>`}
      `;
      li.querySelector("[data-unregister]")?.addEventListener("click", (e) => {
        e.stopPropagation();
        unregisterAgent(a.name);
      });
      ul.appendChild(li);
    });
  } catch (e) { /* 忽略 */ }
}

async function unregisterAgent(name) {
  if (!confirm(`注销档案「${name}」？`)) return;
  try {
    const r = await fetch(`/api/web/agents/${encodeURIComponent(name)}`, { method: "DELETE" });
    if (!r.ok) {
      const err = await r.json().catch(() => ({}));
      throw new Error(err.detail || r.statusText);
    }
    loadAgents();
    addToolMsg({ tool: "unregister_agent", arguments: { name }, success: true, data: "已注销" });
  } catch (e) {
    addErrorMsg("注销失败: " + e.message);
  }
}

async function registerAgent() {
  const name = $("#agent-name").value.trim();
  const description = $("#agent-desc").value.trim();
  const system_prompt = $("#agent-prompt").value.trim();
  if (!name || !system_prompt) {
    showAgentError("档案名与系统提示必填");
    return;
  }
  const toolsRaw = $("#agent-tools").value.trim();
  const allowed_tools = toolsRaw
    ? toolsRaw.split(/[,，]/).map((s) => s.trim()).filter(Boolean)
    : null;
  try {
    const r = await fetch("/api/web/agents", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name, description, system_prompt, allowed_tools }),
    });
    const data = await r.json().catch(() => ({}));
    if (!r.ok) throw new Error(data.detail || r.statusText);
    $("#agent-form").style.display = "none";
    $("#agent-name").value = $("#agent-desc").value = $("#agent-prompt").value = $("#agent-tools").value = "";
    hideAgentError();
    loadAgents();
    addToolMsg({ tool: "register_agent", arguments: { name }, success: true, data: "注册成功，可立即用于编排" });
  } catch (e) {
    showAgentError("注册失败: " + e.message);
  }
}

function showAgentError(msg) { const el = $("#agent-error"); el.textContent = msg; el.style.display = "block"; }
function hideAgentError() { const el = $("#agent-error"); el.style.display = "none"; }

function renderList(sel, items) {
  const ul = $(sel);
  ul.innerHTML = "";
  items.forEach((item) => {
    const li = document.createElement("li");
    li.textContent = item.text;
    if (item.title) li.title = item.title;
    li.dataset.id = item.id || "";
    ul.appendChild(li);
  });
}

/* ================= 会话 ================= */
async function loadSessions() {
  try {
    const data = await fetch("/api/web/sessions").then((r) => r.json());
    const ul = $("#session-list");
    ul.innerHTML = "";
    (data.sessions || []).forEach((s) => {
      const li = document.createElement("li");
      li.className = "session-item";
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      li.dataset.sessionId = s.session_id;
      li.innerHTML = `
        <span class="si-icon">💬</span>
        <span class="si-name">${esc(s.session_id.slice(-16))}</span>
        <span class="si-time">${esc((s.updated_at || "").slice(11, 19))}</span>
        <button type="button" class="si-del" title="删除会话" aria-label="删除会话">✕</button>
      `;
      if (state.sessionId === s.session_id) li.classList.add("active");
      const activate = (e) => {
        if (e.target.classList.contains("si-del")) {
          e.stopPropagation();
          deleteSession(s.session_id);
          return;
        }
        openSession(s.session_id);
      };
      li.addEventListener("click", activate);
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openSession(s.session_id);
        }
      });
      ul.appendChild(li);
    });
  } catch (e) { /* 忽略 */ }
}

async function openSession(sessionId) {
  if (state.streaming) {
    setStatus("请先停止或等待当前执行完成。");
    return;
  }
  state.sessionId = sessionId;
  // 高亮
  $$("#session-list .session-item").forEach((el) => el.classList.toggle("active", el.dataset.sessionId === sessionId));
  // 加载消息
  try {
    const data = await fetch(`/api/web/sessions/${sessionId}/messages`).then((r) => r.json());
    renderHistory(data.messages || []);
    setStatus(`会话 ${sessionId.slice(-12)}`);
  } catch (e) {
    addErrorMsg("加载会话失败: " + e.message);
  }
  loadOrchestrations(sessionId);
}

/* ================= 编排记录（委派结果持久化） ================= */
async function loadOrchestrations(sessionId) {
  try {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    const data = await fetch(`/api/web/orchestrations${q}`).then((r) => r.json());
    const ul = $("#orch-list");
    ul.innerHTML = "";
    $("#orch-count").textContent = data.count || 0;
    (data.runs || []).forEach((run) => {
      const li = document.createElement("li");
      li.className = "orch-item";
      li.tabIndex = 0;
      li.setAttribute("role", "button");
      const icon = run.status === "SUCCEEDED" ? "✅" : run.status === "PARTIAL" ? "⚠️" : "❌";
      li.innerHTML = `
        <span class="si-icon">${icon}</span>
        <span class="si-name">${esc(run.task.slice(0, 18)) || "(无任务)"}</span>
        <span class="si-time">${run.depth > 1 ? `L${run.depth} · ` : ""}${esc(run.created_at.slice(11, 19))}</span>
      `;
      li.title = `${run.task}\n状态: ${run.status} · 子 Agent: ${run.agent_count} · ${(run.duration_ms / 1000).toFixed(1)}s`;
      li.dataset.runId = run.run_id;
      li.addEventListener("click", () => openOrchestrationDetail(run.run_id));
      li.addEventListener("keydown", (e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          openOrchestrationDetail(run.run_id);
        }
      });
      ul.appendChild(li);
    });
  } catch (e) { /* 忽略 */ }
}

async function openOrchestrationDetail(runId) {
  try {
    const r = await fetch(`/api/web/orchestrations/${encodeURIComponent(runId)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    renderOrchestrationPanel(data);
  } catch (e) {
    addErrorMsg("加载编排详情失败: " + e.message);
  }
}

function renderOrchestrationPanel(data) {
  const panel = document.createElement("div");
  panel.className = "workflow";
  panel.id = "orch-detail-" + data.run_id;

  const header = document.createElement("div");
  header.className = "workflow-header";
  header.setAttribute("role", "button");
  header.tabIndex = 0;
  const statusIcon = data.status === "SUCCEEDED" ? "✅" : data.status === "PARTIAL" ? "⚠️" : "❌";
  header.innerHTML = `
    <span class="wf-title">🌐 编排详情</span>
    <span class="wf-meta">${esc(data.run_id.slice(-12))} · ${statusIcon} ${esc(data.status)} · ${formatMs(data.duration_ms)}${data.depth > 1 ? ` · L${data.depth}` : ""}</span>
    <span class="wf-toggle">收起 ▴</span>
  `;
  const body = document.createElement("div");
  body.className = "workflow-body";
  body.innerHTML = renderOrchestrationBody(data);
  bindWorkflowToggle(panel, header);
  panel.appendChild(header);
  panel.appendChild(body);
  // 插入到消息流顶部（最新编排）
  const messages = $("#messages");
  messages.insertBefore(panel, messages.firstChild);
  panel.classList.add("open");
  bindOrchestrationToggles(panel);
}

function renderOrchestrationBody(data) {
  let html = "";
  // 任务
  html += `<div class="orch-task">📋 ${esc(data.task)}</div>`;

  // 计划（分工）
  if (data.plan && data.plan.steps && data.plan.steps.length) {
    html += `<div class="orch-section"><div class="tl-title">分工计划 ${data.plan.rationale ? `（${esc(data.plan.rationale)}）` : ""}</div>`;
    html += `<div class="plan-flow plan-flow-layers">`;
    const layers = buildPlanLayers(data.plan.steps);
    layers.forEach((layer, layerIndex) => {
      html += `<div class="pf-layer" aria-label="依赖层 ${layerIndex + 1}">`;
      layer.forEach((item) => {
        const result = data.agent_results && data.agent_results[item.index];
        const status = orchestrationStatus(result?.status);
        const deps = item.step.depends_on?.length ? `依赖步骤 ${item.step.depends_on.map((n) => n + 1).join("、")}` : "可并行";
        html += `<div class="pf-step ${status.cls}" title="${esc(deps)}">
          <span class="pf-icon">${status.icon}</span>
          <span class="pf-desc"><b>${esc(item.step.agent)}</b><small>${esc(deps)}</small></span>
        </div>`;
      });
      html += `</div>`;
      if (layerIndex < layers.length - 1) html += `<div class="pf-layer-arrow" aria-label="后续依赖阶段">↓</div>`;
    });
    html += `</div></div>`;
  }

  // 子 Agent 结果卡片
  if (data.agent_results && data.agent_results.length) {
    html += `<div class="orch-section"><div class="tl-title">子 Agent 结果（${data.agent_results.length}）</div>`;
    data.agent_results.forEach((ar, i) => {
      const icon = ar.status === "SUCCEEDED" ? "✅" : ar.status === "FAILED" ? "❌" : "⏭️";
      const tools = (ar.tool_calls || []).map((t) => t.name).join(", ") || "无工具调用";
      html += `
        <div class="subagent-card" data-idx="${i}">
          <div class="sa-header">
            <span class="sa-icon">${icon}</span>
            <span class="sa-name">${esc(ar.agent)}</span>
            <span class="sa-status ${ar.status === "SUCCEEDED" ? "ok" : "err"}">${esc(ar.status)}</span>
            <span class="sa-meta">${ar.steps} 步 · ${formatMs(ar.duration_ms)} · 🛠 ${esc(tools)}</span>
            <span class="ts-caret">▾</span>
          </div>
          <div class="sa-body">
            ${ar.error ? `<div class="ts-section"><div class="ts-label err-label">⚠️ 错误</div><pre class="ts-code err-code">${esc(ar.error)}</pre></div>` : ""}
            <div class="ts-section"><div class="ts-label">📤 回答</div>
              <div class="md-body">${renderMarkdown(ar.answer || "(无输出)")}</div>
            </div>
          </div>
        </div>`;
    });
    html += `</div>`;
  }

  // 嵌套子编排（多级）
  if (data.children && data.children.length) {
    html += `<div class="orch-section"><div class="tl-title">嵌套子编排（${data.children.length}）</div>`;
    data.children.forEach((c) => {
      const icon = c.status === "SUCCEEDED" ? "✅" : "⚠️";
      html += `<div class="orch-child" data-child="${esc(c.run_id)}">${icon} L${c.depth} · ${esc(c.task.slice(0, 40))}</div>`;
    });
    html += `</div>`;
  }

  // 最终答案
  if (data.final_answer) {
    html += `<div class="orch-section"><div class="tl-title">最终合成答案</div>`;
    html += `<div class="md-body">${renderMarkdown(data.final_answer)}</div></div>`;
  }

  // Trace 树
  if (data.trace && data.trace.spans && data.trace.spans.length) {
    const root = data.trace.spans;
    const totalDur = Math.max(root.reduce((a, n) => a + (n.duration_ms || 0), 0), 1);
    html += `<div class="orch-section"><div class="tl-title">Trace 树</div><div class="trace-tree">`;
    root.forEach((span) => { html += renderTraceNodeV2(span, 0, totalDur); });
    html += `</div></div>`;
  }
  return html;
}

function orchestrationStatus(status) {
  if (status === "SUCCEEDED") return { icon: "✓", cls: "ok" };
  if (status === "FAILED") return { icon: "✕", cls: "fail" };
  if (status === "SKIPPED") return { icon: "■", cls: "skip" };
  return { icon: "◌", cls: "running" };
}

function buildPlanLayers(steps) {
  const levels = new Map();
  steps.forEach((step, index) => {
    const dependencies = (step.depends_on || []).filter((dependency) => Number.isInteger(dependency) && dependency >= 0 && dependency < index);
    levels.set(index, dependencies.length ? Math.max(...dependencies.map((dependency) => levels.get(dependency) || 0)) + 1 : 0);
  });
  return Array.from({ length: Math.max(...levels.values()) + 1 }, (_, level) =>
    steps.map((step, index) => ({ step, index })).filter((item) => levels.get(item.index) === level)
  );
}

function bindOrchestrationToggles(panel) {
  // 子 agent 卡片展开
  panel.querySelectorAll(".subagent-card").forEach((card) => {
    const header = card.querySelector(".sa-header");
    header.setAttribute("role", "button");
    header.tabIndex = 0;
    const toggle = () => {
      card.classList.toggle("open");
      const open = card.classList.contains("open");
      card.querySelector(".ts-caret").textContent = open ? "▴" : "▾";
      header.setAttribute("aria-expanded", String(open));
    };
    header.setAttribute("aria-expanded", "false");
    header.addEventListener("click", toggle);
    header.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); toggle(); }
    });
  });
  // 嵌套子编排：点击加载详情
  panel.querySelectorAll(".orch-child").forEach((el) => {
    el.addEventListener("click", () => openOrchestrationDetail(el.dataset.child));
    el.tabIndex = 0;
    el.setAttribute("role", "button");
    el.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") { e.preventDefault(); openOrchestrationDetail(el.dataset.child); }
    });
  });
  // Trace 树折叠/详情
  bindTreeToggles(panel);
  bindToolStepToggles(panel);
}

function renderHistory(messages) {
  $("#messages").innerHTML = "";
  const pendingToolCalls = new Map();
  let historyRun = null;
  messages.forEach((m) => {
    if (m.role === "user") {
      addMessage("user", typeof m.content === "string" ? m.content : JSON.stringify(m.content));
    } else if (m.role === "assistant") {
      if (m.content === null || m.content === undefined || m.content === "") {
        if (m.tool_calls && m.tool_calls.length) {
          historyRun = addHistoryRunPanel(m.tool_calls.length);
          m.tool_calls.forEach((call) => {
            const id = call.id || call.tool_call_id;
            const fn = call.function || call;
            let argumentsValue = fn.arguments || call.arguments || {};
            if (typeof argumentsValue === "string") {
              try { argumentsValue = JSON.parse(argumentsValue); } catch (e) { argumentsValue = {}; }
            }
            if (id) pendingToolCalls.set(id, { id, name: fn.name || call.name || "tool", arguments: argumentsValue });
          });
        }
        return;
      }
      const content = typeof m.content === "string" ? m.content : JSON.stringify(m.content);
      addMessage("assistant", content);
    } else if (m.role === "tool") {
      const call = pendingToolCalls.get(m.tool_call_id) || {};
      const envelope = parseToolEnvelope(m.content);
      const card = {
        tool_call_id: m.tool_call_id || call.id,
        tool: m.name || call.name || "tool",
        arguments: call.arguments || {},
        success: envelope.success,
        status: envelope.success === false ? "failed" : "succeeded",
        error: envelope.error,
      };
      if (Object.prototype.hasOwnProperty.call(envelope, "data")) card.data = envelope.data;
      addToolMsg(card, historyRun?.querySelector("[data-history-tools]") || undefined);
    }
  });
  scrollToBottom();
}

function addHistoryRunPanel(toolCount) {
  const panel = document.createElement("section");
  panel.className = "workflow history-workflow open";
  panel.innerHTML = `
    <div class="workflow-header" role="button" tabindex="0" aria-expanded="true">
      <span class="wf-title">⌁ 历史执行</span>
      <span class="wf-meta">${toolCount} 个工具调用 · Trace 未记录</span>
      <span class="wf-toggle">收起 ▴</span>
    </div>
    <div class="workflow-body"><div class="run-summary">已根据保存的工具调用和结果还原；计划与 Trace 未记录。</div><div data-history-tools></div></div>`;
  bindWorkflowToggle(panel, panel.querySelector(".workflow-header"));
  $("#messages").appendChild(panel);
  return panel;
}

function parseToolEnvelope(content) {
  try {
    const parsed = typeof content === "string" ? JSON.parse(content) : content;
    if (parsed && typeof parsed === "object") return parsed;
  } catch (e) { /* retain raw content below */ }
  return { success: true, data: content };
}

function contentPreview(s) {
  try {
    const obj = JSON.parse(s);
    return (obj.data || obj.content || s).toString().slice(0, 200);
  } catch (e) {
    return String(s).slice(0, 200);
  }
}

function newSession() {
  if (state.streaming) {
    setStatus("请先停止或等待当前执行完成。");
    return;
  }
  state.sessionId = null;
  $("#messages").innerHTML = `
    <div class="welcome">
      <h2>🤖 ReAgent</h2>
      <p>ReAct / Plan 双模式 · 记忆 · MCP · 技能 · 多 Agent 编排</p>
      <p class="sub">工作流完全透明：每步决策、工具调用、耗时、Trace 树实时可见</p>
    </div>`;
  $$("#session-list .session-item").forEach((el) => el.classList.remove("active"));
  // 清空编排记录列表
  $("#orch-list").innerHTML = "";
  $("#orch-count").textContent = "0";
  setStatus("新会话");
}

/* ================= 消息渲染 ================= */
function addMessage(role, content, meta) {
  const div = document.createElement("div");
  div.className = "msg " + role;
  if (meta) {
    const metaEl = document.createElement("div");
    metaEl.className = "msg-meta";
    metaEl.textContent = meta;
    div.appendChild(metaEl);
  }
  const contentEl = document.createElement("div");
  if (role === "assistant") {
    contentEl.className = "md-body";
    contentEl.innerHTML = renderMarkdown(content);
  } else {
    contentEl.textContent = content;
  }
  div.appendChild(contentEl);
  if (role === "assistant") ensureCopyButton(div, content);
  $("#messages").appendChild(div);
  scrollToBottom();
  return div;
}

function normalizeToolStatus(data) {
  if (["queued", "running", "succeeded", "failed", "cancelled", "unknown"].includes(data.status)) {
    return data.status;
  }
  if (data.success === false || data.error) return "failed";
  if (Object.prototype.hasOwnProperty.call(data, "data")) return "succeeded";
  return "queued";
}

function toolStatusMeta(status) {
  return {
    queued: { icon: "⏳", cls: "pending", label: "等待执行" },
    running: { icon: "◌", cls: "running", label: "正在执行" },
    succeeded: { icon: "✓", cls: "ok", label: "已完成" },
    failed: { icon: "✕", cls: "err", label: "执行失败" },
    cancelled: { icon: "■", cls: "cancelled", label: "已取消" },
    unknown: { icon: "?", cls: "unknown", label: "状态未知" },
  }[status] || { icon: "?", cls: "unknown", label: "状态未知" };
}

function jsonText(value, fallback) {
  try { return JSON.stringify(value === undefined ? fallback : value, null, 2); } catch (e) { return String(value); }
}

function hasToolOutput(data) {
  return Object.prototype.hasOwnProperty.call(data, "data") && data.data !== undefined;
}

function renderToolCard(div, update) {
  const data = { ...(div._toolData || {}), ...update };
  data.status = normalizeToolStatus(data);
  div._toolData = data;
  div.dataset.toolCallId = data.tool_call_id || div.dataset.toolCallId || "";
  div.dataset.runId = data.run_id || div.dataset.runId || "";
  div.dataset.status = data.status;
  const status = toolStatusMeta(data.status);
  const dur = data.duration_ms != null ? ` · ⏱ ${formatMs(data.duration_ms)}` : "";
  const hasOutput = hasToolOutput(data);
  const output = hasOutput
    ? `<div class="ts-section"><div class="ts-label">📤 输出${data.truncated ? "（已截断）" : ""}</div>${toolOutputHtml(data.tool, data.data)}</div>`
    : "";
  const error = data.error
    ? `<div class="ts-section"><div class="ts-label err-label">⚠️ 错误</div><pre class="ts-code err-code">${esc(data.error.message || jsonText(data.error, {}))}</pre></div>`
    : "";
  const pending = !hasOutput && !data.error && ["queued", "running"].includes(data.status)
    ? `<div class="ts-section"><div class="ts-label">${data.status === "running" ? "◌ 正在执行..." : "⏳ 等待执行..."}</div></div>`
    : "";

  div.innerHTML = `
    <button type="button" class="ts-header" aria-expanded="false">
      <span class="ts-icon">${data.tool === "delegate" ? "🌐" : "🛠"}</span>
      <span class="ts-name">${esc(data.tool || "tool")}</span>
      <span class="ts-status ${status.cls}" title="${status.label}">${status.icon}</span>
      <span class="ts-dur">${dur}</span>
      <span class="ts-args-preview">${esc(jsonText(data.arguments, {}).slice(0, 50))}</span>
      <span class="ts-caret">▾</span>
    </button>
    <div class="ts-body">
      <div class="ts-section"><div class="ts-label">📋 参数</div><pre class="ts-code">${esc(jsonText(data.arguments, {}))}</pre></div>
      ${output || pending || `<div class="ts-section"><div class="ts-label">${status.label}</div></div>`}
      ${error}
      ${data.duration_ms != null ? `<div class="ts-meta">⏱ 耗时: ${formatMs(data.duration_ms)}</div>` : ""}
    </div>`;
  div.querySelector(".ts-header").addEventListener("click", () => {
    div.classList.toggle("open");
    const open = div.classList.contains("open");
    div.querySelector(".ts-header").setAttribute("aria-expanded", String(open));
    div.querySelector(".ts-caret").textContent = open ? "▴" : "▾";
  });
}

function addToolMsg(data, parent) {
  const div = document.createElement("div");
  div.className = "msg tool ts-card";
  renderToolCard(div, data);
  (parent || $("#messages")).appendChild(div);
  if (!parent) scrollToBottom();
  return div;
}

// delegate 等编排工具的输出：结构化渲染而非原始 JSON
function toolOutputHtml(tool, raw) {
  if (tool === "delegate") {
    const summary = parseDelegateOutput(raw);
    if (summary) {
      const icon = summary.status === "SUCCEEDED" ? "✅" : summary.status === "PARTIAL" ? "⚠️" : "❌";
      let html = `<div class="delegate-summary">
        <div class="ds-row"><span>${icon} 编排状态: <b>${esc(summary.status)}</b></span>
        <span>${summary.duration_ms != null ? `⏱ ${formatMs(summary.duration_ms)}` : ""}</span></div>`;
      if (summary.agents && summary.agents.length) {
        html += `<div class="ds-agents">${summary.agents.map((a) => `
          <span class="ds-agent ${a.status === "SUCCEEDED" ? "ok" : "err"}">${a.status === "SUCCEEDED" ? "✅" : "❌"} ${esc(a.agent)}</span>`).join("")}</div>`;
      }
      if (summary.final_answer) {
        html += `<div class="ds-answer">${esc(String(summary.final_answer).slice(0, 300))}</div>`;
      }
      html += `</div>`;
      return html;
    }
  }
  return `<pre class="ts-code">${esc(String(raw).slice(0, 300))}</pre>`;
}

// 解析 delegate 工具输出：可能是对象或 JSON 字符串
function parseDelegateOutput(raw) {
  try {
    let obj = raw;
    if (typeof raw === "string") {
      try { obj = JSON.parse(raw); } catch (e) { return null; }
    }
    // 工具信封（data 字段包装）
    if (obj && typeof obj === "object" && "data" in obj && !("status" in obj)) {
      const inner = obj.data;
      if (typeof inner === "string") { try { return JSON.parse(inner); } catch (e) { return null; } }
      return inner;
    }
    return obj && typeof obj === "object" && "agent_results" in obj ? obj : null;
  } catch (e) {
    return null;
  }
}

function updateToolCard(toolCallId, data, scope) {
  if (!toolCallId) return false;
  const cards = Array.from((scope || document).querySelectorAll(".ts-card[data-tool-call-id]"));
  const card = cards.find((item) => item.dataset.toolCallId === toolCallId);
  if (!card) return false;
  renderToolCard(card, { ...data, tool_call_id: toolCallId });
  return true;
}

function addErrorMsg(message) {
  addMessage("error", `⚠️ ${message}`);
}

function setStatus(text) {
  const el = $("#chat-status");
  if (el) el.textContent = text;
}

function scrollToBottom() {
  const msgs = $("#messages");
  msgs.scrollTop = msgs.scrollHeight;
}

function esc(s) {
  // 纯字符串 HTML 转义（不依赖 DOM，更健壮）
  return String(s == null ? "" : s)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

const SAFE_MARKDOWN_TAGS = new Set([
  "A", "BLOCKQUOTE", "BR", "CODE", "DEL", "EM", "H1", "H2", "H3", "H4", "H5", "H6",
  "HR", "LI", "OL", "P", "PRE", "STRONG", "TABLE", "TBODY", "TD", "TH", "THEAD", "TR", "UL",
]);

function renderMarkdown(content) {
  const source = String(content == null ? "" : content);
  if (!window.marked || typeof window.marked.parse !== "function") return esc(source);

  const template = document.createElement("template");
  template.innerHTML = window.marked.parse(source);
  template.content.querySelectorAll("*").forEach((node) => {
    if (!SAFE_MARKDOWN_TAGS.has(node.tagName)) {
      node.replaceWith(document.createTextNode(node.textContent || ""));
      return;
    }
    Array.from(node.attributes).forEach((attr) => {
      if (node.tagName === "A" && attr.name.toLowerCase() === "href") {
        try {
          const url = new URL(attr.value, window.location.origin);
          if (["http:", "https:", "mailto:"].includes(url.protocol)) {
            node.setAttribute("href", url.href);
            node.setAttribute("target", "_blank");
            node.setAttribute("rel", "noopener noreferrer");
            return;
          }
        } catch (e) { /* remove invalid links below */ }
      }
      node.removeAttribute(attr.name);
    });
  });
  return template.innerHTML;
}

/* ================= 工作流面板（Trace 树 v2） ================= */
const SPAN_ICONS = {
  gateway: "🚪", worker: "⚙️", agent: "🧠", llm: "💬", tool: "🛠",
  tool_gateway: "🛡️", context_builder: "📦", checkpoint: "💾", queue: "📨",
  memory: "🧠", eval: "📊", generic: "·",
};

function addWorkflowPanel(data, opts) {
  const panel = document.createElement("div");
  panel.className = "workflow";
  panel.id = "wf-" + (data.trace_id || Date.now());

  // Header
  const header = document.createElement("div");
  header.className = "workflow-header";
  header.setAttribute("role", "button");
  header.tabIndex = 0;
  const planInfo = data.plan && data.plan.length
    ? ` · 计划 ${data.plan.length} 步${data.plan_revisions ? ` · 重规划 ${data.plan_revisions}` : ""}`
    : "";
  header.innerHTML = `
    <span class="wf-title">🔍 Agent 工作流</span>
    <span class="wf-meta">${data.trace_id ? data.trace_id.slice(-12) : ""}${planInfo}</span>
    <span class="wf-toggle">收起 ▴</span>
  `;

  // Body
  const body = document.createElement("div");
  body.className = "workflow-body";
  body.innerHTML = renderWorkflowBody(data);
  bindWorkflowToggle(panel, header);
  panel.appendChild(header);
  panel.appendChild(body);
  $("#messages").appendChild(panel);
  panel.classList.add("open");
  scrollToBottom();
  // 绑定树节点折叠 + 工具卡片展开
  bindTreeToggles(panel);
  bindToolStepToggles(panel);
  return panel;
}

function bindWorkflowToggle(panel, header) {
  const toggle = () => {
    panel.classList.toggle("open");
    const open = panel.classList.contains("open");
    header.querySelector(".wf-toggle").textContent = open ? "收起 ▴" : "展开 ▾";
    header.setAttribute("aria-expanded", String(open));
  };
  header.setAttribute("aria-expanded", String(panel.classList.contains("open")));
  header.addEventListener("click", toggle);
  header.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      toggle();
    }
  });
}

function startLiveRun(run, data) {
  if (run.panel) return run.panel;
  const panel = document.createElement("section");
  panel.className = "workflow live-workflow open";
  panel.id = "wf-live-" + (data.run_id || Date.now());
  panel.dataset.runId = data.run_id || "";
  panel.innerHTML = `
    <div class="workflow-header" role="button" tabindex="0" aria-expanded="true">
      <span class="wf-title">◌ 本次执行</span>
      <span class="wf-meta">${esc(data.mode || state.agentMode)} · <span data-run-status>正在连接...</span></span>
      <span class="wf-toggle">收起 ▴</span>
    </div>
    <div class="workflow-body live-workflow-body">
      <div class="run-summary" data-run-summary>正在建立执行通道…</div>
      <div class="live-tool-list" data-live-tools></div>
    </div>`;
  bindWorkflowToggle(panel, panel.querySelector(".workflow-header"));
  const messages = $("#messages");
  messages.insertBefore(panel, run.assistantEl);
  run.panel = panel;
  run.toolResults = new Map();
  scrollToBottom();
  return panel;
}

function updateLiveRun(run, data, summary) {
  if (!run) return;
  const panel = startLiveRun(run, data);
  if (data.run_id) {
    run.runId = data.run_id;
    panel.dataset.runId = data.run_id;
  }
  const status = panel.querySelector("[data-run-status]");
  if (status) status.textContent = data.status === "running" ? "正在执行" : (data.status || "正在执行");
  const summaryEl = panel.querySelector("[data-run-summary]");
  if (summaryEl && summary) summaryEl.textContent = summary;
}

function finalizeRun(run, status, message, finalData) {
  if (!run) return;
  run.assistantEl?.classList.remove("streaming");
  const panel = run.panel;
  if (!panel) return;

  if (finalData) {
    const toolCalls = (finalData.tool_calls || []).map((call) => ({
      ...call,
      ...(run.toolResults?.get(call.tool_call_id || call.id) || {}),
    }));
    panel.className = "workflow open";
    panel.id = "wf-" + (finalData.trace_id || run.runId || Date.now());
    panel.innerHTML = `
      <div class="workflow-header" role="button" tabindex="0" aria-expanded="true">
        <span class="wf-title">🔍 本次执行</span>
        <span class="wf-meta">${finalData.trace_id ? esc(finalData.trace_id.slice(-12)) : ""} · 已完成</span>
        <span class="wf-toggle">收起 ▴</span>
      </div>
      <div class="workflow-body">${renderWorkflowBody({ ...finalData, tool_calls: toolCalls })}</div>`;
    bindWorkflowToggle(panel, panel.querySelector(".workflow-header"));
    bindTreeToggles(panel);
    bindToolStepToggles(panel);
    return;
  }

  panel.classList.toggle("failed", status === "failed");
  const statusEl = panel.querySelector("[data-run-status]");
  if (statusEl) statusEl.textContent = status === "cancelled" ? "已停止" : "执行失败";
  const summaryEl = panel.querySelector("[data-run-summary]");
  if (summaryEl) summaryEl.textContent = message || (status === "cancelled" ? "已停止接收本次执行结果。" : "本次执行未能完成。");
  const incomplete = status === "cancelled" ? "cancelled" : "unknown";
  panel.querySelectorAll(".ts-card").forEach((card) => {
    if (["queued", "running"].includes(card.dataset.status)) {
      renderToolCard(card, { status: incomplete, error: status === "failed" ? { message: message || "执行中断" } : null });
    }
  });
}

function bindToolStepToggles(panel) {
  panel.querySelectorAll(".ts-card").forEach((card) => {
    const header = card.querySelector(".ts-header");
    header.addEventListener("click", () => {
      card.classList.toggle("open");
      const open = card.classList.contains("open");
      header.setAttribute("aria-expanded", String(open));
      const caret = card.querySelector(".ts-caret");
      caret.textContent = open ? "▴" : "▾";
    });
  });
}

function renderWorkflowBody(data) {
  let html = "";

  // 1) Plan 步骤（横向流程）
  if (data.plan && data.plan.length) {
    html += `<div class="plan-flow">`;
    data.plan.forEach((s, i) => {
      const icon = s.status === "SUCCEEDED" ? "✅" : s.status === "FAILED" ? "❌" : s.status === "SKIPPED" ? "⏭️" : "⏳";
      const cls = s.status === "SUCCEEDED" ? "ok" : s.status === "FAILED" ? "fail" : "skip";
      html += `<div class="pf-step ${cls}">
        <span class="pf-icon">${icon}</span>
        <span class="pf-desc">${esc(s.description)}</span>
        ${s.result ? `<span class="pf-result">${esc(s.result.slice(0, 40))}</span>` : ""}
      </div>`;
      if (i < data.plan.length - 1) html += `<span class="pf-arrow">→</span>`;
    });
    html += `</div>`;
  }

  // 2) Trace 树（含耗时条）
  if (data.trace && data.trace.spans && data.trace.spans.length) {
    const root = data.trace.spans;
    const totalDur = Math.max(root.reduce((a, n) => a + (n.duration_ms || 0), 0), 1);
    html += `<div class="trace-tree">`;
    root.forEach((span) => { html += renderTraceNodeV2(span, 0, totalDur); });
    html += `</div>`;
  } else {
    html += `<div class="trace-tree"><div class="tn-row"><span class="tn-name">(Tracing 未启用)</span></div></div>`;
  }

  // 3) 工具调用步骤卡片（可点击展开详情）
  if (data.tool_calls && data.tool_calls.length) {
    html += `<div class="tool-steps"><div class="tl-title">工具调用流程（${data.tool_calls.length} 步）</div>`;
    data.tool_calls.forEach((tc, i) => {
      html += renderToolStepCard(tc, i);
    });
    html += `</div>`;
  }

  return html;
}

function renderToolStepCard(tc, index) {
  const dur = tc.duration_ms != null ? formatMs(tc.duration_ms) : "";
  const status = toolStatusMeta(normalizeToolStatus({
    ...tc,
    status: tc.status === "ERROR" ? "failed" : tc.status,
  }));
  const argsPreview = jsonText(tc.arguments, {}).slice(0, 60);
  const dataStr = hasToolOutput(tc) ? jsonText(tc.data, null).slice(0, 120) : "";
  const errStr = tc.error ? (tc.error.message || JSON.stringify(tc.error)).slice(0, 120) : "";
  return `
    <div class="ts-card" data-index="${index}">
      <button type="button" class="ts-header" aria-expanded="false">
        <span class="ts-num">${index + 1}</span>
        <span class="ts-icon">🛠</span>
        <span class="ts-name">${esc(tc.name)}</span>
        <span class="ts-status ${status.cls}" title="${status.label}">${status.icon}</span>
        ${dur ? `<span class="ts-dur">⏱ ${dur}</span>` : ""}
        <span class="ts-args-preview">${esc(argsPreview)}</span>
        <span class="ts-caret">▾</span>
      </button>
      <div class="ts-body">
        <div class="ts-section">
          <div class="ts-label">📋 参数</div>
          <pre class="ts-code">${esc(JSON.stringify(tc.arguments || {}, null, 2))}</pre>
        </div>
        ${hasToolOutput(tc) ? `
        <div class="ts-section">
          <div class="ts-label">📤 输出</div>
          <pre class="ts-code">${esc(dataStr)}${String(tc.data).length > 120 ? "\n..." : ""}</pre>
        </div>` : ""}
        ${errStr ? `
        <div class="ts-section">
          <div class="ts-label err-label">⚠️ 错误</div>
          <pre class="ts-code err-code">${esc(errStr)}</pre>
        </div>` : ""}
        ${dur ? `
        <div class="ts-meta">⏱ 耗时: ${dur}</div>` : ""}
      </div>
    </div>`;
}

function formatMs(ms) {
  if (ms == null) return "";
  if (ms >= 1000) return (ms / 1000).toFixed(1) + "s";
  return Math.round(ms) + "ms";
}

function renderTraceNodeV2(span, depth, totalDur) {
  const isErr = span.status === "ERROR";
  const cls = isErr ? "err" : "ok";
  const icon = SPAN_ICONS[span.span_type] || SPAN_ICONS.generic;
  const dur = span.duration_ms || 0;
  const pct = Math.max((dur / totalDur) * 100, 0.5);
  const hasChildren = span.children && span.children.length;
  // 详情是否有内容（决定是否可展开）
  const hasDetails = span.error || span.input !== undefined || span.output !== undefined
    || (span.attributes && Object.keys(span.attributes).length);

  let html = `<div class="trace-node">`;
  // 行：子树和详情分别由可键盘操作的按钮控制。
  html += `<div class="tn-row ${isErr ? "is-err" : ""}" style="padding-left:${depth * 14}px">`;
  html += hasChildren
    ? `<button type="button" class="tn-caret open" data-caret aria-label="折叠子步骤" aria-expanded="true">▾</button>`
    : `<span class="tn-caret-placeholder"></span>`;
  html += `<span class="tn-icon">${icon}</span>`;
  html += `<span class="tn-name">${esc(span.name)}</span>`;
  if (span.attributes && span.attributes.tool_name) {
    html += `<span class="tn-tool">${esc(span.attributes.tool_name)}</span>`;
  }
  html += `<span class="tn-bar-wrap"><span class="tn-bar" style="width:${Math.min(pct * 3, 60)}px"></span></span>`;
  html += `<span class="tn-dur">${dur.toFixed(dur >= 100 ? 0 : 1)}ms</span>`;
  html += `<span class="tn-status ${cls}">${isErr ? "❌" : "✓"}</span>`;
  // 详情展开指示（有详情才显示）
  html += hasDetails ? `<button type="button" class="tn-expand" data-expand aria-expanded="false">详情 ▾</button>` : "";
  html += `</div>`;

  // 详情面板（点击行展开）
  if (hasDetails) {
    html += `<div class="tn-detail" data-detail style="margin-left:${depth * 14 + 30}px">`;
    html += renderSpanDetail(span);
    html += `</div>`;
  }

  // 错误详情（保留在行内，展开面板里也有）
  if (span.error) {
    html += `<div class="tn-err" style="margin-left:${depth * 14 + 30}px">${esc(span.error.type || "Error")}: ${esc((span.error.message || "").slice(0, 150))}</div>`;
  }
  // 子节点
  if (hasChildren) {
    html += `<div class="tn-children" data-children>`;
    span.children.forEach((c) => { html += renderTraceNodeV2(c, depth + 1, totalDur); });
    html += `</div>`;
  }
  html += `</div>`;
  return html;
}

function renderSpanDetail(span) {
  let html = "";
  // 元信息
  html += `<div class="tn-detail-meta">`;
  if (span.span_id) html += `<span class="ts-meta">id: ${esc(span.span_id.slice(-12))}</span>`;
  if (span.start_time) html += `<span class="ts-meta">start: ${esc(span.start_time.slice(11, 19))}</span>`;
  if (span.end_time) html += `<span class="ts-meta">end: ${esc(span.end_time.slice(11, 19))}</span>`;
  if (span.duration_ms != null) html += `<span class="ts-meta">耗时: ${formatMs(span.duration_ms)}</span>`;
  html += `</div>`;

  // attributes（如 model / tokens / tool_name）
  if (span.attributes && Object.keys(span.attributes).length) {
    html += `<div class="ts-section"><div class="ts-label">🏷 属性</div>`;
    html += `<pre class="ts-code">${esc(JSON.stringify(span.attributes, null, 2))}</pre></div>`;
  }
  // input
  if (span.input !== undefined && span.input !== null) {
    html += `<div class="ts-section"><div class="ts-label">📥 输入</div>`;
    html += `<pre class="ts-code">${esc(JSON.stringify(span.input, null, 2).slice(0, 600))}</pre></div>`;
  }
  // output
  if (span.output !== undefined && span.output !== null) {
    html += `<div class="ts-section"><div class="ts-label">📤 输出</div>`;
    html += `<pre class="ts-code">${esc(JSON.stringify(span.output, null, 2).slice(0, 600))}</pre></div>`;
  }
  // error
  if (span.error) {
    html += `<div class="ts-section"><div class="ts-label err-label">⚠️ 错误</div>`;
    html += `<pre class="ts-code err-code">${esc(JSON.stringify(span.error, null, 2))}</pre></div>`;
  }
  return html;
}

function bindTreeToggles(panel) {
  // caret：折叠子节点
  panel.querySelectorAll("[data-caret]").forEach((caret) => {
    caret.addEventListener("click", (e) => {
      e.stopPropagation();
      const node = caret.closest(".trace-node");
      const children = node?.querySelector(":scope > .tn-children");
      if (!children) return;
      const open = caret.classList.contains("open");
      caret.classList.toggle("open", !open);
      children.style.display = open ? "none" : "";
      caret.setAttribute("aria-expanded", String(!open));
      caret.setAttribute("aria-label", open ? "展开子步骤" : "折叠子步骤");
    });
  });
  panel.querySelectorAll("[data-expand]").forEach((expand) => {
    expand.addEventListener("click", (e) => {
      e.stopPropagation();
      const node = expand.closest(".trace-node");
      const detail = node?.querySelector(":scope > .tn-detail");
      if (!detail) return;
      const isOpen = detail.classList.contains("open");
      detail.classList.toggle("open", !isOpen);
      expand.textContent = isOpen ? "详情 ▾" : "详情 ▴";
      expand.setAttribute("aria-expanded", String(!isOpen));
    });
  });
}

/* ================= 发送 ================= */
async function send() {
  const input = $("#input");
  const message = input.value.trim();
  if (!message || state.streaming) return;

  $("#messages .welcome")?.remove();
  addMessage("user", message);
  input.value = "";
  setStreaming(true);

  const assistantEl = addMessage("assistant", "");
  assistantEl.classList.add("streaming");
  const activeRun = { assistantEl, contentEl: assistantEl.querySelector(".md-body"), panel: null, runId: null, toolResults: new Map() };
  state.activeRun = activeRun;
  startLiveRun(activeRun, { mode: state.agentMode, status: "connecting" });

  // 停止按钮
  state.abortCtrl = new AbortController();
  $("#stop").style.display = "block";

  try {
    const resp = await fetch("/api/web/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: state.sessionId, agent_mode: state.agentMode }),
      signal: state.abortCtrl.signal,
    });
    if (!resp.ok) throw new Error(await responseError(resp));
    const contentType = resp.headers.get("content-type") || "";
    if (!contentType.includes("text/event-stream")) throw new Error("服务没有返回执行事件流。");
    if (!resp.body) throw new Error("服务未返回可读取的执行事件流。");
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalData = null;
    let streamError = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleFrame(frame, activeRun, (data) => { finalData = data; }, (error) => { streamError = error; });
        if (streamError) throw streamError;
      }
    }
    if (buffer.trim()) handleFrame(buffer, activeRun, (data) => { finalData = data; }, (error) => { streamError = error; });
    if (streamError) throw streamError;
    if (!finalData) throw new Error("执行连接意外结束，未收到完成状态。");
    state.sessionId = finalData.session_id;
    setAssistantContent(assistantEl, finalData.answer || activeRun.finalAnswer || "");
    finalizeRun(activeRun, "succeeded", "", finalData);
  } catch (e) {
    if (e.name === "AbortError") {
      setAssistantContent(assistantEl, "⏹ 已停止生成。", false);
      finalizeRun(activeRun, "cancelled", "已停止接收本次执行结果。");
    } else {
      setAssistantContent(assistantEl, "⚠️ 请求失败: " + e.message, false);
      finalizeRun(activeRun, "failed", e.message);
    }
  } finally {
    $("#stop").style.display = "none";
    setStreaming(false);
    state.activeRun = null;
    loadSessions();
    // 编排记录可能新增（delegate 工具）
    if (state.sessionId) loadOrchestrations(state.sessionId);
  }
}

async function responseError(resp) {
  const payload = await resp.json().catch(() => null);
  const detail = payload?.detail;
  if (typeof detail === "string") return detail;
  if (detail?.message) return detail.message;
  return `服务响应 HTTP ${resp.status}`;
}

function setAssistantContent(assistantEl, content, copyable = true) {
  const text = String(content == null ? "" : content);
  const contentEl = assistantEl.querySelector(".md-body");
  if (contentEl) contentEl.innerHTML = renderMarkdown(text);
  ensureCopyButton(assistantEl, copyable ? text : "");
}

function ensureCopyButton(assistantEl, content) {
  assistantEl.dataset.copyText = String(content || "");
  let copyBtn = assistantEl.querySelector(".copy-btn");
  if (!copyBtn) {
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.type = "button";
    copyBtn.addEventListener("click", async () => {
      try {
        await navigator.clipboard.writeText(assistantEl.dataset.copyText || "");
        copyBtn.textContent = "已复制 ✓";
        setTimeout(() => (copyBtn.textContent = "复制"), 1500);
      } catch (e) {
        copyBtn.textContent = "复制失败";
        setTimeout(() => (copyBtn.textContent = "复制"), 1500);
      }
    });
    actions.appendChild(copyBtn);
    assistantEl.appendChild(actions);
  }
  copyBtn.textContent = "复制";
  copyBtn.disabled = !assistantEl.dataset.copyText;
}

function handleFrame(frame, run, onComplete, onError) {
  const lines = frame.split("\n");
  const eventLine = lines.find((l) => l.startsWith("event:"));
  const dataLine = lines.find((l) => l.startsWith("data:"));
  if (!eventLine || !dataLine) return;
  const event = eventLine.slice(6).trim();
  let data;
  try { data = JSON.parse(dataLine.slice(5).trim()); } catch (e) { return; }

  switch (event) {
    case "run_started":
      updateLiveRun(run, data, "正在等待 Agent 的第一项决策…");
      break;
    case "step":
      updateLiveRun(run, data, data.tool_calls?.length
        ? `第 ${data.step} 步：准备调用 ${data.tool_calls.map((call) => call.name).join("、")}`
        : `第 ${data.step} 步：正在整理最终回答…`);
      if (data.tool_calls && data.tool_calls.length) {
        data.tool_calls.forEach((tc) => {
          addToolMsg({
            run_id: data.run_id,
            tool_call_id: tc.tool_call_id || tc.id,
            tool: tc.name,
            arguments: tc.arguments,
            status: tc.status || "queued",
          }, run.panel?.querySelector("[data-live-tools]"));
        });
      }
      break;
    case "tool_result":
      run.toolResults?.set(data.tool_call_id, data);
      updateLiveRun(run, data, `第 ${data.step} 步：${data.tool} ${data.status === "succeeded" ? "已完成" : "未完成"}`);
      if (!updateToolCard(data.tool_call_id, data, run.panel)) {
        addToolMsg(data, run.panel?.querySelector("[data-live-tools]"));
      }
      break;
    case "final":
      run.finalAnswer = data.content || "";
      setAssistantContent(run.assistantEl, run.finalAnswer);
      break;
    case "done":
      onComplete(data);
      break;
    case "error":
      onError(new Error(data.message || "执行失败"));
      break;
  }
}

function setStreaming(v) {
  state.streaming = v;
  $("#send").disabled = v;
  if (!v) state.abortCtrl = null;
}

function stopStreaming() {
  if (state.abortCtrl) {
    updateLiveRun(state.activeRun, { status: "running" }, "正在停止本次执行…");
    state.abortCtrl.abort();
    $("#stop").style.display = "none";
  }
}

function setSidebarOpen(open) {
  const sidebar = $("#sidebar");
  const toggle = $("#sidebar-toggle");
  if (!sidebar || !toggle) return;
  sidebar.classList.toggle("is-open", open);
  document.body.classList.toggle("sidebar-open", open);
  toggle.setAttribute("aria-expanded", String(open));
}

/* ================= 事件绑定 ================= */
function bindEvents() {
  const input = $("#input");
  const sendBtn = $("#send");
  const newBtn = $("#new-session");
  const stopBtn = $("#stop");
  const uploadBtn = $("#upload-btn");
  const fileInput = $("#file-input");
  const sidebarToggle = $("#sidebar-toggle");
  const sidebarScrim = $("#sidebar-scrim");

  sendBtn.addEventListener("click", send);
  if (stopBtn) stopBtn.addEventListener("click", stopStreaming);
  input.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); send(); }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  });
  if (newBtn) newBtn.addEventListener("click", newSession);
  if (sidebarToggle) sidebarToggle.addEventListener("click", () => setSidebarOpen(!$("#sidebar")?.classList.contains("is-open")));
  if (sidebarScrim) sidebarScrim.addEventListener("click", () => setSidebarOpen(false));
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("#sidebar")?.classList.contains("is-open")) {
      setSidebarOpen(false);
      sidebarToggle?.focus();
    }
  });

  // 上传文件
  if (uploadBtn && fileInput) {
    uploadBtn.addEventListener("click", () => fileInput.click());
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      if (!file) return;
      const fd = new FormData();
      fd.append("file", file);
      try {
        const r = await fetch("/api/web/upload", { method: "POST", body: fd });
        const data = await r.json();
        addToolMsg({ tool: "upload", arguments: { file: file.name }, success: true, data: data.hint });
        loadFiles();
      } catch (e) {
        addErrorMsg("上传失败: " + e.message);
      }
      fileInput.value = "";
    });
  }

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (state.streaming) {
        setStatus("请在当前执行结束后切换模式。");
        return;
      }
      document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.agentMode = btn.dataset.mode;
      $("#mode-badge").textContent = btn.dataset.mode === "react" ? "ReAct" : "Plan";
    });
  });

  // 档案注册表单
  const addAgentBtn = $("#add-agent-btn");
  if (addAgentBtn) {
    addAgentBtn.addEventListener("click", () => {
      const form = $("#agent-form");
      form.style.display = form.style.display === "none" ? "block" : "none";
      if (form.style.display === "block") $("#agent-name").focus();
    });
  }
  $("#agent-save")?.addEventListener("click", registerAgent);
  $("#agent-cancel")?.addEventListener("click", () => {
    $("#agent-form").style.display = "none";
    hideAgentError();
  });
}

/* ================= 文件列表 ================= */
async function loadFiles() {
  try {
    const data = await fetch("/api/web/files").then((r) => r.json());
    renderList("#file-list", (data.files || []).map((f) => ({
      text: `${f.name} (${(f.size / 1024).toFixed(1)}KB)`,
      title: f.name,
    })));
  } catch (e) { /* 忽略 */ }
}

/* ================= 会话删除 ================= */
async function deleteSession(sessionId) {
  if (!confirm(`删除会话 ${sessionId.slice(-12)}？`)) return;
  try {
    await fetch(`/api/web/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    if (state.sessionId === sessionId) newSession();
    loadSessions();
    if (state.sessionId) loadOrchestrations(state.sessionId);
  } catch (e) {
    addErrorMsg("删除失败: " + e.message);
  }
}

init();
loadFiles();
