import asyncio
from datetime import datetime
from typing import Annotated, TypedDict

from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode

from app.config import settings
from app.dependencies import llm, vectorstore
from app.prompts import SYSTEM_PROMPT
from app.tools import tools

llm_with_tools = llm.bind_tools(tools)


class ChatState(TypedDict):
    messages: Annotated[list, add_messages]
    retrieved_context: str
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


async def retrieve(state: ChatState, config) -> dict:
    room_id, sender = _resolve(config)
    query = _last_human_text(state["messages"])

    relevant, summary_docs = await asyncio.gather(
        asyncio.to_thread(
            vectorstore.similarity_search,
            query,
            k=settings.rag_search_k,
            filter={"room_id": str(room_id)},
        ),
        asyncio.to_thread(
            vectorstore.similarity_search,
            query,
            k=max(5, settings.rag_search_k // 2),
            filter={"$and": [{"room_id": str(room_id)}, {"role": "context_summary"}]},
        ),
    )
    merged = summary_docs + relevant
    context = "\n".join(d.page_content for d in merged) if merged else ""
    return {"retrieved_context": context, "room_id": room_id, "sender": sender}


async def chat(state: ChatState) -> dict:
    sender = state.get("sender", settings.playground_sender)
    context = state.get("retrieved_context", "")

    system_content = SYSTEM_PROMPT + f"\n\n현재 대화 상대: {sender}"
    if context:
        system_content += f"\n\n참고할 수 있는 이전 대화 내용:\n{context}"

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
    return {}


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
