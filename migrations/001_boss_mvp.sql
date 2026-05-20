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
