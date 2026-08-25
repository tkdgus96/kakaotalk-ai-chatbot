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
    try:
        rows = _search_chat_log_rows(room_id, query, limit)
        if rows:
            return [_format_search_row(r["created_at"], r["sender"], r["content"]) for r in rows]
        return _search_chat_log_like(room_id, query, limit)
    except Exception:
        return _search_chat_log_like(room_id, query, limit)


def search_chat_log_with_windows(
    room_id: int,
    query: str,
    limit: int = 5,
    before: int = 4,
    after: int = 4,
) -> list[str]:
    """Return keyword hits with nearby messages for context-sensitive recall."""
    if not _ensure_fts_available():
        return []
    hits = _search_chat_log_rows(room_id, query, limit)
    windows: list[str] = []
    seen: set[int] = set()
    for hit in hits:
        rowid = int(hit["rowid"])
        if rowid in seen:
            continue
        seen.add(rowid)
        rows = get_chat_log_around(room_id, rowid, before=before, after=after)
        if not rows:
            continue
        windows.append(
            "[검색 hit 주변 대화]\n"
            + "\n".join(rows)
        )
    return windows


def search_chat_log_evidence(
    room_id: int,
    query: str,
    limit: int = 20,
    include_bots: bool = False,
    include_commands: bool = False,
) -> list[str]:
    """Return user-authored evidence lines for room-log questions.

    This is intentionally broader than FTS rank-only search: room questions
    often use wording that differs from the actual game/app-generated phrase
    in the log, so we combine FTS hits with LIKE hits and then filter commands
    and previous bot answers out of the evidence set.
    """
    if not _ensure_fts_available():
        return []

    rows = []
    rows.extend(_search_chat_log_rows(room_id, query, limit * 2))
    rows.extend(_search_chat_log_like_rows(room_id, query, limit * 3, include_term_prefixes=True))

    deduped = []
    seen: set[int] = set()
    for row in rows:
        rowid = int(row["rowid"])
        if rowid in seen:
            continue
        seen.add(rowid)
        sender = str(row["sender"] or "")
        content = str(row["content"] or "").strip()
        if not include_bots and sender == "온반봇":
            continue
        if not include_commands and content.startswith("!"):
            continue
        if not content:
            continue
        deduped.append(row)
    deduped.sort(key=lambda row: str(row["created_at"] or ""), reverse=True)
    deduped = deduped[:limit]
    return [_format_search_row(r["created_at"], r["sender"], r["content"]) for r in deduped]


def _search_chat_log_rows(room_id: int, query: str, limit: int) -> list:
    match = _build_match(query)
    prefers_recent = _prefers_recent_results(query)
    rows = []
    if not match:
        return _search_chat_log_like_rows(room_id, query, limit, include_term_prefixes=prefers_recent)
    order_by = "created_at DESC" if prefers_recent else "rank"
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT rowid, content, sender, created_at FROM chat_log_fts "
                "WHERE chat_log_fts MATCH ? AND room_id = ? "
                f"ORDER BY {order_by} LIMIT ?",
                (match, str(room_id), limit),
            ).fetchall()
    except Exception:
        rows = []

    if prefers_recent:
        rows = list(rows) + _search_chat_log_like_rows(
            room_id,
            query,
            limit * 2,
            include_term_prefixes=True,
        )
        deduped = []
        seen: set[int] = set()
        for row in rows:
            rowid = int(row["rowid"])
            if rowid in seen:
                continue
            seen.add(rowid)
            deduped.append(row)
        deduped.sort(key=lambda row: str(row["created_at"] or ""), reverse=True)
        return deduped[:limit]

    if rows:
        return rows
    return _search_chat_log_like_rows(room_id, query, limit)


def _search_chat_log_like_rows(
    room_id: int,
    query: str,
    limit: int,
    include_term_prefixes: bool = False,
) -> list:
    terms = _like_terms(query, include_term_prefixes=include_term_prefixes)
    if not terms:
        return []
    clauses = " OR ".join("content LIKE ?" for _ in terms[:8])
    params = [f"%{term}%" for term in terms[:8]]
    try:
        with get_conn() as conn:
            return conn.execute(
                "SELECT rowid, content, sender, created_at FROM chat_log_fts "
                f"WHERE room_id = ? AND ({clauses}) "
                "ORDER BY created_at DESC LIMIT ?",
                (str(room_id), *params, limit),
            ).fetchall()
    except Exception:
        return []


def _like_terms(query: str, include_term_prefixes: bool = False) -> list[str]:
    stopwords = {
        "이",
        "방",
        "이방",
        "에서",
        "대해",
        "대화",
        "채팅",
        "채팅방",
        "기록",
        "정리",
        "정리해줘",
        "알려줘",
        "요약",
        "요약해줘",
        "누가",
        "언제",
        "인별",
        "사람별",
        "놀이",
    }
    terms: list[str] = []
    seen: set[str] = set()
    for tok in re.findall(r"[0-9A-Za-z가-힣]+", query):
        candidates = [tok]
        if include_term_prefixes and re.fullmatch(r"[가-힣]{4,}", tok):
            candidates.append(tok[:2])
        for candidate in candidates:
            if len(candidate) < 2 or candidate in seen or candidate in stopwords:
                continue
            seen.add(candidate)
            terms.append(candidate)
    return terms



def get_chat_log_around(room_id: int, rowid: int, before: int = 4, after: int = 4) -> list[str]:
    if not _ensure_fts_available():
        return []
    try:
        with get_conn() as conn:
            target = conn.execute(
                "SELECT created_at FROM chat_log_fts WHERE rowid=? AND room_id=?",
                (rowid, str(room_id)),
            ).fetchone()
            if not target:
                return []
            older = conn.execute(
                "SELECT content, sender, created_at FROM chat_log_fts "
                "WHERE room_id=? AND created_at < ? "
                "ORDER BY created_at DESC LIMIT ?",
                (str(room_id), target["created_at"], before),
            ).fetchall()
            center = conn.execute(
                "SELECT content, sender, created_at FROM chat_log_fts WHERE rowid=?",
                (rowid,),
            ).fetchall()
            newer = conn.execute(
                "SELECT content, sender, created_at FROM chat_log_fts "
                "WHERE room_id=? AND created_at > ? "
                "ORDER BY created_at ASC LIMIT ?",
                (str(room_id), target["created_at"], after),
            ).fetchall()
        rows = list(reversed(older)) + list(center) + list(newer)
        return [_format_search_row(r["created_at"], r["sender"], r["content"]) for r in rows]
    except Exception:
        return []


def _prefers_recent_results(query: str) -> bool:
    return any(word in query for word in ("최근", "오늘", "어제", "언제", "시간", "날짜", "누가"))


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


def list_room_senders(room_id: int, limit: int = 50) -> list[str]:
    """Return the room's known member names, most active first. Used to detect
    when a query mentions another member (so their facts can be loaded too)."""
    if not _ensure_fts_available():
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sender, COUNT(*) AS cnt FROM chat_log_fts "
                "WHERE room_id=? GROUP BY sender ORDER BY cnt DESC LIMIT ?",
                (str(room_id), limit),
            ).fetchall()
        return [str(r["sender"]) for r in rows if r["sender"]]
    except Exception:
        return []


def get_recent_chat_log(room_id: int, limit: int = 300) -> list[str]:
    """Return the most recent raw messages (chronological order), unformatted
    sender: content lines. Used for room topic extraction."""
    if not _ensure_fts_available():
        return []
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT content, sender FROM chat_log_fts "
                "WHERE room_id=? ORDER BY created_at DESC LIMIT ?",
                (str(room_id), limit),
            ).fetchall()
        return [f"[{r['sender']}]: {r['content']}" for r in reversed(rows)]
    except Exception:
        return []


def get_cached_chat_summary(room_id: int, date_label: str, query_key: str) -> str | None:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT summary_text FROM chat_summary_cache WHERE room_id=? AND date_label=? AND query_key=?",
                (room_id, date_label, query_key),
            ).fetchone()
        return row["summary_text"] if row else None
    except Exception:
        return None


def set_cached_chat_summary(
    room_id: int,
    date_label: str,
    query_key: str,
    summary_text: str,
    source_count: int,
    now_iso: str,
) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_summary_cache
                (room_id, date_label, query_key, summary_text, source_count, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(room_id, date_label, query_key) DO UPDATE SET
                    summary_text=excluded.summary_text,
                    source_count=excluded.source_count,
                    updated_at=excluded.updated_at
                """,
                (room_id, date_label, query_key, summary_text, source_count, now_iso, now_iso),
            )
    except Exception:
        pass


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
