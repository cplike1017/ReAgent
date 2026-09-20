"""Project-scoped research operations exposed through the normal ToolGateway."""
import inspect
from functools import wraps
from typing import Literal

from pydantic import BaseModel, Field

from app.errors import ToolExecutionError
from app.research.errors import ResearchError
from app.research.models import (
    ArxivImport, ArtifactImport, ClaimCreate, EvidenceCreate, Identifier, PaperImport,
    ProjectCreate, ResearchModel, ResearchSearchRequest,
)
from app.research.service import ResearchService
from app.tools.registry import ToolDefinition, ToolRegistry


class ProjectArgs(ResearchModel):
    project_id: Identifier = Field(description="目标项目 ID；先列出或创建项目，不猜测 ID")


class PageArgs(ResearchModel):
    limit: int = Field(default=50, ge=1, le=200)
    offset: int = Field(default=0, ge=0, le=2**63 - 1)


class ResourceArgs(ProjectArgs):
    resource: Literal["artifacts", "papers", "evidence", "claims"]


class ListRecordsArgs(ResourceArgs, PageArgs):
    pass


class GetRecordArgs(ResourceArgs):
    record_id: Identifier


class ImportArtifactArgs(ProjectArgs, ArtifactImport):
    pass


class ImportPaperArgs(ProjectArgs, PaperImport):
    pass


class ReadPageArgs(ProjectArgs):
    paper_version_id: Identifier
    page: int = Field(default=1, ge=1)
    offset: int = Field(default=0, ge=0, le=2**63 - 1)
    limit: int = Field(default=20000, ge=1, le=20000)


class CreateEvidenceArgs(ProjectArgs, EvidenceCreate):
    pass


class CreateClaimArgs(ProjectArgs, ClaimCreate):
    pass


class SearchArxivArgs(ResearchModel):
    query: str = Field(min_length=1, max_length=500)
    max_results: int = Field(default=5, ge=1, le=10)
    sort_by: Literal["relevance", "submittedDate"] = "relevance"


class ImportArxivArgs(ProjectArgs, ArxivImport):
    pass


class SearchProjectArgs(ProjectArgs, ResearchSearchRequest):
    pass


class ListQueriesArgs(ProjectArgs, PageArgs):
    pass


def _handler(operation):
    def normalize(result):
        return result.model_dump(mode="json") if isinstance(result, BaseModel) else result

    if inspect.iscoroutinefunction(operation):
        @wraps(operation)
        async def invoke(**args):
            try:
                return normalize(await operation(**args))
            except ResearchError as exc:
                raise ToolExecutionError(str(exc), code=exc.code, transient=False) from exc
        return invoke

    @wraps(operation)
    def invoke(**args):
        try:
            result = operation(**args)
        except ResearchError as exc:
            # Preserve domain codes without opt-in to transient write retries.
            raise ToolExecutionError(str(exc), code=exc.code, transient=False) from exc
        return normalize(result)
    return invoke


def register_research_tools(registry: ToolRegistry, service: ResearchService) -> None:
    """Use the caller-owned service; no HTTP, global settings or DB lifecycle here."""
    repo = service.repository

    def create_project(**body):
        return repo.create_project(ProjectCreate(**body))

    def list_projects(limit, offset):
        return {"items": [p.model_dump(mode="json") for p in repo.list_projects(limit, offset)]}

    def import_paper(project_id, **body):
        return repo.import_paper(project_id, PaperImport(**body))

    def list_records(project_id, resource, limit, offset):
        return {"items": [r.model_dump(mode="json") for r in repo.list_records(project_id, resource, limit, offset)]}

    def get_record(project_id, resource, record_id):
        return repo.get(project_id, resource, record_id)

    def create_evidence(project_id, **body):
        return service.create_evidence(project_id, EvidenceCreate(**body))

    def create_claim(project_id, **body):
        return repo.create_claim(project_id, ClaimCreate(**body))

    def export_report(project_id):
        return {"project_id": project_id, "markdown": service.export_report(project_id)}

    def export_comparison(project_id):
        return {"project_id": project_id, "csv": service.export_comparison(project_id)}

    def export_bibtex(project_id):
        return {"project_id": project_id, "bibtex": service.export_bibtex(project_id)}

    async def search_project(project_id, **body):
        return await service.search_project(project_id, ResearchSearchRequest(**body))

    def list_queries(project_id, limit, offset):
        return {"items": [
            item.model_dump(mode="json")
            for item in repo.list_queries(project_id, limit, offset)
        ]}

    definitions = [
        ("research_create_project", ProjectCreate, create_project, "medium",
         "用户要保存科研资料时创建通用研究项目；title/question/scope 来自用户任务，不绑定算法。先列出项目避免重复；写入失败后先查询，不盲目重建。"),
        ("research_list_projects", PageArgs, list_projects, "low",
         "查找已保存科研项目及其 ID；limit/offset 分页，返回当前页而非全部项目。普通聊天无需创建项目；目标不清楚时向用户核对。"),
        ("research_get_project", ProjectArgs, repo.get_project, "low",
         "读取指定项目的问题与范围；用已知 ID，不存在时列出项目核对，不能猜测。"),
        ("research_import_artifact", ImportArtifactArgs, service.import_artifact, "medium",
         "将沙箱相对路径中的已有 PDF/文本/CSV/JSON 快照归档到项目，返回 SHA-256；不下载 URL、不启动训练。同一文件可安全重复导入；找不到时先 list_files。"),
        ("research_import_paper", ImportPaperArgs, import_paper, "medium",
         "登记已知来源的论文版本；可绑定本项目 Artifact，并声明 abstract/full_text/notes 范围。仅元数据不代表读过全文；不确定版本时先核对来源。"),
        ("research_search_arxiv", SearchArxivArgs, service.search_arxiv, "low",
         "结构化检索 arXiv 元数据，返回明确版本、发布日期、更新日期、摘要与分类；先搜索再选择版本。结果不等于已导入项目，也不代表读过全文；失败时调整查询或说明上游错误。"),
        ("research_search_project", SearchProjectArgs, search_project, "low",
         "在指定项目已保存的论文、证据和主张中检索并持久记录查询；默认组合词法与语义排序，embedding 不可用时明确降级为 lexical_fallback。结果保留资源类型和记录 ID，不跨项目、不自动核验结论。"),
        ("research_list_queries", ListQueriesArgs, list_queries, "low",
         "分页查看指定项目的持久查询历史、检索模式和返回记录 ID；用于复查检索过程，不把历史命中解释为科学证据。"),
        ("research_import_arxiv", ImportArxivArgs, service.import_arxiv, "medium",
         "按带 vN 的精确 arXiv ID 重新核对上游并将摘要快照导入指定项目；保留版本 URL、日期和 SHA-256。只导入摘要，不下载全文；不确定版本时先用 research_search_arxiv。"),
        ("research_import_arxiv_full_text", ImportArxivArgs, service.import_arxiv_full_text, "medium",
         "按带 vN 的精确 arXiv ID 下载受限大小的官方 PDF，校验来源、类型和结构后保存全文快照；可从摘要提升并保留摘要资料引用。导入后仍需逐页 research_read_page 才能声明读过相应内容；扫描页不自动 OCR。"),
        ("research_list_records", ListRecordsArgs, list_records, "low",
         "分页列出指定项目的资料、论文版本、证据或主张；用 offset 继续读取，不能把空页解释为其他项目也无资料。"),
        ("research_get_record", GetRecordArgs, get_record, "low",
         "按项目和记录 ID 查询科研记录及来源状态；找不到时核对项目与类型，禁止用其他项目的证据替代。"),
        ("research_read_page", ReadPageArgs, service.read_page, "low",
         "读取已绑定快照的论文页文本，保留 hash/资料范围/截断信息；next_offset 非空时继续同页。读取片段不代表阅读全文；无全文或解析失败时如实说明，不编造原文。"),
        ("research_create_evidence", CreateEvidenceArgs, create_evidence, "medium",
         "将确实出现在指定资料页的原文摘录保存为证据；先 research_read_page 核对。locator_verified 只证明文字定位，不证明科学结论；摘录失败时重新阅读，不伪造引用。"),
        ("research_create_claim", CreateClaimArgs, create_claim, "medium",
         "保存事实陈述、推断或假设并关联本项目证据；服务端固定为 unverified，不能自称已核验。supports/refutes/background 表达关系；写入失败先查询，避免重复。"),
        ("research_export_report", ProjectArgs, export_report, "low",
         "从项目记录导出确定性 Markdown 报告，返回正文及项目 ID；保留证据 ID、hash、未核验与未训练边界。无统计或训练能力，不凭空补指标；记录过多时缩小项目。"),
        ("research_export_comparison", ProjectArgs, export_comparison, "low",
         "导出项目内论文版本的确定性 CSV 比较矩阵，包含来源、阅读范围、证据数和已关联的待核验主张；只汇总已保存记录，不推断论文结论。"),
        ("research_export_bibtex", ProjectArgs, export_bibtex, "low",
         "为项目内精确论文版本导出确定性 BibTeX；保留 arXiv/DOI、版本、作者和来源 URL，不表示已阅读或核验论文内容。"),
    ]
    for name, model, operation, risk, description in definitions:
        registry.register(ToolDefinition(
            name=name, input_model=model, handler=_handler(operation),
            description=description, risk_level=risk, extra={"category": "research"},
        ))
