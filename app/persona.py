"""Group persona evolution.

Periodically reads recent messages of a room and uses an LLM to extract the
group's communication style. The result is cached in SQLite and injected into
the chat system prompt so the bot's tone matches the room over time.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage

from app.boss.db import get_conn
from app.dependencies import fact_extractor_llm, vectorstore

PERSONA_PROMPT = """\
다음은 한 단톡방에서 최근 오간 대화 샘플이야. 이 방의 커뮤니케이션 스타일과
멤버들의 관계/분위기를 분석해서, AI 챗봇이 이 방에 자연스럽게 어울리도록 도와줄
간결한 페르소나 가이드를 만들어줘.

요구사항:
- 3~5줄. 너무 길게 쓰지 마.
- 톤(친근/격식/장난스러움), 자주 쓰는 어휘/표현, 주요 관심사/주제, 멤버 간 관계 톤
- 봇이 어떻게 말하면 이 방에 어울릴지 마지막 한 줄로 권장
- 정치/종교/사적인 민감 내용은 페르소나에 포함하지 마

대화 샘플:
{conversation}

페르소나 가이드:"""

STALE_THRESHOLD_HOURS = 24 * 7  # weekly refresh
MIN_SAMPLES_FOR_COMPUTE = 5


def _now_iso() -> str:
    return datetime.now().isoformat()


def get_room_persona(room_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT room_id, persona_text, sample_size, computed_at FROM room_persona WHERE room_id=?",
            (room_id,),
        ).fetchone()
        return dict(row) if row else None


def upsert_room_persona(room_id: int, persona_text: str, sample_size: int) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO room_persona (room_id, persona_text, sample_size, computed_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(room_id) DO UPDATE SET
                persona_text=excluded.persona_text,
                sample_size=excluded.sample_size,
                computed_at=excluded.computed_at
            """,
            (room_id, persona_text, sample_size, _now_iso()),
        )


def _is_stale(persona: dict) -> bool:
    try:
        computed = datetime.fromisoformat(persona["computed_at"])
    except Exception:
        return True
    return datetime.now() - computed > timedelta(hours=STALE_THRESHOLD_HOURS)


async def _compute_persona(room_id: int) -> tuple[str, int] | None:
    docs = await asyncio.to_thread(
        lambda: vectorstore._collection.get(
            where={"room_id": str(room_id)},
            limit=150,
        )
    )
    contents = docs.get("documents", []) if isinstance(docs, dict) else []
    if len(contents) < MIN_SAMPLES_FOR_COMPUTE:
        return None

    sample = contents[-100:]
    conversation = "\n".join(sample)
    try:
        response = await fact_extractor_llm.ainvoke(
            [HumanMessage(content=PERSONA_PROMPT.format(conversation=conversation))]
        )
        text = response.content if isinstance(response.content, str) else str(response.content)
        return text.strip(), len(sample)
    except Exception:
        return None


async def ensure_persona(room_id: int) -> str:
    """Return the room's persona text, computing/refreshing if missing or stale."""
    persona = get_room_persona(room_id)
    if persona and not _is_stale(persona):
        return persona["persona_text"]

    computed = await _compute_persona(room_id)
    if computed is None:
        return persona["persona_text"] if persona else ""

    text, sample_size = computed
    upsert_room_persona(room_id, text, sample_size)
    return text
