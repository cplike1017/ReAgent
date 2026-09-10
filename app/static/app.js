/* ReAgent Web UI 前端逻辑 v14 */
"use strict";

const state = {
  sessionId: null,
  agentMode: "react",
  streaming: false,
  abortCtrl: null, // 当前 SSE 的 AbortController（用于停止）
  executionId: null,
  executionStartedAt: null,
  executionTimer: null,
  executionSteps: 0,
  executionTools: 0,
  executionHistory: [],
  followLatest: true,
  currentPlanVersion: null,
  planRevisions: 0,
  planSnapshots: new Map(),
  planRevisionReasons: new Map(),
  orchestrationRuns: new Map(),
  executionContext: null,
};

const $ = (sel) => document.querySelector(sel);
const $$ = (sel) => document.querySelectorAll(sel);

/* ================= 初始化 ================= */
async function init() {
  const [capabilitiesReady] = await Promise.all([loadCapabilities(), loadSessions(), loadAgents()]);
  // 连接状态必须来自真实 API 响应，不能在失败后被无条件覆盖为“已连接”。
  setConnStatus(capabilitiesReady === true);
  bindEvents();
}

function setConnStatus(online) {
  $("#conn-status").className = "status-dot" + (online ? " online" : "");
  $("#conn-text").textContent = online ? "已连接" : "连接失败";
}

function startExecution(data) {
  if (state.executionTimer) window.clearInterval(state.executionTimer);
  state.executionId = data.execution_id || state.executionId;
  if (data.session_id) state.sessionId = data.session_id;
  state.executionStartedAt = Date.now();
  state.executionSteps = 0;
  state.executionTools = 0;
  state.followLatest = true;
  clearLivePlan();
  clearLiveOrchestrations();
  clearExecutionContext();
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
}

function renderExecutionMetrics() {
  const stepCount = $("#run-step-count");
  const toolCount = $("#run-tool-count");
  const elapsed = $("#run-elapsed");
  if (stepCount) stepCount.textContent = String(state.executionSteps);
  if (toolCount) toolCount.textContent = String(state.executionTools);
  if (elapsed) elapsed.textContent = formatExecutionElapsed();
}

function formatExecutionElapsed() {
  if (!state.executionStartedAt) return "0s";
  const seconds = Math.max(0, Math.floor((Date.now() - state.executionStartedAt) / 1000));
  if (seconds < 60) return String(seconds) + "s";
  return String(Math.floor(seconds / 60)) + "m " + String(seconds % 60) + "s";
}

function formatMode(mode) {
  return mode === "plan" ? "Plan" : "ReAct";
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

function appendExecutionEvent(kind, title, detail, timestamp) {
  const list = $("#execution-timeline");
  if (!list) return;
  const empty = list.querySelector(".timeline-empty");
  if (empty) empty.remove();

  const item = document.createElement("li");
  item.className = "timeline-item " + kind;
  const heading = document.createElement("div");
  heading.className = "timeline-title";
  heading.textContent = title;
  item.appendChild(heading);
  if (detail) {
    const copy = document.createElement("div");
    copy.className = "timeline-detail";
    copy.textContent = detail;
    item.appendChild(copy);
  }
  const time = document.createElement("time");
  time.className = "timeline-time";
  time.textContent = new Date(timestamp || Date.now()).toLocaleTimeString("zh-CN", {
    hour: "2-digit", minute: "2-digit", second: "2-digit",
  });
  item.appendChild(time);
  list.appendChild(item);
  list.scrollTop = list.scrollHeight;
  $("#timeline-count").textContent = String(list.querySelectorAll(".timeline-item").length);
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
  state.executionContext = createExecutionContextState();
  if (list) list.replaceChildren();
  if (meta) meta.textContent = "未采集";
  if (section) section.hidden = true;
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
    return;
  }
  entries.forEach(([title, value, detail, kind]) => list.appendChild(createContextFact(title, value, detail, kind)));
  section.hidden = false;
  meta.textContent = String(entries.length) + " 项事实";
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
  state.orchestrationRuns.clear();
  if (list) list.replaceChildren();
  if (meta) meta.textContent = "0 个编排";
  if (section) section.hidden = true;
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

function renderLiveOrchestrations() {
  const section = $("#live-orchestration-section");
  const list = $("#live-orchestrations");
  const meta = $("#live-orchestration-meta");
  if (!section || !list || !meta) return;
  const runs = [...state.orchestrationRuns.values()].sort((left, right) => {
    if (left.depth !== right.depth) return left.depth - right.depth;
    return left.runId.localeCompare(right.runId);
  });
  if (!runs.length) {
    section.hidden = true;
    meta.textContent = "0 个编排";
    list.replaceChildren();
    return;
  }

  const running = runs.filter((run) => orchestrationStateName(run.status) === "running").length;
  meta.textContent = String(runs.length) + " 个编排" + (running ? " · " + String(running) + " 运行中" : "");
  section.hidden = false;
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
    orderedAgents.forEach((agent) => agentList.appendChild(renderOrchestrationAgent(agent)));
    if (orderedAgents.length) body.appendChild(agentList);
    details.appendChild(body);
    list.appendChild(details);
  });
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
    appendExecutionEvent(
      "running",
      String(payload.agent_profile || "子 Agent") + " 完成模型决策",
      modelUsageDetail(payload, payload.is_final ? "正在整理子任务结果" : "已确定下一步"),
      timestamp
    );
  } else if (type === "agent.tool.started" || type === "agent.tool.retry_scheduled" || type === "agent.tool.completed") {
    const agent = upsertOrchestrationAgent(run, payload);
    const completed = type === "agent.tool.completed";
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

function renderExecutionHistory(records) {
  state.executionHistory = Array.isArray(records) ? records : [];
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

    const title = document.createElement("span");
    title.className = "execution-history-title";
    title.textContent = record.input_preview || record.execution_id;

    const meta = document.createElement("span");
    meta.className = "execution-history-meta";
    meta.textContent = formatMode(record.agent_mode) + " · " + formatStoredStatus(record.status)
      + (record.created_at ? " · " + formatStoredTime(record.created_at) : "");

    item.append(title, meta);
    item.addEventListener("click", () => openExecutionHistory(record.execution_id));
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

function formatStoredTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" });
}

async function loadExecutionHistory(sessionId) {
  const container = $("#execution-history");
  if (!sessionId) {
    renderExecutionHistory([]);
    return;
  }
  try {
    const response = await fetch("/api/web/sessions/" + encodeURIComponent(sessionId) + "/executions");
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    const data = await response.json();
    renderExecutionHistory(data.executions || []);
  } catch (error) {
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
  try {
    const response = await fetch("/api/web/executions/" + encodeURIComponent(executionId));
    if (!response.ok) throw new Error("执行记录读取失败");
    const record = await response.json();
    const events = await fetchAllExecutionEvents(executionId);
    replayExecution(record, events);
    renderExecutionHistory(state.executionHistory);
    if (isActiveExecutionStatus(record.status)) {
      resumeExecutionEvents(record, events.length ? events[events.length - 1].seq : 0);
    }
  } catch (error) {
    addErrorMsg("加载执行记录失败: " + error.message);
  }
}

async function resumeExecutionEvents(record, afterSeq) {
  // 已有浏览器流在消费该执行时不再创建第二个订阅，避免时间线重复。
  if (state.streaming) return;
  const controller = new AbortController();
  state.abortCtrl = controller;
  state.streaming = true;
  $("#send").disabled = true;
  const stop = $("#stop");
  if (stop) {
    stop.disabled = false;
    stop.style.display = "block";
  }

  try {
    const response = await fetch(
      "/api/web/executions/" + encodeURIComponent(record.execution_id)
        + "/stream?after_seq=" + String(afterSeq),
      { signal: controller.signal }
    );
    if (!response.ok) throw new Error("HTTP " + String(response.status));
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
        replayExecutionFrame(frame);
      }
    }
    const snapshotResponse = await fetch("/api/web/executions/" + encodeURIComponent(record.execution_id));
    if (snapshotResponse.ok) {
      const snapshot = await snapshotResponse.json();
      if (!isActiveExecutionStatus(snapshot.status)) {
        const finalEvents = await fetchAllExecutionEvents(record.execution_id);
        replayExecution(snapshot, finalEvents);
        if (snapshot.session_id) openSession(snapshot.session_id);
      }
    }
  } catch (error) {
    if (error.name !== "AbortError") addErrorMsg("续接执行事件失败: " + error.message);
  } finally {
    if (state.executionId === record.execution_id) {
      $("#stop").style.display = "none";
      setStreaming(false);
      loadExecutionHistory(record.session_id);
    }
  }
}

function replayExecutionFrame(frame) {
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
  state.sessionId = record.session_id || state.sessionId;
  state.executionStartedAt = Date.parse(record.started_at || record.created_at) || Date.now();
  state.executionSteps = 0;
  state.executionTools = 0;
  clearLivePlan();
  clearLiveOrchestrations();
  clearExecutionContext();

  const title = $("#workspace-title");
  if (title) title.textContent = truncateForWorkspace(record.input_preview || "历史执行");
  const timeline = $("#execution-timeline");
  if (timeline) timeline.replaceChildren();
  $("#timeline-count").textContent = "0";
  updateExecutionStatus("running", "回放中", "正在还原已采集的执行事件");

  events.forEach((event) => replayExecutionEvent(event));
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
    appendExecutionEvent("running", "模型完成决策", modelUsageDetail(payload, payload.is_final ? "正在组织最终回答" : "已确定下一步"), timestamp);
    return;
  }
  if (type === "tool.started") {
    markToolRunning(payload);
    appendExecutionEvent("running", "开始调用工具：" + String(payload.tool || ""), "", timestamp);
    return;
  }
  if (type === "tool.retry_scheduled") {
    recordToolRetry(payload, timestamp);
    return;
  }
  if (type === "tool_result") {
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
    appendExecutionEvent("success", "任务已完成", "", timestamp);
    return;
  }
  if (type === "execution.queued") {
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
    return true;
  } catch (e) {
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
    const count = $("#session-count");
    if (count) count.textContent = String((data.sessions || []).length);
    (data.sessions || []).forEach((s) => {
      const li = document.createElement("li");
      li.className = "session-item";
      li.dataset.sessionId = s.session_id;
      li.innerHTML = `
        <span class="si-icon">💬</span>
        <span class="si-name">${esc(s.session_id.slice(-16))}</span>
        <span class="si-time">${esc((s.updated_at || "").slice(11, 19))}</span>
        <span class="si-del" title="删除会话">✕</span>
      `;
      if (state.sessionId === s.session_id) li.classList.add("active");
      li.addEventListener("click", (e) => {
        if (e.target.classList.contains("si-del")) {
          e.stopPropagation();
          deleteSession(s.session_id);
          return;
        }
        openSession(s.session_id);
      });
      ul.appendChild(li);
    });
  } catch (e) { /* 忽略 */ }
}

async function openSession(sessionId) {
  state.sessionId = sessionId;
  setNavigationOpen(false);
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
  loadExecutionHistory(sessionId);
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
      if (i < data.plan.steps.length - 1) html += `<span class="pf-arrow">→</span>`;
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
  state.sessionId = null;
  state.executionId = null;
  state.executionStartedAt = null;
  state.executionSteps = 0;
  state.executionTools = 0;
  state.followLatest = true;
  clearLivePlan();
  setNavigationOpen(false);
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
  renderExecutionHistory([]);
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
    // 复制按钮（assistant 消息）
    const actions = document.createElement("div");
    actions.className = "msg-actions";
    const copyBtn = document.createElement("button");
    copyBtn.className = "copy-btn";
    copyBtn.textContent = "复制";
    copyBtn.addEventListener("click", () => {
      navigator.clipboard.writeText(String(content || "")).then(() => {
        copyBtn.textContent = "已复制 ✓";
        setTimeout(() => (copyBtn.textContent = "复制"), 1500);
      });
    });
    actions.appendChild(copyBtn);
    div.appendChild(actions);
  } else {
    contentEl.textContent = content;
  }
  div.appendChild(contentEl);
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
      <span class="ts-icon">${normalized.tool === "delegate" ? "🌐" : "🛠"}</span>
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
  $("#messages").appendChild(div);
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
  return window.matchMedia("(max-width: 920px)").matches;
}

function isMobileNavigationViewport() {
  return window.matchMedia("(max-width: 680px)").matches;
}

function isInspectorCollapsed() {
  return isCompactInspectorViewport()
    ? !document.body.classList.contains("inspector-expanded")
    : document.body.classList.contains("inspector-collapsed");
}

function setInspectorCollapsed(collapsed, focusControl = false) {
  const panel = $("#execution-panel");
  const closeButton = $("#toggle-inspector");
  const openButton = $("#open-inspector");
  const backdrop = $("#inspector-backdrop");

  if (isCompactInspectorViewport()) {
    document.body.classList.remove("inspector-collapsed");
    document.body.classList.toggle("inspector-expanded", !collapsed);
  } else {
    document.body.classList.remove("inspector-expanded");
    document.body.classList.toggle("inspector-collapsed", collapsed);
  }

  if (panel) {
    panel.setAttribute("aria-hidden", String(collapsed));
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
    const target = collapsed ? openButton : closeButton;
    if (target) requestAnimationFrame(() => target.focus());
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
    <span class="wf-title">🔍 Agent 工作流</span>
    <span class="wf-meta">${data.trace_id ? data.trace_id.slice(-12) : ""}${planInfo}</span>
    <span class="wf-toggle">收起 ▴</span>
  `;

  // Body
  const body = document.createElement("div");
  body.className = "workflow-body";
  body.innerHTML = renderWorkflowBody(data);
  header.addEventListener("click", () => {
    panel.classList.toggle("open");
    header.querySelector(".wf-toggle").textContent = panel.classList.contains("open") ? "收起 ▴" : "展开 ▾";
  });
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

/* ================= 发送 ================= */
async function send() {
  const input = $("#input");
  const message = input.value.trim();
  if (!message || state.streaming) return;

  addMessage("user", message);
  scrollToLatest();
  input.value = "";
  startExecution({ mode: state.agentMode, message_preview: message });
  setStreaming(true);

  const assistantEl = addMessage("assistant", "");
  assistantEl.classList.add("streaming");
  const contentEl = assistantEl.querySelector("div:last-child");
  contentEl.className = "md-body";
  contentEl.textContent = "";

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
    const executionId = resp.headers.get("X-Execution-ID");
    if (executionId) state.executionId = executionId;
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
        handleFrame(frame, contentEl, (ans, data) => { finalAnswer = ans; finalData = data; });
      }
    }
    if (finalAnswer) {
      contentEl.innerHTML = renderMarkdown(finalAnswer);
      // 补复制按钮
      ensureCopyButton(assistantEl, finalAnswer);
    }
    assistantEl.classList.remove("streaming");
    if (finalData) {
      state.sessionId = finalData.session_id;
      setLivePlan(finalData.plan, finalData.plan_version, finalData.plan_revisions);
      finishExecution("success", "已完成，可查看完整工作流");
      addWorkflowPanel({
        trace: finalData.trace, trace_id: finalData.trace_id,
        plan: finalData.plan, plan_revisions: finalData.plan_revisions,
        tool_calls: finalData.tool_calls,
      });
    }
  } catch (e) {
    if (e.name === "AbortError") {
      contentEl.textContent = "⏹ 已停止生成。";
      finishExecution("cancelled", "已停止接收执行结果");
      appendExecutionEvent("warning", "已请求停止", "服务端会清理当前执行任务");
    } else {
      assistantEl.classList.remove("streaming");
      contentEl.textContent = "⚠️ 请求失败: " + e.message;
      finishExecution("error", "请求失败");
      appendExecutionEvent("error", "请求失败", e.message);
    }
  }
  $("#stop").style.display = "none";
  setStreaming(false);
  loadSessions();
  // 编排记录可能新增（delegate 工具）
  if (state.sessionId) {
    loadOrchestrations(state.sessionId);
    loadExecutionHistory(state.sessionId);
  }
}

function ensureCopyButton(assistantEl, content) {
  if (assistantEl.querySelector(".copy-btn")) return;
  const actions = document.createElement("div");
  actions.className = "msg-actions";
  const copyBtn = document.createElement("button");
  copyBtn.className = "copy-btn";
  copyBtn.textContent = "复制";
  copyBtn.addEventListener("click", () => {
    navigator.clipboard.writeText(content).then(() => {
      copyBtn.textContent = "已复制 ✓";
      setTimeout(() => (copyBtn.textContent = "复制"), 1500);
    });
  });
  actions.appendChild(copyBtn);
  assistantEl.appendChild(actions);
}

function handleFrame(frame, contentEl, onComplete) {
  const lines = frame.split("\n");
  const eventLine = lines.find((l) => l.startsWith("event:"));
  const dataLine = lines.find((l) => l.startsWith("data:"));
  if (!eventLine || !dataLine) return;
  const event = eventLine.slice(6).trim();
  let data;
  try { data = JSON.parse(dataLine.slice(5).trim()); } catch (e) { return; }

  if (applyRuntimeLifecycleEvent(event, data, data.timestamp) || applyOrchestrationLifecycleEvent(event, data, data.timestamp) || applyPlanLifecycleEvent(event, data, data.timestamp)) return;

  switch (event) {
    case "execution.queued":
      if (data.execution_id && data.execution_id !== state.executionId) startExecution(data);
      updateExecutionStatus("pending", "排队中", executionQueueDetail(data));
      appendExecutionEvent("pending", "任务已进入执行队列", executionQueueDetail(data), data.timestamp);
      break;
    case "execution.started":
      if (data.execution_id && data.execution_id !== state.executionId) startExecution(data);
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
  $("#send").disabled = v;
  if (!v) state.abortCtrl = null;
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
  updateExecutionStatus("running", "停止请求已发送", "正在等待服务端确认取消");
  appendExecutionEvent("warning", "已请求停止", "服务端确认后将结束执行");
  try {
    const response = await fetch(
      "/api/web/executions/" + encodeURIComponent(state.executionId) + "/cancel",
      { method: "POST" }
    );
    if (!response.ok) throw new Error("HTTP " + String(response.status));
    const data = await response.json();
    if (!data.cancel_requested && !isActiveExecutionStatus(data.status)) {
      finishExecution("cancelled", "服务端已确认停止");
    }
  } catch (error) {
    button.disabled = false;
    addErrorMsg("停止请求失败: " + error.message);
  }
}

/* ================= 事件绑定 ================= */
function bindEvents() {
  const input = $("#input");
  const sendBtn = $("#send");
  const newBtn = $("#new-session");
  const stopBtn = $("#stop");
  const uploadBtn = $("#upload-btn");
  const fileInput = $("#file-input");
  const messages = $("#messages");
  const jumpLatest = $("#jump-latest");

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
  if (newBtn) newBtn.addEventListener("click", newSession);
  if (messages) {
    messages.addEventListener("scroll", () => {
      state.followLatest = isAtLatest(messages);
      updateLatestButton();
    }, { passive: true });
  }
  if (jumpLatest) jumpLatest.addEventListener("click", scrollToLatest);

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
  document.addEventListener("keydown", (event) => {
    if (event.key !== "Escape") return;
    if (document.body.classList.contains("navigation-open")) {
      setNavigationOpen(false, true);
    } else if (!isInspectorCollapsed()) {
      setInspectorCollapsed(true, true);
    }
  });

  const inspectorViewport = window.matchMedia("(max-width: 920px)");
  const navigationViewport = window.matchMedia("(max-width: 680px)");
  const syncResponsiveControls = () => {
    setInspectorCollapsed(isCompactInspectorViewport() ? !document.body.classList.contains("inspector-expanded") : document.body.classList.contains("inspector-collapsed"));
    setNavigationOpen(document.body.classList.contains("navigation-open"));
  };
  if (typeof inspectorViewport.addEventListener === "function") {
    inspectorViewport.addEventListener("change", syncResponsiveControls);
    navigationViewport.addEventListener("change", syncResponsiveControls);
  }
  setInspectorCollapsed(isCompactInspectorViewport());
  setNavigationOpen(false);

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
      document.querySelectorAll(".mode-btn").forEach((b) => b.classList.remove("active"));
      btn.classList.add("active");
      state.agentMode = btn.dataset.mode;
      $("#mode-badge").textContent = btn.dataset.mode === "plan" ? "Plan" : "ReAct";
      const inspectorMode = $("#inspector-mode");
      if (inspectorMode) inspectorMode.textContent = btn.dataset.mode === "plan" ? "Plan" : "ReAct";
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
