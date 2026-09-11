"""Executable contracts for the responsive Mist Mint workspace shell."""

from app.main import create_app
from app.config import Settings
from fastapi.testclient import TestClient
import fakeredis.aioredis


def _make_app(tmp_path):
    settings = Settings(
        environment="test",
        llm_provider="stub",
        database_url=f"sqlite:///{tmp_path}/frontend-ui.db",
        trace_file=str(tmp_path / "frontend-ui.jsonl"),
        skills_enabled=False,
        memory_enabled=False,
    )
    return create_app(
        settings,
        redis=fakeredis.aioredis.FakeRedis(decode_responses=True),
    )


def test_chat_transcript_owns_scroll_and_composer_stays_in_shell(tmp_path):
    """The desktop shell must constrain height so only the transcript scrolls."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    assert "html, body { height: 100%;" in style
    assert ".studio-shell" in style and "height: 100dvh" in style
    app_shell = style.split(".app-shell {", 1)[1].split("}", 1)[0]
    assert "overflow: hidden" in app_shell
    messages = style.split(".messages {", 1)[1].split("}", 1)[0]
    assert "overflow-y: auto" in messages
    assert "overscroll-behavior: contain" in messages


def test_desktop_panes_expose_accessible_persisted_splitters(tmp_path):
    """Sessions and Inspector widths are adjustable without inventing API state."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        style = client.get("/style.css").text
        script = client.get("/app.js").text

    assert page.count('role="separator"') == 2
    assert 'id="sidebar-resizer"' in page
    assert 'id="inspector-resizer"' in page
    assert 'aria-orientation="vertical"' in page
    assert 'tabindex="0"' in page
    assert "--sidebar-width" in style
    assert "--inspector-width" in style
    assert "function bindPaneResizers()" in script
    assert "reagent-pane-widths-v1" in script
    assert 'event.key === "ArrowLeft"' in script
    assert '"ArrowRight"' in script
    assert "setPointerCapture" in script


def test_tablet_and_mobile_drop_splitters_and_keep_resource_workspace_usable(tmp_path):
    """Responsive modes replace desktop resizing with drawers/full-width content."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    compact_rules = style.split("@media (max-width: 1279px)", 1)[1]
    assert ".pane-resizer { display: none; }" in compact_rules
    assert ".workspace," in compact_rules and "grid-template-columns: minmax(0, 1fr);" in compact_rules
    assert 'body[data-primary-view]:not([data-primary-view="chat"]) .workspace' in compact_rules
    tablet_rules = compact_rules.split("@media (max-width: 959px)", 1)[1]
    assert ".sidebar" in tablet_rules and "position: fixed" in tablet_rules
    assert ".icon-btn.mobile-nav-toggle { display: inline-grid;" in tablet_rules


def test_desktop_hides_the_compact_navigation_toggle(tmp_path):
    """The generic icon-button display rule must not override desktop hiding."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    assert ".icon-btn.mobile-nav-toggle { display: none;" in style


def test_dark_theme_rebinds_compatibility_aliases(tmp_path):
    """Dark semantic tokens must reach legacy selectors using compatibility names."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    dark = style.split('body[data-theme="dark"] {', 1)[1].split("}", 1)[0]
    for alias in ("--bg:", "--surface:", "--surface-subtle:", "--text:", "--text-muted:", "--border:"):
        assert alias in dark


def test_new_task_resets_all_execution_presentation(tmp_path):
    """A fresh task cannot retain the prior title, status, timer, timeline, or stop control."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    body = script.split("function newSession()", 1)[1].split("/* ================= 消息渲染", 1)[0]
    assert "window.clearInterval(state.executionTimer)" in body
    assert 'title.textContent = "开始一个新任务"' in body
    assert 'updateExecutionStatus("idle", "准备就绪"' in body
    assert "timeline.replaceChildren()" in body
    assert 'stop.style.display = "none"' in body
    assert "renderExecutionMetrics();" in body


def test_replay_uses_record_mode_and_streaming_locks_mode_controls(tmp_path):
    """History mode is factual and cannot be changed while its stream is active."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    replay = script.split("function replayExecution(record, events)", 1)[1].split("function replayExecutionEvent", 1)[0]
    streaming = script.split("function setStreaming(v)", 1)[1].split("async function stopStreaming", 1)[0]
    assert "setAgentMode(record.agent_mode" in replay
    assert '$$(".mode-btn").forEach' in streaming
    assert "button.disabled = v" in streaming


def test_search_session_result_enters_chat_and_focus_is_trapped(tmp_path):
    """Search behaves as a modal and session selection reveals the loaded conversation."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    search = script.split("function renderLoadedSearchResults()", 1)[1].split("function setInspectorSection", 1)[0]
    assert 'setPrimaryView("chat");' in search
    assert "function trapLoadedSearchFocus(event)" in script
    assert "if (trapLoadedSearchFocus(event)) return;" in script
    focusable = script.split("function isElementFocusable(element)", 1)[1].split(
        "function getInspectorFocusableElements", 1
    )[0]
    assert 'element.getAttribute("tabindex") === "-1"' in focusable
    assert 'id="search-backdrop" class="search-backdrop" type="button" tabindex="-1"' in page
    assert page.count('role="tab"') >= 10
    assert page.count('tabindex="-1"') >= 8


def test_resource_failures_are_local_and_preserve_success_state(tmp_path):
    """HTTP failures must be visible without clearing data or mutating session state."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    load_files = script.split("async function loadFiles()", 1)[1].split("/* ================= 会话删除", 1)[0]
    delete_session = script.split("async function deleteSession(sessionId)", 1)[1].split("init();", 1)[0]
    upload = script.split('fileInput.addEventListener("change", async () => {', 1)[1].split('$(`[data-composer-tools]`)', 1)[0] if '$(`[data-composer-tools]`)' in script else script.split('fileInput.addEventListener("change", async () => {', 1)[1].split('$("[data-composer-tools]")', 1)[0]
    assert 'id="resource-feedback"' in page
    assert 'id="session-feedback"' in page
    assert 'id="upload-btn"' in page and 'id="upload-btn" class="mini-btn" type="button" data-upload-trigger' in page
    assert "if (!response.ok) throw new Error" in load_files
    assert "setResourceFeedback" in load_files
    assert "if (!response.ok) throw new Error" in delete_session
    assert "setSessionFeedback" in delete_session
    assert "addToolMsg" not in upload
    assert "addErrorMsg" not in upload


def test_inspector_contains_compact_calls_and_trustworthy_run_summary(tmp_path):
    """Timeline exposes invocation facts and a summary without fabricated cost."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    for element_id in (
        "compact-call-graph", "compact-tool-calls", "inspector-run-summary",
        "summary-status", "summary-mode", "summary-decisions", "summary-plan-steps",
        "summary-model", "summary-tokens", "summary-tool-calls", "summary-duration", "summary-cost",
    ):
        assert f'id="{element_id}"' in page
    assert "function renderCompactCalls()" in script
    assert "function renderInspectorRunSummary()" in script
    assert "当前接口无成本数据" in page


def test_showing_timeline_redraws_compact_call_edges(tmp_path):
    """A graph rendered while hidden must draw its edges once Timeline becomes visible."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    inspector_tab = script.split("function setInspectorTab(tab)", 1)[1].split(
        "function setCompactCallView", 1
    )[0]
    assert 'if (tab === "timeline")' in inspector_tab
    assert 'drawCompactCallLines($("#compact-call-graph"))' in inspector_tab


def test_reopening_inspector_redraws_visible_graphs(tmp_path):
    """Graphs first measured while hidden must be rebuilt after the panel reopens."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    collapsed = script.split("function setInspectorCollapsed(collapsed", 1)[1].split(
        "function setNavigationOpen", 1
    )[0]
    assert "redrawVisibleInspectorGraphs" in collapsed
    assert "if (!collapsed)" in collapsed
    assert "requestAnimationFrame" in collapsed


def test_returning_to_execution_section_redraws_visible_graphs(tmp_path):
    """Outer Inspector navigation must redraw graphs hidden under Files or Settings."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    section = script.split("function setInspectorSection(section)", 1)[1].split(
        "function setInspectorTab", 1
    )[0]
    assert 'if (section === "execution")' in section
    assert "requestAnimationFrame(redrawVisibleInspectorGraphs)" in section


def test_compact_navigation_is_an_accessible_exclusive_drawer(tmp_path):
    """The Sessions drawer traps focus, makes its background inert, and excludes Inspector."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    navigation = script.split("function setNavigationOpen(open", 1)[1].split(
        "function esc", 1
    )[0]
    inspector = script.split("function setInspectorCollapsed(collapsed", 1)[1].split(
        "function setNavigationOpen", 1
    )[0]
    assert "function trapNavigationFocus(event)" in script
    assert "if (trapNavigationFocus(event)) return;" in script
    assert "state.navigationReturnFocus" in navigation
    assert 'navigation.setAttribute("role", "dialog")' in navigation
    assert 'navigation.setAttribute("aria-modal", "true")' in navigation
    assert "setDrawerBackgroundInert" in navigation
    assert "setInspectorCollapsed(true" in navigation
    assert "setNavigationOpen(false" in inspector
    assert 'id="navigation-backdrop"' in page and 'tabindex="-1"' in page


def test_search_result_restores_focus_before_changing_workspace(tmp_path):
    """Activating a result must not strand focus inside the now-hidden search dialog."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    search = script.split("function renderLoadedSearchResults()", 1)[1].split(
        "function setInspectorSection", 1
    )[0]
    assert "closeLoadedSearch(false);" in search
    assert "focusPrimaryWorkspace" in search
    assert "function focusPrimaryWorkspace(view)" in script


def test_search_falls_back_from_a_closing_drawer_and_blocks_modal_stacking(tmp_path):
    """Search gets a stable return target and cannot stack over the Agent dialog."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    open_search = script.split("function openLoadedSearch()", 1)[1].split(
        "function trapLoadedSearchFocus", 1
    )[0]
    keydown = script.split('document.addEventListener("keydown", (event) => {', 1)[1].split(
        'const inspectorViewport = window.matchMedia', 1
    )[0]
    assert '$("#primary-navigation")?.contains(returnFocus)' in open_search
    assert 'state.searchReturnFocus =' in open_search and '$("#toggle-navigation")' in open_search
    assert 'if (!$("#agent-dialog")?.hidden)' in keydown
    assert "openLoadedSearch();" in keydown


def test_session_drawer_actions_restore_focus_when_they_close_it(tmp_path):
    """Opening a session or New Task from the compact drawer restores its launcher."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    open_session = script.split("async function openSession(sessionId)", 1)[1].split(
        "async function openExecutionHistory", 1
    )[0]
    new_session = script.split("function newSession()", 1)[1].split(
        "/* ================= 消息渲染", 1
    )[0]
    for body in (open_session, new_session):
        assert "const navigationWasOpen" in body
        assert "setNavigationOpen(false, navigationWasOpen)" in body


def test_capability_and_agent_loaders_isolate_http_failures(tmp_path):
    """One failed endpoint preserves successful resource lists and reports the failed scope."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    capabilities = script.split("async function loadCapabilities()", 1)[1].split(
        "/* ================= 子 Agent 档案", 1
    )[0]
    agents = script.split("async function loadAgents()", 1)[1].split(
        "async function unregisterAgent", 1
    )[0]
    assert "Promise.allSettled" in capabilities
    assert "state.resources[result.config.key]" in capabilities
    assert "部分能力读取失败" in capabilities
    assert "responses.some" not in capabilities
    assert "if (!response.ok)" in agents
    assert "setResourceFeedback" in agents
    assert "return false" in agents


def test_agent_management_uses_a_modal_and_local_feedback(tmp_path):
    """CRUD controls belong to the Agents workspace and never fabricate chat execution cards."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    register = script.split("async function registerAgent()", 1)[1].split(
        "function showAgentError", 1
    )[0]
    unregister = script.split("async function unregisterAgent(name)", 1)[1].split(
        "async function registerAgent", 1
    )[0]
    assert 'id="agent-dialog"' in page
    assert 'role="dialog"' in page
    assert 'aria-modal="true"' in page
    assert "function openAgentDialog()" in script
    assert "function closeAgentDialog(" in script
    assert "function trapAgentDialogFocus(event)" in script
    assert "setResourceFeedback" in register
    assert "setResourceFeedback" in unregister
    assert "addToolMsg" not in register
    assert "addToolMsg" not in unregister
    assert "addErrorMsg" not in register
    assert "addErrorMsg" not in unregister


def test_saved_pane_widths_survive_an_initial_compact_viewport(tmp_path):
    """Widths loaded on tablet are retained and applied after widening to desktop."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    binding = script.split("function bindPaneResizers()", 1)[1].split(
        "function isInspectorCollapsed", 1
    )[0]
    assert "uiState.paneWidths[name] = savedWidth" in binding
    assert "if (!isCompactInspectorViewport()) setPaneWidth" in binding


def test_inspector_files_mirror_and_settings_are_real_local_controls(tmp_path):
    """Outer Inspector tabs consume shared file state and local preferences."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        script = client.get("/app.js").text

    assert 'id="inspector-file-list"' in page
    assert 'data-inspector-upload data-upload-trigger' in page
    assert 'id="settings-theme-toggle"' in page
    assert 'id="settings-default-tab"' in page
    assert 'id="clear-local-preferences"' in page
    assert 'id="settings-feedback"' in page
    assert "function renderInspectorFiles()" in script
    assert "function bindLocalSettings()" in script
    assert "reagent-default-inspector-tab" in script


def test_historical_orchestration_uses_real_dependencies_in_visible_inspector(tmp_path):
    """Opening persisted orchestration data must not infer sequence from array order."""
    with TestClient(_make_app(tmp_path)) as client:
        script = client.get("/app.js").text

    open_body = script.split("async function openOrchestrationDetail(runId)", 1)[1].split("function renderOrchestrationPanel", 1)[0]
    old_renderer = script.split("function renderOrchestrationBody(data)", 1)[1].split("function bindOrchestrationToggles", 1)[0]
    assert 'setPrimaryView("chat")' in open_body
    assert 'setInspectorTab("agents")' in open_body
    assert "hydrateHistoricalOrchestration(data)" in open_body
    assert "if (i < data.plan.steps.length - 1)" not in old_renderer
    assert "function drawAgentDependencyLines" in script
    assert "createElementNS" in script
    assert "关系不可用" in script


def test_execution_progress_precedes_final_assistant_answer(tmp_path):
    """Tool/workflow progress belongs before the active streaming answer in DOM order."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text
        script = client.get("/app.js").text

    helper = script.split("function insertExecutionProgress", 1)[1].split("function addToolMsg", 1)[0]
    send = script.split("async function send()", 1)[1].split("function ensureCopyButton", 1)[0]
    assert "activeAssistantElement" in script
    assert "insertBefore(element, anchor)" in helper
    assert "insertExecutionProgress(div)" in script
    assert "state.activeAssistantElement = assistantEl" in send
    assert 'className = "assistant-mark"' in script
    assistant = style.split(".msg.assistant {", 1)[1].split("}", 1)[0]
    assert "background: transparent" in assistant
    assert "border: 0" in assistant
    assert "box-shadow: none" in assistant


def test_loaded_session_copy_is_memory_cached_and_recent_uses_calendar_days(tmp_path):
    """Loaded messages improve navigation copy without persisting sensitive content."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        style = client.get("/style.css").text
        script = client.get("/app.js").text

    assert "sessionContentCache: new Map()" in script
    assert "function cacheSessionMessages" in script
    assert "state.sessionContentCache.get" in script
    cache = script.split("function cacheSessionMessages", 1)[1].split(
        "function updateSessionMetadata", 1
    )[0]
    assert "existing && existing.title" in cache
    assert "cacheSessionMessages(sessionId, data.messages" in script
    assert "function recentSessionCutoff" in script
    recent = script.split("function filteredSessions()", 1)[1].split("function renderSessionList", 1)[0]
    assert "recentSessionCutoff()" in recent
    assert "7 * 86400000" not in recent
    assert 'data-session-open' in script
    assert 'class="session-scope"' in page
    sessions = style.split(".sessions-section {", 1)[1].split("}", 1)[0]
    session_list = style.split(".session-list {", 1)[1].split("}", 1)[0]
    assert "flex: 1" in sessions
    assert "flex: 1" in session_list
    assert "max-height" not in session_list


def test_primary_shell_and_live_tool_cards_use_inline_svg_icons(tmp_path):
    """Core navigation and execution cards use one stroke-based SVG icon language."""
    with TestClient(_make_app(tmp_path)) as client:
        page = client.get("/").text
        style = client.get("/style.css").text
        script = client.get("/app.js").text

    assert 'class="icon-sprite"' in page
    assert page.count('<use href="#icon-') >= 14
    assert 'class="brand-emblem"' in page
    assert "function uiIconMarkup" in script
    tool = script.split("function addToolMsg(data)", 1)[1].split("function parseDelegateOutput", 1)[0]
    assert "uiIconMarkup" in tool
    assert "🛠" not in tool
    assert ".ui-icon" in style


def test_desktop_inspector_starts_beside_workspace_header(tmp_path):
    """The desktop Inspector is a first-class full-height pane under the global header."""
    with TestClient(_make_app(tmp_path)) as client:
        style = client.get("/style.css").text

    desktop = style.split("@media (min-width: 1280px)", 1)[1].split(
        "@media (min-width: 1280px) and (max-width: 1439px)", 1
    )[0]
    assert 'body[data-primary-view="chat"]:not(.inspector-collapsed) .chat-header' in desktop
    assert "margin-right: calc(var(--inspector-width) + var(--pane-resizer-size))" in desktop
    assert 'body[data-primary-view="chat"] .execution-panel' in desktop
    assert "margin-top: -86px" in desktop
    assert 'body[data-primary-view="chat"] .workspace { overflow: visible; }' in desktop
