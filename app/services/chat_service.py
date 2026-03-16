from datetime import datetime

from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.documents import Document
from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage

from app.config import settings
from app.dependencies import llm, logger, message_buffers, vectorstore
from app.models import KakaoMsg
from app.prompts import FILTER_SYSTEM_PROMPT, SUMMARIZE_SYSTEM_PROMPT, SYSTEM_PROMPT
from app.tools import llm_with_tools, tools


async def flush_buffer(room_id: int):
    buffer = message_buffers.pop(room_id, [])
    if not buffer:
        return

    conversation = "\n".join(buffer)
    filter_prompt = [
        SystemMessage(content=FILTER_SYSTEM_PROMPT),
        HumanMessage(content=conversation),
    ]

    response = llm.invoke(filter_prompt)
    summary = response.content.strip()
    if summary and summary != "없음":
        vectorstore.add_documents(
            [
                Document(
                    page_content=summary,
                    metadata={
                        "room_id": str(room_id),
                        "role": "context_summary",
                        "timestamp": datetime.now().isoformat(),
                    },
                )
            ]
        )


async def summarize_history(messages):
    old_messages = messages[: -settings.max_history_messages][-20:]
    conversation = "\n".join(
        f"{'[User]' if msg.type == 'human' else '[AI]'}: {msg.content}" for msg in old_messages
    )
    summary_prompt = [
        SystemMessage(content=SUMMARIZE_SYSTEM_PROMPT),
        HumanMessage(content=conversation),
    ]
    response = llm.invoke(summary_prompt)
    return response.content


async def handle_chat(data: KakaoMsg):
    if data.room_id not in settings.allowed_rooms:
        logger.info("New chatroom detected: '%s' (sender: %s)", data.room_id, data.sender)
        return {"answer": ""}

    if not data.is_command:
        message_buffers[data.room_id].append(f"[{data.sender}]: {data.msg}")
        if len(message_buffers[data.room_id]) >= settings.buffer_size:
            await flush_buffer(data.room_id)
        return {"answer": ""}

    history = SQLChatMessageHistory(
        session_id=str(data.room_id),
        connection_string=settings.db_connection_string,
    )

    relevant_docs = vectorstore.similarity_search(data.msg, k=settings.rag_search_k)
    context = "\n".join([doc.page_content for doc in relevant_docs]) if relevant_docs else ""

    recent_buffer = message_buffers.get(data.room_id, [])
    buffer_context = "\n".join(recent_buffer) if recent_buffer else ""

    system_content = SYSTEM_PROMPT + f"\n\n현재 대화 상대: {data.sender}"
    if context:
        system_content += f"\n\n참고할 수 있는 이전 대화 내용:\n{context}"
    if buffer_context:
        system_content += f"\n\n최근 채팅방 대화 (아직 저장 전):\n{buffer_context}"

    history_msgs = history.messages
    messages = [SystemMessage(content=system_content)]
    if len(history_msgs) > settings.max_history_messages:
        summary = await summarize_history(history_msgs)
        messages.append(SystemMessage(content=f"이전 대화 요약:\n{summary}"))
        messages.extend(history_msgs[-settings.max_history_messages :])
    else:
        messages.extend(history_msgs)
    messages.append(HumanMessage(content=data.msg))

    response = llm_with_tools.invoke(messages)
    tool_map = {t.name: t for t in tools}
    while response.tool_calls:
        messages.append(response)
        for tool_call in response.tool_calls:
            func = tool_map.get(tool_call["name"])
            result = await func.ainvoke(tool_call["args"]) if func else f"Unknown tool: {tool_call['name']}"
            messages.append(ToolMessage(content=result, tool_call_id=tool_call["id"]))
        response = llm_with_tools.invoke(messages)

    history.add_user_message(f"[{data.sender}]: {data.msg}")
    history.add_ai_message(response.content)

    now = datetime.now().isoformat()
    vectorstore.add_documents(
        [
            Document(
                page_content=f"[{data.sender}]: {data.msg}",
                metadata={
                    "room_id": str(data.room_id),
                    "role": "user",
                    "sender": data.sender,
                    "timestamp": now,
                },
            ),
            Document(
                page_content=f"[AI]: {response.content}",
                metadata={"room_id": str(data.room_id), "role": "assistant", "timestamp": now},
            ),
        ]
    )

    return {"answer": response.content}
