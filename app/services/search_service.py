import asyncio
import logging
import re
from typing import Literal

import httpx
from pydantic import BaseModel, Field
from tavily import AsyncTavilyClient

from app.config import Settings

logger = logging.getLogger(__name__)


class SearchResult(BaseModel):
    provider: Literal["naver", "tavily"]
    source_type: str = "web"
    title: str
    url: str
    snippet: str = ""
    published_at: str | None = None
    metadata: dict = Field(default_factory=dict)


def strip_html(text: str | None) -> str:
    return re.sub(r"<[^>]+>", "", text or "")


class SearchService:
    def __init__(
        self,
        settings: Settings,
        tavily_client: AsyncTavilyClient | None = None,
        http_client_factory=httpx.AsyncClient,
    ):
        self.settings = settings
        self._tavily = tavily_client or (
            AsyncTavilyClient(api_key=settings.tavily_api_key) if settings.tavily_api_key else None
        )
        self._http_client_factory = http_client_factory

    def is_web_available(self) -> bool:
        return self._tavily is not None

    async def search_naver(self, query: str, limit: int = 5) -> list[SearchResult]:
        if not (self.settings.naver_client_id and self.settings.naver_client_secret):
            logger.info("naver_search_unavailable missing_credentials=true")
            return []

        headers = {
            "X-Naver-Client-Id": self.settings.naver_client_id,
            "X-Naver-Client-Secret": self.settings.naver_client_secret,
        }
        async with self._http_client_factory(timeout=10.0) as client:
            news_resp, web_resp = await self._gather_naver(client, headers, query, limit)

        results: list[SearchResult] = []
        for source_type, resp in (("news", news_resp), ("web", web_resp)):
            if resp.status_code != 200:
                logger.warning(
                    "naver_search_failed source_type=%s status_code=%s",
                    source_type,
                    resp.status_code,
                )
                continue
            for item in resp.json().get("items", [])[:limit]:
                url = item.get("originallink") or item.get("link") or ""
                if not url:
                    continue
                results.append(
                    SearchResult(
                        provider="naver",
                        source_type=source_type,
                        title=strip_html(item.get("title")),
                        url=url,
                        snippet=strip_html(item.get("description")),
                        published_at=item.get("pubDate") or None,
                    )
                )
        return results[:limit]

    async def search_web(self, query: str, limit: int = 5) -> list[SearchResult]:
        if self._tavily is None:
            logger.info("tavily_search_unavailable missing_credentials=true")
            return []

        result = await self._tavily.search(
            query=query,
            search_depth="advanced",
            max_results=limit,
            include_answer=False,
        )
        results = []
        for item in result.get("results", [])[:limit]:
            url = item.get("url") or ""
            if not url:
                continue
            results.append(
                SearchResult(
                    provider="tavily",
                    title=item.get("title") or "",
                    url=url,
                    snippet=(item.get("content") or "")[:1200],
                    metadata={"score": item.get("score")},
                )
            )
        return results

    async def _gather_naver(
        self,
        client: httpx.AsyncClient,
        headers: dict,
        query: str,
        limit: int,
    ):
        news = client.get(
            "https://openapi.naver.com/v1/search/news.json",
            params={"query": query, "display": limit, "sort": "date"},
            headers=headers,
        )
        web = client.get(
            "https://openapi.naver.com/v1/search/webkr.json",
            params={"query": query, "display": limit},
            headers=headers,
        )
        return await asyncio.gather(news, web, return_exceptions=False)
