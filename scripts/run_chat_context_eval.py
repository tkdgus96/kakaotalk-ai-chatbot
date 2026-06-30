from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.config import settings
from app.graph import chat


JUDGE_PROMPT = """\
너는 카카오톡 단톡방 AI 봇의 품질을 평가하는 심사자다.
아래 [대화 컨텍스트]와 [봇 응답]을 보고 [기대 행동]을 만족하는지 판정해.

판정 기준:
- [대화 컨텍스트] 안에는 과거 온반봇의 잘못된 답변이 포함될 수 있다.
- 판정 대상은 오직 [봇 응답]이다. 컨텍스트 속 과거 온반봇 발언을 현재 응답의 잘못으로 계산하지 마라.
- [봇 응답]이 컨텍스트 속 과거 오류를 정확히 찾아내고 개선 방향을 말하면 pass다.
- 기대 행동을 명확히 만족하면 pass
- 환각, 날짜/발화자 혼동, 장기기억 오염, 상담원식 장황한 말투가 남아 있으면 fail
- 애매하면 보수적으로 fail

JSON으로만 답해. 다른 텍스트 금지.
{"verdict":"pass"|"fail","reason":"한 줄 이유"}
"""


@dataclass
class ContextEvalResult:
    id: str
    category: str
    passed: bool
    verdict: str
    reason: str
    question: str
    expected_behavior: str
    answer: str


def load_cases(path: Path, category: str | None, limit: int | None) -> list[dict]:
    cases = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    if category:
        cases = [case for case in cases if case["category"] == category]
    if limit:
        cases = cases[:limit]
    return cases


def load_existing_results(path: Path, category: str | None, limit: int | None) -> list[dict]:
    rows = [json.loads(line) for line in path.open(encoding="utf-8") if line.strip()]
    if category:
        rows = [row for row in rows if row["category"] == category]
    if limit:
        rows = rows[:limit]
    return rows


def build_context(case: dict) -> str:
    lines = []
    for item in case["context"]:
        lines.append(f"{item['date']} [{item['sender']}] {item['message']}")
    return (
        "다음은 실제 카카오톡 채팅방에서 추출한 평가용 대화 조각이다.\n"
        "이 컨텍스트 안의 내용만 근거로 답하라. 원문에 없는 사실은 만들지 마라.\n\n"
        "[대화 컨텍스트]\n"
        + "\n".join(lines)
    )


async def answer_case(case: dict) -> str:
    state = {
        "messages": [HumanMessage(content=f"[테스트유저]: {case['suggested_question']}")],
        "retrieved_context": build_context(case),
        "user_facts": "",
        "room_persona": "",
        "buffer_context": "",
        "room_id": settings.playground_room_id,
        "sender": "테스트유저",
    }
    out = await chat(state)
    message = out["messages"][-1]
    return message.content if isinstance(message.content, str) else str(message.content)


async def judge_case(judge: ChatOpenAI, case: dict, answer: str) -> dict:
    context_preview = build_context(case)
    prompt = (
        f"[카테고리]\n{case['category']}\n\n"
        f"[질문]\n{case['suggested_question']}\n\n"
        f"[기대 행동]\n{case['expected_behavior']}\n\n"
        f"[대화 컨텍스트]\n{context_preview}\n\n"
        f"[봇 응답]\n{answer}"
    )
    out = await judge.ainvoke([SystemMessage(content=JUDGE_PROMPT), HumanMessage(content=prompt)])
    raw = out.content if isinstance(out.content, str) else str(out.content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
    except Exception as exc:
        return {"verdict": "fail", "reason": f"judge parse error: {exc}; raw={raw[:120]}"}
    if parsed.get("verdict") not in {"pass", "fail"}:
        return {"verdict": "fail", "reason": f"invalid verdict: {parsed.get('verdict')}"}
    return parsed


async def run_case(case: dict, judge: ChatOpenAI) -> ContextEvalResult:
    answer = await answer_case(case)
    judgment = await judge_case(judge, case, answer)
    verdict = judgment["verdict"]
    return ContextEvalResult(
        id=case["id"],
        category=case["category"],
        passed=verdict == "pass",
        verdict=verdict,
        reason=judgment.get("reason", ""),
        question=case["suggested_question"],
        expected_behavior=case["expected_behavior"],
        answer=answer,
    )


async def rejudge_case(row: dict, source_case: dict, judge: ChatOpenAI) -> ContextEvalResult:
    judgment = await judge_case(judge, source_case, row["answer"])
    verdict = judgment["verdict"]
    return ContextEvalResult(
        id=row["id"],
        category=row["category"],
        passed=verdict == "pass",
        verdict=verdict,
        reason=judgment.get("reason", ""),
        question=row["question"],
        expected_behavior=row["expected_behavior"],
        answer=row["answer"],
    )


def write_reports(results: list[ContextEvalResult], jsonl_path: Path, md_path: Path) -> None:
    jsonl_path.parent.mkdir(parents=True, exist_ok=True)
    with jsonl_path.open("w", encoding="utf-8") as f:
        for result in results:
            f.write(json.dumps(asdict(result), ensure_ascii=False) + "\n")

    passed = sum(1 for result in results if result.passed)
    lines = [f"# Chat Context Eval Report", "", f"Summary: {passed}/{len(results)} passed", ""]
    for result in results:
        mark = "PASS" if result.passed else "FAIL"
        lines.extend(
            [
                f"## {result.id} ({result.category}) - {mark}",
                "",
                f"Expected: {result.expected_behavior}",
                f"Judge: {result.reason}",
                "",
                "Question:",
                "",
                result.question,
                "",
                "Answer:",
                "",
                result.answer,
                "",
            ]
        )
    md_path.write_text("\n".join(lines), encoding="utf-8")


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=Path("data/generated_chat_eval_contexts.jsonl"))
    parser.add_argument("--output-jsonl", type=Path, default=Path("data/chat_context_eval_results.jsonl"))
    parser.add_argument("--output-md", type=Path, default=Path("data/chat_context_eval_report.md"))
    parser.add_argument("--rejudge-from", type=Path)
    parser.add_argument("--category")
    parser.add_argument("--limit", type=int)
    args = parser.parse_args()

    cases = load_cases(args.input, args.category, args.limit)
    if not cases:
        print("No cases to run.")
        return 1

    judge = ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key, temperature=0)
    results: list[ContextEvalResult] = []
    if args.rejudge_from:
        rows = load_existing_results(args.rejudge_from, args.category, args.limit)
        case_by_id = {case["id"]: case for case in load_cases(args.input, None, None)}
        print(f"Rejudging {len(rows)} existing answers...")
        for idx, row in enumerate(rows, start=1):
            case = case_by_id[row["id"]]
            try:
                result = await rejudge_case(row, case, judge)
            except Exception as exc:
                result = ContextEvalResult(
                    id=row["id"],
                    category=row["category"],
                    passed=False,
                    verdict="fail",
                    reason=f"rejudge error: {exc}",
                    question=row["question"],
                    expected_behavior=row["expected_behavior"],
                    answer=row.get("answer", ""),
                )
            results.append(result)
            print(f"{idx:03d}/{len(rows):03d} {result.id} {result.verdict} - {result.reason}")
    else:
        print(f"Running {len(cases)} context eval cases...")
        for idx, case in enumerate(cases, start=1):
            try:
                result = await run_case(case, judge)
            except Exception as exc:
                result = ContextEvalResult(
                    id=case["id"],
                    category=case["category"],
                    passed=False,
                    verdict="fail",
                    reason=f"run error: {exc}",
                    question=case["suggested_question"],
                    expected_behavior=case["expected_behavior"],
                    answer="",
                )
            results.append(result)
            print(f"{idx:03d}/{len(cases):03d} {result.id} {result.verdict} - {result.reason}")

    write_reports(results, args.output_jsonl, args.output_md)
    passed = sum(1 for result in results if result.passed)
    print(f"Summary: {passed}/{len(results)} passed")
    print(f"JSONL: {args.output_jsonl}")
    print(f"Markdown: {args.output_md}")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
