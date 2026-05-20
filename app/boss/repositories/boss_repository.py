from datetime import datetime

from app.boss.db import get_conn


class BossRepository:
    def upsert_room(self, room_id: int, room_name: str, now_iso: str):
        with get_conn() as conn:
            conn.execute(
                """
                INSERT INTO chat_room (room_id, room_name, last_seen_at, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(room_id) DO UPDATE SET
                    room_name=excluded.room_name,
                    last_seen_at=excluded.last_seen_at,
                    updated_at=excluded.updated_at
                """,
                (room_id, room_name, now_iso, now_iso, now_iso),
            )

    def get_room(self, room_id: int):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM chat_room WHERE room_id=?", (room_id,)).fetchone()

    def list_weekly_bosses_all(self):
        with get_conn() as conn:
            return conn.execute("SELECT * FROM weekly_boss WHERE enabled=1").fetchall()

    def register_weekly_boss(self, room_id: int, boss_name: str, now_iso: str) -> bool:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM weekly_boss WHERE room_id=? AND boss_name=?",
                (room_id, boss_name),
            ).fetchone()
            if row:
                return False
            conn.execute(
                "INSERT INTO weekly_boss (room_id, boss_name, enabled, created_at, updated_at) VALUES (?, ?, 1, ?, ?)",
                (room_id, boss_name, now_iso, now_iso),
            )
            return True

    def list_weekly_bosses(self, room_id: int):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM weekly_boss WHERE room_id=? AND enabled=1 ORDER BY boss_name",
                (room_id,),
            ).fetchall()

    def upsert_schedule(self, weekly_boss_id: int, room_id: int, boss_name: str, week_start_date: str, scheduled_at: str, now_iso: str):
        with get_conn() as conn:
            row = conn.execute(
                "SELECT id FROM boss_schedule WHERE room_id=? AND boss_name=? AND week_start_date=?",
                (room_id, boss_name, week_start_date),
            ).fetchone()
            if row:
                conn.execute(
                    "UPDATE boss_schedule SET scheduled_at=?, reminder_sent_at=NULL, updated_at=? WHERE id=?",
                    (scheduled_at, now_iso, row["id"]),
                )
                return row["id"]
            cur = conn.execute(
                "INSERT INTO boss_schedule (weekly_boss_id, room_id, boss_name, week_start_date, scheduled_at, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (weekly_boss_id, room_id, boss_name, week_start_date, scheduled_at, now_iso, now_iso),
            )
            return cur.lastrowid

    def find_weekly_boss(self, room_id: int, boss_name: str):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM weekly_boss WHERE room_id=? AND boss_name=? AND enabled=1",
                (room_id, boss_name),
            ).fetchone()

    def find_schedule(self, room_id: int, boss_name: str, week_start_date: str):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM boss_schedule WHERE room_id=? AND boss_name=? AND week_start_date=?",
                (room_id, boss_name, week_start_date),
            ).fetchone()

    def list_room_week_schedules(self, room_id: int, week_start_date: str):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM boss_schedule WHERE room_id=? AND week_start_date=?",
                (room_id, week_start_date),
            ).fetchall()

    def add_drop(self, boss_schedule_id: int, room_id: int, boss_name: str, item_name: str, price_mesos: int, sender: str, now_iso: str):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO boss_drop (boss_schedule_id, room_id, boss_name, item_name, price_mesos, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (boss_schedule_id, room_id, boss_name, item_name, price_mesos, sender, now_iso),
            )

    def list_drops_by_schedule(self, boss_schedule_id: int):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM boss_drop WHERE boss_schedule_id=? ORDER BY id",
                (boss_schedule_id,),
            ).fetchall()

    def find_active_schedules(self, room_id: int, from_iso: str, to_iso: str):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM boss_schedule WHERE room_id=? AND scheduled_at BETWEEN ? AND ? ORDER BY scheduled_at DESC",
                (room_id, from_iso, to_iso),
            ).fetchall()

    def create_settlement(self, settlement_code: str, public_token: str, boss_schedule_id: int, room_id: int, boss_name: str, total: int, member_count: int, per_member: int, created_by: str, now_iso: str):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO settlement (settlement_code, public_token, boss_schedule_id, room_id, boss_name, total_price_mesos, member_count, price_per_member_mesos, status, created_by, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'PENDING', ?, ?)",
                (settlement_code, public_token, boss_schedule_id, room_id, boss_name, total, member_count, per_member, created_by, now_iso),
            )

    def count_settlements(self):
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) AS c FROM settlement").fetchone()
            return row["c"]

    def complete_settlement(self, code: str, now_iso: str) -> bool:
        with get_conn() as conn:
            cur = conn.execute(
                "UPDATE settlement SET status='DONE', completed_at=? WHERE settlement_code=?",
                (now_iso, code),
            )
            return cur.rowcount > 0

    def list_settlements(self, room_id: int, limit: int = 10):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM settlement WHERE room_id=? ORDER BY id DESC LIMIT ?",
                (room_id, limit),
            ).fetchall()

    def find_settlement_by_public_token(self, token: str):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM settlement WHERE public_token=?",
                (token,),
            ).fetchone()

    def get_due_pre_reminders(self, threshold_iso: str):
        with get_conn() as conn:
            return conn.execute(
                "SELECT * FROM boss_schedule WHERE reminder_sent_at IS NULL AND scheduled_at <= ?",
                (threshold_iso,),
            ).fetchall()

    def mark_reminder_sent(self, schedule_id: int, now_iso: str):
        with get_conn() as conn:
            conn.execute("UPDATE boss_schedule SET reminder_sent_at=? WHERE id=?", (now_iso, schedule_id))

    def get_scheduler_state(self, key: str):
        with get_conn() as conn:
            row = conn.execute("SELECT state_value FROM scheduler_state WHERE state_key=?", (key,)).fetchone()
            return row["state_value"] if row else None

    def set_scheduler_state(self, key: str, value: str, now_iso: str):
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO scheduler_state (state_key, state_value, updated_at) VALUES (?, ?, ?) ON CONFLICT(state_key) DO UPDATE SET state_value=excluded.state_value, updated_at=excluded.updated_at",
                (key, value, now_iso),
            )

    def enqueue_outbox(self, room_id: int, room_name: str, message: str, scheduled_at: str, now_iso: str, dedup_key: str | None = None):
        with get_conn() as conn:
            conn.execute(
                """
                INSERT OR IGNORE INTO bot_outbox
                (room_id, room_name, message, status, scheduled_at, dedup_key, created_at, updated_at)
                VALUES (?, ?, ?, 'PENDING', ?, ?, ?, ?)
                """,
                (room_id, room_name, message, scheduled_at, dedup_key, now_iso, now_iso),
            )

    def get_pending_outbox(self, now_iso: str, limit: int = 10):
        with get_conn() as conn:
            return conn.execute(
                """
                SELECT id, room_id, room_name, message
                FROM bot_outbox
                WHERE status='PENDING' AND scheduled_at<=?
                ORDER BY id ASC
                LIMIT ?
                """,
                (now_iso, limit),
            ).fetchall()

    def ack_outbox(self, outbox_id: int, status: str, now_iso: str) -> bool:
        with get_conn() as conn:
            if status == "SENT":
                cur = conn.execute(
                    "UPDATE bot_outbox SET status='SENT', sent_at=?, updated_at=? WHERE id=? AND status='PENDING'",
                    (now_iso, now_iso, outbox_id),
                )
            else:
                cur = conn.execute(
                    "UPDATE bot_outbox SET status='FAILED', updated_at=? WHERE id=? AND status='PENDING'",
                    (now_iso, outbox_id),
                )
            return cur.rowcount > 0
