import tempfile
import unittest
from pathlib import Path

from app.boss.db import init_schema
from app.boss.repositories.boss_repository import BossRepository
from app.boss.services.boss_service import BossService
from app.boss.services.command_parser import parse_command
from app.config import settings
from app.services import chat_service


class BossDisableTests(unittest.TestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()
        self.repo = BossRepository()
        self.service = BossService(self.repo)

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        self._tmp.cleanup()

    def test_disable_weekly_boss_hides_from_weekly_list_and_allows_reregister(self):
        self.service.register_weekly_boss(1, "검마")

        disabled = self.service.disable_weekly_boss(1, "검마")
        weekly = self.service.list_week_bosses(1)

        self.assertIn("[보스 해제 완료]", disabled)
        self.assertNotIn("검마 -", weekly)
        self.assertIsNone(self.repo.find_weekly_boss(1, "검마"))

        reregistered = self.service.register_weekly_boss(1, "검마")

        self.assertIn("[보스 등록 완료]", reregistered)
        self.assertIsNotNone(self.repo.find_weekly_boss(1, "검마"))

    def test_disable_unknown_weekly_boss_returns_user_safe_message(self):
        answer = self.service.disable_weekly_boss(1, "검마")

        self.assertIn("[해제 실패]", answer)

    def test_parser_accepts_boss_disable_command_without_bang(self):
        parsed = parse_command("보스해제 검마")

        self.assertIsNotNone(parsed)
        self.assertEqual(parsed.name, "!보스해제")
        self.assertEqual(parsed.args, ["검마"])

    def test_handle_boss_disable_command(self):
        old_service = chat_service.boss_service
        chat_service.boss_service = self.service
        try:
            self.service.register_weekly_boss(1, "검마")
            answer = chat_service.handle_boss_command(1, "tester", "!보스해제", ["검마"])
        finally:
            chat_service.boss_service = old_service

        self.assertIn("[보스 해제 완료]", answer)


if __name__ == "__main__":
    unittest.main()
