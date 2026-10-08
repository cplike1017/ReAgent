"""Approval replay cannot revive controls for a decided invocation."""
from pathlib import Path
import shutil
import subprocess

import pytest


def test_approval_reducer_and_decision_are_target_bound():
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node.js is unavailable")
    script = r'''
const assert = require("assert");
const fs = require("fs");
const vm = require("vm");
let source = fs.readFileSync("app/static/app.js", "utf8");
source = source.replace(/\r?\ninit\(\);\r?\nloadFiles\(\);\s*$/, "\n");
source += "\nglobalThis.testApi = { state, upsertApproval, isActionableApproval, submitApprovalDecision, bindApprovalExecutionContext };\n";
const requests = [];
const sandbox = { Date, JSON, Map, Math, Number, Set,
  document: { querySelector() { return null; }, querySelectorAll() { return []; } },
  fetch: async (url, options) => { requests.push({ url, options }); return { ok: true,
    json: async () => ({ ...record, status: "approved" }) }; },
};
vm.createContext(sandbox);
vm.runInContext(source, sandbox);
const { state, upsertApproval, isActionableApproval, submitApprovalDecision, bindApprovalExecutionContext } = sandbox.testApi;
const record = { approval_id: "approval_1", target_hash: "a".repeat(64), tool_name: "write_file",
  status: "pending", expires_at: new Date(Date.now() + 60000).toISOString(),
  context: { session_id: "s1", turn_id: "t1" }, arguments: { path: "safe.txt" } };
state.executionId = "e1";
bindApprovalExecutionContext({ execution_id: "e1", session_id: "s1", turn_id: "t1" });
assert.strictEqual(state.sessionId, "s1", "first queued frame binds the new session before the approval arrives");
bindApprovalExecutionContext({ execution_id: "other", session_id: "other", turn_id: "other" });
assert.strictEqual(state.sessionId, "s1", "another execution never changes the approval scope");
upsertApproval(record, { actionable: false });
assert.strictEqual(isActionableApproval(state.approvals.get(record.approval_id)), false, "historical request is read-only until refreshed");
upsertApproval(record, { actionable: true });
assert.strictEqual(isActionableApproval(state.approvals.get(record.approval_id)), true);
upsertApproval({ ...record, status: "rejected" });
upsertApproval(record, { actionable: true });
assert.strictEqual(state.approvals.get(record.approval_id).status, "rejected", "late replay or stale pending fetch never regresses a decision");
assert.strictEqual(isActionableApproval(state.approvals.get(record.approval_id)), false);
state.approvals.clear();
upsertApproval({ ...record, expires_at: new Date(Date.now() - 1).toISOString() }, { actionable: true });
assert.strictEqual(isActionableApproval(state.approvals.get(record.approval_id)), false, "expired local controls stay disabled");
state.approvals.clear();
upsertApproval(record, { actionable: true });
(async () => {
  const first = submitApprovalDecision(record.approval_id, "approved");
  const duplicate = submitApprovalDecision(record.approval_id, "approved");
  await Promise.all([first, duplicate]);
  assert.strictEqual(requests.length, 1, "double-click only submits one decision");
  const body = JSON.parse(requests[0].options.body);
  assert.strictEqual(body.target_hash, record.target_hash);
  assert.strictEqual(body.decision, "approved");
  assert.strictEqual(state.approvals.get(record.approval_id).status, "approved");
  assert.strictEqual(isActionableApproval(state.approvals.get(record.approval_id)), false);
})().catch(error => { process.exitCode = 1; console.error(error); });
'''
    result = subprocess.run([node, "-e", script], cwd=Path(__file__).resolve().parents[1], capture_output=True, text=True)
    assert result.returncode == 0, result.stdout + result.stderr
