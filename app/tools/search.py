import re

import httpx
from langchain_core.tools import tool
from tavily import AsyncTavilyClient

from app.config import settings

_tavily: AsyncTavilyClient | None = (
    AsyncTavilyClient(api_key=settings.tavily_api_key) if settings.tavily_api_key else None
)


def _strip_html(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


@tool
async def naver_search(query: str) -> str:
    """한국 콘텐츠 검색 (네이버). 한국 회사·인물·제품·이벤트·맛집·시사·연예,
    또는 '오늘 출시한 X', '방금 발표된 Y'처럼 한국 미디어에 막 보도된 정보에 대해 물어볼 때 사용하세요.
    네이버는 한국 뉴스를 분 단위로 색인하므로 당일 정보도 잘 찾습니다.
    영어 검색어/해외 콘텐츠는 web_search를 사용하세요."""
    if not (settings.naver_client_id and settings.naver_client_secret):
        return "네이버 검색 키가 설정되어 있지 않습니다. (NAVER_CLIENT_ID/SECRET)"

    headers = {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            news_resp, web_resp = await _gather_naver(client, headers, query)
    except Exception as exc:
        return f"네이버 검색 중 오류: {exc}"

    blocks: list[str] = []
    for src, resp in (("뉴스", news_resp), ("웹", web_resp)):
        if resp is None or resp.status_code != 200:
            continue
        for it in resp.json().get("items", [])[:5]:
            title = _strip_html(it.get("title"))
            desc = _strip_html(it.get("description"))
            link = it.get("originallink") or it.get("link") or ""
            pub = it.get("pubDate", "")
            head = f"[{src}] 제목: {title}"
            if pub:
                head += f"\n날짜: {pub}"
            blocks.append(f"{head}\nURL: {link}\n내용: {desc}")
    if not blocks:
        return "네이버 검색 결과가 없습니다."
    return "\n\n---\n\n".join(blocks)


async def _gather_naver(client: httpx.AsyncClient, headers: dict, query: str):
    import asyncio

    news = client.get(
        "https://openapi.naver.com/v1/search/news.json",
        params={"query": query, "display": 5, "sort": "date"},
        headers=headers,
    )
    web = client.get(
        "https://openapi.naver.com/v1/search/webkr.json",
        params={"query": query, "display": 5},
        headers=headers,
    )
    return await asyncio.gather(news, web, return_exceptions=False)


@tool
async def web_search(query: str) -> str:
    """글로벌 웹 검색 (Tavily). 영어 검색어, 해외 기업·인물·제품·이벤트·기술·학술 정보,
    글로벌 시사·환율·코인·해외 스포츠 등에 사용하세요.
    네이버 검색에서 정보가 부족한 한국 주제도 보조로 사용 가능합니다.
    한국 콘텐츠는 가능하면 naver_search를 우선 호출하세요."""
    if _tavily is None:
        return "Tavily API 키가 설정되어 있지 않습니다. (TAVILY_API_KEY)"
    try:
        result = await _tavily.search(
            query=query,
            search_depth="advanced",
            max_results=5,
            include_answer=False,
        )
    except Exception as exc:
        return f"Tavily 검색 중 오류: {exc}"

    items = result.get("results", [])
    if not items:
        return "검색 결과가 없습니다."
    blocks = []
    for it in items:
        content = (it.get("content") or "")[:1200]
        blocks.append(f"제목: {it.get('title')}\nURL: {it.get('url')}\n내용: {content}")
    return "\n\n---\n\n".join(blocks)
