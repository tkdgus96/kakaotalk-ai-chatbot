import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.boss.db import init_schema
from app.boss.utils.week import now_kst
from app.chat_log import init_chat_log_schema
from app.chat_log import add_chat_log
from app.config import settings
from app.services.search_service import SearchResult
from app.tools.chat_history import summarize_chat_history
from app.tools.memory import remember_user_fact
from app.tools.reminder import cancel_reminder, create_reminder, list_reminders
from app.tools.research import research_web
from app.tools.url_reader import read_url, summarize_url


class ToolResilienceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_db_url = settings.boss_db_url
        self._tmp = tempfile.TemporaryDirectory()
        settings.boss_db_url = f"sqlite:///{Path(self._tmp.name) / 'tool_resilience.db'}"
        init_schema()
        init_chat_log_schema()

    async def asyncTearDown(self):
        settings.boss_db_url = self._old_db_url
        self._tmp.cleanup()

    async def test_summarize_chat_history_empty_date_does_not_guess(self):
        answer = await summarize_chat_history.ainvoke({"period": "어제", "room_id": 123})

        self.assertIn("채팅 로그가 없습니다", answer)
        self.assertIn("추측하지 마세요", answer)

    async def test_summarize_chat_history_short_log_preserves_message(self):
        today = now_kst().replace(hour=10, minute=0, second=0, microsecond=0)
        add_chat_log(123, "alice", "점심 메뉴만 얘기했어", today.isoformat())

        answer = await summarize_chat_history.ainvoke({"period": "오늘", "room_id": 123})

        self.assertIn("기록된 메시지가 1개", answer)
        self.assertIn("점심 메뉴만 얘기했어", answer)

    async def test_url_tools_reject_missing_url(self):
        read_answer = await read_url.ainvoke({"url": "링크 요약해줘"})
        summary_answer = await summarize_url.ainvoke({"url": "not-a-url"})

        self.assertIn("URL을 찾지 못했습니다", read_answer)
        self.assertIn("URL을 찾지 못했습니다", summary_answer)

    async def test_reminder_invalid_time_and_empty_list_are_user_safe(self):
        create_answer = await create_reminder.ainvoke(
            {"message": "회의", "when": "언젠가", "room_id": 123}
        )
        list_answer = await list_reminders.ainvoke({"room_id": 123})

        self.assertIn("시간을 해석하지 못했습니다", create_answer)
        self.assertEqual("대기 중인 리마인더가 없습니다.", list_answer)

    async def test_cancel_missing_reminder_is_user_safe(self):
        answer = await cancel_reminder.ainvoke({"reminder_id": 999})

        self.assertEqual("취소할 대기 리마인더를 찾지 못했습니다.", answer)

    async def test_research_no_results_is_user_safe(self):
        with patch("app.tools.research._search_service.search_web", new=AsyncMock(return_value=[])), patch(
            "app.tools.research._search_service.search_naver", new=AsyncMock(return_value=[])
        ):
            answer = await research_web.ainvoke({"query": "없는 주제", "max_sources": 3})

        self.assertEqual("검색 결과가 없습니다.", answer)

    async def test_research_search_exception_is_user_safe(self):
        with patch(
            "app.tools.research._search_service.search_web",
            new=AsyncMock(side_effect=RuntimeError("provider down")),
        ):
            answer = await research_web.ainvoke({"query": "테스트", "max_sources": 3})

        self.assertIn("검색 중 오류가 발생했습니다", answer)
        self.assertIn("provider down", answer)

    async def test_research_crawl_failure_falls_back_to_snippet(self):
        result = SearchResult(
            provider="tavily",
            title="테스트 문서",
            url="https://example.com/test",
            snippet="검색 요약만 있는 결과",
        )
        fake_llm = AsyncMock()
        fake_llm.ainvoke.return_value = SimpleNamespace(content="검색 요약 기반 비교 답변")
        with patch("app.tools.research._search_service.search_web", new=AsyncMock(return_value=[result])), patch(
            "app.tools.research._crawl_service.crawl_many",
            new=AsyncMock(side_effect=RuntimeError("crawler offline")),
        ), patch("app.tools.research.llm", fake_llm):
            answer = await research_web.ainvoke({"query": "테스트", "max_sources": 3})

        self.assertEqual("검색 요약 기반 비교 답변", answer)
        prompt = fake_llm.ainvoke.call_args.args[0][1].content
        self.assertIn("검색 요약만 있는 결과", prompt)
        self.assertIn("crawler offline", prompt)

    async def test_memory_store_failure_is_reported(self):
        with patch(
            "app.tools.memory.user_profile_store.aadd_documents",
            new=AsyncMock(side_effect=RuntimeError("vector store down")),
        ):
            answer = await remember_user_fact.ainvoke(
                {
                    "fact": "견과류 알레르기",
                    "sender": "tester",
                    "room_id": 123,
                    "fact_type": "제약",
                }
            )

        self.assertIn("기억 저장 실패", answer)
        self.assertIn("vector store down", answer)

    async def test_memory_tool_rejects_joke_directive(self):
        answer = await remember_user_fact.ainvoke(
            {
                "fact": "온반을 엄마라고 부르기",
                "sender": "tester",
                "room_id": 123,
                "fact_type": "기타",
            }
        )

        self.assertIn("기억 저장 거부", answer)


if __name__ == "__main__":
    unittest.main()
