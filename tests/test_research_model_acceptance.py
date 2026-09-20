"""Offline scoring contracts for the real-provider non-PPO literature task."""
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.tools.builtin import build_default_registry
from app.tools.registry import ToolDefinition, ToolRegistry
from evals.research_model_acceptance import (
    REQUIRED_TOOLS, _last_trace_id, _usage, build_task_prompt, ensure_real_provider,
    score_acceptance,
)


def test_real_model_gate_refuses_stub_configuration():
    with pytest.raises(ValueError, match="real provider"):
        ensure_real_provider(Settings(llm_provider="stub"))


def test_task_prompt_is_project_scoped_non_ppo_and_forbids_training():
    prompt = build_task_prompt("project_fixture", "version_fixture")
    assert "project_fixture" in prompt and "version_fixture" in prompt
    assert "non-PPO" in prompt and "不得执行训练" in prompt
    assert "page 1" in prompt and "逐字摘录" in prompt


def test_score_requires_tool_calls_and_persisted_linked_unverified_records():
    paper = SimpleNamespace(read_pages=[1])
    evidence = [SimpleNamespace(evidence_id="evidence_1", page=1, locator_verified=True)]
    claims = [SimpleNamespace(
        verification_status="unverified",
        evidence_links=[SimpleNamespace(evidence_id="evidence_1")],
    )]
    score = score_acceptance(list(REQUIRED_TOOLS), paper, evidence, claims, answer="done")
    assert score["passed"] is True
    assert score["missing_tools"] == [] and score["failures"] == []

    failed = score_acceptance(
        ["research_read_page"], SimpleNamespace(read_pages=[]), [], [], answer="",
    )
    assert failed["passed"] is False
    assert set(failed["missing_tools"]) == REQUIRED_TOOLS - {"research_read_page"}
    assert {"page_1_not_read", "no_evidence", "no_claim", "empty_answer"} <= set(failed["failures"])


def test_failed_llm_attempt_remains_counted_and_traceable(tmp_path):
    spans = [
        SimpleNamespace(name="llm_call", attributes={}),
        SimpleNamespace(name="agent.run", attributes={}),
    ]
    assert _usage(spans) == {
        "model_calls": 1, "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0,
    }
    trace = tmp_path / "trace.jsonl"
    trace.write_text(
        '{"trace_id":"trace_failed","name":"llm_call"}\n'
        '{"trace_id":"trace_failed","name":"agent.run"}\n',
        encoding="utf-8",
    )
    assert _last_trace_id(trace) == "trace_failed"


def test_acceptance_tool_allowlist_contains_only_required_schemas():
    registry = build_default_registry()
    for name in REQUIRED_TOOLS:
        registry.register(ToolDefinition(
            name=name, description=name, input_model=registry.all()[0].input_model,
            handler=lambda **_: None,
        ))
    selected = registry.subset(REQUIRED_TOOLS)
    assert isinstance(selected, ToolRegistry)
    assert {item.name for item in selected.all()} == REQUIRED_TOOLS
    assert len(selected.schemas()) == 4
