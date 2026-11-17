"""Operational telemetry: per-turn latency, per-tool latency/failures, throttles.

Everything lands in the SQLite `ops_event` table (see `app/boss/db.py`) so the
ops report (`app/services/report_service.py`) can aggregate it without an
external tracing stack. Recording never raises — a telemetry failure must not
break a chat turn.
"""

from __future__ import annotations

import asyncio
import time
from datetime import timedelta
from typing import Any
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.dependencies import logger

# Tools swallow most upstream errors and answer with a Korean failure string.
# These markers (checked against the head of the output) flag such soft
# failures so the report can show them next to hard exceptions.
_SOFT_FAIL_MARKERS = ("실패", "오류", "에러", "error", "Error", "못 찾", "찾을 수 없", "불가", "응답이 없")
_SOFT_FAIL_HEAD_CHARS = 60


def record_event(
    kind: str,
    name: str,
    room_id: int | None = None,
    sender: str | None = None,
    ok: bool = True,
    latency_ms: int | None = None,
    detail: str | None = None,
    inbound_ms: int | None = None,
) -> None:
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO ops_event (kind, name, room_id, sender, ok, latency_ms, inbound_ms, detail, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?)",
                (kind, name, room_id, sender, 1 if ok else 0, latency_ms, inbound_ms,
                 (detail or None) and str(detail)[:300], now_kst().isoformat()),
            )
    except Exception as e:
        logger.warning("ops record_event failed: %s", e)


def is_soft_failure(output: Any) -> bool:
    text = output if isinstance(output, str) else getattr(output, "content", None)
    if not isinstance(text, str):
        return False
    head = text.strip()[:_SOFT_FAIL_HEAD_CHARS]
    return any(m in head for m in _SOFT_FAIL_MARKERS)


class ToolUsageCallback(AsyncCallbackHandler):
    """Times every tool invocation in a graph run and records it as an
    `ops_event(kind='tool')`. Attach via `config={"callbacks": [cb]}`."""

    def __init__(self, room_id: int | None, sender: str | None):
        super().__init__()
        self.room_id = room_id
        self.sender = sender
        self._started: dict[UUID, tuple[str, float]] = {}
        self.calls: list[tuple[str, bool, int]] = []  # (tool, ok, latency_ms)

    async def on_tool_start(self, serialized: dict, input_str: str, *, run_id: UUID, **kwargs) -> None:
        name = str((serialized or {}).get("name") or kwargs.get("name") or "tool")
        self._started[run_id] = (name, time.monotonic())

    async def on_tool_end(self, output: Any, *, run_id: UUID, **kwargs) -> None:
        name, t0 = self._started.pop(run_id, ("tool", time.monotonic()))
        latency = int((time.monotonic() - t0) * 1000)
        soft = is_soft_failure(output)
        self.calls.append((name, not soft, latency))
        await asyncio.to_thread(
            record_event, "tool", name, self.room_id, self.sender, True, latency,
            "soft_fail" if soft else None,
        )

    async def on_tool_error(self, error: BaseException, *, run_id: UUID, **kwargs) -> None:
        name, t0 = self._started.pop(run_id, ("tool", time.monotonic()))
        latency = int((time.monotonic() - t0) * 1000)
        self.calls.append((name, False, latency))
        await asyncio.to_thread(
            record_event, "tool", name, self.room_id, self.sender, False, latency,
            f"{type(error).__name__}: {error}",
        )


def _cutoff(days: float) -> str:
    return (now_kst() - timedelta(days=days)).isoformat()


def _percentile(values: list[int], pct: float) -> int:
    if not values:
        return 0
    values = sorted(values)
    idx = min(len(values) - 1, max(0, int(round((len(values) - 1) * pct))))
    return values[idx]


def turn_stats(days: float) -> dict:
    """Command turns in the window: count, per-room counts, latency p50/p95, failures."""
    out = {"count": 0, "failed": 0, "p50_ms": 0, "p95_ms": 0, "rooms": [], "tool_calls": 0,
           "inbound_p50_ms": 0, "inbound_p95_ms": 0}
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT room_id, ok, latency_ms, inbound_ms, detail FROM ops_event "
                "WHERE kind='turn' AND created_at >= ?",
                (_cutoff(days),),
            ).fetchall()
            names = {
                int(r["room_id"]): str(r["room_name"])
                for r in conn.execute("SELECT room_id, room_name FROM chat_room").fetchall()
            }
    except Exception as e:
        logger.warning("turn_stats failed: %s", e)
        return out
    lat: list[int] = []
    inbound: list[int] = []
    per_room: dict[int, int] = {}
    for r in rows:
        out["count"] += 1
        if not r["ok"]:
            out["failed"] += 1
        if r["latency_ms"] is not None:
            lat.append(int(r["latency_ms"]))
        if r["inbound_ms"] is not None and int(r["inbound_ms"]) >= 0:
            inbound.append(int(r["inbound_ms"]))
        if r["room_id"] is not None:
            per_room[int(r["room_id"])] = per_room.get(int(r["room_id"]), 0) + 1
        try:
            out["tool_calls"] += int(str(r["detail"] or "0").split("tools=")[-1].split()[0])
        except Exception:
            pass
    out["p50_ms"] = _percentile(lat, 0.5)
    out["p95_ms"] = _percentile(lat, 0.95)
    out["inbound_p50_ms"] = _percentile(inbound, 0.5)
    out["inbound_p95_ms"] = _percentile(inbound, 0.95)
    out["rooms"] = sorted(
        ((names.get(rid, str(rid)), n) for rid, n in per_room.items()), key=lambda x: -x[1]
    )
    return out


def tool_stats(days: float) -> list[dict]:
    """Per-tool: calls, hard failures, soft failures, avg latency (ms)."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT name, COUNT(*) c, SUM(CASE WHEN ok=0 THEN 1 ELSE 0 END) hard, "
                "SUM(CASE WHEN ok=1 AND detail='soft_fail' THEN 1 ELSE 0 END) soft, "
                "AVG(latency_ms) avg_ms FROM ops_event "
                "WHERE kind='tool' AND created_at >= ? GROUP BY name ORDER BY c DESC",
                (_cutoff(days),),
            ).fetchall()
        return [
            {
                "name": str(r["name"]),
                "calls": int(r["c"]),
                "hard_fail": int(r["hard"] or 0),
                "soft_fail": int(r["soft"] or 0),
                "avg_ms": int(r["avg_ms"] or 0),
            }
            for r in rows
        ]
    except Exception as e:
        logger.warning("tool_stats failed: %s", e)
        return []


def event_count(kind: str, days: float, name: str | None = None) -> int:
    try:
        with get_conn() as conn:
            if name:
                row = conn.execute(
                    "SELECT COUNT(*) c FROM ops_event WHERE kind=? AND name=? AND created_at >= ?",
                    (kind, name, _cutoff(days)),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) c FROM ops_event WHERE kind=? AND created_at >= ?",
                    (kind, _cutoff(days)),
                ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0
