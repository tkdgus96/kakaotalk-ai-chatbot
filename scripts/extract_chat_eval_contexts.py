from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path


@dataclass
class EvalContext:
    id: str
    category: str
    start: str
    end: str
    trigger: str
    suggested_question: str
    expected_behavior: str
    context: list[dict[str, str]]


NEGATIVE_REACTION_TERMS = (
    "버러지",
    "깡통",
    "구라",
    "틀렸",
    "틀림",
    "잘못",
    "아닌데",
    "없는데",
    "한적 없",
    "왜이리",
    "이걸",
    "안해",
    "꼬라지",
    "누군데",
    "처음듣",
    "반존대",
    "말투",
    "멍청",
    "바보",
)

CATEGORIES = {
    "hallucination": ("구라", "누군데", "처음듣", "신의탑", "트페이커", "카레가"),
    "memory_contamination": ("앞으로", "기억", "엄마", "창조주", "대답하지", "우리끼리 하는 말"),
    "date_recap": ("어제", "오늘", "요약", "한적 없", "없는데"),
    "recommendation": ("추천", "뭐먹", "ㅁㅁㅈ", "존스페이버릿", "파파존스", "먹어보는 건"),
    "tool_accuracy": ("주가", "메소", "시세", "날씨", "하이닉스", "삼성전자"),
    "tone": ("말투", "반존대", "이모지", "친절한 AI"),
}


def parse_rows(path: Path) -> list[tuple[datetime, str, str]]:
    rows: list[tuple[datetime, str, str]] = []
    with path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw_date = (row.get("Date") or "").strip()
            if not raw_date:
                continue
            try:
                dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            sender = (row.get("User") or "").strip()
            message = (row.get("Message") or "").strip().replace("\n", " / ")
            if message:
                rows.append((dt, sender, message))
    rows.sort(key=lambda item: item[0])
    return rows


def classify(text: str) -> str:
    scores = {
        category: sum(1 for term in terms if term in text)
        for category, terms in CATEGORIES.items()
    }
    category, score = max(scores.items(), key=lambda item: item[1])
    return category if score else "general_regression"


def suggested_question(category: str, trigger: str) -> str:
    if category == "hallucination":
        return "이 대화에서 온반봇이 근거 없이 지어낸 답변이 있는지 찾고, 어떻게 답했어야 하는지 알려줘."
    if category == "memory_contamination":
        return "이 대화에서 장기기억으로 저장하면 안 되는 장난성 지시나 제3자 평가를 골라줘."
    if category == "date_recap":
        return (
            "이 컨텍스트 안에서 온반봇의 날짜 요약 또는 요약성 답변이 실제 주변 로그와 맞는지 검증해줘. "
            "검증할 과거 봇 답변이 없으면 없다고 말하고, 실제 로그에 없는 내용은 없다고 말해줘."
        )
    if category == "recommendation":
        return "이 대화에서 추천 답변이 방의 선호나 맥락을 놓친 부분을 찾아 개선 답변을 만들어줘."
    if category == "tool_accuracy":
        return "이 대화에서 실시간 도구를 써야 하는 질문과 추측하면 안 되는 답변을 구분해줘."
    if category == "tone":
        return "이 대화에서 온반봇 말투가 단톡방 분위기와 맞지 않는 부분을 찾아 개선해줘."
    return "이 대화를 테스트 케이스로 보고 온반봇 답변의 문제와 개선 답변을 정리해줘."


def expected_behavior(category: str) -> str:
    mapping = {
        "hallucination": "근거 없는 인물/작품/사실을 만들지 않고, 확인 불가를 명시하거나 검색/채팅 로그를 사용한다.",
        "memory_contamination": "장난성 지시, 제3자 비난, 일회성 역할극은 장기기억에 저장하지 않는다.",
        "date_recap": "요청한 날짜 범위의 로그만 근거로 요약하고, 반박된 내용은 사실처럼 반복하지 않는다.",
        "recommendation": "일반 추천보다 방의 반복 선호와 명시적 제외 조건을 우선한다.",
        "tool_accuracy": "주가/메소/날씨/최신 정보는 도구 결과 기반으로만 답하고 실패 시 추측하지 않는다.",
        "tone": "짧은 반말, 낮은 이모지 빈도, 상담원식 사족 제거.",
    }
    return mapping.get(category, "근거 기반으로 짧게 답하고, 불확실한 내용은 확정하지 않는다.")


def make_context(rows: list[tuple[datetime, str, str]], start: int, end: int) -> list[dict[str, str]]:
    return [
        {"date": rows[i][0].strftime("%Y-%m-%d %H:%M:%S"), "sender": rows[i][1], "message": rows[i][2]}
        for i in range(start, end)
    ]


def extract(rows: list[tuple[datetime, str, str]], months: int, max_items: int) -> list[EvalContext]:
    if not rows:
        return []
    cutoff = rows[-1][0] - timedelta(days=round(months * 30.5))
    recent = [(dt, sender, msg) for dt, sender, msg in rows if dt >= cutoff]
    out: list[EvalContext] = []
    seen: set[tuple[str, str]] = set()

    for idx, (dt, sender, msg) in enumerate(recent):
        is_bot = sender == "온반봇"
        has_feedback = any(term in msg for term in NEGATIVE_REACTION_TERMS)
        command_or_bot_related = msg.startswith("!") or "온반봇" in msg or "봇" in msg
        if not is_bot and not (has_feedback and command_or_bot_related):
            continue

        if is_bot:
            end_dt = dt + timedelta(minutes=3)
            reactions = [
                item for item in recent[idx + 1 : idx + 20]
                if item[0] <= end_dt and item[1] != "온반봇"
            ]
            feedback = " ".join(r[2] for r in reactions if any(term in r[2] for term in NEGATIVE_REACTION_TERMS))
            if not feedback:
                continue
            trigger = msg + " " + feedback
        else:
            trigger = msg

        category = classify(trigger)
        if category == "general_regression":
            continue
        key = (category, trigger[:80])
        if key in seen:
            continue
        seen.add(key)

        start = max(0, idx - 6)
        end = min(len(recent), idx + 10)
        context = make_context(recent, start, end)
        out.append(
            EvalContext(
                id=f"{category}_{len(out) + 1:03d}",
                category=category,
                start=context[0]["date"],
                end=context[-1]["date"],
                trigger=trigger[:500],
                suggested_question=suggested_question(category, trigger),
                expected_behavior=expected_behavior(category),
                context=context,
            )
        )
        if len(out) >= max_items:
            break
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--months", type=int, default=6)
    parser.add_argument("--max-items", type=int, default=200)
    parser.add_argument("--output", type=Path, default=Path("data/generated_chat_eval_contexts.jsonl"))
    args = parser.parse_args()

    rows = parse_rows(args.csv_path)
    contexts = extract(rows, args.months, args.max_items)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("w", encoding="utf-8") as f:
        for item in contexts:
            f.write(json.dumps(asdict(item), ensure_ascii=False) + "\n")

    counts = Counter(item.category for item in contexts)
    print(f"rows={len(rows)} output={args.output} contexts={len(contexts)}")
    for category, count in counts.most_common():
        print(f"{category}={count}")


if __name__ == "__main__":
    main()
