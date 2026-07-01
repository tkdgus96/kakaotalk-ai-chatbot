from datetime import datetime

from langchain_core.documents import Document
from langchain_core.tools import tool

from app.config import settings
from app.dependencies import user_profile_store
from app.memory_policy import validate_memory_fact


@tool
async def get_user_memory(sender: str, room_id: int | None = None) -> str:
    """특정 사용자의 저장된 장기 기억/선호/제약 정보를 조회합니다."""
    target_room_id = room_id or settings.playground_room_id
    try:
        res = user_profile_store._collection.get(
            where={"$and": [{"room_id": str(target_room_id)}, {"sender": sender}]},
            limit=50,
        )
    except Exception as exc:
        return f"기억 조회 실패: {exc}"
    docs = res.get("documents", []) if isinstance(res, dict) else []
    if not docs:
        return f"{sender}에 대해 저장된 기억이 없습니다."
    return "\n".join(f"- {doc}" for doc in docs)


@tool
async def remember_user_fact(fact: str, sender: str, room_id: int | None = None, fact_type: str = "기타") -> str:
    """사용자에 대한 선호, 제약, 신상, 일정 등 장기 기억을 명시적으로 저장합니다."""
    target_room_id = room_id or settings.playground_room_id
    text = fact.strip()
    if not text:
        return "저장할 내용이 없습니다."
    policy = validate_memory_fact(text, sender, fact_type, source_text=text)
    if not policy.allowed:
        return f"기억 저장 거부: {policy.reason}"
    doc = Document(
        page_content=f"[{fact_type}] {text}",
        metadata={
            "room_id": str(target_room_id),
            "sender": sender,
            "fact_type": fact_type,
            "timestamp": datetime.now().isoformat(),
            "source": "memory_tool",
        },
    )
    try:
        await user_profile_store.aadd_documents([doc])
    except Exception as exc:
        return f"기억 저장 실패: {exc}"
    return f"{sender}에 대한 기억을 저장했습니다: {text}"


@tool
async def forget_user_memory(sender: str, room_id: int | None = None) -> str:
    """특정 사용자에 대해 저장된 장기 기억을 모두 삭제합니다. 사용자가 명시적으로 삭제를 요청할 때만 사용하세요."""
    target_room_id = room_id or settings.playground_room_id
    try:
        user_profile_store._collection.delete(
            where={"$and": [{"room_id": str(target_room_id)}, {"sender": sender}]}
        )
    except Exception as exc:
        return f"기억 삭제 실패: {exc}"
    return f"{sender}에 대한 저장된 기억을 삭제했습니다."
