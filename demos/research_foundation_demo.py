"""Offline PPO research foundation demo; never calls a model or starts training."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from app.research.artifacts import ArtifactStore
from app.research.models import ClaimCreate, EvidenceCreate, EvidenceLink, PaperImport, ProjectCreate
from app.research.repository import SQLiteResearchRepository
from app.research.service import ResearchService

FIXTURES = Path(__file__).parent / "fixtures" / "research"


def run_demo(output: Path) -> dict:
    output = output.resolve()
    # Keep every previous run reviewable, including its database and source files.
    output.mkdir(parents=True, exist_ok=False)
    sources = output / "sources"
    sources.mkdir()
    for name in ("README.md", "papers.json", "ppo_abstract_excerpt.txt", "synthetic_metrics.csv"):
        shutil.copyfile(FIXTURES / name, sources / name)
    repository = SQLiteResearchRepository(f"sqlite:///{(output / 'research.db').as_posix()}")
    try:
        service = ResearchService(repository, ArtifactStore(str(output / "artifacts"), str(sources), 20 * 1024 * 1024))
        project = repository.create_project(ProjectCreate(
            title="PPO 单智能体复现：资料与证据基础样例",
            question="开展 PPO 复现前，哪些方法细节、评估协议和统计单位仍需核对？",
            scope="公开元数据、摘要短摘录与合成指标；训练尚未执行。",
        ))
        excerpt = service.import_artifact(project.project_id, "ppo_abstract_excerpt.txt")
        metrics = service.import_artifact(project.project_id, "synthetic_metrics.csv")
        paper_inputs = json.loads((sources / "papers.json").read_text(encoding="utf-8"))
        papers = []
        for data in paper_inputs:
            if data["source_id"] == "1707.06347":
                data.update(artifact_id=excerpt.artifact_id, content_scope="abstract")
            papers.append(repository.import_paper(project.project_id, PaperImport.model_validate(data)))
        span = service.create_evidence(project.project_id, EvidenceCreate(
            paper_version_id=papers[0].paper_version_id,
            quote=(sources / "ppo_abstract_excerpt.txt").read_text(encoding="utf-8").strip(),
        ))
        repository.create_claim(project.project_id, ClaimCreate(
            text="PPO 摘要提到对小批量数据进行多轮更新；尚需阅读全文核对实现与超参数。",
            kind="fact", evidence_links=[EvidenceLink(evidence_id=span.evidence_id)],
        ))
        repository.create_claim(project.project_id, ClaimCreate(
            text="待检验：固定评估协议后，不同独立训练 seed 的 PPO 结果仍可能存在差异。",
            kind="hypothesis",
        ))
        repository.create_claim(project.project_id, ClaimCreate(
            text="synthetic_metrics.csv 是合成格式样例，尚未计算统计量，不能用于判断 PPO 性能。",
            kind="fact",
        ))
        report = service.export_report(project.project_id)
        (output / "report.md").write_text(report, encoding="utf-8")
        manifest = {
            "project_id": project.project_id, "papers": len(papers), "evidence": 1,
            "claims": 3, "training_executed": False, "metrics_origin": "synthetic",
            "synthetic_metrics_artifact_id": metrics.artifact_id,
            "report_sha256": hashlib.sha256((output / "report.md").read_bytes()).hexdigest(),
            "sources": {
                path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                for path in sorted(sources.iterdir())
            },
        }
        (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return manifest
    finally:
        repository.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("data/research-demo"),
                        help="New output directory; existing runs are never overwritten")
    args = parser.parse_args()
    try:
        result = run_demo(args.output)
    except FileExistsError:
        parser.error("Output already exists; choose a new --output directory.")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print(f"Report: {(args.output / 'report.md').resolve()}")


if __name__ == "__main__":
    main()
