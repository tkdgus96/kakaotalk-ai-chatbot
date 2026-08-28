"""Recurring (daily) reminder commands.

Registers per-room daily messages that the boss scheduler enqueues into
bot_outbox every day at the configured time. Templates support `{N}` which
renders as the day count since registration (registration day = 1일차),
e.g. `!매일 00:00 허재승 금주 {N}일차`.
"""

import re

from app.boss.services.scheduler import render_recurring_message
from app.boss.utils.week import now_kst
from app.dependencies import boss_repo

RECURRING_COMMANDS = {"!매일", "!매일목록", "!매일해제", "!매일도움"}


async def generate_briefing(room_id: int, query: str) -> str:
    """Run the query through the full agent graph (tools + web search) and
    return the answer text — used to produce dynamic recurring briefings."""
    from langchain_core.messages import AIMessage, HumanMessage

    from app.graph import graph

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=query)]},
        config={"configurable": {"room_id": room_id, "sender": "브리핑"}},
    )
    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            return m.content if isinstance(m.content, str) else str(m.content)
    return ""

USAGE = (
    "[매일 리마인더 사용법]\n\n"
    "1) 등록\n"
    "!매일 [HH:MM] [메시지]\n"
    "메시지에 {N}을 넣으면 등록일을 1일차로 매일 증가하는 일차로 바뀝니다.\n"
    "예) !매일 00:00 허재승 금주 {N}일차\n\n"
    "2) 목록\n"
    "!매일목록\n\n"
    "3) 해제\n"
    "!매일해제 [id]"
)


def parse_fire_time(raw: str) -> tuple[int, int] | None:
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", raw) or re.fullmatch(r"(\d{1,2})시(?:(\d{1,2})분?)?", raw)
    if not match:
        return None
    hour = int(match.group(1))
    minute = int(match.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return hour, minute


def handle_recurring_command(room_id: int, sender: str, name: str, args: list[str]) -> str | None:
    if name == "!매일도움":
        return USAGE

    if name == "!매일":
        if len(args) < 2:
            return USAGE
        fire_time = parse_fire_time(args[0])
        if fire_time is None:
            return "시간 형식을 해석하지 못했습니다. 예: 00:00, 9:30, 21시"
        template = " ".join(args[1:]).strip()
        if not template:
            return USAGE
        now = now_kst()
        reminder_id = boss_repo.add_recurring_reminder(
            room_id,
            fire_time[0],
            fire_time[1],
            template,
            now.date().isoformat(),
            sender,
            now.isoformat(),
        )
        preview = render_recurring_message(template, now.date().isoformat(), now)
        return (
            f"매일 리마인더 #{reminder_id} 등록 완료\n"
            f"- 시간: 매일 {fire_time[0]:02d}:{fire_time[1]:02d} (KST)\n"
            f"- 오늘 기준 내용: {preview}"
            + (" (오늘이 1일차)" if "{N}" in template else "")
        )

    if name == "!매일목록":
        rows = boss_repo.list_recurring_reminders(room_id)
        if not rows:
            return "등록된 매일 리마인더가 없습니다."
        now = now_kst()
        lines = []
        for r in rows:
            is_dyn = False
            try:
                is_dyn = bool(r["dynamic"])
            except (KeyError, IndexError):
                pass
            tag = "[동적] " if is_dyn else ""
            body = r["template"] if is_dyn else render_recurring_message(r["template"], r["start_date"], now)
            lines.append(
                f"- #{r['id']} 매일 {int(r['fire_hour']):02d}:{int(r['fire_minute']):02d} {tag}→ {body}"
            )
        return "[매일 리마인더 목록]\n" + "\n".join(lines)

    if name == "!매일해제":
        if len(args) != 1 or not args[0].lstrip("#").isdigit():
            return "사용법: !매일해제 [id]"
        reminder_id = int(args[0].lstrip("#"))
        if boss_repo.disable_recurring_reminder(room_id, reminder_id, now_kst().isoformat()):
            return f"매일 리마인더 #{reminder_id}를 해제했습니다."
        return "해제할 매일 리마인더를 찾지 못했습니다. !매일목록으로 id를 확인해주세요."

    return None
