"""Run the fixed synthetic 10-paper / 30-claim research evidence gate."""
import argparse
import hashlib
import json
import shutil
from pathlib import Path

from app.research.artifacts import ArtifactStore
from app.research.models import (
    ClaimCreate, EvidenceCreate, EvidenceLink, PaperImport, ProjectCreate,
)
from app.research.repository import SQLiteResearchRepository
from app.research.service import ResearchService, normalized_text

DATASET = Path(__file__).parent / "datasets" / "research_evidence_v1.json"
DATASET_ID = "research-evidence-v1"


def load_dataset(path: Path = DATASET) -> list[dict]:
    records = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(records, list) or len(records) != 10:
        raise ValueError("research evidence dataset must contain exactly 10 papers")
    source_ids = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"source_id", "title", "statements"}:
            raise ValueError("each paper requires source_id, title and statements")
        source_ids.append(record["source_id"])
        statements = record["statements"]
        if not isinstance(statements, list) or len(statements) != 3:
            raise ValueError("each paper must contain exactly 3 statements")
        for statement in statements:
            if not isinstance(statement, dict) or set(statement) != {
                "quote", "claim", "kind", "relation",
            }:
                raise ValueError("each statement requires quote, claim, kind and relation")
    if len(source_ids) != len(set(source_ids)):
        raise ValueError("paper source_id values must be unique")
    return records


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_acceptance(output: Path, dataset_path: Path = DATASET) -> dict:
    """Materialize and verify a reviewable acceptance run without model calls."""
    records = load_dataset(dataset_path)
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=False)
    sources = output / "sources"
    sources.mkdir()
    shutil.copyfile(dataset_path, output / "dataset.json")

    repository = SQLiteResearchRepository(f"sqlite:///{(output / 'research.db').as_posix()}")
    try:
        service = ResearchService(
            repository,
            ArtifactStore(str(output / "artifacts"), str(sources), 20 * 1024 * 1024),
        )
        project = repository.create_project(ProjectCreate(
            title="Fixed synthetic research evidence acceptance set",
            question="Can every one of 30 synthetic claims reopen its exact stored locator?",
            scope="Ten generated text fixtures; engineering provenance only; no model or training.",
        ))
        duplicate_artifacts_reused = True
        duplicate_versions_reused = True
        run_records = []

        for index, record in enumerate(records, 1):
            source_name = f"paper-{index:02}.txt"
            source = sources / source_name
            source.write_text(
                "SYNTHETIC ENGINEERING FIXTURE. NOT A SCIENTIFIC PAPER OR RESULT.\n\n"
                + "\n\n".join(item["quote"] for item in record["statements"])
                + "\n",
                encoding="utf-8",
            )
            artifact = service.import_artifact(project.project_id, source_name)
            repeated_artifact = service.import_artifact(project.project_id, source_name)
            duplicate_artifacts_reused &= repeated_artifact.artifact_id == artifact.artifact_id
            paper_input = PaperImport(
                source="local", source_id=record["source_id"], version="v1",
                title=record["title"], authors=["Synthetic acceptance fixture"],
                artifact_id=artifact.artifact_id, content_scope="notes",
            )
            paper = repository.import_paper(project.project_id, paper_input)
            repeated_paper = repository.import_paper(project.project_id, paper_input)
            duplicate_versions_reused &= repeated_paper.paper_version_id == paper.paper_version_id

            item_records = []
            for statement in record["statements"]:
                span = service.create_evidence(project.project_id, EvidenceCreate(
                    paper_version_id=paper.paper_version_id, page=1,
                    quote=statement["quote"],
                ))
                claim = repository.create_claim(project.project_id, ClaimCreate(
                    text=statement["claim"], kind=statement["kind"],
                    evidence_links=[EvidenceLink(
                        evidence_id=span.evidence_id, relation=statement["relation"],
                    )],
                ))
                item_records.append({
                    "evidence_id": span.evidence_id, "claim_id": claim.claim_id,
                    "quote_sha256": span.quote_sha256,
                })
            run_records.append({
                "source_id": record["source_id"], "source_file": source_name,
                "source_sha256": _sha256(source), "paper_version_id": paper.paper_version_id,
                "artifact_id": artifact.artifact_id, "artifact_sha256": artifact.sha256,
                "links": item_records,
            })

        papers = repository.list_records(project.project_id, "papers", limit=50)
        artifacts = repository.list_records(project.project_id, "artifacts", limit=50)
        evidence = repository.list_records(project.project_id, "evidence", limit=50)
        claims = repository.list_records(project.project_id, "claims", limit=50)
        all_artifact_hashes_match = all(
            service.artifacts.verified_path(artifact).is_file() for artifact in artifacts
        )
        all_locators_reopened = True
        for span in evidence:
            page = service.read_page(project.project_id, span.paper_version_id, span.page)
            all_locators_reopened &= (
                normalized_text(span.quote) in page["text"]
                and page["artifact_sha256"] == span.artifact_sha256
            )
        all_claims_unverified = all(
            claim.verification_status == "unverified" and len(claim.evidence_links) == 1
            for claim in claims
        )

        report_path = output / "report.md"
        report_path.write_text(service.export_report(project.project_id), encoding="utf-8")
        manifest = {
            "dataset_id": DATASET_ID,
            "dataset_sha256": _sha256(output / "dataset.json"),
            "synthetic_fixture": True,
            "project_id": project.project_id,
            "papers": len(papers), "artifacts": len(artifacts),
            "evidence": len(evidence), "claims": len(claims),
            "duplicate_artifacts_reused": duplicate_artifacts_reused,
            "duplicate_versions_reused": duplicate_versions_reused,
            "all_locators_reopened": all_locators_reopened,
            "all_artifact_hashes_match": all_artifact_hashes_match,
            "all_claims_unverified": all_claims_unverified,
            "model_calls": 0, "training_executed": False,
            "report_sha256": _sha256(report_path),
            "records": run_records,
        }
        if not all([
            len(papers) == 10, len(artifacts) == 10, len(evidence) == 30,
            len(claims) == 30, duplicate_artifacts_reused,
            duplicate_versions_reused, all_locators_reopened,
            all_artifact_hashes_match, all_claims_unverified,
        ]):
            raise RuntimeError("research evidence acceptance gate failed")
        (output / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8",
        )
        return manifest
    finally:
        repository.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output", type=Path, default=Path("evals/runs/research-evidence-v1"),
        help="New output directory; previous runs are never overwritten",
    )
    args = parser.parse_args()
    try:
        result = run_acceptance(args.output)
    except FileExistsError:
        parser.error("Output already exists; choose a new --output directory.")
    print(json.dumps(result, ensure_ascii=True, indent=2))
    print(f"Report: {(args.output / 'report.md').resolve()}")


if __name__ == "__main__":
    main()
