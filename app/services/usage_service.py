"""LLM/image usage tracking + lightweight rate limiting (SQLite-backed).

Records every model call's token usage and estimated cost into `llm_usage`,
and enforces per-scope quotas via `usage_counter`. Pricing is approximate
(USD per 1M tokens, or per image) — adjust PRICING as OpenAI changes rates.
"""

from __future__ import annotations

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.dependencies import logger

# Approximate 2026 USD rates. (input_per_1m, output_per_1m) for token models;
# image models use a flat per-image cost in IMAGE_PRICING.
PRICING = {
    "gpt-4o": (2.50, 10.00),
    "gpt-4o-mini": (0.15, 0.60),
}
IMAGE_PRICING = {
    "gpt-image-1": 0.04,
    "gpt-image-1-mini": 0.005,
    "pollinations": 0.0,  # free provider
    "gemini-2.5-flash-image": 0.039,  # ~0.039/image; free tier available
}
_DEFAULT_TOKEN_RATE = (2.50, 10.00)
_DEFAULT_IMAGE_COST = 0.01


def _token_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    rate_in, rate_out = PRICING.get(model, _DEFAULT_TOKEN_RATE)
    return (prompt_tokens * rate_in + completion_tokens * rate_out) / 1_000_000


def record_usage(
    kind: str,
    model: str,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
    room_id: int | None = None,
    sender: str | None = None,
) -> None:
    try:
        cost = _token_cost(model, prompt_tokens, completion_tokens)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO llm_usage (room_id, sender, kind, model, prompt_tokens, "
                "completion_tokens, est_cost_usd, created_at) VALUES (?,?,?,?,?,?,?,?)",
                (room_id, sender, kind, model, prompt_tokens, completion_tokens, cost,
                 now_kst().isoformat()),
            )
    except Exception as e:
        logger.warning("record_usage failed: %s", e)


def record_image_usage(model: str, room_id: int | None = None, sender: str | None = None) -> None:
    try:
        cost = IMAGE_PRICING.get(model, _DEFAULT_IMAGE_COST)
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO llm_usage (room_id, sender, kind, model, est_cost_usd, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (room_id, sender, "image_gen", model, cost, now_kst().isoformat()),
            )
    except Exception as e:
        logger.warning("record_image_usage failed: %s", e)


def record_message_usage(response, kind: str, model: str, room_id=None, sender=None) -> None:
    """Extract token usage from a LangChain AIMessage and record it."""
    meta = getattr(response, "usage_metadata", None) or {}
    record_usage(
        kind, model,
        int(meta.get("input_tokens", 0) or 0),
        int(meta.get("output_tokens", 0) or 0),
        room_id, sender,
    )


def spend_since_usd(days: int) -> float:
    """Our own tracked spend over the last `days` (no admin key needed)."""
    try:
        cutoff = (now_kst() - _days(days)).isoformat()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(est_cost_usd),0) s FROM llm_usage WHERE created_at >= ?",
                (cutoff,),
            ).fetchone()
        return float(row["s"] if row else 0.0)
    except Exception as e:
        logger.warning("spend_since failed: %s", e)
        return 0.0


def spend_breakdown(days: int) -> list[tuple[str, float]]:
    try:
        cutoff = (now_kst() - _days(days)).isoformat()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT kind, COALESCE(SUM(est_cost_usd),0) s FROM llm_usage "
                "WHERE created_at >= ? GROUP BY kind ORDER BY s DESC",
                (cutoff,),
            ).fetchall()
        return [(r["kind"], float(r["s"])) for r in rows]
    except Exception:
        return []


def _days(n: int):
    from datetime import timedelta

    return timedelta(days=n)


def allow(scope_key: str, window_key: str, limit: int) -> bool:
    """Increment the counter for (scope, window); return False if over `limit`.
    limit <= 0 disables the check."""
    if limit <= 0:
        return True
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT count FROM usage_counter WHERE scope_key=? AND window_key=?",
                (scope_key, window_key),
            ).fetchone()
            current = int(row["count"]) if row else 0
            if current >= limit:
                return False
            conn.execute(
                "INSERT INTO usage_counter (scope_key, window_key, count) VALUES (?,?,1) "
                "ON CONFLICT(scope_key, window_key) DO UPDATE SET count = count + 1",
                (scope_key, window_key),
            )
        return True
    except Exception as e:
        logger.warning("rate limit check failed (allowing): %s", e)
        return True


def allow_image_gen(sender: str, limit: int) -> bool:
    day = now_kst().strftime("%Y-%m-%d")
    return allow(f"image_gen:{sender}", day, limit)


def allow_chat(room_id: int, limit: int) -> bool:
    minute = now_kst().strftime("%Y-%m-%dT%H:%M")
    return allow(f"chat:{room_id}", minute, limit)
