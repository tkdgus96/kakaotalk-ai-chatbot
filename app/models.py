from pydantic import BaseModel
from typing import Literal


class KakaoMsg(BaseModel):
    room_id: int
    room: str
    msg: str
    sender: str
    is_command: bool
    # KakaoTalk 답장(reply): the message the user is pointing at, if any.
    quoted_text: str | None = None
    quoted_sender: str | None = None
    # Stable KakaoTalk user_id of the sender (Iris path only).
    sender_id: int | None = None
    # KakaoTalk's own send timestamp (KST ISO, naive) — more accurate than receive time.
    sent_at: str | None = None
    # Display names of members @mentioned in the message (resolved via room_member).
    mentioned_names: list[str] = []
    # text | photo | video | audio | file | location | contact | schedule | emoticon | ...
    msg_kind: str = "text"


class OutboxAckRequest(BaseModel):
    status: Literal["SENT", "FAILED"]
