import re
from datetime import timedelta

from langchain_core.tools import tool

from app.boss.db import get_conn, init_schema
from app.boss.utils.week import now_kst
from app.config import settings


def _parse_when(text: str):
    raw = (text or "").strip()
    now = now_kst()
    if "내일" in raw:
        base = now + timedelta(days=1)
    elif "모레" in raw:
        base = now + timedelta(days=2)
    elif "오늘" in raw:
        base = now
    else:
        base = now

    match = re.search(r"(\d{1,2})\s*시(?:\s*(\d{1,2})\s*분)?", raw)
    if match:
        hour = int(match.group(1))
        minute = int(match.group(2) or 0)
        if "오후" in raw and hour < 12:
            hour += 12
        if "오전" in raw and hour == 12:
            hour = 0
        return base.replace(hour=hour, minute=minute, second=0, microsecond=0)

    minutes = re.search(r"(\d+)\s*분\s*(뒤|후)", raw)
    if minutes:
        return now + timedelta(minutes=int(minutes.group(1)))
    hours = re.search(r"(\d+)\s*시간\s*(뒤|후)", raw)
    if hours:
        return now + timedelta(hours=int(hours.group(1)))
    return None


@tool
async def create_reminder(message: str, when: str, room_id: int | None = None, room_name: str = "reminder") -> str:
    """일반 리마인더를 생성합니다. when은 '내일 오후 3시', '30분 뒤' 같은 표현을 지원합니다."""
    scheduled_at = _parse_when(when)
    if scheduled_at is None:
        return "리마인더 시간을 해석하지 못했습니다. 예: 내일 오후 3시, 30분 뒤"
    target_room_id = room_id or settings.playground_room_id
    now = now_kst().isoformat()
    init_schema()
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bot_outbox
            (room_id, room_name, message, status, scheduled_at, created_at, updated_at)
            VALUES (?, ?, ?, 'PENDING', ?, ?, ?)
            """,
            (target_room_id, room_name, message, scheduled_at.isoformat(), now, now),
        )
    return f"리마인더를 등록했습니다. 시간: {scheduled_at:%Y-%m-%d %H:%M KST}, 내용: {message}"


@tool
async def list_reminders(room_id: int | None = None, limit: int = 10) -> str:
    """대기 중인 일반/봇 리마인더 목록을 조회합니다."""
    target_room_id = room_id or settings.playground_room_id
    init_schema()
    with get_conn() as conn:
        rows = conn.execute(
            """
            SELECT id, message, scheduled_at
            FROM bot_outbox
            WHERE room_id=? AND status='PENDING'
            ORDER BY scheduled_at ASC
            LIMIT ?
            """,
            (target_room_id, limit),
        ).fetchall()
    if not rows:
        return "대기 중인 리마인더가 없습니다."
    return "\n".join(f"- #{r['id']} {r['scheduled_at']}: {r['message']}" for r in rows)


@tool
async def cancel_reminder(reminder_id: int) -> str:
    """리마인더 ID로 대기 중인 리마인더를 취소합니다."""
    now = now_kst().isoformat()
    init_schema()
    with get_conn() as conn:
        cur = conn.execute(
            "UPDATE bot_outbox SET status='FAILED', updated_at=? WHERE id=? AND status='PENDING'",
            (now, reminder_id),
        )
    if cur.rowcount == 0:
        return "취소할 대기 리마인더를 찾지 못했습니다."
    return f"리마인더 #{reminder_id}를 취소했습니다."
