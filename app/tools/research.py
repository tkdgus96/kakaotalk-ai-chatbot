from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.config import settings
from app.dependencies import llm
from app.services.crawl_service import CrawlService
from app.services.search_service import SearchService

_search_service = SearchService(settings)
_crawl_service = CrawlService(settings)


@tool
async def research_web(query: str, max_sources: int = 3) -> str:
    """검색 결과 상위 출처를 실제로 읽고, 출처별 근거를 비교해 리서치 요약을 만듭니다."""
    limit = max(1, min(max_sources, settings.max_crawl_urls, 5))
    try:
        results = await _search_service.search_web(query, limit=limit)
        if not results:
            results = await _search_service.search_naver(query, limit=limit)
    except Exception as exc:
        return f"검색 중 오류가 발생했습니다: {exc}"
    if not results:
        return "검색 결과가 없습니다."

    try:
        docs = await _crawl_service.crawl_many([r.url for r in results[:limit]])
    except Exception as exc:
        docs = []
        crawl_error = str(exc)
    else:
        crawl_error = ""
    blocks = []
    for idx, result in enumerate(results[:limit]):
        doc = docs[idx] if idx < len(docs) else None
        if doc and doc.success and doc.markdown:
            blocks.append(
                f"[출처]\n제목: {result.title}\nURL: {result.url}\n본문:\n{doc.markdown[:4000]}"
            )
        else:
            blocks.append(
                f"[출처]\n제목: {result.title}\nURL: {result.url}\n검색 요약: {result.snippet}\n크롤 오류: {doc.error if doc else crawl_error or '없음'}"
            )

    response = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "여러 출처를 비교해 한국어로 답하라. 출처별 공통점/차이점, 확실한 사실, "
                    "불확실한 주장, 답변에 사용할 수 있는 근거 URL을 구분해라. "
                    "출처 본문에 명시되지 않은 모델명, 날짜, 수치, 발표 내용을 추측하거나 만들지 마라."
                )
            ),
            HumanMessage(content=f"질문: {query}\n\n" + "\n\n---\n\n".join(blocks)),
        ]
    )
    return str(response.content)


@tool
async def compare_sources(query: str, max_sources: int = 3) -> str:
    """같은 주제에 대한 여러 출처의 주장 차이를 비교합니다."""
    return await research_web.ainvoke({"query": query, "max_sources": max_sources})
