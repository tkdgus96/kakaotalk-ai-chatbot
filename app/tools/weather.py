import asyncio
from collections import defaultdict
from datetime import datetime, timedelta, timezone

import httpx
from langchain_core.tools import tool

from app.config import settings

_KST = timezone(timedelta(hours=9))
_KO_WEEKDAYS = ["월", "화", "수", "목", "금", "토", "일"]

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


def _fmt_hour(ts: int) -> str:
    return datetime.fromtimestamp(ts, _KST).strftime("%m/%d %H시")


def _build_daily(fc_list: list) -> list[str]:
    """Aggregate OpenWeather 3-hourly entries into per-day summaries (KST):
    daily min/max temp, max precipitation probability, and the weather state
    nearest midday. Free tier covers ~5 days."""
    days: dict = defaultdict(list)
    for entry in fc_list:
        try:
            dt = datetime.fromtimestamp(int(entry["dt"]), _KST)
        except Exception:
            continue
        days[dt.date()].append((dt, entry))

    lines: list[str] = []
    for d in sorted(days)[:5]:
        entries = days[d]
        temps = [e.get("main", {}).get("temp") for _, e in entries if e.get("main", {}).get("temp") is not None]
        if not temps:
            continue
        pops = [e.get("pop", 0) or 0 for _, e in entries]
        rep = min(entries, key=lambda te: abs(te[0].hour - 15))[1]
        desc = (rep.get("weather") or [{}])[0].get("description", "?")
        label = f"{d.strftime('%m/%d')} ({_KO_WEEKDAYS[d.weekday()]})"
        lines.append(
            f"- {label}: {min(temps):.0f}~{max(temps):.0f}°C, {desc}, 강수확률 {int(max(pops) * 100)}%"
        )
    return lines


@tool
async def get_weather(city: str = "서울") -> str:
    """현재 날씨 + 24시간 시간별 예보 + 최대 5일 일별 예보.
    기온·체감온도·강수확률·습도·날씨 상태를 정확히 가져옵니다.
    유저가 "지금 날씨", "오늘/내일 기온", "시간별 예보", "이번 주 날씨", "며칠간/N일 예보",
    "주말 날씨", "강수 확률" 등 날씨 질문을 하면 검색 도구 대신 이 도구를 **우선** 사용하세요.
    다일 예보는 무료 API 한계로 최대 5일까지이며, 7일 등 그 이상을 물으면 5일까지 주고
    "무료 예보는 5일까지"라고 안내하세요. 캐시 없이 OpenWeather에서 실시간 응답.

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
                        "cnt": 40,
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

    fc_list = fc.get("list", [])
    lines.append("")
    lines.append("[향후 24시간 예보 (3시간 간격)]")
    for entry in fc_list[:8]:
        dt_label = _fmt_hour(int(entry["dt"])) if entry.get("dt") else entry.get("dt_txt", "?")
        m = entry.get("main", {})
        w = (entry.get("weather") or [{}])[0]
        pop = int(entry.get("pop", 0) * 100)
        lines.append(
            f"- {dt_label}: {m.get('temp')}°C, {w.get('description', '?')}, 강수확률 {pop}%"
        )

    daily = _build_daily(fc_list)
    if daily:
        lines.append("")
        lines.append("[일별 예보 (최대 5일, 최저~최고 기온)]")
        lines.extend(daily)
        lines.append("(무료 예보는 최대 5일까지 제공)")
    return "\n".join(lines)
