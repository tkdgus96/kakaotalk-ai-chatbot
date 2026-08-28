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
from app.services.reminder_service import handle_recurring_command
from app.services.image_service import (
    IMAGE_COMMANDS,
    detect_image_generation,
    generate_image_b64,
    handle_image_command,
    maybe_answer_with_image,
    start_image_collection,
    take_generated_images,
)
from app.services.usage_service import allow_chat, allow_image_gen
from app.services.memory_service import handle_memory_command
from app.services.game_stats import handle_game_command
from app.services.summary_service import handle_summary_command
from app.services.feedback_service import maybe_log_correction


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


def _help_text() -> str:
    return (
        "[온반봇 명령어]\n"
        "• 대화/질문: 메시지 앞에 ! (예: !오늘 날씨, !삼성전자 주가)\n"
        "• 이미지 생성: !그림 [설명] 또는 !<설명> 그려줘\n"
        "• 이미지 검색: !이미지 [검색어]\n"
        "• 사진 이해: 사진 올린 뒤 !이 사진 뭐야\n"
        "• 기억: !기억 / !기억삭제 [키워드] / !기억끄기 / !기억켜기\n"
        "• 대화 요약: !요약 [오늘|어제|N일]\n"
        "• 게임 랭킹: !기록 / !단어기록 / !챌린지기록 / !내기록 (기간: 주/월/오늘)\n"
        "• 보스: !보스도움\n"
        "• 매일 알림: !매일도움"
    )


async def handle_chat(data: KakaoMsg):
    boss_service.touch_room(data.room_id, data.room)
    logger.info("chat_in room_id=%s is_command=%s msg=%r", data.room_id, data.is_command, data.msg)

    parsed = parse_command(data.msg)
    if parsed:
        logger.info("parsed_command room_id=%s name=%s args=%r", data.room_id, parsed.name, parsed.args)
        answer = handle_boss_command(data.room_id, data.sender, parsed.name, parsed.args)
        if answer is None:
            answer = handle_recurring_command(data.room_id, data.sender, parsed.name, parsed.args)
        if answer is None:
            answer = handle_memory_command(data.room_id, data.sender, parsed.name, parsed.args)
        if answer is None:
            answer = handle_game_command(data.room_id, data.sender, parsed.name, parsed.args)
        if answer is None:
            answer = await handle_summary_command(data.room_id, parsed.name, parsed.args)
        if answer is None and parsed.name == "!도움말":
            answer = _help_text()
        if answer is not None:
            return {"answer": answer}
        if parsed.name in IMAGE_COMMANDS:
            if not allow_image_gen(data.sender, settings.image_gen_daily_limit):
                return {"answer": f"오늘 이미지는 여기까지야 (하루 {settings.image_gen_daily_limit}장). 내일 다시 해줘!"}
            img_result = await handle_image_command(parsed.name, parsed.args)
            if img_result is not None:
                text, images = img_result
                return {"answer": text, "images": images}
    elif data.msg.strip().startswith(("!", "！")):
        logger.info("Command-like message not parsed: room_id=%s raw=%r", data.room_id, data.msg)

    if data.room_id not in settings.allowed_rooms:
        logger.info("New chatroom detected: '%s' (sender: %s)", data.room_id, data.sender)
        return {"answer": ""}

    # Deterministic image generation for "<묘사> 그려줘" (before vision, since
    # "그림 그려줘" would otherwise look like a reference to a recent photo).
    if data.is_command:
        gen_prompt = detect_image_generation(data.msg)
        if gen_prompt:
            await asyncio.to_thread(
                add_chat_log, data.room_id, data.sender, data.msg, datetime.now().isoformat()
            )
            if not allow_image_gen(data.sender, settings.image_gen_daily_limit):
                return {"answer": f"오늘 이미지는 여기까지야 (하루 {settings.image_gen_daily_limit}장). 내일 다시 해줘!"}
            b64 = await generate_image_b64(gen_prompt)
            if b64:
                return {"answer": f"🎨 '{gen_prompt}' 그려봤어", "images": [b64]}
            return {"answer": "이미지 생성에 실패했어. 잠시 후 다시 시도해줘."}

    # A command that refers to a recently posted photo -> answer via vision.
    if data.is_command:
        vision_answer = await maybe_answer_with_image(data.room_id, data.msg)
        if vision_answer:
            await asyncio.to_thread(
                add_chat_log, data.room_id, data.sender, data.msg, datetime.now().isoformat()
            )
            return {"answer": vision_answer}

    await asyncio.to_thread(
        add_chat_log, data.room_id, data.sender, data.msg, datetime.now().isoformat()
    )

    # Feedback loop: capture corrections that directly follow a bot answer.
    await asyncio.to_thread(maybe_log_correction, data.room_id, data.sender, data.msg)

    if not data.is_command:
        message_buffers[data.room_id].append(f"[{data.sender}]: {data.msg}")
        if len(message_buffers[data.room_id]) >= settings.buffer_size:
            await flush_buffer(data.room_id)
        return {"answer": ""}

    # Per-room chat throttle: protect against spam-driven LLM cost.
    if not allow_chat(data.room_id, settings.chat_per_min_limit):
        logger.info("chat throttled room_id=%s", data.room_id)
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

    start_image_collection(data.room_id)
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

    images = take_generated_images(data.room_id)
    if images:
        return {"answer": answer, "images": images}
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
