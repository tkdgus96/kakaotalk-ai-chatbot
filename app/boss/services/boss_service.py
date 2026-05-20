import secrets
from datetime import datetime
from datetime import timedelta

from app.boss.repositories.boss_repository import BossRepository
from app.boss.utils.price import PriceParseError, format_mesos_kr, parse_price_to_mesos
from app.boss.utils.week import DAY_LABEL, DayTimeParseError, get_week_start_thursday, now_kst, parse_day_time, schedule_datetime_for_current_cycle
from app.config import settings


class BossService:
    def __init__(self, repo: BossRepository):
        self.repo = repo

    def register_weekly_boss(self, room_id: int, boss_name: str) -> str:
        now_iso = now_kst().isoformat()
        ok = self.repo.register_weekly_boss(room_id, boss_name, now_iso)
        if not ok:
            return f"[이미 등록됨]\n{boss_name}는 이미 매주 보스로 등록되어 있습니다."
        return f"[보스 등록 완료]\n{boss_name}를 매주 진행 보스로 등록했습니다.\n목요일마다 이번 주 보스 시간 설정을 요청합니다."

    def touch_room(self, room_id: int, room_name: str) -> None:
        self.repo.upsert_room(room_id, room_name, now_kst().isoformat())

    def set_boss_time(self, room_id: int, boss_name: str, day_raw: str, time_raw: str) -> str:
        wb = self.repo.find_weekly_boss(room_id, boss_name)
        if not wb:
            return f"{boss_name}가 매주 보스로 등록되어 있지 않습니다. 먼저 !보스매주 {boss_name} 를 입력해주세요."
        try:
            day_idx, hh, mm = parse_day_time(day_raw, time_raw)
        except DayTimeParseError as e:
            return str(e)

        schedule_dt = schedule_datetime_for_current_cycle(day_idx, hh, mm)
        week_start = get_week_start_thursday().date().isoformat()
        now_iso = now_kst().isoformat()
        self.repo.upsert_schedule(wb["id"], room_id, boss_name, week_start, schedule_dt.isoformat(), now_iso)

        return f"[보스 일정 등록]\n보스: {boss_name}\n시간: {DAY_LABEL[day_idx]} {hh:02d}:{mm:02d}\n\n30분 전에 알림을 드립니다."

    def list_week_bosses(self, room_id: int) -> str:
        week_start = get_week_start_thursday().date().isoformat()
        weekly = self.repo.list_weekly_bosses(room_id)
        by_boss = {r["boss_name"]: r for r in self.repo.list_room_week_schedules(room_id, week_start)}

        lines = ["[이번 주 보스 일정]", ""]
        for wb in weekly:
            s = by_boss.get(wb["boss_name"])
            if not s:
                lines.append(f"{wb['boss_name']} - 미정")
                continue
            dt = datetime.fromisoformat(s["scheduled_at"])
            lines.append(f"{wb['boss_name']} - {DAY_LABEL[dt.weekday()]} {dt.strftime('%H:%M')}")
        return "\n".join(lines)

    def register_drop(self, room_id: int, sender: str, args: list[str]) -> str:
        week_start = get_week_start_thursday().date().isoformat()

        if len(args) == 2:
            item_name, price_raw = args
            now = now_kst()
            active = self.repo.find_active_schedules(
                room_id,
                (now - timedelta(hours=settings.drop_active_window_hours)).isoformat(),
                (now + timedelta(hours=settings.drop_active_window_hours)).isoformat(),
            )
            if len(active) != 1:
                return "진행 보스가 여러 개라 구분이 필요합니다. !드랍 [bossName] [itemName] [price] 형식으로 입력해주세요."
            target = active[0]
            boss_name = target["boss_name"]
        elif len(args) == 3:
            boss_name, item_name, price_raw = args
            target = self.repo.find_schedule(room_id, boss_name, week_start)
            if not target:
                return f"이번 주 {boss_name} 일정이 없습니다. 먼저 !보스시간으로 등록해주세요."
        else:
            return "사용법: !드랍 [itemName] [price] 또는 !드랍 [bossName] [itemName] [price]"

        try:
            price = parse_price_to_mesos(price_raw)
        except PriceParseError as e:
            return str(e)

        self.repo.add_drop(target["id"], room_id, boss_name, item_name, price, sender, now_kst().isoformat())
        return f"[드랍 등록]\n{boss_name} / {item_name} / {format_mesos_kr(price)}"

    def create_settlement(self, room_id: int, sender: str, boss_name: str, member_count_raw: str) -> str:
        if not member_count_raw.isdigit() or int(member_count_raw) <= 0:
            return "분배 인원은 1 이상의 숫자여야 합니다."
        member_count = int(member_count_raw)

        week_start = get_week_start_thursday().date().isoformat()
        schedule = self.repo.find_schedule(room_id, boss_name, week_start)
        if not schedule:
            return "이번 주 보스 일정을 찾을 수 없습니다."

        drops = self.repo.list_drops_by_schedule(schedule["id"])
        if not drops:
            return "정산할 드랍이 없습니다."

        total = sum(d["price_mesos"] for d in drops)
        per_member = total // member_count
        idx = self.repo.count_settlements() + 1
        code = f"B{idx:03d}"
        token = secrets.token_urlsafe(16)
        self.repo.create_settlement(code, token, schedule["id"], room_id, boss_name, total, member_count, per_member, sender, now_kst().isoformat())

        lines = [f"[{boss_name} 정산]", "", "드랍 아이템"]
        for d in drops:
            lines.append(f"- {d['item_name']} {format_mesos_kr(d['price_mesos'])}")
        lines.extend(
            [
                "",
                f"총 금액: {format_mesos_kr(total)}",
                f"분배 인원: {member_count}명",
                f"1인당 분배금: {format_mesos_kr(per_member)}",
                "",
                f"정산 ID: {code}",
                f"현황: {settings.public_base_url}/s/{token}",
                f"완료 처리: !정산완료 {code}",
            ]
        )
        return "\n".join(lines)

    def complete_settlement(self, code: str) -> str:
        ok = self.repo.complete_settlement(code, now_kst().isoformat())
        if not ok:
            return "해당 정산 코드를 찾지 못했습니다."
        return f"정산 {code}를 완료 처리했습니다."

    def settlement_history(self, room_id: int) -> str:
        rows = self.repo.list_settlements(room_id)
        lines = ["[최근 정산 기록]", ""]
        for r in rows:
            status = "완료" if r["status"] == "DONE" else "미완료"
            lines.append(
                f"{r['settlement_code']} {r['boss_name']} / 총 {format_mesos_kr(r['total_price_mesos'])} / {r['member_count']}명 / 1인 {format_mesos_kr(r['price_per_member_mesos'])} / {status}"
            )
        return "\n".join(lines)

    def settlement_view(self, token: str):
        settlement = self.repo.find_settlement_by_public_token(token)
        if not settlement:
            return None
        drops = self.repo.list_drops_by_schedule(settlement["boss_schedule_id"])
        return settlement, drops

    def get_pending_outbox(self, limit: int = 10):
        rows = self.repo.get_pending_outbox(now_kst().isoformat(), limit * 3)
        filtered = [r for r in rows if r["room_id"] in settings.allowed_rooms]
        return filtered[:limit]

    def ack_outbox(self, outbox_id: int, status: str) -> bool:
        return self.repo.ack_outbox(outbox_id, status, now_kst().isoformat())
