"""LLM tools for recurring reminders (daily or specific weekdays), including
DYNAMIC briefings that fetch fresh data (weather/stock/etc.) at send time.

Registration is via these tools so the model can act on natural language like
"매일 아침 8시에 날씨 알려줘" or "매주 월요일 9시에 이번주 일정 정리해줘".
The scheduler generates dynamic answers with tools at fire time.
"""

from __future__ import annotations

import re

from langchain_core.runnables import RunnableConfig
from langchain_core.tools import tool

from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import boss_repo

_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]
_KO_TO_IDX = {d: i for i, d in enumerate(_WEEKDAYS)}


def _room(config: RunnableConfig) -> int:
    return int((config or {}).get("configurable", {}).get("room_id", settings.playground_room_id))


def _parse_hhmm(raw: str) -> tuple[int, int] | None:
    raw = (raw or "").strip()
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", raw) or re.fullmatch(r"(\d{1,2})\s*시\s*(?:(\d{1,2})\s*분?)?", raw)
    if not m:
        return None
    h, mnt = int(m.group(1)), int(m.group(2) or 0)
    return (h, mnt) if (0 <= h <= 23 and 0 <= mnt <= 59) else None


def _parse_weekdays(raw: str) -> str:
    """Return comma-separated weekday indices (0=Mon..6=Sun). Empty = every day.
    Accepts '월,수', '매일', '주말', '평일', 'mon,wed', numbers, etc."""
    if not raw:
        return ""
    t = raw.strip().lower()
    if any(w in t for w in ("매일", "everyday", "daily")):
        return ""
    if "주말" in t or "weekend" in t:
        return "5,6"
    if "평일" in t or "weekday" in t:
        return "0,1,2,3,4"
    idxs: list[int] = []
    for tok in re.split(r"[,\s/·]+", t):
        tok = tok.strip().replace("요일", "")
        if not tok:
            continue
        if tok in _KO_TO_IDX:
            idxs.append(_KO_TO_IDX[tok])
        elif tok.isdigit() and 0 <= int(tok) <= 6:
            idxs.append(int(tok))
        else:
            en = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
            for k, v in en.items():
                if tok.startswith(k):
                    idxs.append(v)
                    break
    return ",".join(str(i) for i in sorted(set(idxs)))


def _schedule_label(days: str) -> str:
    if not days:
        return "매일"
    return "매주 " + "·".join(_WEEKDAYS[int(i)] for i in days.split(",")) + "요일"


@tool
async def schedule_recurring_reminder(
    time: str, content: str, dynamic: bool, config: RunnableConfig, weekdays: str = ""
) -> str:
    """반복되는 알림/브리핑을 등록한다. 매일 또는 특정 요일 반복을 지원한다.
    예: "매일 아침 8시에 날씨 알려줘", "매주 월요일 9시에 이번주 보스 정리",
        "매일 0시에 허재승 금주 며칠째인지 알려줘".

    time: 발송 시각 "HH:MM" 24시간제 (예 "08:00", "00:00").
    content:
      - dynamic=True: 발송 시점에 조회해서 답할 '질문' 그대로 (매번 최신값).
        예) "오늘 서울 날씨랑 삼성전자 주가 알려줘"
      - dynamic=False: 매일 그대로 보낼 고정 문구. {N}을 넣으면 등록일=1일차로
        매일 증가하는 일차로 치환. 예) "허재승 금주 {N}일차"
    dynamic: 날씨/시세/뉴스 등 최신 정보가 필요하면 True, 고정 문구면 False.
    weekdays: 반복 요일. 비우면 매일. 예: "월", "월,수,금", "주말", "평일".

    한 번만 알림은 create_reminder를 써라."""
    hhmm = _parse_hhmm(time)
    if hhmm is None:
        return "시간 형식을 해석하지 못했어. 예: 08:00, 00:00, 21시"
    content = (content or "").strip()
    if not content:
        return "알림 내용이 비어있어."
    days = _parse_weekdays(weekdays)
    now = now_kst()
    rid = boss_repo.add_recurring_reminder(
        _room(config), hhmm[0], hhmm[1], content,
        now.date().isoformat(), "tool", now.isoformat(),
        dynamic=1 if dynamic else 0, days_of_week=days,
    )
    kind = "매번 최신값 조회" if dynamic else "고정 문구"
    return (
        f"알림 등록 완료(#{rid}). {_schedule_label(days)} {hhmm[0]:02d}:{hhmm[1]:02d}, {kind}.\n"
        f"내용: {content}"
    )


@tool
async def list_recurring_reminders(config: RunnableConfig) -> str:
    """이 방에 등록된 반복 알림/브리핑 목록을 보여준다."""
    rows = boss_repo.list_recurring_reminders(_room(config))
    if not rows:
        return "등록된 반복 알림이 없어."
    out = []
    for r in rows:
        try:
            dyn = bool(r["dynamic"])
            days = r["days_of_week"] or ""
        except (KeyError, IndexError):
            dyn, days = False, ""
        tag = "[동적] " if dyn else ""
        out.append(
            f"#{r['id']} {_schedule_label(days)} {int(r['fire_hour']):02d}:{int(r['fire_minute']):02d} {tag}{r['template']}"
        )
    return "반복 알림 목록:\n" + "\n".join(out)


@tool
async def cancel_recurring_reminder(reminder_id: int, config: RunnableConfig) -> str:
    """반복 알림을 id로 해제한다. 먼저 list_recurring_reminders로 id를 확인해라."""
    if boss_repo.disable_recurring_reminder(_room(config), int(reminder_id), now_kst().isoformat()):
        return f"반복 알림 #{reminder_id}를 해제했어."
    return "해당 id의 반복 알림을 찾지 못했어."
