"""Room member identity: KakaoTalk user_id <-> display name.

Everything downstream (FTS chat log, Chroma user facts, memory opt-out,
feedback/usage/ops rows) is keyed by the sender's *display name*. Iris also
gives the stable KakaoTalk `user_id`, so we track (room_id, user_id) -> name
here and, when a member renames themselves, rewrite the stored name keys so
their history/memory/stats carry over instead of splitting into two people.
The same table resolves `@mention` user_ids and reply (답장) authors to names.
"""

from __future__ import annotations

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.dependencies import logger


def touch_member(room_id: int, user_id: int | None, name: str) -> str | None:
    """Upsert the member's current name. Returns the previous name when a
    rename was detected (caller migrates keyed data), else None."""
    if user_id is None or not name:
        return None
    now = now_kst().isoformat()
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM room_member WHERE room_id=? AND user_id=?", (room_id, user_id)
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO room_member (room_id, user_id, name, first_seen_at, last_seen_at) "
                    "VALUES (?,?,?,?,?)",
                    (room_id, user_id, name, now, now),
                )
                return None
            old = str(row["name"])
            if old == name:
                conn.execute(
                    "UPDATE room_member SET last_seen_at=? WHERE room_id=? AND user_id=?",
                    (now, room_id, user_id),
                )
                return None
            conn.execute(
                "UPDATE room_member SET name=?, last_seen_at=? WHERE room_id=? AND user_id=?",
                (name, now, room_id, user_id),
            )
            conn.execute(
                "INSERT INTO room_member_alias (room_id, user_id, old_name, new_name, changed_at) "
                "VALUES (?,?,?,?,?)",
                (room_id, user_id, old, name, now),
            )
        return old
    except Exception as e:
        logger.warning("touch_member failed: %s", e)
        return None


def name_for(room_id: int, user_id: int | None) -> str | None:
    if user_id is None:
        return None
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT name FROM room_member WHERE room_id=? AND user_id=?", (room_id, user_id)
            ).fetchone()
        return str(row["name"]) if row else None
    except Exception:
        return None


def names_for(room_id: int, user_ids) -> list[str]:
    out: list[str] = []
    for uid in user_ids:
        n = name_for(room_id, uid)
        if n and n not in out:
            out.append(n)
    return out


def migrate_sender_name(room_id: int, old: str, new: str) -> dict[str, int]:
    """Rewrite name-keyed rows from `old` to `new` for this room. Returns
    per-store affected counts. Each store is best-effort and independent."""
    counts: dict[str, int] = {}
    if not old or not new or old == new:
        return counts
    sql_targets = (
        ("chat_log_fts", "UPDATE chat_log_fts SET sender=? WHERE room_id=? AND sender=?", (new, str(room_id), old)),
        ("memory_optout", "UPDATE OR IGNORE memory_optout SET sender=? WHERE room_id=? AND sender=?", (new, room_id, old)),
        ("feedback_log", "UPDATE feedback_log SET sender=? WHERE room_id=? AND sender=?", (new, room_id, old)),
        ("llm_usage", "UPDATE llm_usage SET sender=? WHERE room_id=? AND sender=?", (new, room_id, old)),
        ("ops_event", "UPDATE ops_event SET sender=? WHERE room_id=? AND sender=?", (new, room_id, old)),
    )
    for table, sql, params in sql_targets:
        try:
            with get_conn() as conn:
                cur = conn.execute(sql, params)
                counts[table] = cur.rowcount or 0
        except Exception as e:
            logger.debug("rename migrate skipped %s: %s", table, e)

    # Chroma user facts: metadata.sender is the key.
    try:
        from app.dependencies import user_profile_store

        col = user_profile_store._collection
        got = col.get(where={"$and": [{"room_id": str(room_id)}, {"sender": old}]}, include=["metadatas"])
        ids = got.get("ids") or []
        if ids:
            metas = [dict(m or {}, sender=new) for m in got.get("metadatas") or []]
            col.update(ids=ids, metadatas=metas)
        counts["user_profile"] = len(ids)
    except Exception as e:
        logger.debug("rename migrate skipped user_profile: %s", e)

    logger.info("member renamed room_id=%s %r -> %r migrated=%s", room_id, old, new, counts)
    return counts
