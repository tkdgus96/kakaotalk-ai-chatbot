import sqlite3
from contextlib import contextmanager
from pathlib import Path

from app.config import settings


def _sqlite_path_from_url(url: str) -> str:
    prefix = "sqlite:///"
    if not url.startswith(prefix):
        raise ValueError("Only sqlite URLs are supported for boss feature DB")
    return url[len(prefix) :]


def ensure_parent_dir(path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_conn():
    db_url = settings.boss_db_url
    db_path = _sqlite_path_from_url(db_url)
    ensure_parent_dir(db_path)
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


def init_schema() -> None:
    with get_conn() as conn:
        conn.executescript(
            """
CREATE TABLE IF NOT EXISTS weekly_boss (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id INTEGER NOT NULL,
  boss_name TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(room_id, boss_name)
);

CREATE TABLE IF NOT EXISTS boss_schedule (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  weekly_boss_id INTEGER NOT NULL,
  room_id INTEGER NOT NULL,
  boss_name TEXT NOT NULL,
  week_start_date TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  reminder_sent_at TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  UNIQUE(room_id, boss_name, week_start_date),
  FOREIGN KEY (weekly_boss_id) REFERENCES weekly_boss(id)
);

CREATE TABLE IF NOT EXISTS boss_drop (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  boss_schedule_id INTEGER NOT NULL,
  room_id INTEGER NOT NULL,
  boss_name TEXT NOT NULL,
  item_name TEXT NOT NULL,
  price_mesos INTEGER NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  FOREIGN KEY (boss_schedule_id) REFERENCES boss_schedule(id)
);

CREATE TABLE IF NOT EXISTS settlement (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  settlement_code TEXT NOT NULL UNIQUE,
  public_token TEXT NOT NULL UNIQUE,
  boss_schedule_id INTEGER NOT NULL,
  room_id INTEGER NOT NULL,
  boss_name TEXT NOT NULL,
  total_price_mesos INTEGER NOT NULL,
  member_count INTEGER NOT NULL,
  price_per_member_mesos INTEGER NOT NULL,
  status TEXT NOT NULL,
  created_by TEXT NOT NULL,
  created_at TEXT NOT NULL,
  completed_at TEXT,
  FOREIGN KEY (boss_schedule_id) REFERENCES boss_schedule(id)
);

CREATE TABLE IF NOT EXISTS scheduler_state (
  state_key TEXT PRIMARY KEY,
  state_value TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_room (
  room_id INTEGER PRIMARY KEY,
  room_name TEXT NOT NULL,
  last_seen_at TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bot_outbox (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id INTEGER NOT NULL,
  room_name TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL,
  scheduled_at TEXT NOT NULL,
  sent_at TEXT,
  dedup_key TEXT UNIQUE,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_persona (
  room_id INTEGER PRIMARY KEY,
  persona_text TEXT NOT NULL,
  sample_size INTEGER NOT NULL,
  computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS recurring_reminder (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  room_id INTEGER NOT NULL,
  fire_hour INTEGER NOT NULL,
  fire_minute INTEGER NOT NULL,
  template TEXT NOT NULL,
  start_date TEXT NOT NULL,
  created_by TEXT NOT NULL,
  enabled INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS room_topics (
  room_id INTEGER PRIMARY KEY,
  topics_json TEXT NOT NULL,
  sample_size INTEGER NOT NULL,
  computed_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS chat_summary_cache (
  room_id INTEGER NOT NULL,
  date_label TEXT NOT NULL,
  query_key TEXT NOT NULL,
  summary_text TEXT NOT NULL,
  source_count INTEGER NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (room_id, date_label, query_key)
);
"""
        )
