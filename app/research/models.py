"""M0 input and record contracts. Scientific verification is never inferred."""
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, model_validator

Identifier = Annotated[str, Field(min_length=1, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$")]
ShortText = Annotated[str, Field(min_length=1, max_length=500)]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
VersionedArxivId = Annotated[
    str, Field(pattern=r"^(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})v[1-9]\d*$")
]


class ResearchModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ProjectCreate(ResearchModel):
    title: ShortText
    question: str = Field(min_length=1, max_length=4000)
    scope: str = Field(default="", max_length=4000)


class ResearchProject(ProjectCreate):
    project_id: Identifier
    owner: Literal["local"] = "local"
    created_at: str


class ArtifactImport(ResearchModel):
    path: str = Field(min_length=1, max_length=1024, pattern=r"^[^\x00]+$")


class Artifact(ResearchModel):
    artifact_id: Identifier
    project_id: Identifier
    name: ShortText
    media_type: str
    sha256: Digest
    size_bytes: int = Field(ge=0)
    created_at: str


class PaperImport(ResearchModel):
    source: Literal["arxiv", "doi", "local"]
    source_id: ShortText
    version: str = Field(min_length=1, max_length=100)
    title: ShortText
    authors: list[ShortText] = Field(default_factory=list, max_length=100)
    source_url: HttpUrl | None = None
    published_at: str = Field(default="", max_length=100)
    updated_at: str = Field(default="", max_length=100)
    categories: list[ShortText] = Field(default_factory=list, max_length=100)
    primary_category: str = Field(default="", max_length=100)
    artifact_id: Identifier | None = None
    content_scope: Literal["abstract", "full_text", "notes"] | None = None

    @model_validator(mode="after")
    def validate_source(self):
        if (self.artifact_id is None) != (self.content_scope is None):
            raise ValueError("artifact_id 与 content_scope 必须一起提供")
        if self.source == "arxiv":
            if not re.fullmatch(r"(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7})", self.source_id):
                raise ValueError("arxiv source_id 必须为不含版本号的规范 ID")
            if not re.fullmatch(r"v?[1-9]\d*", self.version):
                raise ValueError("arxiv version 必须如 v1")
            self.version = "v" + self.version.removeprefix("v")
            canonical = f"https://arxiv.org/abs/{self.source_id}{self.version}"
            if self.source_url is not None and str(self.source_url) != canonical:
                raise ValueError("arxiv source_url 必须对应指定版本")
            self.source_url = HttpUrl(canonical)
        elif self.source == "doi":
            self.source_id = self.source_id.lower()
            if not re.fullmatch(r"10\.\d{4,9}/\S+", self.source_id):
                raise ValueError("doi source_id 必须为规范 DOI")
        return self


class PaperVersion(PaperImport):
    project_id: Identifier
    paper_id: Identifier
    paper_version_id: Identifier
    created_at: str
    # The active artifact remains backward compatible. These explicit material
    # references preserve an abstract snapshot when a validated PDF is promoted.
    abstract_artifact_id: Identifier | None = None
    full_text_artifact_id: Identifier | None = None
    read_scope: Literal["metadata", "selected_pages"] = "metadata"
    read_pages: list[int] = Field(default_factory=list)


class ArxivImport(ResearchModel):
    arxiv_id: VersionedArxivId


class ArxivPaper(ResearchModel):
    arxiv_id: VersionedArxivId
    source_id: ShortText
    version: str = Field(pattern=r"^v[1-9]\d*$")
    title: ShortText
    authors: list[ShortText] = Field(default_factory=list, max_length=100)
    abstract: str = Field(min_length=1, max_length=50000)
    published_at: str = Field(default="", max_length=100)
    updated_at: str = Field(default="", max_length=100)
    categories: list[ShortText] = Field(default_factory=list, max_length=100)
    primary_category: str = Field(default="", max_length=100)
    source_url: HttpUrl
    pdf_url: HttpUrl


class EvidenceCreate(ResearchModel):
    paper_version_id: Identifier
    page: int = Field(default=1, ge=1)
    quote: str = Field(min_length=1, max_length=6000)


class EvidenceSpan(EvidenceCreate):
    evidence_id: Identifier
    project_id: Identifier
    artifact_id: Identifier
    artifact_sha256: Digest
    quote_sha256: Digest
    page_kind: Literal["pdf_page", "text_document"]
    char_start: int = Field(ge=0)
    char_end: int = Field(gt=0)
    locator_verified: Literal[True] = True
    extraction_method: str
    created_at: str


class EvidenceLink(ResearchModel):
    evidence_id: Identifier
    relation: Literal["supports", "refutes", "background"] = "supports"


class ClaimCreate(ResearchModel):
    text: str = Field(min_length=1, max_length=6000)
    kind: Literal["fact", "inference", "hypothesis"] = "hypothesis"
    evidence_links: list[EvidenceLink] = Field(default_factory=list, max_length=100)

    @model_validator(mode="after")
    def unique_links(self):
        keys = [(item.evidence_id, item.relation) for item in self.evidence_links]
        if len(keys) != len(set(keys)):
            raise ValueError("证据关联不能重复")
        return self


class Claim(ClaimCreate):
    claim_id: Identifier
    project_id: Identifier
    verification_status: Literal["unverified"] = "unverified"
    created_at: str


SearchResource = Literal["papers", "evidence", "claims"]


class ResearchSearchRequest(ResearchModel):
    query: str = Field(min_length=1, max_length=500)
    resources: list[SearchResource] = Field(
        default_factory=lambda: ["papers", "evidence", "claims"], min_length=1, max_length=3,
    )
    semantic: bool = True
    limit: int = Field(default=10, ge=1, le=50)

    @model_validator(mode="after")
    def unique_resources(self):
        if len(self.resources) != len(set(self.resources)):
            raise ValueError("检索资源不能重复")
        return self


class ResearchSearchHit(ResearchModel):
    project_id: Identifier
    resource: SearchResource
    record_id: Identifier
    paper_version_id: Identifier | None = None
    title: str = Field(default="", max_length=500)
    snippet: str = Field(min_length=1, max_length=1000)
    score: float = Field(ge=0.0, le=1.0)
    lexical_score: float = Field(ge=0.0, le=1.0)
    semantic_score: float | None = Field(default=None, ge=0.0, le=1.0)


class ResearchQueryRecord(ResearchSearchRequest):
    query_id: Identifier
    project_id: Identifier
    mode: Literal["hybrid", "lexical", "lexical_fallback"]
    semantic_available: bool
    result_refs: list[str] = Field(default_factory=list, max_length=50)
    created_at: str


class ResearchSearchResult(ResearchModel):
    query_id: Identifier
    mode: Literal["hybrid", "lexical", "lexical_fallback"]
    semantic_available: bool
    items: list[ResearchSearchHit] = Field(default_factory=list, max_length=50)
