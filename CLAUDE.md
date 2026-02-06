# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

KakaoTalk AI chatbot backend. A FastAPI server that receives KakaoTalk messages via POST `/chat` and responds using OpenAI GPT-4o-mini through LangChain. PostgreSQL database is provisioned via Docker Compose but not yet integrated into the application.

## Tech Stack

- **Python 3.10** with venv at `.venv/`
- **FastAPI** + **Uvicorn** (HTTP server)
- **LangChain** + **langchain-openai** (LLM integration)
- **Pydantic** (request validation)
- **PostgreSQL 15** via Docker Compose (container: `postgres_db`, db: `chat_db`)

## Commands

```bash
# Activate virtualenv
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run the server (port 8000)
python main.py
# or: uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# Start PostgreSQL
docker compose up -d

# Stop PostgreSQL
docker compose down
```

## Architecture

Single-file application (`main.py`). One endpoint:

- `POST /chat` — Accepts `{"room": str, "msg": str, "sender": str}`, forwards `msg` to GPT-4o-mini via LangChain, returns `{"answer": str}`

PostgreSQL is configured in `docker-compose.yml` but has no application-level integration yet (no ORM, no models, no migrations).

## Critical Issues

- **API key is hardcoded in `main.py`** — must be moved to environment variables (e.g., `OPENAI_API_KEY`, which langchain-openai reads automatically)
- **Database credentials are hardcoded in `docker-compose.yml`** — should use `.env` file
- No `.gitignore` exists — `postgres_data/`, `.venv/`, `.env` should be excluded
