import asyncio
import json
from datetime import datetime
from typing import Annotated, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import fact_extractor_llm, llm, user_profile_store, vectorstore
from app.persona import ensure_persona
from app.prompts import get_system_prompt
from app.tools import tools

FACT_EXTRACTION_PROMPT = """\
다음은 단톡방의 한 사용자(발화자: {sender})가 방금 한 발화야.
이 발화에서 그 사용자에 대해 영속적으로 기억할 만한 사실을 추출해.

영속적인 사실의 예:
- 선호: 좋아하는 음식/장르/취미/제품
- 비선호: 싫어하는 것
- 제약: 알레르기/금기/식이/예산 제한
- 신상: 거주지/직장/직업/가족/관계
- 일정: 반복되는 약속/이벤트 (일회성 약속 제외)

영속적이지 않은 것은 추출하지 마:
- 오늘의 기분, 일회성 질문, 단발성 요청
- 봇에 대한 지시 ("이건 검색해줘", "표로 정리해줘" 등)
- 다른 사람에 대한 평가/관찰

[중요] 기존에 이 사용자에 대해 이미 알려진 사실:
{existing_facts}

위 사실들에 이미 포함된 내용은 다시 추출하지 마. 다음 경우에만 추출:
- 완전히 새로운 정보
- 기존 사실을 갱신하는 정보 (예: 이전엔 부산 살았는데 이제 서울로 이사)
  → 이 경우 새 사실 text 끝에 "(갱신)" 표시

발화: "{text}"

JSON으로만 답해. 다른 텍스트 금지.
{{"facts": [{{"type": "선호|비선호|제약|신상|일정", "text": "구체적 사실 한 줄"}}]}}
추출할 사실이 없으면 {{"facts": []}}.
"""

llm_with_tools = llm.bind_tools(tools)


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    retrieved_context: str
    user_facts: str
    room_persona: str
    buffer_context: str
    room_id: int
    sender: str


def _resolve(config):
    cfg = (config or {}).get("configurable", {})
    room_id = int(cfg.get("room_id") or settings.playground_room_id)
    sender = cfg.get("sender") or settings.playground_sender
    return room_id, sender


def _last_human_text(messages: list) -> str:
    for m in reversed(messages):
        if isinstance(m, HumanMessage):
            return m.content if isinstance(m.content, str) else str(m.content)
    last = messages[-1]
    return last.content if hasattr(last, "content") else str(last)


RERANK_PROMPT = """\
유저 질문에 답하는 데 도움이 되는 문서를 골라.
각 문서에 0-10 점수를 매겨. 10=강한 관련, 0=무관.
JSON으로만 답해. 다른 텍스트 금지.

질문: {query}

문서들:
{docs}

응답 형식: {{"scores": [점수1, 점수2, ...]}}
"""


async def _rerank(query: str, docs: list, top_n: int) -> list:
    """Rerank docs by relevance using a cheap LLM. Returns top_n docs."""
    if len(docs) <= top_n:
        return docs
    doc_block = "\n".join(f"[{i}] {d.page_content[:300]}" for i, d in enumerate(docs))
    prompt = RERANK_PROMPT.format(query=query, docs=doc_block)
    try:
        response = await fact_extractor_llm.ainvoke([HumanMessage(content=prompt)])
        raw = response.content if isinstance(response.content, str) else str(response.content)
        raw = raw.strip().lstrip("`").lstrip("json").strip()
        scores = json.loads(raw).get("scores", [])
        if len(scores) != len(docs):
            return docs[:top_n]
        ranked = sorted(zip(docs, scores), key=lambda p: p[1], reverse=True)
        return [d for d, _ in ranked[:top_n]]
    except Exception:
        return docs[:top_n]


async def retrieve(state: ChatState, config) -> dict:
    room_id, sender = _resolve(config)
    query = _last_human_text(state["messages"])

    candidate_k = settings.rag_search_k * 3
    relevant, summary_docs, user_fact_docs = await asyncio.gather(
        asyncio.to_thread(
            vectorstore.similarity_search,
            query,
            k=candidate_k,
            filter={"room_id": str(room_id)},
        ),
        asyncio.to_thread(
            vectorstore.similarity_search,
            query,
            k=max(5, settings.rag_search_k // 2),
            filter={"$and": [{"room_id": str(room_id)}, {"role": "context_summary"}]},
        ),
        asyncio.to_thread(
            user_profile_store.similarity_search,
            query,
            k=8,
            filter={"$and": [{"room_id": str(room_id)}, {"sender": sender}]},
        ),
    )
    relevant = await _rerank(query, relevant, top_n=settings.rag_search_k)

    merged = summary_docs + relevant
    context = "\n".join(d.page_content for d in merged) if merged else ""
    user_facts = "\n".join(f"- {d.page_content}" for d in user_fact_docs) if user_fact_docs else ""

    try:
        persona = await ensure_persona(room_id)
    except Exception:
        persona = ""

    return {
        "retrieved_context": context,
        "user_facts": user_facts,
        "room_persona": persona,
        "room_id": room_id,
        "sender": sender,
    }


async def chat(state: ChatState) -> dict:
    sender = state.get("sender", settings.playground_sender)
    room_id = state.get("room_id", settings.playground_room_id)
    context = state.get("retrieved_context", "")
    user_facts = state.get("user_facts", "")
    persona = state.get("room_persona", "")

    variant = settings.prompt_variant_overrides.get(room_id, settings.default_prompt_variant)
    now_str = now_kst().strftime("%Y-%m-%d %H:%M:%S %A KST")
    system_content = get_system_prompt(variant) + f"\n\n현재 시각: {now_str}\n현재 대화 상대: {sender}"
    if persona:
        system_content += f"\n\n이 단톡방의 분위기 / 페르소나 가이드:\n{persona}"
    if user_facts:
        system_content += (
            f"\n\n{sender}에 대해 알려진 사실 (선호/제약/신상). "
            f"답변할 때 이 사실들을 위반하지 말 것:\n{user_facts}"
        )
    if context:
        system_content += f"\n\n참고할 수 있는 이전 대화 내용:\n{context}"
    buffer_context = state.get("buffer_context", "")
    if buffer_context:
        system_content += f"\n\n최근 채팅방 대화 (아직 저장 전):\n{buffer_context}"

    messages = [SystemMessage(content=system_content)] + list(state["messages"])
    response = await llm_with_tools.ainvoke(messages)
    return {"messages": [response]}


async def store(state: ChatState) -> dict:
    room_id = state.get("room_id", settings.playground_room_id)
    sender = state.get("sender", settings.playground_sender)
    now = datetime.now().isoformat()

    last_ai = None
    last_human = None
    for m in reversed(state["messages"]):
        if last_ai is None and isinstance(m, AIMessage) and m.content:
            last_ai = m
        elif last_human is None and isinstance(m, HumanMessage) and m.content:
            last_human = m
        if last_ai and last_human:
            break

    docs = []
    if last_human:
        docs.append(
            Document(
                page_content=f"[{sender}]: {last_human.content}",
                metadata={
                    "room_id": str(room_id),
                    "role": "user",
                    "sender": sender,
                    "timestamp": now,
                },
            )
        )
    if last_ai:
        docs.append(
            Document(
                page_content=f"[AI]: {last_ai.content}",
                metadata={"room_id": str(room_id), "role": "assistant", "timestamp": now},
            )
        )
    if docs:
        await asyncio.to_thread(vectorstore.add_documents, docs)

    if last_human:
        await _extract_and_store_user_facts(last_human.content, room_id, sender, now)
    return {}


async def _extract_and_store_user_facts(text: str, room_id: int, sender: str, now: str) -> None:
    existing = await asyncio.to_thread(
        user_profile_store.similarity_search,
        text,
        k=10,
        filter={"$and": [{"room_id": str(room_id)}, {"sender": sender}]},
    )
    existing_block = "\n".join(f"- {d.page_content}" for d in existing) if existing else "(없음)"

    try:
        response = await fact_extractor_llm.ainvoke(
            [
                HumanMessage(
                    content=FACT_EXTRACTION_PROMPT.format(
                        sender=sender, text=text, existing_facts=existing_block
                    )
                )
            ]
        )
        raw = response.content if isinstance(response.content, str) else str(response.content)
        raw = raw.strip().lstrip("`").lstrip("json").strip()
        data = json.loads(raw)
        facts = data.get("facts", [])
    except Exception:
        return

    if not facts:
        return

    fact_docs = []
    for f in facts:
        fact_type = str(f.get("type", "기타"))
        fact_text = str(f.get("text", "")).strip()
        if not fact_text:
            continue
        fact_docs.append(
            Document(
                page_content=f"[{fact_type}] {fact_text}",
                metadata={
                    "room_id": str(room_id),
                    "sender": sender,
                    "fact_type": fact_type,
                    "timestamp": now,
                },
            )
        )
    if fact_docs:
        await asyncio.to_thread(user_profile_store.add_documents, fact_docs)


def route_after_chat(state: ChatState) -> str:
    last = state["messages"][-1]
    if getattr(last, "tool_calls", None):
        return "tools"
    return "store"


builder = StateGraph(ChatState)
builder.add_node("retrieve", retrieve)
builder.add_node("chat", chat)
builder.add_node("tools", ToolNode(tools))
builder.add_node("store", store)

builder.add_edge(START, "retrieve")
builder.add_edge("retrieve", "chat")
builder.add_conditional_edges("chat", route_after_chat, {"tools": "tools", "store": "store"})
builder.add_edge("tools", "chat")
builder.add_edge("store", END)

graph = builder.compile()
