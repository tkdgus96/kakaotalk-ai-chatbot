"""Deterministic date/time calculations (KST) — weekday, D-day, date math."""

import re
from datetime import datetime, timedelta

from langchain_core.tools import tool

from app.boss.utils.week import now_kst

_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]


def _parse_date(s: str) -> datetime | None:
    s = s.strip()
    if s in ("오늘", "today"):
        return now_kst().replace(hour=0, minute=0, second=0, microsecond=0)
    if s in ("내일", "tomorrow"):
        return now_kst().replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1)
    if s in ("어제", "yesterday"):
        return now_kst().replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=1)
    m = re.search(r"(20\d{2})\s*[-./년]\s*(\d{1,2})\s*[-./월]\s*(\d{1,2})", s)
    if m:
        try:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    return None


@tool
async def date_calculate(operation: str, date: str = "오늘", date2: str = "", days: int = 0) -> str:
    """날짜/요일/D-day를 정확히 계산한다. 직접 세지 말고 이 도구를 써라. 시간대는 KST.

    operation:
      - "weekday": date의 요일
      - "diff": date 와 date2 사이 일수 (D-day 계산)
      - "add": date 에 days(음수 가능)를 더한 날짜와 요일
    date/date2: "오늘","내일","어제" 또는 "2026-09-01"/"2026년 9월 1일" 형식.
    days: add 연산에서 더할 일수."""
    d1 = _parse_date(date)
    if d1 is None:
        return f"날짜를 해석하지 못했어: {date}"
    if operation == "weekday":
        return f"{d1:%Y-%m-%d}은 {_WEEKDAYS[d1.weekday()]}요일이야."
    if operation == "add":
        d = d1 + timedelta(days=days)
        return f"{d1:%Y-%m-%d} 기준 {days:+d}일은 {d:%Y-%m-%d} ({_WEEKDAYS[d.weekday()]}요일)이야."
    if operation == "diff":
        d2 = _parse_date(date2)
        if d2 is None:
            return f"두 번째 날짜를 해석하지 못했어: {date2}"
        delta = (d2.date() - d1.date()).days
        return f"{d1:%Y-%m-%d} 부터 {d2:%Y-%m-%d} 까지는 {delta}일이야."
    return "operation은 weekday/diff/add 중 하나여야 해."
