"""Ops report: requests, cost, tools, images, corrections, safety — delivered to
the operator by email (SMTP alert channel) and/or KakaoTalk (bot_outbox to
REPORT_CHAT_ID), on a daily/weekly schedule or on demand via `!리포트`.

Data sources are all local SQLite tables: `llm_usage` (tokens/cost),
`ops_event` (turn/tool latency, throttles), `feedback_log` (user corrections),
`audit_log` (injection blocks), `bot_outbox` (reminder deliveries).
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import logger
from app.services import ops_service
from app.services.alert_service import fetch_openai_spend_usd, send_alert
from app.services.feedback_service import recent_feedback
from app.services.usage_service import spend_breakdown, spend_since_usd

_KAKAO_MAX_CHARS = 1800


def _fmt_ms(ms: int) -> str:
    return f"{ms / 1000:.1f}s" if ms >= 1000 else f"{ms}ms"


def _room_cost(days: int) -> dict[str, float]:
    try:
        cutoff = (now_kst() - timedelta(days=days)).isoformat()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT u.room_id, COALESCE(r.room_name, CAST(u.room_id AS TEXT)) name, "
                "COALESCE(SUM(u.est_cost_usd),0) s FROM llm_usage u "
                "LEFT JOIN chat_room r ON r.room_id = u.room_id "
                "WHERE u.created_at >= ? AND u.room_id IS NOT NULL GROUP BY u.room_id",
                (cutoff,),
            ).fetchall()
        return {str(r["name"]): float(r["s"]) for r in rows}
    except Exception:
        return {}


def _image_stats(days: int) -> tuple[int, list[tuple[str, int]]]:
    try:
        cutoff = (now_kst() - timedelta(days=days)).isoformat()
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT COALESCE(sender,'?') sender, COUNT(*) c FROM llm_usage "
                "WHERE kind='image_gen' AND created_at >= ? GROUP BY sender ORDER BY c DESC",
                (cutoff,),
            ).fetchall()
        per = [(str(r["sender"]), int(r["c"])) for r in rows]
        return sum(c for _, c in per), per
    except Exception:
        return 0, []


def _audit_count(action: str, days: int) -> int:
    try:
        cutoff = (now_kst() - timedelta(days=days)).isoformat()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM audit_log WHERE action=? AND created_at >= ?",
                (action, cutoff),
            ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0


def _outbox_sent(days: int) -> int:
    try:
        cutoff = (now_kst() - timedelta(days=days)).isoformat()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COUNT(*) c FROM bot_outbox WHERE status='SENT' AND updated_at >= ?",
                (cutoff,),
            ).fetchone()
        return int(row["c"]) if row else 0
    except Exception:
        return 0


def build_report(days: int = 1, detailed: bool = False, admin_spend: tuple | None = None) -> str:
    """Plain-text report over the last `days`. `detailed=True` (email) lists
    everything; the compact form (KakaoTalk) keeps top-N per section.
    `admin_spend` = (day_usd, week_usd) from the OpenAI Costs API, optional."""
    now = now_kst()
    start = now - timedelta(days=days)
    title = "주간" if days >= 7 else "일일"
    top = 50 if detailed else 5
    lines = [f"📊 온반봇 {title} 리포트", f"{start:%m-%d %H:%M} ~ {now:%m-%d %H:%M} KST", ""]

    # Requests
    t = ops_service.turn_stats(days)
    lines.append("[요청]")
    lines.append(
        f"- 질문 {t['count']}건 / 방 {len(t['rooms'])}개 / 실패 {t['failed']}건 / 도구 호출 {t['tool_calls']}회"
    )
    if t["count"]:
        lines.append(f"- 응답 지연 중앙값 {_fmt_ms(t['p50_ms'])}, p95 {_fmt_ms(t['p95_ms'])}")
    if t.get("inbound_p50_ms") or t.get("inbound_p95_ms"):
        lines.append(
            f"- 수신 지연(카톡→서버) 중앙값 {_fmt_ms(t['inbound_p50_ms'])}, p95 {_fmt_ms(t['inbound_p95_ms'])}"
        )
    room_cost = _room_cost(days)
    for name, n in t["rooms"][:top]:
        cost = room_cost.get(name)
        lines.append(f"- {name}: {n}건" + (f" (${cost:.3f})" if cost is not None else ""))
    throttled = ops_service.event_count("throttle", days)
    if throttled:
        lines.append(f"- 스로틀로 무시한 요청 {throttled}건")
    lines.append("")

    # Cost
    lines.append("[비용 (자체 추정)]")
    lines.append(f"- 이번 기간 ${spend_since_usd(days):.3f}")
    if days < 7:
        lines.append(f"- 최근 7일 ${spend_since_usd(7):.3f}")
    breakdown = spend_breakdown(days)
    if breakdown:
        lines.append("- 종류별: " + ", ".join(f"{k} ${v:.3f}" for k, v in breakdown[:8]))
    if admin_spend and any(v is not None for v in admin_spend):
        day_usd, week_usd = admin_spend
        parts = []
        if day_usd is not None:
            parts.append(f"24h ${day_usd:.2f}")
        if week_usd is not None:
            parts.append(f"7일 ${week_usd:.2f}")
        lines.append("- OpenAI 청구 기준: " + ", ".join(parts))
    lines.append("")

    # Tools
    tools = ops_service.tool_stats(days)
    if tools:
        lines.append("[도구]")
        for row in tools[:top]:
            fails = row["hard_fail"] + row["soft_fail"]
            fail_txt = f", 실패 {fails}" if fails else ""
            lines.append(f"- {row['name']} {row['calls']}회 (평균 {_fmt_ms(row['avg_ms'])}{fail_txt})")
        lines.append("")

    # Images
    img_total, per_sender = _image_stats(days)
    if img_total:
        who = ", ".join(f"{s} {c}" for s, c in per_sender[:top])
        lines.append(f"[이미지] 생성 {img_total}장 ({who})")
        lines.append("")

    # Corrections
    fb = recent_feedback(days, limit=top)
    lines.append(f"[오답 지적 {len(fb)}건]")
    for f in fb[: (top if detailed else 3)]:
        q = (f.get("prev_question") or "").replace("\n", " ")
        q = q[:60] if detailed else q[:30]
        lines.append(f"- {f.get('sender')}: \"{(f.get('correction') or '')[:40]}\" ← {q}")
    lines.append("")

    # Safety / delivery
    inj = _audit_count("injection_blocked", days)
    sent = _outbox_sent(days)
    lines.append(f"[기타] 인젝션 차단 {inj}건, 알림 발송 {sent}건")

    text = "\n".join(lines).rstrip()
    if not detailed and len(text) > _KAKAO_MAX_CHARS:
        text = text[: _KAKAO_MAX_CHARS - 1] + "…"
    return text


def _report_chat_id() -> int:
    return int(settings.report_chat_id or settings.health_self_chat_id or 0)


async def deliver_report(period: str = "daily", repo=None) -> dict:
    """Build and send the report over the configured channels.
    period: "daily" (1 day) | "weekly" (7 days). Returns per-channel results."""
    days = 7 if period == "weekly" else 1
    admin_spend = (await fetch_openai_spend_usd(1), await fetch_openai_spend_usd(7))
    results: dict[str, bool] = {}
    channels = settings.report_channels

    if "email" in channels:
        body = await asyncio.to_thread(build_report, days, True, admin_spend)
        results["email"] = await send_alert(f"{'주간' if days >= 7 else '일일'} 운영 리포트", body)

    if "kakao" in channels:
        chat_id = _report_chat_id()
        if not chat_id:
            logger.info("report kakao channel skipped (REPORT_CHAT_ID unset)")
            results["kakao"] = False
        else:
            if repo is None:
                from app.dependencies import boss_repo as repo  # noqa: PLC0415
            body = await asyncio.to_thread(build_report, days, False, admin_spend)
            now_iso = now_kst().isoformat()
            dedup = f"report:{period}:{now_kst():%Y-%m-%d}"
            try:
                await asyncio.to_thread(
                    repo.enqueue_outbox, chat_id, "리포트", body, now_iso, now_iso, dedup
                )
                results["kakao"] = True
            except Exception as e:
                logger.warning("report enqueue failed: %s", e)
                results["kakao"] = False
    return results


def is_admin(room_id: int, sender: str) -> bool:
    if settings.admin_room_id and room_id == settings.admin_room_id:
        return True
    if _report_chat_id() and room_id == _report_chat_id():
        return True
    return sender in settings.admin_senders


REPORT_COMMANDS = {"!리포트", "!리포트도움"}


async def handle_report_command(room_id: int, sender: str, name: str, args: list[str]) -> str | None:
    if name not in REPORT_COMMANDS:
        return None
    if name == "!리포트도움":
        return "!리포트 [오늘|주간|N일] — 요청/비용/도구/오답 운영 리포트 (관리자 전용)"
    if not is_admin(room_id, sender):
        return "리포트는 관리자만 볼 수 있어."
    days = 1
    arg = args[0] if args else ""
    if arg.startswith("주간") or arg in {"7일", "주"}:
        days = 7
    elif arg.endswith("일") and arg[:-1].isdigit():
        days = max(1, min(30, int(arg[:-1])))
    return await asyncio.to_thread(build_report, days, False, None)
