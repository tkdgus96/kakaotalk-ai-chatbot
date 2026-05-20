from dataclasses import dataclass


@dataclass
class WeeklyBoss:
    id: int
    room_id: int
    boss_name: str
    enabled: bool
    created_at: str
    updated_at: str


@dataclass
class BossSchedule:
    id: int
    weekly_boss_id: int
    room_id: int
    boss_name: str
    week_start_date: str
    scheduled_at: str
    reminder_sent_at: str | None
    created_at: str
    updated_at: str


@dataclass
class BossDrop:
    id: int
    boss_schedule_id: int
    room_id: int
    boss_name: str
    item_name: str
    price_mesos: int
    created_by: str
    created_at: str


@dataclass
class Settlement:
    id: int
    settlement_code: str
    public_token: str
    boss_schedule_id: int
    room_id: int
    boss_name: str
    total_price_mesos: int
    member_count: int
    price_per_member_mesos: int
    status: str
    created_by: str
    created_at: str
    completed_at: str | None
