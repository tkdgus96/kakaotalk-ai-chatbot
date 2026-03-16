# KakaoTalk AI Bot

FastAPI-based KakaoTalk chatbot backend with:
- OpenAI (LangChain) chat + tool calling
- Conversation memory in PostgreSQL (`SQLChatMessageHistory`)
- Long-term semantic memory in ChromaDB
- Built-in tools for web search, MapleStory lookup, and stock quotes

## Features

- `POST /chat` endpoint for KakaoTalk messages
- Room allowlist control (`/rooms` endpoints)
- Message buffering + periodic context summarization
- RAG retrieval from Chroma before each command response
- Tool-calling flow (LLM can call external tools during response generation)

## Project Structure

```text
app/
  api.py                # FastAPI routes
  config.py             # env-based settings
  dependencies.py       # shared singletons (llm, vectorstore, logger, buffers)
  models.py             # request models
  prompts.py            # system prompts
  services/chat_service.py
  tools/
    maplestory.py
    search.py
    stock.py
main.py                 # app entrypoint (uvicorn main:app)
```

## Requirements

- Python 3.10+
- PostgreSQL (for SQL chat history)
- OpenAI API key
- (Optional) Exa API key for better web search
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

### Room management

- `GET /rooms`
- `POST /rooms/{room_id}`
- `DELETE /rooms/{room_id}`

### Debug

- `POST /debug` echoes raw request JSON.

## Tests

```bash
env ANONYMIZED_TELEMETRY=false CHROMA_TELEMETRY=false \
.venv/bin/python -m unittest discover -s tests -p 'test_*.py' -v
```

## Notes

- `DB_CONNECTION_STRING` must point to a reachable PostgreSQL instance.
- Chroma persistent data is stored in `./chroma_db`.
- Stock tool uses multiple sources/fallbacks; for best KRX reliability, set KIS credentials.
