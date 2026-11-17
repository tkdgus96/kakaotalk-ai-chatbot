# KakaoTalk AI Bot

FastAPI-based KakaoTalk chatbot backend with:
- OpenAI (LangChain) chat + tool calling
- Recency memory in PostgreSQL/SQLite (`SQLChatMessageHistory`) for the KakaoTalk webhook
- Long-term semantic memory in ChromaDB (conversation summaries + per-user facts)
- Keyword/verbatim recall over raw turns via SQLite FTS5 (trigram) — raw turns are not embedded
- LangGraph-based agent graph (shared by the webhook and the dev chat UI)
- Built-in tools: Naver search (Korean), Tavily web search (global), OpenWeather, MapleStory lookup, stock quotes

## Features

- `POST /chat` endpoint for KakaoTalk messages
- Room allowlist control (`/rooms` endpoints)
- Message buffering + periodic context summarization
- RAG retrieval from Chroma before each command response
- Tool-calling flow (LLM can call external tools during response generation)
- KakaoTalk 답장(reply) awareness: replying to a message with `!질문` answers about that message;
  replying to one of the bot's own messages works without the `!` trigger
- Ops report (`!리포트`, daily/weekly email + KakaoTalk self-chat): requests per room, cost,
  tool latency/failures, image count, user corrections
- Provider-agnostic chat model (`CHAT_MODEL`, `LLM_FALLBACK_MODEL`; defaults stay on OpenAI)
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
  graph.py              # LangGraph StateGraph: retrieve → chat ⇄ tools → store
  chat_log.py           # SQLite FTS5 (trigram) keyword recall over raw turns
  persona.py            # per-room persona evolution (weekly refresh)
  models.py             # request models
  prompts.py            # system prompts (A/B tone variants)
  services/chat_service.py
  tools/
    maplestory.py       # lookup_maplestory_character (Nexon Open API)
    search.py           # naver_search (Naver) + web_search (Tavily)
    stock.py            # get_stock_quote (KIS / Yahoo / Naver / Tavily fallback)
    weather.py          # get_weather (OpenWeather current + 24h forecast)
langgraph.json          # LangGraph dev config (registers graph as "agent")
main.py                 # FastAPI entrypoint (uvicorn main:app)
```

## Requirements

- Python 3.10+
- PostgreSQL (for KakaoTalk webhook chat history — dev UI uses LangGraph in-memory thread store, so SQLite is fine)
- OpenAI API key
- (Recommended) Naver Developers credentials for Korean search (free 25k queries/day)
- (Recommended) Tavily API key for global web search (free 1k queries/month)
- (Optional) OpenWeather API key for the weather tool
- (Optional) Nexon API key for MapleStory tool
- (Optional) Korea Investment API credentials for KRX stock quotes

## Quick Start

### 1) Create virtualenv and install

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Web crawling (news / URL reading / web fallbacks) needs the Playwright browser.
# Without this, crawl4ai fails with "Executable doesn't exist ... chrome-headless-shell".
.venv/bin/playwright install chromium chromium-headless-shell
sudo .venv/bin/playwright install-deps chromium   # system libs (Linux)
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
START → retrieve (Chroma summaries + FTS keyword recall + user facts + persona)
      → chat (LLM with system prompt + tools)
      ↔ tools (search/weather/stock/maplestory)
      → store (extract per-user facts; raw turns go to FTS, not embedded)
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
- `!보스해제 [bossName]`
- `!보스시간 [bossName] [dayOfWeek] [HH:mm]`
- `!이번주보스`
- `!드랍 [itemName] [price]`
- `!드랍 [bossName] [itemName] [price]`
- `!정산 [bossName] [memberCount]`
- `!정산완료 [settlementCode]`
- `!정산목록`

Examples:
- `!보스매주 검마`
- `!보스해제 검마`
- `!보스시간 검마 토요일 22:00`
- `!드랍 루컨마 84억`
- `!드랍 검마 몽벨 220억`
- `!정산 검마 4`
- `!정산완료 B105`

## Reminders

Recurring/one-shot reminders are LLM tools, not commands — just say
`!매일 아침 8시에 날씨 알려줘` or `!매주 월요일 9시에 ~`. (The former `!매일*` commands were retired.)

## Ops Report (operator)

- `!리포트 [오늘|주간|N일]` — requests per room, estimated cost, tool latency/failures, images,
  user corrections, injection blocks, reminder deliveries. Allowed for `ADMIN_SENDERS`,
  inside `ADMIN_ROOM_ID`, or in the bot's self-chat (`REPORT_CHAT_ID`).
- Scheduled: daily at `COST_REPORT_HOUR`, weekly on `REPORT_WEEKLY_WEEKDAY`, over `REPORT_CHANNELS`
  (`email` via SMTP alert settings, `kakao` via `bot_outbox` → `REPORT_CHAT_ID`).
- Data: `llm_usage`, `ops_event` (turn/tool latency, throttles), `feedback_log`, `audit_log`.

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

### Iris Bridge (redroid — phone replacement)

- `POST /iris` — webhook target for the [Iris](https://github.com/dolidolih/Iris) bridge; converts
  the payload to `KakaoMsg` and runs the same pipeline. Replies go back via Iris `POST /reply`.
- `GET /iris/rooms` — current `chat_id -> room_id` mapping (`IRIS_ROOM_MAP` 작성용).
- `ENABLE_IRIS_SENDER=true` — the backend delivers `bot_outbox` rows itself through Iris
  (replaces phone polling; exact-time delivery for reminders and reports).
- 답장(reply, KakaoTalk type 26) attachments are parsed: the quoted text and author are passed
  to the agent; `IRIS_REPLY_TO_BOT_IS_COMMAND=true` makes replies to bot messages count as `!`.
- `@멘션` attachments are parsed: mentioned members' facts are loaded for the answer, and
  `@봇이름 질문` counts as `!` (`IRIS_MENTION_IS_COMMAND=true`).
- Stable identity: `(room_id, user_id) -> name` is tracked in `room_member`; when a member renames
  themselves, name-keyed data (FTS log, user facts, memory opt-out, feedback/usage) is migrated so
  history and stats carry over (`app/services/member_service.py`).
- Message types are classified (`classify_message`): emoticon-only and system-feed rows are dropped;
  video/audio/file/location/contact/schedule become placeholders like `(파일: a.pdf)` in the room
  buffer; KakaoTalk's own `created_at` is used as the log timestamp and for the inbound-delay metric.
- Setup guide: `deploy/iris/README.md` (redroid compose, kernel prereqs, room_id 연속성 매핑, 전환 절차).

## Tests

```bash
env ANONYMIZED_TELEMETRY=false CHROMA_TELEMETRY=false \
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## Notes

- For the KakaoTalk webhook (`uvicorn main:app`), `DB_CONNECTION_STRING` must point to a reachable PostgreSQL instance.
- For local dev (`langgraph dev`), SQLite is fine — set `DB_CONNECTION_STRING=sqlite:///./data/chat_history.db`.
- Chroma persistent data is stored in `./chroma_db` (shared between webhook and dev UI for cross-session memory).
- Memory model: recency via `SQLChatMessageHistory`; long-term semantic recall via Chroma `context_summary` docs; verbatim/keyword recall via SQLite FTS5 (`app/chat_log.py`, trigram); per-user facts loaded all-by-key from Chroma. Raw conversation turns are not embedded (avoids cost, noise, and stale-data recall).
- Search tools: `naver_search` is preferred for Korean topics (faster indexing for same-day news); `web_search` (Tavily) is for global/English content. The system prompt routes the agent between them.
- Chat models are built by `app/llm_factory.py`. `CHAT_MODEL` / `LIGHT_MODEL` / `FACT_MODEL` accept
  `"<model>"` (OpenAI) or `"<provider>:<model>"`; non-OpenAI providers need their `langchain-<provider>`
  package and API key (separate billing). `LLM_FALLBACK_MODEL` is tried when the primary call raises.
- Stock tool uses multiple sources/fallbacks (KIS → Yahoo → Naver → Tavily on 429); for best KRX reliability, set KIS credentials.
- Boss MVP schema is in `migrations/001_boss_mvp.sql` and auto-initialized on startup.
- Outbox/room metadata schema is in `migrations/002_outbox_and_room_metadata.sql` and auto-initialized on startup.
