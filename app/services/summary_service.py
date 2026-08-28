"""On-demand conversation summary (!요약) — catch up on what was said.

Pulls raw chat-log lines for a period and summarizes them with the cheap
model. Group-chat catch-up is the single most-requested bot feature
(KakaoTalk added it natively), and we already store the raw turns.
"""

from __future__ import annotations

import re
from datetime import timedelta

from langchain_core.messages import HumanMessage, SystemMessage

from app.boss.utils.week import now_kst
from app.chat_log import get_chat_log_between
from app.config import settings
from app.dependencies import fact_extractor_llm, logger

SUMMARY_COMMANDS = {"!요약", "!요약도움"}

_SYS = (
    "너는 단톡방 대화 요약기다. 아래 원문 로그만 근거로 핵심을 5~8개 불릿으로 요약해라. "
    "누가 무슨 얘기를 했는지, 결정된 것/약속/할 일, 공유된 링크나 정보를 우선 담아라. "
    "없는 내용은 지어내지 말고, 게임 성공/실패 같은 반복 메시지는 '오늘의 단어/챌린지 참여'로 묶어라. "
    "마크다운 볼드/헤더 없이 간결하게."
)

_MAX_LINES = 400


def _parse_period(args: list[str]) -> tuple[str, str]:
    """Return (iso_start, label)."""
    text = " ".join(args)
    today0 = now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    if "어제" in text:
        return (today0 - timedelta(days=1)).isoformat(), "어제"
    m = re.search(r"(\d+)\s*일", text)
    if m:
        d = int(m.group(1))
        return (today0 - timedelta(days=d - 1)).isoformat(), f"최근 {d}일"
    h = re.search(r"(\d+)\s*시간", text)
    if h:
        return (now_kst() - timedelta(hours=int(h.group(1)))).isoformat(), f"최근 {h.group(1)}시간"
    return today0.isoformat(), "오늘"


async def handle_summary_command(room_id: int, name: str, args: list[str]) -> str | None:
    if name == "!요약도움":
        return "!요약 [오늘|어제|N일|N시간] — 그 기간 대화를 요약해줘. 예: !요약, !요약 어제, !요약 3일"
    if name != "!요약":
        return None
    start, label = _parse_period(args)
    end = now_kst().isoformat()
    try:
        import asyncio

        lines = await asyncio.to_thread(get_chat_log_between, room_id, start, end, 3000)
    except Exception as e:
        logger.warning("summary fetch failed: %s", e)
        return "대화 기록을 불러오지 못했어."
    if not lines:
        return f"{label} 대화 기록이 없어."
    if len(lines) > _MAX_LINES:
        lines = lines[-_MAX_LINES:]
    convo = "\n".join(lines)
    try:
        resp = await fact_extractor_llm.ainvoke(
            [SystemMessage(content=_SYS), HumanMessage(content=f"[{label} 대화 로그]\n{convo}")]
        )
        from app.services.usage_service import record_message_usage

        record_message_usage(resp, "summary", settings.light_model, room_id)
        text = resp.content if isinstance(resp.content, str) else str(resp.content)
        return f"[{label} 대화 요약]\n{text.strip()}"
    except Exception as e:
        logger.warning("summary llm failed: %s", e)
        return "요약 중 문제가 생겼어. 잠시 후 다시 해줘."
