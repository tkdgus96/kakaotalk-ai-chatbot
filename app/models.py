from pydantic import BaseModel


class KakaoMsg(BaseModel):
    room_id: int
    room: str
    msg: str
    sender: str
    is_command: bool
