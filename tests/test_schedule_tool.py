import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from app.boss.db import get_conn, init_schema
from app.boss.repositories.boss_repository import BossRepository
from app.boss.services.scheduler import BossScheduler
from app.config import settings
from app.tools import schedule as sch


class WeekdayParseTests(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(sch._parse_weekdays(""), "")
        self.assertEqual(sch._parse_weekdays("매일"), "")
        self.assertEqual(sch._parse_weekdays("월,수,금"), "0,2,4")
        self.assertEqual(sch._parse_weekdays("주말"), "5,6")
        self.assertEqual(sch._parse_weekdays("평일"), "0,1,2,3,4")
        self.assertEqual(sch._parse_weekdays("월요일"), "0")

    def test_label(self):
        self.assertEqual(sch._schedule_label(""), "매일")
        self.assertEqual(sch._schedule_label("0"), "매주 월요일")
        self.assertEqual(sch._schedule_label("5,6"), "매주 토·일요일")


class ScheduleRegisterTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old = settings.boss_db_url
        self._old_rooms = settings.allowed_rooms
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 's.db'}"
        settings.allowed_rooms = {1}
        init_schema()
        self.repo = BossRepository()
        self.repo.upsert_room(1, "방", "2026-08-29T00:00:00")

    def tearDown(self):
        settings.boss_db_url = self._old
        settings.allowed_rooms = self._old_rooms
        self._tmp.cleanup()

    async def test_register_dynamic_and_weekly(self):
        cfg = {"configurable": {"room_id": 1}}
        out = await sch.schedule_recurring_reminder.ainvoke(
            {"time": "08:00", "content": "오늘 서울 날씨 알려줘", "dynamic": True, "weekdays": "월,수"},
            config=cfg,
        )
        self.assertIn("등록 완료", out)
        self.assertIn("월·수", out)
        rows = self.repo.list_recurring_reminders(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["dynamic"], 1)
        self.assertEqual(rows[0]["days_of_week"], "0,2")

    async def test_weekday_filter_in_scheduler(self):
        # register weekly Monday static reminder
        self.repo.add_recurring_reminder(1, 9, 0, "월요일 알림", "2026-08-29", "tool",
                                         "2026-08-29T00:00:00", dynamic=0, days_of_week="0")
        s = BossScheduler(self.repo)
        # Tuesday 2026-09-01 -> should NOT fire
        s._run_recurring_reminders(datetime(2026, 9, 1, 9, 0))
        with get_conn() as c:
            n = c.execute("SELECT COUNT(*) c FROM bot_outbox").fetchone()["c"]
        self.assertEqual(n, 0)
        # Monday 2026-08-31 -> should fire
        s._run_recurring_reminders(datetime(2026, 8, 31, 9, 0))
        with get_conn() as c:
            n = c.execute("SELECT COUNT(*) c FROM bot_outbox").fetchone()["c"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
