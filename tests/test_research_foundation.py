"""Local research API contracts; no network, Redis server or model calls."""
import sqlite3
import hashlib
import csv
import io
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import fakeredis.aioredis
import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture
def research_settings(settings, tmp_path):
    sandbox = tmp_path / "sandbox"
    sandbox.mkdir()
    (sandbox / "paper.txt").write_text(
        "Synthetic fixture, not experimental evidence.\n"
        "The baseline uses a fixed evaluation protocol.\n",
        encoding="utf-8",
    )
    return settings.model_copy(update={
        "research_enabled": True,
        "research_artifact_dir": str(tmp_path / "artifacts"),
        "research_max_artifact_bytes": 1024 * 1024,
        "sandbox_dir": str(sandbox),
        "runtime_config_file": str(tmp_path / "overrides.json"),
    })


@pytest.fixture
def research_client(research_settings):
    app = create_app(research_settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    with TestClient(app) as client:
        yield client


def test_project_creation_survives_app_restart(research_settings):
    app = create_app(research_settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    with TestClient(app) as client:
        response = client.post("/api/research/projects", json={
            "title": "PPO replication", "question": "Are evaluation protocols comparable?",
        })
        assert response.status_code == 201, response.text
        project = response.json()
        assert project["project_id"]
    app = create_app(research_settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    with TestClient(app) as client:
        assert client.get(f"/api/research/projects/{project['project_id']}").json() == project
        assert client.get("/api/research/projects").json()["items"] == [project]


def test_research_is_disabled_by_default(settings):
    settings = settings.model_copy(update={"research_enabled": False})
    app = create_app(settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    with TestClient(app) as client:
        response = client.get("/api/research/projects")
        assert response.status_code == 503
        assert response.json()["detail"]["code"] == "research_disabled"
        assert client.get("/health").status_code == 200
    with sqlite3.connect(settings.database_url.removeprefix("sqlite:///")) as conn:
        assert conn.execute(
            "SELECT name FROM sqlite_master WHERE name LIKE 'research_%'"
        ).fetchall() == []


def create_project(client, title="PPO"):
    response = client.post("/api/research/projects", json={
        "title": title, "question": "Compare evaluation protocols",
    })
    assert response.status_code == 201, response.text
    return response.json()["project_id"]


def import_artifact(client, project_id, path="paper.txt"):
    response = client.post(f"/api/research/projects/{project_id}/artifacts/import", json={"path": path})
    assert response.status_code == 200, response.text
    return response.json()


def import_paper(client, project_id, artifact_id=None, **updates):
    payload = {
        "source": "local", "source_id": "synthetic-paper", "version": "v1",
        "title": "Synthetic paper", "authors": ["Fixture author"],
    }
    if artifact_id:
        payload.update(artifact_id=artifact_id, content_scope="notes")
    payload.update(updates)
    response = client.post(f"/api/research/projects/{project_id}/papers/import", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def evidence(client, project_id, paper_id, **updates):
    payload = {
        "paper_version_id": paper_id, "page": 1,
        "quote": "The baseline uses a fixed evaluation protocol.",
    }
    payload.update(updates)
    return client.post(f"/api/research/projects/{project_id}/evidence", json=payload)


def test_artifacts_are_snapshots_and_deduplicated(research_client, research_settings):
    project = create_project(research_client)
    artifact = import_artifact(research_client, project)
    original = (Path(research_settings.sandbox_dir) / "paper.txt").read_bytes()
    assert artifact["sha256"] == hashlib.sha256(original).hexdigest()
    assert artifact["size_bytes"] == len(original)
    assert import_artifact(research_client, project)["artifact_id"] == artifact["artifact_id"]
    (Path(research_settings.sandbox_dir) / "paper.txt").write_text("changed", encoding="utf-8")
    url = f"/api/research/projects/{project}/artifacts/{artifact['artifact_id']}/content"
    assert research_client.get(url).content == original
    changed = import_artifact(research_client, project)
    assert changed["artifact_id"] != artifact["artifact_id"]


@pytest.mark.parametrize("path", ["../outside.txt", "/outside.txt", "C:\\outside.txt", "missing.txt"])
def test_artifact_import_rejects_invalid_paths(research_client, path):
    project = create_project(research_client)
    response = research_client.post(
        f"/api/research/projects/{project}/artifacts/import", json={"path": path},
    )
    assert response.status_code in {404, 422}
    assert research_client.get(f"/api/research/projects/{project}/artifacts").json()["items"] == []


def test_artifact_size_limit_leaves_no_record(research_settings):
    research_settings.research_max_artifact_bytes = 5
    with TestClient(create_app(research_settings)) as client:
        project = create_project(client)
        response = client.post(f"/api/research/projects/{project}/artifacts/import", json={"path": "paper.txt"})
        assert response.status_code == 413
        assert client.get(f"/api/research/projects/{project}/artifacts").json()["items"] == []
        assert not list(Path(research_settings.research_artifact_dir).rglob("*.part"))


def test_corrupted_snapshot_is_not_returned_as_valid(research_client, research_settings):
    project = create_project(research_client)
    artifact = import_artifact(research_client, project)
    blob = Path(research_settings.research_artifact_dir) / project / artifact["sha256"]
    blob.write_bytes(b"corrupted")
    response = research_client.get(
        f"/api/research/projects/{project}/artifacts/{artifact['artifact_id']}/content"
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "artifact_integrity"


def test_paper_versions_keep_identity_and_reject_changed_content(research_client):
    project = create_project(research_client)
    first = import_paper(research_client, project)
    assert import_paper(research_client, project) == first
    second = import_paper(research_client, project, version="v2")
    assert first["paper_id"] == second["paper_id"]
    assert first["paper_version_id"] != second["paper_version_id"]
    response = research_client.post(f"/api/research/projects/{project}/papers/import", json={
        "source": "local", "source_id": "synthetic-paper", "version": "v1", "title": "Changed",
    })
    assert response.status_code == 409
    assert research_client.get(
        f"/api/research/projects/{project}/papers/{first['paper_version_id']}"
    ).json() == first


def test_metadata_can_bind_source_once_without_claiming_full_read(research_client):
    project = create_project(research_client)
    first = import_paper(research_client, project)
    assert first["read_scope"] == "metadata"
    artifact = import_artifact(research_client, project)
    attached = import_paper(research_client, project, artifact["artifact_id"])
    assert attached["paper_version_id"] == first["paper_version_id"]
    assert attached["read_scope"] == "metadata"
    assert attached["content_scope"] == "notes"
    page = research_client.get(
        f"/api/research/projects/{project}/papers/{first['paper_version_id']}/text",
        params={"page": 1, "limit": 12},
    ).json()
    assert len(page["text"]) == 12
    assert page["truncated"] is True
    assert page["page_kind"] == "text_document"
    reread = research_client.get(
        f"/api/research/projects/{project}/papers/{first['paper_version_id']}"
    ).json()
    assert reread["read_scope"] == "selected_pages"
    assert reread["read_pages"] == [1]


def test_evidence_validates_source_and_claim_stays_unverified(research_client):
    project = create_project(research_client)
    artifact = import_artifact(research_client, project)
    paper = import_paper(research_client, project, artifact["artifact_id"])
    response = evidence(research_client, project, paper["paper_version_id"])
    assert response.status_code == 201, response.text
    span = response.json()
    assert span["locator_verified"] is True
    assert span["artifact_sha256"] == artifact["sha256"]
    assert evidence(research_client, project, paper["paper_version_id"]).json()["evidence_id"] == span["evidence_id"]
    claim = research_client.post(f"/api/research/projects/{project}/claims", json={
        "text": "The fixture describes a fixed protocol.", "kind": "fact",
        "evidence_links": [{"evidence_id": span["evidence_id"], "relation": "supports"}],
    })
    assert claim.status_code == 201, claim.text
    assert claim.json()["verification_status"] == "unverified"
    report = research_client.get(f"/api/research/projects/{project}/report")
    assert report.status_code == 200
    assert "text/markdown" in report.headers["content-type"]
    assert span["evidence_id"] in report.text
    assert artifact["sha256"] in report.text
    assert "unverified" in report.text
    assert "训练尚未执行" in report.text


def test_literature_exports_are_deterministic_and_project_scoped(research_client):
    project = create_project(research_client, "Comparison")
    other = create_project(research_client, "Other")
    artifact = import_artifact(research_client, project)
    paper = import_paper(
        research_client, project, artifact["artifact_id"],
        source="arxiv", source_id="1707.06347", version="v2",
        title="Proximal Policy Optimization Algorithms",
        authors=["John Schulman", "Filip Wolski"],
        source_url="https://arxiv.org/abs/1707.06347v2",
        published_at="2017-07-20T00:00:00Z",
        categories=["cs.LG", "cs.AI"], primary_category="cs.LG",
    )
    span = evidence(research_client, project, paper["paper_version_id"]).json()
    claim = research_client.post(f"/api/research/projects/{project}/claims", json={
        "text": "The fixture describes a fixed evaluation protocol.",
        "kind": "fact",
        "evidence_links": [{"evidence_id": span["evidence_id"], "relation": "supports"}],
    }).json()
    import_paper(research_client, other, title="Must stay outside the export")

    comparison_url = f"/api/research/projects/{project}/exports/comparison.csv"
    first = research_client.get(comparison_url)
    second = research_client.get(comparison_url)
    assert first.status_code == 200 and first.content == second.content
    assert "text/csv" in first.headers["content-type"]
    assert 'filename="comparison.csv"' in first.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(first.text)))
    assert len(rows) == 1
    assert rows[0]["paper_version_id"] == paper["paper_version_id"]
    assert rows[0]["read_pages"] == "1"
    assert rows[0]["evidence_count"] == "1"
    assert rows[0]["linked_claim_count"] == "1"
    assert claim["text"] in rows[0]["linked_claims"]
    assert "Must stay outside" not in first.text

    bibtex_url = f"/api/research/projects/{project}/exports/references.bib"
    bibtex = research_client.get(bibtex_url)
    assert bibtex.status_code == 200
    assert bibtex.content == research_client.get(bibtex_url).content
    assert "application/x-bibtex" in bibtex.headers["content-type"]
    assert 'filename="references.bib"' in bibtex.headers["content-disposition"]
    assert "@misc{arxiv_1707_06347_v2," in bibtex.text
    assert "title = {Proximal Policy Optimization Algorithms}" in bibtex.text
    assert "author = {John Schulman and Filip Wolski}" in bibtex.text
    assert "year = {2017}" in bibtex.text
    assert "eprint = {1707.06347}" in bibtex.text
    assert "primaryClass = {cs.LG}" in bibtex.text
    assert "Must stay outside" not in bibtex.text


@pytest.mark.parametrize("updates", [
    {"quote": "This sentence is not present."},
    {"page": 2}, {"quote": ""}, {"page": 0},
])
def test_invalid_evidence_is_not_recorded(research_client, updates):
    project = create_project(research_client)
    artifact = import_artifact(research_client, project)
    paper = import_paper(research_client, project, artifact["artifact_id"])
    response = evidence(research_client, project, paper["paper_version_id"], **updates)
    assert response.status_code == 422
    assert research_client.get(f"/api/research/projects/{project}/evidence").json()["items"] == []


def test_metadata_only_paper_cannot_supply_evidence(research_client):
    project = create_project(research_client)
    paper = import_paper(research_client, project)
    assert evidence(research_client, project, paper["paper_version_id"]).status_code == 422


def test_cross_project_references_and_downloads_are_rejected(research_client):
    a, b = create_project(research_client, "A"), create_project(research_client, "B")
    artifact = import_artifact(research_client, a)
    paper = import_paper(research_client, a, artifact["artifact_id"])
    span = evidence(research_client, a, paper["paper_version_id"]).json()
    assert research_client.get(f"/api/research/projects/{b}/papers").json()["items"] == []
    for url in [
        f"/api/research/projects/{b}/artifacts/{artifact['artifact_id']}/content",
        f"/api/research/projects/{b}/papers/{paper['paper_version_id']}",
        f"/api/research/projects/{b}/evidence/{span['evidence_id']}",
    ]:
        assert research_client.get(url).status_code == 404
    assert evidence(research_client, b, paper["paper_version_id"]).status_code == 404
    response = research_client.post(f"/api/research/projects/{b}/claims", json={
        "text": "Invalid reference", "evidence_links": [{"evidence_id": span["evidence_id"]}],
    })
    assert response.status_code == 404
    assert research_client.get(f"/api/research/projects/{b}/claims").json()["items"] == []


def test_claim_allows_missing_evidence_but_not_forged_verification(research_client):
    project = create_project(research_client)
    url = f"/api/research/projects/{project}/claims"
    draft = research_client.post(url, json={"text": "Unconfirmed hypothesis", "kind": "hypothesis"})
    assert draft.status_code == 201
    assert draft.json()["evidence_links"] == []
    assert draft.json()["verification_status"] == "unverified"
    assert research_client.post(url, json={
        "text": "Forged", "verification_status": "verified",
    }).status_code == 422


@pytest.mark.parametrize("payload", [
    {"title": " ", "question": "q"}, {"title": "t", "question": ""},
    {"title": "t", "question": "q", "owner": "someone-else"},
])
def test_invalid_project_payloads(research_client, payload):
    assert research_client.post("/api/research/projects", json=payload).status_code == 422


def test_pagination_and_missing_project(research_client):
    for i in range(3):
        create_project(research_client, str(i))
    first = research_client.get("/api/research/projects?limit=2&offset=0").json()["items"]
    second = research_client.get("/api/research/projects?limit=2&offset=2").json()["items"]
    assert len(first) == 2 and len(second) == 1
    assert {x["project_id"] for x in first}.isdisjoint(x["project_id"] for x in second)
    assert research_client.get("/api/research/projects?limit=1001").status_code == 422
    assert research_client.get("/api/research/projects/missing/papers").status_code == 404


def test_simultaneous_paper_imports_create_one_version(research_client):
    project = create_project(research_client)
    with ThreadPoolExecutor(max_workers=4) as pool:
        papers = list(pool.map(lambda _: import_paper(research_client, project), range(8)))
    assert len({paper["paper_version_id"] for paper in papers}) == 1
    assert len(research_client.get(f"/api/research/projects/{project}/papers").json()["items"]) == 1


def test_pdf_evidence_keeps_physical_page(research_client, research_settings):
    import fitz
    path = Path(research_settings.sandbox_dir) / "pages.pdf"
    with fitz.open() as doc:
        doc.new_page().insert_text((72, 72), "Synthetic page one.")
        doc.new_page().insert_text((72, 72), "The baseline uses a fixed evaluation protocol.")
        doc.save(path)
    project = create_project(research_client)
    artifact = import_artifact(research_client, project, "pages.pdf")
    paper = import_paper(research_client, project, artifact["artifact_id"], content_scope="full_text")
    assert evidence(research_client, project, paper["paper_version_id"]).status_code == 422
    response = evidence(research_client, project, paper["paper_version_id"], page=2)
    assert response.status_code == 201
    assert response.json()["page_kind"] == "pdf_page"
    assert response.json()["page"] == 2


def test_migrations_preserve_legacy_data_and_reapply_safely(research_settings):
    path = research_settings.database_url.removeprefix("sqlite:///")
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE legacy_record (value TEXT)")
        conn.execute("INSERT INTO legacy_record VALUES ('keep me')")
    for _ in range(2):
        with TestClient(create_app(research_settings)) as client:
            assert client.get("/api/research/projects").status_code == 200
    with sqlite3.connect(path) as conn:
        assert conn.execute("SELECT * FROM legacy_record").fetchall() == [("keep me",)]
        assert conn.execute("SELECT version FROM research_schema_migrations").fetchall() == [(1,), (2,)]


def test_failed_migration_rolls_back_schema_and_version(monkeypatch):
    from app.research import migrations

    with sqlite3.connect(":memory:") as conn:
        migrations.apply_migrations(conn)
        monkeypatch.setattr(migrations, "MIGRATIONS", migrations.MIGRATIONS + [(3, (
            "CREATE TABLE research_partial (value TEXT)", "INVALID SQL",
        ))])
        with pytest.raises(sqlite3.OperationalError):
            migrations.apply_migrations(conn)
        assert conn.execute("SELECT version FROM research_schema_migrations").fetchall() == [(1,), (2,)]
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='research_partial'").fetchall()


def test_unknown_migration_version_refuses_modification():
    from app.research.migrations import apply_migrations

    with sqlite3.connect(":memory:") as conn:
        apply_migrations(conn)
        conn.execute("INSERT INTO research_schema_migrations VALUES (99)")
        conn.commit()
        before = conn.execute("SELECT name, sql FROM sqlite_master").fetchall()
        with pytest.raises(ValueError, match="迁移版本"):
            apply_migrations(conn)
        assert conn.execute("SELECT name, sql FROM sqlite_master").fetchall() == before


def test_separate_connections_deduplicate_imports_and_enforce_project_foreign_keys(research_settings):
    from app.research.models import PaperImport, ProjectCreate
    from app.research.repository import SQLiteResearchRepository

    first = SQLiteResearchRepository(research_settings.database_url)
    second = SQLiteResearchRepository(research_settings.database_url)
    try:
        a = first.create_project(ProjectCreate(title="A", question="Question A"))
        b = first.create_project(ProjectCreate(title="B", question="Question B"))
        request = PaperImport(source="local", source_id="same", version="v1", title="Paper")
        with ThreadPoolExecutor(max_workers=4) as pool:
            versions = list(pool.map(
                lambda i: (first if i % 2 else second).import_paper(a.project_id, request), range(16),
            ))
        assert len({v.paper_version_id for v in versions}) == 1
        with pytest.raises(sqlite3.IntegrityError):
            with second._write():
                second._conn.execute("INSERT INTO research_paper_reads VALUES (?, ?, ?)",
                                     (b.project_id, versions[0].paper_version_id, 1))
        assert first.get(a.project_id, "papers", versions[0].paper_version_id).read_pages == []
    finally:
        first.close()
        second.close()


def test_bound_paper_cannot_replace_artifact_or_scope(research_client, research_settings):
    project = create_project(research_client)
    artifact = import_artifact(research_client, project)
    paper = import_paper(research_client, project, artifact["artifact_id"])
    (Path(research_settings.sandbox_dir) / "paper.txt").write_text("Different source", encoding="utf-8")
    changed = import_artifact(research_client, project)
    payload = {key: paper[key] for key in (
        "source", "source_id", "version", "title", "authors", "artifact_id", "content_scope",
    )}
    for update in ({"artifact_id": changed["artifact_id"]}, {"content_scope": "full_text"}):
        assert research_client.post(f"/api/research/projects/{project}/papers/import",
                                    json=payload | update).status_code == 409
    other = create_project(research_client)
    assert research_client.post(f"/api/research/projects/{other}/papers/import",
                                json=payload).status_code == 404
    assert research_client.get(f"/api/research/projects/{other}/papers").json()["items"] == []


def test_arxiv_version_is_canonical_and_cannot_point_at_a_different_source(research_client):
    project = create_project(research_client)
    paper = import_paper(research_client, project, source="arxiv", source_id="1707.06347", version="2")
    assert paper["version"] == "v2"
    assert paper["source_url"] == "https://arxiv.org/abs/1707.06347v2"
    payload = {"source": "arxiv", "source_id": "1707.06347", "version": "v2", "title": "PPO"}
    for update in (
        {"source_id": "1707.06347v2"}, {"version": "latest"},
        {"source_url": "https://arxiv.org/abs/1707.06347v1"},
        {"content_scope": "full_text"},
    ):
        assert research_client.post(f"/api/research/projects/{project}/papers/import",
                                    json=payload | update).status_code == 422


@pytest.mark.parametrize("name, content", [("broken.pdf", b"not a pdf"), ("bad.txt", b"\xff\xfe"), ("blank.txt", b" ")])
def test_unreadable_sources_do_not_create_evidence(research_client, research_settings, name, content):
    (Path(research_settings.sandbox_dir) / name).write_bytes(content)
    project = create_project(research_client)
    artifact = import_artifact(research_client, project, name)
    paper = import_paper(research_client, project, artifact["artifact_id"])
    assert evidence(research_client, project, paper["paper_version_id"]).status_code == 422
    assert research_client.get(f"/api/research/projects/{project}/evidence").json()["items"] == []


def test_ppo_demo_exports_auditable_bundle_without_training(tmp_path):
    from demos.research_foundation_demo import run_demo

    output = tmp_path / "demo"
    result = run_demo(output)
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "1707.06347" in report and "训练尚未执行" in report
    assert "unverified" in report and "abstract" in report
    assert result["papers"] == 3 and result["evidence"] == 1
    assert (output / "research.db").is_file()
    assert (output / "manifest.json").is_file()
    # Original sources and stored snapshots must survive the demonstration.
    assert (output / "sources" / "synthetic_metrics.csv").is_file()
    assert len(list((output / "artifacts").glob("*/*"))) == 2
    with pytest.raises(FileExistsError):
        run_demo(output)


def test_report_uses_one_snapshot_during_concurrent_evidence_and_claim_write(
    research_client, research_settings, monkeypatch,
):
    from app.research.models import ClaimCreate, EvidenceCreate, EvidenceLink
    from app.research.repository import SQLiteResearchRepository
    from app.research.service import ResearchService

    project = create_project(research_client)
    artifact = import_artifact(research_client, project)
    paper = import_paper(research_client, project, artifact["artifact_id"])
    service = research_client.app.state.research_service
    writer_repo = SQLiteResearchRepository(research_settings.database_url)
    writer = ResearchService(writer_repo, service.artifacts)
    original = service.repository.list_records
    late = {}

    def interleaved(project_id, kind, limit=50, offset=0):
        rows = original(project_id, kind, limit, offset)
        if kind == "evidence" and not late:
            span = writer.create_evidence(project, EvidenceCreate(
                paper_version_id=paper["paper_version_id"],
                quote="The baseline uses a fixed evaluation protocol.",
            ))
            late["span"] = span
            late["claim"] = writer_repo.create_claim(project, ClaimCreate(
                text="Claim committed during report export",
                evidence_links=[EvidenceLink(evidence_id=span.evidence_id)],
            ))
        return rows

    monkeypatch.setattr(service.repository, "list_records", interleaved)
    try:
        report = research_client.get(f"/api/research/projects/{project}/report").text
        assert late["claim"].claim_id not in report
        next_report = research_client.get(f"/api/research/projects/{project}/report").text
        assert late["claim"].claim_id in next_report and late["span"].evidence_id in next_report
        assert next_report == research_client.get(f"/api/research/projects/{project}/report").text
    finally:
        writer_repo.close()


def test_null_path_is_a_validation_error(research_settings):
    with TestClient(create_app(research_settings), raise_server_exceptions=False) as client:
        project = create_project(client)
        response = client.post(f"/api/research/projects/{project}/artifacts/import",
                               json={"path": "\u0000.txt"})
        assert response.status_code == 422, response.text


def test_pagination_rejects_sqlite_integer_overflow(research_settings):
    with TestClient(create_app(research_settings), raise_server_exceptions=False) as client:
        assert client.get("/api/research/projects", params={"offset": 2**63}).status_code == 422
