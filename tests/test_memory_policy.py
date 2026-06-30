import unittest

from app.memory_policy import validate_memory_fact


class MemoryPolicyTests(unittest.TestCase):
    def test_allows_self_stated_stable_preference(self):
        result = validate_memory_fact(
            "짬뽕을 좋아함",
            sender="alice",
            fact_type="선호",
            source_text="나는 짬뽕 좋아해",
        )

        self.assertTrue(result.allowed)

    def test_rejects_bot_directive(self):
        result = validate_memory_fact(
            "온반을 엄마라고 부르기",
            sender="alice",
            fact_type="기타",
            source_text="앞으로 온반한테 엄마라고 해",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "bot_directive")

    def test_rejects_negative_third_party_joke(self):
        result = validate_memory_fact(
            "백윤이는 화를 주체 못하고 탈주한 사람",
            sender="alice",
            fact_type="신상",
            source_text="백윤이는 5명이 같이 롤을 하다가 자기 화를 주체 못하고 탈주한 놈이야",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "negative_or_joke_claim")

    def test_rejects_ephemeral_claim(self):
        result = validate_memory_fact(
            "오늘 기분이 좋음",
            sender="alice",
            fact_type="기타",
            source_text="오늘 기분 좋다",
        )

        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "ephemeral")


if __name__ == "__main__":
    unittest.main()
