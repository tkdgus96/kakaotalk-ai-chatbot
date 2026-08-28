from langchain_core.tools import tool

from app.services.image_service import collect_generated_image, generate_image_b64


@tool
async def generate_image(prompt: str) -> str:
    """사용자가 그림/사진/이미지를 새로 '그려줘/생성해줘/만들어줘'라고 요청할 때 호출한다.
    실제로 존재하는 사진 검색이 아니라 **생성(create)** 요청일 때만 쓴다.

    prompt: 그릴 장면을 구체적으로 묘사한 영어 또는 한국어 문구.
            사용자의 표현을 그대로 옮기되 필요하면 화풍/구도를 보강한다.
            예) "한강에서 라면 먹고 있는 고양이, 귀여운 일러스트풍".

    이미지는 이 도구가 방으로 직접 전송한다. 호출 후에는 짧은 코멘트만 답하면 된다
    (이미지를 텍스트로 다시 묘사하지 마)."""
    b64 = await generate_image_b64(prompt)
    if not b64:
        return "이미지 생성에 실패했어. 잠시 후 다시 시도해줘."
    collect_generated_image(b64)
    return f"'{prompt}' 이미지를 생성해서 방에 전송했어."
