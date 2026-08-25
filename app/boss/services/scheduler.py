import asyncio
from datetime import datetime, timedelta

from app.boss.repositories.boss_repository import BossRepository
from app.boss.utils.week import get_week_start_thursday, now_kst
from app.config import settings
from app.dependencies import logger


def render_recurring_message(template: str, start_date: str, now: datetime) -> str:
    """Render a recurring reminder template. `{N}` becomes the day count since
    start_date (start day = day 1), so '금주 {N}일차' counts up daily."""
    if "{N}" not in template:
        return template
    try:
        start = datetime.fromisoformat(start_date).date()
        days = (now.date() - start).days + 1
    except Exception:
        return template
    return template.replace("{N}", str(days))


class BossScheduler:
    def __init__(self, repo: BossRepository):
        self.repo = repo
        self._running = False

    async def run_forever(self):
        self._running = True
        while self._running:
            try:
                self.tick()
            except Exception as e:
                logger.exception("scheduler tick failed: %s", e)
            await asyncio.sleep(settings.scheduler_interval_seconds)

    def stop(self):
        self._running = False

    def tick(self):
        now = now_kst()
        self._run_weekly_reset_once(now)
        self._run_pre_reminders(now)
        self._run_recurring_reminders(now)

    def _run_weekly_reset_once(self, now: datetime):
        if now.weekday() != 3:
            return
        if now.hour != settings.weekly_reset_reminder_hour or now.minute < settings.weekly_reset_reminder_minute:
            return

        key = f"weekly_reset:{now.date().isoformat()}"
        if self.repo.get_scheduler_state(key):
            return

        week_start = get_week_start_thursday(now).date().isoformat()
        with_rooms = {}
        for wb in self.repo.list_weekly_bosses_all():
            with_rooms.setdefault(wb["room_id"], []).append(wb)

        for room_id, bosses in with_rooms.items():
            if room_id not in settings.allowed_rooms:
                continue
            room = self.repo.get_room(room_id)
            if not room:
                continue
            schedules = {s["boss_name"] for s in self.repo.list_room_week_schedules(room_id, week_start)}
            for b in bosses:
                if b["boss_name"] not in schedules:
                    msg = (
                        "[이번 주 보스 시간 설정 필요]\n"
                        f"{b['boss_name']} 시간이 아직 등록되지 않았습니다.\n\n"
                        f"예시:\n!보스시간 {b['boss_name']} 토요일 22:00"
                    )
                    dedup_key = f"weekly-reset:{week_start}:{room_id}:{b['boss_name']}"
                    self.repo.enqueue_outbox(room_id, room["room_name"], msg, now.isoformat(), now.isoformat(), dedup_key)

        self.repo.set_scheduler_state(key, "sent", now.isoformat())

    def _run_recurring_reminders(self, now: datetime):
        for r in self.repo.list_recurring_reminders_all():
            if r["room_id"] not in settings.allowed_rooms:
                continue
            fire_at = now.replace(
                hour=int(r["fire_hour"]), minute=int(r["fire_minute"]), second=0, microsecond=0
            )
            if now < fire_at:
                continue
            room = self.repo.get_room(r["room_id"])
            if not room:
                continue
            msg = render_recurring_message(r["template"], r["start_date"], now)
            # dedup_key가 UNIQUE라 같은 날 tick이 여러 번 돌아도 한 번만 enqueue된다
            dedup_key = f"recurring:{r['id']}:{now.date().isoformat()}"
            self.repo.enqueue_outbox(
                r["room_id"], room["room_name"], msg, fire_at.isoformat(), now.isoformat(), dedup_key
            )

    def _run_pre_reminders(self, now: datetime):
        threshold = (now + timedelta(minutes=settings.reminder_offset_minutes)).isoformat()
        rows = self.repo.get_due_pre_reminders(threshold)
        for r in rows:
            if r["room_id"] not in settings.allowed_rooms:
                continue
            room = self.repo.get_room(r["room_id"])
            if not room:
                continue
            msg = (
                f"[보스 {settings.reminder_offset_minutes}분 전 알림]\n"
                f"{settings.reminder_offset_minutes}분 뒤 {r['boss_name']} 예정입니다.\n"
                "준비해주세요."
            )
            dedup_key = f"pre-reminder:{r['id']}:{settings.reminder_offset_minutes}"
            self.repo.enqueue_outbox(r["room_id"], room["room_name"], msg, now.isoformat(), now.isoformat(), dedup_key)
            self.repo.mark_reminder_sent(r["id"], now.isoformat())
