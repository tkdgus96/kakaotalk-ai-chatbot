import os
from dataclasses import dataclass
from dotenv import load_dotenv

load_dotenv()


def _parse_variant_overrides(raw: str) -> dict[int, str]:
    """Parse 'room_id:variant,room_id:variant' string into a dict."""
    out: dict[int, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if not pair or ":" not in pair:
            continue
        rid_str, variant = pair.split(":", 1)
        try:
            out[int(rid_str.strip())] = variant.strip()
        except ValueError:
            continue
    return out


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
    openweather_api_key: str | None
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
    prompt_variant_overrides: dict[int, str]
    default_prompt_variant: str
    enable_model_routing: bool
    light_model: str
    image_gen_model: str
    iris_base_url: str
    iris_bot_triggers: list[str]
    iris_self_names: set[str]
    iris_room_map_seed: str
    enable_iris_sender: bool
    iris_sender_interval_seconds: int
    health_heartbeat_minutes: int
    health_self_chat_id: int
    health_iris_fail_threshold: int
    health_kakao_fail_threshold: int
    smtp_host: str | None
    smtp_port: int
    smtp_user: str | None
    smtp_password: str | None
    smtp_starttls: bool
    alert_email_from: str | None
    alert_email_to: str | None
    openai_admin_key: str | None
    cost_report_hour: int
    enable_health_monitor: bool
    image_gen_daily_limit: int
    chat_per_min_limit: int
    chat_log_retention_days: int
    admin_room_id: int
    max_crawl_urls: int
    crawl_timeout: float
    enable_js: bool
    cache_ttl: int
    enable_crawl4ai: bool


settings = Settings(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    db_connection_string=os.getenv("DB_CONNECTION_STRING"),
    allowed_rooms=set(int(r) for r in os.getenv("ALLOWED_ROOMS", "").split(",") if r.strip()),
    nexon_api_key=os.getenv("NEXON_API_KEY"),
    nexon_api_base="https://open.api.nexon.com/maplestory/v1",
    naver_client_id=os.getenv("NAVER_CLIENT_ID"),
    naver_client_secret=os.getenv("NAVER_CLIENT_SECRET"),
    tavily_api_key=os.getenv("TAVILY_API_KEY"),
    openweather_api_key=os.getenv("OPENWEATHER_API_KEY"),
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
    prompt_variant_overrides=_parse_variant_overrides(os.getenv("PROMPT_VARIANT_OVERRIDES", "")),
    default_prompt_variant=os.getenv("DEFAULT_PROMPT_VARIANT", "default"),
    enable_model_routing=os.getenv("ENABLE_MODEL_ROUTING", "true").lower() in {"1", "true", "yes", "on"},
    light_model=os.getenv("LIGHT_MODEL", "gpt-4o-mini"),
    image_gen_model=os.getenv("IMAGE_GEN_MODEL", "gpt-image-1"),
    iris_base_url=os.getenv("IRIS_BASE_URL", "http://127.0.0.1:3000"),
    iris_bot_triggers=[t.strip() for t in os.getenv("IRIS_BOT_TRIGGERS", "!,！").split(",") if t.strip()],
    iris_self_names={n.strip() for n in os.getenv("IRIS_SELF_NAMES", "온반봇").split(",") if n.strip()},
    iris_room_map_seed=os.getenv("IRIS_ROOM_MAP", ""),
    enable_iris_sender=os.getenv("ENABLE_IRIS_SENDER", "false").lower() in {"1", "true", "yes", "on"},
    iris_sender_interval_seconds=int(os.getenv("IRIS_SENDER_INTERVAL_SECONDS", "5")),
    health_heartbeat_minutes=int(os.getenv("HEALTH_HEARTBEAT_MINUTES", "360")),
    health_self_chat_id=int(os.getenv("HEALTH_SELF_CHAT_ID", "0")),
    health_iris_fail_threshold=int(os.getenv("HEALTH_IRIS_FAIL_THRESHOLD", "3")),
    health_kakao_fail_threshold=int(os.getenv("HEALTH_KAKAO_FAIL_THRESHOLD", "2")),
    smtp_host=os.getenv("SMTP_HOST") or None,
    smtp_port=int(os.getenv("SMTP_PORT", "587")),
    smtp_user=os.getenv("SMTP_USER") or None,
    smtp_password=os.getenv("SMTP_PASSWORD") or None,
    smtp_starttls=os.getenv("SMTP_STARTTLS", "true").lower() in {"1", "true", "yes", "on"},
    alert_email_from=os.getenv("ALERT_EMAIL_FROM") or None,
    alert_email_to=os.getenv("ALERT_EMAIL_TO") or None,
    openai_admin_key=os.getenv("OPENAI_ADMIN_KEY") or None,
    cost_report_hour=int(os.getenv("COST_REPORT_HOUR", "9")),
    enable_health_monitor=os.getenv("ENABLE_HEALTH_MONITOR", "true").lower() in {"1", "true", "yes", "on"},
    image_gen_daily_limit=int(os.getenv("IMAGE_GEN_DAILY_LIMIT", "15")),
    chat_per_min_limit=int(os.getenv("CHAT_PER_MIN_LIMIT", "8")),
    chat_log_retention_days=int(os.getenv("CHAT_LOG_RETENTION_DAYS", "0")),
    admin_room_id=int(os.getenv("ADMIN_ROOM_ID", "0")),
    max_crawl_urls=int(os.getenv("MAX_CRAWL_URLS", "3")),
    crawl_timeout=float(os.getenv("CRAWL_TIMEOUT", "10")),
    enable_js=os.getenv("ENABLE_JS", "true").lower() in {"1", "true", "yes", "on"},
    cache_ttl=int(os.getenv("CACHE_TTL", "1800")),
    enable_crawl4ai=os.getenv("ENABLE_CRAWL4AI", "true").lower() in {"1", "true", "yes", "on"},
)
