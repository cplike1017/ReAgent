"""Desktop panes can hide independently without losing size or mobile behavior."""
from pathlib import Path

from tests.test_frontend_requests import run_js


ROOT = Path(__file__).resolve().parents[1]


def test_left_sidebar_has_a_reopen_control_outside_the_hidden_pane():
    page = (ROOT / "app/static/index.html").read_text(encoding="utf-8")
    assert 'id="toggle-sidebar"' in page
    header = page.split('<header class="global-header"', 1)[1].split('</header>', 1)[0]
    assert 'id="toggle-sidebar"' in header
    assert 'aria-controls="primary-navigation"' in header
    style = (ROOT / "app/static/style.css").read_text(encoding="utf-8")
    assert "body.sidebar-collapsed .app-shell" in style


def test_collapsed_other_pane_does_not_consume_resizing_budget():
    run_js('''
const classes = new Set(["sidebar-collapsed", "inspector-collapsed"]);
sandbox.document.body.classList.contains = name => classes.has(name);
sandbox.window = {innerWidth: 1600, matchMedia: () => ({matches: false})};
sandbox.document.querySelector = selector => ({getBoundingClientRect: () => ({width: selector === "#app-rail" ? 124 : 0})});
const paneWidthLimits = vm.runInContext("paneWidthLimits", sandbox);
assert.ok(paneWidthLimits("inspector").max >= 700, "hidden sidebar must release space for a wide inspector");
assert.ok(paneWidthLimits("sidebar").max >= 440, "sidebar can grow when inspector is hidden");
''')


def test_sidebar_collapse_persists_and_retains_width_preference():
    run_js('''
assert.strictEqual(vm.runInContext("typeof setSidebarCollapsed", sandbox), "function");
const classes = new Set();
const saved = new Map();
const panel = {inert: false, setAttribute() {}};
const toggle = {setAttribute() {}, focus() {}};
sandbox.document.body.classList = {contains: name => classes.has(name), toggle: (name, active) => active ? classes.add(name) : classes.delete(name)};
sandbox.document.querySelector = selector => ({"#primary-navigation": panel, "#toggle-sidebar": toggle}[selector] || null);
sandbox.window = {matchMedia: () => ({matches: false})};
sandbox.requestAnimationFrame = () => {};
sandbox.localStorage = {setItem: (key, value) => saved.set(key, value)};
vm.runInContext("uiState.paneWidths = {sidebar: 300, inspector: 450}", sandbox);
const collapse = vm.runInContext("setSidebarCollapsed", sandbox);
collapse(true);
assert.strictEqual(classes.has("sidebar-collapsed"), true);
assert.strictEqual(panel.inert, true);
assert.strictEqual(JSON.parse(saved.get("reagent-pane-visibility-v1")).sidebar, true);
assert.strictEqual(vm.runInContext("uiState.paneWidths.sidebar", sandbox), 300);
collapse(false);
assert.strictEqual(panel.inert, false);
assert.strictEqual(classes.has("sidebar-collapsed"), false);
''')


def test_startup_restores_hidden_panes_before_clamping_saved_widths():
    script = (ROOT / "app/static/app.js").read_text(encoding="utf-8")
    bindings = script.split("function bindEvents()", 1)[1]
    assert bindings.index("restorePaneVisibility();") < bindings.index("bindPaneResizers();"), (
        "a saved 720px inspector beside a hidden left pane must not be clamped against its invisible width"
    )


def test_reset_at_compact_width_clears_desktop_inspector_preference():
    run_js('''
let reset;
let saved;
sandbox.document.body.dataset = {theme: "light"};
sandbox.document.querySelector = selector => selector === "#clear-local-preferences" ? {addEventListener: (event, callback) => {reset = callback;}} : null;
sandbox.localStorage = {getItem: () => null, removeItem() {}, setItem: (key, value) => { if (key === "reagent-pane-visibility-v1") saved = JSON.parse(value); }};
sandbox.window = {matchMedia: () => ({matches: true})};
vm.runInContext(`
  uiState.desktopInspectorCollapsed = true;
  setSidebarCollapsed = () => persistPaneVisibility();
  setInspectorCollapsed = () => {};
  resetPaneWidth = setTimelineFilter = setInspectorTab = () => {};
  bindLocalSettings();
`, sandbox);
reset();
assert.strictEqual(saved.inspector, false, "resetting on mobile must reset desktop layout too");
''')
