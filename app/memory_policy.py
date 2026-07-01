from __future__ import annotations

import re
from dataclasses import dataclass

ALLOWED_FACT_TYPES = {"선호", "비선호", "제약", "신상", "일정", "기타"}

_BOT_DIRECTIVE_PATTERNS = (
    r"앞으로",
    r"기억해",
    r"저장해",
    r"대답하지\s*마",
    r"답변.*붙",
    r"말투",
    r"~처럼\s*대화",
    r"엄마라고\s*(해|부르)",
    r"창조주",
)

_THIRD_PERSON_MARKERS = (
    "는 ",
    "은 ",
    "이 ",
    "가 ",
    "님은",
    "님이",
    "씨는",
    "씨가",
)

_NEGATIVE_OR_JOKE_WORDS = (
    "바보",
    "멍청",
    "병신",
    "새끼",
    "씹덕",
    "분노조절",
    "탈주",
    "거짓말",
    "구라",
    "쓰레기",
    "버러지",
    "깡통",
    "못하는",
    "못함",
    "화가",
)

_EPHEMERAL_WORDS = (
    "오늘",
    "지금",
    "방금",
    "이번",
    "이따",
    "내일",
    "어제",
    "요즘",
)

_SELF_MARKERS = (
    "나는",
    "난",
    "제가",
    "저는",
    "내가",
    "전",
    "나 ",
    "내 ",
)


@dataclass(frozen=True)
class MemoryPolicyResult:
    allowed: bool
    reason: str = ""


def validate_memory_fact(
    fact: str,
    sender: str,
    fact_type: str = "기타",
    source_text: str | None = None,
) -> MemoryPolicyResult:
    """Deterministic guardrail before anything is written to long-term memory.

    The LLM extractor is useful for recall, but group chats contain jokes,
    prompt-injection attempts, and third-party gossip. This policy keeps memory
    conservative: self-stated stable facts only, with explicit blocks for bot
    directives and negative claims about other people.
    """
    text = _normalize(fact)
    source = _normalize(source_text or fact)
    if not text:
        return MemoryPolicyResult(False, "empty")
    if fact_type not in ALLOWED_FACT_TYPES:
        return MemoryPolicyResult(False, "unsupported_fact_type")
    if _looks_like_bot_directive(source) or _looks_like_bot_directive(text):
        return MemoryPolicyResult(False, "bot_directive")
    if _contains_negative_or_joke_claim(text) or _contains_negative_or_joke_claim(source):
        return MemoryPolicyResult(False, "negative_or_joke_claim")
    if _is_ephemeral(text):
        return MemoryPolicyResult(False, "ephemeral")
    if _looks_like_third_person_claim(text, sender) and not _looks_like_self_statement(source):
        return MemoryPolicyResult(False, "third_person_claim")
    return MemoryPolicyResult(True)


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", (value or "").strip())


def _looks_like_bot_directive(text: str) -> bool:
    return any(re.search(pattern, text) for pattern in _BOT_DIRECTIVE_PATTERNS)


def _contains_negative_or_joke_claim(text: str) -> bool:
    return any(word in text for word in _NEGATIVE_OR_JOKE_WORDS)


def _is_ephemeral(text: str) -> bool:
    if any(word in text for word in _EPHEMERAL_WORDS):
        stable_hint = any(word in text for word in ("매주", "매일", "항상", "반복", "알레르기", "못 먹"))
        return not stable_hint
    return False


def _looks_like_self_statement(text: str) -> bool:
    return any(marker in text for marker in _SELF_MARKERS)


def _looks_like_third_person_claim(text: str, sender: str) -> bool:
    if sender and sender in text:
        return False
    return any(marker in text for marker in _THIRD_PERSON_MARKERS)
