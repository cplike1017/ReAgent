"""Contracts for bounded arXiv PDF snapshots and page-level provenance."""
from contextlib import nullcontext

import httpx
import pymupdf as fitz
import pytest

from tests.test_research_foundation import (
    create_project, import_artifact, research_client, research_settings,
)
from tests.test_research_literature import arxiv_http, atom_entry


def pdf_bytes(text="Full text page one. Reproducible evaluation uses independent seeds."):
    document = fitz.open()
    page = document.new_page()
    page.insert_text((72, 72), text)
    content = document.tobytes()
    document.close()
    return content


@pytest.fixture
def arxiv_pdf_http(monkeypatch):
    calls = []
    queued = []

    def queue(content, *, status=200, content_type="application/pdf",
              content_length=None, final_url=None):
        queued.append({
            "content": content, "status": status, "content_type": content_type,
            "content_length": content_length, "final_url": final_url,
        })
        return calls

    def stream(method, url, **kwargs):
        calls.append({"method": method, "url": url, **kwargs})
        item = queued.pop(0)
        headers = {"content-type": item["content_type"]}
        if item["content_length"] is not None:
            headers["content-length"] = str(item["content_length"])
        request = httpx.Request(method, item["final_url"] or url, headers=kwargs.get("headers"))
        response = httpx.Response(
            item["status"], content=item["content"], headers=headers, request=request,
        )
        return nullcontext(response)

    monkeypatch.setattr(httpx, "stream", stream)
    return queue


def test_abstract_can_be_promoted_to_versioned_full_text_and_remains_idempotent(
    research_client, arxiv_http, arxiv_pdf_http,
):
    project_id = create_project(research_client, "Full-text provenance")
    metadata_calls = arxiv_http(atom_entry())
    pdf_calls = arxiv_pdf_http(pdf_bytes())
    abstract = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv",
        json={"arxiv_id": "2401.00001v2"},
    ).json()

    url = f"/api/research/projects/{project_id}/papers/import/arxiv/full-text"
    first = research_client.post(url, json={"arxiv_id": "2401.00001v2"})
    assert first.status_code == 200, first.text
    result = first.json()
    paper, artifact = result["paper"], result["artifact"]
    assert paper["content_scope"] == "full_text"
    assert paper["artifact_id"] == artifact["artifact_id"]
    assert paper["abstract_artifact_id"] == abstract["artifact"]["artifact_id"]
    assert paper["full_text_artifact_id"] == artifact["artifact_id"]
    assert artifact["media_type"] == "application/pdf"
    assert artifact["name"] == "arxiv-2401.00001v2.pdf"

    second = research_client.post(url, json={"arxiv_id": "2401.00001v2"})
    assert second.status_code == 200 and second.json() == result
    assert len(metadata_calls) == 1 and len(pdf_calls) == 1
    artifacts = research_client.get(
        f"/api/research/projects/{project_id}/artifacts"
    ).json()["items"]
    assert {item["artifact_id"] for item in artifacts} == {
        abstract["artifact"]["artifact_id"], artifact["artifact_id"],
    }

    page = research_client.get(
        f"/api/research/projects/{project_id}/papers/{paper['paper_version_id']}/text",
        params={"page": 1},
    )
    assert page.status_code == 200
    assert page.json()["page_kind"] == "pdf_page"
    assert page.json()["content_scope"] == "full_text"
    assert "independent seeds" in page.json()["text"]
    assert page.json()["artifact_sha256"] == artifact["sha256"]


def test_full_text_import_can_create_the_paper_without_an_abstract_snapshot(
    research_client, arxiv_http, arxiv_pdf_http,
):
    project_id = create_project(research_client)
    arxiv_http(atom_entry())
    arxiv_pdf_http(pdf_bytes())
    response = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv/full-text",
        json={"arxiv_id": "2401.00001v2"},
    )
    assert response.status_code == 200, response.text
    paper = response.json()["paper"]
    assert paper["content_scope"] == "full_text"
    assert paper["abstract_artifact_id"] is None
    assert paper["full_text_artifact_id"] == paper["artifact_id"]


@pytest.mark.parametrize(
    "failure", ["html", "oversized", "oversized_stream", "cross_origin", "broken_pdf"],
)
def test_invalid_pdf_download_does_not_promote_or_create_artifacts(
    research_client, research_settings, arxiv_http, arxiv_pdf_http, failure,
):
    project_id = create_project(research_client)
    arxiv_http(atom_entry())
    abstract = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv",
        json={"arxiv_id": "2401.00001v2"},
    ).json()
    options = {
        "html": dict(content=b"<html>not a PDF</html>", content_type="text/html"),
        "oversized": dict(content=pdf_bytes(), content_length=research_settings.research_max_artifact_bytes + 1),
        "oversized_stream": dict(
            content=b"%PDF-" + b"x" * research_settings.research_max_artifact_bytes,
        ),
        "cross_origin": dict(content=pdf_bytes(), final_url="https://example.invalid/paper.pdf"),
        "broken_pdf": dict(content=b"%PDF-broken", content_type="application/pdf"),
    }[failure]
    arxiv_pdf_http(**options)
    response = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv/full-text",
        json={"arxiv_id": "2401.00001v2"},
    )
    assert response.status_code in {413, 422, 502}
    papers = research_client.get(f"/api/research/projects/{project_id}/papers").json()["items"]
    assert len(papers) == 1
    assert papers[0]["content_scope"] == "abstract"
    assert papers[0]["artifact_id"] == abstract["artifact"]["artifact_id"]
    artifacts = research_client.get(f"/api/research/projects/{project_id}/artifacts").json()["items"]
    assert [item["artifact_id"] for item in artifacts] == [abstract["artifact"]["artifact_id"]]


def test_full_text_import_never_replaces_a_notes_binding(
    research_client, arxiv_http, arxiv_pdf_http,
):
    project_id = create_project(research_client)
    artifact = import_artifact(research_client, project_id)
    payload = {
        "source": "arxiv", "source_id": "2401.00001", "version": "v2",
        "title": "Synthetic MARL survey", "authors": ["Alice Example", "Bob Example"],
        "source_url": "https://arxiv.org/abs/2401.00001v2",
        "published_at": "2024-01-01T00:00:00Z", "updated_at": "2024-02-02T00:00:00Z",
        "categories": ["cs.MA", "cs.LG"], "primary_category": "cs.MA",
        "artifact_id": artifact["artifact_id"], "content_scope": "notes",
    }
    created = research_client.post(
        f"/api/research/projects/{project_id}/papers/import", json=payload,
    )
    assert created.status_code == 200, created.text
    arxiv_http(atom_entry())
    pdf_calls = arxiv_pdf_http(pdf_bytes())
    response = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv/full-text",
        json={"arxiv_id": "2401.00001v2"},
    )
    assert response.status_code == 409
    assert pdf_calls == []
    paper = research_client.get(
        f"/api/research/projects/{project_id}/papers/{created.json()['paper_version_id']}"
    ).json()
    assert paper["content_scope"] == "notes" and paper["artifact_id"] == artifact["artifact_id"]


async def test_agent_can_import_full_text_then_read_the_pdf_page(
    research_client, arxiv_http, arxiv_pdf_http,
):
    project_id = create_project(research_client, "Agent full text")
    arxiv_http(atom_entry())
    arxiv_pdf_http(pdf_bytes("Agent-readable full text page."))
    gateway = research_client.app.state.runtime.tool_gateway
    imported = await gateway.execute("research_import_arxiv_full_text", {
        "project_id": project_id, "arxiv_id": "2401.00001v2",
    })
    assert imported.success
    paper = imported.data["paper"]
    page = await gateway.execute("research_read_page", {
        "project_id": project_id, "paper_version_id": paper["paper_version_id"], "page": 1,
    })
    assert page.success and "Agent-readable full text page" in page.data["text"]
