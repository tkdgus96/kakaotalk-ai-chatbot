import unittest
from datetime import datetime

from app import room_topics
from app.graph import (
    _augment_recall_query,
    _detect_mentioned_members,
    _focus_terms,
    _needs_full_model,
    _room_expansions,
)
from app.room_topics import _parse_expansions


class RoomTopicsTests(unittest.TestCase):
    def tearDown(self):
        room_topics._cache.clear()

    def test_parse_expansions_prepends_key_and_skips_invalid(self):
        raw = '{"topics": [{"key": "보스", "terms": ["검마", "해방"]}, {"key": "", "terms": ["x"]}, {"key": "빈거", "terms": []}]}'

        expansions = _parse_expansions(raw)

        self.assertEqual(expansions, {"보스": ["보스", "검마", "해방"]})

    def test_parse_expansions_tolerates_bad_json(self):
        self.assertEqual(_parse_expansions("json이 아님"), {})

    def test_room_expansions_use_computed_topics_only(self):
        room_topics._cache[7] = (datetime.now().isoformat(), {"고양이": ["고양이", "냥이"]})

        merged = _room_expansions(7)

        self.assertEqual(merged, {"고양이": ["고양이", "냥이"]})  # no hardcoded seed

        terms = _focus_terms("어제 고양이 얘기 뭐였지", room_id=7)
        self.assertIn("냥이", terms)

    def test_room_without_topics_has_no_expansions(self):
        room_topics._cache.clear()
        self.assertEqual(_room_expansions(999), {})

    def test_augment_recall_query_uses_room_topics(self):
        room_topics._cache[7] = (datetime.now().isoformat(), {"고양이": ["고양이", "냥이"]})

        query = _augment_recall_query("이 방에서 고양이 얘기 나온 거 정리해줘", room_id=7)

        self.assertIn("냥이", query)


class MentionedMemberTests(unittest.TestCase):
    def test_detects_other_member_not_sender(self):
        mentioned = _detect_mentioned_members(
            "[김상현]: 허재승 알레르기 뭐였지?", ["김상현", "허재승", "온반봇"], "김상현"
        )

        self.assertEqual(mentioned, ["허재승"])

    def test_caps_mentions_and_ignores_absent_names(self):
        mentioned = _detect_mentioned_members(
            "[a]: 허재승이랑 김민수랑 박철수 셋 다 오는거야?",
            ["허재승", "김민수", "박철수"],
            "a",
        )

        self.assertEqual(mentioned, ["허재승", "김민수"])


class ModelRoutingTests(unittest.TestCase):
    def test_casual_small_talk_goes_light(self):
        self.assertFalse(_needs_full_model("[김상현]: ㅋㅋㅋㅋ 오반데"))
        self.assertFalse(_needs_full_model("[김상현]: 온반봇 안녕"))

    def test_fact_or_tool_queries_stay_on_full_model(self):
        self.assertTrue(_needs_full_model("[김상현]: 삼성전자 주가 어때"))
        self.assertTrue(_needs_full_model("[김상현]: 어제 누가 파파존스 얘기했지?"))
        self.assertTrue(_needs_full_model("[김상현]: " + "긴 얘기 " * 20))


if __name__ == "__main__":
    unittest.main()
