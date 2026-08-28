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
import time

import httpx
from langchain_core.messages import HumanMessage
from openai import AsyncOpenAI

from app.config import settings
from app.dependencies import llm, logger

IMAGE_COMMANDS = {"!그림", "!이미지", "!짤"}
_RECENT_IMAGE_TTL = 30 * 60  # 30 min
_IMAGE_REF_WORDS = ("사진", "이미지", "그림", "짤", "방금거", "이거", "위에")

# room_id -> {"url": str, "ts": float}
_recent_image: dict[int, dict] = {}


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


async def _download_b64(url: str, timeout: float = 20.0) -> tuple[str, str] | None:
    """Download a URL and return (base64, mime). None on failure."""
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            res = await client.get(url)
        if res.status_code != 200 or not res.content:
            logger.warning("image download failed status=%s url=%.80s", res.status_code, url)
            return None
        mime = res.headers.get("content-type", "image/jpeg").split(";")[0]
        return base64.b64encode(res.content).decode(), mime
    except Exception as e:
        logger.warning("image download error url=%.80s err=%s", url, e)
        return None


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
    return await describe_image(url, question)


def _strip_command_prefix(msg: str) -> str:
    text = msg.strip()
    if text and text[0] in "!！":
        text = text[1:]
    return text.strip()


async def generate_image_b64(prompt: str) -> str | None:
    if not settings.openai_api_key:
        return None
    try:
        client = AsyncOpenAI(api_key=settings.openai_api_key)
        res = await client.images.generate(
            model="dall-e-3", prompt=prompt, size="1024x1024", n=1, response_format="b64_json"
        )
        return res.data[0].b64_json
    except Exception as e:
        logger.warning("dalle generate failed: %s", e)
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
            got = await _download_b64(link)
            if got:
                return got[0]
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
