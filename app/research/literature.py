"""Structured, cache-bounded arXiv metadata retrieval."""
import re
import threading
import time
import xml.etree.ElementTree as ET
from typing import Literal

import httpx
from pydantic import ValidationError

from app.config import Settings, get_settings
from app.research.errors import ResearchError
from app.research.models import ArxivPaper

ARXIV_API = "https://export.arxiv.org/api/query"
ARXIV_RETRY_SECONDS = 3.0
# arXiv's Fastly edge has been observed returning an empty 406 once for an
# otherwise valid Atom request from a fresh container connection. A single
# delayed retry follows arXiv's own request-spacing guidance and still lets a
# genuinely invalid query fail deterministically on the second response.
_TRANSIENT_STATUSES = {406, 429, 500, 502, 503, 504}
_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_VERSIONED_ID = re.compile(r"^(?P<source>(?:\d{4}\.\d{4,5}|[a-zA-Z.-]+/\d{7}))(?P<version>v[1-9]\d*)$")


def _clean(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()


def _search_query(query: str) -> str:
    value = query.strip()
    if not value:
        raise ResearchError("arXiv 检索词不能为空")
    explicit = re.search(r'\b\w+:|\b(?:AND|OR|ANDNOT)\b|[()"]', value)
    return value if explicit else " AND ".join(f"all:{word}" for word in value.split())


def parse_arxiv_atom(xml_text: str) -> list[ArxivPaper]:
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        raise ResearchError("arXiv 返回了无法解析的 Atom XML", code="literature_upstream", status=502) from exc
    if root.tag != f"{{{_NS['atom']}}}feed":
        raise ResearchError("arXiv 返回内容不是 Atom feed", code="literature_upstream", status=502)
    papers = []
    for entry in root.findall("atom:entry", _NS):
        raw_id = (entry.findtext("atom:id", "", _NS) or "").strip()
        if "/api/errors" in raw_id:
            raise ResearchError("arXiv API 拒绝了查询表达式", code="literature_upstream", status=502)
        identifier = raw_id.rsplit("/abs/", 1)[-1]
        match = _VERSIONED_ID.fullmatch(identifier)
        if match is None:
            raise ResearchError("arXiv 返回了缺少明确版本的记录", code="literature_upstream", status=502)
        abstract = _clean(entry.findtext("atom:summary", "", _NS) or "")
        if not abstract:
            raise ResearchError("arXiv 记录缺少摘要", code="literature_upstream", status=502)
        categories = []
        for category in entry.findall("atom:category", _NS):
            term = (category.attrib.get("term") or "").strip()
            if term and term not in categories:
                categories.append(term)
        primary = entry.find("arxiv:primary_category", _NS)
        primary_category = (primary.attrib.get("term") or "").strip() if primary is not None else ""
        arxiv_id = match.group(0)
        try:
            papers.append(ArxivPaper(
                arxiv_id=arxiv_id,
                source_id=match.group("source"),
                version=match.group("version"),
                title=_clean(entry.findtext("atom:title", "", _NS) or "") or "(无标题)",
                authors=[name for author in entry.findall("atom:author", _NS)
                         if (name := _clean(author.findtext("atom:name", "", _NS) or ""))],
                abstract=abstract,
                published_at=(entry.findtext("atom:published", "", _NS) or "").strip(),
                updated_at=(entry.findtext("atom:updated", "", _NS) or "").strip(),
                categories=categories,
                primary_category=primary_category,
                source_url=f"https://arxiv.org/abs/{arxiv_id}",
                pdf_url=f"https://arxiv.org/pdf/{arxiv_id}",
            ))
        except ValidationError as exc:
            raise ResearchError(
                "arXiv 返回的记录字段不符合约束", code="literature_upstream", status=502,
            ) from exc
    return papers


class ArxivClient:
    def __init__(self, settings: Settings | None = None, *, cache_seconds: int = 86400):
        self.settings = settings or get_settings()
        self.cache_seconds = cache_seconds
        self._cache: dict[tuple, tuple[float, list[ArxivPaper]]] = {}
        self._lock = threading.Lock()

    def _request(self, key: tuple, params: dict) -> tuple[list[ArxivPaper], bool]:
        now = time.monotonic()
        with self._lock:
            cached = self._cache.get(key)
            if cached is not None and cached[0] > now:
                return [item.model_copy(deep=True) for item in cached[1]], True
        for attempt in range(2):
            try:
                response = httpx.get(
                    ARXIV_API, params=params,
                    headers={
                        "User-Agent": f"ReAgent/{self.settings.agent_version}",
                        "Accept": "application/atom+xml",
                    },
                    timeout=self.settings.http_tool_timeout_seconds, follow_redirects=True,
                )
                response.raise_for_status()
                break
            except httpx.HTTPError as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if attempt == 0 and (status is None or status in _TRANSIENT_STATUSES):
                    time.sleep(ARXIV_RETRY_SECONDS)
                    continue
                raise ResearchError("arXiv API 请求失败", code="literature_upstream", status=502) from exc
        items = parse_arxiv_atom(response.text)
        with self._lock:
            self._cache[key] = (now + self.cache_seconds, items)
        return [item.model_copy(deep=True) for item in items], False

    def search(self, query: str, max_results: int = 5,
               sort_by: Literal["relevance", "submittedDate"] = "relevance") -> dict:
        value = query.strip()
        items, cached = self._request(
            ("search", value, max_results, sort_by),
            {"search_query": _search_query(value), "max_results": max_results,
             "sortBy": sort_by, "sortOrder": "descending" if sort_by == "submittedDate" else "ascending"},
        )
        return {"query": value, "items": [item.model_dump(mode="json") for item in items], "cached": cached}

    def exact(self, arxiv_id: str) -> ArxivPaper:
        items, _ = self._request(("exact", arxiv_id), {"id_list": arxiv_id, "max_results": 1})
        if not items:
            raise ResearchError("arXiv 未返回指定论文版本", code="literature_not_found", status=404)
        if len(items) != 1 or items[0].arxiv_id != arxiv_id:
            raise ResearchError("arXiv 返回版本与请求不一致", code="literature_version_mismatch", status=502)
        return items[0]

    def download_pdf(self, arxiv_id: str) -> bytes:
        """Stream one canonical versioned PDF into a bounded in-memory snapshot."""
        if _VERSIONED_ID.fullmatch(arxiv_id) is None:
            raise ResearchError("arXiv 全文导入需要带 vN 的规范 ID")
        url = f"https://arxiv.org/pdf/{arxiv_id}"
        for attempt in range(2):
            try:
                with httpx.stream(
                    "GET", url,
                    headers={
                        "User-Agent": f"ReAgent/{self.settings.agent_version}",
                        "Accept": "application/pdf",
                    },
                    timeout=self.settings.http_tool_timeout_seconds,
                    follow_redirects=True,
                ) as response:
                    response.raise_for_status()
                    if (response.url.host or "").lower() not in {
                        "arxiv.org", "www.arxiv.org", "export.arxiv.org",
                    }:
                        raise ResearchError(
                            "arXiv PDF 重定向到了不受信任的来源",
                            code="literature_pdf_origin", status=502,
                        )
                    content_type = response.headers.get("content-type", "").split(";", 1)[0].lower()
                    if content_type != "application/pdf":
                        raise ResearchError(
                            "arXiv 返回内容不是 PDF", code="literature_pdf_invalid", status=422,
                        )
                    content_length = response.headers.get("content-length")
                    if content_length:
                        try:
                            declared_size = int(content_length)
                        except ValueError as exc:
                            raise ResearchError(
                                "arXiv 返回了无效的 PDF 长度",
                                code="literature_pdf_download", status=502,
                            ) from exc
                        if declared_size > self.settings.research_max_artifact_bytes:
                            raise ResearchError(
                                "arXiv PDF 超过资料大小限制", code="artifact_too_large", status=413,
                            )
                    chunks = []
                    size = 0
                    for chunk in response.iter_bytes():
                        size += len(chunk)
                        if size > self.settings.research_max_artifact_bytes:
                            raise ResearchError(
                                "arXiv PDF 超过资料大小限制", code="artifact_too_large", status=413,
                            )
                        chunks.append(chunk)
                content = b"".join(chunks)
                if not content.startswith(b"%PDF-"):
                    raise ResearchError(
                        "arXiv 返回内容缺少 PDF 标识", code="literature_pdf_invalid", status=422,
                    )
                return content
            except httpx.HTTPError as exc:
                status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
                if attempt == 0 and (status is None or status in _TRANSIENT_STATUSES):
                    time.sleep(ARXIV_RETRY_SECONDS)
                    continue
                raise ResearchError(
                    "arXiv PDF 下载失败", code="literature_pdf_download", status=502,
                ) from exc
        raise AssertionError("unreachable")
