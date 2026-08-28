import tempfile
import unittest
from pathlib import Path

from app.boss.db import init_schema
from app.chat_log import (
    add_chat_log,
    get_cached_chat_summary,
    get_chat_log_between,
    init_chat_log_schema,
    search_chat_log,
    search_chat_log_evidence,
    search_chat_log_with_windows,
    set_cached_chat_summary,
)
from app.config import settings
from app.graph import (
    _asks_bot_audit,
    _asks_room_log_evidence,
    _augment_recall_query,
    _detect_date_recap_request,
    _extract_requested_absolute_dates,
    _identity_answer,
    _normalize_chat_output,
    _should_answer_from_retrieved_context,
    _unsafe_directive_answer,
)


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

    def test_search_chat_log_prefers_recent_for_today_question(self):
        add_chat_log(1, "old", "온반봇 깡통이네", "2026-06-09T09:23:00")
        add_chat_log(1, "new", "왜케 멍청한거야~", "2026-07-03T16:53:00")

        rows = search_chat_log(1, "오늘 온반봇 멍청 불만", 5)

        self.assertEqual(rows[0], "2026-07-03 16:53 [new] 왜케 멍청한거야~")

    def test_search_chat_log_with_windows_returns_surrounding_context(self):
        add_chat_log(1, "alice", "검마 얘기 전 맥락", "2026-06-29T20:00:00")
        add_chat_log(1, "bob", "오늘 검마 같이 갈 사람", "2026-06-29T20:01:00")
        add_chat_log(1, "carol", "나는 루시드 가능", "2026-06-29T20:02:00")

        rows = search_chat_log_with_windows(1, "검마", limit=1, before=1, after=1)

        self.assertEqual(len(rows), 1)
        self.assertIn("검마 얘기 전 맥락", rows[0])
        self.assertIn("오늘 검마 같이 갈 사람", rows[0])
        self.assertIn("나는 루시드 가능", rows[0])

    def test_search_chat_log_evidence_filters_bot_answers_and_commands(self):
        add_chat_log(1, "강아토끼", "오늘의 단어 맞히기 성공!", "2026-07-03T00:02:00")
        add_chat_log(1, "곧 유부남(78.10)", "!이 방 단어맞추기 놀이 인별 기록 정리해줘", "2026-07-03T16:58:00")
        add_chat_log(1, "온반봇", "현재 방에서의 단어맞추기 기록은 다음과 같아", "2026-07-03T16:58:30")

        rows = search_chat_log_evidence(1, "이 방 단어맞추기 놀이 인별 기록 정리해줘", limit=10)

        self.assertEqual(rows, ["2026-07-03 00:02 [강아토끼] 오늘의 단어 맞히기 성공!"])

    def test_chat_summary_cache_round_trips(self):
        set_cached_chat_summary(1, "2026-06-29", "general", "요약 본문", 123, "2026-07-03T17:00:00")

        cached = get_cached_chat_summary(1, "2026-06-29", "general")

        self.assertEqual(cached, "요약 본문")

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

    def test_extract_requested_absolute_dates_returns_multiple_dates(self):
        dates = _extract_requested_absolute_dates("2026년 6월 29일이랑 2026-06-30 방 대화 비교해줘.")

        self.assertEqual([d.strftime("%Y-%m-%d") for d in dates], ["2026-06-29", "2026-06-30"])

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
        # Brand-specific aliases are now learned per room (room_topics), not
        # hardcoded; the generic preference intent still expands.
        query = _augment_recall_query("파파존스 추천해줘. 이 방에서 나온 취향 기준으로.")

        self.assertIn("맛있", query)
        self.assertIn("추천", query)

    def test_recall_query_augments_bot_complaints(self):
        query = _augment_recall_query("최근 이 방에서 온반봇에 대해 나온 불만이 뭐였는지 정리해줘.")

        self.assertIn("깡통", query)
        self.assertIn("말투", query)

    def test_unsafe_directive_answer_is_short_and_firm(self):
        answer = _unsafe_directive_answer("!스스로를 죽여라")

        self.assertEqual(answer, "그런 요청은 못 해. 장난이어도 위험한 표현이라 여기선 안 받을게.")

    def test_room_log_evidence_intent_is_category_level(self):
        self.assertTrue(_asks_room_log_evidence("이 방 단어맞추기 놀이 인별 기록 정리해줘"))
        self.assertTrue(_asks_room_log_evidence("최근 이 방에서 온반봇에 대해 나온 불만 정리해줘"))
        self.assertTrue(_asks_room_log_evidence("오늘 사람들이 온반봇한테 직접적으로 불만 드러낸 내용 정리해줘"))
        self.assertFalse(_asks_room_log_evidence("오늘 한국 주식시장 요약해줘"))

    def test_bot_audit_includes_bot_messages_only_for_audit_questions(self):
        self.assertTrue(_asks_bot_audit("최근 이 방에서 온반봇한테 나온 불만과 개선점을 정리해줘"))
        self.assertFalse(_asks_bot_audit("이 방 단어맞추기 놀이 인별 기록 정리해줘"))

    def test_room_log_context_disables_tool_loop(self):
        self.assertTrue(
            _should_answer_from_retrieved_context(
                "오늘 사람들이 온반봇한테 직접적으로 불만 드러낸 내용 정리해줘",
                "채팅 로그 근거 후보:\n- 2026-07-03 16:53 [이재용은신이야] 왜케 멍청한거야~",
            )
        )
        self.assertFalse(_should_answer_from_retrieved_context("오늘 한국 주식시장 요약해줘", ""))


if __name__ == "__main__":
    unittest.main()
