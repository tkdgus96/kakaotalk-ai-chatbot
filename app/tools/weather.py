import asyncio
from datetime import datetime

import httpx
from langchain_core.tools import tool

from app.config import settings

CITY_ALIASES = {
    "서울": "Seoul,KR",
    "부산": "Busan,KR",
    "인천": "Incheon,KR",
    "대구": "Daegu,KR",
    "대전": "Daejeon,KR",
    "광주": "Gwangju,KR",
    "울산": "Ulsan,KR",
    "수원": "Suwon,KR",
    "세종": "Sejong,KR",
    "제주": "Jeju,KR",
    "춘천": "Chuncheon,KR",
    "전주": "Jeonju,KR",
    "포항": "Pohang,KR",
    "강릉": "Gangneung,KR",
    "도쿄": "Tokyo,JP",
    "오사카": "Osaka,JP",
    "뉴욕": "New York,US",
    "런던": "London,GB",
    "파리": "Paris,FR",
}


def _format_dt(dt_txt: str) -> str:
    try:
        d = datetime.strptime(dt_txt, "%Y-%m-%d %H:%M:%S")
        return d.strftime("%m/%d %H시")
    except Exception:
        return dt_txt


@tool
async def get_weather(city: str = "서울") -> str:
    """현재 날씨 + 시간별 예보 (앞으로 24시간, 3시간 간격).
    기온·체감온도·강수확률·습도·날씨 상태를 정확히 가져옵니다.
    유저가 "지금 날씨", "오늘/내일 기온", "시간별 예보", "3시간 뒤", "강수 확률" 등 날씨 관련
    질문을 하면 검색 도구 대신 이 도구를 **우선** 사용하세요. 캐시 없이 OpenWeather에서 실시간 응답.

    city: 도시 이름. 한국 주요 도시 (서울/부산/인천/대구/대전/광주/울산 등) 및 일부 해외 도시
          (도쿄/뉴욕/런던 등) 한글로 지원. 모르는 도시는 영문으로 전달.
          기본값 "서울"."""
    if not settings.openweather_api_key:
        return "OpenWeather API 키가 설정되어 있지 않습니다. (OPENWEATHER_API_KEY 환경변수 필요)"

    q = CITY_ALIASES.get(city.strip(), city.strip())

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            cur_resp, fc_resp = await asyncio.gather(
                client.get(
                    "https://api.openweathermap.org/data/2.5/weather",
                    params={
                        "q": q,
                        "appid": settings.openweather_api_key,
                        "units": "metric",
                        "lang": "kr",
                    },
                ),
                client.get(
                    "https://api.openweathermap.org/data/2.5/forecast",
                    params={
                        "q": q,
                        "appid": settings.openweather_api_key,
                        "units": "metric",
                        "lang": "kr",
                        "cnt": 8,
                    },
                ),
            )
    except Exception as exc:
        return f"날씨 조회 중 오류: {exc}"

    if cur_resp.status_code == 404:
        return f"'{city}' 도시를 찾을 수 없습니다. 다른 도시명으로 시도해보세요."
    if cur_resp.status_code != 200:
        return f"OpenWeather 오류: HTTP {cur_resp.status_code} {cur_resp.text[:200]}"
    if fc_resp.status_code != 200:
        return f"OpenWeather 예보 오류: HTTP {fc_resp.status_code}"

    cur = cur_resp.json()
    fc = fc_resp.json()

    lines = [f"[현재 {city} 날씨]"]
    main = cur.get("main", {})
    weather = (cur.get("weather") or [{}])[0]
    wind = cur.get("wind", {})
    lines.append(f"기온: {main.get('temp')}°C (체감 {main.get('feels_like')}°C)")
    lines.append(f"상태: {weather.get('description', '?')}")
    lines.append(f"습도: {main.get('humidity')}%, 풍속: {wind.get('speed')} m/s")

    lines.append("")
    lines.append("[향후 24시간 예보 (3시간 간격)]")
    for entry in fc.get("list", [])[:8]:
        dt_label = _format_dt(entry.get("dt_txt", "?"))
        m = entry.get("main", {})
        w = (entry.get("weather") or [{}])[0]
        pop = int(entry.get("pop", 0) * 100)
        lines.append(
            f"- {dt_label}: {m.get('temp')}°C, {w.get('description', '?')}, 강수확률 {pop}%"
        )
    return "\n".join(lines)
