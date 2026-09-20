"""Bounded project retrieval with lexical fallback and cached candidate embeddings."""
from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

from app.research.artifacts import ArtifactStore
from app.research.models import (
    ResearchQueryRecord, ResearchSearchHit, ResearchSearchRequest, ResearchSearchResult,
)
from app.research.repository import SQLiteResearchRepository, new_id
from app.session.repository import utc_now

_MAX_RECORDS_PER_RESOURCE = 1000
_MAX_SEARCH_TEXT = 50_000


@dataclass
class _Candidate:
    resource: str
    record_id: str
    paper_version_id: str | None
    title: str
    text: str
    text_sha256: str


def _tokens(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+|[\u4e00-\u9fff]", text.lower()))


def _lexical_score(query: str, text: str) -> float:
    query_tokens = _tokens(query)
    if not query_tokens:
        return 0.0
    overlap = len(query_tokens & _tokens(text)) / len(query_tokens)
    phrase = 1.0 if " ".join(query.lower().split()) in " ".join(text.lower().split()) else 0.0
    return min(1.0, 0.75 * overlap + 0.25 * phrase)


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or len(left) != len(right):
        raise ValueError("embedding dimension mismatch")
    denominator = math.sqrt(sum(x * x for x in left)) * math.sqrt(sum(x * x for x in right))
    if denominator == 0:
        return 0.0
    return max(-1.0, min(1.0, sum(x * y for x, y in zip(left, right)) / denominator))


class ResearchRetriever:
    def __init__(self, repository: SQLiteResearchRepository, artifacts: ArtifactStore,
                 embedding=None) -> None:
        self.repository = repository
        self.artifacts = artifacts
        self.embedding = embedding

    def _paper_text(self, project_id, paper) -> str:
        parts = [paper.title, " ".join(paper.authors), " ".join(paper.categories),
                 paper.source_id, paper.version]
        artifact_id = paper.abstract_artifact_id
        if artifact_id is None and paper.content_scope in {"abstract", "notes"}:
            artifact_id = paper.artifact_id
        if artifact_id is not None:
            artifact = self.repository.get(project_id, "artifacts", artifact_id)
            if artifact.media_type.startswith("text/"):
                content = self.artifacts.verified_path(artifact).read_bytes()[:_MAX_SEARCH_TEXT]
                parts.append(content.decode("utf-8-sig", errors="replace"))
        return " ".join(part for part in parts if part).strip()[:_MAX_SEARCH_TEXT]

    def _candidates(self, project_id: str, resources: list[str]) -> list[_Candidate]:
        candidates: list[_Candidate] = []
        if "papers" in resources:
            for paper in self.repository.list_records(
                project_id, "papers", limit=_MAX_RECORDS_PER_RESOURCE,
            ):
                text = self._paper_text(project_id, paper)
                candidates.append(_Candidate(
                    "papers", paper.paper_version_id, paper.paper_version_id,
                    paper.title, text, hashlib.sha256(text.encode("utf-8")).hexdigest(),
                ))
        if "evidence" in resources:
            for evidence in self.repository.list_records(
                project_id, "evidence", limit=_MAX_RECORDS_PER_RESOURCE,
            ):
                text = evidence.quote[:_MAX_SEARCH_TEXT]
                candidates.append(_Candidate(
                    "evidence", evidence.evidence_id, evidence.paper_version_id,
                    "Evidence", text, hashlib.sha256(text.encode("utf-8")).hexdigest(),
                ))
        if "claims" in resources:
            for claim in self.repository.list_records(
                project_id, "claims", limit=_MAX_RECORDS_PER_RESOURCE,
            ):
                text = claim.text[:_MAX_SEARCH_TEXT]
                candidates.append(_Candidate(
                    "claims", claim.claim_id, None, "Claim", text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                ))
        return candidates

    def _embedding_model(self) -> str:
        explicit = getattr(self.embedding, "model", None)
        if explicit:
            return str(explicit)
        return f"{type(self.embedding).__name__}:{getattr(self.embedding, 'dim', 'unknown')}"

    async def _semantic_scores(self, project_id: str, query: str,
                               candidates: list[_Candidate]) -> list[float]:
        if self.embedding is None:
            raise RuntimeError("embedding is not configured")
        model = self._embedding_model()
        vectors: list[list[float] | None] = []
        missing: list[tuple[int, _Candidate]] = []
        for index, candidate in enumerate(candidates):
            vector = self.repository.get_search_embedding(
                project_id, candidate.resource, candidate.record_id, model, candidate.text_sha256,
            )
            vectors.append(vector)
            if vector is None:
                missing.append((index, candidate))
        if missing:
            generated = await self.embedding.embed([candidate.text for _, candidate in missing])
            if len(generated) != len(missing):
                raise ValueError("embedding response count mismatch")
            now = utc_now()
            entries = []
            for (index, candidate), vector in zip(missing, generated):
                normalized = [float(item) for item in vector]
                vectors[index] = normalized
                entries.append({
                    "project_id": project_id, "resource": candidate.resource,
                    "record_id": candidate.record_id, "model": model,
                    "text_sha256": candidate.text_sha256, "vector": normalized,
                    "updated_at": now,
                })
            self.repository.save_search_embeddings(entries)
        query_vectors = await self.embedding.embed([query])
        if len(query_vectors) != 1:
            raise ValueError("embedding query response mismatch")
        query_vector = [float(item) for item in query_vectors[0]]
        return [(_cosine(query_vector, vector or []) + 1.0) / 2.0 for vector in vectors]

    async def search(self, project_id: str, request: ResearchSearchRequest) -> ResearchSearchResult:
        self.repository.get_project(project_id)
        candidates = self._candidates(project_id, request.resources)
        lexical = [_lexical_score(request.query, item.text) for item in candidates]
        semantic_scores: list[float | None] = [None] * len(candidates)
        mode = "lexical"
        semantic_available = False
        if request.semantic and candidates:
            try:
                semantic_scores = await self._semantic_scores(project_id, request.query, candidates)
                mode = "hybrid"
                semantic_available = True
            except Exception:
                mode = "lexical_fallback"

        hits: list[ResearchSearchHit] = []
        for candidate, lexical_score, semantic_score in zip(candidates, lexical, semantic_scores):
            if semantic_score is None:
                score = lexical_score
                include = lexical_score > 0
            else:
                score = 0.55 * lexical_score + 0.45 * semantic_score
                include = lexical_score > 0 or semantic_score >= 0.55
            if not include:
                continue
            hits.append(ResearchSearchHit(
                project_id=project_id, resource=candidate.resource,
                record_id=candidate.record_id, paper_version_id=candidate.paper_version_id,
                title=candidate.title, snippet=candidate.text[:1000],
                score=round(score, 6), lexical_score=round(lexical_score, 6),
                semantic_score=(round(semantic_score, 6) if semantic_score is not None else None),
            ))
        hits.sort(key=lambda item: (-item.score, item.resource, item.record_id))
        hits = hits[:request.limit]
        record = ResearchQueryRecord(
            **request.model_dump(), query_id=new_id("query"), project_id=project_id,
            mode=mode, semantic_available=semantic_available,
            result_refs=[f"{item.resource}:{item.record_id}" for item in hits],
            created_at=utc_now(),
        )
        self.repository.save_query(record)
        return ResearchSearchResult(
            query_id=record.query_id, mode=mode,
            semantic_available=semantic_available, items=hits,
        )
