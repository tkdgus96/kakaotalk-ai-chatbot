from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from app.boss.render import render_settlement_html
from app.config import settings
from app.dependencies import boss_service, logger
from app.models import KakaoMsg, OutboxAckRequest
from app.services.chat_service import handle_chat

router = APIRouter()


@router.get("/rooms")
async def list_rooms():
    return {"allowed_rooms": sorted(settings.allowed_rooms)}


@router.post("/rooms/{room_id}")
async def add_room(room_id: int):
    settings.allowed_rooms.add(room_id)
    logger.info("Room added: %s", room_id)
    return {"allowed_rooms": sorted(settings.allowed_rooms)}


@router.delete("/rooms/{room_id}")
async def remove_room(room_id: int):
    settings.allowed_rooms.discard(room_id)
    logger.info("Room removed: %s", room_id)
    return {"allowed_rooms": sorted(settings.allowed_rooms)}


@router.post("/debug")
async def debug_request(request: Request):
    body = await request.json()
    logger.info("Raw request body: %s", body)
    return {"received": body}


@router.post("/chat")
async def chat(data: KakaoMsg):
    return await handle_chat(data)


@router.get("/s/{public_token}", response_class=HTMLResponse)
async def settlement_view(public_token: str):
    found = boss_service.settlement_view(public_token)
    if not found:
        return HTMLResponse("Not found", status_code=404)
    settlement, drops = found
    return HTMLResponse(render_settlement_html(settlement, drops))


@router.get("/bot/outbox")
async def get_bot_outbox(bot_id: str = "main", limit: int = 10):
    rows = boss_service.get_pending_outbox(limit=limit)
    return {
        "bot_id": bot_id,
        "items": [
            {
                "id": r["id"],
                "room_id": r["room_id"],
                "room_name": r["room_name"],
                "message": r["message"],
            }
            for r in rows
        ],
    }


@router.post("/bot/outbox/{outbox_id}/ack")
async def ack_bot_outbox(outbox_id: int, body: OutboxAckRequest):
    ok = boss_service.ack_outbox(outbox_id, body.status)
    if not ok:
        return {"ok": False, "message": "outbox not found or already acked"}
    return {"ok": True}
