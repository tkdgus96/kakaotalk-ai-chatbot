from app.dependencies import llm
from app.tools.chat_history import search_chat_history, summarize_chat_history
from app.tools.image_gen import generate_image
from app.tools.finance_research import compare_stocks, get_stock_news, get_stock_snapshot, summarize_korean_stock_market
from app.tools.maplestory import lookup_maplestory_character
from app.tools.memory import forget_user_memory, get_user_memory, remember_user_fact
from app.tools.meso import get_maple_meso_price
from app.tools.reminder import cancel_reminder, create_reminder, list_reminders
from app.tools.research import compare_sources, research_web
from app.tools.search import naver_search, web_search
from app.tools.stock import get_stock_quote
from app.tools.url_reader import read_url, summarize_url
from app.tools.weather import get_weather

tools = [
    lookup_maplestory_character,
    get_maple_meso_price,
    get_stock_quote,
    naver_search,
    web_search,
    get_weather,
    summarize_chat_history,
    search_chat_history,
    read_url,
    summarize_url,
    research_web,
    compare_sources,
    get_user_memory,
    remember_user_fact,
    forget_user_memory,
    create_reminder,
    list_reminders,
    cancel_reminder,
    get_stock_news,
    get_stock_snapshot,
    compare_stocks,
    summarize_korean_stock_market,
    generate_image,
]
llm_with_tools = llm.bind_tools(tools)

__all__ = [
    "get_maple_meso_price",
    "get_stock_quote",
    "get_weather",
    "lookup_maplestory_character",
    "naver_search",
    "web_search",
    "summarize_chat_history",
    "search_chat_history",
    "read_url",
    "summarize_url",
    "research_web",
    "compare_sources",
    "get_user_memory",
    "remember_user_fact",
    "forget_user_memory",
    "create_reminder",
    "list_reminders",
    "cancel_reminder",
    "get_stock_news",
    "get_stock_snapshot",
    "compare_stocks",
    "summarize_korean_stock_market",
    "generate_image",
    "tools",
    "llm_with_tools",
]
