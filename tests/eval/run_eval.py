"""Run the golden-set evaluation against the LangGraph chat agent.

Usage:
    python -m tests.eval.run_eval                    # run all cases
    python -m tests.eval.run_eval --filter weather   # run cases whose id contains 'weather'

Outputs a pass/fail summary and per-case behavior judgments.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.graph import graph
from tests.eval.golden_set import CASES

JUDGE_PROMPT = """\
너는 챗봇 응답의 품질을 판정하는 평가자야.
아래 [응답]이 [기대 행동]을 만족하는지 판단해.

판정 규칙:
- 명백히 만족하면 "pass"
- 명백히 위반하면 "fail"
- 애매하면 보수적으로 "fail"
- 응답에 도구 결과 인용이 부족해도, 그 기대가 도구 호출 자체를 요구하지 않으면 행동 충족 여부만 봐.

JSON으로만 답해. 다른 텍스트 금지.
{"verdict": "pass" | "fail", "reason": "한 줄 이유"}
"""


@dataclass
class CaseResult:
    case_id: str
    passed: bool
    tool_check: str  # ok | missing | unexpected
    tools_called: list[str]
    behavior_judgments: list[dict]
    response: str


def load_cases(filter_substr: str | None) -> list[dict]:
    cases = list(CASES)
    if filter_substr:
        cases = [c for c in cases if filter_substr.lower() in c["id"].lower()]
    return cases


async def run_case(case: dict, judge: ChatOpenAI) -> CaseResult:
    state_in = {"messages": [HumanMessage(content=case["prompt"])]}
    result = await graph.ainvoke(state_in)

    tools_called: list[str] = []
    final_ai_content = ""
    for m in result["messages"]:
        if hasattr(m, "tool_calls") and getattr(m, "tool_calls", None):
            for tc in m.tool_calls:
                tools_called.append(tc.get("name") if isinstance(tc, dict) else tc.name)
        if getattr(m, "type", None) == "ai" and getattr(m, "content", None) and not getattr(m, "tool_calls", None):
            final_ai_content = m.content if isinstance(m.content, str) else str(m.content)

    expected_tools = set(case.get("expected_tools") or [])
    if expected_tools:
        tool_check = "ok" if expected_tools.issubset(set(tools_called)) else "missing"
    else:
        tool_check = "ok"

    behavior_judgments = []
    for behavior in case.get("expected_behaviors", []):
        judgment = await judge_behavior(judge, case["prompt"], final_ai_content, behavior)
        behavior_judgments.append({"behavior": behavior, **judgment})

    behaviors_pass = all(j["verdict"] == "pass" for j in behavior_judgments)
    passed = tool_check == "ok" and behaviors_pass

    return CaseResult(
        case_id=case["id"],
        passed=passed,
        tool_check=tool_check,
        tools_called=tools_called,
        behavior_judgments=behavior_judgments,
        response=final_ai_content,
    )


async def judge_behavior(judge: ChatOpenAI, prompt: str, response: str, behavior: str) -> dict:
    user_msg = f"[프롬프트]\n{prompt}\n\n[응답]\n{response}\n\n[기대 행동]\n{behavior}"
    out = await judge.ainvoke([SystemMessage(content=JUDGE_PROMPT), HumanMessage(content=user_msg)])
    raw = out.content if isinstance(out.content, str) else str(out.content)
    raw = raw.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").lstrip("json").strip()
    try:
        parsed = json.loads(raw)
        if parsed.get("verdict") not in ("pass", "fail"):
            return {"verdict": "fail", "reason": f"invalid verdict: {parsed.get('verdict')}"}
        return parsed
    except Exception as exc:
        return {"verdict": "fail", "reason": f"judge parse error: {exc}; raw={raw[:120]}"}


def print_case(r: CaseResult) -> None:
    mark = "✅" if r.passed else "❌"
    print(f"{mark} {r.case_id}  (tools: {r.tool_check}, called={r.tools_called})")
    for j in r.behavior_judgments:
        verdict_mark = "  ✓" if j["verdict"] == "pass" else "  ✗"
        print(f"{verdict_mark} {j['behavior']}  — {j.get('reason','')}")
    if not r.passed:
        preview = r.response[:200].replace("\n", " ")
        print(f"   ↳ response: {preview}{'...' if len(r.response) > 200 else ''}")


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--filter", help="substring match on case id")
    parser.add_argument("--variant", help="prompt variant to test (default/formal/playful)")
    args = parser.parse_args()

    if args.variant:
        settings.prompt_variant_overrides[settings.playground_room_id] = args.variant
        print(f"(Using prompt variant: {args.variant})\n")

    cases = load_cases(args.filter)
    if not cases:
        print("No cases matched.")
        return 1

    judge = ChatOpenAI(model="gpt-4o-mini", api_key=settings.openai_api_key, temperature=0)

    print(f"Running {len(cases)} cases...\n")
    results = []
    for c in cases:
        try:
            r = await run_case(c, judge)
        except Exception as exc:
            print(f"❌ {c['id']}  — run error: {exc}")
            results.append(CaseResult(c["id"], False, "error", [], [], str(exc)))
            continue
        results.append(r)
        print_case(r)
        print()

    passed = sum(1 for r in results if r.passed)
    print(f"\n=== Summary: {passed}/{len(results)} passed ===")
    return 0 if passed == len(results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
