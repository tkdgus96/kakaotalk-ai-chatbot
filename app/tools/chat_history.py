from datetime import datetime, timedelta

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.boss.utils.week import now_kst
from app.chat_log import get_chat_log_between, search_chat_log
from app.config import settings
from app.dependencies import llm


def _date_window(period: str) -> tuple[str, datetime, datetime]:
    text = (period or "").strip()
    today = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    if "어제" in text or text.lower() == "yesterday":
        return "어제", today - timedelta(days=1), today
    if "오늘" in text or text.lower() == "today":
        return "오늘", today, today + timedelta(days=1)
    if "그제" in text:
        start = today - timedelta(days=2)
        return "그제", start, start + timedelta(days=1)
    return "오늘", today, today + timedelta(days=1)


def _log_iso(dt: datetime) -> str:
    return dt.replace(tzinfo=None).isoformat()


@tool
async def summarize_chat_history(period: str, room_id: int | None = None) -> str:
    """특정 날짜의 채팅방 원문 로그를 요약합니다.

    period는 '오늘', '어제', '그제' 중 하나를 우선 사용하세요.
    room_id를 모르면 현재 시스템 프롬프트의 room_id를 사용하세요.
    """
    target_room_id = room_id or settings.playground_room_id
    label, start, end = _date_window(period)
    rows = get_chat_log_between(target_room_id, _log_iso(start), _log_iso(end), limit=3000)
    date_text = f"{start:%Y-%m-%d} 00:00 ~ {end:%Y-%m-%d} 00:00 KST"
    if not rows:
        return f"{label}({date_text}) 채팅 로그가 없습니다. 다른 날짜 내용으로 추측하지 마세요."
    if len(rows) <= 3:
        joined_short = "\n".join(f"- {row}" for row in rows)
        return (
            f"{label}({date_text})에는 기록된 메시지가 {len(rows)}개 있습니다.\n"
            "메시지가 적어서 원문 중심으로 정리합니다.\n"
            f"{joined_short}"
        )

    if len(rows) > 700:
        return await _summarize_long_rows(label, date_text, rows)

    joined = "\n".join(rows)
    response = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "너는 채팅방 회고 요약 도우미다. 제공된 원문 로그만 근거로 "
                    "핵심 주제, 결정사항, 할 일, 언급된 사람/일정을 간결하게 요약해라. "
                    "다른 날짜나 기억을 섞지 마라."
                )
            ),
            HumanMessage(content=f"대상: {label} ({date_text})\n\n[원문 로그]\n{joined}"),
        ]
    )
    return str(response.content)


async def _summarize_long_rows(label: str, date_text: str, rows: list[str]) -> str:
    chunk_size = 250
    chunks = [rows[i : i + chunk_size] for i in range(0, len(rows), chunk_size)]
    summaries: list[str] = []
    for idx, chunk in enumerate(chunks, start=1):
        response = await llm.ainvoke(
            [
                SystemMessage(
                    content=(
                        "너는 채팅 로그 압축기다. 제공된 원문만 근거로 핵심 주제, "
                        "결정사항, 할 일, 명시적 불확실성을 10줄 이내로 요약해라. "
                        "다른 날짜나 기억을 섞지 마라."
                    )
                ),
                HumanMessage(content=f"대상: {label} ({date_text}) 구간 {idx}/{len(chunks)}\n\n[원문 로그]\n" + "\n".join(chunk)),
            ]
        )
        summaries.append(f"[구간 {idx}]\n{response.content}")

    response = await llm.ainvoke(
        [
            SystemMessage(
                content=(
                    "너는 채팅방 회고 요약 도우미다. 구간별 요약만 근거로 전체 하루를 요약해라. "
                    "핵심 주제, 결정사항, 할 일, 언급된 사람/일정을 간결하게 정리하고, "
                    "근거가 약한 내용은 불확실하다고 표시해라."
                )
            ),
            HumanMessage(
                content=(
                    f"대상: {label} ({date_text})\n"
                    f"원문 메시지 수: {len(rows)}개\n\n[구간별 요약]\n"
                    + "\n\n".join(summaries)
                )
            ),
        ]
    )
    return str(response.content)


@tool
async def search_chat_history(query: str, room_id: int | None = None, limit: int = 10) -> str:
    """채팅방 과거 원문 로그에서 키워드로 관련 발언을 찾습니다."""
    target_room_id = room_id or settings.playground_room_id
    rows = search_chat_log(target_room_id, query, limit=limit)
    if not rows:
        return "관련 과거 발언을 찾지 못했습니다."
    return "\n".join(f"- {row}" for row in rows)
