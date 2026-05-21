import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


@dataclass
class Settings:
    openai_api_key: str | None
    db_connection_string: str | None
    allowed_rooms: set[int]
    nexon_api_key: str | None
    nexon_api_base: str
    naver_client_id: str | None
    naver_client_secret: str | None
    tavily_api_key: str | None
    kis_app_key: str | None
    kis_app_secret: str | None
    kis_base_url: str
    rag_search_k: int
    buffer_size: int
    max_history_messages: int
    boss_db_url: str
    public_base_url: str
    weekly_reset_reminder_hour: int
    weekly_reset_reminder_minute: int
    reminder_offset_minutes: int
    scheduler_interval_seconds: int
    drop_active_window_hours: int
    playground_room_id: int
    playground_sender: str


settings = Settings(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    db_connection_string=os.getenv("DB_CONNECTION_STRING"),
    allowed_rooms=set(int(r) for r in os.getenv("ALLOWED_ROOMS", "").split(",") if r.strip()),
    nexon_api_key=os.getenv("NEXON_API_KEY"),
    nexon_api_base="https://open.api.nexon.com/maplestory/v1",
    naver_client_id=os.getenv("NAVER_CLIENT_ID"),
    naver_client_secret=os.getenv("NAVER_CLIENT_SECRET"),
    tavily_api_key=os.getenv("TAVILY_API_KEY"),
    kis_app_key=os.getenv("KIS_APP_KEY"),
    kis_app_secret=os.getenv("KIS_APP_SECRET"),
    kis_base_url=os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
    rag_search_k=int(os.getenv("RAG_SEARCH_K", "10")),
    buffer_size=50,
    max_history_messages=10,
    boss_db_url=os.getenv("BOSS_DB_URL", "sqlite:///./data/boss_bot.db"),
    public_base_url=os.getenv("PUBLIC_BASE_URL", "http://localhost:8000"),
    weekly_reset_reminder_hour=int(os.getenv("WEEKLY_RESET_REMINDER_HOUR", "12")),
    weekly_reset_reminder_minute=int(os.getenv("WEEKLY_RESET_REMINDER_MINUTE", "0")),
    reminder_offset_minutes=int(os.getenv("BOSS_REMINDER_OFFSET_MINUTES", "30")),
    scheduler_interval_seconds=int(os.getenv("BOSS_SCHEDULER_INTERVAL_SECONDS", "60")),
    drop_active_window_hours=int(os.getenv("DROP_ACTIVE_WINDOW_HOURS", "6")),
    playground_room_id=int(os.getenv("PLAYGROUND_ROOM_ID", "999999999")),
    playground_sender=os.getenv("PLAYGROUND_SENDER", "dev_user"),
)
