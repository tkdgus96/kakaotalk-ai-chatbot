from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
DAY_MAP = {
    "월": 0,
    "월요일": 0,
    "화": 1,
    "화요일": 1,
    "수": 2,
    "수요일": 2,
    "목": 3,
    "목요일": 3,
    "금": 4,
    "금요일": 4,
    "토": 5,
    "토요일": 5,
    "일": 6,
    "일요일": 6,
}
DAY_LABEL = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]


class DayTimeParseError(ValueError):
    pass


def now_kst() -> datetime:
    return datetime.now(tz=KST)


def get_week_start_thursday(base: datetime | None = None) -> datetime:
    dt = base or now_kst()
    dt = dt.astimezone(KST)
    days_from_thu = (dt.weekday() - 3) % 7
    start = dt - timedelta(days=days_from_thu)
    return start.replace(hour=0, minute=0, second=0, microsecond=0)


def parse_day_time(day_raw: str, time_raw: str) -> tuple[int, int, int]:
    day = DAY_MAP.get(day_raw.strip())
    if day is None:
        raise DayTimeParseError("요일 형식이 올바르지 않습니다.")

    parts = time_raw.strip().split(":")
    if len(parts) != 2 or not parts[0].isdigit() or not parts[1].isdigit():
        raise DayTimeParseError("시간 형식은 HH:mm 이어야 합니다.")

    hh, mm = int(parts[0]), int(parts[1])
    if hh < 0 or hh > 23 or mm < 0 or mm > 59:
        raise DayTimeParseError("시간 범위가 올바르지 않습니다.")
    return day, hh, mm


def schedule_datetime_for_current_cycle(day_idx: int, hh: int, mm: int, base: datetime | None = None) -> datetime:
    week_start = get_week_start_thursday(base)
    offset = (day_idx - 3) % 7
    return week_start + timedelta(days=offset, hours=hh, minutes=mm)
