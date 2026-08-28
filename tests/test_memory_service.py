import tempfile
import unittest
from pathlib import Path

from app.boss.db import init_schema
from app.config import settings
from app.services import memory_service as ms


class MemoryOptOutTests(unittest.TestCase):
    def setUp(self):
        self._old = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'm.db'}"
        init_schema()

    def tearDown(self):
        settings.boss_db_url = self._old
        self._tmp.cleanup()

    def test_optout_roundtrip(self):
        self.assertFalse(ms.is_opted_out(1, "김상현"))
        self.assertIn("저장하지 않을게", ms.handle_memory_command(1, "김상현", "!기억끄기", []))
        self.assertTrue(ms.is_opted_out(1, "김상현"))
        self.assertIn("다시 기억", ms.handle_memory_command(1, "김상현", "!기억켜기", []))
        self.assertFalse(ms.is_opted_out(1, "김상현"))

    def test_help_and_unknown(self):
        self.assertIn("기억", ms.handle_memory_command(1, "a", "!기억도움", []))
        self.assertIsNone(ms.handle_memory_command(1, "a", "!보스매주", ["검마"]))

    def test_isolated_per_room_sender(self):
        ms.set_optout(1, "a", True)
        self.assertTrue(ms.is_opted_out(1, "a"))
        self.assertFalse(ms.is_opted_out(2, "a"))
        self.assertFalse(ms.is_opted_out(1, "b"))


if __name__ == "__main__":
    unittest.main()
