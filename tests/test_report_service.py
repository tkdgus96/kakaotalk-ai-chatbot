import tempfile
import unittest
from pathlib import Path
from unittest import mock
from uuid import uuid4

from app.boss.db import get_conn, init_schema
from app.boss.repositories.boss_repository import BossRepository
from app.config import settings
from app.services import ops_service, report_service
from app.services.iris_service import is_self_message, parse_iris_webhook
from app.services.usage_service import record_usage


class _DbCase:
    def _setup_db(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()

    def _teardown_db(self):
        settings.boss_db_url = self._old_db_url
        self._tmp.cleanup()


class OpsEventTests(_DbCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._setup_db()

    def tearDown(self):
        self._teardown_db()

    def test_turn_and_tool_stats(self):
        ops_service.record_event("turn", "chat", 1, "a", True, 1000, "tools=2")
        ops_service.record_event("turn", "chat", 1, "b", True, 3000, "tools=0")
        ops_service.record_event("turn", "chat", 2, "c", False, 9000, "tools=1 exception")
        ops_service.record_event("tool", "web_search", 1, "a", True, 500)
        ops_service.record_event("tool", "web_search", 1, "a", True, 1500, "soft_fail")
        ops_service.record_event("tool", "get_weather", 1, "a", False, 200, "TimeoutError: x")
        ops_service.record_event("throttle", "chat", 1, "a")

        t = ops_service.turn_stats(1)
        self.assertEqual(t["count"], 3)
        self.assertEqual(t["failed"], 1)
        self.assertEqual(t["tool_calls"], 3)
        self.assertEqual(t["p50_ms"], 3000)
        self.assertEqual(t["p95_ms"], 9000)
        self.assertEqual(sorted(n for _, n in t["rooms"]), [1, 2])

        tools = {row["name"]: row for row in ops_service.tool_stats(1)}
        self.assertEqual(tools["web_search"]["calls"], 2)
        self.assertEqual(tools["web_search"]["soft_fail"], 1)
        self.assertEqual(tools["web_search"]["hard_fail"], 0)
        self.assertEqual(tools["get_weather"]["hard_fail"], 1)
        self.assertEqual(ops_service.event_count("throttle", 1), 1)

    async def test_tool_callback_records_latency_and_failures(self):
        cb = ops_service.ToolUsageCallback(1, "a")
        r1, r2 = uuid4(), uuid4()
        await cb.on_tool_start({"name": "web_search"}, "q", run_id=r1)
        await cb.on_tool_end("검색 결과: ...", run_id=r1)
        await cb.on_tool_start({"name": "get_stock_quote"}, "q", run_id=r2)
        await cb.on_tool_error(RuntimeError("boom"), run_id=r2)
        r3 = uuid4()
        await cb.on_tool_start({"name": "get_weather"}, "q", run_id=r3)
        await cb.on_tool_end("날씨 조회 실패: timeout", run_id=r3)

        self.assertEqual([c[0] for c in cb.calls], ["web_search", "get_stock_quote", "get_weather"])
        self.assertEqual([c[1] for c in cb.calls], [True, False, False])
        tools = {row["name"]: row for row in ops_service.tool_stats(1)}
        self.assertEqual(tools["get_stock_quote"]["hard_fail"], 1)
        self.assertEqual(tools["get_weather"]["soft_fail"], 1)
        with get_conn() as conn:
            row = conn.execute("SELECT detail FROM ops_event WHERE name='get_stock_quote'").fetchone()
        self.assertIn("RuntimeError", row["detail"])


class ReportBuildTests(_DbCase, unittest.TestCase):
    def setUp(self):
        self._setup_db()
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO chat_room (room_id, room_name, last_seen_at, created_at, updated_at) "
                "VALUES (1, '온기방', 'x', 'x', 'x')"
            )
        record_usage("chat", "gpt-4o", 1000, 500, room_id=1, sender="철수")
        record_usage("image_gen", "gpt-image-1", room_id=1, sender="영희")
        ops_service.record_event("turn", "chat", 1, "철수", True, 4100, "tools=1")
        ops_service.record_event("tool", "web_search", 1, "철수", True, 3200)

    def tearDown(self):
        self._teardown_db()

    def test_report_has_sections_and_room_names(self):
        text = report_service.build_report(1, detailed=False)
        self.assertIn("일일 리포트", text)
        self.assertIn("[요청]", text)
        self.assertIn("온기방: 1건", text)
        self.assertIn("4.1s", text)
        self.assertIn("[비용 (자체 추정)]", text)
        self.assertIn("web_search 1회", text)
        self.assertIn("[오답 지적 0건]", text)
        self.assertIn("[기타]", text)

    def test_weekly_title_and_admin_spend(self):
        text = report_service.build_report(7, detailed=True, admin_spend=(0.5, 2.25))
        self.assertIn("주간 리포트", text)
        self.assertIn("OpenAI 청구 기준: 24h $0.50, 7일 $2.25", text)


class DeliverAndCommandTests(_DbCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._setup_db()
        self._saved = (
            settings.report_channels, settings.report_chat_id, settings.health_self_chat_id,
            settings.admin_senders, settings.admin_room_id,
        )
        self.repo = BossRepository()

    def tearDown(self):
        (
            settings.report_channels, settings.report_chat_id, settings.health_self_chat_id,
            settings.admin_senders, settings.admin_room_id,
        ) = self._saved
        self._teardown_db()

    async def test_deliver_kakao_enqueues_once_per_day(self):
        settings.report_channels = {"kakao"}
        settings.report_chat_id = 4242
        with mock.patch.object(report_service, "fetch_openai_spend_usd", mock.AsyncMock(return_value=None)):
            r1 = await report_service.deliver_report("daily", self.repo)
            r2 = await report_service.deliver_report("daily", self.repo)
        self.assertTrue(r1["kakao"] and r2["kakao"])
        with get_conn() as conn:
            rows = conn.execute("SELECT room_id, message FROM bot_outbox").fetchall()
        self.assertEqual(len(rows), 1)  # dedup_key
        self.assertEqual(rows[0]["room_id"], 4242)
        self.assertIn("일일 리포트", rows[0]["message"])

    async def test_deliver_email_uses_alert_channel(self):
        settings.report_channels = {"email"}
        sent = []

        async def fake_alert(subject, body):
            sent.append((subject, body))
            return True

        with mock.patch.object(report_service, "send_alert", side_effect=fake_alert), \
             mock.patch.object(report_service, "fetch_openai_spend_usd", mock.AsyncMock(return_value=1.0)):
            r = await report_service.deliver_report("weekly", self.repo)
        self.assertTrue(r["email"])
        self.assertIn("주간", sent[0][0])
        self.assertIn("OpenAI 청구 기준", sent[0][1])

    async def test_kakao_skipped_without_chat_id(self):
        settings.report_channels = {"kakao"}
        settings.report_chat_id = 0
        settings.health_self_chat_id = 0
        with mock.patch.object(report_service, "fetch_openai_spend_usd", mock.AsyncMock(return_value=None)):
            r = await report_service.deliver_report("daily", self.repo)
        self.assertFalse(r["kakao"])

    async def test_command_permissions_and_period(self):
        settings.admin_senders = {"김상현"}
        settings.admin_room_id = 0
        settings.report_chat_id = 0
        settings.health_self_chat_id = 0
        self.assertIsNone(await report_service.handle_report_command(1, "철수", "!요약", []))
        denied = await report_service.handle_report_command(1, "철수", "!리포트", [])
        self.assertIn("관리자", denied)
        ok = await report_service.handle_report_command(1, "김상현", "!리포트", ["주간"])
        self.assertIn("주간 리포트", ok)
        ok2 = await report_service.handle_report_command(1, "김상현", "!리포트", [])
        self.assertIn("일일 리포트", ok2)
        settings.admin_room_id = 77
        self.assertIn("리포트", await report_service.handle_report_command(77, "누구나", "!리포트", []))
        self.assertIn("!리포트", await report_service.handle_report_command(1, "철수", "!리포트도움", []))

    def test_self_chat_commands_pass_echo_filter(self):
        settings.report_chat_id = 4242
        bot = sorted(settings.iris_self_names)[0]
        cmd = parse_iris_webhook({"msg": "!리포트", "room": "나", "sender": bot, "json": {"chat_id": 4242}})
        echo = parse_iris_webhook({"msg": "📊 온반봇 일일 리포트", "room": "나", "sender": bot, "json": {"chat_id": 4242}})
        other = parse_iris_webhook({"msg": "!리포트", "room": "방", "sender": bot, "json": {"chat_id": 1}})
        self.assertFalse(is_self_message(cmd))
        self.assertTrue(is_self_message(echo))
        self.assertTrue(is_self_message(other))


if __name__ == "__main__":
    unittest.main()
