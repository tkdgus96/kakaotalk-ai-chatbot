import logging
from collections import defaultdict

from langchain_chroma import Chroma
from langchain_openai import OpenAIEmbeddings

from app.config import settings
from app.llm_factory import make_chat_model
from app.boss.repositories.boss_repository import BossRepository
from app.boss.services.boss_service import BossService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

message_buffers: dict[int, list[str]] = defaultdict(list)

# Main chat model (CHAT_MODEL, default gpt-4o). Provider-agnostic via llm_factory.
llm = make_chat_model(settings.chat_model, temperature=0.7, max_tokens=1000, verbose=True)

# Optional secondary model tried when the primary raises (LLM_FALLBACK_MODEL).
fallback_llm = (
    make_chat_model(settings.fallback_model, temperature=0.7, max_tokens=1000)
    if settings.fallback_model
    else None
)

embeddings = OpenAIEmbeddings(api_key=settings.openai_api_key)
vectorstore = Chroma(
    collection_name="chat_history",
    embedding_function=embeddings,
    persist_directory="./chroma_db",
)
user_profile_store = Chroma(
    collection_name="user_profile",
    embedding_function=embeddings,
    persist_directory="./chroma_db",
)

# Cheap model for clearly-casual turns (see _needs_full_model in app/graph.py).
light_llm = make_chat_model(settings.light_model, temperature=0.7, max_tokens=1000)

fact_extractor_llm = make_chat_model(settings.fact_model, temperature=0)

boss_repo = BossRepository()
boss_service = BossService(boss_repo)
