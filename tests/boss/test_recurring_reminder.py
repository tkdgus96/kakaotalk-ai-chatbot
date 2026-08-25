import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from app.boss.db import get_conn, init_schema
from app.boss.repositories.boss_repository import BossRepository
from app.boss.services.command_parser import parse_command
from app.boss.services.scheduler import BossScheduler, render_recurring_message
from app.config import settings
from app.services.reminder_service import handle_recurring_command, parse_fire_time


class RecurringReminderTests(unittest.TestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._old_allowed = settings.allowed_rooms
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        settings.allowed_rooms = {1}
        init_schema()
        self.repo = BossRepository()
        self.repo.upsert_room(1, "테스트방", "2026-08-25T00:00:00")

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        settings.allowed_rooms = self._old_allowed
        self._tmp.cleanup()

    def _outbox_rows(self):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM bot_outbox ORDER BY id").fetchall()

    def test_parse_fire_time_formats(self):
        self.assertEqual(parse_fire_time("00:00"), (0, 0))
        self.assertEqual(parse_fire_time("9:30"), (9, 30))
        self.assertEqual(parse_fire_time("21시"), (21, 0))
        self.assertEqual(parse_fire_time("21시30분"), (21, 30))
        self.assertIsNone(parse_fire_time("25:00"))
        self.assertIsNone(parse_fire_time("내일"))

    def test_parser_accepts_daily_command(self):
        parsed = parse_command("!매일 00:00 허재승 금주 {N}일차")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name, "!매일")
        self.assertEqual(parsed.args, ["00:00", "허재승", "금주", "{N}일차"])

    def test_render_day_count_starts_at_one(self):
        now = datetime(2026, 8, 25, 0, 0)
        self.assertEqual(
            render_recurring_message("허재승 금주 {N}일차", "2026-08-25", now),
            "허재승 금주 1일차",
        )
        self.assertEqual(
            render_recurring_message("허재승 금주 {N}일차", "2026-08-23", now),
            "허재승 금주 3일차",
        )
        self.assertEqual(render_recurring_message("템플릿 변수 없음", "2026-08-25", now), "템플릿 변수 없음")

    def test_register_list_disable_roundtrip(self):
        answer = handle_recurring_command(1, "김상현", "!매일", ["00:00", "허재승", "금주", "{N}일차"])
        self.assertIn("등록 완료", answer)
        self.assertIn("1일차", answer)

        listed = handle_recurring_command(1, "김상현", "!매일목록", [])
        self.assertIn("#1", listed)
        self.assertIn("00:00", listed)

        removed = handle_recurring_command(1, "김상현", "!매일해제", ["1"])
        self.assertIn("해제", removed)
        self.assertIn("없습니다", handle_recurring_command(1, "김상현", "!매일목록", []))

    def test_unknown_command_returns_none(self):
        self.assertIsNone(handle_recurring_command(1, "김상현", "!보스매주", ["검마"]))

    def test_scheduler_enqueues_once_per_day_with_day_count(self):
        start = (datetime(2026, 8, 25) - timedelta(days=2)).date().isoformat()
        self.repo.add_recurring_reminder(1, 0, 0, "허재승 금주 {N}일차", start, "김상현", "2026-08-23T00:00:00")
        scheduler = BossScheduler(self.repo)

        before_fire = datetime(2026, 8, 24, 23, 59)
        scheduler._run_recurring_reminders(before_fire)
        self.assertEqual(len(self._outbox_rows()), 1)  # 8/24 fire time already passed

        now = datetime(2026, 8, 25, 0, 1)
        scheduler._run_recurring_reminders(now)
        scheduler._run_recurring_reminders(now + timedelta(hours=3))  # same-day re-tick dedups

        rows = self._outbox_rows()
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[-1]["message"], "허재승 금주 3일차")
        self.assertEqual(rows[-1]["status"], "PENDING")

    def test_scheduler_skips_rooms_not_allowed(self):
        settings.allowed_rooms = {99}
        self.repo.add_recurring_reminder(1, 0, 0, "메시지", "2026-08-25", "김상현", "2026-08-25T00:00:00")
        BossScheduler(self.repo)._run_recurring_reminders(datetime(2026, 8, 25, 1, 0))

        self.assertEqual(len(self._outbox_rows()), 0)


if __name__ == "__main__":
    unittest.main()
