# KakaoTalk AI Bot

FastAPI-based KakaoTalk chatbot backend with:
- OpenAI (LangChain) chat + tool calling
- Conversation memory in PostgreSQL (`SQLChatMessageHistory`) for the KakaoTalk webhook
- Long-term semantic memory in ChromaDB (RAG)
- LangGraph-based agent graph for local dev chat UI testing
- Built-in tools: Naver search (Korean), Tavily web search (global), MapleStory lookup, stock quotes

## Features

- `POST /chat` endpoint for KakaoTalk messages
- Room allowlist control (`/rooms` endpoints)
- Message buffering + periodic context summarization
- RAG retrieval from Chroma before each command response
- Tool-calling flow (LLM can call external tools during response generation)
- Dev chat UI via `langgraph dev` + Agent Chat UI (LangGraph `StateGraph` mirrors webhook pipeline: retrieve → chat ↔ tools → store)
- MapleStory weekly boss management MVP
  - Weekly boss registration and weekly schedule setting
  - Thursday reset reminder scheduler (KST)
  - 30-minute pre-boss reminder scheduler
  - Drop registration + mesos parser (억/만)
  - Settlement creation/completion/history
  - Read-only settlement page: `/s/{publicToken}`

## Project Structure

```text
app/
  api.py                # FastAPI routes
  config.py             # env-based settings
  dependencies.py       # shared singletons (llm, vectorstore, logger, buffers)
  graph.py              # LangGraph StateGraph (used by `langgraph dev`)
  models.py             # request models
  prompts.py            # system prompts
  services/chat_service.py
  tools/
    maplestory.py       # lookup_maplestory_character (Nexon Open API)
    search.py           # naver_search (Naver) + web_search (Tavily)
    stock.py            # get_stock_quote (KIS / Yahoo / Naver / Tavily fallback)
langgraph.json          # LangGraph dev config (registers graph as "agent")
main.py                 # FastAPI entrypoint (uvicorn main:app)
```

## Requirements

- Python 3.10+
- PostgreSQL (for KakaoTalk webhook chat history — dev UI uses LangGraph in-memory thread store, so SQLite is fine)
- OpenAI API key
- (Recommended) Naver Developers credentials for Korean search (free 25k queries/day)
- (Recommended) Tavily API key for global web search (free 1k queries/month)
- (Optional) Nexon API key for MapleStory tool
- (Optional) Korea Investment API credentials for KRX stock quotes

## Quick Start

### 1) Create virtualenv and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2) Configure environment

```bash
cp .env.example .env
# then edit .env
```

### 3) Start dependencies (optional via Docker)

```bash
docker compose up -d db chromadb
```

### 4) Run server

```bash
.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000
```

## Dev Chat UI (LangGraph)

For local interactive testing of the full agent pipeline (RAG + tools + memory) without going through the KakaoTalk webhook, run the LangGraph dev server and connect Agent Chat UI to it.

### 1) Get API keys

- Naver: https://developers.naver.com/apps — register an app, enable 검색 API → set `NAVER_CLIENT_ID`, `NAVER_CLIENT_SECRET`
- Tavily: https://app.tavily.com → set `TAVILY_API_KEY`

### 2) Run the dev server

```bash
.venv/bin/langgraph dev --port 2024 --no-browser
```

This loads `langgraph.json` and serves the graph (id: `agent`) defined in `app/graph.py`.

### 3) Connect Agent Chat UI

Open https://agentchat.vercel.app/ and configure:
- **Deployment URL**: `http://127.0.0.1:2024`
- **Assistant/Graph ID**: `agent`
- **LangSmith API Key**: leave blank

The graph mirrors the webhook pipeline:

```
START → retrieve (RAG from Chroma)
      → chat (LLM with system prompt + tools)
      ↔ tools (web/stock/maplestory)
      → store (persist user+AI turns to Chroma)
      → END
```

Per-thread defaults `room_id`/`sender` come from `PLAYGROUND_ROOM_ID` / `PLAYGROUND_SENDER` env vars and can be overridden via Agent Chat UI's configurable params.

## PM2 Deployment

This project includes `ecosystem.config.js`.

```bash
npx pm2 start ecosystem.config.js
npx pm2 logs kakao-talk-ai-bot
npx pm2 restart kakao-talk-ai-bot --update-env
```

## API

### `POST /chat`

Request:

```json
{
  "room_id": 123,
  "room": "My Room",
  "msg": "삼성전자 주가 알려줘",
  "sender": "Alice",
  "is_command": true
}
```

Response:

```json
{
  "answer": "..."
}
```

Behavior:
- If `room_id` is not in allowlist, bot returns empty answer.
- If `is_command` is `false`, message is buffered only.
- If `is_command` is `true`, bot retrieves context + history and responds.
- If message starts with supported boss command, boss command is handled directly.

### `GET /s/{publicToken}`
- Read-only settlement page for mobile view.
- No login required in MVP.

## Boss Commands

- `!보스매주 [bossName]`
- `!보스시간 [bossName] [dayOfWeek] [HH:mm]`
- `!이번주보스`
- `!드랍 [itemName] [price]`
- `!드랍 [bossName] [itemName] [price]`
- `!정산 [bossName] [memberCount]`
- `!정산완료 [settlementCode]`
- `!정산목록`

Examples:
- `!보스매주 검마`
- `!보스시간 검마 토요일 22:00`
- `!드랍 루컨마 84억`
- `!드랍 검마 몽벨 220억`
- `!정산 검마 4`
- `!정산완료 B105`

### Room management

- `GET /rooms`
- `POST /rooms/{room_id}`
- `DELETE /rooms/{room_id}`

### Debug

- `POST /debug` echoes raw request JSON.

### Bot Outbox (Kakao sender bridge)

- `GET /bot/outbox?bot_id=main&limit=10`
  - Returns `PENDING` messages with `scheduled_at <= now`.
  - Response items: `id`, `room_id`, `room_name`, `message`.
- `POST /bot/outbox/{id}/ack`
  - Body: `{"status":"SENT"}` or `{"status":"FAILED"}`
  - Marks outbox delivery result. `SENT` sets `sent_at`.

Delivery model:
- Backend scheduler never sends KakaoTalk directly.
- Scheduler writes reminders to `bot_outbox`.
- Android Kakao script polls `/bot/outbox`, calls `Api.replyRoom(room_name, message)`, then `ack`.

## Tests

```bash
env ANONYMIZED_TELEMETRY=false CHROMA_TELEMETRY=false \
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## Notes

- For the KakaoTalk webhook (`uvicorn main:app`), `DB_CONNECTION_STRING` must point to a reachable PostgreSQL instance.
- For local dev (`langgraph dev`), SQLite is fine — set `DB_CONNECTION_STRING=sqlite:///./data/chat_history.db`.
- Chroma persistent data is stored in `./chroma_db` (shared between webhook and dev UI for cross-session RAG memory).
- Search tools: `naver_search` is preferred for Korean topics (faster indexing for same-day news); `web_search` (Tavily) is for global/English content. The system prompt routes the agent between them.
- Stock tool uses multiple sources/fallbacks (KIS → Yahoo → Naver → Tavily on 429); for best KRX reliability, set KIS credentials.
- Boss MVP schema is in `migrations/001_boss_mvp.sql` and auto-initialized on startup.
- Outbox/room metadata schema is in `migrations/002_outbox_and_room_metadata.sql` and auto-initialized on startup.
