"""Regression coverage for client-side execution event ordering."""

from pathlib import Path
import shutil
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_execution_event_registry_deduplicates_and_isolates_runs():
    """A reconnect/replay must not duplicate, reorder, or mix execution events."""
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable; browser reducer test skipped")

    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");

let source = fs.readFileSync("app/static/app.js", "utf8");
source = source.replace(/\ninit\(\);\nloadFiles\(\);\s*$/, "\n");
source += "\nglobalThis.__executionEventTest = { state, resetExecutionEventCursor, registerExecutionEvent, timelineEventMatchesFilter, hasTimelineDetail, timelineDetailNeedsExpansion, canOpenExecutionHistory, openExecutionHistory, advanceExecutionViewVersion, isCurrentExecutionViewVersion, canChangeSession };\n";

const sandbox = { Date, JSON, Map, Math, Number, Set };
vm.createContext(sandbox);
vm.runInContext(source, sandbox, { filename: "app/static/app.js" });

const { state, resetExecutionEventCursor, registerExecutionEvent, timelineEventMatchesFilter, hasTimelineDetail, timelineDetailNeedsExpansion, canOpenExecutionHistory, openExecutionHistory, advanceExecutionViewVersion, isCurrentExecutionViewVersion, canChangeSession } = sandbox.__executionEventTest;
state.executionId = "execution-current";
resetExecutionEventCursor();

assert.strictEqual(registerExecutionEvent({
  execution_id: "execution-current", event_id: "event-1", seq: 1,
}), true);
assert.strictEqual(registerExecutionEvent({
  execution_id: "execution-current", event_id: "event-1", seq: 1,
}), false, "duplicate event id should be ignored");
assert.strictEqual(registerExecutionEvent({
  execution_id: "execution-current", event_id: "event-late", seq: 1,
}), false, "late sequence should be ignored");
assert.strictEqual(registerExecutionEvent({
  execution_id: "execution-other", event_id: "event-other", seq: 2,
}), false, "other execution must not alter the active panel");
assert.strictEqual(registerExecutionEvent({
  payload: { execution_id: "execution-current", event_id: "event-2", seq: 2 },
}), true, "persisted event metadata may arrive under payload");

resetExecutionEventCursor();
assert.strictEqual(registerExecutionEvent({
  execution_id: "execution-current", event_id: "event-1", seq: 1,
}), true, "a fresh replay accepts its own history from the beginning");

state.timelineFilter = "active";
assert.strictEqual(timelineEventMatchesFilter("running"), true);
assert.strictEqual(timelineEventMatchesFilter("success"), false);
state.timelineFilter = "attention";
assert.strictEqual(timelineEventMatchesFilter("warning"), true);
assert.strictEqual(timelineEventMatchesFilter("error"), true);
assert.strictEqual(timelineEventMatchesFilter("pending"), false);
state.timelineFilter = "all";
assert.strictEqual(timelineEventMatchesFilter("success"), true);
assert.strictEqual(hasTimelineDetail(0), true, "falsy numeric details remain observable");
assert.strictEqual(hasTimelineDetail(false), true, "falsy boolean details remain observable");
assert.strictEqual(hasTimelineDetail(""), false);
assert.strictEqual(timelineDetailNeedsExpansion("简短详情"), false);
assert.strictEqual(timelineDetailNeedsExpansion("x".repeat(97)), true);
assert.strictEqual(timelineDetailNeedsExpansion("第一行\n第二行"), true);
state.executionId = "execution-current";
state.streaming = true;
assert.strictEqual(canOpenExecutionHistory("execution-current"), true);
assert.strictEqual(canOpenExecutionHistory("execution-other"), false, "another live stream cannot overwrite the selected run");
const activeViewVersion = state.executionViewVersion;
openExecutionHistory("execution-current");
assert.strictEqual(state.executionViewVersion, activeViewVersion, "reselecting the active stream must not invalidate its subscription");
state.streaming = false;
assert.strictEqual(canOpenExecutionHistory("execution-other"), true);
const firstViewVersion = advanceExecutionViewVersion();
assert.strictEqual(isCurrentExecutionViewVersion(firstViewVersion), true);
const secondViewVersion = advanceExecutionViewVersion();
assert.strictEqual(isCurrentExecutionViewVersion(firstViewVersion), false, "a newer selection invalidates stale responses");
assert.strictEqual(isCurrentExecutionViewVersion(secondViewVersion), true);
state.streaming = true;
assert.strictEqual(canChangeSession(), false);
state.streaming = false;
assert.strictEqual(canChangeSession(), true);
'''

    result = subprocess.run(
        [node, "-e", script],
        cwd=PROJECT_ROOT,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0, result.stdout + result.stderr
