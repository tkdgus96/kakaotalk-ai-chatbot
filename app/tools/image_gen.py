from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.config import settings
from app.dependencies import logger
from app.services.image_service import (
    collect_generated_image,
    generate_image_b64,
    generate_image_ref_b64,
    get_recent_room_image,
)


@tool
async def generate_image(prompt: str, config: RunnableConfig, use_reference: bool = False) -> str:
    """사용자가 그림/사진/이미지를 새로 '그려줘/생성해줘/만들어줘'라고 요청할 때 호출한다.
    실제로 존재하는 사진 검색이 아니라 **생성(create)** 요청일 때만 쓴다.

    prompt: 그릴 장면을 구체적으로 묘사한 문구. 사용자의 표현을 살리되 필요하면 화풍/구도를 보강한다.
    use_reference: 사용자가 "이 사진/이 아이템/이거 보고 그려줘"처럼 **방금 올라온 이미지를 참고**해서
            그려달라고 하면 true. 그 이미지를 시각적 레퍼런스로 반영한다. (예: 특정 게임 아이템을
            먹는 그림 등, 모델이 모르는 대상을 이미지로 알려줄 때)

    이미지는 이 도구가 방으로 직접 전송한다. 호출 후에는 짧은 코멘트만 답하면 된다."""
    room_id = int((config or {}).get("configurable", {}).get("room_id", settings.playground_room_id))
    ref_url = get_recent_room_image(room_id) if use_reference else None
    b64 = await generate_image_ref_b64(prompt, ref_url) if ref_url else await generate_image_b64(prompt)
    logger.info("generate_image room_id=%s ref=%s ok=%s", room_id, bool(ref_url), bool(b64))
    if not b64:
        return "이미지 생성에 실패했어. 잠시 후 다시 시도해줘."
    collect_generated_image(room_id, b64)
    ref_note = " (첨부 이미지 참고)" if ref_url else ""
    return f"'{prompt}' 이미지를 생성해서 방에 전송했어{ref_note}."
