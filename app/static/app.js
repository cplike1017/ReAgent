/* ReAgent Web UI 前端逻辑 v27 */
"use strict";

const state = {
  sessionId: null,
  agentMode: "react",
  streaming: false,
  abortCtrl: null, // 当前 SSE 的 AbortController（用于停止）
  executionId: null,
  executionQueuedAt: null,
  executionStartedAt: null,
  executionFinishedAt: null,
  executionTimer: null,
  executionSteps: 0,
  executionTools: 0,
  modelUsageFacts: new Map(),
  toolFacts: new Map(),
  executionEventIds: new Set(),
  lastExecutionSeq: 0,
  executionHistory: [],
  followLatest: true,
  timelineFilter: "all",
  executionViewVersion: 0,
  inspectorReturnFocus: null,
  currentPlanVersion: null,
  planRevisions: 0,
  planSnapshots: new Map(),
  planRevisionReasons: new Map(),
  orchestrationRuns: new Map(),
  executionContext: null,
  executionTrace: null,
  executionTraceId: null,
  sessions: [],
  sessionFilter: "recent",
  sessionMetadata: {},
  sessionContentCache: new Map(),
  activeAssistantElement: null,
  searchReturnFocus: null,
  resources: {
    tools: [],
    skills: [],
    mcp: [],
    agents: [],
    files: [],
  },
};

const uiState = {
  primaryView: "chat",
  inspectorSection: "execution",
  inspectorTab: "timeline",
  compactCallView: "graph",
};

const PRIMARY_VIEW_COPY = Object.freeze({
  chat: ["Sessions", "在当前会话中发起任务并观察真实执行过程。"],
  agents: ["Agents", "查看当前运行时已提供的子 Agent 档案。"],
  tools: ["Tools", "查看当前运行时已提供的工具与技能。"],
  mcp: ["MCP", "查看当前已连接的 MCP Server。"],
  traces: ["Traces", "查看当前会话的执行记录与编排历史。"],
  files: ["Files", "查看全局沙箱文件；文件不会自动成为任务附件。"],
  settings: ["Settings", "仅调整前端阅读偏好，不修改模型、密钥或运行时配置。"],
});

const SESSION_METADATA_STORAGE_KEY = "reagent-session-metadata-v1";
const PANE_WIDTH_STORAGE_KEY = "reagent-pane-widths-v1";
const DEFAULT_INSPECTOR_TAB_STORAGE_KEY = "reagent-default-inspector-tab";
const PANE_WIDTH_CONFIG = Object.freeze({
  sidebar: { property: "--sidebar-width", element: "#primary-navigation", resizer: "#sidebar-resizer", min: 200, max: 340 },
  inspector: { property: "--inspector-width", element: "#execution-panel", resizer: "#inspector-resizer", min: 320, max: 560 },
});

const TIMELINE_FILTERS = new Set(["all", "active", "success", "attention"]);
const TERMINAL_EXECUTION_OUTCOMES = Object.freeze({
  SUCCEEDED: { uiStatus: "success", stage: "任务已完成，取消请求未生效" },
  FAILED: { uiStatus: "error", stage: "任务执行失败，取消请求未生效" },
  CANCELLED: { uiStatus: "cancelled", stage: "服务端已确认停止" },
  INTERRUPTED: { uiStatus: "error", stage: "执行被服务重启中断" },
});
const INSPECTOR_FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "textarea:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "summary",
  "[tabindex]:not([tabindex='-1'])",
].join(",");

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

function uiIconMarkup(name, className = "ui-icon") {
  return `<svg class="${className}" aria-hidden="true"><use href="#icon-${name}"></use></svg>`;
}

function setPrimaryView(view) {
  if (!Object.prototype.hasOwnProperty.call(PRIMARY_VIEW_COPY, view)) return;
  uiState.primaryView = view;
  document.body.dataset.primaryView = view;
  $$('[data-primary-view]').forEach((button) => {
    const active = button.dataset.primaryView === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-current", active ? "page" : "false");
  });
  $$('[data-context-view]').forEach((element) => {
    const visible = String(element.dataset.contextView || "").split(/\s+/).includes(view);
    element.hidden = !visible;
  });
  const chatView = $("#chat-view");
  const resourceView = $("#resource-workspace");
  if (chatView) chatView.hidden = view !== "chat";
  if (resourceView) resourceView.hidden = view === "chat";
  const [title, description] = PRIMARY_VIEW_COPY[view];
  const contextTitle = $("#context-sidebar-title");
  const resourceTitle = $("#resource-workspace-title");
  const resourceDescription = $("#resource-workspace-description");
  if (contextTitle) contextTitle.textContent = title;
  if (resourceTitle) resourceTitle.textContent = title;
  if (resourceDescription) resourceDescription.textContent = description;
  renderResourceWorkspace();
}

function setLocalFeedback(selector, kind, message) {
  const element = $(selector);
  if (!element) return;
  element.hidden = !message;
  element.className = "local-feedback" + (kind ? " " + kind : "");
  element.textContent = message || "";
}

function setResourceFeedback(kind, message) {
  setLocalFeedback("#resource-feedback", kind, message);
}

function setSessionFeedback(kind, message) {
  setLocalFeedback("#session-feedback", kind, message);
}

function createResourceCard(title, description, meta, onActivate) {
  const card = document.createElement(onActivate ? "button" : "article");
  card.className = "resource-card";
  if (onActivate) {
    card.type = "button";
    card.addEventListener("click", onActivate);
  }
  const titleEl = document.createElement("strong");
  titleEl.textContent = title;
  const descriptionEl = document.createElement("p");
  descriptionEl.textContent = description || "暂无补充说明";
  const metaEl = document.createElement("span");
  metaEl.className = "resource-card-meta";
  metaEl.textContent = meta;
  card.append(titleEl, descriptionEl, metaEl);
  return card;
}

function appendResourceSection(container, title, items, emptyCopy) {
  const section = document.createElement("section");
  section.className = "resource-section";
  const heading = document.createElement("div");
  heading.className = "resource-section-heading";
  const headingTitle = document.createElement("h3");
  headingTitle.textContent = title;
  const count = document.createElement("span");
  count.textContent = String(items.length);
  heading.append(headingTitle, count);
  section.appendChild(heading);
  if (!items.length) {
    const empty = document.createElement("p");
    empty.className = "resource-empty";
    empty.textContent = emptyCopy;
    section.appendChild(empty);
  } else {
    const cards = document.createElement("div");
    cards.className = "resource-card-grid";
    items.forEach((item) => cards.appendChild(item));
    section.appendChild(cards);
  }
  container.appendChild(section);
}

function renderResourceWorkspace() {
  const summary = $("#resource-summary");
  const content = $("#resource-content");
  if (!summary || !content || uiState.primaryView === "chat") return;
  content.replaceChildren();
  const view = uiState.primaryView;
  const resources = state.resources;

  if (view === "tools") {
    summary.textContent = String(resources.tools.length) + " 个工具 · " + String(resources.skills.length) + " 项技能";
    appendResourceSection(content, "Tools", resources.tools.map((tool) => createResourceCard(
      tool.name,
      tool.description,
      (tool.risk_level || "low") + " 风险 · " + (tool.required_permission || "无需额外权限"),
    )), "当前运行时未提供工具。");
    appendResourceSection(content, "Skills", resources.skills.map((skill) => createResourceCard(
      skill.name,
      skill.description,
      "v" + (skill.version || "unknown") + (Array.isArray(skill.triggers) && skill.triggers.length ? " · " + skill.triggers.join("、") : " · 无触发词"),
    )), "当前运行时未加载技能。");
    return;
  }

  if (view === "mcp") {
    summary.textContent = String(resources.mcp.length) + " 个已连接 Server";
    appendResourceSection(content, "MCP Servers", resources.mcp.map((server) => createResourceCard(
      server.name,
      "当前连接可用；工具由 Server 在运行时提供。",
      (server.transport || "unknown") + " · " + String(server.tool_count || 0) + " 个工具",
    )), "当前没有已连接的 MCP Server。");
    return;
  }

  if (view === "agents") {
    summary.textContent = String(resources.agents.length) + " 个可用 Agent 档案";
    appendResourceSection(content, "Agent Profiles", resources.agents.map((agent) => createResourceCard(
      agent.name,
      agent.description,
      (agent.builtin ? "内置" : "自定义") + " · 最多 " + String(agent.max_steps || 0) + " 步 · " + (Array.isArray(agent.allowed_tools) ? agent.allowed_tools.join("、") || "无工具" : "全部工具"),
    )), "当前运行时未启用子 Agent 档案。");
    return;
  }

  if (view === "files") {
    summary.textContent = String(resources.files.length) + " 个全局沙箱文件";
    const actions = document.createElement("div");
    actions.className = "resource-workspace-actions";
    const upload = document.createElement("button");
    upload.type = "button";
    upload.className = "resource-primary-action";
    upload.dataset.resourceAction = "upload";
    upload.textContent = "上传文件";
    actions.appendChild(upload);
    content.appendChild(actions);
    appendResourceSection(content, "Sandbox Files", resources.files.map((file) => createResourceCard(
      file.name,
      "文件位于全局沙箱；发送任务时不会自动附加。",
      formatFileSize(file.size),
    )), "全局沙箱目前没有文件。");
    return;
  }

  if (view === "traces") {
    const records = state.executionHistory;
    summary.textContent = String(records.length) + " 条当前会话执行记录";
    appendResourceSection(content, "Execution history", records.map((record) => createResourceCard(
      record.input_preview || record.execution_id,
      "选择后将在 Inspector 回放该次执行的真实 Timeline、Trace 与 Context。",
      formatMode(record.agent_mode) + " · " + formatStoredStatus(record.status) + (record.created_at ? " · " + formatStoredTime(record.created_at) : ""),
      () => {
        if (!canOpenExecutionHistory(record.execution_id)) return;
        setPrimaryView("chat");
        setInspectorSection("execution");
        setInspectorTab("trace");
        setInspectorCollapsed(false, true);
        void openExecutionHistory(record.execution_id);
      },
    )), "当前会话还没有可回放的执行记录。");
    return;
  }

  if (view === "settings") {
    const theme = document.body.dataset.theme === "dark" ? "深色" : "浅色";
    summary.textContent = "仅包含本地阅读偏好";
    appendResourceSection(content, "Local preferences", [createResourceCard(
      "界面主题",
      "当前为" + theme + "主题；可使用顶部按钮切换。模型、密钥与运行时配置不会在此页面修改。",
      "浏览器本地显示偏好",
    )], "");
    return;
  }

  summary.textContent = "该工作区暂未开放。";
}

function collectLoadedSearchEntities() {
  const sessions = state.sessions.map((session) => ({
    kind: "session",
    view: "chat",
    title: sessionTitle(session),
    detail: session.updated_at ? "会话 · " + formatStoredTime(session.updated_at) : "会话",
    searchText: [sessionTitle(session), session.session_id, session.updated_at].join(" "),
    sessionId: session.session_id,
  }));
  const resources = state.resources;
  const entries = [
    ...resources.tools.map((tool) => ({ kind: "tool", view: "tools", title: tool.name, detail: tool.description || "工具", searchText: [tool.name, tool.description].join(" ") })),
    ...resources.skills.map((skill) => ({ kind: "skill", view: "tools", title: skill.name, detail: skill.description || "技能", searchText: [skill.name, skill.description, ...(skill.triggers || [])].join(" ") })),
    ...resources.agents.map((agent) => ({ kind: "agent", view: "agents", title: agent.name, detail: agent.description || "Agent 档案", searchText: [agent.name, agent.description, ...(agent.allowed_tools || [])].join(" ") })),
    ...resources.mcp.map((server) => ({ kind: "mcp", view: "mcp", title: server.name, detail: (server.transport || "unknown") + " · " + String(server.tool_count || 0) + " 个工具", searchText: [server.name, server.transport].join(" ") })),
    ...resources.files.map((file) => ({ kind: "file", view: "files", title: file.name, detail: formatFileSize(file.size), searchText: file.name })),
  ];
  return [...sessions, ...entries];
}

function closeLoadedSearch(restoreFocus = true) {
  const dialog = $("#search-dialog");
  if (!dialog || dialog.hidden) return;
  dialog.hidden = true;
  document.body.classList.remove("search-open");
  if (restoreFocus && state.searchReturnFocus && typeof state.searchReturnFocus.focus === "function") {
    state.searchReturnFocus.focus();
  }
  state.searchReturnFocus = null;
}

function openLoadedSearch() {
  const dialog = $("#search-dialog");
  const input = $("#search-input");
  if (!dialog || !input || !dialog.hidden) return;
  state.searchReturnFocus = document.activeElement;
  setNavigationOpen(false);
  dialog.hidden = false;
  document.body.classList.add("search-open");
  input.value = "";
  renderLoadedSearchResults();
  window.requestAnimationFrame(() => input.focus());
}

function trapLoadedSearchFocus(event) {
  const dialog = $("#search-dialog");
  if (event.key !== "Tab" || !dialog || dialog.hidden) return false;
  const focusables = Array.from(dialog.querySelectorAll(INSPECTOR_FOCUSABLE_SELECTOR)).filter(isElementFocusable);
  if (!focusables.length) {
    event.preventDefault();
    dialog.focus();
    return true;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && (!dialog.contains(document.activeElement) || document.activeElement === first)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && (!dialog.contains(document.activeElement) || document.activeElement === last)) {
    event.preventDefault();
    first.focus();
  }
  return true;
}

function renderLoadedSearchResults() {
  const input = $("#search-input");
  const results = $("#search-results");
  if (!input || !results) return;
  const query = input.value.trim().toLocaleLowerCase();
  const entities = collectLoadedSearchEntities();
  const matches = query ? entities.filter((entry) => entry.searchText.toLocaleLowerCase().includes(query)) : entities.slice(0, 12);
  results.replaceChildren();
  if (!matches.length) {
    const empty = document.createElement("p");
    empty.className = "search-empty";
    empty.textContent = "没有匹配项；仅搜索已加载内容。";
    results.appendChild(empty);
    return;
  }
  const labels = { session: "Sessions", agent: "Agents", tool: "Tools", skill: "Skills", mcp: "MCP", file: "Files" };
  const groups = new Map();
  matches.forEach((entry) => {
    const group = groups.get(entry.kind) || [];
    group.push(entry);
    groups.set(entry.kind, group);
  });
  groups.forEach((entries, kind) => {
    const section = document.createElement("section");
    section.className = "search-result-group";
    const heading = document.createElement("h3");
    heading.textContent = labels[kind];
    section.appendChild(heading);
    entries.forEach((entry) => {
      const item = document.createElement("button");
      item.type = "button";
      item.className = "search-result";
      const title = document.createElement("strong");
      title.textContent = entry.title;
      const detail = document.createElement("span");
      detail.textContent = entry.detail;
      item.append(title, detail);
      item.addEventListener("click", () => {
        if (entry.kind === "session") {
          if (!canChangeSession()) return;
          closeLoadedSearch(false);
          setPrimaryView("chat");
          void openSession(entry.sessionId);
          return;
        }
        closeLoadedSearch(false);
        setPrimaryView(entry.view);
      });
      section.appendChild(item);
    });
    results.appendChild(section);
  });
}

function setInspectorSection(section) {
  if (!new Set(["execution", "files", "settings"]).has(section)) return;
  uiState.inspectorSection = section;
  $$('[data-inspector-section]').forEach((button) => {
    const active = button.dataset.inspectorSection === section;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$('[data-inspector-section-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.inspectorSectionPanel !== section;
  });
  if (section === "files") renderInspectorFiles();
}

function setInspectorTab(tab) {
  if (!new Set(["timeline", "trace", "agents", "context"]).has(tab)) return;
  uiState.inspectorTab = tab;
  $$('[data-inspector-tab]').forEach((button) => {
    const active = button.dataset.inspectorTab === tab;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$('[data-inspector-tab-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.inspectorTabPanel !== tab;
  });
  if (tab === "timeline") {
    requestAnimationFrame(() => drawCompactCallLines($("#compact-call-graph")));
  }
  if (tab === "agents") renderLiveOrchestrations();
}

function setCompactCallView(view) {
  if (view !== "graph" && view !== "tools") return;
  uiState.compactCallView = view;
  $$('[data-compact-call-view]').forEach((button) => {
    const active = button.dataset.compactCallView === view;
    button.classList.toggle("active", active);
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  $$('[data-compact-call-panel]').forEach((panel) => {
    panel.hidden = panel.dataset.compactCallPanel !== view;
  });
  if (view === "graph") requestAnimationFrame(() => drawCompactCallLines($("#compact-call-graph")));
}

function renderInspectorFiles() {
  const container = $("#inspector-file-list");
  if (!container) return;
  container.replaceChildren();
  if (!state.resources.files.length) {
    const empty = document.createElement("p");
    empty.className = "compact-call-empty";
    empty.textContent = "全局沙箱目前没有文件。";
    container.appendChild(empty);
    return;
  }
  state.resources.files.forEach((file) => {
    const item = document.createElement("div");
    item.className = "inspector-file-item";
    const name = document.createElement("strong");
    name.textContent = file.name;
    const size = document.createElement("span");
    size.textContent = formatFileSize(file.size);
    item.append(name, size);
    container.appendChild(item);
  });
}

function bindLocalSettings() {
  const defaultTab = $("#settings-default-tab");
  let savedDefault = "timeline";
  try {
    const candidate = localStorage.getItem(DEFAULT_INSPECTOR_TAB_STORAGE_KEY);
    if (["timeline", "trace", "agents", "context"].includes(candidate)) savedDefault = candidate;
  } catch (error) { /* 忽略 */ }
  if (defaultTab) {
    defaultTab.value = savedDefault;
    defaultTab.addEventListener("change", () => {
      setInspectorTab(defaultTab.value);
      try { localStorage.setItem(DEFAULT_INSPECTOR_TAB_STORAGE_KEY, defaultTab.value); } catch (error) { /* 忽略 */ }
      setLocalFeedback("#settings-feedback", "success", "默认 Inspector Tab 已保存到当前浏览器。");
    });
  }
  $("#settings-theme-toggle")?.addEventListener("click", () => $("#theme-toggle")?.click());
  $("#clear-local-preferences")?.addEventListener("click", () => {
    try {
      ["reagent-theme", "reagent-timeline-filter", PANE_WIDTH_STORAGE_KEY, DEFAULT_INSPECTOR_TAB_STORAGE_KEY, SESSION_METADATA_STORAGE_KEY]
        .forEach((key) => localStorage.removeItem(key));
    } catch (error) { /* 忽略 */ }
    document.body.dataset.theme = "light";
    const theme = $("#theme-toggle");
    if (theme) theme.textContent = "◐";
    setTimelineFilter("all");
    resetPaneWidth("sidebar");
    resetPaneWidth("inspector");
    state.sessionMetadata = {};
    renderSessionList();
    if (defaultTab) defaultTab.value = "timeline";
    setInspectorTab("timeline");
    setLocalFeedback("#settings-feedback", "success", "本地阅读偏好已恢复默认；服务器会话未删除。");
  });
  return savedDefault;
}

function advanceExecutionViewVersion() {
  state.executionViewVersion += 1;
  return state.executionViewVersion;
}

function isCurrentExecutionViewVersion(version) {
  return version === state.executionViewVersion;
}

function canChangeSession() {
  return !state.streaming;
}

function syncSessionNavigationState() {
  $$("#new-session, [data-new-session]").forEach((newSession) => {
    newSession.disabled = !canChangeSession();
    newSession.title = canChangeSession() ? "新建会话" : "当前任务仍在执行；结束或停止后可切换会话";
  });
  $$("#session-list .session-item").forEach((item) => {
    const locked = !canChangeSession();
    item.setAttribute("aria-disabled", String(locked));
    const openButton = item.querySelector("[data-session-open]");
    if (openButton) openButton.disabled = locked;
    if (locked) item.title = "当前任务仍在执行；结束或停止后可切换会话";
    else item.removeAttribute("title");
  });
}

/* ================= 初始化 ================= */
async function init() {
  renderWelcome();
  state.sessionMetadata = loadSessionMetadata();
  const [capabilitiesReady] = await Promise.all([loadCapabilities(), loadSessions(), loadAgents()]);
  // 连接状态必须来自真实 API 响应，不能在失败后被无条件覆盖为“已连接”。
  setConnStatus(capabilitiesReady === true);
  bindEvents();
}

function setConnStatus(online) {
  $("#conn-status").className = "status-dot" + (online ? " online" : "");
  $("#conn-text").textContent = online ? "已连接" : "连接失败";
}

function resetExecutionEventCursor() {
  state.executionEventIds.clear();
  state.lastExecutionSeq = 0;
}

function registerExecutionEvent(event) {
  const payload = event && event.payload ? event.payload : (event || {});
  const executionId = event && event.execution_id ? event.execution_id : payload.execution_id;
  const eventId = event && event.event_id ? event.event_id : payload.event_id;
  const seq = Number(event && event.seq != null ? event.seq : payload.seq);

  // 只有 execution.queued 能成为另一条运行的入口；其余旧运行事件不能污染当前面板。
  if (executionId && state.executionId && executionId !== state.executionId) return false;
  if (eventId && state.executionEventIds.has(eventId)) return false;
  if (Number.isInteger(seq) && seq > 0 && state.lastExecutionSeq > 0 && seq <= state.lastExecutionSeq) return false;

  if (eventId) {
    state.executionEventIds.add(eventId);
    // seq 仍是主要去重依据；限制 Set 大小以免超长执行无限占用内存。
    if (state.executionEventIds.size > 4096) {
      const oldest = state.executionEventIds.values().next().value;
      state.executionEventIds.delete(oldest);
    }
  }
  if (Number.isInteger(seq) && seq > 0) state.lastExecutionSeq = Math.max(state.lastExecutionSeq, seq);
  return true;
}

function timestampToMillis(value) {
  const parsed = Date.parse(value);
  return Number.isFinite(parsed) ? parsed : null;
}

function markExecutionQueued(timestamp) {
  state.executionQueuedAt = timestampToMillis(timestamp) || state.executionQueuedAt || Date.now();
}

function markExecutionStarted(timestamp) {
  state.executionStartedAt = timestampToMillis(timestamp) || state.executionStartedAt || Date.now();
}

function startExecution(data) {
  advanceExecutionViewVersion();
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionId = data.execution_id || null;
  setAgentMode(data.mode || state.agentMode);
  resetExecutionEventCursor();
  renderExecutionHistory(state.executionHistory);
  if (data.session_id) state.sessionId = data.session_id;
  state.executionQueuedAt = Date.now();
  state.executionStartedAt = null;
  state.executionFinishedAt = null;
  const stop = $("#stop");
  if (stop) stop.disabled = false;
  state.executionSteps = 0;
  state.executionTools = 0;
  resetRunSummaryFacts();
  state.followLatest = true;
  clearLivePlan();
  clearLiveOrchestrations();
  clearExecutionContext();
  clearExecutionTrace();
  const title = $("#workspace-title");
  if (title && data.message_preview) title.textContent = truncateForWorkspace(data.message_preview);
  updateExecutionStatus("pending", "正在提交", "正在提交任务");
  const list = $("#execution-timeline");
  if (list) list.replaceChildren();
  $("#timeline-count").textContent = "0";
  appendExecutionEvent("pending", "正在提交任务", data.mode ? "模式：" + formatMode(data.mode) : "");
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionTimer = window.setInterval(renderExecutionMetrics, 500);
  renderExecutionMetrics();
}

function finishExecution(status, stage) {
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionTimer = null;
  if (!state.executionFinishedAt) state.executionFinishedAt = Date.now();
  const labels = {
    success: ["已完成", "执行完成"],
    error: ["执行失败", "执行失败"],
    cancelled: ["已停止", "已停止"],
  };
  const label = labels[status] || labels.success;
  updateExecutionStatus(status, label[0], stage || label[1]);
  renderExecutionMetrics();
}

function updateExecutionStatus(status, badgeText, stage) {
  const badge = $("#run-state-badge");
  if (badge) {
    badge.className = "run-state-badge " + status;
    badge.textContent = badgeText;
  }
  const runStage = $("#run-stage");
  if (runStage && stage) runStage.textContent = stage;
  const inspectorState = $("#inspector-state");
  if (inspectorState) inspectorState.textContent = badgeText;
  const mode = $("#inspector-mode");
  if (mode) mode.textContent = formatMode(state.agentMode);
  renderInspectorRunSummary();
}

function renderExecutionMetrics() {
  const stepCount = $("#run-step-count");
  const toolCount = $("#run-tool-count");
  const elapsed = $("#run-elapsed");
  if (stepCount) stepCount.textContent = String(state.executionSteps);
  if (toolCount) toolCount.textContent = String(state.executionTools);
  if (elapsed) elapsed.textContent = formatExecutionElapsed();
  renderInspectorRunSummary();
}

function usageNumber(value) {
  if (value === null || value === undefined || value === "") return null;
  const number = Number(value);
  return Number.isFinite(number) && number >= 0 ? number : null;
}

function resetRunSummaryFacts() {
  state.modelUsageFacts.clear();
  state.toolFacts.clear();
  renderInspectorRunSummary();
  renderCompactCalls();
}

function recordModelUsage(data, scopeKey) {
  const key = String(scopeKey || "observed:" + String(state.modelUsageFacts.size + 1));
  if (state.modelUsageFacts.has(key)) return false;
  const usage = data && data.usage && typeof data.usage === "object" ? data.usage : {};
  const prompt = usageNumber(usage.prompt_tokens);
  const completion = usageNumber(usage.completion_tokens);
  const providedTotal = usageNumber(usage.total_tokens);
  const total = providedTotal !== null ? providedTotal
    : prompt !== null && completion !== null ? prompt + completion : null;
  state.modelUsageFacts.set(key, {
    model: data && data.model ? String(data.model) : "",
    prompt,
    completion,
    total,
  });
  renderInspectorRunSummary();
  return true;
}

function summarizeUsage() {
  const models = new Set();
  let captured = 0;
  let prompt = 0;
  let completion = 0;
  let total = 0;
  state.modelUsageFacts.forEach((fact) => {
    if (fact.model) models.add(fact.model);
    if (fact.total !== null) {
      captured += 1;
      total += fact.total;
    }
    if (fact.prompt !== null) prompt += fact.prompt;
    if (fact.completion !== null) completion += fact.completion;
  });
  const observed = state.modelUsageFacts.size;
  return { models, observed, captured, prompt, completion, total, partial: captured < observed };
}

function recordToolFact(data, status, eventKey) {
  const explicitId = data && data.tool_call_id ? String(data.tool_call_id) : "";
  const key = explicitId ? "root:" + explicitId : "event:" + String(eventKey || state.toolFacts.size + 1);
  const existing = state.toolFacts.get(key);
  const normalizedStatus = status === "failed" || (data && data.success === false) ? "failed"
    : status === "success" || (data && data.success === true) ? "success" : "pending";
  state.toolFacts.set(key, {
    key,
    name: String((data && data.tool) || (existing && existing.name) || "tool"),
    status: normalizedStatus,
    durationMs: data && data.duration_ms != null ? Number(data.duration_ms) : existing?.durationMs ?? null,
  });
  renderCompactCalls();
  renderInspectorRunSummary();
  return !existing;
}

function summarizeToolFacts() {
  const summary = { total: state.toolFacts.size, success: 0, failed: 0, pending: 0 };
  state.toolFacts.forEach((fact) => {
    if (fact.status === "success") summary.success += 1;
    else if (fact.status === "failed") summary.failed += 1;
    else summary.pending += 1;
  });
  return summary;
}

function createCompactCallNode(label, role, status = "") {
  const node = document.createElement("span");
  node.className = "compact-call-node " + role + (status ? " " + status : "");
  node.dataset.callRole = role;
  node.textContent = label;
  return node;
}

function drawCompactCallLines(container) {
  const svg = container.querySelector(".compact-call-svg");
  const user = container.querySelector('[data-call-role="user"]');
  const runtime = container.querySelector('[data-call-role="runtime"]');
  if (!svg || !user || !runtime || !container.offsetWidth) return;
  const containerRect = container.getBoundingClientRect();
  const point = (node, edge) => {
    const rect = node.getBoundingClientRect();
    return {
      x: rect.left - containerRect.left + rect.width / 2,
      y: edge === "top" ? rect.top - containerRect.top : rect.bottom - containerRect.top,
    };
  };
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${containerRect.width} ${containerRect.height}`);
  const appendLine = (from, to) => {
    const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
    line.setAttribute("x1", String(from.x));
    line.setAttribute("y1", String(from.y));
    line.setAttribute("x2", String(to.x));
    line.setAttribute("y2", String(to.y));
    svg.appendChild(line);
  };
  appendLine(point(user, "bottom"), point(runtime, "top"));
  container.querySelectorAll('[data-call-role="tool"], [data-call-role="agent"]').forEach((node) => {
    appendLine(point(runtime, "bottom"), point(node, "top"));
  });
}

function renderCompactCalls() {
  const graph = $("#compact-call-graph");
  const tools = $("#compact-tool-calls");
  if (!graph || !tools) return;
  graph.replaceChildren();
  tools.replaceChildren();

  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("compact-call-svg");
  svg.setAttribute("aria-hidden", "true");
  const nodes = document.createElement("div");
  nodes.className = "compact-call-nodes";
  nodes.append(createCompactCallNode("User", "user"), createCompactCallNode("General Agent", "runtime"));
  state.toolFacts.forEach((fact) => nodes.appendChild(createCompactCallNode(fact.name, "tool", fact.status)));
  state.orchestrationRuns.forEach((run) => {
    run.agents.forEach((agent) => nodes.appendChild(createCompactCallNode(agent.profile, "agent", orchestrationStateName(agent.status))));
  });
  graph.append(svg, nodes);

  const toolList = document.createElement("ol");
  toolList.className = "compact-tool-list";
  state.toolFacts.forEach((fact) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.textContent = fact.name;
    const status = document.createElement("strong");
    status.textContent = fact.status === "success" ? "成功" : fact.status === "failed" ? "失败" : "等待结果";
    item.append(name, status);
    toolList.appendChild(item);
  });
  if (!toolList.children.length) {
    const empty = document.createElement("p");
    empty.className = "compact-call-empty";
    empty.textContent = "当前运行尚无工具调用。";
    tools.appendChild(empty);
  } else {
    tools.appendChild(toolList);
  }
  requestAnimationFrame(() => drawCompactCallLines(graph));
}

function renderInspectorRunSummary() {
  const usage = summarizeUsage();
  const tools = summarizeToolFacts();
  const models = [...usage.models];
  const setText = (selector, value) => { const element = $(selector); if (element) element.textContent = value; };
  setText("#summary-status", $("#run-state-badge")?.textContent || "准备就绪");
  setText("#summary-mode", formatMode(state.agentMode));
  setText("#summary-decisions", String(state.executionSteps));
  const plan = state.currentPlanVersion !== null ? state.planSnapshots.get(state.currentPlanVersion) : null;
  setText("#summary-plan-steps", String(plan && Array.isArray(plan.steps) ? plan.steps.length : 0));
  setText("#summary-model", !models.length ? "未采集" : models.length === 1 ? models[0] : "多模型 · " + models.join("、"));
  const coverage = usage.observed ? String(usage.captured) + "/" + String(usage.observed) + " 次调用" : "未采集";
  setText("#summary-tokens", usage.captured ? String(usage.total) + " tokens · " + (usage.partial ? "部分采集 " : "") + coverage : coverage);
  const toolParts = [String(tools.total) + " 次"];
  if (tools.success) toolParts.push(String(tools.success) + " 成功");
  if (tools.failed) toolParts.push(String(tools.failed) + " 失败");
  if (tools.pending) toolParts.push(String(tools.pending) + " 等待");
  setText("#summary-tool-calls", toolParts.join(" · "));
  setText("#summary-duration", formatExecutionElapsed());
}

function formatExecutionElapsed() {
  const startedAt = state.executionStartedAt || state.executionQueuedAt;
  if (!startedAt) return "0s";
  const endedAt = state.executionFinishedAt || Date.now();
  const seconds = Math.max(0, Math.floor((endedAt - startedAt) / 1000));
  const duration = seconds < 60
    ? String(seconds) + "s"
    : String(Math.floor(seconds / 60)) + "m " + String(seconds % 60) + "s";
  return state.executionStartedAt ? duration : "排队 " + duration;
}

function formatMode(mode) {
  return mode === "plan" ? "Plan" : "ReAct";
}

function setAgentMode(mode) {
  const normalized = mode === "plan" ? "plan" : "react";
  state.agentMode = normalized;
  $$(".mode-btn").forEach((button) => {
    const active = button.dataset.mode === normalized;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
  const label = formatMode(normalized);
  const badge = $("#mode-badge");
  if (badge) badge.textContent = label;
  const inspectorMode = $("#inspector-mode");
  if (inspectorMode) inspectorMode.textContent = label;
  renderInspectorRunSummary();
}

function truncateForWorkspace(value) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > 46 ? text.slice(0, 45) + "…" : text;
}

function executionQueueDetail(data) {
  return data && data.queue_reason === "runtime_busy"
    ? "运行时正在处理另一项任务"
    : "正在分配执行资源";
}

function timelineEventMatchesFilter(kind) {
  if (state.timelineFilter === "active") return kind === "pending" || kind === "running";
  if (state.timelineFilter === "success") return kind === "success";
  if (state.timelineFilter === "attention") return kind === "warning" || kind === "error";
  return true;
}

function hasTimelineDetail(detail) {
  return detail !== null && detail !== undefined && String(detail) !== "";
}

function timelineDetailNeedsExpansion(detail) {
  const text = String(detail);
  return text.length > 96 || /[\r\n]/.test(text);
}

function applyTimelineFilter() {
  const list = $("#execution-timeline");
  if (!list) return { total: 0, visible: 0 };

  const items = Array.from(list.children).filter((item) => item.classList.contains("timeline-item"));
  let visible = 0;
  for (const item of items) {
    const matches = timelineEventMatchesFilter(item.dataset.timelineKind || "");
    item.hidden = !matches;
    if (matches) visible += 1;
  }

  let empty = list.querySelector(".timeline-filter-empty");
  if (items.length && !visible) {
    if (!empty) {
      empty = document.createElement("li");
      empty.className = "timeline-empty timeline-filter-empty";
      list.appendChild(empty);
    }
    empty.textContent = "当前筛选条件下没有事件。";
  } else if (empty) {
    empty.remove();
  }

  const count = $("#timeline-count");
  if (count) {
    count.textContent = items.length === visible ? String(items.length) : String(visible) + " / " + String(items.length);
    count.setAttribute("aria-label", "显示 " + String(visible) + " 条，共 " + String(items.length) + " 条事件");
  }
  return { total: items.length, visible };
}

function updateTimelineFilterControls() {
  $$('[data-timeline-filter]').forEach((button) => {
    const selected = button.dataset.timelineFilter === state.timelineFilter;
    button.classList.toggle("active", selected);
    button.setAttribute("aria-pressed", String(selected));
  });
}

function setTimelineFilter(filter, savePreference = true) {
  if (!TIMELINE_FILTERS.has(filter)) return;
  state.timelineFilter = filter;
  updateTimelineFilterControls();
  applyTimelineFilter();
  if (savePreference) {
    try { localStorage.setItem("reagent-timeline-filter", filter); } catch (e) { /* 忽略 */ }
  }
}

function appendExecutionEvent(kind, title, detail, timestamp) {
  const list = $("#execution-timeline");
  if (!list) return;
  const empty = list.querySelector(".timeline-empty");
  if (empty) empty.remove();

  const item = document.createElement("li");
  item.className = "timeline-item " + kind;
  item.dataset.timelineKind = kind;
  const heading = document.createElement("div");
  heading.className = "timeline-title";
  heading.textContent = title;
  item.appendChild(heading);
  if (hasTimelineDetail(detail)) {
    const detailText = String(detail);
    const copy = document.createElement("div");
    copy.className = "timeline-detail";
    copy.textContent = detailText;
    copy.title = detailText;
    item.appendChild(copy);
    if (timelineDetailNeedsExpansion(detailText)) {
      const expanded = document.createElement("details");
      expanded.className = "timeline-detail-expand";
      const summary = document.createElement("summary");
      summary.textContent = "查看完整详情";
      const full = document.createElement("div");
      full.className = "timeline-detail-full";
      full.textContent = detailText;
      expanded.append(summary, full);
      item.appendChild(expanded);
    }
  }
  const time = document.createElement("time");
  time.className = "timeline-time";
  time.textContent = new Date(timestamp || Date.now()).toLocaleTimeString("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  item.appendChild(time);
  list.appendChild(item);
  applyTimelineFilter();
  if (!item.hidden) list.scrollTop = list.scrollHeight;
}

function clonePlanSteps(plan) {
  return plan.map((step) => ({
    ...step,
    tools_hint: Array.isArray(step.tools_hint) ? [...step.tools_hint] : [],
  }));
}

function planStateName(step) {
  return String((step && step.status) || "PLANNED").toLowerCase();
}

function planStepIcon(stateName, index) {
  if (stateName === "succeeded") return "●";
  if (stateName === "failed") return "×";
  if (stateName === "skipped") return "–";
  if (stateName === "running") return "◌";
  return String(index + 1);
}

function createPlanStepItem(step, index, className = "live-plan-item") {
  const stateName = planStateName(step);
  const item = document.createElement("div");
  item.className = className + " " + stateName;
  const icon = document.createElement("span");
  icon.className = "live-plan-icon";
  icon.textContent = planStepIcon(stateName, index);
  icon.setAttribute("aria-label", planStatusLabel(stateName));
  const copy = document.createElement("div");
  copy.className = "live-plan-copy";
  const title = document.createElement("strong");
  title.textContent = step.description || "未命名步骤";
  copy.appendChild(title);
  if (step.result) {
    const result = document.createElement("span");
    result.textContent = step.result;
    copy.appendChild(result);
  } else if (Array.isArray(step.tools_hint) && step.tools_hint.length) {
    const hint = document.createElement("span");
    hint.textContent = "建议工具：" + step.tools_hint.join("、");
    copy.appendChild(hint);
  }
  item.append(icon, copy);
  return item;
}

function planStatusLabel(stateName) {
  const labels = {
    planned: "待执行",
    running: "执行中",
    succeeded: "已完成",
    failed: "失败",
    skipped: "已跳过",
  };
  return labels[stateName] || "状态未知";
}

function renderLivePlan(plan, revisions = 0, planVersion = null) {
  const section = $("#live-plan-section");
  const list = $("#live-plan");
  const meta = $("#live-plan-meta");
  if (!section || !list || !meta) return;
  if (!Array.isArray(plan) || !plan.length) {
    clearLivePlan();
    return;
  }

  const total = plan.length;
  const terminal = plan.filter((step) => ["SUCCEEDED", "FAILED", "SKIPPED"].includes(String(step.status))).length;
  const failed = plan.filter((step) => String(step.status) === "FAILED").length;
  const version = Number(planVersion) || Math.max(1, Number(revisions || 0) + 1);
  const details = ["v" + String(version), String(total) + " 步", "已处理 " + String(terminal) + "/" + String(total)];
  if (failed) details.push(String(failed) + " 失败");
  if (revisions) details.push("调整 " + String(revisions));

  section.hidden = false;
  meta.textContent = details.join(" · ");
  list.replaceChildren();
  plan.forEach((step, index) => list.appendChild(createPlanStepItem(step, index)));
}

function setLivePlan(plan, planVersion, revisions = 0) {
  if (!Array.isArray(plan) || !plan.length) {
    clearLivePlan();
    return;
  }
  const version = Number(planVersion) || Math.max(1, Number(revisions || 0) + 1);
  const normalized = clonePlanSteps(plan);
  state.planSnapshots.set(version, normalized);
  state.currentPlanVersion = version;
  state.planRevisions = Math.max(0, Number(revisions) || 0);
  renderLivePlan(normalized, state.planRevisions, version);
  renderPlanRevisionHistory();
  renderInspectorRunSummary();
}

function updateLivePlanStep(payload) {
  const version = Number(payload.plan_version);
  const step = payload.step;
  if (!version || !step || !payload.plan_step_id) return false;
  const plan = state.planSnapshots.get(version);
  if (!plan) return false;
  const index = plan.findIndex((candidate) => candidate.step_id === payload.plan_step_id);
  if (index < 0) return false;
  plan[index] = { ...plan[index], ...step };
  if (state.currentPlanVersion === version) {
    renderLivePlan(plan, state.planRevisions, version);
  }
  renderPlanRevisionHistory();
  return true;
}

function recordPlanRevision(payload) {
  const previousVersion = Number(payload.previous_plan_version);
  if (!previousVersion) return;
  if (Array.isArray(payload.previous_steps) && payload.previous_steps.length) {
    state.planSnapshots.set(previousVersion, clonePlanSteps(payload.previous_steps));
  }
  state.planRevisionReasons.set(previousVersion, String(payload.reason || ""));
  renderPlanRevisionHistory();
}

function renderPlanRevisionHistory() {
  const container = $("#plan-revision-history");
  if (!container) return;
  container.replaceChildren();
  const versions = [...state.planSnapshots.keys()]
    .filter((version) => version !== state.currentPlanVersion)
    .sort((left, right) => right - left);
  versions.forEach((version) => {
    const snapshot = state.planSnapshots.get(version) || [];
    const details = document.createElement("details");
    details.className = "plan-revision";
    const summary = document.createElement("summary");
    const reason = state.planRevisionReasons.get(version);
    summary.textContent = "v" + String(version) + " · 已调整" + (reason ? "：" + reason : "");
    details.appendChild(summary);
    const list = document.createElement("div");
    list.className = "plan-revision-list";
    snapshot.forEach((step, index) => list.appendChild(createPlanStepItem(step, index, "plan-revision-step")));
    details.appendChild(list);
    container.appendChild(details);
  });
}

function clearLivePlan() {
  const section = $("#live-plan-section");
  const list = $("#live-plan");
  const meta = $("#live-plan-meta");
  const history = $("#plan-revision-history");
  state.currentPlanVersion = null;
  state.planRevisions = 0;
  state.planSnapshots.clear();
  state.planRevisionReasons.clear();
  if (list) list.replaceChildren();
  if (meta) meta.textContent = "0 步";
  if (history) history.replaceChildren();
  if (section) section.hidden = true;
  renderInspectorRunSummary();
}

function applyPlanLifecycleEvent(type, payload, timestamp) {
  if (type === "plan.created") {
    const version = Number(payload.plan_version) || 1;
    const revisions = Math.max(state.planRevisions, version - 1);
    setLivePlan(payload.steps, version, revisions);
    updateExecutionStatus("running", "执行中", "已生成执行计划 v" + String(version));
    appendExecutionEvent("running", "已生成执行计划", "v" + String(version) + " · " + String(payload.total_steps || 0) + " 步", timestamp);
    return true;
  }
  if (type === "plan.degraded") {
    updateExecutionStatus("running", "执行中", "规划不可用，正在改用直接 ReAct");
    appendExecutionEvent("warning", "计划未生成，已降级执行", payload.reason || "", timestamp);
    return true;
  }
  if (type === "plan_step.started") {
    updateLivePlanStep(payload);
    const step = payload.step || {};
    updateExecutionStatus("running", "执行中", "正在执行计划步骤 " + String((Number(step.order) || 0) + 1) + "/" + String(payload.total_steps || "?"));
    appendExecutionEvent("running", "开始计划步骤：" + String(step.description || payload.plan_step_id || ""), "v" + String(payload.plan_version || "?"), timestamp);
    return true;
  }
  if (type === "plan_step.completed" || type === "plan_step.failed") {
    updateLivePlanStep(payload);
    const step = payload.step || {};
    const failed = type === "plan_step.failed";
    appendExecutionEvent(
      failed ? "error" : "success",
      (failed ? "计划步骤失败：" : "计划步骤完成：") + String(step.description || payload.plan_step_id || ""),
      "v" + String(payload.plan_version || "?"),
      timestamp
    );
    return true;
  }
  if (type === "plan.summarize_started") {
    updateExecutionStatus("running", "执行中", "计划步骤已结束，正在汇总结果");
    appendExecutionEvent("running", "开始汇总计划结果", "v" + String(payload.plan_version || "?"), timestamp);
    return true;
  }
  if (type === "reflection.completed") {
    const needReplan = payload.need_replan === true;
    updateExecutionStatus(
      "running",
      "执行中",
      needReplan ? "反思发现问题，正在调整计划" : "计划检查完成"
    );
    appendExecutionEvent(
      needReplan ? "warning" : "success",
      needReplan ? "反思需要调整计划" : "反思确认计划结果",
      payload.reason || "",
      timestamp
    );
    return true;
  }
  if (type === "plan.revised") {
    recordPlanRevision(payload);
    updateExecutionStatus("running", "执行中", "计划已调整，正在生成 v" + String(payload.next_plan_version || "?"));
    appendExecutionEvent(
      "warning",
      "计划已调整",
      "v" + String(payload.previous_plan_version || "?") + " → v" + String(payload.next_plan_version || "?") + (payload.reason ? " · " + payload.reason : ""),
      timestamp
    );
    return true;
  }
  return false;
}

function createExecutionContextState() {
  return {
    memory: null,
    skills: null,
    context: null,
    checkpoint: null,
    memoryStored: null,
  };
}

function clearExecutionContext() {
  const section = $("#execution-context-section");
  const list = $("#execution-context");
  const meta = $("#execution-context-meta");
  const empty = $("#context-empty-state");
  state.executionContext = createExecutionContextState();
  if (list) list.replaceChildren();
  if (meta) meta.textContent = "未采集";
  if (section) section.hidden = true;
  if (empty) empty.hidden = false;
}

function clearExecutionTrace() {
  state.executionTrace = null;
  state.executionTraceId = null;
  renderExecutionTrace();
}

function setExecutionTrace(trace, traceId) {
  state.executionTrace = trace && typeof trace === "object" ? trace : null;
  state.executionTraceId = traceId ? String(traceId) : null;
  renderExecutionTrace();
}

function renderExecutionTrace() {
  const container = $("#execution-trace");
  const meta = $("#execution-trace-meta");
  if (!container || !meta) return;
  container.replaceChildren();

  const trace = state.executionTrace;
  const spans = trace && Array.isArray(trace.spans) ? trace.spans : [];
  if (!trace) {
    meta.textContent = state.executionTraceId ? "加载失败" : "未采集";
    const empty = document.createElement("p");
    empty.className = "inspector-empty-copy";
    empty.textContent = state.executionTraceId
      ? "Trace 已记录，但当前无法读取调用树。"
      : "选择一次执行后，这里会显示已采集的调用树。未启用或未生成的 Trace 会明确标识。";
    container.appendChild(empty);
    return;
  }
  if (!spans.length) {
    meta.textContent = "无 Span";
    const empty = document.createElement("p");
    empty.className = "inspector-empty-copy";
    empty.textContent = "本次运行未采集到可展示的 Trace Span。";
    container.appendChild(empty);
    return;
  }

  const totalDuration = Math.max(spans.reduce((sum, span) => sum + (Number(span.duration_ms) || 0), 0), 1);
  const tree = document.createElement("div");
  tree.className = "trace-tree";
  tree.innerHTML = spans.map((span) => renderTraceNodeV2(span, 0, totalDuration)).join("");
  container.appendChild(tree);
  meta.textContent = String(spans.length) + " 个根 Span";
  bindTreeToggles(tree);
}

async function fetchExecutionTrace(traceId, viewVersion = state.executionViewVersion) {
  if (!traceId) return;
  state.executionTraceId = String(traceId);
  renderExecutionTrace();
  try {
    const response = await fetch("/api/web/traces/" + encodeURIComponent(traceId));
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    const trace = await response.json();
    if (!isCurrentExecutionViewVersion(viewVersion) || state.executionTraceId !== String(traceId)) return;
    setExecutionTrace(trace, traceId);
  } catch (error) {
    if (!isCurrentExecutionViewVersion(viewVersion) || state.executionTraceId !== String(traceId)) return;
    state.executionTrace = null;
    renderExecutionTrace();
  }
}

function createContextFact(title, value, detail, kind = "") {
  const item = document.createElement("div");
  item.className = "execution-context-item" + (kind ? " " + kind : "");
  const copy = document.createElement("div");
  copy.className = "execution-context-copy";
  const heading = document.createElement("strong");
  heading.textContent = title;
  const main = document.createElement("span");
  main.textContent = value;
  copy.append(heading, main);
  if (detail) {
    const extra = document.createElement("span");
    extra.className = "execution-context-detail";
    extra.textContent = detail;
    copy.appendChild(extra);
  }
  item.appendChild(copy);
  return item;
}

function renderExecutionContext() {
  const section = $("#execution-context-section");
  const list = $("#execution-context");
  const meta = $("#execution-context-meta");
  const empty = $("#context-empty-state");
  if (!section || !list || !meta) return;
  const facts = state.executionContext || createExecutionContextState();
  const entries = [];
  if (facts.memory) entries.push(["记忆检索", String(facts.memory.hitCount) + " 条匹配", facts.memory.purpose === "planning" ? "用于计划生成" : "用于本轮上下文", facts.memory.hitCount ? "success" : ""]);
  if (facts.skills) entries.push(["技能匹配", facts.skills.count ? facts.skills.names.join("、") : "未匹配技能", facts.skills.count ? String(facts.skills.count) + " 项技能指令已注入" : "未向上下文注入技能指令", ""]);
  if (facts.context) {
    const context = facts.context;
    const detailParts = ["历史 " + String(context.totalHistory) + " → " + String(context.selectedMessages) + " 条"];
    if (context.retrievedDocuments) detailParts.push(String(context.retrievedDocuments) + " 份参考资料");
    if (context.hasSummary) detailParts.push("已使用历史摘要");
    entries.push(["模型上下文", "第 " + String(context.step) + " 轮 · 约 " + String(context.estimatedTokens) + " tokens", detailParts.join(" · "), ""]);
  }
  if (facts.checkpoint) {
    const checkpoint = facts.checkpoint;
    const action = checkpoint.restored ? "已恢复" : "已保存";
    entries.push(["执行检查点", action + " v" + String(checkpoint.version), checkpoint.point ? checkpoint.point + " · " + String(checkpoint.status || "") : String(checkpoint.status || ""), checkpoint.restored ? "warning" : ""]);
  }
  if (facts.memoryStored) entries.push(["记忆写入", String(facts.memoryStored.count) + " 条已保存", "供后续会话检索", "success"]);

  list.replaceChildren();
  if (!entries.length) {
    section.hidden = true;
    meta.textContent = "未采集";
    if (empty) empty.hidden = false;
    return;
  }
  entries.forEach(([title, value, detail, kind]) => list.appendChild(createContextFact(title, value, detail, kind)));
  section.hidden = false;
  meta.textContent = String(entries.length) + " 项事实";
  $("#context-empty-state").hidden = entries.length > 0;
}

function applyRuntimeLifecycleEvent(type, payload, timestamp) {
  const context = state.executionContext || createExecutionContextState();
  state.executionContext = context;
  if (type === "memory.retrieved") {
    context.memory = {
      hitCount: Number(payload.hit_count) || 0,
      purpose: String(payload.purpose || "context"),
    };
    appendExecutionEvent("running", "记忆检索完成", String(context.memory.hitCount) + " 条匹配", timestamp);
  } else if (type === "skill.selected") {
    context.skills = {
      count: Number(payload.count) || 0,
      names: Array.isArray(payload.skills) ? payload.skills.map((item) => String(item)) : [],
    };
    appendExecutionEvent("running", context.skills.count ? "已选择技能：" + context.skills.names.join("、") : "未匹配技能", "", timestamp);
  } else if (type === "context.completed") {
    context.context = {
      step: Number(payload.step) || 0,
      totalHistory: Number(payload.total_history) || 0,
      selectedMessages: Number(payload.selected_messages) || 0,
      estimatedTokens: Number(payload.estimated_tokens) || 0,
      retrievedDocuments: Number(payload.retrieved_documents) || 0,
      hasSummary: payload.has_summary === true,
    };
    appendExecutionEvent(
      "running",
      "模型上下文准备完成",
      "第 " + String(context.context.step || "?") + " 轮 · 约 " + String(context.context.estimatedTokens) + " tokens",
      timestamp
    );
  } else if (type === "checkpoint.saved" || type === "checkpoint.restored") {
    context.checkpoint = {
      version: Number(payload.checkpoint_version) || 0,
      status: String(payload.state_status || ""),
      point: String(payload.point || ""),
      restored: type === "checkpoint.restored",
    };
    appendExecutionEvent(
      type === "checkpoint.restored" ? "warning" : "running",
      type === "checkpoint.restored" ? "已恢复执行检查点" : "已保存执行检查点",
      "v" + String(context.checkpoint.version) + (context.checkpoint.point ? " · " + context.checkpoint.point : ""),
      timestamp
    );
  } else if (type === "memory.stored") {
    context.memoryStored = { count: Number(payload.stored_count) || 0 };
    appendExecutionEvent("success", "记忆提炼完成", String(context.memoryStored.count) + " 条已保存", timestamp);
  } else {
    return false;
  }
  renderExecutionContext();
  return true;
}

function orchestrationStateName(status) {
  const value = String(status || "PENDING").toUpperCase();
  const mapping = {
    PENDING: "pending",
    SCHEDULED: "pending",
    RUNNING: "running",
    SUCCEEDED: "succeeded",
    COMPLETED: "succeeded",
    PARTIAL: "partial",
    FAILED: "failed",
    SKIPPED: "skipped",
  };
  return mapping[value] || "pending";
}

function orchestrationStateLabel(status) {
  const labels = {
    pending: "待执行",
    running: "执行中",
    succeeded: "已完成",
    partial: "部分完成",
    failed: "失败",
    skipped: "已跳过",
  };
  return labels[orchestrationStateName(status)] || "待执行";
}

function orchestrationStateIcon(status) {
  const stateName = orchestrationStateName(status);
  if (stateName === "succeeded") return "●";
  if (stateName === "partial") return "◐";
  if (stateName === "failed") return "×";
  if (stateName === "skipped") return "–";
  if (stateName === "running") return "◌";
  return "○";
}

function getOrchestrationRun(runId) {
  if (!runId) return null;
  let run = state.orchestrationRuns.get(runId);
  if (!run) {
    run = {
      runId,
      parentRunId: null,
      depth: 1,
      taskPreview: "",
      requestedAgents: [],
      rationale: "",
      status: "PENDING",
      stage: "等待编排",
      durationMs: null,
      agents: new Map(),
      planOrder: [],
    };
    state.orchestrationRuns.set(runId, run);
  }
  return run;
}

function upsertOrchestrationAgent(run, payload) {
  if (!run || !payload.agent_instance_id) return null;
  const id = String(payload.agent_instance_id);
  let agent = run.agents.get(id);
  if (!agent) {
    agent = {
      id,
      profile: "未命名 Agent",
      stepIndex: 0,
      taskPreview: "",
      dependsOn: [],
      status: "PENDING",
      stage: "等待调度",
      answerPreview: "",
      error: "",
      durationMs: null,
      toolCalls: 0,
      completedToolCallIds: new Set(),
      toolSummary: "",
    };
    run.agents.set(id, agent);
  }
  if (payload.agent_profile) agent.profile = String(payload.agent_profile);
  if (Number.isFinite(Number(payload.step_index))) agent.stepIndex = Number(payload.step_index);
  if (payload.task_preview) agent.taskPreview = String(payload.task_preview);
  if (Array.isArray(payload.depends_on)) agent.dependsOn = [...payload.depends_on];
  if (!run.planOrder.includes(id)) run.planOrder.push(id);
  return agent;
}

function clearLiveOrchestrations() {
  const section = $("#live-orchestration-section");
  const list = $("#live-orchestrations");
  const meta = $("#live-orchestration-meta");
  const empty = $("#agents-empty-state");
  state.orchestrationRuns.clear();
  if (list) list.replaceChildren();
  if (meta) meta.textContent = "0 个编排";
  if (section) section.hidden = true;
  if (empty) empty.hidden = false;
  renderCompactCalls();
}

function renderOrchestrationAgent(agent) {
  const stateName = orchestrationStateName(agent.status);
  const item = document.createElement("div");
  item.className = "live-agent-item " + stateName;
  const icon = document.createElement("span");
  icon.className = "live-agent-icon";
  icon.textContent = orchestrationStateIcon(agent.status);
  icon.setAttribute("aria-label", orchestrationStateLabel(agent.status));

  const copy = document.createElement("div");
  copy.className = "live-agent-copy";
  const title = document.createElement("strong");
  title.textContent = agent.profile + " · 步骤 " + String(agent.stepIndex + 1);
  copy.appendChild(title);

  const task = document.createElement("span");
  task.textContent = agent.taskPreview || agent.stage || "等待执行";
  copy.appendChild(task);

  const metaParts = [];
  if (agent.dependsOn.length) metaParts.push("依赖 " + agent.dependsOn.map((index) => String(Number(index) + 1)).join("、"));
  if (agent.durationMs != null) metaParts.push("耗时 " + formatMs(agent.durationMs));
  if (agent.toolCalls) metaParts.push(String(agent.toolCalls) + " 次工具");
  if (metaParts.length) {
    const meta = document.createElement("span");
    meta.className = "live-agent-meta";
    meta.textContent = metaParts.join(" · ");
    copy.appendChild(meta);
  }
  if (agent.answerPreview) {
    const answer = document.createElement("span");
    answer.className = "live-agent-result";
    answer.textContent = agent.answerPreview;
    copy.appendChild(answer);
  }
  if (agent.toolSummary) {
    const tool = document.createElement("span");
    tool.className = "live-agent-tool";
    tool.textContent = agent.toolSummary;
    copy.appendChild(tool);
  }
  if (agent.error) {
    const error = document.createElement("span");
    error.className = "live-agent-error";
    error.textContent = agent.error;
    copy.appendChild(error);
  }
  item.append(icon, copy);
  return item;
}

function dependencyGraphProblem(agentsByStep) {
  for (const agent of agentsByStep.values()) {
    if (agent.dependsOn.some((stepIndex) => !agentsByStep.has(Number(stepIndex)))) return "存在未知依赖";
  }
  const visiting = new Set();
  const visited = new Set();
  const visit = (stepIndex) => {
    if (visiting.has(stepIndex)) return true;
    if (visited.has(stepIndex)) return false;
    visiting.add(stepIndex);
    const agent = agentsByStep.get(stepIndex);
    if (agent && agent.dependsOn.some((source) => visit(Number(source)))) return true;
    visiting.delete(stepIndex);
    visited.add(stepIndex);
    return false;
  };
  for (const stepIndex of agentsByStep.keys()) {
    if (visit(stepIndex)) return "检测到循环依赖";
  }
  return "";
}

function drawAgentDependencyLines(graph, agentsByStep) {
  const svg = graph.querySelector(".agent-dependency-svg");
  const nodeList = graph.querySelector(".agent-dependency-nodes");
  if (!svg || !nodeList || !nodeList.offsetWidth) return;
  const containerRect = nodeList.getBoundingClientRect();
  svg.replaceChildren();
  svg.setAttribute("viewBox", `0 0 ${containerRect.width} ${containerRect.height}`);
  agentsByStep.forEach((agent) => {
    const target = nodeList.querySelector(`[data-step-index="${agent.stepIndex}"]`);
    if (!target) return;
    const targetRect = target.getBoundingClientRect();
    agent.dependsOn.forEach((stepIndex) => {
      const source = nodeList.querySelector(`[data-step-index="${Number(stepIndex)}"]`);
      if (!source) return;
      const sourceRect = source.getBoundingClientRect();
      const line = document.createElementNS("http://www.w3.org/2000/svg", "line");
      line.setAttribute("x1", String(sourceRect.right - containerRect.left));
      line.setAttribute("y1", String(sourceRect.top - containerRect.top + sourceRect.height / 2));
      line.setAttribute("x2", String(targetRect.left - containerRect.left));
      line.setAttribute("y2", String(targetRect.top - containerRect.top + targetRect.height / 2));
      svg.appendChild(line);
    });
  });
}

function renderOrchestrationDependencyGraph(agents) {
  const graph = document.createElement("section");
  graph.className = "agent-dependency-graph";
  const heading = document.createElement("h4");
  heading.textContent = "依赖关系";
  graph.appendChild(heading);

  const agentsByStep = new Map(agents.map((agent) => [agent.stepIndex, agent]));
  const problem = dependencyGraphProblem(agentsByStep);
  const visibleAgents = agents.slice(0, 12);
  const nodeList = document.createElement("div");
  nodeList.className = "agent-dependency-nodes";
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.classList.add("agent-dependency-svg");
  svg.setAttribute("aria-hidden", "true");
  nodeList.appendChild(svg);
  visibleAgents.forEach((agent) => {
    const node = document.createElement("span");
    node.className = "agent-dependency-node " + orchestrationStateName(agent.status);
    node.dataset.stepIndex = String(agent.stepIndex);
    node.textContent = String(agent.stepIndex + 1) + " · " + agent.profile;
    nodeList.appendChild(node);
  });
  graph.appendChild(nodeList);

  const edges = document.createElement("div");
  edges.className = "agent-dependency-edges";
  let edgeCount = 0;
  agents.forEach((agent) => {
    if (!agent.dependsOn.length) return;
    const edge = document.createElement("span");
    edge.className = "agent-dependency-edge";
    edge.textContent = agent.dependsOn.map((source) => "步骤 " + String(Number(source) + 1)).join("、") + " → 步骤 " + String(agent.stepIndex + 1);
    edges.appendChild(edge);
    edgeCount += 1;
  });
  if (problem || !edgeCount || agents.length > 12) {
    const message = document.createElement("span");
    message.className = "agent-dependency-empty";
    message.textContent = problem ? "关系不可用（" + problem + "），已回退到实例列表"
      : agents.length > 12 ? "概览仅显示前 12 个节点；完整实例见下方列表"
      : "本次计划未声明跨 Agent 依赖";
    edges.appendChild(message);
  }
  graph.appendChild(edges);
  if (!problem) requestAnimationFrame(() => drawAgentDependencyLines(graph, agentsByStep));
  return graph;
}

function renderLiveOrchestrations() {
  const section = $("#live-orchestration-section");
  const list = $("#live-orchestrations");
  const meta = $("#live-orchestration-meta");
  const empty = $("#agents-empty-state");
  if (!section || !list || !meta) return;
  const runs = [...state.orchestrationRuns.values()].sort((left, right) => {
    if (left.depth !== right.depth) return left.depth - right.depth;
    return left.runId.localeCompare(right.runId);
  });
  if (!runs.length) {
    section.hidden = true;
    meta.textContent = "0 个编排";
    list.replaceChildren();
    if (empty) empty.hidden = false;
    renderCompactCalls();
    return;
  }

  const running = runs.filter((run) => orchestrationStateName(run.status) === "running").length;
  meta.textContent = String(runs.length) + " 个编排" + (running ? " · " + String(running) + " 运行中" : "");
  section.hidden = false;
  $("#agents-empty-state").hidden = runs.length > 0;
  list.replaceChildren();

  runs.forEach((run) => {
    const stateName = orchestrationStateName(run.status);
    const details = document.createElement("details");
    details.className = "live-orchestration " + stateName;
    details.open = run.expanded !== false;
    details.addEventListener("toggle", () => { run.expanded = details.open; });

    const summary = document.createElement("summary");
    const summaryIcon = document.createElement("span");
    summaryIcon.className = "live-orchestration-icon";
    summaryIcon.textContent = orchestrationStateIcon(run.status);
    const summaryCopy = document.createElement("span");
    summaryCopy.className = "live-orchestration-summary";
    const title = document.createElement("strong");
    title.textContent = "编排层级 " + String(run.depth) + (run.parentRunId ? " · 嵌套" : "") + " · " + orchestrationStateLabel(run.status);
    const task = document.createElement("span");
    task.textContent = run.taskPreview || run.stage || "子 Agent 编排";
    summaryCopy.append(title, task);
    summary.append(summaryIcon, summaryCopy);
    details.appendChild(summary);

    const body = document.createElement("div");
    body.className = "live-orchestration-body";
    if (run.rationale) {
      const rationale = document.createElement("p");
      rationale.className = "live-orchestration-rationale";
      rationale.textContent = "分工说明：" + run.rationale;
      body.appendChild(rationale);
    }
    const stage = document.createElement("p");
    stage.className = "live-orchestration-stage";
    stage.textContent = run.stage || "等待执行";
    body.appendChild(stage);

    const agentList = document.createElement("div");
    agentList.className = "live-agent-list";
    const orderedAgents = [
      ...run.planOrder.map((id) => run.agents.get(id)).filter(Boolean),
      ...[...run.agents.values()].filter((agent) => !run.planOrder.includes(agent.id)),
    ];
    if (orderedAgents.length) body.appendChild(renderOrchestrationDependencyGraph(orderedAgents));
    orderedAgents.forEach((agent) => agentList.appendChild(renderOrchestrationAgent(agent)));
    if (orderedAgents.length) body.appendChild(agentList);
    details.appendChild(body);
    list.appendChild(details);
  });
  renderCompactCalls();
}

function applyOrchestrationLifecycleEvent(type, payload, timestamp) {
  if (!type.startsWith("orchestration.") && !type.startsWith("agent.")) return false;
  const run = getOrchestrationRun(payload.run_id);
  if (!run) return true;

  if (type === "orchestration.started") {
    run.parentRunId = payload.parent_run_id || null;
    run.depth = Number(payload.depth) || 1;
    run.taskPreview = String(payload.task_preview || run.taskPreview);
    run.requestedAgents = Array.isArray(payload.requested_agents) ? [...payload.requested_agents] : [];
    run.status = "RUNNING";
    run.stage = "正在生成子 Agent 分工";
    updateExecutionStatus("running", "执行中", "正在启动子 Agent 编排");
    appendExecutionEvent("running", "启动子 Agent 编排", "层级 " + String(run.depth), timestamp);
  } else if (type === "orchestration.plan_created") {
    run.rationale = String(payload.rationale_preview || "");
    run.status = "RUNNING";
    run.stage = "已生成 " + String(payload.total_agents || 0) + " 个子 Agent 步骤";
    (payload.steps || []).forEach((step) => {
      const agent = upsertOrchestrationAgent(run, step);
      if (agent) agent.status = "PENDING";
    });
    appendExecutionEvent("running", "已生成子 Agent 分工", String(payload.total_agents || 0) + " 个步骤", timestamp);
  } else if (type === "agent.scheduled") {
    const agent = upsertOrchestrationAgent(run, payload);
    if (agent) {
      agent.status = "SCHEDULED";
      agent.stage = "已排队，等待依赖完成";
    }
    run.status = "RUNNING";
    appendExecutionEvent("running", "已安排子 Agent：" + String(payload.agent_profile || ""), "步骤 " + String((Number(payload.step_index) || 0) + 1), timestamp);
  } else if (type === "agent.started") {
    const agent = upsertOrchestrationAgent(run, payload);
    if (agent) {
      agent.status = "RUNNING";
      agent.stage = "正在执行子任务";
    }
    run.status = "RUNNING";
    updateExecutionStatus("running", "执行中", "子 Agent 正在执行：" + String(payload.agent_profile || ""));
    appendExecutionEvent("running", "子 Agent 开始执行：" + String(payload.agent_profile || ""), "步骤 " + String((Number(payload.step_index) || 0) + 1), timestamp);
  } else if (type === "agent.llm.started") {
    const agent = upsertOrchestrationAgent(run, payload);
    if (agent) {
      agent.status = "RUNNING";
      agent.stage = "正在请求模型第 " + String(payload.step || "?") + " 轮决策";
    }
    appendExecutionEvent("running", String(payload.agent_profile || "子 Agent") + " 开始模型决策", "第 " + String(payload.step || "?") + " 轮", timestamp);
  } else if (type === "agent.llm.retry_scheduled") {
    const agent = upsertOrchestrationAgent(run, payload);
    const attempt = Math.max(1, Number(payload.attempt) || 1);
    const maxRetries = Math.max(0, Number(payload.max_retries) || 0);
    const error = toolErrorText(payload.error);
    if (agent) {
      agent.status = "RUNNING";
      agent.stage = "模型暂时失败，正在第 " + String(attempt) + "/" + String(maxRetries) + " 次重试";
    }
    appendExecutionEvent("warning", String(payload.agent_profile || "子 Agent") + " 模型准备重试", "第 " + String(payload.step || "?") + " 轮 · 第 " + String(attempt) + "/" + String(maxRetries) + " 次" + (error ? " · " + error : ""), timestamp);
  } else if (type === "agent.llm.failed") {
    const agent = upsertOrchestrationAgent(run, payload);
    const error = toolErrorText(payload.error);
    if (agent) {
      agent.status = "FAILED";
      agent.stage = "模型调用失败";
      agent.error = error || agent.error;
    }
    appendExecutionEvent("error", String(payload.agent_profile || "子 Agent") + " 模型调用失败", error, timestamp);
  } else if (type === "agent.decision") {
    const agent = upsertOrchestrationAgent(run, payload);
    if (agent) {
      agent.status = "RUNNING";
      agent.stage = payload.is_final ? "已生成子任务结果" : "已决定下一步工具调用";
      if (payload.is_final && payload.content_preview) agent.answerPreview = String(payload.content_preview);
    }
    recordModelUsage(payload, "agent:" + String(run.runId) + ":" + String(payload.agent_instance_id || "unknown") + ":" + String(payload.step || "unknown"));
    appendExecutionEvent(
      "running",
      String(payload.agent_profile || "子 Agent") + " 完成模型决策",
      modelUsageDetail(payload, payload.is_final ? "正在整理子任务结果" : "已确定下一步"),
      timestamp
    );
  } else if (type === "agent.tool.started" || type === "agent.tool.retry_scheduled" || type === "agent.tool.completed") {
    const agent = upsertOrchestrationAgent(run, payload);
    const completed = type === "agent.tool.completed";
    recordToolFact(
      { ...payload, tool_call_id: String(payload.agent_instance_id || "unknown") + ":" + String(payload.tool_call_id || payload.event_id || "unknown") },
      completed ? (payload.success === false ? "failed" : "success") : "pending",
      payload.event_id
    );
    const retrying = type === "agent.tool.retry_scheduled";
    if (agent) {
      agent.status = "RUNNING";
      if (retrying) {
        const attempt = Math.max(1, Number(payload.attempt) || 1);
        const maxRetries = Math.max(0, Number(payload.max_retries) || 0);
        const error = payload.error && payload.error.message ? String(payload.error.message) : "";
        agent.stage = "工具暂时失败，正在重试：" + String(payload.tool || "");
        agent.toolSummary = String(payload.tool || "工具") + "：第 " + String(attempt) + "/" + String(maxRetries) + " 次重试" + (error ? " · " + error : "");
      } else {
        agent.stage = (completed ? "已完成工具调用：" : "正在调用工具：") + String(payload.tool || "");
      }
      if (completed) {
        const toolCallId = String(payload.tool_call_id || "");
        if (!toolCallId || !agent.completedToolCallIds.has(toolCallId)) {
          if (toolCallId) agent.completedToolCallIds.add(toolCallId);
          agent.toolCalls = agent.completedToolCallIds.size || Number(agent.toolCalls || 0) + 1;
        }
        const error = payload.error && payload.error.message ? payload.error.message : "";
        const output = payload.has_output ? String(payload.data ?? "") : "无输出";
        agent.toolSummary = String(payload.tool || "工具") + "：" + (payload.success === false ? error || "执行失败" : output);
      }
    }
    const kind = retrying ? "warning" : completed && payload.success === false ? "error" : completed ? "success" : "running";
    const title = retrying ? "子 Agent 工具准备重试：" : completed ? "子 Agent 工具完成：" : "子 Agent 调用工具：";
    const retryDetail = retrying ? "第 " + String(payload.attempt || 1) + "/" + String(payload.max_retries || "?") + " 次" : String(payload.agent_profile || "");
    appendExecutionEvent(kind, title + String(payload.tool || ""), retryDetail, timestamp);
  } else if (type === "agent.completed" || type === "agent.failed" || type === "agent.skipped") {
    const agent = upsertOrchestrationAgent(run, payload);
    if (agent) {
      agent.status = payload.status || (type === "agent.completed" ? "SUCCEEDED" : type === "agent.skipped" ? "SKIPPED" : "FAILED");
      agent.stage = orchestrationStateLabel(agent.status);
      agent.answerPreview = String(payload.answer_preview || agent.answerPreview || "");
      agent.error = String(payload.error || "");
      agent.durationMs = payload.duration_ms != null ? Number(payload.duration_ms) : agent.durationMs;
      agent.toolCalls = Number(payload.tool_calls || agent.toolCalls || 0);
    }
    const kind = type === "agent.completed" ? "success" : type === "agent.skipped" ? "warning" : "error";
    appendExecutionEvent(kind, "子 Agent " + orchestrationStateLabel(agent ? agent.status : payload.status) + "：" + String(payload.agent_profile || ""), payload.error || "", timestamp);
  } else if (type === "orchestration.synthesis_started") {
    run.status = "RUNNING";
    run.stage = "子任务已结束，正在整合结果";
    updateExecutionStatus("running", "执行中", "正在整合子 Agent 结果");
    appendExecutionEvent("running", "开始整合子 Agent 结果", "共 " + String(payload.total_agents || 0) + " 个子任务", timestamp);
  } else if (type === "orchestration.completed") {
    run.status = payload.status || "SUCCEEDED";
    run.durationMs = payload.duration_ms != null ? Number(payload.duration_ms) : null;
    run.stage = orchestrationStateLabel(run.status) + (run.durationMs != null ? " · 耗时 " + formatMs(run.durationMs) : "");
    appendExecutionEvent(
      run.status === "FAILED" ? "error" : run.status === "PARTIAL" ? "warning" : "success",
      "子 Agent 编排" + orchestrationStateLabel(run.status),
      run.durationMs != null ? "耗时 " + formatMs(run.durationMs) : "",
      timestamp
    );
  } else if (type === "orchestration.failed") {
    run.status = "FAILED";
    run.stage = "编排失败";
    appendExecutionEvent("error", "子 Agent 编排失败", payload.error || "", timestamp);
  } else {
    return false;
  }
  renderLiveOrchestrations();
  return true;
}

function canOpenExecutionHistory(executionId) {
  return !state.streaming || executionId === state.executionId;
}

function renderExecutionHistory(records) {
  state.executionHistory = Array.isArray(records) ? records : [];
  renderResourceWorkspace();
  const container = $("#execution-history");
  const count = $("#execution-history-count");
  if (!container || !count) return;
  count.textContent = String(state.executionHistory.length);
  container.replaceChildren();

  if (!state.executionHistory.length) {
    const empty = document.createElement("p");
    empty.className = "timeline-empty";
    empty.textContent = "当前会话还没有可回放的执行记录。";
    container.appendChild(empty);
    return;
  }

  state.executionHistory.forEach((record) => {
    const item = document.createElement("button");
    item.type = "button";
    item.className = "execution-history-item";
    item.dataset.executionId = record.execution_id;
    if (record.execution_id === state.executionId) item.classList.add("active");
    const selectable = canOpenExecutionHistory(record.execution_id);
    item.disabled = !selectable;
    item.title = selectable ? "回放此执行记录" : "当前运行仍在接续；结束后可切换其他执行记录";

    const title = document.createElement("span");
    title.className = "execution-history-title";
    title.textContent = record.input_preview || record.execution_id;

    const meta = document.createElement("span");
    meta.className = "execution-history-meta";
    meta.textContent = formatMode(record.agent_mode) + " · " + formatStoredStatus(record.status)
      + (record.created_at ? " · " + formatStoredTime(record.created_at) : "");

    item.append(title, meta);
    item.addEventListener("click", () => {
      if (canOpenExecutionHistory(record.execution_id)) openExecutionHistory(record.execution_id);
    });
    container.appendChild(item);
  });
}

function formatStoredStatus(status) {
  const labels = {
    QUEUED: "排队中",
    RUNNING: "执行中",
    SUCCEEDED: "已完成",
    FAILED: "失败",
    CANCELLED: "已取消",
    INTERRUPTED: "已中断",
  };
  return labels[status] || status || "未知";
}

function isActiveExecutionStatus(status) {
  return status === "QUEUED" || status === "RUNNING";
}

function terminalExecutionOutcome(status) {
  return TERMINAL_EXECUTION_OUTCOMES[status] || null;
}

function formatStoredTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

async function loadExecutionHistory(sessionId, viewVersion = state.executionViewVersion) {
  const container = $("#execution-history");
  if (!sessionId) {
    if (isCurrentExecutionViewVersion(viewVersion)) renderExecutionHistory([]);
    return;
  }
  try {
    const response = await fetch("/api/web/sessions/" + encodeURIComponent(sessionId) + "/executions");
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    const data = await response.json();
    if (!isCurrentExecutionViewVersion(viewVersion) || state.sessionId !== sessionId) return;
    renderExecutionHistory(data.executions || []);
  } catch (error) {
    if (!isCurrentExecutionViewVersion(viewVersion) || state.sessionId !== sessionId) return;
    if (container) {
      container.replaceChildren();
      const message = document.createElement("p");
      message.className = "timeline-empty";
      message.textContent = "无法读取执行记录：" + error.message;
      container.appendChild(message);
    }
  }
}

async function fetchAllExecutionEvents(executionId) {
  const events = [];
  let afterSeq = 0;
  while (true) {
    const response = await fetch(
      "/api/web/executions/" + encodeURIComponent(executionId)
        + "/events?after_seq=" + String(afterSeq) + "&limit=1000"
    );
    if (!response.ok) throw new Error("执行事件读取失败");
    const page = await response.json();
    const batch = page.events || [];
    events.push(...batch);
    if (!batch.length || afterSeq >= Number(page.last_seq || 0)) break;
    afterSeq = Number(batch[batch.length - 1].seq || afterSeq);
    if (afterSeq >= Number(page.last_seq || 0)) break;
  }
  return events;
}

async function openExecutionHistory(executionId) {
  // 单一工作台状态不能同时承载两条活动流；锁定可避免晚到事件污染历史回放。
  // 当前运行已经有 SSE 订阅时，重复点击只保留当前视图，不能使订阅版本失效。
  if (state.streaming && executionId === state.executionId) return;
  if (!canOpenExecutionHistory(executionId)) return;
  const viewVersion = advanceExecutionViewVersion();
  try {
    const response = await fetch("/api/web/executions/" + encodeURIComponent(executionId));
    if (!response.ok) throw new Error("执行记录读取失败");
    const record = await response.json();
    if (!isCurrentExecutionViewVersion(viewVersion) || state.streaming) return;
    const events = await fetchAllExecutionEvents(executionId);
    if (!isCurrentExecutionViewVersion(viewVersion) || state.streaming) return;
    replayExecution(record, events);
    renderExecutionHistory(state.executionHistory);
    if (isActiveExecutionStatus(record.status)) {
      resumeExecutionEvents(record, events.length ? events[events.length - 1].seq : 0, viewVersion);
    }
  } catch (error) {
    if (!isCurrentExecutionViewVersion(viewVersion)) return;
    addErrorMsg("加载执行记录失败: " + error.message);
  }
}

async function resumeExecutionEvents(record, afterSeq, viewVersion = state.executionViewVersion) {
  // 已有浏览器流在消费该执行时不再创建第二个订阅，避免时间线重复。
  if (state.streaming || !isCurrentExecutionViewVersion(viewVersion) || state.executionId !== record.execution_id) return;
  const controller = new AbortController();
  state.abortCtrl = controller;
  setStreaming(true);
  const stop = $("#stop");
  if (stop) {
    stop.disabled = false;
    stop.style.display = "block";
  }
  let completedSessionId = null;

  try {
    const response = await fetch(
      "/api/web/executions/" + encodeURIComponent(record.execution_id)
        + "/stream?after_seq=" + String(afterSeq),
      { signal: controller.signal }
    );
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== record.execution_id) return;
    setConnStatus(true);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let index;
      while ((index = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, index);
        buffer = buffer.slice(index + 2);
        if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== record.execution_id) {
          controller.abort();
          return;
        }
        replayExecutionFrame(frame, viewVersion);
      }
    }
    const snapshotResponse = await fetch("/api/web/executions/" + encodeURIComponent(record.execution_id));
    if (snapshotResponse.ok) {
      const snapshot = await snapshotResponse.json();
      if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== record.execution_id) return;
      if (!isActiveExecutionStatus(snapshot.status)) {
        const finalEvents = await fetchAllExecutionEvents(record.execution_id);
        if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== record.execution_id) return;
        replayExecution(snapshot, finalEvents);
        completedSessionId = snapshot.session_id || null;
      }
    }
  } catch (error) {
    if (error.name !== "AbortError" && isCurrentExecutionViewVersion(viewVersion) && state.executionId === record.execution_id) {
      setConnStatus(false);
      markExecutionStateUncertain("执行事件连接已断开", "服务端任务可能仍在执行；可稍后从执行历史继续接续。");
    }
  } finally {
    if (state.abortCtrl === controller && isCurrentExecutionViewVersion(viewVersion) && state.executionId === record.execution_id) {
      $("#stop").style.display = "none";
      setStreaming(false);
      if (completedSessionId) void openSession(completedSessionId);
      else loadExecutionHistory(record.session_id, viewVersion);
    }
  }
}

function replayExecutionFrame(frame, viewVersion = state.executionViewVersion) {
  if (!isCurrentExecutionViewVersion(viewVersion)) return;
  const lines = frame.split("\n");
  const eventLine = lines.find((line) => line.startsWith("event:"));
  const dataLine = lines.find((line) => line.startsWith("data:"));
  if (!eventLine || !dataLine) return;
  try {
    const payload = JSON.parse(dataLine.slice(5).trim());
    replayExecutionEvent({
      event_type: eventLine.slice(6).trim(),
      payload,
      timestamp: payload.timestamp,
    });
  } catch (error) {
    // 单条回放数据异常不应中断后续可用事件。
  }
}

function replayExecution(record, events) {
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionId = record.execution_id;
  setAgentMode(record.agent_mode || state.agentMode);
  state.sessionId = record.session_id || state.sessionId;
  renderExecutionHistory(state.executionHistory);
  state.executionQueuedAt = timestampToMillis(record.created_at);
  state.executionStartedAt = timestampToMillis(record.started_at);
  state.executionFinishedAt = timestampToMillis(record.finished_at);
  state.executionSteps = 0;
  state.executionTools = 0;
  resetRunSummaryFacts();
  resetExecutionEventCursor();
  clearLivePlan();
  clearLiveOrchestrations();
  clearExecutionContext();
  clearExecutionTrace();

  const title = $("#workspace-title");
  if (title) title.textContent = truncateForWorkspace(record.input_preview || "历史执行");
  const timeline = $("#execution-timeline");
  if (timeline) timeline.replaceChildren();
  $("#timeline-count").textContent = "0";
  updateExecutionStatus("running", "回放中", "正在还原已采集的执行事件");

  events.forEach((event) => replayExecutionEvent(event));
  if (record.trace_id) void fetchExecutionTrace(record.trace_id, state.executionViewVersion);
  const status = record.status;
  if (status === "SUCCEEDED") finishExecution("success", "历史执行已完成");
  else if (status === "FAILED" || status === "INTERRUPTED") finishExecution("error", status === "INTERRUPTED" ? "执行被服务重启中断" : "历史执行失败");
  else if (status === "CANCELLED") finishExecution("cancelled", "历史执行已取消");
  else if (status === "QUEUED") {
    updateExecutionStatus("pending", "排队中", "该运行正在等待可用执行资源；可继续接续事件");
    state.executionTimer = window.setInterval(renderExecutionMetrics, 500);
  } else {
    updateExecutionStatus("running", "执行中", "该运行仍在执行；可通过事件流继续接续");
    state.executionTimer = window.setInterval(renderExecutionMetrics, 500);
  }
  renderExecutionMetrics();
}

function replayExecutionEvent(event) {
  if (!registerExecutionEvent(event)) return;
  const payload = event.payload || {};
  const timestamp = event.timestamp;
  const type = event.event_type;

  if (applyRuntimeLifecycleEvent(type, payload, timestamp) || applyOrchestrationLifecycleEvent(type, payload, timestamp) || applyPlanLifecycleEvent(type, payload, timestamp)) return;

  if (type === "llm.started") {
    appendExecutionEvent("running", "模型开始决策", "第 " + String(payload.step || "?") + " 轮", timestamp);
    return;
  }
  if (type === "llm.retry_scheduled") {
    recordLLMRetry(payload, timestamp);
    return;
  }
  if (type === "llm.failed") {
    recordLLMFailure(payload, timestamp);
    return;
  }
  if (type === "step") {
    state.executionSteps = Math.max(state.executionSteps, Number(payload.step) || 0);
    recordModelUsage(payload, "root:" + String(event.event_id || payload.step || state.executionSteps));
    appendExecutionEvent("running", "模型完成决策", modelUsageDetail(payload, payload.is_final ? "正在组织最终回答" : "已确定下一步"), timestamp);
    return;
  }
  if (type === "tool.started") {
    recordToolFact(payload, "pending", event.event_id);
    markToolRunning(payload);
    appendExecutionEvent("running", "开始调用工具：" + String(payload.tool || ""), "", timestamp);
    return;
  }
  if (type === "tool.retry_scheduled") {
    recordToolRetry(payload, timestamp);
    return;
  }
  if (type === "tool_result") {
    recordToolFact(payload, payload.success === false ? "failed" : "success", event.event_id);
    state.executionTools += 1;
    appendExecutionEvent(
      payload.success === false ? "error" : "success",
      payload.success === false ? "工具执行失败：" + String(payload.tool || "") : "工具执行完成：" + String(payload.tool || ""),
      payload.duration_ms != null ? "耗时 " + formatMs(payload.duration_ms) : "",
      timestamp
    );
    return;
  }
  if (type === "final") {
    appendExecutionEvent("running", "已生成最终回答", "等待执行记录归档", timestamp);
    return;
  }
  if (type === "done") {
    setLivePlan(payload.plan, payload.plan_version, payload.plan_revisions);
    setExecutionTrace(payload.trace, payload.trace_id);
    appendExecutionEvent("success", "任务已完成", "", timestamp);
    return;
  }
  if (type === "execution.queued") {
    markExecutionQueued(timestamp);
    updateExecutionStatus("pending", "排队中", executionQueueDetail(payload));
    appendExecutionEvent("pending", "任务已进入执行队列", executionQueueDetail(payload), timestamp);
    return;
  }
  if (type === "execution.cancel_requested") {
    appendExecutionEvent("warning", "已请求停止", "等待服务端确认", timestamp);
    return;
  }
  if (type === "execution.cancelled") {
    appendExecutionEvent("warning", "服务端确认已取消", "", timestamp);
    return;
  }
  if (type === "execution.failed") {
    appendExecutionEvent("error", "执行失败", payload.message || "", timestamp);
    return;
  }
  if (type === "execution.completed") {
    appendExecutionEvent("success", "执行记录已保存", payload.trace_id ? "Trace " + String(payload.trace_id).slice(-12) : "", timestamp);
    return;
  }
  if (type === "execution.started") {
    markExecutionStarted(timestamp);
    updateExecutionStatus("running", "执行中", "正在准备执行环境");
    appendExecutionEvent("running", "任务开始执行", payload.mode ? "模式：" + formatMode(payload.mode) : "", timestamp);
  }
}


/* ================= 能力列表 ================= */
async function loadCapabilities() {
  try {
    const responses = await Promise.all([
      fetch("/api/web/tools"),
      fetch("/api/web/skills"),
      fetch("/api/web/mcp"),
    ]);
    if (responses.some((response) => !response.ok)) {
      throw new Error("能力接口返回失败");
    }
    const [tools, skills, mcp] = await Promise.all(responses.map((response) => response.json()));
    state.resources.tools = Array.isArray(tools.tools) ? tools.tools : [];
    state.resources.skills = Array.isArray(skills.skills) ? skills.skills : [];
    state.resources.mcp = Array.isArray(mcp.servers) ? mcp.servers : [];
    renderList("#tool-list", (tools.tools || []).map((t) => ({
      text: t.name + (t.risk_level !== "low" ? ` [${t.risk_level}]` : ""),
      title: t.description,
    })));
    $("#tool-count").textContent = tools.count;
    renderList("#skill-list", (skills.skills || []).map((s) => ({
      text: s.name, title: s.description,
    })));
    $("#skill-count").textContent = skills.count;
    renderList("#mcp-list", (mcp.servers || []).map((s) => ({
      text: `${s.name} (${s.tool_count})`, title: `transport: ${s.transport}`,
    })));
    $("#mcp-count").textContent = mcp.count;
    renderResourceWorkspace();
    return true;
  } catch (e) {
    return false;
  }
}

/* ================= 子 Agent 档案（动态注册） ================= */
async function loadAgents() {
  try {
    const data = await fetch("/api/web/agents").then((r) => r.json());
    state.resources.agents = Array.isArray(data.agents) ? data.agents : [];
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
    renderResourceWorkspace();
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
function loadSessionMetadata() {
  try {
    const parsed = JSON.parse(localStorage.getItem(SESSION_METADATA_STORAGE_KEY) || "{}");
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) return {};
    const cleaned = {};
    Object.entries(parsed).forEach(([sessionId, metadata]) => {
      if (!metadata || typeof metadata !== "object") return;
      const alias = typeof metadata.alias === "string" ? metadata.alias.trim().slice(0, 80) : "";
      const pinned = metadata.pinned === true;
      if (alias || pinned) cleaned[sessionId] = { alias, pinned };
    });
    return cleaned;
  } catch (error) {
    return {};
  }
}

function saveSessionMetadata() {
  try {
    localStorage.setItem(SESSION_METADATA_STORAGE_KEY, JSON.stringify(state.sessionMetadata));
  } catch (error) {
    // 浏览器禁用或写满 localStorage 时，仍保留当前页面内的偏好。
  }
}

function sessionMetadataFor(sessionId) {
  return state.sessionMetadata[sessionId] || { alias: "", pinned: false };
}

function sessionTitle(session) {
  const alias = sessionMetadataFor(session.session_id).alias;
  const cached = state.sessionContentCache.get(session.session_id);
  return alias || (cached && cached.title) || session.session_id.slice(-16);
}

function sessionPreview(session) {
  const cached = state.sessionContentCache.get(session.session_id);
  return cached && cached.preview ? cached.preview : "打开后加载消息预览";
}

function sessionMessageText(message) {
  if (!message || message.content === null || message.content === undefined) return "";
  const raw = typeof message.content === "string" ? message.content : formatJsonValue(message.content);
  return raw.replace(/\s+/g, " ").trim();
}

function cacheSessionMessages(sessionId, messages) {
  if (!sessionId || !Array.isArray(messages)) return;
  const existing = state.sessionContentCache.get(sessionId);
  const firstUser = messages.find((message) => message && message.role === "user");
  const firstAssistant = messages.find((message) => message && message.role === "assistant" && sessionMessageText(message));
  const title = sessionMessageText(firstUser).slice(0, 48);
  const preview = sessionMessageText(firstAssistant).slice(0, 72);
  if (!title && !preview) return;
  state.sessionContentCache.set(sessionId, {
    title: (existing && existing.title) || title || sessionId.slice(-16),
    preview: (existing && existing.preview) || preview || "尚无 Assistant 回复",
  });
  renderSessionList();
  if (!$("#search-dialog")?.hidden) renderLoadedSearchResults();
}

function updateSessionMetadata(sessionId, patch) {
  const current = sessionMetadataFor(sessionId);
  const next = {
    alias: typeof patch.alias === "string" ? patch.alias.trim().slice(0, 80) : current.alias,
    pinned: typeof patch.pinned === "boolean" ? patch.pinned : current.pinned,
  };
  if (next.alias || next.pinned) state.sessionMetadata[sessionId] = next;
  else delete state.sessionMetadata[sessionId];
  saveSessionMetadata();
  renderSessionList();
  if (!$("#search-dialog")?.hidden) renderLoadedSearchResults();
}

function sessionGroupLabel(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "较早";
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const sessionDay = new Date(date);
  sessionDay.setHours(0, 0, 0, 0);
  const daysAgo = Math.round((today.getTime() - sessionDay.getTime()) / 86400000);
  if (daysAgo <= 0) return "今天";
  if (daysAgo === 1) return "昨天";
  if (daysAgo <= 6) return "最近 7 天";
  return "较早";
}

function recentSessionCutoff() {
  const cutoff = new Date();
  cutoff.setHours(0, 0, 0, 0);
  cutoff.setDate(cutoff.getDate() - 6);
  return cutoff.getTime();
}

function filteredSessions() {
  const filter = state.sessionFilter;
  return state.sessions.filter((session) => {
    const metadata = sessionMetadataFor(session.session_id);
    if (filter === "pinned") return metadata.pinned;
    if (filter === "recent") {
      const date = new Date(session.updated_at);
      if (Number.isNaN(date.getTime())) return false;
      return date.getTime() >= recentSessionCutoff();
    }
    return true;
  });
}

function renderSessionList() {
  const ul = $("#session-list");
  const count = $("#session-count");
  const heading = $("#sessions-heading");
  if (!ul || !count || !heading) return;
  const sessions = filteredSessions();
  count.textContent = String(sessions.length);
  heading.textContent = state.sessionFilter === "pinned" ? "本地置顶" : state.sessionFilter === "all" ? "已加载会话" : "最近会话";
  ul.replaceChildren();
  const groups = new Map();
  sessions.forEach((session) => {
    const label = state.sessionFilter === "pinned" ? "已置顶" : sessionGroupLabel(session.updated_at);
    const group = groups.get(label) || [];
    group.push(session);
    groups.set(label, group);
  });
  groups.forEach((group, label) => {
    const groupHeading = document.createElement("li");
    groupHeading.className = "session-group-heading";
    groupHeading.textContent = label;
    groupHeading.setAttribute("aria-hidden", "true");
    ul.appendChild(groupHeading);
    group.forEach((session) => {
      const metadata = sessionMetadataFor(session.session_id);
      const li = document.createElement("li");
      li.className = "session-item";
      li.dataset.sessionId = session.session_id;
      li.innerHTML = `
        <button class="session-open" type="button" data-session-open aria-label="打开会话：${esc(sessionTitle(session))}">
          ${uiIconMarkup("chat")}
          <span class="session-copy"><span class="si-name">${esc(sessionTitle(session))}</span><span class="si-preview">${esc(sessionPreview(session))}</span></span>
          <span class="si-time">${esc((session.updated_at || "").slice(11, 16))}</span>
        </button>
        <span class="session-actions">
          <button class="si-pin${metadata.pinned ? " active" : ""}" type="button" data-session-pin title="${metadata.pinned ? "取消本地置顶" : "本地置顶"}" aria-label="${metadata.pinned ? "取消本地置顶" : "本地置顶"}">${uiIconMarkup("pin")}</button>
          <button class="si-alias" type="button" data-session-alias title="编辑本地名称" aria-label="编辑本地名称">${uiIconMarkup("edit")}</button>
          <button class="si-del" type="button" data-session-delete title="删除会话" aria-label="删除会话">${uiIconMarkup("x")}</button>
        </span>
      `;
      if (state.sessionId === session.session_id) li.classList.add("active");
      const sessionLocked = !canChangeSession();
      li.setAttribute("aria-disabled", String(sessionLocked));
      if (sessionLocked) li.title = "当前任务仍在执行；结束或停止后可切换会话";
      const openButton = li.querySelector("[data-session-open]");
      openButton.disabled = sessionLocked;
      openButton.addEventListener("click", () => {
        if (canChangeSession()) void openSession(session.session_id);
      });
      li.querySelector("[data-session-pin]").addEventListener("click", () => {
        updateSessionMetadata(session.session_id, { pinned: !metadata.pinned });
      });
      li.querySelector("[data-session-alias]").addEventListener("click", () => {
        const alias = window.prompt("仅此浏览器显示的会话名称", metadata.alias || "");
        if (alias !== null) updateSessionMetadata(session.session_id, { alias });
      });
      li.querySelector("[data-session-delete]").addEventListener("click", () => {
        if (canChangeSession()) void deleteSession(session.session_id);
      });
      ul.appendChild(li);
    });
  });
  syncSessionNavigationState();
}

async function loadSessions() {
  try {
    const data = await fetch("/api/web/sessions").then((r) => r.json());
    state.sessions = Array.isArray(data.sessions) ? data.sessions : [];
    renderSessionList();
    if (!$("#search-dialog")?.hidden) renderLoadedSearchResults();
  } catch (e) { /* 忽略 */ }
}

async function openSession(sessionId) {
  if (!canChangeSession()) return;
  const viewVersion = advanceExecutionViewVersion();
  state.sessionId = sessionId;
  setNavigationOpen(false);
  // 高亮
  $$("#session-list .session-item").forEach((el) => el.classList.toggle("active", el.dataset.sessionId === sessionId));
  // 加载消息
  try {
    const data = await fetch(`/api/web/sessions/${sessionId}/messages`).then((r) => r.json());
    if (!isCurrentExecutionViewVersion(viewVersion) || state.sessionId !== sessionId) return;
    cacheSessionMessages(sessionId, data.messages || []);
    renderHistory(data.messages || []);
    const session = state.sessions.find((item) => item.session_id === sessionId) || { session_id: sessionId };
    const title = $("#workspace-title");
    if (title) title.textContent = sessionTitle(session);
    setStatus(`会话 ${sessionId.slice(-12)}`);
  } catch (e) {
    if (!isCurrentExecutionViewVersion(viewVersion) || state.sessionId !== sessionId) return;
    addErrorMsg("加载会话失败: " + e.message);
  }
  if (!isCurrentExecutionViewVersion(viewVersion) || state.sessionId !== sessionId) return;
  loadOrchestrations(sessionId, viewVersion);
  loadExecutionHistory(sessionId, viewVersion);
}

/* ================= 编排记录（委派结果持久化） ================= */
async function loadOrchestrations(sessionId, viewVersion = state.executionViewVersion) {
  try {
    const q = sessionId ? `?session_id=${encodeURIComponent(sessionId)}` : "";
    const data = await fetch(`/api/web/orchestrations${q}`).then((r) => r.json());
    if (!isCurrentExecutionViewVersion(viewVersion) || (sessionId && state.sessionId !== sessionId)) return;
    const ul = $("#orch-list");
    ul.innerHTML = "";
    $("#orch-count").textContent = data.count || 0;
    (data.runs || []).forEach((run) => {
      const li = document.createElement("li");
      li.className = "orch-item";
      const icon = run.status === "SUCCEEDED" ? "✅" : run.status === "PARTIAL" ? "⚠️" : "❌";
      li.innerHTML = `
        <span class="si-icon">${icon}</span>
        <span class="si-name">${esc(run.task.slice(0, 18)) || "(无任务)"}</span>
        <span class="si-time">${run.depth > 1 ? `L${run.depth} · ` : ""}${esc(run.created_at.slice(11, 19))}</span>
      `;
      li.title = `${run.task}\n状态: ${run.status} · 子 Agent: ${run.agent_count} · ${(run.duration_ms / 1000).toFixed(1)}s`;
      li.dataset.runId = run.run_id;
      li.addEventListener("click", () => openOrchestrationDetail(run.run_id));
      ul.appendChild(li);
    });
  } catch (e) { /* 忽略 */ }
}

async function openOrchestrationDetail(runId) {
  try {
    const r = await fetch(`/api/web/orchestrations/${encodeURIComponent(runId)}`);
    if (!r.ok) throw new Error(`HTTP ${r.status}`);
    const data = await r.json();
    setPrimaryView("chat");
    setInspectorSection("execution");
    setInspectorTab("agents");
    setInspectorCollapsed(false, true);
    hydrateHistoricalOrchestration(data);
    renderLiveOrchestrations();
  } catch (e) {
    addErrorMsg("加载编排详情失败: " + e.message);
  }
}

function hydrateHistoricalOrchestration(data) {
  clearLiveOrchestrations();
  const run = getOrchestrationRun(data.run_id);
  run.depth = Number(data.depth) || 1;
  run.taskPreview = String(data.task || "历史编排");
  run.rationale = String(data.plan?.rationale || "");
  run.status = data.status || "PENDING";
  run.durationMs = data.duration_ms != null ? Number(data.duration_ms) : null;
  run.stage = orchestrationStateLabel(run.status) + " · 历史记录";
  const steps = Array.isArray(data.plan?.steps) ? data.plan.steps : [];
  const results = Array.isArray(data.agent_results) ? data.agent_results : [];
  steps.forEach((step, index) => {
    const result = results[index] || results.find((item) => item.agent === step.agent) || {};
    const id = String(data.run_id) + ":step:" + String(index);
    const agent = upsertOrchestrationAgent(run, {
      agent_instance_id: id,
      agent_profile: step.agent,
      step_index: index,
      task_preview: step.task || step.description || "",
      depends_on: Array.isArray(step.depends_on) ? step.depends_on : [],
    });
    agent.status = result.status || (run.status === "SUCCEEDED" ? "SUCCEEDED" : "PENDING");
    agent.answerPreview = String(result.answer || "");
    agent.error = String(result.error || "");
    agent.durationMs = result.duration_ms != null ? Number(result.duration_ms) : null;
    agent.toolCalls = Array.isArray(result.tool_calls) ? result.tool_calls.length : Number(result.tool_calls || 0);
    agent.stage = orchestrationStateLabel(agent.status) + " · 历史记录";
  });
  return run;
}

function renderOrchestrationPanel(data) {
  const panel = document.createElement("div");
  panel.className = "workflow";
  panel.id = "orch-detail-" + data.run_id;

  const header = document.createElement("div");
  header.className = "workflow-header";
  const statusIcon = data.status === "SUCCEEDED" ? "✅" : data.status === "PARTIAL" ? "⚠️" : "❌";
  header.innerHTML = `
    <span class="wf-title">🌐 编排详情</span>
    <span class="wf-meta">${esc(data.run_id.slice(-12))} · ${statusIcon} ${esc(data.status)} · ${formatMs(data.duration_ms)}${data.depth > 1 ? ` · L${data.depth}` : ""}</span>
    <span class="wf-toggle">收起 ▴</span>
  `;
  const body = document.createElement("div");
  body.className = "workflow-body";
  body.innerHTML = renderOrchestrationBody(data);
  header.addEventListener("click", () => {
    panel.classList.toggle("open");
    header.querySelector(".wf-toggle").textContent = panel.classList.contains("open") ? "收起 ▴" : "展开 ▾";
  });
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
    html += `<div class="plan-flow">`;
    data.plan.steps.forEach((s, i) => {
      const deps = s.depends_on && s.depends_on.length ? ` ⬅${s.depends_on.join(",")}` : "";
      html += `<div class="pf-step ok">
        <span class="pf-icon">👤</span>
        <span class="pf-desc"><b>${esc(s.agent)}</b>${esc(deps)}</span>
      </div>`;
      if (s.depends_on && s.depends_on.length) {
        html += `<span class="pf-dependency">${esc(s.depends_on.map((index) => String(Number(index) + 1)).join("、"))} → ${String(i + 1)}</span>`;
      }
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

function bindOrchestrationToggles(panel) {
  // 子 agent 卡片展开
  panel.querySelectorAll(".subagent-card").forEach((card) => {
    card.querySelector(".sa-header").addEventListener("click", () => {
      card.classList.toggle("open");
      card.querySelector(".ts-caret").textContent = card.classList.contains("open") ? "▴" : "▾";
    });
  });
  // 嵌套子编排：点击加载详情
  panel.querySelectorAll(".orch-child").forEach((el) => {
    el.addEventListener("click", () => openOrchestrationDetail(el.dataset.child));
  });
  // Trace 树折叠/详情
  bindTreeToggles(panel);
  bindToolStepToggles(panel);
}

function renderHistory(messages) {
  const callsById = new Map();
  state.activeAssistantElement = null;
  $("#messages").replaceChildren();
  messages.forEach((message) => {
    if (!message || !message.role) return;
    if (message.role === "user") {
      addMessage("user", typeof message.content === "string" ? message.content : formatJsonValue(message.content));
      return;
    }
    if (message.role === "assistant") {
      if (Array.isArray(message.tool_calls)) {
        message.tool_calls.forEach((call) => {
          const id = call && call.id;
          if (!id) return;
          callsById.set(id, {
            id,
            tool: (call.function && call.function.name) || call.name || "tool",
            arguments: parseToolArguments(
              (call.function && call.function.arguments) ?? call.arguments
            ),
          });
        });
      }
      // 含 tool_calls 的决策消息没有用户可读正文；工具卡片会按稳定 ID 呈现。
      if (message.content === null || message.content === undefined || message.content === "") return;
      addMessage("assistant", typeof message.content === "string" ? message.content : formatJsonValue(message.content));
      return;
    }
    if (message.role === "tool") {
      addToolMsg(toolHistoryToCardData(message, callsById.get(message.tool_call_id)));
    }
  });
  scrollToBottom();
}

function parseToolArguments(raw) {
  if (raw === null || raw === undefined || raw === "") return {};
  if (typeof raw !== "string") return raw;
  try { return JSON.parse(raw); } catch (error) { return raw; }
}

function parsePersistedToolResult(raw) {
  try {
    const parsed = typeof raw === "string" ? JSON.parse(raw) : raw;
    return parsed && typeof parsed === "object" ? parsed : null;
  } catch (error) {
    return null;
  }
}

function toolHistoryToCardData(message, declaredCall) {
  const envelope = parsePersistedToolResult(message.content);
  const hasEnvelope = envelope && typeof envelope.success === "boolean";
  const metadata = hasEnvelope && envelope.metadata && typeof envelope.metadata === "object"
    ? envelope.metadata : {};
  const hasOutput = hasEnvelope
    ? envelope.success || (envelope.data !== null && envelope.data !== undefined)
    : Boolean(envelope && hasOwn(envelope, "data"));
  return {
    tool: message.name || (declaredCall && declaredCall.tool) || (envelope && envelope.tool_name) || "tool",
    tool_call_id: message.tool_call_id || (declaredCall && declaredCall.id) || "",
    arguments: (declaredCall && declaredCall.arguments) || metadata.args || {},
    success: hasEnvelope ? envelope.success : undefined,
    status: hasEnvelope ? undefined : "unknown",
    has_output: hasOutput,
    data: envelope && hasOwn(envelope, "data") ? envelope.data : undefined,
    error: envelope && envelope.error ? envelope.error : null,
    duration_ms: metadata.duration_ms,
    retries: metadata.retries,
    historical: true,
  };
}

function newSession() {
  if (!canChangeSession()) return;
  setPrimaryView("chat");
  advanceExecutionViewVersion();
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionTimer = null;
  state.abortCtrl = null;
  state.sessionId = null;
  state.executionId = null;
  state.activeAssistantElement = null;
  resetExecutionEventCursor();
  state.executionQueuedAt = null;
  state.executionStartedAt = null;
  state.executionFinishedAt = null;
  state.executionSteps = 0;
  state.executionTools = 0;
  resetRunSummaryFacts();
  state.followLatest = true;
  clearLivePlan();
  clearLiveOrchestrations();
  clearExecutionContext();
  clearExecutionTrace();
  const title = $("#workspace-title");
  if (title) title.textContent = "开始一个新任务";
  const timeline = $("#execution-timeline");
  if (timeline) timeline.replaceChildren();
  const timelineCount = $("#timeline-count");
  if (timelineCount) timelineCount.textContent = "0";
  const stop = $("#stop");
  if (stop) {
    stop.disabled = false;
    stop.style.display = "none";
  }
  updateExecutionStatus("idle", "准备就绪", "提交任务后，执行过程会实时显示在右侧。");
  renderExecutionMetrics();
  setNavigationOpen(false);
  $("#messages").innerHTML = renderWelcome();
  $$("#session-list .session-item").forEach((el) => el.classList.remove("active"));
  // 清空编排记录列表
  $("#orch-list").innerHTML = "";
  $("#orch-count").textContent = "0";
  renderExecutionHistory([]);
  setStatus("新会话");
}

/* ================= 消息渲染 ================= */
function renderWelcome() {
  return `
    <div class="welcome">
      ${uiIconMarkup("brand", "brand-emblem welcome-emblem")}
      <h2>让 Agent 的每一步都有迹可循</h2>
      <p>发送任务后，可在右侧查看决策轮次、工具调用、计划和 Trace 详情。</p>
      <div class="welcome-tips" aria-label="可用运行方式">
        <span>ReAct：边思考边调用工具</span>
        <span>Plan：先分解，再逐步执行</span>
        <span>支持多 Agent 编排</span>
      </div>
      <div class="welcome-examples" aria-label="任务示例">
        <button type="button" class="welcome-example" data-welcome-prompt="调研 Agent memory 的主流实现方案并比较优缺点">技术调研</button>
        <button type="button" class="welcome-example" data-welcome-prompt="分析一份 CSV 数据，给出关键趋势与异常值">数据分析</button>
        <button type="button" class="welcome-example" data-welcome-prompt="把需求拆分给研究、分析和写作 Agent 并协调结果">多 Agent 编排</button>
      </div>
    </div>`;
}

function addMessage(role, content, meta) {
  if (role === "user") $("#messages .welcome")?.remove();
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
    const mark = document.createElement("span");
    mark.className = "assistant-mark";
    mark.innerHTML = uiIconMarkup("brand", "brand-emblem");
    const body = document.createElement("div");
    body.className = "assistant-content";
    contentEl.className = "md-body";
    contentEl.innerHTML = renderMarkdown(content);
    body.appendChild(contentEl);
    div.append(mark, body);
  } else {
    contentEl.textContent = content;
    div.appendChild(contentEl);
  }
  if (role === "assistant") ensureCopyButton(div);
  $("#messages").appendChild(div);
  scrollToBottom();
  return div;
}

const TOOL_STATUS_VIEW = {
  pending: { className: "pending", icon: "⏳", label: "待执行" },
  running: { className: "running", icon: "◌", label: "执行中" },
  succeeded: { className: "ok", icon: "✓", label: "已完成" },
  failed: { className: "err", icon: "!", label: "失败" },
  unknown: { className: "unknown", icon: "?", label: "未采集" },
};

function hasOwn(value, key) {
  return value != null && Object.prototype.hasOwnProperty.call(value, key);
}

function resolveToolStatus(data) {
  if (data && TOOL_STATUS_VIEW[data.status]) return data.status;
  if (data && data.success === false) return "failed";
  if (data && data.success === true) return "succeeded";
  if (data && data.data === "等待执行...") return "pending";
  return "unknown";
}

function toolStatusMarkup(status) {
  const view = TOOL_STATUS_VIEW[status] || TOOL_STATUS_VIEW.unknown;
  return `<span class="ts-status ${view.className}">${view.icon} ${view.label}</span>`;
}

function formatJsonValue(value) {
  try {
    const serialized = JSON.stringify(value, null, 2);
    return serialized === undefined ? String(value) : serialized;
  } catch (error) {
    return String(value);
  }
}

function formatToolOutput(value, limit = 1600) {
  let text;
  if (value === "") text = "（空字符串）";
  else if (value === null) text = "null";
  else if (value === undefined) text = "（未提供）";
  else if (typeof value === "string") text = value;
  else text = formatJsonValue(value);
  return {
    text: text.length > limit ? text.slice(0, limit) + "\n…（浏览器预览已截断）" : text,
    clientTruncated: text.length > limit,
  };
}

function hasToolOutput(data) {
  if (data && typeof data.has_output === "boolean") return data.has_output;
  if (!data || !hasOwn(data, "data") || data.data === undefined) return false;
  if (data.data === "等待执行..." && resolveToolStatus(data) === "pending") return false;
  return !(data.success === false && data.data === null);
}

function toolErrorText(error) {
  if (!error) return "";
  if (typeof error === "string") return error;
  if (hasOwn(error, "message")) return String(error.message ?? "");
  return formatJsonValue(error);
}

function toolOutputHtml(tool, raw, options = {}) {
  if (tool === "delegate") {
    const summary = parseDelegateOutput(raw);
    if (summary) {
      const icon = summary.status === "SUCCEEDED" ? "✅" : summary.status === "PARTIAL" ? "⚠️" : "❌";
      let html = `<div class="delegate-summary">
        <div class="ds-row"><span>${icon} 编排状态: <b>${esc(summary.status)}</b></span>
        <span>${summary.duration_ms != null ? `⏱ ${formatMs(summary.duration_ms)}` : ""}</span></div>`;
      if (summary.agents && summary.agents.length) {
        html += `<div class="ds-agents">${summary.agents.map((agent) => `
          <span class="ds-agent ${agent.status === "SUCCEEDED" ? "ok" : "err"}">${agent.status === "SUCCEEDED" ? "✅" : "❌"} ${esc(agent.agent)}</span>`).join("")}</div>`;
      }
      if (summary.final_answer !== undefined && summary.final_answer !== null) {
        html += `<div class="ds-answer">${esc(formatToolOutput(summary.final_answer, 600).text)}</div>`;
      }
      html += `</div>`;
      return html + toolOutputNotice(options);
    }
  }
  const output = formatToolOutput(raw);
  return `<pre class="ts-code">${esc(output.text)}</pre>${toolOutputNotice({
    ...options, clientTruncated: output.clientTruncated,
  })}`;
}

function toolOutputNotice(options) {
  const notices = [];
  if (options.output_truncated) notices.push("服务端已截断输出");
  if (options.clientTruncated) notices.push("浏览器已截断预览");
  if (options.output_detail_truncated) notices.push("详情已按保留上限截断");
  if (options.output_detail_unavailable) notices.push("完整详情未采集");
  const notice = notices.length ? `<p class="ts-meta">${esc(notices.join("；"))}</p>` : "";
  const outputId = options.output_id ? String(options.output_id) : "";
  // 只携带不透明 output_id；完整内容在用户显式点击后再按 execution 请求。
  const detailAction = outputId && options.output_available !== false
    ? `<button type="button" class="tool-output-detail-btn" data-output-id="${esc(outputId)}">查看完整输出</button>`
    : "";
  return notice + detailAction;
}

function toolCardBodyHtml(data) {
  const status = resolveToolStatus(data);
  const args = data.arguments === undefined ? {} : data.arguments;
  let outputSection;
  if (hasToolOutput(data)) {
    outputSection = `
      <div class="ts-section">
        <div class="ts-label">📤 输出</div>
        ${toolOutputHtml(data.tool, data.data, data)}
      </div>`;
  } else if (status === "pending") {
    outputSection = `<div class="ts-section"><div class="ts-label">⏳ 尚未开始执行</div></div>`;
  } else if (status === "running") {
    outputSection = `<div class="ts-section"><div class="ts-label">◌ 正在执行，尚未返回输出</div></div>`;
  } else if (status === "unknown") {
    outputSection = `<div class="ts-section"><div class="ts-label">历史记录未采集完整工具输出</div></div>`;
  } else {
    outputSection = `<div class="ts-section"><div class="ts-label">工具未返回输出</div></div>`;
  }
  const errorText = toolErrorText(data.error);
  const metadata = [];
  if (data.duration_ms != null) metadata.push("耗时 " + formatMs(data.duration_ms));
  if (Number(data.retries) > 0) metadata.push("重试 " + String(data.retries) + " 次");
  if (data.output_type) metadata.push("输出类型 " + String(data.output_type));
  return `
    <div class="ts-section">
      <div class="ts-label">📋 参数</div>
      <pre class="ts-code">${esc(formatJsonValue(args))}</pre>
    </div>
    ${outputSection}
    ${errorText ? `
    <div class="ts-section">
      <div class="ts-label err-label">⚠️ 错误</div>
      <pre class="ts-code err-code">${esc(errorText)}</pre>
    </div>` : ""}
    ${metadata.length ? `<p class="ts-meta">${esc(metadata.join(" · "))}</p>` : ""}
  `;
}

function insertExecutionProgress(element) {
  const messages = $("#messages");
  const anchor = state.activeAssistantElement;
  if (anchor && anchor.isConnected && anchor.parentElement === messages) {
    messages.insertBefore(element, anchor);
  } else {
    messages.appendChild(element);
  }
}

function addToolMsg(data) {
  // 工具卡片按稳定 tool_call_id 对应真实生命周期，历史记录复用同一套展示。
  const normalized = { ...data, arguments: data.arguments === undefined ? {} : data.arguments };
  const status = resolveToolStatus(normalized);
  const div = document.createElement("div");
  div.className = "msg tool ts-card";
  div.dataset.toolCallId = normalized.tool_call_id || normalized.id || "";
  div.dataset.toolName = normalized.tool || "";
  div.dataset.executionId = normalized.execution_id || state.executionId || "";
  div.dataset.toolStatus = status;
  const dur = normalized.duration_ms != null ? `⏱ ${formatMs(normalized.duration_ms)}` : "";
  div.innerHTML = `
    <div class="ts-header">
      <span class="ts-icon">${uiIconMarkup(normalized.tool === "delegate" ? "network" : "tool")}</span>
      <span class="ts-name">${esc(normalized.tool || "tool")}</span>
      ${toolStatusMarkup(status)}
      <span class="ts-dur">${esc(dur)}</span>
      <span class="ts-args-preview">${esc(formatJsonValue(normalized.arguments).slice(0, 50))}</span>
      <span class="ts-caret">▾</span>
    </div>
    <div class="ts-body">${toolCardBodyHtml(normalized)}</div>
  `;
  div.querySelector(".ts-header").addEventListener("click", () => {
    div.classList.toggle("open");
    const caret = div.querySelector(".ts-caret");
    caret.textContent = div.classList.contains("open") ? "▴" : "▾";
  });
  bindToolOutputDetailAction(div);
  insertExecutionProgress(div);
  scrollToBottom();
  return div;
}

// delegate 等编排工具的输出：结构化渲染而非原始 JSON
function parseDelegateOutput(raw) {
  try {
    let obj = raw;
    if (typeof raw === "string") {
      try { obj = JSON.parse(raw); } catch (error) { return null; }
    }
    // 工具信封（data 字段包装）
    if (obj && typeof obj === "object" && "data" in obj && !("status" in obj)) {
      const inner = obj.data;
      if (typeof inner === "string") { try { return JSON.parse(inner); } catch (error) { return null; } }
      return inner;
    }
    return obj && typeof obj === "object" && "agent_results" in obj ? obj : null;
  } catch (error) {
    return null;
  }
}

function modelUsageDetail(data, fallback = "") {
  const details = [];
  if (data.model) details.push(String(data.model));
  const usage = data.usage && typeof data.usage === "object" ? data.usage : {};
  const usageParts = [];
  [
    ["prompt_tokens", "输入"],
    ["completion_tokens", "输出"],
    ["total_tokens", "合计"],
  ].forEach(([key, label]) => {
    const value = Number(usage[key]);
    if (Number.isFinite(value) && value >= 0) {
      usageParts.push(label + " " + String(value) + (key === "total_tokens" ? " tokens" : ""));
    }
  });
  details.push(usageParts.length ? usageParts.join(" / ") : "用量未采集");
  if (fallback) details.push(fallback);
  return details.join(" · ");
}

function recordLLMRetry(data, timestamp) {
  const attempt = Math.max(1, Number(data.attempt) || 1);
  const maxRetries = Math.max(0, Number(data.max_retries) || 0);
  const error = toolErrorText(data.error);
  updateExecutionStatus("running", "执行中", "模型暂时失败，正在重试");
  appendExecutionEvent(
    "warning",
    "模型准备重试",
    "第 " + String(data.step || "?") + " 轮 · 第 " + String(attempt) + "/" + String(maxRetries) + " 次" + (error ? " · " + error : ""),
    timestamp
  );
}

function recordLLMFailure(data, timestamp) {
  const error = toolErrorText(data.error);
  appendExecutionEvent(
    "error",
    "模型调用失败",
    "第 " + String(data.step || "?") + " 轮" + (error ? " · " + error : ""),
    timestamp
  );
}

function recordToolRetry(data, timestamp) {
  const attempt = Math.max(1, Number(data.attempt) || 1);
  const maxRetries = Math.max(0, Number(data.max_retries) || 0);
  const error = toolErrorText(data.error);
  updateExecutionStatus(
    "running",
    "执行中",
    "工具暂时失败，正在重试：" + String(data.tool || "")
  );
  appendExecutionEvent(
    "warning",
    "工具准备重试：" + String(data.tool || ""),
    "第 " + String(attempt) + "/" + String(maxRetries) + " 次" + (error ? " · " + error : ""),
    timestamp
  );
  const cards = $$("#messages .ts-card[data-tool-call-id]");
  for (let index = cards.length - 1; index >= 0; index--) {
    const card = cards[index];
    if (!data.tool_call_id || card.dataset.toolCallId !== data.tool_call_id) continue;
    if (card.dataset.toolStatus === "succeeded" || card.dataset.toolStatus === "failed") return;
    card.dataset.toolStatus = "running";
    const status = card.querySelector(".ts-status");
    if (status) status.outerHTML = toolStatusMarkup("running");
    card.dataset.toolRetries = String(attempt);
    const body = card.querySelector(".ts-body");
    if (body) {
      const oldNotice = body.querySelector(".ts-retry-live");
      if (oldNotice) oldNotice.remove();
      const notice = document.createElement("p");
      notice.className = "ts-meta ts-retry-live";
      notice.textContent = "瞬时故障，正在第 " + String(attempt) + "/" + String(maxRetries) + " 次重试" + (error ? "：" + error : "");
      body.appendChild(notice);
    }
    return;
  }
}

function markToolRunning(data) {
  const cards = $$("#messages .ts-card[data-tool-call-id]");
  for (let index = cards.length - 1; index >= 0; index--) {
    const card = cards[index];
    if (!data.tool_call_id || card.dataset.toolCallId !== data.tool_call_id) continue;
    if (card.dataset.toolStatus === "succeeded" || card.dataset.toolStatus === "failed") return;
    card.dataset.toolStatus = "running";
    const status = card.querySelector(".ts-status");
    if (status) status.outerHTML = toolStatusMarkup("running");
    const body = card.querySelector(".ts-body");
    if (body) body.innerHTML = toolCardBodyHtml({ ...data, status: "running" });
    return;
  }
}

// 按稳定 ID 找到对应工具卡片；只有旧事件没有 ID 时才退回到最近的待完成同名卡片。
function updateToolCard(name, data) {
  const cards = $$("#messages .ts-card[data-tool-call-id]");
  for (let index = cards.length - 1; index >= 0; index--) {
    const card = cards[index];
    const callId = data.tool_call_id || "";
    const matchesId = callId && card.dataset.toolCallId === callId;
    const matchesPendingName = !callId && card.dataset.toolName === name
      && ["pending", "running"].includes(card.dataset.toolStatus);
    if (!matchesId && !matchesPendingName) continue;

    const normalized = { ...data, arguments: data.arguments === undefined ? {} : data.arguments };
    const statusState = resolveToolStatus(normalized);
    if (normalized.execution_id) card.dataset.executionId = normalized.execution_id;
    card.dataset.toolStatus = statusState;
    const status = card.querySelector(".ts-status");
    if (status) status.outerHTML = toolStatusMarkup(statusState);
    const duration = card.querySelector(".ts-dur");
    if (duration) duration.textContent = normalized.duration_ms != null ? "⏱ " + formatMs(normalized.duration_ms) : "";
    const body = card.querySelector(".ts-body");
    if (body) body.innerHTML = toolCardBodyHtml(normalized);
    bindToolOutputDetailAction(card);
    return true;
  }
  return false;
}

function bindToolOutputDetailAction(card) {
  const button = card.querySelector(".tool-output-detail-btn");
  if (!button) return;
  button.addEventListener("click", (event) => {
    event.stopPropagation();
    toggleToolOutputDetail(card, button);
  });
}

function formatOutputByteCount(value) {
  const bytes = Number(value) || 0;
  if (bytes < 1024) return String(bytes) + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
}

function appendToolOutputDetail(card, detail) {
  const body = card.querySelector(".ts-body");
  if (!body) return;
  const existing = body.querySelector(".ts-output-detail");
  if (existing) existing.remove();
  const section = document.createElement("section");
  section.className = "ts-section ts-output-detail";
  const label = document.createElement("div");
  label.className = "ts-label";
  label.textContent = "完整输出详情";
  const metadata = document.createElement("p");
  metadata.className = "ts-meta";
  const details = ["原始 " + formatOutputByteCount(detail.original_bytes)];
  if (detail.truncated) details.push("服务端按上限保留了截断预览");
  else details.push("已按需加载");
  metadata.textContent = details.join(" · ");
  const output = document.createElement("pre");
  output.className = "ts-code";
  // 详情内容必须以 textContent 写入，不把工具返回数据当作 HTML。
  output.textContent = formatToolOutput(detail.content, 16000).text;
  section.append(label, metadata, output);
  body.appendChild(section);
}

async function toggleToolOutputDetail(card, button) {
  const openDetail = card.querySelector(".ts-output-detail");
  if (openDetail) {
    openDetail.remove();
    button.textContent = "查看完整输出";
    return;
  }
  const executionId = card.dataset.executionId || state.executionId;
  const outputId = button.dataset.outputId;
  if (!executionId || !outputId) {
    button.textContent = "详情不可用";
    button.disabled = true;
    return;
  }
  button.disabled = true;
  button.textContent = "正在加载…";
  try {
    const response = await fetch(
      "/api/web/executions/" + encodeURIComponent(executionId) + "/outputs/" + encodeURIComponent(outputId)
    );
    if (!response.ok) throw new Error("详情请求失败（" + String(response.status) + "）");
    const detail = await response.json();
    appendToolOutputDetail(card, detail);
    button.textContent = "收起完整输出";
  } catch (error) {
    button.textContent = "加载失败，重试";
  } finally {
    button.disabled = false;
  }
}

function addErrorMsg(message) {
  addMessage("error", `⚠️ ${message}`);
}

function setStatus(text) {
  const el = $("#chat-status");
  if (el) el.textContent = text;
  const stage = $("#run-stage");
  if (stage) stage.textContent = text || "准备就绪";
}

function scrollToBottom() {
  const msgs = $("#messages");
  if (!msgs) return;
  if (!state.followLatest) {
    updateLatestButton();
    return;
  }
  msgs.scrollTop = msgs.scrollHeight;
  updateLatestButton();
}

function scrollToLatest() {
  const msgs = $("#messages");
  if (!msgs) return;
  state.followLatest = true;
  msgs.scrollTop = msgs.scrollHeight;
  updateLatestButton();
}

function updateLatestButton() {
  const button = $("#jump-latest");
  if (button) button.hidden = state.followLatest;
}

function isAtLatest(messages) {
  return messages.scrollHeight - messages.scrollTop - messages.clientHeight < 48;
}

function isCompactInspectorViewport() {
  return window.matchMedia("(max-width: 1279px)").matches;
}

function isMobileNavigationViewport() {
  return window.matchMedia("(max-width: 959px)").matches;
}

function paneWidthLimits(name) {
  const config = PANE_WIDTH_CONFIG[name];
  if (!config) return null;
  const otherName = name === "sidebar" ? "inspector" : "sidebar";
  const otherConfig = PANE_WIDTH_CONFIG[otherName];
  const railWidth = $("#app-rail")?.getBoundingClientRect().width || 60;
  const otherWidth = $(otherConfig.element)?.getBoundingClientRect().width || otherConfig.min;
  const available = window.innerWidth - railWidth - otherWidth - 420 - 12;
  return { min: config.min, max: Math.max(config.min, Math.min(config.max, available)) };
}

function persistPaneWidths() {
  const widths = {};
  Object.keys(PANE_WIDTH_CONFIG).forEach((name) => {
    const value = uiState.paneWidths?.[name];
    if (Number.isFinite(value)) widths[name] = value;
  });
  try { localStorage.setItem(PANE_WIDTH_STORAGE_KEY, JSON.stringify(widths)); } catch (error) { /* 忽略 */ }
}

function syncPaneResizerValue(name) {
  const config = PANE_WIDTH_CONFIG[name];
  const resizer = config ? $(config.resizer) : null;
  const pane = config ? $(config.element) : null;
  if (!resizer || !pane) return;
  const limits = paneWidthLimits(name);
  const width = Math.round(pane.getBoundingClientRect().width);
  resizer.setAttribute("aria-valuemin", String(limits.min));
  resizer.setAttribute("aria-valuemax", String(limits.max));
  resizer.setAttribute("aria-valuenow", String(width));
  resizer.setAttribute("aria-valuetext", String(width) + " 像素");
}

function setPaneWidth(name, requestedWidth, persist = true) {
  const config = PANE_WIDTH_CONFIG[name];
  if (!config || isCompactInspectorViewport()) return null;
  const limits = paneWidthLimits(name);
  const width = Math.round(Math.min(limits.max, Math.max(limits.min, Number(requestedWidth) || limits.min)));
  document.documentElement.style.setProperty(config.property, String(width) + "px");
  uiState.paneWidths = uiState.paneWidths || {};
  uiState.paneWidths[name] = width;
  syncPaneResizerValue(name);
  syncPaneResizerValue(name === "sidebar" ? "inspector" : "sidebar");
  if (persist) persistPaneWidths();
  return width;
}

function resetPaneWidth(name) {
  const config = PANE_WIDTH_CONFIG[name];
  if (!config) return;
  document.documentElement.style.removeProperty(config.property);
  uiState.paneWidths = uiState.paneWidths || {};
  delete uiState.paneWidths[name];
  persistPaneWidths();
  requestAnimationFrame(() => syncPaneResizerValue(name));
}

function bindPaneResizers() {
  uiState.paneWidths = {};
  try {
    const saved = JSON.parse(localStorage.getItem(PANE_WIDTH_STORAGE_KEY) || "{}");
    Object.keys(PANE_WIDTH_CONFIG).forEach((name) => {
      if (Number.isFinite(Number(saved[name]))) setPaneWidth(name, Number(saved[name]), false);
    });
  } catch (error) { /* 忽略 */ }

  Object.entries(PANE_WIDTH_CONFIG).forEach(([name, config]) => {
    const resizer = $(config.resizer);
    const pane = $(config.element);
    if (!resizer || !pane) return;
    syncPaneResizerValue(name);
    resizer.addEventListener("pointerdown", (event) => {
      if (isCompactInspectorViewport()) return;
      event.preventDefault();
      const startX = event.clientX;
      const startWidth = pane.getBoundingClientRect().width;
      resizer.setPointerCapture(event.pointerId);
      document.body.classList.add("is-pane-resizing");
      const move = (moveEvent) => {
        const delta = name === "sidebar" ? moveEvent.clientX - startX : startX - moveEvent.clientX;
        setPaneWidth(name, startWidth + delta, false);
      };
      const end = (endEvent) => {
        resizer.removeEventListener("pointermove", move);
        resizer.removeEventListener("pointerup", end);
        resizer.removeEventListener("pointercancel", end);
        if (resizer.hasPointerCapture(endEvent.pointerId)) resizer.releasePointerCapture(endEvent.pointerId);
        document.body.classList.remove("is-pane-resizing");
        persistPaneWidths();
      };
      resizer.addEventListener("pointermove", move);
      resizer.addEventListener("pointerup", end);
      resizer.addEventListener("pointercancel", end);
    });
    resizer.addEventListener("keydown", (event) => {
      if (event.key !== "ArrowLeft" && event.key !== "ArrowRight" && event.key !== "Home" && event.key !== "End") return;
      event.preventDefault();
      const limits = paneWidthLimits(name);
      const current = pane.getBoundingClientRect().width;
      const direction = event.key === "ArrowLeft" ? -1 : 1;
      const requested = event.key === "Home" ? limits.min
        : event.key === "End" ? limits.max
        : current + (name === "sidebar" ? direction : -direction) * (event.shiftKey ? 32 : 8);
      setPaneWidth(name, requested);
    });
    resizer.addEventListener("dblclick", () => resetPaneWidth(name));
  });
  window.addEventListener("resize", () => {
    renderCompactCalls();
    if (uiState.inspectorTab === "agents") renderLiveOrchestrations();
    if (isCompactInspectorViewport()) return;
    Object.keys(PANE_WIDTH_CONFIG).forEach((name) => {
      const saved = uiState.paneWidths?.[name];
      if (Number.isFinite(saved)) setPaneWidth(name, saved, false);
      else syncPaneResizerValue(name);
    });
  });
}

function isInspectorCollapsed() {
  return isCompactInspectorViewport()
    ? !document.body.classList.contains("inspector-expanded")
    : document.body.classList.contains("inspector-collapsed");
}

function isElementFocusable(element) {
  if (!element || element.disabled || element.getAttribute("aria-hidden") === "true"
      || element.getAttribute("tabindex") === "-1") return false;
  if (element.closest("[inert], [aria-hidden='true']")) return false;
  return !!(element.offsetWidth || element.offsetHeight || element.getClientRects().length);
}

function getInspectorFocusableElements() {
  const panel = $("#execution-panel");
  if (!panel || isInspectorCollapsed()) return [];
  return Array.from(panel.querySelectorAll(INSPECTOR_FOCUSABLE_SELECTOR)).filter(isElementFocusable);
}

function rememberInspectorReturnFocus() {
  const panel = $("#execution-panel");
  const active = document.activeElement;
  if (!active || active === document.body || panel?.contains(active)) return;
  state.inspectorReturnFocus = active;
}

function focusInspectorControl(collapsed, fallback) {
  const openButton = $("#open-inspector");
  const closeButton = $("#toggle-inspector");
  let target = fallback || (collapsed ? state.inspectorReturnFocus || openButton : closeButton);
  if (target && !document.contains(target)) target = collapsed ? openButton : closeButton;
  if (!target && !collapsed) target = getInspectorFocusableElements()[0] || $("#execution-panel");
  state.inspectorReturnFocus = collapsed ? null : state.inspectorReturnFocus;
  if (target) requestAnimationFrame(() => target.focus());
}

function trapInspectorFocus(event) {
  if (event.key !== "Tab" || isInspectorCollapsed() || !isCompactInspectorViewport()) return;
  const panel = $("#execution-panel");
  if (!panel) return;
  const focusables = getInspectorFocusableElements();
  if (!focusables.length) {
    event.preventDefault();
    panel.focus();
    return;
  }
  const first = focusables[0];
  const last = focusables[focusables.length - 1];
  if (event.shiftKey && (!panel.contains(document.activeElement) || document.activeElement === first)) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function setInspectorCollapsed(collapsed, focusControl = false) {
  const panel = $("#execution-panel");
  const closeButton = $("#toggle-inspector");
  const openButton = $("#open-inspector");
  const backdrop = $("#inspector-backdrop");
  const compactInspector = isCompactInspectorViewport();
  if (focusControl && !collapsed) rememberInspectorReturnFocus();

  if (compactInspector) {
    document.body.classList.remove("inspector-collapsed");
    document.body.classList.toggle("inspector-expanded", !collapsed);
  } else {
    document.body.classList.remove("inspector-expanded");
    document.body.classList.toggle("inspector-collapsed", collapsed);
  }

  if (panel) {
    panel.setAttribute("aria-hidden", String(collapsed));
    if (compactInspector && !collapsed) {
      panel.setAttribute("role", "dialog");
      panel.setAttribute("aria-modal", "true");
    } else {
      panel.removeAttribute("role");
      panel.removeAttribute("aria-modal");
    }
    if (!panel.hasAttribute("tabindex")) panel.tabIndex = -1;
    if ("inert" in panel) panel.inert = collapsed;
  }
  if (closeButton) {
    closeButton.title = collapsed ? "展开执行面板" : "收起执行面板";
    closeButton.setAttribute("aria-label", closeButton.title);
    closeButton.setAttribute("aria-expanded", String(!collapsed));
  }
  if (openButton) {
    openButton.title = collapsed ? "查看执行过程" : "隐藏执行过程";
    openButton.setAttribute("aria-label", openButton.title);
    openButton.setAttribute("aria-expanded", String(!collapsed));
  }
  if (backdrop) {
    backdrop.setAttribute("aria-hidden", String(collapsed));
    backdrop.tabIndex = collapsed ? -1 : 0;
  }

  if (focusControl) {
    focusInspectorControl(collapsed, collapsed ? null : closeButton);
  }
}

function setNavigationOpen(open, focusControl = false) {
  const toggle = $("#toggle-navigation");
  const navigation = $("#primary-navigation");
  const backdrop = $("#navigation-backdrop");
  const active = isMobileNavigationViewport() && open;
  document.body.classList.toggle("navigation-open", active);
  if (toggle) {
    toggle.title = active ? "关闭会话导航" : "打开会话导航";
    toggle.setAttribute("aria-label", toggle.title);
    toggle.setAttribute("aria-expanded", String(active));
  }
  if (navigation) {
    navigation.setAttribute("aria-hidden", String(!active && isMobileNavigationViewport()));
    if ("inert" in navigation) navigation.inert = !active && isMobileNavigationViewport();
  }
  if (backdrop) {
    backdrop.setAttribute("aria-hidden", String(!active));
    backdrop.tabIndex = active ? 0 : -1;
  }
  if (focusControl && active && navigation) {
    requestAnimationFrame(() => navigation.querySelector("button, summary, [href]")?.focus());
  } else if (focusControl && toggle) {
    requestAnimationFrame(() => toggle.focus());
  }
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
  "a", "blockquote", "br", "code", "del", "em", "h1", "h2", "h3", "h4", "h5", "h6",
  "hr", "li", "ol", "p", "pre", "strong", "table", "tbody", "td", "th", "thead", "tr", "ul",
]);
const DROP_MARKDOWN_TAGS = new Set([
  "base", "embed", "form", "iframe", "input", "math", "object", "script", "style", "svg", "video", "audio",
]);

function isSafeMarkdownHref(raw) {
  const value = String(raw || "").trim();
  if (!value || value.startsWith("//")) return false;
  if (value.startsWith("#") || value.startsWith("/") || value.startsWith("./") || value.startsWith("../")) return true;
  try {
    const url = new URL(value, window.location.origin);
    return ["http:", "https:", "mailto:"].includes(url.protocol);
  } catch (error) {
    return false;
  }
}

function renderMarkdown(content) {
  const source = String(content == null ? "" : content);
  if (!window.marked || typeof window.marked.parse !== "function" || typeof document === "undefined") {
    return esc(source);
  }
  let rendered;
  try {
    rendered = window.marked.parse(source, { headerIds: false, mangle: false });
  } catch (error) {
    return esc(source);
  }
  const template = document.createElement("template");
  template.innerHTML = rendered;
  template.content.querySelectorAll("*").forEach((element) => {
    const tag = element.tagName.toLowerCase();
    if (DROP_MARKDOWN_TAGS.has(tag)) {
      element.remove();
      return;
    }
    if (!SAFE_MARKDOWN_TAGS.has(tag)) {
      element.replaceWith(document.createTextNode(element.textContent || ""));
      return;
    }
    const href = element.getAttribute("href");
    const title = element.getAttribute("title");
    [...element.attributes].forEach((attribute) => element.removeAttribute(attribute.name));
    if (tag === "a" && isSafeMarkdownHref(href)) {
      element.setAttribute("href", href);
      if (title) element.setAttribute("title", title);
      element.setAttribute("target", "_blank");
      element.setAttribute("rel", "noopener noreferrer");
    }
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
  const planInfo = data.plan && data.plan.length
    ? ` · 计划 ${data.plan.length} 步${data.plan_revisions ? ` · 重规划 ${data.plan_revisions}` : ""}`
    : "";
  header.innerHTML = `
    <span class="wf-title">${uiIconMarkup("trace")} Agent 工作流</span>
    <span class="wf-meta">${data.trace_id ? data.trace_id.slice(-12) : ""}${planInfo}</span>
    <span class="wf-toggle">收起 ▴</span>
  `;

  // Body
  const body = document.createElement("div");
  body.className = "workflow-body";
  body.innerHTML = renderWorkflowSummary(data);
  header.addEventListener("click", () => {
    panel.classList.toggle("open");
    header.querySelector(".wf-toggle").textContent = panel.classList.contains("open") ? "收起 ▴" : "展开 ▾";
  });
  panel.appendChild(header);
  panel.appendChild(body);
  insertExecutionProgress(panel);
  panel.classList.add("open");
  scrollToBottom();
  body.querySelector("[data-workflow-inspector]")?.addEventListener("click", (event) => {
    event.stopPropagation();
    setInspectorSection("execution");
    setInspectorTab(event.currentTarget.dataset.inspectorTab || "timeline");
    setInspectorCollapsed(false, true);
  });
  return panel;
}

function renderWorkflowSummary(data) {
  const toolCount = Array.isArray(data.tool_calls) ? data.tool_calls.length : 0;
  const planCount = Array.isArray(data.plan) ? data.plan.length : 0;
  const hasTrace = Boolean(data.trace_id || data.trace);
  const details = [];
  if (toolCount) details.push(`${toolCount} 次工具调用`);
  if (planCount) details.push(`${planCount} 个计划步骤`);
  if (!details.length) details.push("运行事件已记录");
  const inspectorTab = hasTrace ? "trace" : "timeline";
  return `
    <div class="workflow-summary">
      <p>${esc(details.join(" · "))}。完整事件与调用详情保留在右侧 Inspector。</p>
      <button type="button" class="workflow-inspector-link" data-workflow-inspector data-inspector-tab="${inspectorTab}">在 Inspector 查看${hasTrace ? " Trace" : "时间线"} →</button>
    </div>`;
}

function bindToolStepToggles(panel) {
  panel.querySelectorAll(".ts-card").forEach((card) => {
    const header = card.querySelector(".ts-header");
    header.addEventListener("click", () => {
      card.classList.toggle("open");
      const caret = card.querySelector(".ts-caret");
      caret.textContent = card.classList.contains("open") ? "▴" : "▾";
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
  const failed = tc.status === "ERROR" || Boolean(tc.error);
  const status = failed ? "failed" : "succeeded";
  const args = tc.arguments === undefined ? {} : tc.arguments;
  const hasOutput = hasOwn(tc, "data") && tc.data !== undefined;
  const errorText = toolErrorText(tc.error);
  return `
    <div class="ts-card" data-index="${index}">
      <div class="ts-header">
        <span class="ts-num">${index + 1}</span>
        <span class="ts-icon">🛠</span>
        <span class="ts-name">${esc(tc.name)}</span>
        ${toolStatusMarkup(status)}
        ${dur ? `<span class="ts-dur">⏱ ${dur}</span>` : ""}
        <span class="ts-args-preview">${esc(formatJsonValue(args).slice(0, 60))}</span>
        <span class="ts-caret">▾</span>
      </div>
      <div class="ts-body">
        <div class="ts-section">
          <div class="ts-label">📋 参数</div>
          <pre class="ts-code">${esc(formatJsonValue(args))}</pre>
        </div>
        ${hasOutput ? `
        <div class="ts-section">
          <div class="ts-label">📤 输出</div>
          ${toolOutputHtml(tc.name, tc.data, tc)}
        </div>` : ""}
        ${errorText ? `
        <div class="ts-section">
          <div class="ts-label err-label">⚠️ 错误</div>
          <pre class="ts-code err-code">${esc(errorText)}</pre>
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

function formatFileSize(size) {
  const bytes = Number(size);
  if (!Number.isFinite(bytes) || bytes < 0) return "大小未知";
  if (bytes < 1024) return String(bytes) + " B";
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + " KB";
  return (bytes / (1024 * 1024)).toFixed(1) + " MB";
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
  // 行（点击展开详情；caret 折叠子节点）
  html += `<div class="tn-row ${isErr ? "is-err" : ""} ${hasDetails ? "clickable" : ""}" style="padding-left:${depth * 14}px">`;
  html += hasChildren
    ? `<span class="tn-caret open" data-caret>▾</span>`
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
  html += hasDetails ? `<span class="tn-expand" data-expand>详情 ▾</span>` : "";
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
      const row = caret.closest(".tn-row");
      const children = row.nextElementSibling && row.nextElementSibling.classList.contains("tn-children")
        ? row.nextElementSibling : null;
      if (!children) return;
      const open = caret.classList.contains("open");
      caret.classList.toggle("open", !open);
      children.style.display = open ? "none" : "";
    });
  });
  // 行点击：展开/收起详情面板
  panel.querySelectorAll(".tn-row.clickable").forEach((row) => {
    row.addEventListener("click", (e) => {
      if (e.target.closest("[data-caret]")) return; // caret 已处理
      const detail = row.nextElementSibling && row.nextElementSibling.classList.contains("tn-detail")
        ? row.nextElementSibling : null;
      if (!detail) return;
      const expand = row.querySelector("[data-expand]");
      const isOpen = detail.classList.contains("open");
      detail.classList.toggle("open", !isOpen);
      if (expand) expand.textContent = isOpen ? "详情 ▾" : "详情 ▴";
    });
  });
}

/* ================= 流式连接状态核对 ================= */
function markExecutionStateUncertain(stage, detail) {
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionTimer = null;
  updateExecutionStatus("pending", "状态待确认", stage);
  appendExecutionEvent("warning", "执行状态待确认", detail || "浏览器连接已断开；服务端任务可能仍在继续。");
}

async function reconcileDetachedExecution(executionId, assistantEl, contentEl) {
  if (!executionId || state.executionId !== executionId || state.streaming) return;
  const viewVersion = state.executionViewVersion;
  try {
    const response = await fetch("/api/web/executions/" + encodeURIComponent(executionId));
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    const record = await response.json();
    if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== executionId || state.streaming) return;
    setConnStatus(true);
    const events = await fetchAllExecutionEvents(executionId);
    if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== executionId || state.streaming) return;
    replayExecution(record, events);
    assistantEl.classList.remove("streaming");

    if (isActiveExecutionStatus(record.status)) {
      contentEl.textContent = "连接已恢复，正在从持久化执行记录继续接续…";
      await resumeExecutionEvents(record, events.length ? events[events.length - 1].seq : 0, viewVersion);
    } else {
      contentEl.textContent = "执行已结束，正在加载会话历史。";
      if (record.session_id) await openSession(record.session_id);
    }
  } catch (error) {
    if (!isCurrentExecutionViewVersion(viewVersion) || state.executionId !== executionId || state.streaming) return;
    setConnStatus(false);
    markExecutionStateUncertain(
      "执行连接已断开，状态待确认",
      "无法读取服务端执行记录；请稍后从执行历史使用 execution ID 继续查看。"
    );
  }
  if (state.activeAssistantElement === assistantEl) state.activeAssistantElement = null;
}

/* ================= 发送 ================= */
async function send() {
  const input = $("#input");
  const message = input.value.trim();
  if (!message || state.streaming) return;

  addMessage("user", message);
  scrollToLatest();
  input.value = "";
  // 新提交在服务器确认 execution_id 前不能继承上一轮运行的取消/回放目标。
  startExecution({ mode: state.agentMode, message_preview: message });
  setStreaming(true);

  const assistantEl = addMessage("assistant", "");
  assistantEl.classList.add("streaming");
  state.activeAssistantElement = assistantEl;
  const contentEl = assistantEl.querySelector(".md-body");
  contentEl.className = "md-body";
  contentEl.textContent = "";

  state.abortCtrl = new AbortController();
  $("#stop").style.display = "block";
  let detachedExecutionId = null;

  try {
    const resp = await fetch("/api/web/chat/stream", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: state.sessionId, agent_mode: state.agentMode }),
      signal: state.abortCtrl.signal,
    });
    if (!resp.ok) {
      let message = "HTTP " + String(resp.status);
      try {
        const payload = await resp.json();
        message = payload.detail && typeof payload.detail === "object"
          ? String(payload.detail.message || message)
          : String(payload.detail || message);
      } catch (error) {
        // 非 JSON 错误体保留 HTTP 状态，避免把 HTML 当作 SSE 解析。
      }
      const requestError = new Error(message);
      requestError.requestRejected = true;
      throw requestError;
    }
    setConnStatus(true);
    const executionId = resp.headers.get("X-Execution-ID");
    if (executionId) {
      state.executionId = executionId;
      renderExecutionHistory(state.executionHistory);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let finalAnswer = "";
    let finalData = null;

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, idx);
        buffer = buffer.slice(idx + 2);
        handleFrame(frame, contentEl, (answer, data) => { finalAnswer = answer; finalData = data; });
      }
    }
    if (finalAnswer) {
      contentEl.innerHTML = renderMarkdown(finalAnswer);
      ensureCopyButton(assistantEl);
    }
    assistantEl.classList.remove("streaming");
    if (finalData) {
      state.sessionId = finalData.session_id;
      cacheSessionMessages(state.sessionId, [
        { role: "user", content: message },
        { role: "assistant", content: finalAnswer || finalData.answer || "" },
      ]);
      setLivePlan(finalData.plan, finalData.plan_version, finalData.plan_revisions);
      finishExecution("success", "已完成，可查看完整工作流");
      addWorkflowPanel({
        trace: finalData.trace, trace_id: finalData.trace_id,
        plan: finalData.plan, plan_revisions: finalData.plan_revisions,
        tool_calls: finalData.tool_calls,
      });
    }
  } catch (error) {
    assistantEl.classList.remove("streaming");
    if (error.requestRejected) {
      contentEl.textContent = "⚠️ 请求被服务端拒绝: " + error.message;
      finishExecution("error", "请求被服务端拒绝");
      appendExecutionEvent("error", "请求被服务端拒绝", error.message);
    } else {
      const knownExecutionId = state.executionId;
      setConnStatus(false);
      if (knownExecutionId) {
        detachedExecutionId = knownExecutionId;
        contentEl.textContent = error.name === "AbortError"
          ? "本地连接已停止，正在核对服务端执行状态…"
          : "连接已中断，正在核对服务端执行状态…";
        markExecutionStateUncertain(
          "连接已断开，状态待确认",
          "服务端任务可能仍在继续；正在从持久化事件记录重新接续。"
        );
      } else {
        contentEl.textContent = error.name === "AbortError"
          ? "停止请求发生在服务器返回执行标识前，状态待确认。"
          : "连接未建立或已中断，服务器执行状态待确认。";
        markExecutionStateUncertain(
          "状态待确认",
          "尚未收到 execution ID，无法将本地连接与服务端任务可靠关联。"
        );
      }
    }
  }
  $("#stop").style.display = "none";
  setStreaming(false);
  loadSessions();
  if (state.sessionId) {
    loadOrchestrations(state.sessionId);
    loadExecutionHistory(state.sessionId);
  }
  if (detachedExecutionId) {
    void reconcileDetachedExecution(detachedExecutionId, assistantEl, contentEl);
  } else if (state.activeAssistantElement === assistantEl) {
    state.activeAssistantElement = null;
  }
}

function ensureCopyButton(assistantEl) {
  if (assistantEl.querySelector(".copy-btn")) return;
  const contentEl = assistantEl.querySelector(".md-body");
  if (!contentEl) return;
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-btn";
  copyBtn.textContent = "复制";
  copyBtn.addEventListener("click", () => {
    if (!navigator.clipboard?.writeText) {
      copyBtn.textContent = "复制失败";
      setTimeout(() => (copyBtn.textContent = "复制"), 1800);
      return;
    }
    navigator.clipboard.writeText(String(contentEl.textContent || "")).then(() => {
      copyBtn.textContent = "已复制 ✓";
      setTimeout(() => (copyBtn.textContent = "复制"), 1500);
    }).catch(() => {
      copyBtn.textContent = "复制失败";
      setTimeout(() => (copyBtn.textContent = "复制"), 1800);
    });
  });
  actions.appendChild(copyBtn);
  (assistantEl.querySelector(".assistant-content") || assistantEl).appendChild(actions);
}

function handleFrame(frame, contentEl, onComplete) {
  const lines = frame.split("\n");
  const eventLine = lines.find((l) => l.startsWith("event:"));
  const dataLine = lines.find((l) => l.startsWith("data:"));
  if (!eventLine || !dataLine) return;
  const event = eventLine.slice(6).trim();
  let data;
  try { data = JSON.parse(dataLine.slice(5).trim()); } catch (e) { return; }

  // 新运行只接受服务端确认的 queued（兼容旧流的 started）入口；旧运行的晚到帧直接忽略。
  if ((event === "execution.queued" || event === "execution.started")
      && data.execution_id && data.execution_id !== state.executionId) {
    startExecution(data);
  }
  if (!registerExecutionEvent(data)) return;
  if (applyRuntimeLifecycleEvent(event, data, data.timestamp) || applyOrchestrationLifecycleEvent(event, data, data.timestamp) || applyPlanLifecycleEvent(event, data, data.timestamp)) return;

  switch (event) {
    case "execution.queued":
      markExecutionQueued(data.timestamp);
      updateExecutionStatus("pending", "排队中", executionQueueDetail(data));
      appendExecutionEvent("pending", "任务已进入执行队列", executionQueueDetail(data), data.timestamp);
      break;
    case "execution.started":
      markExecutionStarted(data.timestamp);
      updateExecutionStatus("running", "执行中", "正在准备执行环境");
      appendExecutionEvent("running", "任务开始执行", data.mode ? "模式：" + formatMode(data.mode) : "", data.timestamp);
      break;
    case "llm.started":
      updateExecutionStatus("running", "执行中", "正在请求模型第 " + String(data.step || "?") + " 轮决策");
      appendExecutionEvent("running", "模型开始决策", "第 " + String(data.step || "?") + " 轮");
      break;
    case "llm.retry_scheduled":
      recordLLMRetry(data, data.timestamp);
      break;
    case "llm.failed":
      recordLLMFailure(data, data.timestamp);
      break;
    case "step":
      state.executionSteps = Math.max(state.executionSteps, Number(data.step) || 0);
      recordModelUsage(data, "root:" + String(data.event_id || data.step || state.executionSteps));
      renderExecutionMetrics();
      updateExecutionStatus("running", "执行中", "第 " + String(data.step || "?") + " 轮决策完成");
      appendExecutionEvent("running", "模型完成决策", modelUsageDetail(data, data.is_final ? "正在生成最终回答" : "已确定下一步"));
      if (data.tool_calls && data.tool_calls.length) {
        data.tool_calls.forEach((tc) => {
          addToolMsg({ tool: tc.name, arguments: tc.arguments, tool_call_id: tc.id, data: "等待执行..." });
        });
      }
      break;
    case "tool.started":
      recordToolFact(data, "pending", data.event_id);
      markToolRunning(data);
      updateExecutionStatus("running", "执行中", "正在调用工具：" + String(data.tool || ""));
      appendExecutionEvent("running", "开始调用工具：" + String(data.tool || ""), "");
      break;
    case "tool.retry_scheduled":
      recordToolRetry(data, data.timestamp);
      break;
    case "tool_result":
      // 回填对应的卡片：优先按稳定 tool_call_id，而不是按工具名猜测。
      if (!updateToolCard(data.tool, data)) {
        addToolMsg(data); // 找不到则新增
      }
      recordToolFact(data, data.success === false ? "failed" : "success", data.event_id);
      state.executionTools += 1;
      renderExecutionMetrics();
      appendExecutionEvent(
        data.success === false ? "error" : "success",
        data.success === false ? "工具执行失败：" + String(data.tool || "") : "工具执行完成：" + String(data.tool || ""),
        data.duration_ms != null ? "耗时 " + formatMs(data.duration_ms) : ""
      );
      break;
    case "final":
      contentEl.innerHTML = renderMarkdown(data.content || "");
      updateExecutionStatus("running", "执行中", "正在整理最终回答");
      appendExecutionEvent("running", "已生成最终回答", "等待执行记录归档");
      break;
    case "done":
      setLivePlan(data.plan, data.plan_version, data.plan_revisions);
      setExecutionTrace(data.trace, data.trace_id);
      onComplete(data.answer || "", data);
      break;
    case "execution.completed":
      finishExecution("success", "执行记录已保存");
      appendExecutionEvent("success", "执行记录已保存", data.trace_id ? "Trace " + String(data.trace_id).slice(-12) : "");
      break;
    case "execution.cancel_requested":
      updateExecutionStatus("running", "停止请求已发送", "正在等待服务端确认取消");
      appendExecutionEvent("warning", "已请求停止", "等待服务端确认");
      break;
    case "execution.cancelled":
      if (!contentEl.textContent.trim()) contentEl.textContent = "⏹ 服务端已确认取消执行。";
      finishExecution("cancelled", "服务端已确认停止");
      appendExecutionEvent("warning", "服务端确认已取消", "");
      break;
    case "error":
      addErrorMsg(data.message);
      finishExecution("error", "Agent 返回错误");
      appendExecutionEvent("error", "Agent 执行失败", data.message || "");
      break;
    case "execution.failed":
      finishExecution("error", "执行失败");
      break;
  }
}

function setStreaming(v) {
  state.streaming = v;
  const send = $("#send");
  if (send) send.disabled = v;
  $$(".mode-btn").forEach((button) => { button.disabled = v; });
  if (!v) state.abortCtrl = null;
  syncSessionNavigationState();
  renderExecutionHistory(state.executionHistory);
}

async function stopStreaming() {
  if (!state.abortCtrl) return;
  const button = $("#stop");
  if (!state.executionId) {
    state.abortCtrl.abort();
    button.style.display = "none";
    return;
  }

  button.disabled = true;
  const executionId = state.executionId;
  const viewVersion = state.executionViewVersion;
  updateExecutionStatus("running", "停止请求已发送", "正在等待服务端确认取消");
  appendExecutionEvent("warning", "已请求停止", "服务端确认后将结束执行");
  try {
    const response = await fetch(
      "/api/web/executions/" + encodeURIComponent(executionId) + "/cancel",
      { method: "POST" }
    );
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    const data = await response.json();
    if (state.executionId !== executionId || !isCurrentExecutionViewVersion(viewVersion)) return;
    const outcome = terminalExecutionOutcome(data.status);
    if (!data.cancel_requested && outcome) {
      finishExecution(outcome.uiStatus, outcome.stage);
    }
  } catch (error) {
    if (state.executionId !== executionId || !isCurrentExecutionViewVersion(viewVersion)) return;
    button.disabled = false;
    addErrorMsg("停止请求失败: " + error.message);
  }
}

function bindRovingTablist(selector) {
  const tabs = Array.from($$(selector));
  tabs.forEach((tab, index) => {
    tab.addEventListener("keydown", (event) => {
      if (!["ArrowLeft", "ArrowRight", "Home", "End"].includes(event.key)) return;
      event.preventDefault();
      const nextIndex = event.key === "Home" ? 0
        : event.key === "End" ? tabs.length - 1
        : (index + (event.key === "ArrowRight" ? 1 : -1) + tabs.length) % tabs.length;
      tabs[nextIndex].focus();
      tabs[nextIndex].click();
    });
  });
}

/* ================= 事件绑定 ================= */
function bindEvents() {
  const input = $("#input");
  const sendBtn = $("#send");
  const newBtns = $$("#new-session, [data-new-session]");
  const stopBtn = $("#stop");
  const uploadBtns = $$("[data-upload-trigger]");
  const fileInput = $("#file-input");
  const resourceContent = $("#resource-content");
  const messages = $("#messages");
  const jumpLatest = $("#jump-latest");
  const searchInput = $("#search-input");

  sendBtn.addEventListener("click", send);
  if (stopBtn) stopBtn.addEventListener("click", stopStreaming);
  input.addEventListener("keydown", (e) => {
    // 中文等输入法组合阶段的 Enter 只用于选字，不能误发送任务。
    if (e.isComposing) return;
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); }
  });
  input.addEventListener("input", () => {
    input.style.height = "auto";
    input.style.height = Math.min(input.scrollHeight, 120) + "px";
  });
  newBtns.forEach((newBtn) => newBtn.addEventListener("click", newSession));
  $$("[data-session-filter]").forEach((button) => {
    button.addEventListener("click", () => {
      state.sessionFilter = button.dataset.sessionFilter || "recent";
      $$("[data-session-filter]").forEach((filter) => {
        const active = filter === button;
        filter.classList.toggle("active", active);
        filter.setAttribute("aria-selected", String(active));
        filter.tabIndex = active ? 0 : -1;
      });
      renderSessionList();
    });
  });
  $("#open-search")?.addEventListener("click", openLoadedSearch);
  $("#close-search")?.addEventListener("click", () => closeLoadedSearch());
  $("#search-backdrop")?.addEventListener("click", () => closeLoadedSearch());
  if (searchInput) {
    searchInput.addEventListener("input", renderLoadedSearchResults);
    searchInput.addEventListener("keydown", (event) => {
      if (event.isComposing) return;
      if (event.key === "ArrowDown") {
        event.preventDefault();
        $("#search-results .search-result")?.focus();
      } else if (event.key === "Enter") {
        event.preventDefault();
        $("#search-results .search-result")?.click();
      }
    });
  }
  if (messages) {
    messages.addEventListener("scroll", () => {
      state.followLatest = isAtLatest(messages);
      updateLatestButton();
    }, { passive: true });
    messages.addEventListener("click", (event) => {
      const example = event.target.closest("[data-welcome-prompt]");
      if (!example) return;
      input.value = example.dataset.welcomePrompt || "";
      input.dispatchEvent(new Event("input", { bubbles: true }));
      input.focus();
    });
  }
  if (jumpLatest) jumpLatest.addEventListener("click", scrollToLatest);

  $$('[data-timeline-filter]').forEach((button) => {
    button.addEventListener("click", () => setTimelineFilter(button.dataset.timelineFilter));
  });
  try {
    const savedTimelineFilter = localStorage.getItem("reagent-timeline-filter");
    setTimelineFilter(TIMELINE_FILTERS.has(savedTimelineFilter) ? savedTimelineFilter : "all", false);
  } catch (e) {
    setTimelineFilter("all", false);
  }

  const themeBtn = $("#theme-toggle");
  if (themeBtn) {
    themeBtn.addEventListener("click", () => {
      const nextTheme = document.body.dataset.theme === "dark" ? "light" : "dark";
      document.body.dataset.theme = nextTheme;
      themeBtn.textContent = nextTheme === "dark" ? "☀" : "◐";
      try { localStorage.setItem("reagent-theme", nextTheme); } catch (e) { /* 忽略 */ }
    });
    try {
      const savedTheme = localStorage.getItem("reagent-theme");
      if (savedTheme === "dark") {
        document.body.dataset.theme = "dark";
        themeBtn.textContent = "☀";
      }
    } catch (e) { /* 忽略 */ }
  }
  const defaultInspectorTab = bindLocalSettings();

  const inspectorBtn = $("#toggle-inspector");
  if (inspectorBtn) {
    inspectorBtn.addEventListener("click", () => {
      setInspectorCollapsed(true, true);
    });
  }
  const inspectorLauncher = $("#open-inspector");
  if (inspectorLauncher) {
    inspectorLauncher.addEventListener("click", () => {
      setInspectorCollapsed(!isInspectorCollapsed(), true);
    });
  }
  $("#inspector-backdrop")?.addEventListener("click", () => setInspectorCollapsed(true, true));

  const navigationToggle = $("#toggle-navigation");
  if (navigationToggle) {
    navigationToggle.addEventListener("click", () => {
      setNavigationOpen(!document.body.classList.contains("navigation-open"), true);
    });
  }
  $("#navigation-backdrop")?.addEventListener("click", () => setNavigationOpen(false, true));

  $$('[data-primary-view]').forEach((button) => {
    button.addEventListener("click", () => {
      if (button.getAttribute("aria-disabled") === "true") return;
      setPrimaryView(button.dataset.primaryView);
    });
  });
  $$('[data-inspector-section]').forEach((button) => {
    button.addEventListener("click", () => setInspectorSection(button.dataset.inspectorSection));
  });
  $$('[data-inspector-tab]').forEach((button) => {
    button.addEventListener("click", () => setInspectorTab(button.dataset.inspectorTab));
  });
  $$('[data-compact-call-view]').forEach((button) => {
    button.addEventListener("click", () => setCompactCallView(button.dataset.compactCallView));
  });
  bindRovingTablist('.session-filters [role="tab"]');
  bindRovingTablist('.inspector-section-tabs [role="tab"]');
  bindRovingTablist('.inspector-tabs [role="tab"]');
  bindRovingTablist('.compact-call-tabs [role="tab"]');
  document.addEventListener("keydown", (event) => {
    if (!event.isComposing && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
      event.preventDefault();
      openLoadedSearch();
      return;
    }
    if (trapLoadedSearchFocus(event)) return;
    trapInspectorFocus(event);
    if (event.key !== "Escape") return;
    if (!$("#search-dialog")?.hidden) {
      closeLoadedSearch();
    } else if (document.body.classList.contains("navigation-open")) {
      setNavigationOpen(false, true);
    } else if (!isInspectorCollapsed()) {
      setInspectorCollapsed(true, true);
    }
  });

  const inspectorViewport = window.matchMedia("(max-width: 1279px)");
  const navigationViewport = window.matchMedia("(max-width: 959px)");
  const syncResponsiveControls = () => {
    setInspectorCollapsed(isCompactInspectorViewport() ? !document.body.classList.contains("inspector-expanded") : document.body.classList.contains("inspector-collapsed"));
    setNavigationOpen(document.body.classList.contains("navigation-open"));
  };
  if (typeof inspectorViewport.addEventListener === "function") {
    inspectorViewport.addEventListener("change", syncResponsiveControls);
    navigationViewport.addEventListener("change", syncResponsiveControls);
  }
  bindPaneResizers();
  setInspectorCollapsed(isCompactInspectorViewport());
  setNavigationOpen(false);
  setPrimaryView(uiState.primaryView);
  setInspectorSection(uiState.inspectorSection);
  setInspectorTab(defaultInspectorTab);
  setCompactCallView(uiState.compactCallView);
  renderCompactCalls();
  renderInspectorRunSummary();
  renderInspectorFiles();

  // 上传文件
  if (uploadBtns.length && fileInput) {
    uploadBtns.forEach((uploadBtn) => uploadBtn.addEventListener("click", () => fileInput.click()));
    fileInput.addEventListener("change", async () => {
      const file = fileInput.files[0];
      if (!file) return;
      if (file.size > 1024 * 1024) {
        setPrimaryView("files");
        setResourceFeedback("error", "上传失败：文件超过全局沙箱 1MiB 限制。");
        fileInput.value = "";
        return;
      }
      const fd = new FormData();
      fd.append("file", file);
      try {
        const r = await fetch("/api/web/upload", { method: "POST", body: fd });
        const data = await r.json().catch(() => ({}));
        if (!r.ok) throw new Error(data.detail || "HTTP " + String(r.status));
        await loadFiles();
        setPrimaryView("files");
        setResourceFeedback("success", "已上传 " + file.name + " 到全局沙箱。");
      } catch (e) {
        setPrimaryView("files");
        setResourceFeedback("error", "上传失败: " + e.message);
      }
      fileInput.value = "";
    });
  }
  if (resourceContent) {
    resourceContent.addEventListener("click", (event) => {
      if (event.target.closest('[data-resource-action="upload"]')) fileInput?.click();
    });
  }

  $("[data-composer-tools]")?.addEventListener("click", () => {
    setPrimaryView("tools");
  });

  document.querySelectorAll(".mode-btn").forEach((btn) => {
    btn.addEventListener("click", () => {
      if (!state.streaming) setAgentMode(btn.dataset.mode);
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
    const response = await fetch("/api/web/files");
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "HTTP " + String(response.status));
    state.resources.files = Array.isArray(data.files) ? data.files : [];
    renderList("#file-list", state.resources.files.map((f) => ({
      text: `${f.name} (${(f.size / 1024).toFixed(1)}KB)`,
      title: f.name,
    })));
    renderInspectorFiles();
    renderResourceWorkspace();
  } catch (e) {
    setResourceFeedback("error", "文件列表读取失败: " + e.message);
  }
}

/* ================= 会话删除 ================= */
async function deleteSession(sessionId) {
  if (!canChangeSession()) return;
  if (!confirm(`删除会话 ${sessionId.slice(-12)}？`)) return;
  try {
    const response = await fetch(`/api/web/sessions/${encodeURIComponent(sessionId)}`, { method: "DELETE" });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || "HTTP " + String(response.status));
    if (state.sessionId === sessionId) newSession();
    setSessionFeedback("success", "会话已删除。");
    loadSessions();
    if (state.sessionId) loadOrchestrations(state.sessionId);
  } catch (e) {
    setSessionFeedback("error", "删除失败: " + e.message);
  }
}

init();
loadFiles();
