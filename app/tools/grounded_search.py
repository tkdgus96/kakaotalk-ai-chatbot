"""Grounded web answer via the OpenAI Responses API `web_search` tool.

OpenAI runs the search server-side and returns an answer with inline source
citations — Google-grade recency + citations, far more accurate than scraping
SEO'd preview articles. Used as the primary implementation behind web_search.
"""

from __future__ import annotations

from openai import AsyncOpenAI

from app.config import settings
from app.dependencies import logger


async def grounded_web_answer(query: str) -> str | None:
    """Return a cited answer string, or None if unavailable/failed."""
    if not (settings.enable_grounded_search and settings.openai_api_key):
        return None
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        resp = await client.responses.create(
            model=settings.web_search_model,
            tools=[{"type": "web_search"}],
            input=(
                "다음 질문에 웹 검색으로 사실을 확인해 정확히 답해. 기사 작성 시점과 현재 시각을 "
                "대조해 옛 정보를 최신처럼 말하지 말고, 핵심을 간결히. 출처 링크를 본문에 포함해.\n\n"
                f"질문: {query}"
            ),
            max_output_tokens=700,
        )
        text = (getattr(resp, "output_text", None) or "").strip()
        if not text:
            return None
        try:
            from app.services.usage_service import record_usage

            usage = getattr(resp, "usage", None)
            record_usage(
                "web_search", settings.web_search_model,
                int(getattr(usage, "input_tokens", 0) or 0),
                int(getattr(usage, "output_tokens", 0) or 0),
            )
        except Exception:
            pass
        return text
    except Exception as e:
        logger.warning("grounded web search failed: %s", e)
        return None
