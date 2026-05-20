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
