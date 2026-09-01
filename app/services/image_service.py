"""Image send/receive for the Iris bridge.

Receive: KakaoTalk photo messages arrive via Iris with a signed CDN `url`
(no decryption needed). We cache the most recent photo per room; a following
`!` command that refers to it ("이 사진/이미지/그림 …") is answered by gpt-4o
vision.

Send: `!그림 <프롬프트>` generates an image (DALL·E 3); `!이미지/!짤 <검색어>`
fetches a web image (Naver image search). Both return base64 for Iris /reply.
"""

from __future__ import annotations

import base64
import io
import re
import time

import httpx
from langchain_core.messages import HumanMessage
from openai import AsyncOpenAI

from app.config import settings
from app.dependencies import llm, logger

IMAGE_COMMANDS = {"!그림", "!이미지", "!짤"}
_RECENT_IMAGE_TTL = 30 * 60  # 30 min
_IMAGE_REF_WORDS = ("사진", "이미지", "그림", "짤", "방금거", "이거", "위에", "아이템", "참고", "참조", "이걸", "저거")

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


def references_image(text: str) -> bool:
    return any(w in text for w in _IMAGE_REF_WORDS)


# "<묘사> 그려줘/생성해줘/만들어줘 [부연설명]" 형태를 결정론적으로 잡아 프롬프트를 뽑는다.
# 동사가 문장 끝이 아니어도(뒤에 "이 사진이 ~야" 같은 설명이 붙어도) 감지한다.
# LLM 도구 선택은 히스토리에 휩쓸려 누락되기도 해서, 이 경로로 확실히 생성한다.
_GEN_VERB_RE = re.compile(
    r"(그려\s*줘|그려\s*봐|그려\s*줄래|그려\s*라|그려\s*주라|"
    r"생성\s*해\s*줘|만들어\s*줘|그려\s*주세요|그려(?=\s*[.!~?ㅋㅎ]*$))"
)
# 비명령형 오탐 방지: "그려진/그려졌", "만들어졌/먹지", "생성된" 등
_GEN_FALSE = re.compile(r"그려(졌|진|지)|만들어(졌|진|먹|둔|낸)|생성(된|되|물)")


def detect_image_generation(msg: str) -> str | None:
    """Return the image prompt if `msg` is a '<desc> 그려줘 …' style request."""
    text = msg.strip()
    if text and text[0] in "!！":
        text = text[1:].strip()
    if not _GEN_VERB_RE.search(text) or _GEN_FALSE.search(text):
        return None
    prompt = _GEN_VERB_RE.sub(" ", text)
    prompt = re.sub(r"\s+", " ", prompt).strip(" .!~?ㅋㅎ")
    return prompt or None


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


async def maybe_answer_with_image(room_id: int, msg: str) -> str | None:
    """If `msg` refers to a recent photo in the room, answer via vision."""
    if not references_image(msg):
        return None
    url = get_recent_room_image(room_id)
    if not url:
        return None
    question = _strip_command_prefix(msg)
    answer = await describe_image(url, question)
    if answer:
        # Remember what the photo showed so it's searchable later ("저번 그 사진").
        try:
            import asyncio

            from app.boss.utils.week import now_kst
            from app.chat_log import add_chat_log

            summary = answer[:300].replace("\n", " ")
            await asyncio.to_thread(
                add_chat_log, room_id, "온반봇", f"[사진 설명] {summary}", now_kst().isoformat()
            )
        except Exception:
            pass
    return answer


def _strip_command_prefix(msg: str) -> str:
    text = msg.strip()
    if text and text[0] in "!！":
        text = text[1:]
    return text.strip()


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
    members eating THIS item'). Uses gpt-image-1's image-input edit endpoint so
    the model actually sees the reference — pricier than plain gen, used only
    when the request refers to an image."""
    if not settings.openai_api_key:
        return None
    if await is_prompt_flagged(prompt):
        logger.info("ref image prompt blocked by moderation")
        return None
    got = await _download_bytes(ref_url)
    if not got:
        return None
    raw, _ = got
    try:
        import io

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
        logger.warning("ref image generate failed: %s", e)
        return None


async def generate_image_b64(prompt: str) -> str | None:
    if not settings.openai_api_key:
        return None
    if await is_prompt_flagged(prompt):
        logger.info("image prompt blocked by moderation")
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
