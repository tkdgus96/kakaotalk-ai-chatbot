import asyncio
from datetime import datetime

from langchain_community.chat_message_histories import SQLChatMessageHistory
from langchain_core.documents import Document
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.boss.services.command_parser import parse_command
from app.chat_log import add_chat_log
from app.config import settings
from app.dependencies import boss_service, llm, logger, message_buffers, vectorstore
from app.graph import graph
from app.models import KakaoMsg
from app.prompts import FILTER_SYSTEM_PROMPT, SUMMARIZE_SYSTEM_PROMPT


async def flush_buffer(room_id: int):
    buffer = message_buffers.pop(room_id, [])
    if not buffer:
        return

    conversation = "\n".join(buffer)
    filter_prompt = [
        SystemMessage(content=FILTER_SYSTEM_PROMPT),
        HumanMessage(content=conversation),
    ]

    response = await llm.ainvoke(filter_prompt)
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
    response = await llm.ainvoke(summary_prompt)
    return response.content


async def handle_chat(data: KakaoMsg):
    boss_service.touch_room(data.room_id, data.room)
    logger.info("chat_in room_id=%s is_command=%s msg=%r", data.room_id, data.is_command, data.msg)

    parsed = parse_command(data.msg)
    if parsed:
        logger.info("parsed_command room_id=%s name=%s args=%r", data.room_id, parsed.name, parsed.args)
        answer = handle_boss_command(data.room_id, data.sender, parsed.name, parsed.args)
        if answer is not None:
            return {"answer": answer}
    elif data.msg.strip().startswith(("!", "！")):
        logger.info("Command-like message not parsed: room_id=%s raw=%r", data.room_id, data.msg)

    if data.room_id not in settings.allowed_rooms:
        logger.info("New chatroom detected: '%s' (sender: %s)", data.room_id, data.sender)
        return {"answer": ""}

    await asyncio.to_thread(
        add_chat_log, data.room_id, data.sender, data.msg, datetime.now().isoformat()
    )

    if not data.is_command:
        message_buffers[data.room_id].append(f"[{data.sender}]: {data.msg}")
        if len(message_buffers[data.room_id]) >= settings.buffer_size:
            await flush_buffer(data.room_id)
        return {"answer": ""}

    history = SQLChatMessageHistory(
        session_id=str(data.room_id),
        connection_string=settings.db_connection_string,
    )
    history_msgs = history.messages

    graph_messages: list = []
    if len(history_msgs) > settings.max_history_messages:
        summary = await summarize_history(history_msgs)
        graph_messages.append(SystemMessage(content=f"이전 대화 요약:\n{summary}"))
        graph_messages.extend(history_msgs[-settings.max_history_messages :])
    else:
        graph_messages.extend(history_msgs)
    graph_messages.append(HumanMessage(content=f"[{data.sender}]: {data.msg}"))

    recent_buffer = message_buffers.get(data.room_id, [])
    buffer_context = "\n".join(recent_buffer) if recent_buffer else ""

    result = await graph.ainvoke(
        {
            "messages": graph_messages,
            "buffer_context": buffer_context,
        },
        config={"configurable": {"room_id": data.room_id, "sender": data.sender}},
    )

    answer = ""
    for m in reversed(result["messages"]):
        if isinstance(m, AIMessage) and m.content and not getattr(m, "tool_calls", None):
            answer = m.content if isinstance(m.content, str) else str(m.content)
            break

    history.add_user_message(f"[{data.sender}]: {data.msg}")
    history.add_ai_message(answer)

    return {"answer": answer}


def handle_boss_command(room_id: int, sender: str, name: str, args: list[str]) -> str | None:
    if name in ("!보스도움", "!보스도움말", "!보스help", "!bosshelp"):
        return (
            "[보스 기능 사용법]\n\n"
            "1) 주간 보스 등록\n"
            "!보스매주 [bossName]\n"
            "예) !보스매주 검마\n\n"
            "2) 주간 보스 해제\n"
            "!보스해제 [bossName]\n"
            "예) !보스해제 검마\n\n"
            "3) 이번 주 보스 시간 등록/수정\n"
            "!보스시간 [bossName] [요일] [HH:mm]\n"
            "예) !보스시간 검마 토요일 22:00\n\n"
            "4) 이번 주 보스 일정 조회\n"
            "!이번주보스\n\n"
            "5) 드랍 등록\n"
            "!드랍 [itemName] [price]\n"
            "!드랍 [bossName] [itemName] [price]\n"
            "예) !드랍 루컨마 84억\n"
            "예) !드랍 검마 몽벨 220억\n\n"
            "6) 정산\n"
            "!정산 [bossName] [memberCount]\n"
            "예) !정산 검마 4\n\n"
            "7) 정산 완료 처리\n"
            "!정산완료 [settlementCode]\n"
            "예) !정산완료 B105\n\n"
            "8) 최근 정산 목록\n"
            "!정산목록"
        )

    if name == "!보스매주":
        if len(args) != 1:
            return "사용법: !보스매주 [bossName]"
        return boss_service.register_weekly_boss(room_id, args[0])

    if name == "!보스해제":
        if len(args) != 1:
            return "사용법: !보스해제 [bossName]"
        return boss_service.disable_weekly_boss(room_id, args[0])

    if name == "!보스시간":
        if len(args) != 3:
            return "사용법: !보스시간 [bossName] [dayOfWeek] [HH:mm]"
        return boss_service.set_boss_time(room_id, args[0], args[1], args[2])

    if name == "!이번주보스":
        return boss_service.list_week_bosses(room_id)

    if name == "!드랍":
        return boss_service.register_drop(room_id, sender, args)

    if name == "!정산":
        if len(args) != 2:
            return "사용법: !정산 [bossName] [memberCount]"
        return boss_service.create_settlement(room_id, sender, args[0], args[1])

    if name == "!정산완료":
        if len(args) != 1:
            return "사용법: !정산완료 [settlementCode]"
        return boss_service.complete_settlement(args[0])

    if name == "!정산목록":
        return boss_service.settlement_history(room_id)

    return None
