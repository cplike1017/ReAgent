"""
真实工具包：arxiv_search（学术论文检索）。

为什么需要它？Trace 数据显示 researcher 在调研任务里反复用 web_search 找论文、
再用 http_get 手拼 arxiv.org/abs/XXX 验证 —— 低效且易错。arxiv_search 直接
调 arXiv API（export.arxiv.org/api/query，免费无需 Key），一次返回结构化
论文列表（标题/作者/年份/摘要/链接），是学术调研的垂直工具。

安全设计：
    - 只请求 arXiv 官方 API；响应大小限制 32KB；
    - 无 Key、零依赖（免费 API），失败返回结构化错误。
"""
import httpx
from pydantic import BaseModel, Field

from app.config import get_settings
from app.errors import ToolExecutionError
from app.research.errors import ResearchError
from app.research.literature import ArxivClient, parse_arxiv_atom


class ArxivSearchArgs(BaseModel):
    """arXiv 检索参数。"""

    query: str = Field(description="检索关键词（支持 AND/OR/引号，如 'graph neural network MARL'）")
    max_results: int = Field(default=5, ge=1, le=10, description="返回论文条数（1-10）")
    sort_by: str = Field(default="relevance", description="排序：relevance(相关度) | submittedDate(最新)")


def arxiv_search_handler(query: str, max_results: int = 5, sort_by: str = "relevance") -> str:
    """检索 arXiv 论文，返回结构化列表文本。"""
    if not query.strip():
        raise ToolExecutionError("检索关键词不能为空")
    sort = "relevance" if sort_by == "relevance" else "submittedDate"
    try:
        entries = ArxivClient(get_settings()).search(query, max_results, sort)["items"]
    except ResearchError as exc:
        raise ToolExecutionError(str(exc), code=exc.code) from exc
    if not entries:
        return "arXiv 未检索到相关论文。"
    lines = [f"arXiv 检索「{query}」共 {len(entries)} 条结果：", ""]
    for i, e in enumerate(entries, 1):
        lines.append(f"{i}. **{e['title']}**")
        lines.append(f"   作者: {', '.join(e['authors']) or '未知'}")
        lines.append(f"   年份: {e['published_at'][:4]} | 链接: {e['source_url']}")
        lines.append(f"   首次发表: {e['published_at']} | 最近更新: {e['updated_at']}")
        if e["abstract"]:
            lines.append(f"   摘要: {e['abstract'][:220]}")
        lines.append("")
    return "\n".join(lines)


def _parse_arxiv_atom(xml_text: str) -> list[dict]:
    """Legacy formatted parser backed by the shared structured Atom parser."""
    try:
        items = parse_arxiv_atom(xml_text)
    except ResearchError as exc:
        raise ToolExecutionError(str(exc), code=exc.code) from exc
    return [{
        "title": item.title, "authors": ", ".join(item.authors)[:120] or "未知",
        "year": item.published_at[:4], "published": item.published_at,
        "updated": item.updated_at, "link": str(item.source_url), "abstract": item.abstract,
    } for item in items]
