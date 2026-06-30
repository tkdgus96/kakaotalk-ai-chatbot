"""Keyword recall over raw chat turns via SQLite FTS5 (trigram tokenizer).

This is the verbatim/keyword complement to the semantic summary memory in
Chroma: summaries catch paraphrase, FTS catches exact words ("who posted that
link"). Raw turns are no longer embedded — they are indexed here instead, which
costs no embeddings/LLM calls.

The trigram tokenizer matches arbitrary substrings, so it works for Korean
without word segmentation (requires SQLite >= 3.34). If FTS5/trigram is
unavailable, every function degrades to a no-op.
"""

from __future__ import annotations

import re

from app.boss.db import get_conn

_FTS_AVAILABLE = False


def _ensure_fts_available() -> bool:
    if not _FTS_AVAILABLE:
        init_chat_log_schema()
    return _FTS_AVAILABLE


def init_chat_log_schema() -> None:
    """Create the FTS5 chat-log table. Sets a module flag so add/search become
    no-ops when FTS5 or the trigram tokenizer isn't supported."""
    global _FTS_AVAILABLE
    try:
        with get_conn() as conn:
            conn.execute(
                "CREATE VIRTUAL TABLE IF NOT EXISTS chat_log_fts USING fts5("
                "content, room_id UNINDEXED, sender UNINDEXED, created_at UNINDEXED, "
                "tokenize='trigram')"
            )
        _FTS_AVAILABLE = True
    except Exception:
        _FTS_AVAILABLE = False


def add_chat_log(room_id: int, sender: str, content: str, now_iso: str) -> None:
    """Index a single inbound user message for later keyword recall."""
    if not _ensure_fts_available():
        return
    content = (content or "").strip()
    if not content:
        return
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO chat_log_fts (content, room_id, sender, created_at) "
                "VALUES (?, ?, ?, ?)",
                (content, str(room_id), sender, now_iso),
            )
    except Exception:
        pass


def _build_match(query: str) -> str | None:
    """Turn a free-form query into a trigram MATCH expression.

    The `[0-9A-Za-z가-힣]+` extraction strips quotes/punctuation, so the quoted
    terms can't break FTS5 MATCH syntax. Terms shorter than 2 chars are dropped
    (trigram needs >= 3 chars to actually match anything)."""
    seen: set[str] = set()
    terms: list[str] = []
    for tok in re.findall(r"[0-9A-Za-z가-힣]+", query):
        if len(tok) < 2 or tok in seen:
            continue
        seen.add(tok)
        terms.append(f'"{tok}"')
        if len(terms) >= 8:
            break
    return " OR ".join(terms) if terms else None


def search_chat_log(room_id: int, query: str, limit: int = 5) -> list[str]:
    """Return up to `limit` past messages in the room matching the query's
    keywords, ranked by FTS relevance. Formatted with timestamp and sender."""
    if not _ensure_fts_available():
        return []
    match = _build_match(query)
    if not match:
        return []
    order_by = "created_at DESC" if _prefers_recent_results(query) else "rank"
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT content, sender, created_at FROM chat_log_fts "
                "WHERE chat_log_fts MATCH ? AND room_id = ? "
                f"ORDER BY {order_by} LIMIT ?",
                (match, str(room_id), limit),
            ).fetchall()
        if rows:
            return [_format_search_row(r["created_at"], r["sender"], r["content"]) for r in rows]
        return _search_chat_log_like(room_id, query, limit)
    except Exception:
        return _search_chat_log_like(room_id, query, limit)


def _prefers_recent_results(query: str) -> bool:
    return any(word in query for word in ("최근", "언제", "시간", "날짜", "누가"))


def _search_chat_log_like(room_id: int, query: str, limit: int) -> list[str]:
    terms = [tok for tok in re.findall(r"[0-9A-Za-z가-힣]+", query) if len(tok) >= 2]
    if not terms:
        return []
    clauses = " OR ".join("content LIKE ?" for _ in terms[:5])
    params = [f"%{term}%" for term in terms[:5]]
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT content, sender, created_at FROM chat_log_fts "
                f"WHERE room_id = ? AND ({clauses}) "
                "ORDER BY created_at DESC LIMIT ?",
                (str(room_id), *params, limit),
            ).fetchall()
        return [_format_search_row(r["created_at"], r["sender"], r["content"]) for r in rows]
    except Exception:
        return []


def get_chat_log_between(
    room_id: int,
    start_iso: str,
    end_iso: str,
    limit: int = 500,
) -> list[str]:
    """Return room messages in a deterministic created_at range."""
    if not _ensure_fts_available():
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT content, sender, created_at FROM chat_log_fts "
                "WHERE room_id = ? AND created_at >= ? AND created_at < ? "
                "ORDER BY created_at ASC LIMIT ?",
                (str(room_id), start_iso, end_iso, limit),
            ).fetchall()
        return [_format_row(r["created_at"], r["sender"], r["content"]) for r in rows]
    except Exception:
        return []


def _format_row(created_at: str | None, sender: str, content: str) -> str:
    if created_at:
        match = re.match(r"\d{4}-\d{2}-\d{2}T(\d{2}:\d{2})", created_at)
        if match:
            return f"{match.group(1)} [{sender}] {content}"
    return f"[{sender}] {content}"


def _format_search_row(created_at: str | None, sender: str, content: str) -> str:
    if created_at:
        match = re.match(r"(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})", created_at)
        if match:
            return f"{match.group(1)} {match.group(2)} [{sender}] {content}"
    return f"[{sender}] {content}"
