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
    exa_api_key: str | None
    kis_app_key: str | None
    kis_app_secret: str | None
    kis_base_url: str
    rag_search_k: int
    buffer_size: int
    max_history_messages: int


settings = Settings(
    openai_api_key=os.getenv("OPENAI_API_KEY"),
    db_connection_string=os.getenv("DB_CONNECTION_STRING"),
    allowed_rooms=set(int(r) for r in os.getenv("ALLOWED_ROOMS", "").split(",") if r.strip()),
    nexon_api_key=os.getenv("NEXON_API_KEY"),
    nexon_api_base="https://open.api.nexon.com/maplestory/v1",
    exa_api_key=os.getenv("EXA_API_KEY"),
    kis_app_key=os.getenv("KIS_APP_KEY"),
    kis_app_secret=os.getenv("KIS_APP_SECRET"),
    kis_base_url=os.getenv("KIS_BASE_URL", "https://openapi.koreainvestment.com:9443"),
    rag_search_k=int(os.getenv("RAG_SEARCH_K", "10")),
    buffer_size=20,
    max_history_messages=10,
)
