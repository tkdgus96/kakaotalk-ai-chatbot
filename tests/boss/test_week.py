import unittest
from datetime import datetime
from zoneinfo import ZoneInfo

from app.boss.utils.week import get_week_start_thursday, parse_day_time, schedule_datetime_for_current_cycle


class TestWeekUtils(unittest.TestCase):
    def test_week_start(self):
        base = datetime(2026, 5, 20, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))  # Wed
        start = get_week_start_thursday(base)
        self.assertEqual(start.date().isoformat(), "2026-05-14")

    def test_parse_day_short(self):
        day, hh, mm = parse_day_time("토", "22:00")
        self.assertEqual((day, hh, mm), (5, 22, 0))

    def test_schedule_calc(self):
        base = datetime(2026, 5, 20, 10, 0, tzinfo=ZoneInfo("Asia/Seoul"))
        dt = schedule_datetime_for_current_cycle(5, 22, 0, base)
        self.assertEqual(dt.date().isoformat(), "2026-05-16")
        self.assertEqual(dt.strftime("%H:%M"), "22:00")
