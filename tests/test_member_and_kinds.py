import tempfile
import unittest
from pathlib import Path

from app.boss.db import get_conn, init_schema
from app.config import settings
from app.services import iris_service, member_service
from app.services.iris_service import (
    QuotedMessage,
    classify_message,
    parse_iris_webhook,
    resolve_quoted_sender,
    strip_bot_mention,
)


class _DbCase:
    def _setup_db(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()
        from app import chat_log

        chat_log.init_chat_log_schema()
        self.chat_log = chat_log

    def _teardown_db(self):
        settings.boss_db_url = self._old_db_url
        self._tmp.cleanup()


class ClassifyTests(unittest.TestCase):
    def test_kinds_and_placeholders(self):
        self.assertEqual(classify_message(1, "안녕", None), ("text", "안녕"))
        self.assertEqual(classify_message(12, "이모티콘", {}), ("emoticon", ""))
        self.assertEqual(classify_message(20, "ㅋㅋ 이거봐", {}), ("text", "ㅋㅋ 이거봐"))
        self.assertEqual(classify_message(18, "", {"name": "보고서.pdf", "size": 1}), ("file", "(파일: 보고서.pdf)"))
        self.assertEqual(classify_message(3, "동영상", {}), ("video", "(동영상)"))
        self.assertEqual(classify_message(5, "", {}), ("audio", "(음성 메시지)"))
        self.assertEqual(classify_message(16, "", {"t": "강남역", "a": "서울 강남구"}), ("location", "(위치 공유: 강남역 서울 강남구)"))
        self.assertEqual(classify_message(0, '{"feedType":1}', {}), ("feed", '{"feedType":1}'))
        self.assertEqual(classify_message(2, "사진", {"url": "x"}), ("photo", "사진"))
        self.assertEqual(classify_message(None, "hi", None), ("text", "hi"))

    def test_parse_sets_kind_sent_at_and_mentions(self):
        msg = parse_iris_webhook(
            {
                "msg": "@영희 이거 봤어?",
                "room": "방",
                "sender": "철수",
                "json": {
                    "chat_id": 1,
                    "user_id": "77",
                    "type": "1",
                    "created_at": "1757462400",  # 2025-09-10 09:00:00 KST
                    "attachment": '{"mentions":[{"user_id":"55","at":[0],"len":2}]}',
                },
            }
        )
        self.assertEqual(msg.kind, "text")
        self.assertEqual(msg.user_id, 77)
        self.assertEqual(msg.mentioned_user_ids, (55,))
        self.assertEqual(msg.sent_at.strftime("%Y-%m-%d %H:%M"), "2025-09-10 09:00")

    def test_parse_marks_ignored_kinds(self):
        emo = parse_iris_webhook({"msg": "이모티콘", "room": "방", "sender": "a", "json": {"chat_id": 1, "type": 12}})
        self.assertEqual(emo.kind, "emoticon")
        feed = parse_iris_webhook({"msg": "{}", "room": "방", "sender": "a", "json": {"chat_id": 1, "type": 0}})
        self.assertEqual(feed.kind, "feed")
        file_msg = parse_iris_webhook(
            {"msg": "", "room": "방", "sender": "a", "json": {"chat_id": 1, "type": 18, "attachment": {"name": "a.zip"}}}
        )
        self.assertEqual((file_msg.kind, file_msg.msg), ("file", "(파일: a.zip)"))

    def test_strip_bot_mention(self):
        bot = sorted(settings.iris_self_names)[0]
        self.assertEqual(strip_bot_mention(f"@{bot} 오늘 날씨"), "오늘 날씨")
        self.assertEqual(strip_bot_mention(f"오늘 날씨 @{bot}"), "오늘 날씨")
        self.assertEqual(strip_bot_mention("그냥 텍스트"), "그냥 텍스트")


class MemberServiceTests(_DbCase, unittest.TestCase):
    def setUp(self):
        self._setup_db()

    def tearDown(self):
        self._teardown_db()

    def test_touch_and_rename_detection(self):
        self.assertIsNone(member_service.touch_member(1, 55, "영희"))
        self.assertIsNone(member_service.touch_member(1, 55, "영희"))
        self.assertEqual(member_service.touch_member(1, 55, "영희2"), "영희")
        self.assertEqual(member_service.name_for(1, 55), "영희2")
        self.assertEqual(member_service.names_for(1, [55, 999]), ["영희2"])
        with get_conn() as conn:
            row = conn.execute("SELECT old_name, new_name FROM room_member_alias").fetchone()
        self.assertEqual((row["old_name"], row["new_name"]), ("영희", "영희2"))

    def test_migrate_rewrites_name_keys(self):
        from app.services.memory_service import set_optout, is_opted_out

        self.chat_log.add_chat_log(1, "영희", "오늘의 단어 맞히기 성공!", "2026-09-10T10:00:00")
        set_optout(1, "영희", True)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO feedback_log (room_id, sender, correction, created_at) VALUES (1,'영희','틀렸어','x')"
            )
        counts = member_service.migrate_sender_name(1, "영희", "영희2")
        self.assertEqual(counts["chat_log_fts"], 1)
        self.assertEqual(counts["feedback_log"], 1)
        self.assertTrue(is_opted_out(1, "영희2"))
        self.assertFalse(is_opted_out(1, "영희"))
        with get_conn() as conn:
            row = conn.execute("SELECT sender FROM chat_log_fts").fetchone()
        self.assertEqual(row["sender"], "영희2")

    def test_quoted_sender_prefers_member_table(self):
        member_service.touch_member(1, 55, "영희")
        q = QuotedMessage(text="아무 말", src_user_id=55)
        self.assertEqual(resolve_quoted_sender(1, q, bot_user_id=None), "영희")
        q2 = QuotedMessage(text="(사진)", src_type=2, src_user_id=55)
        self.assertEqual(resolve_quoted_sender(1, q2, bot_user_id=None), "영희")


class WebhookFlowTests(_DbCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._setup_db()
        self._old_get_bot = iris_service.get_bot_user_id
        self._old_client = iris_service.IrisClient
        import app.services.chat_service as chat_service

        self.chat_service = chat_service
        self._old_handle = chat_service.handle_chat
        self.captured = []

        async def fake_handle_chat(data):
            self.captured.append(data)
            return {"answer": ""}

        async def fake_bot_id(client=None):
            return 999

        class Client:
            def __init__(self, *a, **k):
                pass

            async def send_text(self, chat_id, message):
                return True

        chat_service.handle_chat = fake_handle_chat
        iris_service.get_bot_user_id = fake_bot_id
        iris_service.IrisClient = Client

    def tearDown(self):
        self.chat_service.handle_chat = self._old_handle
        iris_service.get_bot_user_id = self._old_get_bot
        iris_service.IrisClient = self._old_client
        self._teardown_db()

    async def test_emoticon_and_feed_are_dropped(self):
        r1 = await iris_service.handle_iris_webhook(
            {"msg": "이모티콘", "room": "방", "sender": "철수", "json": {"chat_id": 1, "type": 12}}
        )
        r2 = await iris_service.handle_iris_webhook(
            {"msg": '{"feedType":4}', "room": "방", "sender": "철수", "json": {"chat_id": 1, "type": 0}}
        )
        self.assertEqual(r1.get("ignored"), "emoticon")
        self.assertEqual(r2.get("ignored"), "feed")
        self.assertEqual(self.captured, [])

    async def test_file_becomes_placeholder_non_command(self):
        await iris_service.handle_iris_webhook(
            {"msg": "", "room": "방", "sender": "철수",
             "json": {"chat_id": 1, "type": 18, "attachment": {"name": "!중요.pdf"}}}
        )
        data = self.captured[0]
        self.assertEqual(data.msg, "(파일: !중요.pdf)")
        self.assertFalse(data.is_command)
        self.assertEqual(data.msg_kind, "file")

    async def test_mention_of_bot_is_command_and_names_resolved(self):
        bot = sorted(settings.iris_self_names)[0]
        member_service.touch_member(1, 55, "영희")
        await iris_service.handle_iris_webhook(
            {
                "msg": f"@{bot} @영희 생일 언제였지",
                "room": "방",
                "sender": "철수",
                "json": {
                    "chat_id": 1, "user_id": 77, "type": 1, "created_at": 1757462400,
                    "attachment": {"mentions": [{"user_id": 999, "at": [0]}, {"user_id": 55, "at": [1]}]},
                },
            }
        )
        data = self.captured[0]
        self.assertTrue(data.is_command)
        self.assertEqual(data.msg, "@영희 생일 언제였지")
        self.assertEqual(data.mentioned_names, ["영희"])
        self.assertEqual(data.sender_id, 77)
        self.assertTrue(data.sent_at.startswith("2025-09-10T09:00"))
        self.assertEqual(member_service.name_for(1, 77), "철수")

    async def test_rename_migrates_chat_log(self):
        self.chat_log.add_chat_log(1, "철수", "오늘의 단어 맞히기 성공!", "2026-09-10T10:00:00")
        member_service.touch_member(1, 77, "철수")
        await iris_service.handle_iris_webhook(
            {"msg": "안녕", "room": "방", "sender": "철수_new", "json": {"chat_id": 1, "user_id": 77, "type": 1}}
        )
        with get_conn() as conn:
            row = conn.execute("SELECT sender FROM chat_log_fts").fetchone()
        self.assertEqual(row["sender"], "철수_new")


if __name__ == "__main__":
    unittest.main()
