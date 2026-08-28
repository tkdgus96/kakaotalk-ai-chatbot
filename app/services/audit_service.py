"""Lightweight audit trail for admin actions and safety blocks."""

from __future__ import annotations

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.dependencies import logger


def record_audit(action: str, room_id: int | None = None, sender: str | None = None,
                 detail: str | None = None) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO audit_log (action, room_id, sender, detail, created_at) "
                "VALUES (?,?,?,?,?)",
                (action, room_id, sender, detail, now_kst().isoformat()),
            )
    except Exception as e:
        logger.warning("audit record failed: %s", e)
