"""Offline contracts for persistent project-scoped hybrid research retrieval."""
import fakeredis.aioredis
from fastapi.testclient import TestClient

from app.main import create_app
from app.research.models import ProjectCreate
from app.research.repository import SQLiteResearchRepository
from tests.test_research_foundation import (
    create_project, import_paper, research_client, research_settings,
)


class SemanticEmbedding:
    model = "semantic-fixture-v1"
    dim = 2

    def __init__(self):
        self.calls = []

    async def embed(self, texts):
        self.calls.append(list(texts))
        vectors = []
        for text in texts:
            value = text.lower()
            if "temporal difference" in value or "value learning" in value:
                vectors.append([1.0, 0.0])
            elif "policy gradient" in value:
                vectors.append([0.0, 1.0])
            else:
                vectors.append([0.5, 0.5])
        return vectors


class FailingEmbedding:
    model = "unavailable-fixture-v1"
    dim = 2

    async def embed(self, texts):
        raise RuntimeError("embedding unavailable")


def _paper(client, project_id, source_id, title):
    return import_paper(
        client, project_id, source_id=source_id, title=title, version="v1",
    )


def test_existing_v1_database_upgrades_without_losing_projects(monkeypatch, research_settings):
    from app.research import migrations

    complete = migrations.MIGRATIONS
    monkeypatch.setattr(migrations, "MIGRATIONS", complete[:1])
    repository = SQLiteResearchRepository(research_settings.database_url)
    project = repository.create_project(ProjectCreate(title="Legacy", question="Keep this"))
    repository.close()
    monkeypatch.setattr(migrations, "MIGRATIONS", complete)

    repository = SQLiteResearchRepository(research_settings.database_url)
    try:
        assert repository.get_project(project.project_id) == project
        assert repository.list_queries(project.project_id) == []
    finally:
        repository.close()


def test_hybrid_search_ranks_semantic_match_and_reuses_cache_after_restart(research_settings):
    fake_redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    app = create_app(research_settings, redis=fake_redis)
    with TestClient(app) as client:
        project_id = create_project(client, "Retrieval project")
        value_paper = _paper(client, project_id, "value-paper", "Value learning control")
        _paper(client, project_id, "policy-paper", "Policy gradient optimization")
        embedding = SemanticEmbedding()
        app.state.research_service.embedding = embedding
        response = client.post(f"/api/research/projects/{project_id}/search", json={
            "query": "temporal difference", "resources": ["papers"], "limit": 2,
        })
        assert response.status_code == 200, response.text
        body = response.json()
        assert body["mode"] == "hybrid" and body["semantic_available"] is True
        assert body["items"][0]["record_id"] == value_paper["paper_version_id"]
        assert [len(call) for call in embedding.calls] == [2, 1]

    app = create_app(research_settings, redis=fakeredis.aioredis.FakeRedis(decode_responses=True))
    with TestClient(app) as client:
        embedding = SemanticEmbedding()
        app.state.research_service.embedding = embedding
        repeated = client.post(f"/api/research/projects/{project_id}/search", json={
            "query": "temporal difference", "resources": ["papers"], "limit": 2,
        })
        assert repeated.status_code == 200
        assert [len(call) for call in embedding.calls] == [1]
        history = client.get(f"/api/research/projects/{project_id}/queries").json()["items"]
        assert len(history) == 2
        assert all(item["query"] == "temporal difference" for item in history)
        assert all(item["result_refs"][0] == f"papers:{value_paper['paper_version_id']}" for item in history)


def test_embedding_failure_falls_back_to_lexical_and_records_mode(research_client):
    project_id = create_project(research_client, "Fallback project")
    paper = _paper(research_client, project_id, "policy-paper", "Policy gradient optimization")
    research_client.app.state.research_service.embedding = FailingEmbedding()
    response = research_client.post(f"/api/research/projects/{project_id}/search", json={
        "query": "policy gradient", "resources": ["papers"], "limit": 5,
    })
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["mode"] == "lexical_fallback" and body["semantic_available"] is False
    assert [item["record_id"] for item in body["items"]] == [paper["paper_version_id"]]
    history = research_client.get(f"/api/research/projects/{project_id}/queries").json()["items"]
    assert history[0]["mode"] == "lexical_fallback"


def test_search_is_project_scoped_and_returns_evidence_claim_provenance(research_client):
    project_id = create_project(research_client, "Evidence search")
    other_id = create_project(research_client, "Other project")
    artifact = research_client.post(
        f"/api/research/projects/{project_id}/artifacts/import", json={"path": "paper.txt"},
    ).json()
    paper = import_paper(research_client, project_id, artifact["artifact_id"])
    evidence = research_client.post(f"/api/research/projects/{project_id}/evidence", json={
        "paper_version_id": paper["paper_version_id"], "page": 1,
        "quote": "The baseline uses a fixed evaluation protocol.",
    }).json()
    claim = research_client.post(f"/api/research/projects/{project_id}/claims", json={
        "text": "The evaluation protocol is fixed.", "kind": "fact",
        "evidence_links": [{"evidence_id": evidence["evidence_id"], "relation": "supports"}],
    }).json()
    _paper(research_client, other_id, "other", "Fixed evaluation protocol hidden elsewhere")

    response = research_client.post(f"/api/research/projects/{project_id}/search", json={
        "query": "fixed evaluation protocol", "resources": ["evidence", "claims"],
        "semantic": False, "limit": 10,
    })
    assert response.status_code == 200
    body = response.json()
    assert body["mode"] == "lexical" and body["semantic_available"] is False
    refs = {(item["resource"], item["record_id"]) for item in body["items"]}
    assert refs == {("evidence", evidence["evidence_id"]), ("claims", claim["claim_id"])}
    assert all(item["project_id"] == project_id for item in body["items"])
    assert next(item for item in body["items"] if item["resource"] == "evidence")[
        "paper_version_id"
    ] == paper["paper_version_id"]


async def test_agent_search_and_history_tools_share_persisted_records(research_client):
    project_id = create_project(research_client, "Agent retrieval")
    paper = _paper(research_client, project_id, "agent-paper", "Policy gradient optimization")
    gateway = research_client.app.state.runtime.tool_gateway
    search = await gateway.execute("research_search_project", {
        "project_id": project_id, "query": "policy gradient",
        "resources": ["papers"], "semantic": False,
    })
    assert search.success and search.data["items"][0]["record_id"] == paper["paper_version_id"]
    history = await gateway.execute("research_list_queries", {"project_id": project_id})
    assert history.success and len(history.data["items"]) == 1
    assert history.data["items"][0]["query_id"] == search.data["query_id"]


def test_search_inputs_are_bounded_and_queries_are_project_isolated(research_client):
    first = create_project(research_client, "First")
    second = create_project(research_client, "Second")
    assert research_client.post(f"/api/research/projects/{first}/search", json={
        "query": "", "limit": 5,
    }).status_code == 422
    assert research_client.post(f"/api/research/projects/{first}/search", json={
        "query": "x", "limit": 51,
    }).status_code == 422
    assert research_client.post(f"/api/research/projects/{first}/search", json={
        "query": "x", "resources": ["papers", "papers"],
    }).status_code == 422
    ok = research_client.post(f"/api/research/projects/{first}/search", json={
        "query": "nothing", "semantic": False,
    })
    assert ok.status_code == 200 and ok.json()["items"] == []
    assert len(research_client.get(f"/api/research/projects/{first}/queries").json()["items"]) == 1
    assert research_client.get(f"/api/research/projects/{second}/queries").json()["items"] == []
