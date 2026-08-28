"""Per-room topic dictionary, auto-extracted from raw chat logs.

Retrieval quality depends on expanding a query term into the words the room
actually uses ("보스" → "검마", "해방"...). Those expansions used to be
hardcoded for one room in app/graph.py; this module extracts them per room
with an LLM (weekly, like the room persona) and caches them in SQLite, so the
bot generalizes to any room without code changes.
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage

from app.boss.db import get_conn
from app.chat_log import get_recent_chat_log
from app.dependencies import fact_extractor_llm

TOPICS_PROMPT = """\
다음은 한 단톡방의 최근 대화 로그야. 이 방에서 반복적으로 등장하는 주제를 뽑아서,
나중에 "그 주제에 대한 질문"이 들어왔을 때 로그 검색에 쓸 연관 검색어 사전을 만들어줘.

요구사항:
- 주제(key)는 사용자가 질문에 쓸 법한 대표 단어 1개.
- terms는 로그에 실제로 등장한 표현만. 별칭/줄임말/관련 고유명사 포함, 주제당 2~6개.
- 주제는 5~12개. 일회성 잡담은 제외하고 반복 주제만.
- 사람 이름은 주제로 잡지 마.

대화 로그:
{conversation}

JSON으로만 답해. 다른 텍스트 금지.
{{"topics": [{{"key": "보스", "terms": ["검마", "루시드", "해방"]}}]}}
"""

STALE_THRESHOLD_HOURS = 24 * 7  # weekly refresh, same cadence as room persona
MIN_MESSAGES_FOR_COMPUTE = 30

# in-memory cache: room_id -> (computed_at iso, expansions)
_cache: dict[int, tuple[str, dict[str, list[str]]]] = {}


def _now_iso() -> str:
    return datetime.now().isoformat()


def _row(room_id: int) -> dict | None:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT room_id, topics_json, sample_size, computed_at FROM room_topics WHERE room_id=?",
                (room_id,),
            ).fetchone()
        return dict(row) if row else None
    except Exception:
        return None


def _upsert(room_id: int, topics_json: str, sample_size: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO room_topics (room_id, topics_json, sample_size, computed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                topics_json=excluded.topics_json,
                sample_size=excluded.sample_size,
                computed_at=excluded.computed_at
            """,
            (room_id, topics_json, sample_size, _now_iso()),
        )


def _parse_expansions(topics_json: str) -> dict[str, list[str]]:
    try:
        data = json.loads(topics_json)
    except Exception:
        return {}
    expansions: dict[str, list[str]] = {}
    for topic in data.get("topics", []):
        key = str(topic.get("key", "")).strip()
        terms = [str(t).strip() for t in topic.get("terms", []) if str(t).strip()]
        if not key or not terms:
            continue
        if key not in terms:
            terms = [key] + terms
        expansions[key] = terms[:8]
    return expansions


def _is_stale(computed_at: str) -> bool:
    try:
        computed = datetime.fromisoformat(computed_at)
    except Exception:
        return True
    return datetime.now() - computed > timedelta(hours=STALE_THRESHOLD_HOURS)


def get_room_topic_expansions(room_id: int) -> dict[str, list[str]]:
    """Return the room's cached topic expansions (sync, no LLM call).
    Empty dict when the room has no computed topics yet."""
    cached = _cache.get(room_id)
    if cached:
        return cached[1]
    row = _row(room_id)
    if not row:
        return {}
    expansions = _parse_expansions(row["topics_json"])
    _cache[room_id] = (row["computed_at"], expansions)
    return expansions


async def ensure_room_topics(room_id: int) -> dict[str, list[str]]:
    """Return the room's topic expansions, recomputing weekly from raw logs."""
    cached = _cache.get(room_id)
    if cached and not _is_stale(cached[0]):
        return cached[1]

    row = await asyncio.to_thread(_row, room_id)
    if row and not _is_stale(row["computed_at"]):
        expansions = _parse_expansions(row["topics_json"])
        _cache[room_id] = (row["computed_at"], expansions)
        return expansions

    messages = await asyncio.to_thread(get_recent_chat_log, room_id, 300)
    if len(messages) < MIN_MESSAGES_FOR_COMPUTE:
        return get_room_topic_expansions(room_id)

    try:
        response = await fact_extractor_llm.ainvoke(
            [HumanMessage(content=TOPICS_PROMPT.format(conversation="\n".join(messages)))]
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        raw = raw.strip().lstrip("`").lstrip("json").strip()
        expansions = _parse_expansions(raw)
    except Exception:
        return get_room_topic_expansions(room_id)

    if not expansions:
        return get_room_topic_expansions(room_id)

    topics_json = json.dumps(
        {"topics": [{"key": k, "terms": v} for k, v in expansions.items()]},
        ensure_ascii=False,
    )
    await asyncio.to_thread(_upsert, room_id, topics_json, len(messages))
    _cache[room_id] = (_now_iso(), expansions)
    return expansions
