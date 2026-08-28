"""Naver 지식백과(encyc) and 지역(local) search tools — reuse existing keys."""

import re

import httpx
from langchain_core.tools import tool

from app.config import settings

_UA_HEADERS = None


def _headers():
    return {
        "X-Naver-Client-Id": settings.naver_client_id,
        "X-Naver-Client-Secret": settings.naver_client_secret,
    }


def _strip(t: str | None) -> str:
    return re.sub(r"<[^>]+>", "", t or "")


@tool
async def naver_encyclopedia(query: str) -> str:
    """네이버 지식백과에서 용어/개념/사실을 조회한다. 백과사전적 사실 질문에
    (특히 한국 맥락) 웹검색 대신 우선 쓰면 정확도가 높다.

    query: 찾을 용어/개념 (예: "전세사기", "탄소중립", "면역항체")."""
    if not (settings.naver_client_id and settings.naver_client_secret):
        return "네이버 키가 없어 지식백과를 쓸 수 없어."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://openapi.naver.com/v1/search/encyc.json",
                params={"query": query, "display": 3},
                headers=_headers(),
            )
        if res.status_code != 200:
            return f"지식백과 조회 실패 (HTTP {res.status_code})."
        items = res.json().get("items", [])
        if not items:
            return f"'{query}' 지식백과 결과가 없어."
        blocks = [f"[{_strip(i.get('title'))}] {_strip(i.get('description'))}" for i in items]
        return "\n".join(blocks)
    except Exception as e:
        return f"지식백과 조회 중 오류: {e}"


@tool
async def naver_local_search(query: str) -> str:
    """네이버 지역 검색으로 맛집·상점·장소 정보(주소·분류·전화)를 조회한다.
    "근처 맛집", "○○역 카페", "△△ 위치" 같은 장소 질문에 써라.

    query: 지역/업종 포함 검색어 (예: "강남역 파스타 맛집", "홍대 카페")."""
    if not (settings.naver_client_id and settings.naver_client_secret):
        return "네이버 키가 없어 지역 검색을 쓸 수 없어."
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://openapi.naver.com/v1/search/local.json",
                params={"query": query, "display": 5, "sort": "random"},
                headers=_headers(),
            )
        if res.status_code != 200:
            return f"지역 검색 실패 (HTTP {res.status_code})."
        items = res.json().get("items", [])
        if not items:
            return f"'{query}' 지역 결과가 없어."
        lines = []
        for i in items:
            name = _strip(i.get("title"))
            cat = _strip(i.get("category"))
            addr = _strip(i.get("roadAddress") or i.get("address"))
            tel = i.get("telephone") or ""
            lines.append(f"- {name} ({cat}) | {addr}" + (f" | {tel}" if tel else ""))
        return "네이버 지역 검색 결과:\n" + "\n".join(lines)
    except Exception as e:
        return f"지역 검색 중 오류: {e}"
