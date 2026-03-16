from fastapi import APIRouter, Request

from app.config import settings
from app.dependencies import logger
from app.models import KakaoMsg
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
