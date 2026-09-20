"""Source-backed evidence and deterministic research reports."""
import hashlib

from app.research.artifacts import ArtifactStore
from app.research.errors import ResearchConflict, ResearchError
from app.research.literature import ArxivClient
from app.research.models import EvidenceCreate, EvidenceSpan, PaperImport
from app.research.repository import SQLiteResearchRepository, new_id
from app.research.retrieval import ResearchRetriever
from app.session.repository import utc_now


def normalized_text(text: str) -> str:
    return " ".join(text.split())


class ResearchService:
    def __init__(self, repository: SQLiteResearchRepository, artifacts: ArtifactStore,
                 literature: ArxivClient | None = None, embedding=None):
        self.repository = repository
        self.artifacts = artifacts
        self.literature = literature or ArxivClient()
        self.embedding = embedding
        self.retriever = ResearchRetriever(repository, artifacts, embedding)

    async def search_project(self, project_id: str, request):
        self.retriever.embedding = self.embedding
        return await self.retriever.search(project_id, request)

    def import_artifact(self, project_id: str, path: str):
        self.repository.get_project(project_id)
        artifact = self.artifacts.import_file(project_id, path)
        # The snapshot exists before the DB reference. Failed DB writes can leave
        # an unreferenced hash file; retry safely adopts it, never a missing blob.
        return self.repository.save_artifact(artifact)

    def search_arxiv(self, query: str, max_results: int = 5, sort_by: str = "relevance") -> dict:
        return self.literature.search(query, max_results, sort_by)

    @staticmethod
    def _arxiv_metadata(item) -> PaperImport:
        return PaperImport(
            source="arxiv", source_id=item.source_id, version=item.version,
            title=item.title, authors=item.authors, source_url=item.source_url,
            published_at=item.published_at, updated_at=item.updated_at,
            categories=item.categories, primary_category=item.primary_category,
        )

    def import_arxiv(self, project_id: str, arxiv_id: str) -> dict:
        self.repository.get_project(project_id)
        item = self.literature.exact(arxiv_id)
        metadata = self._arxiv_metadata(item)
        paper = self.repository.import_paper(project_id, metadata)
        if paper.artifact_id is not None:
            artifact = self.repository.get(project_id, "artifacts", paper.artifact_id)
            return {"paper": paper.model_dump(mode="json"), "artifact": artifact.model_dump(mode="json")}
        safe_id = item.arxiv_id.replace("/", "-")
        artifact = self.artifacts.import_bytes(
            project_id, f"arxiv-{safe_id}-abstract.txt", item.abstract.encode("utf-8"), "text/plain",
        )
        artifact = self.repository.save_artifact(artifact)
        attached = metadata.model_copy(update={"artifact_id": artifact.artifact_id, "content_scope": "abstract"})
        paper = self.repository.import_paper(project_id, attached)
        return {"paper": paper.model_dump(mode="json"), "artifact": artifact.model_dump(mode="json")}

    @staticmethod
    def _validate_pdf(content: bytes) -> None:
        import pymupdf as fitz
        try:
            with fitz.open(stream=content, filetype="pdf") as document:
                if document.needs_pass:
                    raise ResearchError(
                        "暂不支持加密 arXiv PDF", code="literature_pdf_invalid", status=422,
                    )
                if document.page_count < 1:
                    raise ResearchError(
                        "arXiv PDF 没有可读取页面", code="literature_pdf_invalid", status=422,
                    )
        except (RuntimeError, ValueError) as exc:
            raise ResearchError(
                "arXiv PDF 无法解析", code="literature_pdf_invalid", status=422,
            ) from exc

    def import_arxiv_full_text(self, project_id: str, arxiv_id: str) -> dict:
        self.repository.get_project(project_id)
        item = self.literature.exact(arxiv_id)
        metadata = self._arxiv_metadata(item)
        paper = self.repository.import_paper(project_id, metadata)
        if paper.content_scope == "full_text":
            artifact = self.repository.get(project_id, "artifacts", paper.artifact_id)
            return {"paper": paper.model_dump(mode="json"), "artifact": artifact.model_dump(mode="json")}
        if paper.content_scope not in {None, "abstract"}:
            raise ResearchConflict("论文版本已绑定其他资料，不允许替换")
        content = self.literature.download_pdf(arxiv_id)
        self._validate_pdf(content)
        safe_id = item.arxiv_id.replace("/", "-")
        artifact = self.artifacts.import_bytes(
            project_id, f"arxiv-{safe_id}.pdf", content, "application/pdf",
        )
        artifact = self.repository.save_artifact(artifact)
        paper = self.repository.promote_full_text(
            project_id, paper.paper_version_id, artifact.artifact_id,
        )
        return {"paper": paper.model_dump(mode="json"), "artifact": artifact.model_dump(mode="json")}

    def _page(self, project_id: str, paper_version_id: str, page: int):
        paper = self.repository.get(project_id, "papers", paper_version_id)
        if paper.artifact_id is None:
            raise ResearchError("论文只有元数据，尚未绑定可读取资料")
        artifact = self.repository.get(project_id, "artifacts", paper.artifact_id)
        path = self.artifacts.verified_path(artifact)
        if artifact.media_type == "application/pdf":
            import pymupdf as fitz
            try:
                with fitz.open(path) as document:
                    if document.needs_pass:
                        raise ResearchError("暂不支持加密 PDF")
                    if page < 1 or page > document.page_count:
                        raise ResearchError("PDF 页号超出范围")
                    text = document[page - 1].get_text()
                    count = document.page_count
                method = f"pymupdf-{fitz.VersionBind}/whitespace-v1"
                page_kind = "pdf_page"
            except (RuntimeError, ValueError) as exc:
                raise ResearchError("PDF 无法解析") from exc
        else:
            if page != 1:
                raise ResearchError("文本资料只有一个逻辑页，不是 PDF 物理页")
            try:
                text = path.read_text(encoding="utf-8-sig")
            except UnicodeError as exc:
                raise ResearchError("文本资料必须为 UTF-8") from exc
            count, page_kind, method = 1, "text_document", "utf8/whitespace-v1"
        text = normalized_text(text)
        if not text:
            raise ResearchError("该页未提取到文本；扫描件需要人工处理")
        return paper, artifact, text, count, page_kind, method

    def read_page(self, project_id: str, paper_version_id: str, page: int,
                  offset: int = 0, limit: int = 20000) -> dict:
        paper, artifact, text, count, page_kind, method = self._page(project_id, paper_version_id, page)
        if offset >= len(text):
            raise ResearchError("offset 超出该页文本范围")
        end = min(len(text), offset + limit)
        self.repository.record_read(project_id, paper_version_id, page)
        return {
            "paper_version_id": paper_version_id, "artifact_id": artifact.artifact_id,
            "artifact_sha256": artifact.sha256, "content_scope": paper.content_scope,
            "page": page, "page_kind": page_kind, "total_pages": count,
            "offset": offset, "next_offset": end if end < len(text) else None,
            "text": text[offset:end], "truncated": offset > 0 or end < len(text),
            "extraction_method": method,
        }

    def create_evidence(self, project_id: str, request: EvidenceCreate) -> EvidenceSpan:
        _, artifact, text, _, page_kind, method = self._page(
            project_id, request.paper_version_id, request.page,
        )
        quote = normalized_text(request.quote)
        start = text.find(quote)
        if start < 0:
            raise ResearchError("摘录不在指定资料页中，未创建证据")
        record = EvidenceSpan(
            project_id=project_id, paper_version_id=request.paper_version_id,
            page=request.page, quote=quote, evidence_id=new_id("evidence"),
            artifact_id=artifact.artifact_id, artifact_sha256=artifact.sha256,
            quote_sha256=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
            char_start=start, char_end=start + len(quote), page_kind=page_kind,
            extraction_method=method, created_at=utc_now(),
        )
        return self.repository.save_evidence(record)

    def export_report(self, project_id: str) -> str:
        project, records = self.repository.report_snapshot(project_id)
        for collection in records.values():
            if len(collection) > 1000:
                raise ResearchError("首版报告限制每类 1000 条记录，请缩小项目范围")
        lines = [
            f"# {project.title}", "", f"研究问题：{project.question}", "",
            f"项目：{project.project_id}", "", "## 当前证据边界", "",
            "训练尚未执行；本报告只导出登记的来源、定位与待核验主张。",
            "locator_verified 仅表示摘录存在于资料中，不表示已验证科学结论。",
            "selected_pages 表示曾返回该页的文本片段，不表示读完该页或全文。", "",
            "## 论文版本", "",
        ]
        for paper in records["papers"]:
            lines.extend([
                f"- {paper.title} ({paper.source}:{paper.source_id}, {paper.version})",
                f"  - version_id: {paper.paper_version_id}",
                f"  - 来源：{paper.source_url or '用户提供的本地资料'}",
                f"  - 资料范围：{paper.content_scope or 'metadata'}；读取：{paper.read_scope}；页：{paper.read_pages}",
            ])
        lines.extend(["", "## 主张（尚未完成语义核验）", ""])
        for claim in records["claims"]:
            lines.extend([f"- [{claim.kind} / {claim.verification_status}] {claim.text}",
                          f"  - claim_id: {claim.claim_id}"])
            if not claim.evidence_links:
                lines.append("  - 缺少证据")
            for link in claim.evidence_links:
                lines.append(f"  - {link.relation}: {link.evidence_id}")
        lines.extend(["", "## 原文定位", ""])
        for span in records["evidence"]:
            # Excerpts are user-imported source text. The API does not fetch or
            # invent citations, and reports preserve the declared source scope.
            lines.extend([
                f"- {span.evidence_id} → {span.paper_version_id}",
                f"  - {span.page_kind}: {span.page}; normalized chars: {span.char_start}:{span.char_end}",
                f"  - SHA-256: {span.artifact_sha256}; parser: {span.extraction_method}",
                f"  - 摘录：{span.quote}",
            ])
        lines.extend(["", "## 产物清单", ""])
        for artifact in records["artifacts"]:
            lines.append(
                f"- {artifact.artifact_id}: {artifact.name}; {artifact.size_bytes} bytes; SHA-256: {artifact.sha256}"
            )
        lines.extend(["", "## 下一步", "", "核对论文全文与实验协议；在独立训练记录接入后再分析实验结果。", ""])
        return "\n".join(lines)
