"""Currency conversion via the free Frankfurter API (ECB rates, no key)."""

import httpx
from langchain_core.tools import tool

_ALIAS = {
    "달러": "USD", "미국달러": "USD", "불": "USD", "$": "USD",
    "원": "KRW", "한국돈": "KRW", "₩": "KRW",
    "엔": "JPY", "엔화": "JPY", "円": "JPY",
    "유로": "EUR", "€": "EUR",
    "위안": "CNY", "위안화": "CNY", "元": "CNY",
    "파운드": "GBP", "£": "GBP",
}


def _norm(code: str) -> str:
    c = code.strip()
    return _ALIAS.get(c, c.upper())


@tool
async def convert_currency(amount: float, from_currency: str, to_currency: str = "KRW") -> str:
    """실시간 환율로 통화를 변환한다. "100달러 얼마", "1엔 원화로" 같은 환율/환전 질문에
    직접 추측하지 말고 이 도구를 써라.

    amount: 금액 숫자
    from_currency: 원래 통화 (USD/JPY/EUR/CNY/GBP 또는 달러/엔/유로/위안 등)
    to_currency: 바꿀 통화 (기본 KRW/원)"""
    src, dst = _norm(from_currency), _norm(to_currency)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            res = await client.get(
                "https://api.frankfurter.app/latest",
                params={"amount": amount, "from": src, "to": dst},
            )
        if res.status_code != 200:
            return f"환율 조회 실패 (HTTP {res.status_code}). 통화 코드를 확인해줘."
        data = res.json()
        rate = data.get("rates", {}).get(dst)
        if rate is None:
            return f"'{src}'→'{dst}' 환율을 찾지 못했어. (지원 통화 코드인지 확인)"
        date = data.get("date", "")
        return f"{amount} {src} = {rate:,.2f} {dst} (기준일 {date})"
    except Exception as e:
        return f"환율 조회 중 오류: {e}"
