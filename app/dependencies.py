import logging
from collections import defaultdict

from langchain_chroma import Chroma
from langchain_openai import ChatOpenAI, OpenAIEmbeddings

from app.config import settings
from app.boss.repositories.boss_repository import BossRepository
from app.boss.services.boss_service import BossService

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

message_buffers: dict[int, list[str]] = defaultdict(list)

llm = ChatOpenAI(
    model="gpt-4o",
    api_key=settings.openai_api_key,
    temperature=0.7,
    max_tokens=1000,
    verbose=True,
)

embeddings = OpenAIEmbeddings(api_key=settings.openai_api_key)
vectorstore = Chroma(
    collection_name="chat_history",
    embedding_function=embeddings,
    persist_directory="./chroma_db",
)

boss_repo = BossRepository()
boss_service = BossService(boss_repo)
