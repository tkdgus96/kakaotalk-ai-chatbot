"""Iris (redroid) bridge adapter.

Iris (https://github.com/dolidolih/Iris) observes the KakaoTalk DB on a rooted
Android (redroid container) and POSTs new messages to `web_server_endpoint`
(our `POST /iris`). Replies and outbox deliveries go back through Iris's
`POST /reply` HTTP API — no phone, no notification listener.

Payload from Iris:
    {"msg": "...", "room": "<room name>", "sender": "<name>",
     "json": {"_id": ..., "chat_id": ..., "user_id": ..., "message": ..., ...}}

Legacy continuity: everything in this backend (Chroma metadata, FTS logs,
persona, allowed_rooms, boss tables) is keyed on the room_id integers the
MessengerBotR bridge used. Iris gives KakaoTalk-internal chat_ids instead, so
`iris_room_map` translates chat_id <-> legacy room_id. Unknown chat_ids get an
identity mapping (room_id = chat_id), which is fine for rooms with no history;
legacy rooms must be seeded via the IRIS_ROOM_MAP env ("chat_id:room_id,...").
"""

from __future__ import annotations

import asyncio
import json as jsonlib
from dataclasses import dataclass

import httpx

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import logger
from app.models import KakaoMsg


@dataclass(frozen=True)
class IrisMessage:
    chat_id: int
    room_name: str
    sender: str
    msg: str


def parse_iris_webhook(payload: object) -> IrisMessage | None:
    if not isinstance(payload, dict):
        return None
    raw = payload.get("json") or {}
    if isinstance(raw, str):
        try:
            raw = jsonlib.loads(raw)
        except Exception:
            raw = {}
    if not isinstance(raw, dict):
        raw = {}

    chat_id_raw = raw.get("chat_id") if raw.get("chat_id") is not None else payload.get("chat_id")
    try:
        chat_id = int(str(chat_id_raw))
    except (TypeError, ValueError):
        return None

    msg = str(payload.get("msg") or raw.get("message") or "").strip()
    sender = str(payload.get("sender") or "").strip()
    room_name = str(payload.get("room") or "").strip()
    if not msg or not sender:
        return None
    return IrisMessage(chat_id=chat_id, room_name=room_name, sender=sender, msg=msg)


def is_self_message(message: IrisMessage) -> bool:
    """Iris sees the bot account's own sent rows in the DB — skip the echo."""
    return message.sender in settings.iris_self_names


def is_command_message(msg: str) -> bool:
    """Mirror of the phone bridge's is_command rule: the message addresses the
    bot (trigger prefix) and should run the LLM pipeline instead of buffering.
    Keep IRIS_BOT_TRIGGERS in sync with what the room actually uses."""
    text = msg.strip()
    return any(text.startswith(trigger) for trigger in settings.iris_bot_triggers)


def resolve_room(chat_id: int, room_name: str) -> tuple[int, str]:
    now = now_kst().isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT room_id, room_name FROM iris_room_map WHERE chat_id=?", (chat_id,)
        ).fetchone()
        if row:
            if room_name and room_name != row["room_name"]:
                conn.execute(
                    "UPDATE iris_room_map SET room_name=?, updated_at=? WHERE chat_id=?",
                    (room_name, now, chat_id),
                )
            return int(row["room_id"]), room_name or str(row["room_name"])
        conn.execute(
            "INSERT INTO iris_room_map (chat_id, room_id, room_name, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (chat_id, chat_id, room_name, now, now),
        )
    return chat_id, room_name


def chat_id_for_room(room_id: int) -> int:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT chat_id FROM iris_room_map WHERE room_id=?", (room_id,)
            ).fetchone()
        return int(row["chat_id"]) if row else room_id
    except Exception:
        return room_id


def seed_room_map_from_env() -> None:
    """Apply IRIS_ROOM_MAP="chat_id:room_id,..." — legacy room continuity."""
    now = now_kst().isoformat()
    for pair in settings.iris_room_map_seed.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        chat_str, rid_str = pair.split(":", 1)
        try:
            chat_id, room_id = int(chat_str.strip()), int(rid_str.strip())
        except ValueError:
            continue
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO iris_room_map (chat_id, room_id, room_name, created_at, updated_at)
                VALUES (?, ?, '', ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    room_id=excluded.room_id,
                    updated_at=excluded.updated_at
                """,
                (chat_id, room_id, now, now),
            )


class IrisClient:
    def __init__(self, base_url: str | None = None):
        self.base_url = (base_url or settings.iris_base_url).rstrip("/")

    async def send_text(self, chat_id: int, message: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                res = await client.post(
                    f"{self.base_url}/reply",
                    json={"type": "text", "room": str(chat_id), "data": message},
                )
            if res.status_code != 200:
                logger.warning("iris /reply status=%s chat_id=%s", res.status_code, chat_id)
            return res.status_code == 200
        except Exception as e:
            logger.warning("iris /reply failed chat_id=%s err=%s", chat_id, e)
            return False


async def handle_iris_webhook(payload: object) -> dict:
    # Import here: chat_service must stay importable without the Iris stack.
    from app.services.chat_service import handle_chat

    # TEMP capture: log decrypted attachment format for photo messages (type 2/71)
    # so we can implement image handling against the real payload shape.
    if isinstance(payload, dict):
        raw = payload.get("json") or {}
        if isinstance(raw, str):
            try:
                raw = jsonlib.loads(raw)
            except Exception:
                raw = {}
        if isinstance(raw, dict) and str(raw.get("type")) in ("2", "71", "27", "18", "26"):
            logger.info(
                "IRIS_MEDIA type=%s attachment=%.1500s", raw.get("type"), str(raw.get("attachment"))
            )

    message = parse_iris_webhook(payload)
    if message is None:
        logger.info("iris webhook ignored (unparseable): %.200r", payload)
        return {"ok": True, "handled": False}
    if is_self_message(message):
        return {"ok": True, "handled": False}

    room_id, room_name = await asyncio.to_thread(resolve_room, message.chat_id, message.room_name)
    data = KakaoMsg(
        room_id=room_id,
        room=room_name or str(message.chat_id),
        msg=message.msg,
        sender=message.sender,
        is_command=is_command_message(message.msg),
    )
    result = await handle_chat(data)
    answer = (result or {}).get("answer") or ""
    if not answer:
        return {"ok": True, "handled": True, "sent": False}
    sent = await IrisClient().send_text(message.chat_id, answer)
    return {"ok": True, "handled": True, "sent": sent}


async def send_pending_outbox_once(repo, client: IrisClient, limit: int = 10) -> int:
    """One outbox pass: deliver due PENDING rows through Iris. Failures stay
    PENDING and are retried next pass (same policy as the phone bridge)."""
    rows = await asyncio.to_thread(repo.get_pending_outbox, now_kst().isoformat(), limit)
    sent = 0
    for r in rows:
        chat_id = await asyncio.to_thread(chat_id_for_room, r["room_id"])
        if await client.send_text(chat_id, r["message"]):
            await asyncio.to_thread(repo.ack_outbox, r["id"], "SENT", now_kst().isoformat())
            sent += 1
    return sent


async def run_outbox_sender(repo, client: IrisClient | None = None):
    """Local replacement for the phone's /bot/outbox polling loop."""
    client = client or IrisClient()
    while True:
        try:
            await send_pending_outbox_once(repo, client)
        except Exception as e:
            logger.exception("iris outbox sender tick failed: %s", e)
        await asyncio.sleep(settings.iris_sender_interval_seconds)
