import tempfile
import unittest
from pathlib import Path

from app.boss.db import init_schema
from app.chat_log import add_chat_log, get_chat_log_between, init_chat_log_schema, search_chat_log
from app.config import settings
from app.graph import _augment_recall_query, _detect_date_recap_request, _identity_answer, _normalize_chat_output


class ChatLogToolTests(unittest.TestCase):
    def setUp(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'chat_log.db'}"
        init_schema()
        init_chat_log_schema()

    def tearDown(self):
        settings.boss_db_url = self._old_db_url
        self._tmp.cleanup()

    def test_get_chat_log_between_filters_by_date(self):
        add_chat_log(1, "alice", "어제 얘기", "2026-06-29T10:00:00")
        add_chat_log(1, "bob", "오늘 얘기", "2026-06-30T10:00:00")

        rows = get_chat_log_between(1, "2026-06-29T00:00:00", "2026-06-30T00:00:00")

        self.assertEqual(rows, ["10:00 [alice] 어제 얘기"])

    def test_search_chat_log_falls_back_for_two_char_korean_keyword(self):
        add_chat_log(1, "alice", "오늘 보스 일정은 밤 10시", "2026-06-30T10:00:00")

        rows = search_chat_log(1, "보스", 5)

        self.assertEqual(rows, ["2026-06-30 10:00 [alice] 오늘 보스 일정은 밤 10시"])

    def test_search_chat_log_prefers_recent_when_query_asks_recently(self):
        add_chat_log(1, "old", "메소 시세", "2025-06-16T10:10:00")
        add_chat_log(1, "new", "!메소 시세", "2026-06-29T17:49:00")

        rows = search_chat_log(1, "최근 메소 시세 누가 언제", 5)

        self.assertEqual(rows[0], "2026-06-29 17:49 [new] !메소 시세")

    def test_date_recap_detects_absolute_korean_date(self):
        result = _detect_date_recap_request("2026년 6월 29일 이 방에서 나온 얘기만 정리해줘.")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "2026-06-29")
        self.assertEqual(result[1].strftime("%Y-%m-%d"), "2026-06-29")
        self.assertEqual(result[2].strftime("%Y-%m-%d"), "2026-06-30")

    def test_date_recap_uses_today_when_yesterday_is_excluded(self):
        result = _detect_date_recap_request("오늘 채팅방 내용만 정리해줘. 어제 얘기는 섞지 말고.")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "오늘")

    def test_date_recap_uses_yesterday_when_today_is_excluded(self):
        result = _detect_date_recap_request("어제 채팅방 내용만 정리해줘. 오늘 얘기는 빼줘.")

        self.assertIsNotNone(result)
        self.assertEqual(result[0], "어제")

    def test_normalize_chat_output_removes_markdown_shell(self):
        answer = _normalize_chat_output("## 제목\n**핵심**\n```text\n내용\n```")

        self.assertNotIn("##", answer)
        self.assertNotIn("**", answer)
        self.assertNotIn("```", answer)

    def test_identity_answer_uses_current_sender(self):
        answer = _identity_answer("[이재용은신이야]: 내가 누구야?", "이재용은신이야")

        self.assertEqual(answer, "너는 지금 이 방에서 '이재용은신이야'로 말하고 있어.")

    def test_identity_answer_handles_phrase_inside_longer_question(self):
        answer = _identity_answer("내가 누구야? 예전에 방에서 언급된 다른 사람 말고 지금 나 기준으로만 답해.", "이재용은신이야")

        self.assertEqual(answer, "너는 지금 이 방에서 '이재용은신이야'로 말하고 있어.")

    def test_recall_query_augments_room_preference(self):
        query = _augment_recall_query("파파존스 추천해줘. 이 방에서 나온 취향 기준으로.")

        self.assertIn("존스페이버릿", query)
        self.assertIn("수퍼파파스", query)

    def test_recall_query_augments_bot_complaints(self):
        query = _augment_recall_query("최근 이 방에서 온반봇에 대해 나온 불만이 뭐였는지 정리해줘.")

        self.assertIn("깡통", query)
        self.assertIn("말투", query)


if __name__ == "__main__":
    unittest.main()
