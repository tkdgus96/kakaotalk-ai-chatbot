# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KakaoTalk AI chatbot backend. A FastAPI server that receives KakaoTalk group-chat messages via `POST /chat` and replies using OpenAI **gpt-4o** through a LangGraph agent pipeline (LangChain tool-calling). It has long-term memory, per-user facts, evolving room personas, web/weather/stock/MapleStory tools, and a MapleStory guild-boss scheduling subsystem. A KakaoTalk bridge (Android script) delivers replies and polls an outbox for scheduled reminders.

## Tech Stack

- **Python 3.10+** with venv at `.venv/`
- **FastAPI** + **Uvicorn** (HTTP server)
- **LangChain** + **langchain-openai** + **LangGraph** (agent graph)
- **Chroma** (embedded, `./chroma_db`) — semantic memory
- **SQLite** (`./data/`) — boss subsystem, room persona, FTS5 keyword recall
- **PostgreSQL** (optional) — `SQLChatMessageHistory` for the webhook path
- **Pydantic** (request validation)

## Commands

```bash
source .venv/bin/activate
pip install -r requirements.txt

# Webhook server (port 8000)
python main.py            # or: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# LangGraph dev UI (Agent Chat UI); graph id must be "agent"
.venv/bin/langgraph dev --port 2024 --no-browser

# Offline unit tests (no network)
env ANONYMIZED_TELEMETRY=false CHROMA_TELEMETRY=false \
  .venv/bin/python -m unittest discover -s tests -p 'test_*.py'

# Golden-set agent eval (needs OPENAI_API_KEY + network)
.venv/bin/python -m tests.eval.run_eval [--filter <id>] [--variant default|formal|playful]

# Postgres (optional, webhook chat history)
docker compose up -d db
```

## Architecture

Modular `app/` package. Entry: `main.py` → `app.create_app()`.

Request flow (`app/services/chat_service.py:handle_chat`):
1. Boss commands (`!보스…`, `!정산…`) are parsed and short-circuit before the LLM.
2. Room allowlist gate (`settings.allowed_rooms`); non-allowed rooms get an empty reply.
3. Every inbound user message is indexed for keyword recall (`app/chat_log.py`, FTS5).
4. Non-command messages are buffered; at `buffer_size` they're LLM-summarized into Chroma.
5. Command messages (`is_command=True`) run the **LangGraph pipeline** (`app/graph.py`):
   `START → retrieve → chat ⇄ tools → store → END`.

LangGraph nodes:
- **retrieve** — loads memory for the room/sender: semantic search over `context_summary` docs (Chroma), keyword recall over raw turns (SQLite FTS5), all user facts by key (Chroma metadata `.get`), and the room persona.
- **chat** — gpt-4o with the system prompt (`app/prompts.py`, A/B tone variants) + injected memory, bound to tools.
- **tools** — `naver_search`, `web_search` (Tavily), `get_weather` (OpenWeather), `get_stock_quote` (KIS→Yahoo→Naver→Tavily fallback), `lookup_maplestory_character` (Nexon).
- **store** — extracts persistent per-user facts (gpt-4o-mini) into the `user_profile` Chroma collection. Raw turns are **not** embedded.

The same graph backs both the webhook and the `langgraph dev` UI.

### Memory model

- **Recency** — `SQLChatMessageHistory` (last `max_history_messages`, Postgres or SQLite); older history is transiently summarized.
- **Long-term semantic** — `context_summary` docs in the Chroma `chat_history` collection, produced by buffer flushes; embeddings catch paraphrase.
- **Keyword/verbatim** — SQLite FTS5 (`chat_log_fts`, trigram tokenizer) over raw inbound messages (`app/chat_log.py`); exact-substring recall, no embeddings. Complements the semantic path.
- **Per-user facts** — `user_profile` Chroma collection, loaded **all by key** (room+sender), not ranked — so constraints (allergies, etc.) are never dropped from the top-k.
- **Room persona** — `room_persona` SQLite table, refreshed weekly (`app/persona.py`).

Raw conversation turns are intentionally **not embedded** (avoids embedding cost, low-precision fragment retrieval, and stale-data recall such as weeks-old stock prices); FTS handles their keyword recall instead.

### Boss subsystem (`app/boss/`)

MapleStory guild-boss MVP: weekly boss registration, per-week schedules, Thursday-reset and 30-min pre-boss reminders (KST scheduler), drop registration (억/만 mesos parser), settlement creation/completion, and a public read-only settlement page (`/s/{token}`). Reminders are written to a `bot_outbox` table that the Kakao bridge polls (`/bot/outbox`) and acks — the backend never pushes to KakaoTalk directly. Schema is created by `init_schema()` in `app/boss/db.py`; the SQL files in `migrations/` are documentation only and may lag the real schema.

## Configuration

All config is env-based via `app/config.py` (`.env`; see `.env.example`). Notable keys: `OPENAI_API_KEY`, `DB_CONNECTION_STRING`, `ALLOWED_ROOMS`, `NAVER_CLIENT_ID/SECRET`, `TAVILY_API_KEY`, `OPENWEATHER_API_KEY`, `NEXON_API_KEY`, `KIS_APP_KEY/SECRET`, plus boss / playground / prompt-variant tuning.

## Notes / gotchas

- Chroma is **embedded** (`./chroma_db`); the `chromadb` service in `docker-compose.yml` is unused — the app never connects to it.
- `docker-compose.yml` Postgres credentials are hardcoded dev defaults — do not reuse in production.
- Chroma calls on async paths must be wrapped in `asyncio.to_thread` (see `app/graph.py` / `app/chat_log.py`).
- FTS5 trigram needs SQLite ≥ 3.34; `init_chat_log_schema()` degrades to a no-op if unavailable.
- `POST /rooms`, `/debug`, and `/bot/outbox` are unauthenticated — fine behind a trusted bridge, not for public exposure.
