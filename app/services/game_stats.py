"""Tally KakaoTalk mini-game results (오늘의 단어 / 오늘의 챌린지) per member.

Those games post a fixed line under the member's name on completion:
"오늘의 단어 맞히기 성공!" / "오늘의 단어 맞히기 실패!" / "오늘의 챌린지 성공!" (등).
We count success/fail from the raw chat log (FTS) to produce per-member
plays / wins / win-rate leaderboards — a group-native stat no general
chatbot can do. (KakaoTalk doesn't post per-game attempt counts, so a "play"
= one success or fail message that day.)
"""

from __future__ import annotations

from datetime import timedelta

from app.boss.db import get_conn
from app.boss.utils.week import now_kst
from app.chat_log import _ensure_fts_available
from app.config import settings

GAME_COMMANDS = {"!기록", "!단어기록", "!챌린지기록", "!게임기록", "!랭킹", "!내기록"}

# game_key -> (success substring, fail substring)
_GAMES = {
    "단어": ("오늘의 단어 맞히기 성공", "오늘의 단어 맞히기 실패"),
    "챌린지": ("오늘의 챌린지 성공", "오늘의 챌린지 실패"),
}


def _bot_names() -> set[str]:
    return set(settings.iris_self_names) | {"Iris", "온반봇"}


def _count_by_sender(room_id: int, like: str, since_days: int | None) -> dict[str, int]:
    if not _ensure_fts_available():
        return {}
    params: list = [str(room_id), f"%{like}%"]
    date_clause = ""
    if since_days:
        params.append((now_kst() - timedelta(days=since_days)).isoformat())
        date_clause = " AND created_at >= ?"
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sender, COUNT(*) c FROM chat_log_fts "
                "WHERE room_id=? AND content LIKE ?" + date_clause + " GROUP BY sender",
                params,
            ).fetchall()
    except Exception:
        return {}
    bots = _bot_names()
    return {r["sender"]: int(r["c"]) for r in rows if r["sender"] and r["sender"] not in bots}


def stats(room_id: int, game_key: str, since_days: int | None = None) -> list[dict]:
    """Per-member [{sender, wins, plays, rate}] sorted by wins then rate."""
    success_sub, fail_sub = _GAMES[game_key]
    wins = _count_by_sender(room_id, success_sub, since_days)
    fails = _count_by_sender(room_id, fail_sub, since_days)
    members = set(wins) | set(fails)
    out = []
    for m in members:
        w = wins.get(m, 0)
        plays = w + fails.get(m, 0)
        out.append({"sender": m, "wins": w, "plays": plays, "rate": (w / plays) if plays else 0.0})
    out.sort(key=lambda d: (d["wins"], d["rate"]), reverse=True)
    return out


def _format_board(title: str, rows: list[dict]) -> str:
    if not rows:
        return f"[{title}]\n아직 기록이 없어."
    medals = ["🥇", "🥈", "🥉"]
    lines = [f"[{title}]"]
    for i, r in enumerate(rows[:10]):
        mark = medals[i] if i < 3 else f"{i + 1}."
        lines.append(
            f"{mark} {r['sender']} — {r['wins']}승/{r['plays']}판 (승률 {round(r['rate'] * 100)}%)"
        )
    return "\n".join(lines)


def _parse_period(args: list[str]) -> tuple[int | None, str]:
    text = " ".join(args)
    if "주" in text or "week" in text.lower():
        return 7, "최근 7일"
    if "월" in text or "month" in text.lower():
        return 30, "최근 30일"
    if "오늘" in text:
        return 1, "오늘"
    return None, "전체"


def _my_line(room_id: int, sender: str, game_key: str, game_label: str, since, period_label) -> str:
    for r in stats(room_id, game_key, since):
        if r["sender"] == sender:
            return f"{game_label}: {r['wins']}승/{r['plays']}판 (승률 {round(r['rate'] * 100)}%)"
    return f"{game_label}: 기록 없음"


def handle_game_command(room_id: int, sender: str, name: str, args: list[str]) -> str | None:
    since, label = _parse_period(args)
    if name == "!내기록":
        word = _my_line(room_id, sender, "단어", "오늘의 단어", since, label)
        chal = _my_line(room_id, sender, "챌린지", "오늘의 챌린지", since, label)
        return f"[{sender} 게임 기록 ({label})]\n{word}\n{chal}"
    if name == "!단어기록":
        return _format_board(f"오늘의 단어 랭킹 ({label})", stats(room_id, "단어", since))
    if name == "!챌린지기록":
        return _format_board(f"오늘의 챌린지 랭킹 ({label})", stats(room_id, "챌린지", since))
    if name in ("!기록", "!게임기록", "!랭킹"):
        word = _format_board(f"오늘의 단어 랭킹 ({label})", stats(room_id, "단어", since))
        chal = _format_board(f"오늘의 챌린지 랭킹 ({label})", stats(room_id, "챌린지", since))
        return word + "\n\n" + chal
    return None
