"""KakaoTalk / Iris health monitor.

Detects the failure modes that make the bot silently useless — Iris process
down, or KakaoTalk logged out / session broken — and alerts out-of-band
(email), because in those states the bot can't message through KakaoTalk.

Detection:
  1. Iris process: GET {iris_base_url}/config reachable.
  2. KakaoTalk send round-trip (heartbeat, every N min): send a marker to the
     bot self-chat via Iris, then confirm it registered as a bot-authored row
     in the KakaoTalk DB (via Iris /query). Failure => logout/session problem.

State is persisted in scheduler_state so alerts fire on transitions (and once
per hour while down), not every loop. Also emails a daily spend report.
"""

from __future__ import annotations

import asyncio
import json as jsonlib
import time
from datetime import datetime

import httpx

from app.boss.utils.week import now_kst
from app.config import settings
from app.dependencies import logger
from app.services.alert_service import send_alert, send_cost_report

_CHECK_INTERVAL_SEC = 60
_ALERT_REPEAT_SEC = 3600  # re-alert hourly while still down


class HealthMonitor:
    def __init__(self, repo):
        self.repo = repo
        self._running = False
        self._iris_fails = 0
        self._kakao_fails = 0
        self._last_heartbeat = 0.0
        self._status = "ok"  # ok | iris_down | kakao_down
        self._last_alert_at = 0.0
        self._last_cost_report_date = ""

    async def run_forever(self):
        self._running = True
        # Grace period so redroid/Iris can come up after a reboot.
        await asyncio.sleep(30)
        while self._running:
            try:
                await self._tick()
            except Exception as e:
                logger.exception("health monitor tick failed: %s", e)
            await asyncio.sleep(_CHECK_INTERVAL_SEC)

    def stop(self):
        self._running = False

    async def _tick(self):
        now = now_kst()
        iris_ok = await self._check_iris()
        self._iris_fails = 0 if iris_ok else self._iris_fails + 1

        kakao_ok = True
        if iris_ok and self._due_heartbeat():
            self._last_heartbeat = time.time()
            kakao_ok = await self._heartbeat_roundtrip()
            self._kakao_fails = 0 if kakao_ok else self._kakao_fails + 1

        problem = None
        if self._iris_fails >= settings.health_iris_fail_threshold:
            problem = (
                "iris_down",
                "Iris(브리지)가 응답하지 않습니다.",
                f"연속 {self._iris_fails}회 /config 실패. redroid 컨테이너/Iris 프로세스를 확인하세요.",
            )
        elif self._kakao_fails >= settings.health_kakao_fail_threshold:
            problem = (
                "kakao_down",
                "카카오톡 전송이 실패합니다 (로그아웃/세션 의심).",
                f"자기채팅 왕복 하트비트 연속 {self._kakao_fails}회 실패. "
                "카톡이 로그아웃됐거나 세션이 만료됐을 수 있습니다. scrcpy로 로그인 상태를 확인하세요.",
            )

        await self._handle_state(problem)
        await self._maybe_cost_report(now)

    async def _check_iris(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=8.0) as client:
                res = await client.get(f"{settings.iris_base_url.rstrip('/')}/config")
            return res.status_code == 200
        except Exception:
            return False

    def _due_heartbeat(self) -> bool:
        return (time.time() - self._last_heartbeat) >= settings.health_heartbeat_minutes * 60

    async def _heartbeat_roundtrip(self) -> bool:
        """Send a marker to the self-chat and confirm it lands in the DB."""
        chat_id = settings.health_self_chat_id
        if not chat_id:
            return True  # not configured -> skip (treat as ok)
        base = settings.iris_base_url.rstrip("/")
        sent_at = int(time.time())
        marker = f"[health {sent_at}]"
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.post(
                    f"{base}/reply",
                    json={"type": "text", "room": str(chat_id), "data": marker},
                )
                if r.status_code != 200:
                    return False
                # give Iris a moment to write + observe the row
                await asyncio.sleep(3)
                bot_id = await self._iris_bot_id(client)
                q = {
                    "query": (
                        "SELECT COUNT(*) c FROM chat_logs "
                        "WHERE chat_id = ? AND user_id = ? AND created_at >= ?"
                    ),
                    "bind": [str(chat_id), str(bot_id or 0), str(sent_at)],
                }
                res = await client.post(f"{base}/query", json=q)
            if res.status_code != 200:
                return False
            rows = res.json().get("data", [])
            return bool(rows) and int(rows[0].get("c", 0)) > 0
        except Exception as e:
            logger.warning("heartbeat roundtrip error: %s", e)
            return False

    async def _iris_bot_id(self, client: httpx.AsyncClient) -> int | None:
        try:
            r = await client.get(f"{settings.iris_base_url.rstrip('/')}/config")
            if r.status_code == 200:
                return r.json().get("bot_id")
        except Exception:
            pass
        return None

    async def _handle_state(self, problem):
        now_ts = time.time()
        if problem is None:
            if self._status != "ok":
                await send_alert("복구됨 ✅", "카카오톡/Iris 연결이 정상으로 돌아왔습니다.")
                self._status = "ok"
                self._last_alert_at = 0.0
            return
        status, subject, detail = problem
        transitioned = status != self._status
        stale = (now_ts - self._last_alert_at) >= _ALERT_REPEAT_SEC
        if transitioned or stale:
            when = now_kst().strftime("%Y-%m-%d %H:%M KST")
            await send_alert(f"⚠️ {subject}", f"{detail}\n\n감지 시각: {when}")
            self._status = status
            self._last_alert_at = now_ts

    async def _maybe_cost_report(self, now: datetime):
        today = now.strftime("%Y-%m-%d")
        if now.hour == settings.cost_report_hour and self._last_cost_report_date != today:
            self._last_cost_report_date = today
            await send_cost_report()
