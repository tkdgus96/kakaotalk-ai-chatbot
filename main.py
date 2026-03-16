import os
import json
import asyncio
import logging
import time
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
from exa_py import Exa

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI()

# Buffer config
BUFFER_SIZE = 20
message_buffers: dict[int, list[str]] = defaultdict(list)

# Max history messages before summarizing
MAX_HISTORY_MESSAGES = 10
RAG_SEARCH_K = int(os.getenv("RAG_SEARCH_K", "10"))

# Config
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
CONNECTION_STRING = os.getenv("DB_CONNECTION_STRING")
ALLOWED_ROOMS = set(int(r) for r in os.getenv("ALLOWED_ROOMS", "").split(",") if r.strip())
NEXON_API_KEY = os.getenv("NEXON_API_KEY")
NEXON_API_BASE = "https://open.api.nexon.com/maplestory/v1"
EXA_API_KEY = os.getenv("EXA_API_KEY")
exa = Exa(api_key=EXA_API_KEY)
KIS_APP_KEY = os.getenv("KIS_APP_KEY")
KIS_APP_SECRET = os.getenv("KIS_APP_SECRET")
KIS_BASE_URL = os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443")
kis_access_token = None
kis_access_token_expiry = 0.0

SYSTEM_PROMPT = """너는 카카오톡 채팅방의 친절한 AI 어시스턴트야.
친근하고 도움이 되는 말투로 대화해줘. 이모지도 적절히 사용해줘.
대화 기록을 기억하고 있으니, 이전 대화 맥락을 참고해서 답변해줘.
모르는 정보나 최신 정보가 필요한 질문을 받으면, 웹 검색 도구를 사용해서 정확한 정보를 찾아서 답변해줘.
주식/증권 관련 질문에서는 현재 가격이 필요하면 `get_stock_quote`를 우선 호출하고,
추가 설명이나 최신 이슈/뉴스가 필요하면 `web_search`도 함께 사용해."""

# LLM
llm = ChatOpenAI(model="gpt-4o", api_key=OPENAI_API_KEY, temperature=0.7, max_tokens=1000, verbose=True)

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

@tool
async def web_search(query: str) -> str:
    """웹에서 최신 정보를 검색합니다. 유저가 실시간 정보, 뉴스, 최신 이벤트, 주식 관련 배경 정보, 또는 AI가 모르는 정보에 대해 물어볼 때 사용하세요."""
    try:
        response = exa.search(
            query,
            num_results=3,
            type="auto",
            contents={
                "text": {
                    "max_characters": 3000,
                }
            },
        )
        results = []
        for r in response.results:
            results.append(f"제목: {r.title}\nURL: {r.url}\n내용: {r.text[:1000]}")
        return "\n\n---\n\n".join(results) if results else "검색 결과가 없습니다."
    except Exception as e:
        return f"검색 중 오류가 발생했습니다: {str(e)}"

@tool
async def get_stock_quote(symbol_or_name: str) -> str:
    """주식 심볼 또는 회사명(예: AAPL, META, 메타, 삼성전자)으로 최신 시세를 조회합니다. 현재가, 전일종가, 변동률 같은 실시간 수치가 필요할 때 사용하세요."""
    raw_query = symbol_or_name.strip()
    if not raw_query:
        return "심볼/회사명이 비어 있습니다. 예: META, 메타, 005930.KS"

    alias_map = {
        "메타": "META",
        "페이스북": "META",
        "meta": "META",
        "facebook": "META",
        "알파벳": "GOOGL",
        "구글": "GOOGL",
        "google": "GOOGL",
        "애플": "AAPL",
        "apple": "AAPL",
        "엔비디아": "NVDA",
        "nvidia": "NVDA",
        "마이크로소프트": "MSFT",
        "microsoft": "MSFT",
        "테슬라": "TSLA",
        "tesla": "TSLA",
        "카카오": "035720.KQ",
        "삼성전자": "005930.KS",
        "코스피": "^KS11",
        "kospi": "^KS11",
        "코스닥": "^KQ11",
        "kosdaq": "^KQ11",
    }

    normalized = raw_query.lower()
    primary_symbol = alias_map.get(normalized, raw_query.upper())

    candidate_symbols = []
    if primary_symbol:
        candidate_symbols.append(primary_symbol)

    # Handle 6-digit Korean stock codes robustly by trying both KOSPI/KOSDAQ suffixes.
    if raw_query.isdigit() and len(raw_query) == 6:
        candidate_symbols.extend([f"{raw_query}.KS", f"{raw_query}.KQ", raw_query])

    # De-duplicate while preserving order.
    candidate_symbols = list(dict.fromkeys(candidate_symbols))

    def get_krx_code(symbol: str):
        if symbol.isdigit() and len(symbol) == 6:
            return symbol
        upper_symbol = symbol.upper()
        if (upper_symbol.endswith(".KS") or upper_symbol.endswith(".KQ")) and upper_symbol[:-3].isdigit() and len(upper_symbol[:-3]) == 6:
            return upper_symbol[:-3]
        return None

    async def get_kis_token(client: httpx.AsyncClient):
        global kis_access_token, kis_access_token_expiry
        if not KIS_APP_KEY or not KIS_APP_SECRET:
            return None
        if kis_access_token and time.time() < kis_access_token_expiry - 60:
            return kis_access_token

        resp = await client.post(
            f"{KIS_BASE_URL}/oauth2/tokenP",
            json={
                "grant_type": "client_credentials",
                "appkey": KIS_APP_KEY,
                "appsecret": KIS_APP_SECRET,
            },
            headers={"content-type": "application/json"},
        )
        if resp.status_code != 200:
            return None
        data = resp.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            return None
        kis_access_token = token
        kis_access_token_expiry = time.time() + expires_in
        return kis_access_token

    async def fetch_kis_krx_quote(client: httpx.AsyncClient, code: str):
        def to_float(value, default=0.0):
            try:
                return float(value)
            except (TypeError, ValueError):
                return default

        token = await get_kis_token(client)
        if not token:
            return None, None

        resp = await client.get(
            f"{KIS_BASE_URL}/uapi/domestic-stock/v1/quotations/inquire-price",
            params={
                "FID_COND_MRKT_DIV_CODE": "J",
                "FID_INPUT_ISCD": code,
            },
            headers={
                "authorization": f"Bearer {token}",
                "appkey": KIS_APP_KEY,
                "appsecret": KIS_APP_SECRET,
                "tr_id": "FHKST01010100",
            },
        )
        if resp.status_code != 200:
            return None, f"KIS 시세 조회 실패: HTTP {resp.status_code}"

        data = resp.json()
        output = data.get("output", {})
        price = output.get("stck_prpr")
        if not price:
            return None, None

        quote = {
            "symbol": code,
            "shortName": output.get("hts_kor_isnm"),
            "currency": "KRW",
            "marketState": output.get("new_mkop_cls_code"),
            "regularMarketPrice": to_float(price),
            "regularMarketChange": to_float(output.get("prdy_vrss")),
            "regularMarketChangePercent": to_float(output.get("prdy_ctrt")),
            "regularMarketPreviousClose": to_float(output.get("stck_sdpr")),
            "regularMarketOpen": to_float(output.get("stck_oprc")),
            "regularMarketDayLow": to_float(output.get("stck_lwpr")),
            "regularMarketDayHigh": to_float(output.get("stck_hgpr")),
            "regularMarketTime": f"{output.get('stck_bsop_date', '')}{output.get('stck_cntg_hour', '')}",
            "source": "korea_investment_openapi",
        }
        return quote, None

    async def fetch_naver_krx_quote(client: httpx.AsyncClient, code: str):
        try:
            resp = await client.get(
                "https://polling.finance.naver.com/api/realtime",
                params={"query": f"SERVICE_ITEM:{code}|SERVICE_RECENT_ITEM:{code}"},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            if resp.status_code != 200:
                return None, f"네이버 시세 조회 실패: HTTP {resp.status_code}"

            raw_text = resp.text.strip()
            data = None
            try:
                data = resp.json()
            except Exception:
                # Some responses may arrive as JSONP-like text.
                if "(" in raw_text and raw_text.endswith(")"):
                    inner = raw_text[raw_text.find("(") + 1 : raw_text.rfind(")")]
                    data = json.loads(inner)

            if not isinstance(data, dict):
                return None, None

            areas = data.get("result", {}).get("areas", [])
            if not areas:
                return None, None
            datas = areas[0].get("datas", [])
            if not datas:
                return None, None
            item = datas[0]

            quote = {
                "symbol": f"{code}",
                "shortName": item.get("nm"),
                "currency": "KRW",
                "marketState": item.get("ms"),
                "regularMarketPrice": item.get("nv"),
                "regularMarketChange": item.get("cv"),
                "regularMarketChangePercent": item.get("cr"),
                "regularMarketPreviousClose": item.get("pcv"),
                "regularMarketOpen": item.get("ov"),
                "regularMarketDayLow": item.get("lv"),
                "regularMarketDayHigh": item.get("hv"),
                "regularMarketTime": f"{item.get('dt', '')}{item.get('tm', '')}",
                "source": "naver_finance_realtime",
            }
            if quote["regularMarketPrice"] is None:
                return None, None
            return quote, None
        except Exception:
            return None, None

    async def fetch_quote(client: httpx.AsyncClient, symbol: str):
        last_status = None
        for attempt in range(3):
            resp = await client.get(
                "https://query1.finance.yahoo.com/v7/finance/quote",
                params={"symbols": symbol},
                headers={"User-Agent": "Mozilla/5.0"},
            )
            last_status = resp.status_code
            if resp.status_code == 429 and attempt < 2:
                await asyncio.sleep(0.7 * (attempt + 1))
                continue
            if resp.status_code != 200:
                return None, f"시세 조회 실패: HTTP {resp.status_code}"
            data = resp.json()
            results = data.get("quoteResponse", {}).get("result", [])
            if not results:
                return None, None
            yq = results[0]
            yq["source"] = "yahoo_finance"
            return yq, None

        return None, f"시세 조회 실패: HTTP {last_status}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            quote = None
            err = None
            resolved_symbol = candidate_symbols[0]

            for symbol in candidate_symbols:
                resolved_symbol = symbol

                krx_code = get_krx_code(symbol)
                if krx_code:
                    quote, err = await fetch_kis_krx_quote(client, krx_code)
                    if quote is not None:
                        break
                    quote, err = await fetch_naver_krx_quote(client, krx_code)
                    if quote is not None:
                        break
                    if err:
                        # Continue to Yahoo fallback for KRX symbols.
                        pass

                quote, err = await fetch_quote(client, symbol)
                if quote is not None:
                    break
                if err and "HTTP 429" not in err:
                    break

            # Fallback: resolve company name to ticker via Yahoo search API.
            if quote is None and err is None:
                search_resp = await client.get(
                    "https://query1.finance.yahoo.com/v1/finance/search",
                    params={"q": raw_query, "quotesCount": 5, "newsCount": 0},
                )
                if search_resp.status_code == 200:
                    search_data = search_resp.json()
                    quotes = search_data.get("quotes", [])
                    equity = next((q for q in quotes if q.get("quoteType") == "EQUITY"), None)
                    if equity and equity.get("symbol"):
                        resolved_symbol = equity["symbol"]
                        quote, err = await fetch_quote(client, resolved_symbol)

            if err and "HTTP 429" in err:
                # Yahoo rate-limit fallback: return web snippets so user still gets near-real-time context.
                try:
                    fallback = exa.search(
                        f"{raw_query} 주가 현재 KRX 코스피 코스닥",
                        num_results=3,
                        type="auto",
                        contents={"text": {"max_characters": 1200}},
                    )
                    snippets = []
                    for r in fallback.results:
                        snippets.append(f"제목: {r.title}\nURL: {r.url}\n내용: {r.text[:500]}")
                    if snippets:
                        return (
                            "실시간 시세 API가 일시적으로 혼잡합니다(429).\n"
                            "대체 웹 검색 결과를 참고해 주세요:\n\n"
                            + "\n\n---\n\n".join(snippets)
                        )
                except Exception:
                    pass

                return (
                    "실시간 시세 API가 일시적으로 혼잡합니다(429). "
                    "잠시 후 다시 시도하거나 심볼로 재시도해 주세요. "
                    f"(입력: {raw_query})"
                )

            if err:
                return err
            if quote is None:
                return f"'{raw_query}'에 해당하는 시세를 찾지 못했습니다. 심볼(예: META, 005930.KS)로 다시 시도해 주세요."

            output = {
                "query": raw_query,
                "resolvedSymbol": resolved_symbol,
                "source": quote.get("source", "unknown"),
                "symbol": quote.get("symbol", resolved_symbol),
                "shortName": quote.get("shortName"),
                "currency": quote.get("currency"),
                "marketState": quote.get("marketState"),
                "regularMarketPrice": quote.get("regularMarketPrice"),
                "regularMarketChange": quote.get("regularMarketChange"),
                "regularMarketChangePercent": quote.get("regularMarketChangePercent"),
                "regularMarketPreviousClose": quote.get("regularMarketPreviousClose"),
                "regularMarketOpen": quote.get("regularMarketOpen"),
                "regularMarketDayLow": quote.get("regularMarketDayLow"),
                "regularMarketDayHigh": quote.get("regularMarketDayHigh"),
                "regularMarketTime": quote.get("regularMarketTime"),
            }
            logger.info(
                "stock_quote query=%s resolved=%s source=%s price=%s",
                raw_query,
                output["symbol"],
                output["source"],
                output["regularMarketPrice"],
            )
            return json.dumps(output, ensure_ascii=False, indent=2)
    except Exception as e:
        return f"시세 조회 중 오류가 발생했습니다: {str(e)}"

# LLM with tool calling
tools = [lookup_maplestory_character, get_stock_quote, web_search]
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
    old_messages = messages[:-MAX_HISTORY_MESSAGES][-20:]
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
    relevant_docs = vectorstore.similarity_search(data.msg, k=RAG_SEARCH_K)
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

    # Handle tool calls
    tool_map = {t.name: t for t in tools}
    while response.tool_calls:
        messages.append(response)
        for tc in response.tool_calls:
            func = tool_map.get(tc["name"])
            if func:
                result = await func.ainvoke(tc["args"])
            else:
                result = f"Unknown tool: {tc['name']}"
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
