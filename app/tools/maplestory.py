import asyncio
import json

import httpx
from langchain_core.tools import tool

from app.config import settings


@tool
async def lookup_maplestory_character(character_name: str) -> str:
    """메이플스토리 캐릭터 정보를 조회합니다. 유저가 메이플스토리 캐릭터에 대해 물어볼 때 사용하세요."""
    headers = {"x-nxopen-api-key": settings.nexon_api_key}
    async with httpx.AsyncClient() as client:
        resp = await client.get(
            f"{settings.nexon_api_base}/id",
            params={"character_name": character_name},
            headers=headers,
        )
        if resp.status_code != 200:
            return f"캐릭터 '{character_name}'을(를) 찾을 수 없습니다."
        ocid = resp.json()["ocid"]

        basic_resp, stat_resp = await asyncio.gather(
            client.get(f"{settings.nexon_api_base}/character/basic", params={"ocid": ocid}, headers=headers),
            client.get(f"{settings.nexon_api_base}/character/stat", params={"ocid": ocid}, headers=headers),
        )

    basic = basic_resp.json()
    stats = stat_resp.json()

    info = {
        "캐릭터명": basic.get("character_name"),
        "월드": basic.get("world_name"),
        "직업": basic.get("character_class"),
        "레벨": basic.get("character_level"),
        "경험치율": f"{basic.get('character_exp_rate')}%",
        "길드": basic.get("character_guild_name") or "없음",
        "성별": basic.get("character_gender"),
    }

    for stat in stats.get("final_stat", []):
        info[stat["stat_name"]] = stat["stat_value"]

    return json.dumps(info, ensure_ascii=False, indent=2)
