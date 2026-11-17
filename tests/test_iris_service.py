import tempfile
import unittest
from pathlib import Path

from app.boss.db import get_conn, init_schema
from app.boss.repositories.boss_repository import BossRepository
from app.config import settings
from app.services import iris_service
from app.services.iris_service import (
    QuotedMessage,
    chat_id_for_room,
    is_command_message,
    is_self_message,
    parse_iris_webhook,
    resolve_quoted_sender,
    resolve_room,
    seed_room_map_from_env,
    send_pending_outbox_once,
)


class FakeIrisClient:
    def __init__(self, ok: bool = True):
        self.ok = ok
        self.sent: list[tuple[int, str]] = []

    async def send_text(self, chat_id: int, message: str) -> bool:
        self.sent.append((chat_id, message))
        return self.ok


class ParseWebhookTests(unittest.TestCase):
    def test_parses_standard_payload(self):
        msg = parse_iris_webhook(
            {
                "msg": "!매일목록",
                "room": "온기방",
                "sender": "김상현",
                "json": {"_id": "1", "chat_id": "418123456789", "user_id": "99", "message": "!매일목록"},
            }
        )

        self.assertIsNotNone(msg)
        self.assertEqual(msg.chat_id, 418123456789)
        self.assertEqual(msg.room_name, "온기방")
        self.assertEqual(msg.sender, "김상현")
        self.assertEqual(msg.msg, "!매일목록")

    def test_parses_json_field_as_string(self):
        msg = parse_iris_webhook(
            {"msg": "안녕", "room": "방", "sender": "a", "json": '{"chat_id": 7}'}
        )

        self.assertIsNotNone(msg)
        self.assertEqual(msg.chat_id, 7)

    def test_rejects_missing_chat_id_or_empty_msg(self):
        self.assertIsNone(parse_iris_webhook({"msg": "hi", "sender": "a", "json": {}}))
        self.assertIsNone(
            parse_iris_webhook({"msg": " ", "sender": "a", "json": {"chat_id": 1}})
        )
        self.assertIsNone(parse_iris_webhook("json 아님"))

    def test_command_and_self_detection(self):
        # Default trigger is "!" (and fullwidth "！") only.
        self.assertTrue(is_command_message("!보스도움"))
        self.assertTrue(is_command_message("！날씨"))
        self.assertFalse(is_command_message("온반봇 오늘 날씨 어때"))
        self.assertFalse(is_command_message("그냥 잡담"))

        payload = {"msg": "답장", "room": "방", "sender": "온반봇", "json": {"chat_id": 1}}
        self.assertTrue(is_self_message(parse_iris_webhook(payload)))


class QuoteParseTests(unittest.TestCase):
    def test_parses_reply_attachment(self):
        msg = parse_iris_webhook(
            {
                "msg": "!이거 무슨 뜻이야",
                "room": "방",
                "sender": "철수",
                "json": {
                    "chat_id": 1,
                    "user_id": "77",
                    "type": "26",
                    "attachment": '{"attach_only":false,"attach_type":1,"src_logId":"9001",'
                    '"src_message":"어제 그 얘기 ㄹㅇ 억까","src_type":1,"src_userId":"55"}',
                },
            }
        )

        self.assertIsNotNone(msg.quoted)
        self.assertEqual(msg.quoted.text, "어제 그 얘기 ㄹㅇ 억까")
        self.assertEqual(msg.quoted.src_user_id, 55)
        self.assertEqual(msg.quoted.src_log_id, 9001)
        self.assertEqual(msg.user_id, 77)

    def test_reply_to_photo_marks_placeholder(self):
        msg = parse_iris_webhook(
            {
                "msg": "!이 사진 누구야",
                "room": "방",
                "sender": "철수",
                "json": {"chat_id": 1, "type": 26, "attachment": {"src_type": 2, "src_userId": 5, "src_message": ""}},
            }
        )
        self.assertEqual(msg.quoted.text, "(사진)")

    def test_plain_message_has_no_quote(self):
        msg = parse_iris_webhook(
            {"msg": "안녕", "room": "방", "sender": "a", "json": {"chat_id": 7, "type": 1, "attachment": "{}"}}
        )
        self.assertIsNone(msg.quoted)


class QuotedSenderTests(unittest.TestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()
        from app import chat_log

        chat_log.init_chat_log_schema()
        self.chat_log = chat_log

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        self._tmp.cleanup()

    def test_bot_id_maps_to_bot_name(self):
        q = QuotedMessage(text="오늘 서울 20도야", src_user_id=999)
        self.assertEqual(resolve_quoted_sender(1, q, bot_user_id=999), sorted(settings.iris_self_names)[0])

    def test_looks_up_sender_from_chat_log(self):
        self.chat_log.add_chat_log(1, "영희", "어제 그 얘기 ㄹㅇ 억까", "2026-09-10T10:00:00")
        q = QuotedMessage(text="어제 그 얘기 ㄹㅇ 억까", src_user_id=55)
        self.assertEqual(resolve_quoted_sender(1, q, bot_user_id=999), "영희")

    def test_unknown_when_no_match(self):
        q = QuotedMessage(text="아무도 안 한 말", src_user_id=55)
        self.assertEqual(resolve_quoted_sender(1, q, bot_user_id=None), "")


class ReplyToBotWebhookTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()
        self._old_get_bot = iris_service.get_bot_user_id
        self._old_client = iris_service.IrisClient

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        iris_service.get_bot_user_id = self._old_get_bot
        iris_service.IrisClient = self._old_client
        self._tmp.cleanup()

    async def test_reply_to_bot_becomes_command_with_quote(self):
        import app.services.chat_service as chat_service

        captured = {}

        async def fake_handle_chat(data):
            captured["data"] = data
            return {"answer": "ok"}

        async def fake_bot_id(client=None):
            return 999

        class Client:
            def __init__(self, *a, **k):
                pass

            async def send_text(self, chat_id, message):
                return True

        old_handle = chat_service.handle_chat
        chat_service.handle_chat = fake_handle_chat
        iris_service.get_bot_user_id = fake_bot_id
        iris_service.IrisClient = Client
        try:
            await iris_service.handle_iris_webhook(
                {
                    "msg": "그거 어디서 봤어?",
                    "room": "방",
                    "sender": "철수",
                    "json": {
                        "chat_id": 1,
                        "type": 26,
                        "attachment": {"src_message": "오늘 서울 20도야", "src_type": 1, "src_userId": 999},
                    },
                }
            )
        finally:
            chat_service.handle_chat = old_handle

        data = captured["data"]
        self.assertTrue(data.is_command)
        self.assertEqual(data.quoted_text, "오늘 서울 20도야")
        self.assertEqual(data.quoted_sender, sorted(settings.iris_self_names)[0])


class FormatQuoteTests(unittest.TestCase):
    def test_format_quote_line(self):
        from app.models import KakaoMsg
        from app.services.chat_service import _format_quote

        data = KakaoMsg(room_id=1, room="방", msg="!이거 뭐야", sender="철수", is_command=True,
                        quoted_text="어제 그 얘기", quoted_sender="영희")
        self.assertEqual(_format_quote(data), "(답장 대상 [영희]: 어제 그 얘기)")
        data2 = KakaoMsg(room_id=1, room="방", msg="!이거 뭐야", sender="철수", is_command=True,
                         quoted_text="x" * 400)
        out = _format_quote(data2)
        self.assertTrue(out.startswith("(답장 대상 [알 수 없음]: "))
        self.assertTrue(out.endswith("…)"))
        self.assertEqual(_format_quote(KakaoMsg(room_id=1, room="방", msg="!a", sender="s", is_command=True)), "")


class RoomMapTests(unittest.TestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._old_seed = settings.iris_room_map_seed
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        settings.iris_room_map_seed = self._old_seed
        self._tmp.cleanup()

    def test_unknown_chat_id_gets_identity_mapping(self):
        room_id, room_name = resolve_room(555, "새로운방")

        self.assertEqual(room_id, 555)
        self.assertEqual(room_name, "새로운방")
        self.assertEqual(chat_id_for_room(555), 555)

    def test_seed_maps_legacy_room_and_overrides_identity(self):
        resolve_room(418123456789, "온기방")  # identity로 먼저 등록된 상태
        settings.iris_room_map_seed = "418123456789:12345"

        seed_room_map_from_env()

        room_id, _ = resolve_room(418123456789, "온기방")
        self.assertEqual(room_id, 12345)
        self.assertEqual(chat_id_for_room(12345), 418123456789)

    def test_room_name_refreshes_on_change(self):
        resolve_room(1, "옛이름")
        resolve_room(1, "새이름")

        with get_conn() as conn:
            row = conn.execute("SELECT room_name FROM iris_room_map WHERE chat_id=1").fetchone()
        self.assertEqual(row["room_name"], "새이름")


class OutboxSenderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._old_seed = settings.iris_room_map_seed
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'boss.db'}"
        init_schema()
        self.repo = BossRepository()

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        settings.iris_room_map_seed = self._old_seed
        self._tmp.cleanup()

    async def test_sends_due_rows_to_mapped_chat_id_and_acks(self):
        settings.iris_room_map_seed = "418123456789:12345"
        seed_room_map_from_env()
        self.repo.enqueue_outbox(12345, "온기방", "허재승 금주 3일차", "2020-01-01T00:00:00", "2020-01-01T00:00:00")
        client = FakeIrisClient(ok=True)

        sent = await send_pending_outbox_once(self.repo, client)

        self.assertEqual(sent, 1)
        self.assertEqual(client.sent, [(418123456789, "허재승 금주 3일차")])
        with get_conn() as conn:
            row = conn.execute("SELECT status FROM bot_outbox").fetchone()
        self.assertEqual(row["status"], "SENT")

    async def test_failed_send_stays_pending_for_retry(self):
        self.repo.enqueue_outbox(1, "방", "메시지", "2020-01-01T00:00:00", "2020-01-01T00:00:00")
        client = FakeIrisClient(ok=False)

        sent = await send_pending_outbox_once(self.repo, client)

        self.assertEqual(sent, 0)
        with get_conn() as conn:
            row = conn.execute("SELECT status FROM bot_outbox").fetchone()
        self.assertEqual(row["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
