"""Acceptance contract for the fixed synthetic 10-paper / 30-claim evidence set."""
import hashlib
import json

import pytest

from app.research.repository import SQLiteResearchRepository
from evals.research_evidence_acceptance import DATASET, load_dataset, run_acceptance


def test_dataset_is_fixed_reviewable_and_has_exactly_ten_by_three_records():
    records = load_dataset(DATASET)
    assert len(records) == 10
    assert len({item["source_id"] for item in records}) == 10
    assert all(len(item["statements"]) == 3 for item in records)
    assert sum(len(item["statements"]) for item in records) == 30
    assert all(item["source_id"].startswith("synthetic-evidence-") for item in records)


def test_acceptance_run_reopens_every_locator_and_exports_reviewable_outputs(tmp_path):
    output = tmp_path / "research-evidence-v1"
    manifest = run_acceptance(output)
    assert manifest["dataset_id"] == "research-evidence-v1"
    assert manifest["synthetic_fixture"] is True
    assert manifest["papers"] == 10
    assert manifest["artifacts"] == 10
    assert manifest["evidence"] == 30
    assert manifest["claims"] == 30
    assert manifest["duplicate_artifacts_reused"] is True
    assert manifest["duplicate_versions_reused"] is True
    assert manifest["all_locators_reopened"] is True
    assert manifest["all_artifact_hashes_match"] is True
    assert manifest["all_claims_unverified"] is True
    assert manifest["model_calls"] == 0 and manifest["training_executed"] is False

    saved = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    assert saved == manifest
    report = output / "report.md"
    assert manifest["report_sha256"] == hashlib.sha256(report.read_bytes()).hexdigest()
    assert "训练尚未执行" in report.read_text(encoding="utf-8")
    assert len(list((output / "sources").glob("paper-*.txt"))) == 10
    assert len(list((output / "artifacts").glob("*/*"))) == 10

    repository = SQLiteResearchRepository(f"sqlite:///{(output / 'research.db').as_posix()}")
    try:
        project_id = manifest["project_id"]
        papers = repository.list_records(project_id, "papers", limit=50)
        evidence = repository.list_records(project_id, "evidence", limit=50)
        claims = repository.list_records(project_id, "claims", limit=50)
        assert len(papers) == 10 and all(paper.read_pages == [1] for paper in papers)
        assert len(evidence) == 30 and all(span.locator_verified for span in evidence)
        assert len(claims) == 30 and all(
            claim.verification_status == "unverified" and len(claim.evidence_links) == 1
            for claim in claims
        )
    finally:
        repository.close()


def test_acceptance_run_never_overwrites_a_previous_run(tmp_path):
    output = tmp_path / "existing"
    output.mkdir()
    marker = output / "keep.txt"
    marker.write_text("preserve", encoding="utf-8")
    with pytest.raises(FileExistsError):
        run_acceptance(output)
    assert marker.read_text(encoding="utf-8") == "preserve"
