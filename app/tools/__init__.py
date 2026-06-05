from app.dependencies import llm
from app.tools.maplestory import lookup_maplestory_character
from app.tools.meso import get_maple_meso_price
from app.tools.search import naver_search, web_search
from app.tools.stock import get_stock_quote
from app.tools.weather import get_weather

tools = [
    lookup_maplestory_character,
    get_maple_meso_price,
    get_stock_quote,
    naver_search,
    web_search,
    get_weather,
]
llm_with_tools = llm.bind_tools(tools)

__all__ = [
    "get_maple_meso_price",
    "get_stock_quote",
    "get_weather",
    "lookup_maplestory_character",
    "naver_search",
    "web_search",
    "tools",
    "llm_with_tools",
]
