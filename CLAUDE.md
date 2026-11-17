# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KakaoTalk AI chatbot backend. A FastAPI server that receives KakaoTalk group-chat messages via `POST /chat` and replies using OpenAI **gpt-4o** by default (`CHAT_MODEL`, built via `app/llm_factory.py`) through a LangGraph agent pipeline (LangChain tool-calling). It has long-term memory, per-user facts, evolving room personas, web/weather/stock/MapleStory tools, and a MapleStory guild-boss scheduling subsystem. A KakaoTalk bridge (Android script) delivers replies and polls an outbox for scheduled reminders.

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
- **chat** — the chat model (`CHAT_MODEL`, default gpt-4o; optional `LLM_FALLBACK_MODEL`) with the system prompt (`app/prompts.py`, A/B tone variants) + injected memory + the replied-to message (`quoted_context`, KakaoTalk 답장), bound to tools.
- **tools** — 34 tools registered in `app/tools/__init__.py`: search (`naver_search`, `web_search` grounded via OpenAI Responses → Tavily), weather, stocks/finance, MapleStory, chat-history recall, URL reading/research, memory, one-shot + recurring reminders, image generate/analyze, calculator/wikipedia/currency/units/date.
- **store** — extracts persistent per-user facts (`FACT_MODEL`, default gpt-4o-mini) into the `user_profile` Chroma collection. Raw turns are **not** embedded.

Per-turn telemetry (`app/services/ops_service.py`): turn latency and every tool call (latency, hard/soft failure) go to the SQLite `ops_event` table via a LangChain callback attached in `handle_chat`. `app/services/report_service.py` aggregates it with `llm_usage`/`feedback_log`/`audit_log` into the ops report (`!리포트`, scheduled daily/weekly from the health monitor; email + KakaoTalk self-chat).

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

All config is env-based via `app/config.py` (`.env`; see `.env.example`). Notable keys: `OPENAI_API_KEY`, `DB_CONNECTION_STRING`, `ALLOWED_ROOMS`, `NAVER_CLIENT_ID/SECRET`, `TAVILY_API_KEY`, `OPENWEATHER_API_KEY`, `NEXON_API_KEY`, `KIS_APP_KEY/SECRET`, `CHAT_MODEL`/`LIGHT_MODEL`/`FACT_MODEL`/`LLM_FALLBACK_MODEL` (model specs, see `app/llm_factory.py`), `REPORT_CHANNELS`/`REPORT_CHAT_ID`/`ADMIN_SENDERS` (ops report), plus boss / playground / prompt-variant tuning.

## Notes / gotchas

- GitHub account discipline: commits and pushes for this repository must use the personal GitHub account `tkdgus96`. Do not use the company account `tkdgus9610`. Before committing or pushing, verify `git config user.name`, `git config user.email`, and `git remote -v` point to `tkdgus96`.
- Chroma is **embedded** (`./chroma_db`); the `chromadb` service in `docker-compose.yml` is unused — the app never connects to it.
- `docker-compose.yml` Postgres credentials are hardcoded dev defaults — do not reuse in production.
- Chroma calls on async paths must be wrapped in `asyncio.to_thread` (see `app/graph.py` / `app/chat_log.py`).
- FTS5 trigram needs SQLite ≥ 3.34; `init_chat_log_schema()` degrades to a no-op if unavailable.
- `POST /rooms`, `/debug`, `/iris`, and `/bot/outbox` accept a shared token only when `BOT_SHARED_TOKEN` is set; `/chat` is always unauthenticated — fine behind a trusted bridge, not for public exposure.
- Iris echo filter: the bot's own messages are dropped, except trigger-prefixed commands typed in the bot's self-chat (`HEALTH_SELF_CHAT_ID`/`REPORT_CHAT_ID`), so `!리포트` works there.
- Iris payload parsing (`app/services/iris_service.py`): `classify_message` maps KakaoTalk `type` to a kind (emoticon/feed dropped; file/video/audio/location/etc. become placeholder text), `_extract_quote` (답장), `_extract_mentions` (@멘션 user_ids), `_parse_sent_at` (KakaoTalk `created_at`). `KakaoMsg` carries `sender_id`, `sent_at`, `mentioned_names`, `msg_kind`, `quoted_*`.
- Member identity: all stores are keyed by display name; `app/services/member_service.py` tracks `user_id -> name` per room and rewrites name keys on rename (`migrate_sender_name`). Use `name_for`/`names_for` to turn KakaoTalk user_ids into names.
