"""Offline scripted Agent/tool integration demo; no real model or training."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path

from app.agent.runtime import AgentRuntime
from app.config import Settings
from app.llm.client import LLMResponse, ToolCallRequest
from app.research.artifacts import ArtifactStore
from app.research.repository import SQLiteResearchRepository
from app.research.service import ResearchService
from app.research.tools import register_research_tools
from app.tools.registry import ToolRegistry
from app.tracing.recorder import TraceRecorder

QUOTE = "Evaluation assumptions must be recorded before comparing DQN results."


class ScriptedResearchLLM:
    """Fixed fixture decisions, consuming actual tool results for IDs and text.

    This verifies runtime plumbing, not a model's autonomous research ability.
    """

    async def chat(self, messages, tools=None, **kwargs):
        results = [json.loads(m["content"]) for m in messages if m["role"] == "tool"]
        for result in results:
            if not result["success"]:
                raise RuntimeError(f"Demo tool failed: {result['error']}")
        step = len(results)
        pid = results[0]["data"]["project_id"] if results else None
        if step == 0:
            name, args = "research_create_project", {
                "title": "DQN literature notes: synthetic integration fixture",
                "question": "Which evaluation assumptions need checking before comparison?",
                "scope": "Synthetic local notes only; no paper claims or experimental results.",
            }
        elif step == 1:
            name, args = "research_import_artifact", {"project_id": pid, "path": "notes.txt"}
        elif step == 2:
            name, args = "research_import_paper", {
                "project_id": pid, "source": "local", "source_id": "synthetic-dqn-notes",
                "version": "v1", "title": "Synthetic DQN evaluation notes",
                "artifact_id": results[1]["data"]["artifact_id"], "content_scope": "notes",
            }
        elif step == 3:
            name, args = "research_read_page", {
                "project_id": pid, "paper_version_id": results[2]["data"]["paper_version_id"],
            }
        elif step == 4:
            if QUOTE not in results[3]["data"]["text"]:
                raise RuntimeError("Fixture quote absent from actual source text")
            name, args = "research_create_evidence", {
                "project_id": pid, "paper_version_id": results[2]["data"]["paper_version_id"],
                "quote": QUOTE,
            }
        elif step == 5:
            name, args = "research_create_claim", {
                "project_id": pid, "text": "The synthetic notes request explicit evaluation assumptions.",
                "kind": "fact", "evidence_links": [{
                    "evidence_id": results[4]["data"]["evidence_id"], "relation": "background",
                }],
            }
        elif step == 6:
            name, args = "research_export_report", {"project_id": pid}
        else:
            return LLMResponse(content=results[-1]["data"]["markdown"])
        if name not in {tool["function"]["name"] for tool in tools or []}:
            raise RuntimeError(f"Required tool not exposed to model: {name}")
        return LLMResponse(content=None, finish_reason="tool_calls", tool_calls=[
            ToolCallRequest(id=f"research_demo_{step}", name=name, arguments=args),
        ])


async def run_demo(output: Path) -> dict:
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = output / "sources"
    sources.mkdir()
    (sources / "notes.txt").write_text(
        "Synthetic integration fixture, not an actual paper or experiment.\n" + QUOTE + "\n",
        encoding="utf-8",
    )
    repository = SQLiteResearchRepository(f"sqlite:///{(output / 'research.db').as_posix()}")
    try:
        service = ResearchService(repository, ArtifactStore(str(output / "artifacts"), str(sources), 1048576))
        registry = ToolRegistry()
        register_research_tools(registry, service)
        settings = Settings(
            _env_file=None, environment="test", llm_provider="stub", embedding_provider="stub",
            llm_model="scripted-research-fixture",
            agent_mode="react", max_agent_steps=10, memory_enabled=False,
            max_context_messages=32, context_summary_strategy="off",
            skills_enabled=False, orchestrator_enabled=False, trace_enabled=True,
            policy_require_confirmation_risks="",
        )
        recorder = TraceRecorder(str(output / "trace.jsonl"), enabled=True, capture_content=True)
        runtime = AgentRuntime(llm=ScriptedResearchLLM(), registry=registry, settings=settings, recorder=recorder)
        result = await runtime.run(
            "Archive the synthetic DQN notes, read the source, link evidence and export a report. No training."
        )
        (output / "conversation.json").write_text(
            json.dumps(result.messages, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        (output / "report.md").write_text(result.answer, encoding="utf-8")
        manifest = {
            "project_id": repository.list_projects()[0].project_id,
            "scripted_model": True, "synthetic_sources": True, "training_executed": False,
            "autonomous_research_evaluated": False, "tool_calls": len(result.tool_calls),
            "report_sha256": hashlib.sha256((output / "report.md").read_bytes()).hexdigest(),
            "trace_id": result.trace_id,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        return manifest
    finally:
        repository.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(asyncio.run(run_demo(args.output)), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
