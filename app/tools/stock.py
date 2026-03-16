import asyncio
import json
import re
import time

import httpx
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.tools import tool

from app.config import settings
from app.dependencies import exa, llm, logger

_kis_access_token = None
_kis_access_token_expiry = 0.0


def normalize_stock_candidates(raw_query: str) -> list[str]:
    alias_map = {
        "메타": "META",
        "페이스북": "META",
        "meta": "META",
        "facebook": "META",
        "알파벳": "GOOGL",
        "구글": "GOOGL",
        "google": "GOOGL",
        "애플": "AAPL",
        "apple": "AAPL",
        "엔비디아": "NVDA",
        "nvidia": "NVDA",
        "마이크로소프트": "MSFT",
        "microsoft": "MSFT",
        "테슬라": "TSLA",
        "tesla": "TSLA",
        "팔란티어": "PLTR",
        "palantir": "PLTR",
        "보잉": "BA",
        "boeing": "BA",
        "록히드마틴": "LMT",
        "lockheed": "LMT",
        "lockheedmartin": "LMT",
        "카카오": "035720.KQ",
        "삼성전자": "005930.KS",
        "코스피": "^KS11",
        "kospi": "^KS11",
        "코스닥": "^KQ11",
        "kosdaq": "^KQ11",
    }

    normalized_raw = raw_query.strip()
    lowered_raw = normalized_raw.lower()
    cleaned_raw = re.sub(r"'s\b", "", normalized_raw)
    cleaned_lower = re.sub(r"'s\b", "", lowered_raw)

    tokens_raw = re.findall(r"[0-9A-Za-z가-힣\.\^]+", cleaned_raw)
    tokens_lower = re.findall(r"[0-9A-Za-z가-힣\.\^]+", cleaned_lower)

    symbols: list[str] = []

    exact_alias = alias_map.get(lowered_raw) or alias_map.get(cleaned_lower)
    if exact_alias:
        symbols.append(exact_alias)

    for token in tokens_lower:
        alias = alias_map.get(token.lower())
        if alias:
            symbols.append(alias)

    for token in tokens_raw:
        if is_symbol_like(token):
            symbols.append(token.upper())

    if normalized_raw.isdigit() and len(normalized_raw) == 6:
        symbols.extend([f"{normalized_raw}.KS", f"{normalized_raw}.KQ", normalized_raw])

    if not symbols and is_symbol_like(normalized_raw):
        symbols.append(normalized_raw.upper())

    return list(dict.fromkeys(symbols))


def is_symbol_like(token: str) -> bool:
    if re.fullmatch(r"[A-Z]{1,6}", token):
        return True
    upper = token.upper()
    if re.fullmatch(r"\d{6}(\.(KS|KQ))?", upper):
        return True
    if re.fullmatch(r"\^[A-Z0-9]{2,8}", upper):
        return True
    return False


def _extract_first_ticker(text: str | None):
    if not text:
        return None
    match = re.search(r"\b[A-Z]{1,6}(?:\.(?:KS|KQ))?\b", text)
    if match:
        return match.group(0)
    idx_match = re.search(r"\^[A-Z0-9]{2,8}", text)
    if idx_match:
        return idx_match.group(0)
    return None


def _resolve_symbol_with_llm(raw_query: str):
    prompt = [
        SystemMessage(
            content=(
                "Extract the single most likely stock ticker from the user's query. "
                "Return ticker only, uppercase, no extra text. "
                "If Korea stock, return Yahoo format like 005930.KS or 035720.KQ. "
                "If unknown, return UNKNOWN."
            )
        ),
        HumanMessage(content=raw_query),
    ]
    try:
        response = llm.invoke(prompt)
        ticker = _extract_first_ticker(str(response.content).strip())
        if ticker and is_symbol_like(ticker):
            return ticker
    except Exception:
        return None
    return None


def get_krx_code(symbol: str):
    if symbol.isdigit() and len(symbol) == 6:
        return symbol
    upper_symbol = symbol.upper()
    if (upper_symbol.endswith(".KS") or upper_symbol.endswith(".KQ")) and upper_symbol[:-3].isdigit() and len(upper_symbol[:-3]) == 6:
        return upper_symbol[:-3]
    return None


async def _get_kis_token(client: httpx.AsyncClient):
    global _kis_access_token, _kis_access_token_expiry
    if not settings.kis_app_key or not settings.kis_app_secret:
        return None
    if _kis_access_token and time.time() < _kis_access_token_expiry - 60:
        return _kis_access_token

    resp = await client.post(
        f"{settings.kis_base_url}/oauth2/tokenP",
        json={
            "grant_type": "client_credentials",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
        },
        headers={"content-type": "application/json"},
    )
    if resp.status_code != 200:
        return None

    data = resp.json()
    token = data.get("access_token")
    expires_in = int(data.get("expires_in", 3600))
    if not token:
        return None

    _kis_access_token = token
    _kis_access_token_expiry = time.time() + expires_in
    return _kis_access_token


async def _fetch_kis_krx_quote(client: httpx.AsyncClient, code: str):
    def to_float(value, default=0.0):
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    token = await _get_kis_token(client)
    if not token:
        return None, None

    resp = await client.get(
        f"{settings.kis_base_url}/uapi/domestic-stock/v1/quotations/inquire-price",
        params={"FID_COND_MRKT_DIV_CODE": "J", "FID_INPUT_ISCD": code},
        headers={
            "authorization": f"Bearer {token}",
            "appkey": settings.kis_app_key,
            "appsecret": settings.kis_app_secret,
            "tr_id": "FHKST01010100",
        },
    )
    if resp.status_code != 200:
        return None, f"KIS 시세 조회 실패: HTTP {resp.status_code}"

    output = resp.json().get("output", {})
    price = output.get("stck_prpr")
    if not price:
        return None, None

    return {
        "symbol": code,
        "shortName": output.get("hts_kor_isnm"),
        "currency": "KRW",
        "marketState": output.get("new_mkop_cls_code"),
        "regularMarketPrice": to_float(price),
        "regularMarketChange": to_float(output.get("prdy_vrss")),
        "regularMarketChangePercent": to_float(output.get("prdy_ctrt")),
        "regularMarketPreviousClose": to_float(output.get("stck_sdpr")),
        "regularMarketOpen": to_float(output.get("stck_oprc")),
        "regularMarketDayLow": to_float(output.get("stck_lwpr")),
        "regularMarketDayHigh": to_float(output.get("stck_hgpr")),
        "regularMarketTime": f"{output.get('stck_bsop_date', '')}{output.get('stck_cntg_hour', '')}",
        "source": "korea_investment_openapi",
    }, None


async def _fetch_naver_krx_quote(client: httpx.AsyncClient, code: str):
    try:
        resp = await client.get(
            "https://polling.finance.naver.com/api/realtime",
            params={"query": f"SERVICE_ITEM:{code}|SERVICE_RECENT_ITEM:{code}"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if resp.status_code != 200:
            return None, f"네이버 시세 조회 실패: HTTP {resp.status_code}"

        data = None
        raw_text = resp.text.strip()
        try:
            data = resp.json()
        except Exception:
            if "(" in raw_text and raw_text.endswith(")"):
                inner = raw_text[raw_text.find("(") + 1 : raw_text.rfind(")")]
                data = json.loads(inner)

        if not isinstance(data, dict):
            return None, None

        areas = data.get("result", {}).get("areas", [])
        if not areas:
            return None, None
        datas = areas[0].get("datas", [])
        if not datas:
            return None, None

        item = datas[0]
        quote = {
            "symbol": code,
            "shortName": item.get("nm"),
            "currency": "KRW",
            "marketState": item.get("ms"),
            "regularMarketPrice": item.get("nv"),
            "regularMarketChange": item.get("cv"),
            "regularMarketChangePercent": item.get("cr"),
            "regularMarketPreviousClose": item.get("pcv"),
            "regularMarketOpen": item.get("ov"),
            "regularMarketDayLow": item.get("lv"),
            "regularMarketDayHigh": item.get("hv"),
            "regularMarketTime": f"{item.get('dt', '')}{item.get('tm', '')}",
            "source": "naver_finance_realtime",
        }
        if quote["regularMarketPrice"] is None:
            return None, None
        return quote, None
    except Exception:
        return None, None


async def _fetch_yahoo_quote(client: httpx.AsyncClient, symbol: str):
    async def fetch_chart_quote():
        chart_resp = await client.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}",
            params={"interval": "1d", "range": "5d"},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if chart_resp.status_code != 200:
            return None, f"시세 조회 실패: HTTP {chart_resp.status_code}"

        chart = chart_resp.json().get("chart", {})
        results = chart.get("result", [])
        if not results:
            return None, None
        meta = results[0].get("meta", {})
        if not meta:
            return None, None

        quote = {
            "source": "yahoo_finance_chart",
            "symbol": meta.get("symbol", symbol),
            "shortName": meta.get("shortName"),
            "currency": meta.get("currency"),
            "marketState": meta.get("marketState"),
            "regularMarketPrice": meta.get("regularMarketPrice"),
            "regularMarketChange": None,
            "regularMarketChangePercent": None,
            "regularMarketPreviousClose": meta.get("previousClose"),
            "regularMarketOpen": meta.get("regularMarketOpen"),
            "regularMarketDayLow": meta.get("regularMarketDayLow"),
            "regularMarketDayHigh": meta.get("regularMarketDayHigh"),
            "regularMarketTime": meta.get("regularMarketTime"),
        }
        return quote, None

    last_status = None
    for attempt in range(3):
        resp = await client.get(
            "https://query1.finance.yahoo.com/v7/finance/quote",
            params={"symbols": symbol},
            headers={"User-Agent": "Mozilla/5.0"},
        )
        last_status = resp.status_code
        if resp.status_code == 429 and attempt < 2:
            await asyncio.sleep(0.7 * (attempt + 1))
            continue
        if resp.status_code == 401:
            chart_quote, chart_err = await fetch_chart_quote()
            if chart_quote is not None:
                return chart_quote, None
            return None, chart_err
        if resp.status_code != 200:
            return None, f"시세 조회 실패: HTTP {resp.status_code}"

        results = resp.json().get("quoteResponse", {}).get("result", [])
        if not results:
            return None, None

        quote = results[0]
        quote["source"] = "yahoo_finance"
        return quote, None

    return None, f"시세 조회 실패: HTTP {last_status}"


@tool
async def get_stock_quote(symbol_or_name: str) -> str:
    """주식 심볼 또는 회사명(예: AAPL, META, 메타, 삼성전자)으로 최신 시세를 조회합니다. 현재가, 전일종가, 변동률 같은 실시간 수치가 필요할 때 사용하세요."""
    raw_query = symbol_or_name.strip()
    if not raw_query:
        return "심볼/회사명이 비어 있습니다. 예: META, 메타, 005930.KS"

    candidate_symbols = normalize_stock_candidates(raw_query)
    # If no direct alias/ticker was extracted, keep the raw query so we can
    # still run resolver/search fallback (e.g. "palantir", "meta stock price").
    if not candidate_symbols:
        candidate_symbols = [raw_query]

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            # For plain text company names (e.g. "팔란티어"), resolve ticker first.
            if not any(is_symbol_like(sym) for sym in candidate_symbols):
                llm_symbol = _resolve_symbol_with_llm(raw_query)
                if llm_symbol:
                    candidate_symbols.insert(0, llm_symbol)
                search_resp = await client.get(
                    "https://query1.finance.yahoo.com/v1/finance/search",
                    params={"q": raw_query, "quotesCount": 8, "newsCount": 0},
                )
                if search_resp.status_code == 200:
                    quotes = search_resp.json().get("quotes", [])
                    equity = next((q for q in quotes if q.get("quoteType") == "EQUITY"), None)
                    if equity and equity.get("symbol"):
                        candidate_symbols.insert(0, equity["symbol"])
                        candidate_symbols = list(dict.fromkeys(candidate_symbols))

            quote = None
            err = None
            resolved_symbol = raw_query

            for symbol in candidate_symbols:
                resolved_symbol = symbol
                krx_code = get_krx_code(symbol)

                if krx_code:
                    quote, err = await _fetch_kis_krx_quote(client, krx_code)
                    if quote is not None:
                        break
                    quote, err = await _fetch_naver_krx_quote(client, krx_code)
                    if quote is not None:
                        break

                quote, err = await _fetch_yahoo_quote(client, symbol)
                if quote is not None:
                    break
                if err and "HTTP 429" not in err and is_symbol_like(symbol):
                    break

            if quote is None and err is None:
                search_resp = await client.get(
                    "https://query1.finance.yahoo.com/v1/finance/search",
                    params={"q": raw_query, "quotesCount": 5, "newsCount": 0},
                )
                if search_resp.status_code == 200:
                    quotes = search_resp.json().get("quotes", [])
                    equity = next((q for q in quotes if q.get("quoteType") == "EQUITY"), None)
                    if equity and equity.get("symbol"):
                        resolved_symbol = equity["symbol"]
                        quote, err = await _fetch_yahoo_quote(client, resolved_symbol)

            if err and "HTTP 429" in err:
                try:
                    fallback = exa.search(
                        f"{raw_query} 주가 현재 KRX 코스피 코스닥",
                        num_results=3,
                        type="auto",
                        contents={"text": {"max_characters": 1200}},
                    )
                    snippets = [f"제목: {r.title}\nURL: {r.url}\n내용: {r.text[:500]}" for r in fallback.results]
                    if snippets:
                        return "실시간 시세 API가 일시적으로 혼잡합니다(429).\n대체 웹 검색 결과를 참고해 주세요:\n\n" + "\n\n---\n\n".join(snippets)
                except Exception:
                    pass
                return f"실시간 시세 API가 일시적으로 혼잡합니다(429). 잠시 후 다시 시도하거나 심볼로 재시도해 주세요. (입력: {raw_query})"

            if err:
                return err
            if quote is None:
                return f"'{raw_query}'에 해당하는 시세를 찾지 못했습니다. 심볼(예: META, 005930.KS)로 다시 시도해 주세요."

            output = {
                "query": raw_query,
                "resolvedSymbol": resolved_symbol,
                "source": quote.get("source", "unknown"),
                "symbol": quote.get("symbol", resolved_symbol),
                "shortName": quote.get("shortName"),
                "currency": quote.get("currency"),
                "marketState": quote.get("marketState"),
                "regularMarketPrice": quote.get("regularMarketPrice"),
                "regularMarketChange": quote.get("regularMarketChange"),
                "regularMarketChangePercent": quote.get("regularMarketChangePercent"),
                "regularMarketPreviousClose": quote.get("regularMarketPreviousClose"),
                "regularMarketOpen": quote.get("regularMarketOpen"),
                "regularMarketDayLow": quote.get("regularMarketDayLow"),
                "regularMarketDayHigh": quote.get("regularMarketDayHigh"),
                "regularMarketTime": quote.get("regularMarketTime"),
            }
            logger.info(
                "stock_quote query=%s resolved=%s source=%s price=%s",
                raw_query,
                output["symbol"],
                output["source"],
                output["regularMarketPrice"],
            )
            return json.dumps(output, ensure_ascii=False, indent=2)
    except Exception as exc:
        return f"시세 조회 중 오류가 발생했습니다: {str(exc)}"
