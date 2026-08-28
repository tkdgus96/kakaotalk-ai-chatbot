import tempfile
import unittest
from pathlib import Path

from app.boss.db import get_conn, init_schema
from app.config import settings
from app.graph import _needs_fresh_tool
from app.services import feedback_service as fb


class ToolForceTests(unittest.TestCase):
    def test_fresh_questions_force_tool(self):
        self.assertTrue(_needs_fresh_tool("!삼성전자 주가 얼마야"))
        self.assertTrue(_needs_fresh_tool("!오늘 날씨"))
        self.assertTrue(_needs_fresh_tool("!엔비디아 최근 실적"))
        self.assertTrue(_needs_fresh_tool("!비트코인 시세"))

    def test_evergreen_does_not_force(self):
        self.assertFalse(_needs_fresh_tool("!물의 끓는점은"))
        self.assertFalse(_needs_fresh_tool("!파이썬 리스트랑 튜플 차이"))
        self.assertFalse(_needs_fresh_tool("!ㅋㅋ 뭐하냐"))


class CorrectionDetectTests(unittest.TestCase):
    def test_is_correction(self):
        self.assertTrue(fb.is_correction("아니 그거 틀렸어"))
        self.assertTrue(fb.is_correction("이미 나왔는데.."))
        self.assertTrue(fb.is_correction("[a]: 그게 아니라니까"))
        self.assertFalse(fb.is_correction("고마워 정확하네"))
        self.assertFalse(fb.is_correction("오늘 날씨 알려줘"))


class FeedbackLogTests(unittest.TestCase):
    def setUp(self):
        self._old = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'f.db'}"
        init_schema()

    def tearDown(self):
        settings.boss_db_url = self._old
        self._tmp.cleanup()

    def test_record_and_recent(self):
        fb.record_correction(1, "김상현", "이미 나왔는데", "엔비디아 실적?", "아직 발표 전이야")
        rows = fb.recent_feedback(1)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["sender"], "김상현")
        with get_conn() as c:
            n = c.execute("SELECT COUNT(*) c FROM feedback_log").fetchone()["c"]
        self.assertEqual(n, 1)


if __name__ == "__main__":
    unittest.main()
