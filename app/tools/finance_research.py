from langchain_core.tools import tool
from langchain_core.messages import HumanMessage, SystemMessage

from app.dependencies import llm
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


@tool
async def summarize_korean_stock_market(query: str = "오늘 한국 주식시장") -> str:
    """한국 주식시장 흐름을 KOSPI/KOSDAQ/대표주 시세와 오늘 증권 뉴스 근거로 요약합니다."""
    quote_queries = ["코스피", "코스닥", "삼성전자", "SK Hynix"]
    quote_blocks = []
    for item in quote_queries:
        quote = await get_stock_quote.ainvoke({"symbol_or_name": item})
        quote_blocks.append(f"[{item}]\n{quote}")

    news = await research_web.ainvoke(
        {
            "query": f"{query} 코스피 코스닥 삼성전자 SK하이닉스 증권 뉴스 오늘",
            "max_sources": 4,
        }
    )
    response = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "한국 주식시장 요약 전용 답변을 작성한다. 반드시 제공된 시세 JSON과 뉴스 요약만 근거로 사용한다. "
                    "코스피 8000선 같은 비현실적 수치나 제공되지 않은 원인을 만들지 마라. "
                    "시세와 뉴스 방향이 충돌하면 시세 기준 흐름과 뉴스 해석을 분리해서 말하라. "
                    "오늘 기사인지 불확실한 내용은 '기사 시점 확인 필요'라고 표시하라. 짧게 답하라."
                )
            ),
            HumanMessage(
                content=(
                    f"사용자 질문: {query}\n\n"
                    "[시세]\n"
                    + "\n\n".join(quote_blocks)
                    + "\n\n[증권 뉴스 검색/본문 요약]\n"
                    + news
                )
            ),
        ]
    )
    return str(response.content)
