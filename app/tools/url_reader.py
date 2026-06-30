import re

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.config import settings
from app.dependencies import llm
from app.services.crawl_service import CrawlService

_crawl_service = CrawlService(settings)


def _first_url(text: str) -> str | None:
    match = re.search(r"https?://[^\s<>)]+", text or "")
    return match.group(0) if match else None


@tool
async def read_url(url: str) -> str:
    """URL 하나를 Crawl4AI로 읽고 Markdown 본문을 반환합니다."""
    target = _first_url(url)
    if not target:
        return "URL을 찾지 못했습니다. http:// 또는 https:// 로 시작하는 링크를 보내주세요."
    doc = await _crawl_service.crawl(target)
    if not doc.success:
        return f"URL 읽기 실패: {target}\n오류: {doc.error}"
    title = f"제목: {doc.title}\n" if doc.title else ""
    return f"{title}URL: {doc.url}\n\n{doc.markdown[:5000]}"


@tool
async def summarize_url(url: str) -> str:
    """URL 하나를 읽고 핵심 내용을 한국어로 요약합니다."""
    target = _first_url(url)
    if not target:
        return "URL을 찾지 못했습니다. http:// 또는 https:// 로 시작하는 링크를 보내주세요."
    doc = await _crawl_service.crawl(target)
    if not doc.success:
        return f"URL 요약 실패: {target}\n오류: {doc.error}"
    response = await llm.ainvoke(
        [
            SystemMessage(
                content="웹 페이지 Markdown을 근거로 핵심 요지, 중요한 수치/날짜, 사용자가 해야 할 일을 한국어로 요약해라."
            ),
            HumanMessage(content=f"URL: {doc.url}\n제목: {doc.title or ''}\n\n{doc.markdown[:12000]}"),
        ]
    )
    return str(response.content)
