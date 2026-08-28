"""Wikipedia lookup — authoritative facts for encyclopedic questions.

Uses the MediaWiki API (Korean first, English fallback): search for the best
matching title, then fetch its intro extract. One or two HTTP calls, no key.
"""

import httpx
from langchain_core.tools import tool

_UA = {"User-Agent": "onban-bot/1.0 (kakao chatbot)"}


async def _lookup(lang: str, query: str) -> str | None:
    base = f"https://{lang}.wikipedia.org/w/api.php"
    try:
        async with httpx.AsyncClient(timeout=10.0, headers=_UA) as client:
            sr = await client.get(base, params={
                "action": "query", "list": "search", "srsearch": query,
                "srlimit": 1, "format": "json",
            })
            hits = sr.json().get("query", {}).get("search", []) if sr.status_code == 200 else []
            if not hits:
                return None
            title = hits[0]["title"]
            er = await client.get(base, params={
                "action": "query", "prop": "extracts", "exintro": 1, "explaintext": 1,
                "redirects": 1, "titles": title, "format": "json",
            })
            pages = er.json().get("query", {}).get("pages", {}) if er.status_code == 200 else {}
        for page in pages.values():
            extract = (page.get("extract") or "").strip()
            if extract:
                return f"[위키백과: {title}]\n{extract[:1500]}"
        return None
    except Exception:
        return None


@tool
async def wikipedia_lookup(query: str) -> str:
    """위키백과에서 사실/지식을 조회한다. 인물·역사·과학·개념·지명 등 백과사전적 사실을
    물으면 웹검색보다 이 도구를 우선 써서 정확도를 높여라. 한국어에 없으면 영어로 보강한다.

    query: 찾을 주제/표제어 (예: "광합성", "이순신", "블랙홀")."""
    result = await _lookup("ko", query)
    if not result:
        result = await _lookup("en", query)
    return result or f"'{query}'에 대한 위키백과 문서를 찾지 못했어."
