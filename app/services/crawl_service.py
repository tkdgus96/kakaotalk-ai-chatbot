import asyncio
import logging
import re
import time
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel, Field

from app.config import Settings

logger = logging.getLogger(__name__)


class CrawlDocument(BaseModel):
    url: str
    title: str | None = None
    markdown: str = ""
    chunks: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)
    success: bool
    error: str | None = None
    from_cache: bool = False


class CrawlCache:
    async def get(self, key: str) -> CrawlDocument | None:
        raise NotImplementedError

    async def set(self, key: str, value: CrawlDocument, ttl_seconds: int) -> None:
        raise NotImplementedError


class InMemoryCrawlCache(CrawlCache):
    def __init__(self):
        self._items: dict[str, tuple[float, CrawlDocument]] = {}

    async def get(self, key: str) -> CrawlDocument | None:
        item = self._items.get(key)
        if item is None:
            return None
        expires_at, value = item
        if expires_at <= time.monotonic():
            self._items.pop(key, None)
            return None
        return value.copy(update={"from_cache": True})

    async def set(self, key: str, value: CrawlDocument, ttl_seconds: int) -> None:
        if ttl_seconds <= 0 or not value.success:
            return
        self._items[key] = (time.monotonic() + ttl_seconds, value.copy(update={"from_cache": False}))


class CrawlService:
    def __init__(
        self,
        settings: Settings,
        cache: CrawlCache | None = None,
        crawler_factory: Callable[[], Any] | None = None,
        retries: int = 2,
    ):
        self.settings = settings
        self.cache = cache or InMemoryCrawlCache()
        self._crawler_factory = crawler_factory
        self.retries = retries

    async def crawl(self, url: str) -> CrawlDocument:
        cached = await self.cache.get(url)
        if cached is not None:
            logger.info("crawl_cache_hit url=%s", url)
            return cached

        if not self.settings.enable_crawl4ai:
            return CrawlDocument(
                url=url,
                success=False,
                error="Crawl4AI is disabled. Set ENABLE_CRAWL4AI=true to enable crawling.",
            )

        last_error: str | None = None
        for attempt in range(1, self.retries + 2):
            try:
                logger.info("crawl_start url=%s attempt=%s", url, attempt)
                doc = await asyncio.wait_for(
                    asyncio.to_thread(lambda: asyncio.run(self._crawl_once(url))),
                    timeout=self.settings.crawl_timeout,
                )
                if doc.success:
                    await self.cache.set(url, doc, self.settings.cache_ttl)
                logger.info("crawl_done url=%s success=%s attempt=%s", url, doc.success, attempt)
                return doc
            except Exception as exc:
                last_error = str(exc)
                logger.warning("crawl_failed url=%s attempt=%s error=%s", url, attempt, last_error)
                if attempt <= self.retries:
                    await asyncio.sleep(min(0.25 * attempt, 1.0))

        return CrawlDocument(url=url, success=False, error=last_error or "Unknown crawl failure")

    async def crawl_many(self, urls: list[str]) -> list[CrawlDocument]:
        limited_urls = [url for url in urls if url][: self.settings.max_crawl_urls]
        docs = await asyncio.gather(*(self.crawl(url) for url in limited_urls), return_exceptions=True)
        out: list[CrawlDocument] = []
        for url, doc in zip(limited_urls, docs, strict=False):
            if isinstance(doc, Exception):
                logger.warning("crawl_many_item_failed url=%s error=%s", url, doc)
                out.append(CrawlDocument(url=url, success=False, error=str(doc)))
            else:
                out.append(doc)
        return out

    async def _crawl_once(self, url: str) -> CrawlDocument:
        try:
            from crawl4ai import AsyncWebCrawler, BrowserConfig, CacheMode, CrawlerRunConfig
        except ImportError as exc:
            return CrawlDocument(
                url=url,
                success=False,
                error=f"Crawl4AI is not installed: {exc}",
            )

        browser_config = self._build_browser_config(BrowserConfig)
        run_config = self._build_run_config(CrawlerRunConfig, CacheMode)
        crawler_factory = self._crawler_factory or (lambda: AsyncWebCrawler(config=browser_config))

        async with crawler_factory() as crawler:
            result = await crawler.arun(url=url, config=run_config)

        success = bool(getattr(result, "success", True))
        metadata = getattr(result, "metadata", None) or {}
        title = metadata.get("title") if isinstance(metadata, dict) else None
        markdown = self._extract_markdown(result)
        cleaned = clean_markdown(markdown)

        if not success:
            return CrawlDocument(
                url=url,
                title=title,
                markdown=cleaned,
                metadata=metadata if isinstance(metadata, dict) else {},
                success=False,
                error=getattr(result, "error_message", None) or "Crawl4AI returned unsuccessful result",
            )

        return CrawlDocument(
            url=url,
            title=title,
            markdown=cleaned,
            chunks=chunk_markdown(cleaned),
            metadata=metadata if isinstance(metadata, dict) else {},
            success=True,
        )

    def _build_browser_config(self, browser_config_cls):
        kwargs = {"headless": True}
        try:
            return browser_config_cls(java_script_enabled=self.settings.enable_js, **kwargs)
        except TypeError:
            return browser_config_cls(**kwargs)

    def _build_run_config(self, run_config_cls, cache_mode_cls):
        kwargs = {
            "cache_mode": cache_mode_cls.BYPASS,
            "page_timeout": int(self.settings.crawl_timeout * 1000),
        }
        try:
            return run_config_cls(**kwargs)
        except TypeError:
            try:
                return run_config_cls()
            except TypeError:
                return None

    def _extract_markdown(self, result) -> str:
        markdown = getattr(result, "markdown", "") or ""
        if isinstance(markdown, str):
            return markdown
        for attr in ("raw_markdown", "fit_markdown", "markdown"):
            value = getattr(markdown, attr, None)
            if isinstance(value, str):
                return value
        return str(markdown)


def clean_markdown(markdown: str) -> str:
    text = re.sub(r"\n{3,}", "\n\n", markdown or "")
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def chunk_markdown(markdown: str, chunk_size: int = 2500, overlap: int = 250) -> list[str]:
    if not markdown:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(markdown):
        end = min(start + chunk_size, len(markdown))
        chunks.append(markdown[start:end].strip())
        if end == len(markdown):
            break
        start = max(end - overlap, start + 1)
    return [chunk for chunk in chunks if chunk]
