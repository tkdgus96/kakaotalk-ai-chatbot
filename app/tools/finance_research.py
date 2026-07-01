from langchain_core.tools import tool

from app.tools.research import research_web
from app.tools.stock import get_stock_quote


@tool
async def get_stock_news(query: str) -> str:
    """종목/기업 관련 최신 뉴스와 주가 맥락을 검색하고 출처 기반으로 요약합니다."""
    return await research_web.ainvoke({"query": f"{query} 주가 뉴스 전망", "max_sources": 3})


@tool
async def get_stock_snapshot(query: str) -> str:
    """종목의 현재 시세와 최근 뉴스 맥락을 함께 요약합니다."""
    quote = await get_stock_quote.ainvoke({"symbol_or_name": query})
    news = await research_web.ainvoke({"query": f"{query} 주가 뉴스 전망", "max_sources": 2})
    return f"[시세]\n{quote}\n\n[뉴스/맥락]\n{news}"


@tool
async def compare_stocks(query: str) -> str:
    """두 개 이상의 종목/기업을 뉴스와 전망 관점에서 비교합니다."""
    return await research_web.ainvoke({"query": f"{query} 주가 전망 비교", "max_sources": 4})
