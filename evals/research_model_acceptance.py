"""Run one isolated real-provider research task over a public non-PPO paper."""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

import pymupdf

from app.config import Settings, get_settings
from app.main import build_web_runtime
from app.research.artifacts import ArtifactStore
from app.research.literature import ArxivClient
from app.research.models import ProjectCreate
from app.research.repository import SQLiteResearchRepository
from app.research.service import ResearchService
from app.tools.schemas import UserContext
from app.tracing.recorder import TraceRecorder

ARXIV_ID = "1312.5602v1"
TASK_ID = "research-model-dqn-v1"
REQUIRED_TOOLS = frozenset({
    "research_read_page",
    "research_create_evidence",
    "research_create_claim",
    "research_export_report",
})


def ensure_real_provider(settings: Settings) -> None:
    """Fail closed so the acceptance label can never describe a stub run."""
    if settings.llm_provider_resolved == "stub":
        raise ValueError("research model acceptance requires a real provider")


def build_task_prompt(project_id: str, paper_version_id: str) -> str:
    """Build the fixed, project-scoped instruction given to the real model."""
    return f"""你正在执行一个公开论文的 non-PPO 文献证据任务。

项目 ID：{project_id}
论文版本 ID：{paper_version_id}

必须严格按以下顺序完成：
1. 调用 research_read_page 读取该论文的 page 1（offset=0，limit=6000）。
2. 只从工具返回的 page 1 文本中选择一段较短、连续的逐字摘录，不得改写原文。
3. 调用 research_create_evidence，将这段摘录保存到上述项目和论文版本的 page 1。
4. 调用 research_create_claim，保存一条由该摘录支持的 fact，并用 supports 关联刚创建的 evidence_id。
5. 调用 research_export_report 导出项目报告。
6. 最终回答列出 evidence_id、claim_id，并说明主张仍为 unverified、只读取了所返回的页面片段。

不得执行训练，不得调用与任务无关的工具，不得声称读完全文或完成科学结论核验。"""


def score_acceptance(
    tool_names: Iterable[str], paper: Any, evidence: list[Any], claims: list[Any], answer: str,
) -> dict[str, Any]:
    """Score observable model actions and persisted records without semantic inflation."""
    called = set(tool_names)
    missing_tools = sorted(REQUIRED_TOOLS - called)
    failures: list[str] = []
    if 1 not in getattr(paper, "read_pages", []):
        failures.append("page_1_not_read")
    if not evidence:
        failures.append("no_evidence")
    elif not any(
        getattr(item, "page", None) == 1 and getattr(item, "locator_verified", False)
        for item in evidence
    ):
        failures.append("no_verified_page_1_locator")
    if not claims:
        failures.append("no_claim")
    else:
        evidence_ids = {getattr(item, "evidence_id", None) for item in evidence}
        linked = any(
            getattr(claim, "verification_status", None) == "unverified"
            and any(
                getattr(link, "evidence_id", None) in evidence_ids
                for link in getattr(claim, "evidence_links", [])
            )
            for claim in claims
        )
        if not linked:
            failures.append("no_linked_unverified_claim")
    if not answer.strip():
        failures.append("empty_answer")
    return {
        "passed": not missing_tools and not failures,
        "missing_tools": missing_tools,
        "failures": failures,
        "page_1_read": 1 in getattr(paper, "read_pages", []),
        "evidence_count": len(evidence),
        "claim_count": len(claims),
    }


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _usage(spans: list[Any]) -> dict[str, int]:
    calls = [span for span in spans if span.name == "llm_call"]
    return {
        "model_calls": len(calls),
        "prompt_tokens": sum(int(span.attributes.get("prompt_tokens", 0) or 0) for span in calls),
        "completion_tokens": sum(int(span.attributes.get("completion_tokens", 0) or 0) for span in calls),
        "total_tokens": sum(int(span.attributes.get("total_tokens", 0) or 0) for span in calls),
    }


def _last_trace_id(trace_file: Path) -> str | None:
    """Recover the run trace when AgentRuntime raises before returning a result."""
    if not trace_file.is_file():
        return None
    trace_id = None
    for line in trace_file.read_text(encoding="utf-8").splitlines():
        try:
            candidate = json.loads(line).get("trace_id")
        except (json.JSONDecodeError, AttributeError):
            continue
        if candidate:
            trace_id = candidate
    return trace_id


async def run_acceptance(output: Path, arxiv_id: str = ARXIV_ID) -> dict[str, Any]:
    """Import a pinned public PDF, run the real model, and save reviewable evidence."""
    base = get_settings()
    ensure_real_provider(base)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sandbox = output / "sandbox"
    sandbox.mkdir()
    settings = base.model_copy(update={
        "database_url": f"sqlite:///{(output / 'research.db').as_posix()}",
        "research_enabled": True,
        "research_artifact_dir": str(output / "artifacts"),
        "sandbox_dir": str(sandbox),
        "runtime_config_file": str(output / "runtime-settings.json"),
        "trace_file": str(output / "trace.jsonl"),
        "trace_enabled": True,
        "trace_capture_content": False,
        "memory_enabled": False,
        "orchestrator_enabled": False,
        "skills_enabled": False,
        "environment": "test",
        "mcp_auto_register": False,
        "agent_mode": "react",
        "max_agent_steps": 10,
        "max_context_messages": 30,
        "context_summary_strategy": "off",
    })
    ensure_real_provider(settings)

    repository = SQLiteResearchRepository(settings.database_url)
    runtime = None
    manifest: dict[str, Any] = {}
    try:
        service = ResearchService(
            repository,
            ArtifactStore(
                settings.research_artifact_dir,
                settings.sandbox_dir,
                settings.research_max_artifact_bytes,
            ),
            ArxivClient(settings),
        )
        project = repository.create_project(ProjectCreate(
            title="DQN public literature real-model acceptance",
            question="Can the agent preserve one exact, page-located claim from the public DQN paper?",
            scope="One pinned public non-PPO arXiv PDF; evidence workflow only; no training.",
        ))
        imported = service.import_arxiv_full_text(project.project_id, arxiv_id)
        paper = repository.get(project.project_id, "papers", imported["paper"]["paper_version_id"])
        artifact = repository.get(project.project_id, "artifacts", paper.artifact_id)
        with pymupdf.open(service.artifacts.verified_path(artifact)) as document:
            pdf_pages = document.page_count

        prompt = build_task_prompt(project.project_id, paper.paper_version_id)
        (output / "task.json").write_text(json.dumps({
            "task_id": TASK_ID,
            "arxiv_id": arxiv_id,
            "project_id": project.project_id,
            "paper_version_id": paper.paper_version_id,
            "prompt": prompt,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

        recorder = TraceRecorder(
            settings.trace_file, enabled=True, capture_content=settings.trace_capture_content,
        )
        runtime = build_web_runtime(
            settings, recorder, service, tool_allowlist=REQUIRED_TOOLS,
        )
        result = None
        runtime_error = None
        try:
            result = await runtime.run(
                prompt,
                session_id="real_model_non_ppo",
                user=UserContext(user_id="research_model_acceptance"),
            )
        except Exception as exc:  # Persist an auditable failed run without leaking endpoint details.
            runtime_error = {"type": type(exc).__name__, "message": "real model execution failed"}

        paper = repository.get(project.project_id, "papers", paper.paper_version_id)
        evidence = repository.list_records(project.project_id, "evidence", limit=50)
        claims = repository.list_records(project.project_id, "claims", limit=50)
        tool_names = [call.name for call in result.tool_calls] if result is not None else []
        answer = result.answer if result is not None else ""
        score = score_acceptance(tool_names, paper, evidence, claims, answer)
        if runtime_error is not None:
            score["failures"].append("runtime_error")
            score["passed"] = False

        answer_path = output / "answer.md"
        answer_path.write_text(answer, encoding="utf-8")
        report_path = output / "report.md"
        report_path.write_text(service.export_report(project.project_id), encoding="utf-8")
        trace_id = result.trace_id if result is not None else _last_trace_id(Path(settings.trace_file))
        spans = recorder.load_trace(trace_id) if trace_id else []
        usage = _usage(spans)
        manifest = {
            "task_id": TASK_ID,
            "provider": settings.llm_provider_resolved,
            "model": settings.llm_model,
            "public_source": f"https://arxiv.org/abs/{arxiv_id}",
            "arxiv_id": arxiv_id,
            "project_id": project.project_id,
            "paper_version_id": paper.paper_version_id,
            "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.sha256,
            "artifact_bytes": artifact.size_bytes,
            "pdf_pages": pdf_pages,
            "tool_names": tool_names,
            "steps": result.steps if result is not None else 0,
            "trace_id": trace_id,
            **usage,
            "score": score,
            "passed": score["passed"],
            "training_executed": False,
            "verification_status": "unverified",
            "answer_sha256": _sha256(answer_path),
            "report_sha256": _sha256(report_path),
            "runtime_error": runtime_error,
        }
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        return manifest
    finally:
        if runtime is not None:
            close_llm = getattr(runtime.llm, "aclose", None)
            if close_llm is not None:
                await close_llm()
            if runtime.session_repo is not None:
                runtime.session_repo.close()
            if runtime.checkpoint_repo is not None:
                runtime.checkpoint_repo.close()
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("evals/runs/research-model-dqn-v1"),
        help="New output directory; previous runs are never overwritten",
    )
    parser.add_argument("--arxiv-id", default=ARXIV_ID)
    args = parser.parse_args()
    try:
        result = asyncio.run(run_acceptance(args.output, args.arxiv_id))
    except FileExistsError:
        parser.error("Output already exists; choose a new --output directory.")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    if not result["passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
