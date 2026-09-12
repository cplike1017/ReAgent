"""Execute real frontend async functions with deterministic network/DOM boundaries."""
from pathlib import Path
import shutil
import subprocess
import re

import pytest


ROOT = Path(__file__).resolve().parents[1]
HARNESS = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
const feedback = { hidden: true, textContent: "", dataset: {} };
const title = { textContent: "Old title" };
const sandbox = {
  document: {
    querySelector: (selector) => ({"#session-feedback": feedback, "#workspace-title": title}[selector] || null),
    querySelectorAll: () => [],
    body: { classList: { contains: () => false } },
  },
  console, setTimeout, clearTimeout,
};
vm.createContext(sandbox);
let source = fs.readFileSync("app/static/app.js", "utf8");
source = source.replace(/\r?\ninit\(\);\r?\nloadFiles\(\);\s*$/, "\n");
vm.runInContext(source, sandbox);
vm.runInContext(`
  globalThis.calls = [];
  renderSessionList = () => calls.push("list");
  renderLoadedSearchResults = () => {};
  renderHistory = (messages) => calls.push(["history", messages]);
  cacheSessionMessages = () => {};
  setNavigationOpen = () => {};
  setStatus = () => {};
  addErrorMsg = () => calls.push("chat-error");
  loadExecutionHistory = () => calls.push("executions");
  loadOrchestrations = () => calls.push("orchestrations");
  if (typeof resetExecutionPresentation === "function") {
    resetExecutionPresentation = () => { state.executionId = null; calls.push("reset"); };
  }
  globalThis.api = {state, init, loadSessions, openSession, advanceExecutionViewVersion};
`, sandbox);
const {state, init, loadSessions, openSession, advanceExecutionViewVersion} = sandbox.api;
const response = (status, body) => ({ok: status < 400, status, json: async () => body});
'''


def run_js(body):
    node = shutil.which("node")
    if not node:
        pytest.skip("Node.js unavailable")
    script = HARNESS + "\n(async () => {\n" + body + "\n})().catch(error => { console.error(error); process.exitCode = 1; });"
    result = subprocess.run([node, "-e", script], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", timeout=10)
    assert result.returncode == 0, result.stdout + result.stderr


def test_session_list_failure_preserves_loaded_sessions_and_reports_error():
    run_js('''
state.sessions = [{session_id: "existing"}];
sandbox.fetch = async () => response(503, {detail: "fixture unavailable"});
await loadSessions();
assert.strictEqual(state.sessions[0]?.session_id, "existing", "HTTP failure cannot erase the loaded sidebar");
assert.ok(feedback.textContent.includes("fixture unavailable"));
assert.strictEqual(feedback.hidden, false);
''')


@pytest.mark.parametrize("failure", ["response(503, {detail: 'fixture unavailable'})", "Promise.reject(new Error('network offline'))"])
def test_failed_session_selection_preserves_identity_and_transcript(failure):
    run_js(f'''
state.sessionId = "existing";
state.executionId = "existing-run";
sandbox.fetch = async () => {failure};
await openSession("unavailable");
assert.strictEqual(state.sessionId, "existing", "selection commits only after messages load");
assert.strictEqual(state.executionId, "existing-run");
assert.strictEqual(sandbox.calls.length, 0, "failed navigation must not mutate transcript or Inspector");
assert.strictEqual(title.textContent, "Old title");
assert.strictEqual(feedback.hidden, false);
''')


def test_session_selection_commits_after_loading_and_resets_old_inspector():
    run_js('''
state.sessionId = "existing";
state.executionId = "existing-run";
let resolve;
sandbox.fetch = () => new Promise(done => { resolve = done; });
const selection = openSession("next");
assert.strictEqual(state.sessionId, "existing", "pending selection must not change send target");
assert.strictEqual(feedback.hidden, false, "loading is visible");
resolve(response(200, {messages: [{role: "user", content: "next transcript"}]}));
await selection;
assert.strictEqual(state.sessionId, "next");
assert.strictEqual(state.executionId, null, "old execution cannot remain associated with new session");
assert.ok(sandbox.calls.some(call => Array.isArray(call) && call[0] === "history"));
assert.ok(sandbox.calls.includes("executions"));
''')


def test_superseded_session_response_cannot_replace_new_selection():
    run_js('''
const pending = [];
sandbox.fetch = () => new Promise(done => pending.push(done));
const older = openSession("older");
const newer = openSession("newer");
pending[1](response(200, {messages: []}));
await newer;
pending[0](response(200, {messages: [{role:"user", content:"stale"}]}));
await older;
assert.strictEqual(state.sessionId, "newer");
assert.strictEqual(sandbox.calls.filter(call => Array.isArray(call) && call[0] === "history").length, 1);
''')


def test_same_session_refresh_after_reconnect_retains_completed_execution():
    run_js('''
state.sessionId = "current";
state.executionId = "completed-run";
state.executionFinishedAt = 12345;
state.executionSteps = 7;
sandbox.fetch = async () => response(200, {messages: [{role: "assistant", content: "completed answer"} ]});
await openSession("current");
assert.strictEqual(state.executionId, "completed-run", "reconnect transcript refresh must preserve replayed Inspector");
assert.strictEqual(state.executionFinishedAt, 12345);
assert.strictEqual(state.executionSteps, 7);
assert.ok(!sandbox.calls.includes("reset"));
assert.ok(sandbox.calls.some(call => Array.isArray(call) && call[0] === "history"));
''')


def test_deleting_pending_session_prevents_its_late_response_from_reopening_it():
    run_js('''
state.sessionId = "existing";
state.sessions = [{session_id: "existing"}, {session_id: "deleted"}];
sandbox.confirm = () => true;
let resolveMessages;
sandbox.fetch = (url, options = {}) => {
  if (url.endsWith("/messages")) return new Promise(resolve => { resolveMessages = resolve; });
  if (options.method === "DELETE") return Promise.resolve(response(200, {deleted: "deleted"}));
  return Promise.resolve(response(200, {sessions: [{session_id: "existing"}]}));
};
const pending = openSession("deleted");
await vm.runInContext('deleteSession("deleted")', sandbox);
resolveMessages(response(200, {messages: [{role: "user", content: "deleted transcript"}]}));
await pending;
assert.strictEqual(state.sessionId, "existing", "late response must not reopen a successfully deleted session");
assert.ok(!sandbox.calls.some(call => Array.isArray(call) && call[0] === "history"));
''')


def test_ui_handlers_bind_before_slow_startup_requests():
    run_js('''
vm.runInContext(`
  renderWelcome = () => {};
  loadSessionMetadata = () => ({});
  bindEvents = () => calls.push("bound");
  loadCapabilities = () => new Promise(() => {});
  loadSessions = loadAgents = loadRuntimeSettings = async () => true;
`, sandbox);
void init();
assert.ok(sandbox.calls.includes("bound"), "slow startup endpoints cannot freeze navigation/theme/composer");
''')


def test_settings_cannot_save_defaults_before_configuration_loads():
    run_js('''
let writes = 0;
const query = sandbox.document.querySelector;
sandbox.document.querySelector = selector => query(selector) || (selector.startsWith("#settings-") ? {value: "", checked: false} : null);
sandbox.fetch = async () => { writes++; return response(200, {}); };
await vm.runInContext("saveRuntimeSettings()", sandbox);
assert.strictEqual(writes, 0, "unloaded settings must not overwrite server configuration");
''')


def test_sidebar_offers_an_accessible_refresh_action():
    page = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    assert 'id="refresh-sessions"' in page
    assert 'aria-label="刷新会话列表"' in page
    assert '$("#refresh-sessions")?.addEventListener("click", loadSessions)' in script


def test_compact_navigation_keeps_accessible_names_and_mobile_labels():
    page = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    buttons = re.findall(r'<button class="rail-item[^>]*>', page)
    assert len(buttons) == 8
    assert all('aria-label="' in button for button in buttons), "icon-only navigation needs persistent names"
    style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
    mobile = style.split("@media (max-width: 719px)", 1)[1]
    labels = mobile.split(".rail-item > span:nth-child(2)", 1)[1].split("}", 1)[0]
    assert "display: none" not in labels, "mobile navigation should identify destinations visually"


def test_late_session_list_response_cannot_overwrite_a_newer_refresh():
    run_js('''
const pending = [];
sandbox.fetch = () => new Promise(done => pending.push(done));
const older = loadSessions();
const newer = loadSessions();
pending[1](response(200, {sessions: [{session_id: "newer"}]}));
await newer;
pending[0](response(200, {sessions: [{session_id: "older"}]}));
await older;
assert.strictEqual(state.sessions[0].session_id, "newer");
''')


def test_successful_list_retry_clears_its_error_feedback():
    run_js('''
sandbox.fetch = async () => response(503, {detail: "fixture unavailable"});
await loadSessions();
assert.strictEqual(feedback.hidden, false);
sandbox.fetch = async () => response(200, {sessions: []});
await loadSessions();
assert.strictEqual(feedback.hidden, true, "a recovered list must not retain a stale failure banner");
''')


def test_session_title_uses_server_theme_and_never_falls_back_to_id():
    run_js('''
const sessionTitle = vm.runInContext("sessionTitle", sandbox);
const sessionPreview = vm.runInContext("sessionPreview", sandbox);
const session = {session_id: "opaque-internal-id", title: "如何改善前端交互？", preview: "已修复会话切换"};
assert.strictEqual(sessionTitle(session), session.title);
assert.strictEqual(sessionPreview(session), session.preview);
assert.strictEqual(sessionTitle({session_id: "opaque-internal-id"}), "新会话");
state.sessionMetadata[session.session_id] = {alias: "我的课题"};
assert.strictEqual(sessionTitle(session), "我的课题", "manual local aliases take precedence");
''')
