"""MapleStory(본편) meso price lookup via gamebit.co.kr.

The page is server-rendered: each server is a `.ranking-item` div whose
`onclick="changeServer('<sid>','<en>','<ko>',<price>)"` and `data-rate=".."`
expose everything we need. One regex extracts all 14 servers.

Resilience: in-memory cache with a 30-minute fresh TTL. If a refresh fails
and a cached snapshot is still within 6 hours, we return it with `stale=true`
so the LLM can disclose the staleness. Past that, an explicit failure JSON.

The tool returns raw structured JSON; the LLM is expected to summarize it
naturally per the system prompt's KakaoTalk output rules.
"""

from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime, timedelta

import httpx
from langchain_core.tools import tool

_URL = "https://gamebit.co.kr/maple"
_HEADERS = {"User-Agent": "Mozilla/5.0 (kakao-talk-ai-bot)", "Accept-Language": "ko"}
_FRESH_TTL = timedelta(minutes=30)
_STALE_MAX = timedelta(hours=6)

_ITEM_RE = re.compile(
    r"changeServer\('(\d+)',\s*'([a-z_]+)',\s*'([^']+)',\s*([\d.]+)\)"
    r".*?data-rate=\"(-?[\d.]+)\"",
    re.DOTALL,
)
_LABEL_RE = re.compile(r"(\d{1,2}시\s*기준)")

CANONICAL = [
    "노바", "오로라", "크로아", "루나", "베라", "레드", "아케인",
    "이노시스", "유니온", "엘리시움", "스카니아", "제니스", "헬리오스", "에오스",
]

_ALIASES: dict[str, str] = {
    # Korean abbreviations users commonly type
    "스칸": "스카니아", "스캐니아": "스카니아",
    "제니": "제니스", "유니": "유니온", "엘리": "엘리시움",
    "이노": "이노시스", "헬": "헬리오스", "오로": "오로라",
    # English codes (gamebit's internal ids)
    "scania": "스카니아", "bera": "베라", "luna": "루나", "zenith": "제니스",
    "croa": "크로아", "elysium": "엘리시움", "inosys": "이노시스",
    "union": "유니온", "arcane": "아케인", "nova": "노바", "aurora": "오로라",
    "red": "레드", "helios": "헬리오스", "eos": "에오스",
}

_CACHE: dict = {"snap": None, "at": None}
_FETCH_LOCK = asyncio.Lock()


def _normalize_server(q: str | None) -> str | None:
    """Return canonical Korean server name, or None if user asked for 전체."""
    if not q:
        return None
    s = q.strip()
    if s.lower() in ("전체", "all", ""):
        return None
    if s in CANONICAL:
        return s
    low = s.lower()
    if low in _ALIASES:
        return _ALIASES[low]
    return s  # unknown; caller surfaces a "not found" payload


async def _fetch_gamebit() -> dict:
    async with httpx.AsyncClient(timeout=10, headers=_HEADERS, follow_redirects=True) as c:
        r = await c.get(_URL)
    r.raise_for_status()
    html = r.text
    rows = _ITEM_RE.findall(html)
    if not rows:
        raise RuntimeError("게임비트 HTML에서 서버 목록을 추출하지 못함 (구조 변경 가능성)")
    servers = [
        {
            "name_ko": ko,
            "name_en": en,
            "server_id": sid,
            "price_won_per_eok": round(float(price)),
            "change_percent": float(rate),
        }
        for sid, en, ko, price, rate in rows
    ]
    label_m = _LABEL_RE.search(html)
    return {
        "source": "gamebit",
        "snapshot_label": label_m.group(1) if label_m else None,
        "fetched_at": datetime.now().isoformat(timespec="seconds"),
        "servers": servers,
    }


async def _resolve_snapshot(now: datetime) -> tuple[dict, bool, timedelta]:
    """Return (snapshot, is_stale, age). Single-flight via _FETCH_LOCK so
    concurrent calls don't pile on the upstream."""
    if _CACHE["snap"] and now - _CACHE["at"] < _FRESH_TTL:
        return _CACHE["snap"], False, timedelta(0)
    async with _FETCH_LOCK:
        # re-check after acquiring the lock
        if _CACHE["snap"] and now - _CACHE["at"] < _FRESH_TTL:
            return _CACHE["snap"], False, timedelta(0)
        try:
            snap = await _fetch_gamebit()
            _CACHE["snap"], _CACHE["at"] = snap, now
            return snap, False, timedelta(0)
        except Exception:
            if _CACHE["snap"] and now - _CACHE["at"] < _STALE_MAX:
                return _CACHE["snap"], True, now - _CACHE["at"]
            raise


@tool
async def get_maple_meso_price(server: str = "전체") -> str:
    """메이플스토리(본편) 서버별 메소 시세를 1억당 원 기준으로 조회한다.
    server: 서버명(스카니아/베라/루나/제니스/크로아/엘리시움/이노시스/유니온/
            아케인/노바/오로라/레드/헬리오스/에오스) 또는 "전체".
    "메소 시세", "1억당 얼마", "메소 환율", "서버별 메소" 같은 질문에 사용.
    결과 JSON은 출처(게임비트)와 stale 여부를 포함. 답변엔 자연어로 요약하고
    "게임비트 기준" 한 줄을 덧붙일 것."""
    now = datetime.now()
    target = _normalize_server(server)

    try:
        snap, stale, age = await _resolve_snapshot(now)
    except Exception as exc:
        return json.dumps(
            {"error": "메소 시세 조회 실패", "detail": str(exc)[:200], "stale_cache": False},
            ensure_ascii=False,
        )

    payload: dict = {
        "source": snap["source"],
        "snapshot_label": snap.get("snapshot_label"),
        "fetched_at": snap["fetched_at"],
        "stale": stale,
    }
    if stale:
        payload["stale_age_hours"] = round(age.total_seconds() / 3600, 1)

    if target is None:
        payload["servers"] = snap["servers"]
    else:
        match = next((s for s in snap["servers"] if s["name_ko"] == target), None)
        if match:
            payload["server_query"] = server
            payload["server"] = match
        else:
            payload["server_query"] = server
            payload["server"] = None
            payload["available_servers"] = [s["name_ko"] for s in snap["servers"]]
    return json.dumps(payload, ensure_ascii=False, indent=2)
