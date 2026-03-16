from langchain_core.tools import tool

from app.dependencies import exa


@tool
async def web_search(query: str) -> str:
    """웹에서 최신 정보를 검색합니다. 유저가 실시간 정보, 뉴스, 최신 이벤트, 주식 관련 배경 정보, 또는 AI가 모르는 정보에 대해 물어볼 때 사용하세요."""
    try:
        response = exa.search(
            query,
            num_results=3,
            type="auto",
            contents={"text": {"max_characters": 3000}},
        )
        results = [f"제목: {r.title}\nURL: {r.url}\n내용: {r.text[:1000]}" for r in response.results]
        return "\n\n---\n\n".join(results) if results else "검색 결과가 없습니다."
    except Exception as exc:
        return f"검색 중 오류가 발생했습니다: {str(exc)}"
