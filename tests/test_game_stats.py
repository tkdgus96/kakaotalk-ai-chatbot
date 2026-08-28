import tempfile
import unittest
from pathlib import Path

from app.boss.db import init_schema
from app.chat_log import add_chat_log, init_chat_log_schema
from app.config import settings
from app.services import game_stats as gs


class GameStatsTests(unittest.TestCase):
    def setUp(self):
        self._old = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'g.db'}"
        init_schema()
        init_chat_log_schema()
        ts = "2026-08-29T10:00:00"
        # alice: 3 wins, 1 fail (75%); bob: 1 win, 0 fail (100%)
        for _ in range(3):
            add_chat_log(1, "alice", "오늘의 단어 맞히기 성공!", ts)
        add_chat_log(1, "alice", "오늘의 단어 맞히기 실패!", ts)
        add_chat_log(1, "bob", "오늘의 단어 맞히기 성공!", ts)
        # bot's own summary mentioning it should be excluded
        add_chat_log(1, "온반봇", "오늘의 단어 맞히기 성공 소식이 있었어", ts)

    def tearDown(self):
        settings.boss_db_url = self._old
        self._tmp.cleanup()

    def test_winrate_and_ordering(self):
        rows = gs.stats(1, "단어")
        senders = [r["sender"] for r in rows]
        self.assertNotIn("온반봇", senders)  # bot excluded
        alice = next(r for r in rows if r["sender"] == "alice")
        self.assertEqual(alice["wins"], 3)
        self.assertEqual(alice["plays"], 4)
        self.assertAlmostEqual(alice["rate"], 0.75)
        # alice (3 wins) ranks above bob (1 win)
        self.assertEqual(senders[0], "alice")

    def test_board_and_mine(self):
        board = gs.handle_game_command(1, "alice", "!단어기록", [])
        self.assertIn("alice", board)
        self.assertIn("승률", board)
        mine = gs.handle_game_command(1, "bob", "!내기록", [])
        self.assertIn("bob", mine)
        self.assertIn("100%", mine)

    def test_unknown_command(self):
        self.assertIsNone(gs.handle_game_command(1, "a", "!보스매주", ["검마"]))


if __name__ == "__main__":
    unittest.main()
