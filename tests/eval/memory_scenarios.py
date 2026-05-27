"""Before/after demonstration of the memory-retrieval change across diverse
conversation contexts.

It runs the *actual* `app.graph.retrieve` (NEW) and a faithful replica of the
pre-change retrieve (OLD) over identical seeded data, and prints the context
each would inject into the system prompt.

Honesty notes:
- FTS keyword recall (SQLite) and fact keyed-load (Chroma metadata .get) run
  for real — no faking.
- True semantic ranking needs the OpenAI embedding model + network. To stay
  offline/deterministic we swap in a *lexical-overlap proxy embedder* for BOTH
  OLD and NEW, so the comparison is fair; only the absolute semantic quality is
  approximated. This is called out, not hidden.

Run:
    .venv/bin/python -m tests.eval.memory_scenarios
"""

from __future__ import annotations

import hashlib
import math
import os
import re
import tempfile

# Must be set before importing app.* (config reads env at import time).
_TMP = tempfile.mkdtemp(prefix="mem_scn_")
os.environ.setdefault("OPENAI_API_KEY", "sk-dummy")
os.environ["BOSS_DB_URL"] = f"sqlite:///{_TMP}/boss.db"
os.environ["ANONYMIZED_TELEMETRY"] = "false"
os.environ["CHROMA_TELEMETRY"] = "false"

import asyncio

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage
from langchain_chroma import Chroma

import app.graph as g
from app.chat_log import add_chat_log, init_chat_log_schema, search_chat_log
from app.config import settings

K = settings.rag_search_k
_DIM = 512


class LexicalEmbeddings(Embeddings):
    """Deterministic offline proxy: hashes word + char-trigram features so
    cosine/L2 distance tracks lexical overlap. Used identically for OLD & NEW."""

    def _embed(self, text: str) -> list[float]:
        vec = [0.0] * _DIM
        toks = re.findall(r"[0-9A-Za-z가-힣]+", text.lower())
        feats: list[str] = []
        for t in toks:
            feats.append(t)
            for i in range(len(t) - 2):
                feats.append(t[i : i + 3])
        if not feats:
            feats = [text[:3] or "_"]
        for f in feats:
            h = int(hashlib.md5(f.encode()).hexdigest(), 16)
            vec[h % _DIM] += 1.0
        norm = math.sqrt(sum(v * v for v in vec)) or 1.0
        return [v / norm for v in vec]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._embed(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._embed(text)


# --- swap the graph's stores for offline proxy-backed ones ---------------
_emb = LexicalEmbeddings()
_vs = Chroma(collection_name="scn_chat", embedding_function=_emb, persist_directory=f"{_TMP}/chroma")
_ups = Chroma(collection_name="scn_profile", embedding_function=_emb, persist_directory=f"{_TMP}/chroma")
g.vectorstore = _vs
g.user_profile_store = _ups


async def _no_persona(room_id):  # avoid OpenAI call in retrieve
    return ""


g.ensure_persona = _no_persona

init_chat_log_schema()


# --- seed data -----------------------------------------------------------
def seed_room(room_id, raw_user, raw_ai, summaries, facts):
    """raw_user: [(sender, text)] -> FTS (NEW ingest) + chat_history role=user
    raw_ai:    [text]            -> chat_history role=assistant (OLD-only, not FTS)
    summaries: [text]            -> chat_history role=context_summary
    facts:     [(sender, text)]  -> user_profile (keyed)"""
    ts = "2026-05-01T12:00:00"
    for sender, text in raw_user:
        add_chat_log(room_id, sender, text, ts)  # real FTS ingest (NEW)
        _vs.add_documents([Document(page_content=f"[{sender}]: {text}",
                                    metadata={"room_id": str(room_id), "role": "user", "sender": sender})])
    for text in raw_ai:
        _vs.add_documents([Document(page_content=f"[AI]: {text}",
                                    metadata={"room_id": str(room_id), "role": "assistant"})])
    for text in summaries:
        _vs.add_documents([Document(page_content=text,
                                    metadata={"room_id": str(room_id), "role": "context_summary"})])
    for sender, text in facts:
        _ups.add_documents([Document(page_content=text,
                                     metadata={"room_id": str(room_id), "sender": sender})])


seed_room(
    1001,
    raw_user=[
        ("민수", "이번주 보스런 토요일 22시로 하자"),
        ("영희", "토 22시 콜, 검은마법사부터 가자"),
        ("철수", "지난주 정산 아직 안 끝났는데 누가 마무리함?"),
        ("민수", "삼성전자 주가 알려줘"),
    ],
    raw_ai=["삼성전자 74,200원, 전일 대비 +1.2%야"],
    summaries=["이번 주 보스런: 토요일 22시, 검은마법사 우선. 지난주 정산 미완료."],
    facts=[("민수", "[선호] 보스런은 주말 밤 시간대 선호"), ("철수", "[신상] 평일 야간근무, 주말 낮 가능")],
)
seed_room(
    1002,
    raw_user=[
        ("지은", "강남 미들탑 칼국수 손칼국수 진짜 맛있어"),
        ("현우", "거기 발렛주차 돼?"),
        ("지은", "발렛 가능하고 웨이팅은 좀 있어"),
    ],
    raw_ai=[],
    summaries=["강남 정모 후보 미들탑 칼국수(손칼국수 추천, 발렛주차 가능, 웨이팅 있음)."],
    facts=[
        ("현우", "[제약] 갑각류 알레르기 있음"),
        ("현우", "[선호] 매운 음식 좋아함"),
        ("현우", "[선호] 커피보다 차 선호"),
        ("현우", "[신상] 분당 거주"),
        ("현우", "[신상] 디자이너"),
        ("현우", "[선호] 일식 좋아함"),
        ("현우", "[비선호] 양고기 싫어함"),
        ("현우", "[제약] 글루텐 줄이는 중"),
        ("현우", "[일정] 매주 수요일 저녁 운동"),
        ("지은", "[선호] 칼국수/면류 좋아함"),
    ],
)
seed_room(
    1003,
    raw_user=[("보람", "다음 모각코 일요일 2시 카페에서"), ("태현", "ㅇㅋ 일요일 2시")],
    raw_ai=[],
    summaries=["모각코 일정 일요일 14시 카페."],
    facts=[("보람", "[제약] 카페인 못 마심")],
)


# --- OLD retrieve (faithful replica of pre-change logic) -----------------
async def old_retrieve(room_id, sender, query):
    relevant = _vs.similarity_search(query, k=K * 3, filter={"room_id": str(room_id)})
    summary_docs = _vs.similarity_search(
        query, k=max(5, K // 2),
        filter={"$and": [{"room_id": str(room_id)}, {"role": "context_summary"}]},
    )
    fact_docs = _ups.similarity_search(
        query, k=8, filter={"$and": [{"room_id": str(room_id)}, {"sender": sender}]},
    )
    relevant = relevant[:K]  # NOTE: real OLD also ran an LLM rerank here (omitted offline)
    merged = summary_docs + relevant
    context = "\n".join(d.page_content for d in merged) if merged else "(없음)"
    facts = "\n".join(f"- {d.page_content}" for d in fact_docs) if fact_docs else "(없음)"
    return context, facts


async def new_retrieve(room_id, sender, query):
    out = await g.retrieve(
        {"messages": [HumanMessage(content=f"[{sender}]: {query}")]},
        {"configurable": {"room_id": room_id, "sender": sender}},
    )
    return out["retrieved_context"] or "(없음)", out["user_facts"] or "(없음)"


SCENARIOS = [
    (1001, "길드 보스방", "민수", "저번에 보스런 시간 언제로 정했더라?",
     "장기 회상: NEW는 FTS가 '보스런' 원문을 정확히 집고 summary로 보강. OLD는 raw 턴 조각들이 컨텍스트에 섞임."),
    (1001, "길드 보스방", "영희", "요즘 삼성전자 어때?",
     "STALE 함정: OLD는 몇 주 전 '[AI]: 74,200원'을 끌어와 옛 시세를 사실처럼 주입. NEW는 AI답변을 임베딩/색인 안 해 옛 숫자가 안 나옴(유저 질문만 FTS로)."),
    (1002, "맛집모임", "현우", "그때 그 칼국수 어디였지?",
     "키워드 회상 + 안전: NEW는 FTS로 '칼국수' 원문 + 갑각류 알레르기 fact까지 확보."),
    (1002, "맛집모임", "현우", "오늘 점심 뭐 먹을지 추천해줘",
     "FACTS 안전성: 현우는 fact가 9개(>8). OLD는 질문과 어휘가 안 겹치는 '갑각류 알레르기'가 top-8 밖으로 밀려 누락될 수 있음 → 봇이 새우 추천 위험. NEW는 키 조회로 전부 로드해 알레르기 항상 포함."),
    (1003, "스터디방", "보람", "칼국수 먹으러 갈까?",
     "방 격리: 1002엔 칼국수가 있지만 1003 검색엔 안 나옴(room 필터). 무관 질문엔 NEW가 빈 컨텍스트로 깔끔."),
    (1001, "길드 보스방", "영희", "다들 오늘 기분 어때?",
     "노이즈 회피: 매칭 키워드 없음 → NEW는 FTS [] + 무관 summary 제외로 거의 빈 컨텍스트. OLD search#1은 가장 가까운 raw 조각을 억지로 끌어와 프롬프트에 잡음 주입."),
]


async def main():
    print(f"(프록시 임베더 = 어휘중첩 기반, OLD/NEW 동일 적용. FTS·facts키조회는 실동작. K={K})\n")
    for i, (room_id, room_name, sender, query, note) in enumerate(SCENARIOS, 1):
        old_ctx, old_facts = await old_retrieve(room_id, sender, query)
        new_ctx, new_facts = await new_retrieve(room_id, sender, query)
        print("═" * 78)
        print(f"시나리오 {i}: {room_name} | 호출자: {sender}")
        print(f"질문: \"{query}\"")
        print("-" * 78)
        print("[OLD] 주입 컨텍스트:")
        print(_indent(old_ctx))
        print("[OLD] user_facts:")
        print(_indent(old_facts))
        print("·" * 40)
        print("[NEW] 주입 컨텍스트:")
        print(_indent(new_ctx))
        print("[NEW] user_facts:")
        print(_indent(new_facts))
        print(f"\n  차이 ▸ {note}\n")


def _indent(text: str) -> str:
    return "\n".join("    " + ln for ln in text.splitlines()) or "    (없음)"


if __name__ == "__main__":
    asyncio.run(main())
