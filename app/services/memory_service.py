"""User-facing memory management: view / delete / opt-out of stored facts.

Exposes the existing user_profile store through `!기억` commands and adds a
per-(room, sender) opt-out so the store node stops extracting facts for users
who don't want to be remembered.
"""

from __future__ import annotations

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import logger, user_profile_store

MEMORY_COMMANDS = {"!기억", "!기억삭제", "!기억끄기", "!기억켜기", "!기억도움"}


def _ensure_optout_table() -> None:
    with get_conn() as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS memory_optout ("
            "room_id INTEGER NOT NULL, sender TEXT NOT NULL, created_at TEXT NOT NULL, "
            "PRIMARY KEY (room_id, sender))"
        )


def is_opted_out(room_id: int, sender: str) -> bool:
    try:
        _ensure_optout_table()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT 1 FROM memory_optout WHERE room_id=? AND sender=?", (room_id, sender)
            ).fetchone()
        return row is not None
    except Exception:
        return False


def set_optout(room_id: int, sender: str, opted_out: bool) -> None:
    _ensure_optout_table()
    with get_conn() as conn:
        if opted_out:
            conn.execute(
                "INSERT OR IGNORE INTO memory_optout (room_id, sender, created_at) VALUES (?,?,?)",
                (room_id, sender, now_kst().isoformat()),
            )
        else:
            conn.execute(
                "DELETE FROM memory_optout WHERE room_id=? AND sender=?", (room_id, sender)
            )


def _my_facts(room_id: int, sender: str) -> list[str]:
    try:
        res = user_profile_store._collection.get(
            where={"$and": [{"room_id": str(room_id)}, {"sender": sender}]}, limit=50
        )
        return res.get("documents", []) if isinstance(res, dict) else []
    except Exception as e:
        logger.warning("memory fetch failed: %s", e)
        return []


def _delete_my_facts(room_id: int, sender: str, keyword: str | None) -> int:
    docs = _my_facts(room_id, sender)
    try:
        res = user_profile_store._collection.get(
            where={"$and": [{"room_id": str(room_id)}, {"sender": sender}]},
            limit=50,
            include=["documents"],
        )
        ids = res.get("ids", []) if isinstance(res, dict) else []
        contents = res.get("documents", []) if isinstance(res, dict) else []
        if keyword:
            ids = [i for i, c in zip(ids, contents) if keyword in c]
        if not ids:
            return 0
        user_profile_store._collection.delete(ids=ids)
        return len(ids)
    except Exception as e:
        logger.warning("memory delete failed: %s", e)
        return 0


def handle_memory_command(room_id: int, sender: str, name: str, args: list[str]) -> str | None:
    if name == "!기억도움":
        return (
            "[기억 명령]\n"
            "!기억 — 나에 대해 저장된 내용 보기\n"
            "!기억삭제 [키워드] — 해당 내용 삭제 (키워드 없으면 전부)\n"
            "!기억끄기 / !기억켜기 — 앞으로 나를 기억할지 여부"
        )
    if name == "!기억":
        facts = _my_facts(room_id, sender)
        opt = " (현재 기억 끔)" if is_opted_out(room_id, sender) else ""
        if not facts:
            return f"{sender}에 대해 저장된 기억이 없어.{opt}"
        return f"[{sender}에 대해 기억하는 것]{opt}\n" + "\n".join(f"- {f}" for f in facts)
    if name == "!기억삭제":
        keyword = " ".join(args).strip() or None
        n = _delete_my_facts(room_id, sender, keyword)
        if n == 0:
            return "삭제할 기억을 찾지 못했어."
        return f"기억 {n}건 삭제했어" + (f" ('{keyword}' 포함)." if keyword else " (전체).")
    if name == "!기억끄기":
        set_optout(room_id, sender, True)
        return "앞으로 너에 대한 새 기억은 저장하지 않을게. (!기억켜기 로 되돌릴 수 있어)"
    if name == "!기억켜기":
        set_optout(room_id, sender, False)
        return "다시 기억을 저장할게."
    return None
