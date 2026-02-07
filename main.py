import os
import json
import asyncio
import logging
import httpx
from datetime import datetime
from collections import defaultdict
from dotenv import load_dotenv
from fastapi import FastAPI, Request
from pydantic import BaseModel, Field
from langchain_openai import ChatOpenAI, OpenAIEmbeddings
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_core.tools import tool

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Buffer config
BUFFER_SIZE = 20
message_buffers: dict[int, list[str]] = defaultdict(list)

# Max history messages before summarizing
MAX_HISTORY_MESSAGES = 20

# Config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CONNECTION_STRING = os.getenv("DB_CONNECTION_STRING")
ALLOWED_ROOMS = set(int(r) for r in os.getenv("ALLOWED_ROOMS", "").split(",") if r.strip())
NEXON_API_KEY = os.getenv("NEXON_API_KEY")
NEXON_API_BASE = "https://open.api.nexon.com/maplestory/v1"

SYSTEM_PROMPT = """너는 카카오톡 채팅방의 친절한 AI 어시스턴트야.
친근하고 도움이 되는 말투로 대화해줘. 이모지도 적절히 사용해줘.
대화 기록을 기억하고 있으니, 이전 대화 맥락을 참고해서 답변해줘."""

# LLM
llm = ChatOpenAI(model="gpt-4o-mini", api_key=OPENAI_API_KEY, temperature=0.7, max_tokens=1000, verbose=True)

# ChromaDB vector store
embeddings = OpenAIEmbeddings(api_key=OPENAI_API_KEY)
vectorstore = Chroma(
    collection_name="chat_history",
    embedding_function=embeddings,
    persist_directory="./chroma_db"
)

@tool
async def lookup_maplestory_character(character_name: str) -> str:
    """메이플스토리 캐릭터 정보를 조회합니다. 유저가 메이플스토리 캐릭터에 대해 물어볼 때 사용하세요."""
    headers = {"x-nxopen-api-key": NEXON_API_KEY}
    async with httpx.AsyncClient() as client:
        # Step 1: Get OCID
        resp = await client.get(f"{NEXON_API_BASE}/id", params={"character_name": character_name}, headers=headers)
        if resp.status_code != 200:
            return f"캐릭터 '{character_name}'을(를) 찾을 수 없습니다."
        ocid = resp.json()["ocid"]

        # Step 2: Fetch basic info and stats in parallel
        basic_resp, stat_resp = await asyncio.gather(
            client.get(f"{NEXON_API_BASE}/character/basic", params={"ocid": ocid}, headers=headers),
            client.get(f"{NEXON_API_BASE}/character/stat", params={"ocid": ocid}, headers=headers),
        )

    basic = basic_resp.json()
    stats = stat_resp.json()

    # Format the data
    info = {
        "캐릭터명": basic.get("character_name"),
        "월드": basic.get("world_name"),
        "직업": basic.get("character_class"),
        "레벨": basic.get("character_level"),
        "경험치율": f"{basic.get('character_exp_rate')}%",
        "길드": basic.get("character_guild_name") or "없음",
        "성별": basic.get("character_gender"),
    }

    stat_list = stats.get("final_stat", [])
    for s in stat_list:
        info[s["stat_name"]] = s["stat_value"]

    return json.dumps(info, ensure_ascii=False, indent=2)


# LLM with tool calling
tools = [lookup_maplestory_character]
llm_with_tools = llm.bind_tools(tools)


class KakaoMsg(BaseModel):
    room_id: int
    room: str
    msg: str
    sender: str
    is_command: bool

async def flush_buffer(room_id: int):
    """Ask GPT to filter buffered messages for meaningful info, then save to ChromaDB."""
    buffer = message_buffers.pop(room_id, [])
    if not buffer:
        return

    conversation = "\n".join(buffer)
    filter_prompt = [
        SystemMessage(content=(
            "다음은 카카오톡 채팅방의 대화 내용이야. "
            "이 중에서 나중에 참고할 만한 의미 있는 정보만 추출해줘. "
            "예: 약속, 일정, 중요한 결정, 개인 정보(이름, 취향, 선호 등), 핵심 사실. "
            "의미 없는 인사, 감탄사, 잡담은 제외해. "
            "의미 있는 내용이 없으면 '없음'이라고만 답해. "
            "있으면 핵심 내용을 간결하게 요약해서 bullet point로 답해."
        )),
        HumanMessage(content=conversation),
    ]

    response = llm.invoke(filter_prompt)
    summary = response.content.strip()

    if summary and summary != "없음":
        vectorstore.add_documents([
            Document(
                page_content=summary,
                metadata={"room_id": str(room_id), "role": "context_summary", "timestamp": datetime.now().isoformat()},
            )
        ])


async def summarize_history(messages):
    """Summarize old messages to reduce token usage."""
    old_messages = messages[:-MAX_HISTORY_MESSAGES]
    conversation = "\n".join(
        f"{'[User]' if msg.type == 'human' else '[AI]'}: {msg.content}" for msg in old_messages
    )
    summary_prompt = [
        SystemMessage(content="다음 대화 내용을 간결하게 요약해줘. 핵심 주제와 중요한 정보만 포함해."),
        HumanMessage(content=conversation),
    ]
    response = llm.invoke(summary_prompt)
    return response.content


@app.get("/rooms")
async def list_rooms():
    return {"allowed_rooms": sorted(ALLOWED_ROOMS)}


@app.post("/rooms/{room_id}")
async def add_room(room_id: int):
    ALLOWED_ROOMS.add(room_id)
    logger.info(f"Room added: {room_id}")
    return {"allowed_rooms": sorted(ALLOWED_ROOMS)}


@app.delete("/rooms/{room_id}")
async def remove_room(room_id: int):
    ALLOWED_ROOMS.discard(room_id)
    logger.info(f"Room removed: {room_id}")
    return {"allowed_rooms": sorted(ALLOWED_ROOMS)}


@app.post("/debug")
async def debug_request(request: Request):
    body = await request.json()
    logger.info(f"Raw request body: {body}")
    return {"received": body}


@app.post("/chat")
async def handle_msg(data: KakaoMsg):
    # Log and ignore rooms that are not allowed
    if data.room_id not in ALLOWED_ROOMS:
        logger.info(f"New chatroom detected: '{data.room_id}' (sender: {data.sender})")
        return {"answer": ""}

    # If not a command, buffer the message and return
    if not data.is_command:
        message_buffers[data.room_id].append(f"[{data.sender}]: {data.msg}")
        if len(message_buffers[data.room_id]) >= BUFFER_SIZE:
            await flush_buffer(data.room_id)
        return {"answer": ""}

    # 1. Get SQL chat history for this room
    history = SQLChatMessageHistory(session_id=str(data.room_id), connection_string=CONNECTION_STRING)

    # 2. Search ChromaDB for relevant past messages
    relevant_docs = vectorstore.similarity_search(data.msg, k=3)
    context = "\n".join([doc.page_content for doc in relevant_docs]) if relevant_docs else ""

    # 3. Include unflushed buffer messages as recent context
    recent_buffer = message_buffers.get(data.room_id, [])
    buffer_context = "\n".join(recent_buffer) if recent_buffer else ""

    # 4. Build system prompt with RAG context + buffer
    system_content = SYSTEM_PROMPT + f"\n\n현재 대화 상대: {data.sender}"
    if context:
        system_content += f"\n\n참고할 수 있는 이전 대화 내용:\n{context}"
    if buffer_context:
        system_content += f"\n\n최근 채팅방 대화 (아직 저장 전):\n{buffer_context}"

    # 4. Build message list: system + history + new message
    #    Summarize if history is too long
    history_msgs = history.messages
    messages = [SystemMessage(content=system_content)]
    if len(history_msgs) > MAX_HISTORY_MESSAGES:
        summary = await summarize_history(history_msgs)
        messages.append(SystemMessage(content=f"이전 대화 요약:\n{summary}"))
        messages.extend(history_msgs[-MAX_HISTORY_MESSAGES:])
    else:
        messages.extend(history_msgs)
    messages.append(HumanMessage(content=data.msg))

    # 5. Invoke LLM (with tool calling support)
    response = llm_with_tools.invoke(messages)

    # Handle tool calls if the LLM wants to look up a character
    while response.tool_calls:
        messages.append(response)
        for tc in response.tool_calls:
            if tc["name"] == "lookup_maplestory_character":
                result = await lookup_maplestory_character.ainvoke(tc["args"])
                messages.append(ToolMessage(content=result, tool_call_id=tc["id"]))
        response = llm_with_tools.invoke(messages)

    # 6. Save to SQL history (include sender)
    history.add_user_message(f"[{data.sender}]: {data.msg}")
    history.add_ai_message(response.content)

    # 7. Save to ChromaDB (user message + AI response)
    now = datetime.now().isoformat()
    vectorstore.add_documents([
        Document(
            page_content=f"[{data.sender}]: {data.msg}",
            metadata={"room_id": str(data.room_id), "role": "user", "sender": data.sender, "timestamp": now},
        ),
        Document(
            page_content=f"[AI]: {response.content}",
            metadata={"room_id": str(data.room_id), "role": "assistant", "timestamp": now},
        )
    ])

    return {"answer": response.content}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)