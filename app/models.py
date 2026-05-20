from pydantic import BaseModel
from typing import Literal


class KakaoMsg(BaseModel):
    room_id: int
    room: str
    msg: str
    sender: str
    is_command: bool


class OutboxAckRequest(BaseModel):
    status: Literal["SENT", "FAILED"]
