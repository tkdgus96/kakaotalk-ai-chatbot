"""Capture user corrections as an accuracy signal.

When a user replies right after a bot answer with "틀렸어 / 아니야 / 이미 나왔는데"
etc., log (correction, prev question, prev answer) to feedback_log. This turns
real-world mistakes into reviewable data (later promoted to eval cases).
"""

from __future__ import annotations

import re

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import logger

_CORRECTION_MARKERS = (
    "틀렸", "틀림", "틀린", "아니야", "아닌데", "아니잖", "잘못", "거짓",
    "이미 나왔", "이미나왔", "옛날", "구식", "그거 아니", "그게 아니", "말이 안",
    "부정확", "오답", "예전 정보", "낡은",
)


def is_correction(msg: str) -> bool:
    text = re.sub(r"^\[[^\]]+\]:\s*", "", (msg or "").strip())
    return any(m in text for m in _CORRECTION_MARKERS)


def record_correction(room_id: int, sender: str, correction: str,
                      prev_question: str | None, prev_answer: str | None) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO feedback_log (room_id, sender, correction, prev_question, "
                "prev_answer, created_at) VALUES (?,?,?,?,?,?)",
                (room_id, sender, correction[:500],
                 (prev_question or "")[:500], (prev_answer or "")[:1000],
                 now_kst().isoformat()),
            )
        logger.info("feedback logged room_id=%s sender=%s", room_id, sender)
    except Exception as e:
        logger.warning("feedback record failed: %s", e)


def recent_feedback(days: int = 1, limit: int = 10) -> list[dict]:
    from datetime import timedelta

    cutoff = (now_kst() - timedelta(days=days)).isoformat()
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sender, correction, prev_question, created_at FROM feedback_log "
                "WHERE created_at >= ? ORDER BY id DESC LIMIT ?",
                (cutoff, limit),
            ).fetchall()
        return [dict(r) for r in rows]
    except Exception:
        return []


def maybe_log_correction(room_id: int, sender: str, msg: str) -> bool:
    """If msg is a correction that directly follows a bot answer, log it.
    Returns True if logged. Uses SQLChatMessageHistory to fetch the prior
    bot answer + question (only present for command turns, which is what we
    care about — factual answers)."""
    if not is_correction(msg):
        return False
    try:
        from langchain_community.chat_message_histories import SQLChatMessageHistory

        history = SQLChatMessageHistory(
            session_id=str(room_id), connection_string=settings.db_connection_string
        )
        msgs = history.messages
    except Exception:
        msgs = []
    if not msgs or getattr(msgs[-1], "type", None) != "ai":
        return False  # correction not right after a bot answer -> likely unrelated
    prev_answer = msgs[-1].content if isinstance(msgs[-1].content, str) else str(msgs[-1].content)
    prev_question = None
    for m in reversed(msgs[:-1]):
        if getattr(m, "type", None) == "human":
            prev_question = m.content if isinstance(m.content, str) else str(m.content)
            break
    record_correction(room_id, sender, msg, prev_question, prev_answer)
    return True
