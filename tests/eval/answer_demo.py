"""Run the REAL gpt-4o over the OLD vs NEW injected memory context and print
the actual bot answers, so the end-user-visible difference is concrete.

- Retrieval contexts come from tests/eval/memory_scenarios (the same OLD/NEW
  results shown earlier; semantic part is the offline proxy, FTS/facts real).
- Answers are produced by the real model with TOOLS DISABLED, to isolate the
  effect of memory (tools-on would re-fetch live data and mask stale recall).
- temperature=0 for reproducibility.

Run:
    .venv/bin/python -m tests.eval.answer_demo
"""

from __future__ import annotations

import asyncio

from dotenv import dotenv_values
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.boss.utils.week import now_kst
from app.prompts import get_system_prompt
from tests.eval.memory_scenarios import SCENARIOS, new_retrieve, old_retrieve

_KEY = (dotenv_values(".env").get("OPENAI_API_KEY") or "").strip()
real_llm = ChatOpenAI(model="gpt-4o", api_key=_KEY, temperature=0, max_tokens=700)

RUN = [1, 2, 3, 4]  # answer-differentiating scenarios


def build_system(sender: str, context: str, facts: str) -> str:
    now = now_kst().strftime("%Y-%m-%d %H:%M:%S %A KST")
    s = get_system_prompt("default") + f"\n\n현재 시각: {now}\n현재 대화 상대: {sender}"
    if facts and facts != "(없음)":
        s += (
            f"\n\n{sender}에 대해 알려진 사실 (선호/제약/신상). "
            f"답변할 때 이 사실들을 위반하지 말 것:\n{facts}"
        )
    if context and context != "(없음)":
        s += f"\n\n참고할 수 있는 이전 대화 내용:\n{context}"
    return s


async def answer(sender: str, question: str, context: str, facts: str) -> str:
    msgs = [
        SystemMessage(content=build_system(sender, context, facts)),
        HumanMessage(content=f"[{sender}]: {question}"),
    ]
    r = await real_llm.ainvoke(msgs)  # no tools bound — memory effect only
    return (r.content if isinstance(r.content, str) else str(r.content)).strip()


async def main():
    print("(실모델 gpt-4o, 툴 OFF, temp=0. 컨텍스트는 앞서 보여준 OLD/NEW 검색 결과)\n")
    for i, (room_id, room_name, sender, query, _note) in enumerate(SCENARIOS, 1):
        if i not in RUN:
            continue
        old_ctx, old_facts = await old_retrieve(room_id, sender, query)
        new_ctx, new_facts = await new_retrieve(room_id, sender, query)
        old_ans, new_ans = await asyncio.gather(
            answer(sender, query, old_ctx, old_facts),
            answer(sender, query, new_ctx, new_facts),
        )
        print("═" * 78)
        print(f"시나리오 {i} · [{room_name}] {sender} 질문: \"{query}\"")
        print("-" * 78)
        print("【OLD 메모리 기반 답변】")
        print(_indent(old_ans))
        print("\n【NEW 메모리 기반 답변】")
        print(_indent(new_ans))
        print()


def _indent(text: str) -> str:
    return "\n".join("  " + ln for ln in text.splitlines())


if __name__ == "__main__":
    asyncio.run(main())
