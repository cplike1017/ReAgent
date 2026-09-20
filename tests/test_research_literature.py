"""Offline contracts for structured arXiv search and project-scoped abstract import."""
import httpx
import pytest

from tests.test_research_foundation import create_project, research_client, research_settings


def atom_entry(*, arxiv_id="2401.00001v2", title="Synthetic MARL survey",
               published="2024-01-01T00:00:00Z", updated="2024-02-02T00:00:00Z",
               abstract="Structured fixture abstract; not experimental evidence."):
    return f'''<feed xmlns="http://www.w3.org/2005/Atom"
        xmlns:arxiv="http://arxiv.org/schemas/atom">
      <entry>
        <id>http://arxiv.org/abs/{arxiv_id}</id>
        <title>{title}</title>
        <published>{published}</published><updated>{updated}</updated>
        <summary>{abstract}</summary>
        <author><name>Alice Example</name></author>
        <author><name>Bob Example</name></author>
        <category term="cs.MA"/><category term="cs.LG"/>
        <arxiv:primary_category term="cs.MA"/>
        <link title="pdf" href="http://arxiv.org/pdf/{arxiv_id}" rel="related" type="application/pdf"/>
      </entry>
    </feed>'''


@pytest.fixture
def arxiv_http(monkeypatch):
    calls = []
    responses = []

    def queue(*bodies):
        responses.extend(bodies)
        return calls

    def get(url, **kwargs):
        request = httpx.Request("GET", url, params=kwargs.get("params"), headers=kwargs.get("headers"))
        calls.append(request)
        body = responses.pop(0) if responses else atom_entry()
        return httpx.Response(200, text=body, request=request)

    monkeypatch.setattr(httpx, "get", get)
    return queue


def test_structured_search_preserves_version_dates_categories_and_uses_cache(research_client, arxiv_http):
    calls = arxiv_http(atom_entry())
    params = {"query": 'ti:"multi-agent reinforcement learning"', "max_results": 2,
              "sort_by": "submittedDate"}
    first = research_client.get("/api/research/literature/arxiv/search", params=params)
    assert first.status_code == 200, first.text
    assert first.json() == {
        "query": params["query"], "items": [{
            "arxiv_id": "2401.00001v2", "source_id": "2401.00001", "version": "v2",
            "title": "Synthetic MARL survey", "authors": ["Alice Example", "Bob Example"],
            "abstract": "Structured fixture abstract; not experimental evidence.",
            "published_at": "2024-01-01T00:00:00Z", "updated_at": "2024-02-02T00:00:00Z",
            "categories": ["cs.MA", "cs.LG"], "primary_category": "cs.MA",
            "source_url": "https://arxiv.org/abs/2401.00001v2",
            "pdf_url": "https://arxiv.org/pdf/2401.00001v2",
        }], "cached": False,
    }
    second = research_client.get("/api/research/literature/arxiv/search", params=params)
    assert second.status_code == 200
    assert second.json()["items"] == first.json()["items"]
    assert second.json()["cached"] is True
    assert len(calls) == 1
    assert calls[0].url.params["search_query"] == params["query"]
    assert calls[0].url.params["max_results"] == "2"
    assert calls[0].url.params["sortBy"] == "submittedDate"
    assert "start" not in calls[0].url.params
    assert calls[0].headers["user-agent"].startswith("ReAgent/")
    assert calls[0].headers["accept"] == "application/atom+xml"


def test_exact_version_import_snapshots_abstract_and_is_idempotent(research_client, arxiv_http):
    project_id = create_project(research_client, "General literature project")
    calls = arxiv_http(atom_entry())
    url = f"/api/research/projects/{project_id}/papers/import/arxiv"
    first = research_client.post(url, json={"arxiv_id": "2401.00001v2"})
    assert first.status_code == 200, first.text
    body = first.json()
    paper, artifact = body["paper"], body["artifact"]
    assert paper["source_id"] == "2401.00001" and paper["version"] == "v2"
    assert paper["published_at"] == "2024-01-01T00:00:00Z"
    assert paper["updated_at"] == "2024-02-02T00:00:00Z"
    assert paper["categories"] == ["cs.MA", "cs.LG"]
    assert paper["content_scope"] == "abstract" and paper["artifact_id"] == artifact["artifact_id"]
    assert artifact["name"] == "arxiv-2401.00001v2-abstract.txt"
    second = research_client.post(url, json={"arxiv_id": "2401.00001v2"})
    assert second.status_code == 200 and second.json() == body
    assert len(calls) == 1
    assert calls[0].url.params["id_list"] == "2401.00001v2"
    assert "search_query" not in calls[0].url.params
    assert "start" not in calls[0].url.params
    page = research_client.get(
        f"/api/research/projects/{project_id}/papers/{paper['paper_version_id']}/text"
    )
    assert page.status_code == 200
    assert page.json()["text"] == "Structured fixture abstract; not experimental evidence."
    assert page.json()["artifact_sha256"] == artifact["sha256"]


def test_import_rejects_an_upstream_version_mismatch_without_records(research_client, arxiv_http):
    project_id = create_project(research_client)
    arxiv_http(atom_entry(arxiv_id="2401.00001v3"))
    response = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv",
        json={"arxiv_id": "2401.00001v2"},
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "literature_version_mismatch"
    assert research_client.get(f"/api/research/projects/{project_id}/papers").json()["items"] == []
    assert research_client.get(f"/api/research/projects/{project_id}/artifacts").json()["items"] == []


@pytest.mark.parametrize("arxiv_id", ["2401.00001", "2401.00001v0", "2401.00001v2.pdf",
                                       "https://arxiv.org/abs/2401.00001v2", "../2401.00001v2"])
def test_exact_import_requires_a_canonical_versioned_id(research_client, arxiv_id):
    project_id = create_project(research_client)
    response = research_client.post(
        f"/api/research/projects/{project_id}/papers/import/arxiv", json={"arxiv_id": arxiv_id},
    )
    assert response.status_code == 422


async def test_agent_tools_search_and_import_the_same_project_records(research_client, arxiv_http):
    project_id = create_project(research_client, "Non-PPO literature")
    calls = arxiv_http(atom_entry(), atom_entry())
    gateway = research_client.app.state.runtime.tool_gateway
    search = await gateway.execute("research_search_arxiv", {
        "query": "multi-agent learning", "max_results": 1,
    })
    assert search.success and search.data["items"][0]["arxiv_id"] == "2401.00001v2"
    imported = await gateway.execute("research_import_arxiv", {
        "project_id": project_id, "arxiv_id": "2401.00001v2",
    })
    assert imported.success and imported.data["paper"]["content_scope"] == "abstract"
    assert research_client.get(f"/api/research/projects/{project_id}/papers").json()["items"] == [
        imported.data["paper"]
    ]
    assert len(calls) == 2


def test_researcher_profile_can_use_project_literature_tools(research_client):
    from app.orchestrator.executor import SubAgentExecutor
    from app.orchestrator.profiles import get_profile

    runtime = research_client.app.state.runtime
    executor = SubAgentExecutor(
        llm=runtime.llm, master_registry=runtime.registry, settings=runtime.settings,
    )
    names = {tool.name for tool in executor._filtered_registry(get_profile("researcher")).all()}
    assert {"research_search_arxiv", "research_import_arxiv"} <= names


def test_malformed_feed_is_an_upstream_error_not_an_empty_search(research_client, arxiv_http):
    arxiv_http("<not xml")
    response = research_client.get(
        "/api/research/literature/arxiv/search", params={"query": "MARL"},
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "literature_upstream"


def test_invalid_upstream_metadata_is_a_structured_error(research_client, arxiv_http):
    arxiv_http(atom_entry(title="x" * 501))
    response = research_client.get(
        "/api/research/literature/arxiv/search", params={"query": "MARL"},
    )
    assert response.status_code == 502
    assert response.json()["detail"]["code"] == "literature_upstream"


@pytest.mark.parametrize("transient_status", [406, 503])
def test_transient_arxiv_http_status_retries_once(research_client, monkeypatch, transient_status):
    calls = []
    sleeps = []

    def get(url, **kwargs):
        request = httpx.Request("GET", url, params=kwargs.get("params"), headers=kwargs.get("headers"))
        calls.append(request)
        if len(calls) == 1:
            return httpx.Response(transient_status, request=request)
        return httpx.Response(200, text=atom_entry(), request=request)

    monkeypatch.setattr(httpx, "get", get)
    monkeypatch.setattr("app.research.literature.time.sleep", sleeps.append)
    response = research_client.get(
        "/api/research/literature/arxiv/search", params={"query": "MARL"},
    )
    assert response.status_code == 200
    assert response.json()["items"][0]["arxiv_id"] == "2401.00001v2"
    assert len(calls) == 2 and sleeps == [3.0]
