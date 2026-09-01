"""Image send/receive for the Iris bridge.

Receive: KakaoTalk photo messages arrive via Iris with a signed CDN `url`
(no decryption needed). We cache the most recent photo per room. Whether a
following message wants that image described or used as a drawing reference is
judged by the LLM, which calls the `analyze_image` / `generate_image` tools —
this module just provides the primitives (describe/generate/cache).

Send: `!그림 <프롬프트>` generates an image; `!이미지/!짤 <검색어>` fetches a web
image (Naver image search). Both return base64 for Iris /reply.
"""

from __future__ import annotations

import base64
import io
import time

import httpx
from langchain_core.messages import HumanMessage
from openai import AsyncOpenAI

from app.config import settings
from app.dependencies import llm, logger

IMAGE_COMMANDS = {"!그림", "!이미지", "!짤"}
_RECENT_IMAGE_TTL = 30 * 60  # 30 min

# room_id -> {"url": str, "ts": float}
_recent_image: dict[int, dict] = {}

# Collector for images the LLM `generate_image` tool produces, keyed by room so
# it survives langgraph's execution context (contextvars don't propagate there).
# handle_chat starts/takes; the tool collects using room_id injected via config.
_generated_by_room: dict[int, list[str]] = {}


def start_image_collection(room_id: int) -> None:
    _generated_by_room[room_id] = []


def collect_generated_image(room_id: int, b64: str) -> None:
    if b64:
        _generated_by_room.setdefault(room_id, []).append(b64)


def take_generated_images(room_id: int) -> list[str]:
    return _generated_by_room.pop(room_id, [])


def remember_room_image(room_id: int, url: str) -> None:
    if url:
        _recent_image[room_id] = {"url": url, "ts": time.time()}


def get_recent_room_image(room_id: int) -> str | None:
    rec = _recent_image.get(room_id)
    if not rec:
        return None
    if time.time() - rec["ts"] > _RECENT_IMAGE_TTL:
        return None
    return rec["url"]


async def _download_bytes(url: str, timeout: float = 20.0) -> tuple[bytes, str] | None:
    """Download a URL and return (bytes, mime). None on failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            res = await client.get(url, headers={"User-Agent": "Mozilla/5.0 (onban-bot)"})
        if res.status_code != 200 or not res.content:
            logger.warning("image download failed status=%s url=%.80s", res.status_code, url)
            return None
        return res.content, res.headers.get("content-type", "image/jpeg").split(";")[0]
    except Exception as e:
        logger.warning("image download error url=%.80s err=%s", url, e)
        return None


async def _download_b64(url: str, timeout: float = 20.0) -> tuple[str, str] | None:
    """Download a URL and return (base64, mime) — used for vision input."""
    got = await _download_bytes(url, timeout)
    if not got:
        return None
    raw, mime = got
    return base64.b64encode(raw).decode(), mime


def _to_send_jpeg_b64(raw: bytes, max_dim: int = 1280, quality: int = 85) -> str:
    """Re-encode to a reasonably sized JPEG for reliable KakaoTalk delivery.
    Large PNGs (gpt-image-1) / news photos can silently fail to send otherwise.
    Falls back to raw base64 if Pillow can't decode it."""
    try:
        from PIL import Image

        im = Image.open(io.BytesIO(raw)).convert("RGB")
        im.thumbnail((max_dim, max_dim))
        buf = io.BytesIO()
        im.save(buf, format="JPEG", quality=quality)
        return base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        logger.warning("image compress failed, sending raw: %s", e)
        return base64.b64encode(raw).decode()


async def describe_image(url: str, question: str) -> str | None:
    """Download the image and ask gpt-4o (vision) about it."""
    got = await _download_b64(url)
    if not got:
        return None
    b64, mime = got
    prompt = question.strip() or "이 사진에 뭐가 보이는지 한국어로 자연스럽게 설명해줘."
    try:
        resp = await llm.ainvoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{b64}"}},
                    ]
                )
            ]
        )
        from app.services.usage_service import record_message_usage

        record_message_usage(resp, "vision", "gpt-4o")
        return resp.content if isinstance(resp.content, str) else str(resp.content)
    except Exception as e:
        logger.warning("vision describe failed: %s", e)
        return None


async def is_prompt_flagged(prompt: str) -> bool:
    """Run OpenAI moderation on a generation prompt; True if disallowed."""
    if not settings.openai_api_key:
        return False
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        res = await client.moderations.create(model="omni-moderation-latest", input=prompt)
        return bool(res.results and res.results[0].flagged)
    except Exception as e:
        logger.warning("moderation check failed (allowing): %s", e)
        return False


async def generate_image_ref_b64(prompt: str, ref_url: str) -> str | None:
    """Generate an image that references an attached image (e.g. 'draw party
    members eating THIS item'), so the model actually sees the reference.
    Provider = settings.image_ref_provider (gemini | openai); Gemini 2.5 Flash
    Image is cheap/free, OpenAI gpt-image-1 is the paid fallback."""
    if await is_prompt_flagged(prompt):
        logger.info("ref image prompt blocked by moderation")
        return None
    got = await _download_bytes(ref_url)
    if not got:
        return None
    raw, mime = got

    provider = settings.image_ref_provider
    if provider == "gemini" and settings.gemini_api_key:
        b64 = await _gen_gemini_ref_b64(prompt, raw, mime)
        if b64:
            return b64
        logger.info("gemini ref gen failed; falling back to openai")
    return await _gen_openai_ref_b64(prompt, raw)


async def _gen_openai_ref_b64(prompt: str, raw: bytes) -> str | None:
    if not settings.openai_api_key:
        return None
    try:
        from PIL import Image

        # gpt-image-1 edit wants a real image file; normalize to PNG.
        buf = io.BytesIO()
        Image.open(io.BytesIO(raw)).convert("RGB").save(buf, format="PNG")
        buf.seek(0)
        buf.name = "reference.png"

        client = AsyncOpenAI(api_key=settings.openai_api_key)
        res = await client.images.edit(
            model=settings.image_gen_ref_model, image=buf, prompt=prompt, size="1024x1024", n=1
        )
        from app.services.usage_service import record_image_usage

        record_image_usage(settings.image_gen_ref_model)
        item = res.data[0]
        b64 = getattr(item, "b64_json", None)
        if b64:
            return _to_send_jpeg_b64(base64.b64decode(b64))
        url = getattr(item, "url", None)
        if url:
            g = await _download_bytes(url)
            if g:
                return _to_send_jpeg_b64(g[0])
        return None
    except Exception as e:
        logger.warning("openai ref image generate failed: %s", e)
        return None


async def _gen_gemini_ref_b64(prompt: str, raw: bytes, mime: str) -> str | None:
    """Gemini 2.5 Flash Image (image-input edit) via the Generative Language
    REST API. Returns the first inline image part as a compressed JPEG b64."""
    if not settings.gemini_api_key:
        return None
    try:
        from PIL import Image

        # Normalize to a mime Gemini reliably accepts.
        buf = io.BytesIO()
        Image.open(io.BytesIO(raw)).convert("RGB").save(buf, format="PNG")
        ref_b64 = base64.b64encode(buf.getvalue()).decode()

        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/"
            f"{settings.gemini_image_model}:generateContent"
        )
        payload = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {"inlineData": {"mimeType": "image/png", "data": ref_b64}},
                    ]
                }
            ],
            "generationConfig": {"responseModalities": ["IMAGE"]},
        }
        async with httpx.AsyncClient(timeout=90.0) as client:
            res = await client.post(
                url,
                params={"key": settings.gemini_api_key},
                json=payload,
                headers={"Content-Type": "application/json"},
            )
        if res.status_code != 200:
            logger.warning("gemini ref gen status=%s body=%.200s", res.status_code, res.text)
            return None
        data = res.json()
        from app.services.usage_service import record_image_usage

        record_image_usage(settings.gemini_image_model)
        for cand in data.get("candidates", []):
            for part in cand.get("content", {}).get("parts", []):
                inline = part.get("inlineData") or part.get("inline_data")
                if inline and inline.get("data"):
                    return _to_send_jpeg_b64(base64.b64decode(inline["data"]))
        logger.warning("gemini ref gen: no image part in response")
        return None
    except Exception as e:
        logger.warning("gemini ref image generate failed: %s", e)
        return None


async def generate_image_b64(prompt: str) -> str | None:
    """Plain text→image. Provider = settings.image_gen_provider
    (pollinations | openai). Pollinations is free (no key); OpenAI
    gpt-image-1-mini is the paid fallback."""
    if await is_prompt_flagged(prompt):
        logger.info("image prompt blocked by moderation")
        return None

    if settings.image_gen_provider == "pollinations":
        b64 = await _gen_pollinations_b64(prompt)
        if b64:
            return b64
        logger.info("pollinations gen failed; falling back to openai")
    return await _gen_openai_b64(prompt)


async def _gen_pollinations_b64(prompt: str) -> str | None:
    """Free image generation via Pollinations.ai (FLUX-based, no API key)."""
    try:
        from urllib.parse import quote

        url = f"https://image.pollinations.ai/prompt/{quote(prompt)}"
        async with httpx.AsyncClient(timeout=90.0, follow_redirects=True) as client:
            res = await client.get(
                url,
                params={"width": 1024, "height": 1024, "nologo": "true", "model": "flux"},
                headers={"User-Agent": "Mozilla/5.0 (onban-bot)"},
            )
        if res.status_code != 200 or not res.content:
            logger.warning("pollinations status=%s len=%s", res.status_code, len(res.content or b""))
            return None
        if not res.headers.get("content-type", "").startswith("image"):
            logger.warning("pollinations non-image response: %.120s", res.text)
            return None
        from app.services.usage_service import record_image_usage

        record_image_usage("pollinations")
        return _to_send_jpeg_b64(res.content)
    except Exception as e:
        logger.warning("pollinations generate failed: %s", e)
        return None


async def _gen_openai_b64(prompt: str) -> str | None:
    if not settings.openai_api_key:
        return None
    # NOTE: `response_format` is omitted (this account's images API rejects it).
    # gpt-image-1 returns b64_json; other models may return a url (downloaded).
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        res = await client.images.generate(
            model=settings.image_gen_model, prompt=prompt, size="1024x1024", n=1
        )
        item = res.data[0]
        from app.services.usage_service import record_image_usage

        record_image_usage(settings.image_gen_model)
        b64 = getattr(item, "b64_json", None)
        if b64:
            return _to_send_jpeg_b64(base64.b64decode(b64))
        url = getattr(item, "url", None)
        if url:
            got = await _download_bytes(url)
            if got:
                return _to_send_jpeg_b64(got[0])
        return None
    except Exception as e:
        logger.warning("image generate failed: %s", e)
        return None


async def search_web_image_b64(query: str) -> str | None:
    if not (settings.naver_client_id and settings.naver_client_secret):
        return None
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://openapi.naver.com/v1/search/image",
                params={"query": query, "display": 5, "sort": "sim"},
                headers={
                    "X-Naver-Client-Id": settings.naver_client_id,
                    "X-Naver-Client-Secret": settings.naver_client_secret,
                },
            )
        if res.status_code != 200:
            logger.warning("naver image search status=%s", res.status_code)
            return None
        for item in res.json().get("items", []):
            link = item.get("link")
            if not link:
                continue
            got = await _download_bytes(link)
            if got:
                return _to_send_jpeg_b64(got[0])
        return None
    except Exception as e:
        logger.warning("naver image search failed: %s", e)
        return None


async def handle_image_command(name: str, args: list[str]) -> tuple[str, list[str]] | None:
    """Returns (text, [b64 images]) for image-send commands, or None if not one."""
    query = " ".join(args).strip()
    if name == "!그림":
        if not query:
            return ("사용법: !그림 [설명]  예) !그림 노을 지는 바다 픽셀아트", [])
        b64 = await generate_image_b64(query)
        if not b64:
            return ("이미지 생성에 실패했어. 잠시 후 다시 시도해줘.", [])
        return (f"🎨 '{query}' 그려봤어", [b64])
    if name in ("!이미지", "!짤"):
        if not query:
            return ("사용법: !이미지 [검색어]  예) !이미지 손흥민", [])
        b64 = await search_web_image_b64(query)
        if not b64:
            return (f"'{query}' 이미지를 찾지 못했어.", [])
        return (f"'{query}' 검색 이미지야", [b64])
    return None
