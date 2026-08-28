from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from app.boss.render import render_settlement_html
from app.config import settings
from app.dependencies import boss_service, logger
from app.models import KakaoMsg, OutboxAckRequest
from app.services.audit_service import record_audit
from app.services.chat_service import handle_chat
from app.services.iris_service import handle_iris_webhook

router = APIRouter()


async def require_token(request: Request):
    """Opt-in shared-token auth. Disabled when BOT_SHARED_TOKEN is unset so the
    live setup keeps working; enable by setting it + adding ?token=/header."""
    expected = settings.bot_shared_token
    if not expected:
        return
    provided = request.headers.get("X-Bot-Token") or request.query_params.get("token")
    if provided != expected:
        raise HTTPException(status_code=401, detail="unauthorized")


@router.get("/healthz")
async def healthz():
    import asyncio

    import httpx

    from app.boss.db import get_conn

    checks = {}
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1")
        checks["db"] = True
    except Exception:
        checks["db"] = False
    try:
        from app.dependencies import vectorstore

        await asyncio.to_thread(vectorstore._collection.count)
        checks["chroma"] = True
    except Exception:
        checks["chroma"] = False
    try:
        async with httpx.AsyncClient(timeout=4.0) as c:
            r = await c.get(f"{settings.iris_base_url.rstrip('/')}/config")
        checks["iris"] = r.status_code == 200
    except Exception:
        checks["iris"] = False
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM bot_outbox WHERE status='PENDING'"
            ).fetchone()
        checks["outbox_pending"] = int(row["c"]) if row else 0
    except Exception:
        checks["outbox_pending"] = -1
    ok = checks.get("db") and checks.get("iris")
    return {"ok": bool(ok), "checks": checks}


@router.get("/rooms", dependencies=[Depends(require_token)])
async def list_rooms():
    return {"allowed_rooms": sorted(settings.allowed_rooms)}


@router.post("/rooms/{room_id}", dependencies=[Depends(require_token)])
async def add_room(room_id: int):
    settings.allowed_rooms.add(room_id)
    logger.info("Room added: %s", room_id)
    record_audit("room_added", room_id=room_id)
    return {"allowed_rooms": sorted(settings.allowed_rooms)}


@router.delete("/rooms/{room_id}", dependencies=[Depends(require_token)])
async def remove_room(room_id: int):
    settings.allowed_rooms.discard(room_id)
    logger.info("Room removed: %s", room_id)
    record_audit("room_removed", room_id=room_id)
    return {"allowed_rooms": sorted(settings.allowed_rooms)}


@router.post("/debug", dependencies=[Depends(require_token)])
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


@router.post("/iris", dependencies=[Depends(require_token)])
async def iris_webhook(request: Request):
    """Webhook target for the Iris bridge (set Iris web_server_endpoint to
    http://<backend>:8000/iris). Replies are pushed back via Iris /reply,
    not in this HTTP response."""
    try:
        payload = await request.json()
    except Exception:
        return {"ok": False, "message": "invalid json"}
    return await handle_iris_webhook(payload)


@router.get("/iris/rooms", dependencies=[Depends(require_token)])
async def iris_room_map():
    """Current chat_id -> room_id mapping (debugging / IRIS_ROOM_MAP 작성용)."""
    from app.boss.db import get_conn

    with get_conn() as conn:
        rows = conn.execute(
            "SELECT chat_id, room_id, room_name, updated_at FROM iris_room_map ORDER BY updated_at DESC"
        ).fetchall()
    return {"rooms": [dict(r) for r in rows]}


@router.get("/bot/outbox", dependencies=[Depends(require_token)])
async def get_bot_outbox(bot_id: str = "main", limit: int = 10):
    # When the Iris sender owns delivery, the legacy phone-bridge poller must
    # not grab/ack items (it would race and, on failed delivery, drop them).
    # The in-process Iris sender reads the repo directly, so gating the HTTP
    # endpoint here retires the phone contract without affecting delivery.
    if settings.enable_iris_sender:
        return {"bot_id": bot_id, "items": []}
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


@router.post("/bot/outbox/{outbox_id}/ack", dependencies=[Depends(require_token)])
async def ack_bot_outbox(outbox_id: int, body: OutboxAckRequest):
    if settings.enable_iris_sender:
        return {"ok": False, "message": "outbox delivery handled by iris sender"}
    ok = boss_service.ack_outbox(outbox_id, body.status)
    if not ok:
        return {"ok": False, "message": "outbox not found or already acked"}
    return {"ok": True}
