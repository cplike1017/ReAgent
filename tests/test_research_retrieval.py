"""Offline contracts for evidence-preserving arXiv retrieval."""
import httpx
import pytest

from app.errors import ToolExecutionError
from app.tools.builtin import research


EMPTY_FEED = '<feed xmlns="http://www.w3.org/2005/Atom" />'


@pytest.fixture
def arxiv_response(monkeypatch):
    """Replace only the external HTTP boundary, retaining real XML/query handling."""
    calls = []

    def install(body=EMPTY_FEED, status=200):
        def get(url, **kwargs):
            request = httpx.Request("GET", url, params=kwargs.get("params"))
            calls.append(request)
            return httpx.Response(status, text=body, request=request)

        monkeypatch.setattr(research.httpx, "get", get)
        return calls

    return install


@pytest.mark.parametrize("query", [
    'ti:"multi-agent reinforcement learning" AND (all:QMIX OR all:MAPPO)',
    "all:QMIX ANDNOT cat:cs.CV",
    "QMIX OR MAPPO",
    'all:MARL AND submittedDate:[202401010000 TO 202512312359]',
])
def test_arxiv_preserves_explicit_query_syntax(arxiv_response, query):
    calls = arxiv_response()
    research.arxiv_search_handler(query)
    assert calls[0].url.params["search_query"] == query


def test_arxiv_plain_keywords_are_not_one_exact_phrase(arxiv_response):
    calls = arxiv_response()
    research.arxiv_search_handler("multi-agent reinforcement learning")
    assert calls[0].url.params["search_query"] == "all:multi-agent AND all:reinforcement AND all:learning"


@pytest.mark.parametrize("body", [
    "<not xml",
    "<html><body>Temporary upstream failure</body></html>",
    '<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
    '<id>http://arxiv.org/api/errors#incorrect_id_format_for_1</id>'
    '<title>Error</title><summary>Incorrect id</summary></entry></feed>',
])
def test_arxiv_upstream_failure_is_not_reported_as_no_results(arxiv_response, body):
    arxiv_response(body)
    with pytest.raises(ToolExecutionError):
        research.arxiv_search_handler("MARL")


def test_arxiv_valid_empty_feed_is_a_real_empty_result(arxiv_response):
    arxiv_response()
    assert "未检索到" in research.arxiv_search_handler("MARL")


def test_arxiv_http_failure_is_a_tool_error(arxiv_response):
    arxiv_response(status=503)
    with pytest.raises(ToolExecutionError, match="arXiv API"):
        research.arxiv_search_handler("MARL")


def test_arxiv_evidence_retains_version_and_publication_dates(arxiv_response):
    arxiv_response('<feed xmlns="http://www.w3.org/2005/Atom"><entry>'
                   '<id>http://arxiv.org/abs/2401.00001v2</id><title>Synthetic MARL fixture</title>'
                   '<published>2024-01-01T00:00:00Z</published>'
                   '<updated>2024-02-02T00:00:00Z</updated>'
                   '<summary>Fixture abstract only; not a verified experiment.</summary>'
                   '</entry></feed>')
    output = research.arxiv_search_handler("MARL")
    assert "2401.00001v2" in output
    assert "2024-01-01" in output and "2024-02-02" in output
    assert "摘要" in output
