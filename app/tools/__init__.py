from app.dependencies import llm
from app.tools.maplestory import lookup_maplestory_character
from app.tools.search import naver_search, web_search
from app.tools.stock import get_stock_quote

tools = [lookup_maplestory_character, get_stock_quote, naver_search, web_search]
llm_with_tools = llm.bind_tools(tools)

__all__ = [
    "get_stock_quote",
    "lookup_maplestory_character",
    "naver_search",
    "web_search",
    "tools",
    "llm_with_tools",
]
