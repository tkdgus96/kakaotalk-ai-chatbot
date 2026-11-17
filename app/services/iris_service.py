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
from datetime import datetime

import httpx

from app.boss.db import get_conn
from app.boss.utils.week import KST, now_kst
from app.config import settings
from app.dependencies import logger
from app.models import KakaoMsg


@dataclass(frozen=True)
class QuotedMessage:
    """The message a KakaoTalk 답장(reply) points at."""

    text: str
    src_type: int | None = None
    src_user_id: int | None = None
    src_log_id: int | None = None


@dataclass(frozen=True)
class IrisMessage:
    chat_id: int
    room_name: str
    sender: str
    msg: str
    image_urls: tuple[str, ...] = ()
    quoted: QuotedMessage | None = None
    user_id: int | None = None
    msg_type: int | None = None
    kind: str = "text"
    sent_at: datetime | None = None  # KakaoTalk's own timestamp (KST, naive)
    mentioned_user_ids: tuple[int, ...] = ()


# KakaoTalk chat_logs.type -> coarse kind. (Community-documented values; unknown
# types fall back to "text" when there is message text, else "other".)
_TYPE_KINDS: dict[int, str] = {
    0: "feed",          # system feed: join/leave/invite/kick/delete...
    1: "text",
    2: "photo",
    3: "video",
    4: "contact",
    5: "audio",
    6: "emoticon",      # animated emoticon
    10: "schedule",
    12: "emoticon",
    16: "location",
    18: "file",
    20: "emoticon_text",  # emoticon with caption text
    23: "search",
    24: "post",
    25: "emoticon",
    26: "reply",
    27: "photo",        # legacy multi-photo
    71: "photo",        # multi-photo
}
# Kinds that carry no text of their own: log a placeholder so room summaries
# still see that something was shared; never run the LLM on them.
_PLACEHOLDER_KINDS = {"video", "audio", "file", "location", "contact", "schedule"}
# Kinds dropped entirely (noise for memory/summaries).
IGNORED_KINDS = {"feed", "emoticon"}


def classify_message(msg_type, msg: str, attachment: dict | None) -> tuple[str, str]:
    """Return (kind, text). For text-less kinds `text` is a Korean placeholder
    such as "(파일: 보고서.pdf)"; for emoticon-only messages it is ""."""
    t = _as_int(msg_type)
    kind = _TYPE_KINDS.get(t, "text" if msg else "other") if t is not None else "text"
    att = attachment or {}
    if kind == "emoticon":
        return kind, ""
    if kind == "emoticon_text":
        return "text", msg
    if kind == "reply":
        return "text", msg
    if kind in _PLACEHOLDER_KINDS:
        if kind == "file":
            name = str(att.get("name") or "").strip()
            return kind, f"(파일: {name})" if name else "(파일)"
        if kind == "location":
            title = str(att.get("t") or att.get("title") or "").strip()
            addr = str(att.get("a") or att.get("address") or "").strip()
            label = " ".join(x for x in (title, addr) if x)
            return kind, f"(위치 공유: {label})" if label else "(위치 공유)"
        if kind == "schedule":
            title = str(att.get("title") or att.get("t") or "").strip()
            return kind, f"(일정 공유: {title})" if title else "(일정 공유)"
        return kind, {"video": "(동영상)", "audio": "(음성 메시지)", "contact": "(연락처)"}[kind]
    if kind == "photo":
        return kind, msg
    return kind, msg


def _extract_mentions(attachment: dict | None) -> tuple[int, ...]:
    """`@이름` mentions: attachment {"mentions": [{"user_id": 123, "at": [1], "len": 3}, ...]}."""
    if not attachment:
        return ()
    raw = attachment.get("mentions")
    if not isinstance(raw, list):
        return ()
    out: list[int] = []
    for m in raw:
        uid = _as_int((m or {}).get("user_id")) if isinstance(m, dict) else None
        if uid is not None and uid not in out:
            out.append(uid)
    return tuple(out)


def _parse_sent_at(raw_created_at) -> datetime | None:
    """chat_logs.created_at is unix seconds (sometimes ms). -> KST naive datetime."""
    ts = _as_int(raw_created_at)
    if ts is None or ts <= 0:
        return None
    if ts > 10**11:  # milliseconds
        ts = ts // 1000
    try:
        return datetime.fromtimestamp(ts, tz=KST).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):
        return None


def _as_int(v) -> int | None:
    try:
        return int(str(v))
    except (TypeError, ValueError):
        return None


def _parse_attachment(attachment) -> dict | None:
    if isinstance(attachment, str):
        try:
            attachment = jsonlib.loads(attachment)
        except Exception:
            return None
    return attachment if isinstance(attachment, dict) else None


def _extract_quote(msg_type, attachment) -> QuotedMessage | None:
    """KakaoTalk 답장 = type 26. The attachment carries the original message:
    {"src_message": "...", "src_type": 1, "src_userId": ..., "src_logId": ...,
     "attach_only": false, "attach_type": ...}. Reply-to-photo has src_type 2
    and usually an empty src_message."""
    att = _parse_attachment(attachment)
    if not att:
        return None
    if _as_int(msg_type) != 26 and "src_message" not in att and "src_logId" not in att:
        return None
    text = str(att.get("src_message") or "").strip()
    src_type = _as_int(att.get("src_type"))
    if not text:
        if src_type == 2:
            text = "(사진)"
        elif src_type in (71,):
            text = "(사진 여러 장)"
        else:
            return None
    return QuotedMessage(
        text=text,
        src_type=src_type,
        src_user_id=_as_int(att.get("src_userId")),
        src_log_id=_as_int(att.get("src_logId")),
    )


def _extract_image_urls(msg_type, attachment) -> list[str]:
    """Pull signed CDN photo URLs from a decrypted attachment.
    type 2 = single photo ({"url": ...}); type 71 = multi-photo
    ({"C": {"THL": [{"TH": {"THU": url}}...]}} or {"imageUrls": [...]})."""
    if isinstance(attachment, str):
        try:
            attachment = jsonlib.loads(attachment)
        except Exception:
            return []
    if not isinstance(attachment, dict):
        return []
    urls: list[str] = []
    if attachment.get("url"):
        urls.append(str(attachment["url"]))
    for key in ("imageUrls", "urls"):
        vals = attachment.get(key)
        if isinstance(vals, list):
            urls.extend(str(v) for v in vals if v)
    thl = (attachment.get("C") or {}).get("THL") if isinstance(attachment.get("C"), dict) else None
    if isinstance(thl, list):
        for item in thl:
            u = ((item or {}).get("TH") or {}).get("THU") or ((item or {}).get("SL") or {}).get("SLU")
            if u:
                urls.append(str(u))
    seen: set[str] = set()
    return [u for u in urls if not (u in seen or seen.add(u))]


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
    attachment = _parse_attachment(raw.get("attachment"))
    msg_type = _as_int(raw.get("type"))
    image_urls = tuple(_extract_image_urls(raw.get("type"), raw.get("attachment")))
    quoted = _extract_quote(raw.get("type"), raw.get("attachment"))
    kind, text = classify_message(msg_type, msg, attachment)
    if not sender:
        return None
    if kind in IGNORED_KINDS:
        return IrisMessage(
            chat_id=chat_id, room_name=room_name, sender=sender, msg="", kind=kind,
            msg_type=msg_type, user_id=_as_int(raw.get("user_id")),
        )
    if not text and not image_urls:
        return None
    return IrisMessage(
        chat_id=chat_id,
        room_name=room_name,
        sender=sender,
        msg=text,
        image_urls=image_urls,
        quoted=quoted,
        user_id=_as_int(raw.get("user_id")),
        msg_type=msg_type,
        kind=kind,
        sent_at=_parse_sent_at(raw.get("created_at")),
        mentioned_user_ids=_extract_mentions(attachment),
    )


def is_self_message(message: IrisMessage) -> bool:
    """Iris sees the bot account's own sent rows in the DB — skip the echo.
    Exception: in the bot account's own self-chat (health/report chat) the
    operator types as the bot, so trigger-prefixed commands there are real
    input (e.g. `!리포트`) and pass through."""
    if message.sender not in settings.iris_self_names:
        return False
    self_chats = {int(settings.health_self_chat_id or 0), int(settings.report_chat_id or 0)} - {0}
    if message.chat_id in self_chats and is_command_message(message.msg):
        return False
    return True


def is_command_message(msg: str) -> bool:
    """Mirror of the phone bridge's is_command rule: the message addresses the
    bot (trigger prefix) and should run the LLM pipeline instead of buffering.
    Keep IRIS_BOT_TRIGGERS in sync with what the room actually uses."""
    text = msg.strip()
    return any(text.startswith(trigger) for trigger in settings.iris_bot_triggers)


def strip_bot_mention(msg: str) -> str:
    """Remove a leading/trailing "@봇이름" so the LLM sees a clean question."""
    text = msg.strip()
    for name in sorted(settings.iris_self_names, key=len, reverse=True):
        for token in (f"@{name}", name and f"@{name}\u200b"):
            if not token:
                continue
            if text.startswith(token):
                text = text[len(token):].strip()
            if text.endswith(token):
                text = text[: -len(token)].strip()
    return text or msg.strip()


_bot_user_id_cache: dict[str, object] = {"id": None, "ts": 0.0}
_BOT_ID_TTL = 600.0


async def get_bot_user_id(client: IrisClient | None = None) -> int | None:
    """The bot account's KakaoTalk user_id from Iris `/config` (cached)."""
    import time

    if _bot_user_id_cache["id"] is not None and time.time() - float(_bot_user_id_cache["ts"]) < _BOT_ID_TTL:
        return _bot_user_id_cache["id"]  # type: ignore[return-value]
    base = (client.base_url if client else settings.iris_base_url).rstrip("/")
    try:
        async with httpx.AsyncClient(timeout=5.0) as http:
            r = await http.get(f"{base}/config")
        if r.status_code == 200:
            bot_id = _as_int(r.json().get("bot_id"))
            _bot_user_id_cache.update(id=bot_id, ts=time.time())
            return bot_id
    except Exception as e:
        logger.debug("iris /config bot_id lookup failed: %s", e)
    return _bot_user_id_cache["id"]  # type: ignore[return-value]


def resolve_quoted_sender(room_id: int, quoted: QuotedMessage, bot_user_id: int | None) -> str:
    """Best-effort name for the quoted message's author. Iris only gives the
    KakaoTalk user_id, so: bot id -> bot name; otherwise match the exact text
    against our own inbound log for this room (most recent first)."""
    if bot_user_id is not None and quoted.src_user_id == bot_user_id:
        return next(iter(sorted(settings.iris_self_names)), "봇")
    try:
        from app.services.member_service import name_for

        known = name_for(room_id, quoted.src_user_id)
        if known:
            return known
    except Exception:
        pass
    if quoted.src_type in (2, 71):
        return ""
    try:
        from app.chat_log import find_sender_by_content

        found = find_sender_by_content(room_id, quoted.text)
        if found:
            return found
    except Exception:
        pass
    return ""


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

    async def send_image_b64(self, chat_id: int, b64: str) -> bool:
        """Send one image. `b64` is raw base64 (no data: prefix), per Iris /reply."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                res = await client.post(
                    f"{self.base_url}/reply",
                    json={"type": "image", "room": str(chat_id), "data": b64},
                )
            if res.status_code != 200:
                logger.warning("iris image /reply status=%s chat_id=%s", res.status_code, chat_id)
            return res.status_code == 200
        except Exception as e:
            logger.warning("iris image /reply failed chat_id=%s err=%s", chat_id, e)
            return False

    async def send_images_b64(self, chat_id: int, b64_list: list[str]) -> bool:
        """Send images one-by-one with spacing. image_multiple in one call tends
        to drop all but one in this KakaoTalk/Iris build, so we send serially."""
        if not b64_list:
            return False
        ok_any = False
        for i, b64 in enumerate(b64_list):
            if i:
                await asyncio.sleep(0.7)
            ok_any = await self.send_image_b64(chat_id, b64) or ok_any
        return ok_any


async def handle_iris_webhook(payload: object) -> dict:
    # Import here: chat_service must stay importable without the Iris stack.
    from app.services.chat_service import handle_chat
    from app.services.image_service import remember_room_image

    from app.services.member_service import migrate_sender_name, names_for, touch_member

    message = parse_iris_webhook(payload)
    if message is None:
        logger.info("iris webhook ignored (unparseable): %.200r", payload)
        return {"ok": True, "handled": False}
    if message.kind in IGNORED_KINDS:
        # System feeds and emoticon-only messages: no memory/summary value.
        return {"ok": True, "handled": False, "ignored": message.kind}
    if is_self_message(message):
        return {"ok": True, "handled": False}

    room_id, room_name = await asyncio.to_thread(resolve_room, message.chat_id, message.room_name)

    # Stable identity: track user_id -> name; on rename, carry keyed data over.
    if message.user_id is not None:
        old_name = await asyncio.to_thread(touch_member, room_id, message.user_id, message.sender)
        if old_name:
            await asyncio.to_thread(migrate_sender_name, room_id, old_name, message.sender)

    # Cache the latest photo so a following "!이 사진 …" command can be answered.
    if message.image_urls:
        remember_room_image(room_id, message.image_urls[0])

    msg_text = message.msg
    is_command = is_command_message(msg_text) and message.kind not in _PLACEHOLDER_KINDS
    bot_user_id: int | None = None
    if message.quoted or message.mentioned_user_ids:
        bot_user_id = await get_bot_user_id()

    quoted_text = None
    quoted_sender = None
    if message.quoted:
        quoted_text = message.quoted.text
        quoted_sender = await asyncio.to_thread(
            resolve_quoted_sender, room_id, message.quoted, bot_user_id
        ) or None
        # Replying to one of the bot's own messages addresses the bot even
        # without the "!" trigger — treat it as a follow-up question.
        if (
            settings.iris_reply_to_bot_is_command
            and bot_user_id is not None
            and message.quoted.src_user_id == bot_user_id
        ):
            is_command = True

    mentioned_names: list[str] = []
    if message.mentioned_user_ids:
        others = [u for u in message.mentioned_user_ids if u != bot_user_id]
        mentioned_names = await asyncio.to_thread(names_for, room_id, others)
        if (
            settings.iris_mention_is_command
            and bot_user_id is not None
            and bot_user_id in message.mentioned_user_ids
        ):
            is_command = True
            msg_text = strip_bot_mention(msg_text)

    data = KakaoMsg(
        room_id=room_id,
        room=room_name or str(message.chat_id),
        msg=msg_text,
        sender=message.sender,
        is_command=is_command,
        quoted_text=quoted_text,
        quoted_sender=quoted_sender,
        sender_id=message.user_id,
        sent_at=message.sent_at.isoformat() if message.sent_at else None,
        mentioned_names=mentioned_names,
        msg_kind=message.kind,
    )
    result = await handle_chat(data)
    answer = (result or {}).get("answer") or ""
    images = (result or {}).get("images") or []

    client = IrisClient()
    sent = False
    if answer:
        sent = await client.send_text(message.chat_id, answer)
    if images:
        # Back-to-back sends race in Iris/KakaoTalk and silently drop; space them.
        if answer:
            await asyncio.sleep(0.7)
        sent = await client.send_images_b64(message.chat_id, images) or sent
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
