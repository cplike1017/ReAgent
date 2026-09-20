"""Research tools use real storage/gateway with deterministic, offline callers."""
import json

import pytest

from app.llm.client import LLMResponse, ToolCallRequest
from app.tools.policy import PolicyEngine
from app.tracing.recorder import TraceRecorder
from tests.test_research_foundation import research_client, research_settings


async def call(client, name, **args):
    result = await client.app.state.runtime.tool_gateway.execute(name, args)
    assert result.success, result.to_json()
    return json.loads(result.to_json())["data"]


async def project(client, title="DQN literature comparison"):
    return await call(client, "research_create_project", title=title,
                      question="Which evaluation assumptions need checking?")


async def source(client, project_id):
    artifact = await call(client, "research_import_artifact", project_id=project_id, path="paper.txt")
    paper = await call(client, "research_import_paper", project_id=project_id,
                       source="local", source_id="fixture", version="v1", title="Synthetic notes",
                       artifact_id=artifact["artifact_id"], content_scope="notes")
    return artifact, paper


async def test_tools_share_api_storage_and_preserve_provenance(research_client):
    client = research_client
    p = await project(client)
    pid = p["project_id"]
    assert client.get(f"/api/research/projects/{pid}").json() == p
    assert await call(client, "research_get_project", project_id=pid) == p
    assert (await call(client, "research_list_projects"))["items"] == [p]
    artifact, paper = await source(client, pid)
    assert (await call(client, "research_import_artifact", project_id=pid, path="paper.txt")) == artifact
    page = await call(client, "research_read_page", project_id=pid,
                      paper_version_id=paper["paper_version_id"], limit=12)
    assert page["truncated"] and page["next_offset"] == 12
    assert page["artifact_sha256"] == artifact["sha256"]
    assert page["content_scope"] == "notes"
    quote = "The baseline uses a fixed evaluation protocol."
    span = await call(client, "research_create_evidence", project_id=pid,
                      paper_version_id=paper["paper_version_id"], quote=quote)
    assert span["locator_verified"] and span["artifact_sha256"] == artifact["sha256"]
    claim = await call(client, "research_create_claim", project_id=pid, text=quote, kind="fact",
                       evidence_links=[{"evidence_id": span["evidence_id"], "relation": "background"}])
    assert claim["verification_status"] == "unverified"
    assert (await call(client, "research_list_records", project_id=pid, resource="claims"))["items"] == [claim]
    assert await call(client, "research_get_record", project_id=pid, resource="claims",
                      record_id=claim["claim_id"]) == claim
    report = await call(client, "research_export_report", project_id=pid)
    assert report["project_id"] == pid
    assert report["markdown"] == client.get(f"/api/research/projects/{pid}/report").text
    assert span["evidence_id"] in report["markdown"] and "unverified" in report["markdown"]
    comparison = await call(client, "research_export_comparison", project_id=pid)
    assert comparison == {
        "project_id": pid,
        "csv": client.get(f"/api/research/projects/{pid}/exports/comparison.csv").text,
    }
    assert paper["paper_version_id"] in comparison["csv"]
    citations = await call(client, "research_export_bibtex", project_id=pid)
    assert citations == {
        "project_id": pid,
        "bibtex": client.get(f"/api/research/projects/{pid}/exports/references.bib").text,
    }
    assert "Synthetic notes" in citations["bibtex"]


async def test_tools_reject_cross_project_and_fabricated_evidence(research_client):
    client = research_client
    first, other = await project(client), await project(client, "Literature only")
    _, paper = await source(client, first["project_id"])
    gateway = client.app.state.runtime.tool_gateway
    for name, args in [
        ("research_get_record", dict(project_id=other["project_id"], resource="papers",
                                     record_id=paper["paper_version_id"])),
        ("research_read_page", dict(project_id=other["project_id"], paper_version_id=paper["paper_version_id"])),
        ("research_create_evidence", dict(project_id=other["project_id"], paper_version_id=paper["paper_version_id"], quote="fixture")),
    ]:
        result = await gateway.execute(name, args)
        assert not result.success and result.error.code == "research_not_found"
    result = await gateway.execute("research_create_evidence", dict(
        project_id=first["project_id"], paper_version_id=paper["paper_version_id"], quote="Made up result"))
    assert not result.success and result.error.code == "research_invalid"
    assert (await call(client, "research_list_records", project_id=first["project_id"], resource="evidence"))["items"] == []


@pytest.mark.parametrize("name,args", [
    ("research_list_projects", {"limit": 201}),
    ("research_list_projects", {"offset": -1}),
    ("research_list_records", {"project_id": "p", "resource": "sessions"}),
    ("research_read_page", {"project_id": "p", "paper_version_id": "v", "offset": -1}),
    ("research_read_page", {"project_id": "p", "paper_version_id": "v", "limit": 20001}),
    ("research_read_page", {"project_id": "p", "paper_version_id": "v", "page": 0}),
    ("research_create_claim", {"project_id": "p", "text": "Claim", "verification_status": "verified"}),
    ("research_export_report", {}),
    ("research_export_comparison", {}),
    ("research_export_bibtex", {}),
])
async def test_tool_input_contracts(research_client, name, args):
    result = await research_client.app.state.runtime.tool_gateway.execute(name, args)
    assert not result.success and result.error.code == "ToolValidationError"


async def test_sandbox_and_policy_apply_to_research_tools(research_client):
    p = await project(research_client)
    gateway = research_client.app.state.runtime.tool_gateway
    result = await gateway.execute("research_import_artifact", {
        "project_id": p["project_id"], "path": "../outside.txt",
    })
    assert not result.success and result.error.code == "research_invalid"
    gateway.policy_engine = PolicyEngine(denied_tools=["research_create_claim"])
    result = await gateway.execute("research_create_claim", {"project_id": p["project_id"], "text": "blocked"})
    assert not result.success and result.error.code == "ToolPolicyError"
    assert research_client.get(f"/api/research/projects/{p['project_id']}/claims").json()["items"] == []


async def test_domain_failure_is_not_retried(research_client, monkeypatch):
    from app.research.errors import ResearchConflict
    service = research_client.app.state.research_service
    calls = []

    def conflict(request):
        calls.append(request)
        raise ResearchConflict("deterministic conflict")

    monkeypatch.setattr(service.repository, "create_project", conflict)
    result = await research_client.app.state.runtime.tool_gateway.execute("research_create_project", {
        "title": "A", "question": "B",
    })
    assert not result.success and result.error.code == "research_conflict"
    assert len(calls) == 1


async def test_agent_consumes_research_results_in_conversation_and_trace(research_client, tmp_path):
    client = research_client
    p = await project(client, "Non-PPO literature task")

    class ScriptedLLM:
        async def chat(self, messages, tools=None, **kwargs):
            assert "research_export_report" in {t["function"]["name"] for t in tools}
            results = [m for m in messages if m["role"] == "tool"]
            if not results:
                return LLMResponse(content=None, tool_calls=[ToolCallRequest(
                    id="read_project", name="research_export_report", arguments={"project_id": p["project_id"]},
                )], finish_reason="tool_calls")
            result = json.loads(results[-1]["content"])
            assert result["success"] and result["data"]["project_id"] == p["project_id"]
            return LLMResponse(content=result["data"]["markdown"])

    runtime = client.app.state.runtime
    runtime.llm = ScriptedLLM()
    recorder = TraceRecorder(str(tmp_path / "research-trace.jsonl"), enabled=True, capture_content=True)
    runtime.recorder = runtime.tool_gateway.recorder = recorder
    result = await runtime.run(f"Export the evidence report for {p['project_id']}; no training.")
    assert "Non-PPO literature task" in result.answer and "训练尚未执行" in result.answer
    assert [tc.name for tc in result.tool_calls] == ["research_export_report"]
    persisted = runtime.session_repo.list_messages(result.session_id)
    assert any(m["role"] == "tool" and p["project_id"] in m["content"] for m in persisted)
    trace = (tmp_path / "research-trace.jsonl").read_text(encoding="utf-8")
    assert "research_export_report" in trace and "tool.execute" in trace


def test_disabled_runtime_has_no_research_tools(settings):
    from fastapi.testclient import TestClient
    from app.main import create_app
    with TestClient(create_app(settings.model_copy(update={"research_enabled": False}))) as client:
        assert not any(t.name.startswith("research_") for t in client.app.state.runtime.registry.all())


async def test_offline_conversation_demo_preserves_reviewable_outputs(tmp_path):
    from demos.research_tools_demo import run_demo
    output = tmp_path / "demo"
    manifest = await run_demo(output)
    assert manifest["scripted_model"] is True and manifest["training_executed"] is False
    assert manifest["tool_calls"] == 7
    messages = json.loads((output / "conversation.json").read_text(encoding="utf-8"))
    results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
    assert len(results) == 7 and all(r["success"] for r in results)
    assert results[-2]["data"]["verification_status"] == "unverified"
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "DQN" in report and "PPO" not in report
    assert report == results[-1]["data"]["markdown"]
    spans = [json.loads(line) for line in (output / "trace.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(span["name"] == "tool.execute" for span in spans)
    llm_spans = [span for span in spans if span["name"] == "llm_call"]
    assert len(llm_spans) == 8
    assert all(span["attributes"]["model"] == "scripted-research-fixture" for span in llm_spans)
    with pytest.raises(FileExistsError):
        await run_demo(output)
