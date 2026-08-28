import tempfile
import unittest
from pathlib import Path

from app.boss.db import init_schema
from app.config import settings
from app.services import usage_service as us


class UsageServiceTests(unittest.TestCase):
    def setUp(self):
        self._old = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'u.db'}"
        init_schema()

    def tearDown(self):
        settings.boss_db_url = self._old
        self._tmp.cleanup()

    def test_token_cost_and_spend(self):
        us.record_usage("chat", "gpt-4o", 1_000_000, 1_000_000)  # $2.50 + $10.00
        us.record_image_usage("gpt-image-1-mini")  # $0.005
        total = us.spend_since_usd(1)
        self.assertAlmostEqual(total, 12.505, places=3)
        kinds = dict(us.spend_breakdown(1))
        self.assertIn("chat", kinds)
        self.assertIn("image_gen", kinds)

    def test_rate_limit_blocks_after_limit(self):
        self.assertTrue(us.allow("image_gen:bob", "2026-08-29", 2))
        self.assertTrue(us.allow("image_gen:bob", "2026-08-29", 2))
        self.assertFalse(us.allow("image_gen:bob", "2026-08-29", 2))  # 3rd blocked
        # different scope unaffected
        self.assertTrue(us.allow("image_gen:alice", "2026-08-29", 2))

    def test_limit_zero_disables(self):
        for _ in range(100):
            self.assertTrue(us.allow("chat:1", "2026-08-29T21:30", 0))


if __name__ == "__main__":
    unittest.main()
