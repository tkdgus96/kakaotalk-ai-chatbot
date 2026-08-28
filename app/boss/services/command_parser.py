from dataclasses import dataclass
import re
import unicodedata


@dataclass
class ParsedCommand:
    name: str
    args: list[str]


KNOWN_COMMANDS = {
    "!보스매주",
    "!보스해제",
    "!보스시간",
    "!이번주보스",
    "!드랍",
    "!정산",
    "!정산완료",
    "!정산목록",
    "!보스도움",
    "!보스도움말",
    "!보스help",
    "!bosshelp",
    "!매일",
    "!매일목록",
    "!매일해제",
    "!매일도움",
    "!그림",
    "!이미지",
    "!짤",
    "!기억",
    "!기억삭제",
    "!기억끄기",
    "!기억켜기",
    "!기억도움",
    "!도움말",
    "!요약",
    "!요약도움",
    "!기록",
    "!단어기록",
    "!챌린지기록",
    "!게임기록",
    "!랭킹",
    "!내기록",
}


def parse_command(text: str) -> ParsedCommand | None:
    raw = unicodedata.normalize("NFKC", text)
    raw = raw.replace("\u200b", "").replace("\u200c", "").replace("\u200d", "").replace("\ufeff", "")
    raw = raw.strip().replace("！", "!")
    if raw.startswith("\ufeff"):
        raw = raw[1:]
    if raw.startswith("!"):
        raw = re.sub(r"^!\s+", "!", raw)
    else:
        # Some bridges strip the leading "!" before forwarding commands.
        first = raw.split()[0] if raw.split() else ""
        if f"!{first}" in KNOWN_COMMANDS:
            raw = f"!{raw}"
        else:
            return None
    parts = raw.split()
    if not parts:
        return None
    cmd = re.sub(r"[^\w!가-힣]", "", parts[0])
    return ParsedCommand(name=cmd, args=parts[1:])
