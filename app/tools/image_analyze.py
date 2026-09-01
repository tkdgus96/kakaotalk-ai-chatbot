from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.config import settings
from app.dependencies import logger
from app.services.image_service import describe_image, get_recent_room_image


@tool
async def analyze_image(question: str, config: RunnableConfig) -> str:
    """방금 이 방에 올라온 사진/이미지를 보고 질문에 답할 때 호출한다.
    사용자가 "이 사진 뭐야", "이거 무슨 아이템이야", "이 짤 설명해줘"처럼 **직전에 올라온
    이미지의 내용**을 물으면 사용한다. (참고: 새 그림을 그리는 게 아니라 기존 이미지를 '보는' 것)

    question: 그 이미지에 대해 사용자가 궁금해하는 것. 없으면 일반 설명을 요청한다.

    최근 올라온 이미지가 없으면 그 사실을 반환한다."""
    room_id = int((config or {}).get("configurable", {}).get("room_id", settings.playground_room_id))
    url = get_recent_room_image(room_id)
    logger.info("analyze_image room_id=%s has_image=%s", room_id, bool(url))
    if not url:
        return "최근 이 방에 올라온 이미지가 없어. 사진을 먼저 올려줘."
    answer = await describe_image(url, question or "")
    if not answer:
        return "이미지를 확인하는 데 실패했어. 잠시 후 다시 시도해줘."
    return answer
