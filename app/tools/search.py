from langchain_core.tools import tool

from app.config import settings
from app.services.crawl_service import CrawlDocument, CrawlService
from app.services.search_service import SearchResult, SearchService

search_service = SearchService(settings)
crawl_service = CrawlService(settings)
_tavily = search_service._tavily


@tool
async def naver_search(query: str) -> str:
    """한국 관련 검색은 일단 이거 우선 (네이버 API).
    - 한국 날씨·미세먼지·기온 (서울/부산 등 한국 도시)
    - 한국 회사·인물·제품·이벤트·맛집·연예·시사
    - '오늘 출시한 X', '방금 발표된 Y' 같은 한국 미디어 최신 보도
    네이버는 한국 뉴스를 분 단위로 색인하므로 캐시 지연이 적습니다.
    영어 검색어나 해외 도시·기업·기술 등은 web_search를 쓰세요."""
    if not (settings.naver_client_id and settings.naver_client_secret):
        return "네이버 검색 키가 설정되어 있지 않습니다. (NAVER_CLIENT_ID/SECRET)"

    try:
        results = await search_service.search_naver(query)
        docs = await crawl_service.crawl_many([result.url for result in results[: settings.max_crawl_urls]])
    except Exception as exc:
        return f"네이버 검색 중 오류: {exc}"

    if not results:
        return "네이버 검색 결과가 없습니다."
    return _format_search_with_crawl(results, docs)


@tool
async def web_search(query: str) -> str:
    """글로벌/사실 웹 검색. 해외 기업·인물·제품·기술·시사, 최신 사건·실적·수치 등
    정확도가 중요한 질문에 사용하세요. 기본적으로 OpenAI 웹검색으로 출처(인용)까지 붙여
    최신·정확하게 답합니다. 한국 당일 뉴스는 naver_search를 우선 쓰세요."""
    # Primary: grounded search (OpenAI web_search) — recency + citations.
    from app.tools.grounded_search import grounded_web_answer

    grounded = await grounded_web_answer(query)
    if grounded:
        return grounded

    # Fallback: Tavily + crawl.
    if not search_service.is_web_available():
        return "웹 검색을 사용할 수 없습니다. (검색 키 미설정)"
    try:
        results = await search_service.search_web(query)
        docs = await crawl_service.crawl_many([result.url for result in results[: settings.max_crawl_urls]])
    except Exception as exc:
        return f"웹 검색 중 오류: {exc}"

    if not results:
        return "검색 결과가 없습니다."
    return _format_search_with_crawl(results, docs)


def _format_search_with_crawl(results: list[SearchResult], docs: list[CrawlDocument]) -> str:
    docs_by_url = {doc.url: doc for doc in docs}
    blocks: list[str] = []
    for result in results:
        doc = docs_by_url.get(result.url)
        head = f"[{result.source_type}] 제목: {result.title}\nURL: {result.url}"
        if result.published_at:
            head += f"\n날짜: {result.published_at}"

        if doc and doc.success and doc.markdown:
            content = doc.markdown[:3000]
            cache_note = "true" if doc.from_cache else "false"
            blocks.append(f"{head}\n크롤 성공: true\n캐시 사용: {cache_note}\n본문 Markdown:\n{content}")
        else:
            error = doc.error if doc else "크롤 대상 아님"
            blocks.append(f"{head}\n크롤 성공: false\n크롤 오류: {error}\n검색 요약: {result.snippet}")
    return "\n\n---\n\n".join(blocks)
