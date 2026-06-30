import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.graph import _extract_and_store_user_facts


class FakeExtractor:
    def __init__(self, content: str):
        self.content = content

    async def ainvoke(self, _messages):
        return SimpleNamespace(content=self.content)


class GraphMemoryStorageTests(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_llm_extracted_joke_about_other_user(self):
        fake_llm = FakeExtractor('{"facts":[{"type":"신상","text":"백윤이는 화를 주체 못하고 탈주한 사람"}]}')
        with patch("app.graph.user_profile_store.similarity_search", return_value=[]), patch(
            "app.graph.fact_extractor_llm", fake_llm
        ), patch("app.graph.user_profile_store.add_documents") as add_documents:
            await _extract_and_store_user_facts(
                "백윤이는 5명이 같이 롤을 하다가 자기 화를 주체 못하고 탈주한 놈이야",
                room_id=1,
                sender="alice",
                now="2026-06-30T10:00:00",
            )

        add_documents.assert_not_called()

    async def test_stores_self_stated_stable_preference(self):
        fake_llm = FakeExtractor('{"facts":[{"type":"선호","text":"짬뽕을 좋아함"}]}')
        with patch("app.graph.user_profile_store.similarity_search", return_value=[]), patch(
            "app.graph.fact_extractor_llm", fake_llm
        ), patch("app.graph.user_profile_store.add_documents") as add_documents:
            await _extract_and_store_user_facts(
                "나는 짬뽕 좋아해",
                room_id=1,
                sender="alice",
                now="2026-06-30T10:00:00",
            )

        add_documents.assert_called_once()


if __name__ == "__main__":
    unittest.main()
