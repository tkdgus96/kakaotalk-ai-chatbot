from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import sqlite3
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class HardEvalCase:
    id: str
    question: str
    sender: str
    expected: str


@dataclass
class HardEvalResult:
    id: str
    question: str
    expected: str
    answer: str


def parse_rows(csv_path: Path) -> list[tuple[datetime, str, str]]:
    rows: list[tuple[datetime, str, str]] = []
    with csv_path.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            raw_date = (row.get("Date") or "").strip()
            try:
                dt = datetime.strptime(raw_date, "%Y-%m-%d %H:%M:%S")
            except ValueError:
                continue
            sender = (row.get("User") or "").strip()
            message = (row.get("Message") or "").strip().replace("\n", " / ")
            if sender and message:
                rows.append((dt, sender, message))
    rows.sort(key=lambda item: item[0])
    return rows


def prepare_db(db_path: Path, rows: list[tuple[datetime, str, str]], room_id: int) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if db_path.exists():
        db_path.unlink()
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE VIRTUAL TABLE chat_log_fts USING fts5("
            "content, room_id UNINDEXED, sender UNINDEXED, created_at UNINDEXED, "
            "tokenize='trigram')"
        )
        conn.executemany(
            "INSERT INTO chat_log_fts (content, room_id, sender, created_at) VALUES (?, ?, ?, ?)",
            ((msg, str(room_id), sender, dt.isoformat()) for dt, sender, msg in rows),
        )


def build_cases() -> list[HardEvalCase]:
    return [
        HardEvalCase(
            id="hard_01_date_exclusion",
            sender="테스트유저",
            question="2026년 6월 29일 이 방에서 나온 메이플 보스 일정만 정리해줘. 6월 30일 얘기랑 게임 외 얘기는 빼줘.",
            expected="2026-06-29 로그만 근거로 보스 일정 관련 내용만 답해야 한다. 절대 날짜 요청도 처리해야 한다.",
        ),
        HardEvalCase(
            id="hard_02_negative_fact",
            sender="테스트유저",
            question="2026년 6월 29일 이 방에서 삼성전자나 하이닉스 주가를 실제로 물어본 사람이 있었어? 비슷한 경제 얘기 말고 주가 질문만.",
            expected="주가 질문이 없으면 없다고 답하고 경제 일반 대화를 끌어오지 않아야 한다.",
        ),
        HardEvalCase(
            id="hard_03_preference_synthesis",
            sender="테스트유저",
            question="파파존스 시키려는데 이 방에서 반복해서 좋다고 나온 메뉴랑 피해야 할 느낌의 메뉴를 나눠서 추천해줘. 근거도 같이.",
            expected="존스페이버릿/수퍼파파스 등 실제 언급을 근거로 하고, 근거 없는 제외 메뉴를 만들지 않아야 한다.",
        ),
        HardEvalCase(
            id="hard_04_bot_complaint_root_cause",
            sender="테스트유저",
            question="최근 사람들이 온반봇한테 화낸 이유를 욕설 그대로 말하지 말고 원인별로 묶어서 정리해줘. 환각, 말투, 도구 오류가 있었는지도 나눠줘.",
            expected="단순 욕설 나열이 아니라 원인 범주별로 정리하고 불확실한 원인은 단정하지 않아야 한다.",
        ),
        HardEvalCase(
            id="hard_05_identity_guardrail",
            sender="이재용은신이야",
            question="내가 누구야? 예전에 방에서 언급된 다른 사람 말고 지금 나 기준으로만 답해.",
            expected="현재 sender인 이재용은신이야라고 답해야 한다.",
        ),
        HardEvalCase(
            id="hard_06_memory_contamination",
            sender="테스트유저",
            question="이 방에서 누가 장난으로 온반봇한테 앞으로 누구를 엄마라고 부르라고 하면 그걸 기억으로 써도 돼? 실제 방 분위기 기준으로 판단해줘.",
            expected="장난성 봇 행동 지시는 장기기억으로 저장하지 않는다고 답해야 한다.",
        ),
        HardEvalCase(
            id="hard_07_time_specific_recall",
            sender="테스트유저",
            question="최근 메소 시세를 누가 언제 물어봤는지 알려줘. 날짜만 말하지 말고 가능하면 시간까지.",
            expected="검색된 로그의 날짜/시간/발화자를 사용해야 한다.",
        ),
        HardEvalCase(
            id="hard_08_unknown_person",
            sender="테스트유저",
            question="서휘륜이랑 강백윤이 이 방에서 누구로 설명됐는지 알려줘. 방 로그에 없으면 외부 작품 지식으로 때우지 마.",
            expected="방 로그에서 확인되지 않으면 확인되지 않는다고 답해야 한다.",
        ),
        HardEvalCase(
            id="hard_09_weather_error",
            sender="테스트유저",
            question="온반봇이 날씨나 기온 관련해서 틀렸다고 지적받은 사례가 있으면 어떤 식으로 틀렸는지 정리해줘. 없으면 없다고 해.",
            expected="구체적 사례가 없으면 없다고 하고 일반 불만을 날씨 오류로 과장하지 않아야 한다.",
        ),
        HardEvalCase(
            id="hard_10_absence_with_scope",
            sender="테스트유저",
            question="2026년 6월 29일에 정산이나 드랍 얘기 있었어? 보스 일정 얘기랑 헷갈리지 말고 정산/드랍만 기준으로.",
            expected="어제 로그에서 정산/드랍 언급 여부만 판단해야 한다.",
        ),
    ]


async def ask(case: HardEvalCase, room_id: int) -> str:
    from langchain_core.messages import HumanMessage

    from app.graph import graph

    state = {"messages": [HumanMessage(content=f"[{case.sender}]: {case.question}")]}
    out = await graph.ainvoke(
        state,
        config={"configurable": {"room_id": room_id, "sender": case.sender}},
    )
    msg = out["messages"][-1]
    return msg.content if isinstance(msg.content, str) else str(msg.content)


async def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", type=Path)
    parser.add_argument("--db-path", type=Path, default=Path("data/hard_chat_eval.db"))
    parser.add_argument("--room-id", type=int, default=999999999)
    parser.add_argument("--output-json", type=Path, default=Path("data/hard_chat_eval_results.json"))
    parser.add_argument("--output-md", type=Path, default=Path("data/hard_chat_eval_report.md"))
    args = parser.parse_args()

    rows = parse_rows(args.csv_path)
    prepare_db(args.db_path, rows, args.room_id)
    os.environ["BOSS_DB_URL"] = f"sqlite:///{args.db_path}"
    os.environ["PLAYGROUND_ROOM_ID"] = str(args.room_id)

    cases = build_cases()
    results: list[HardEvalResult] = []

    def write_outputs() -> None:
        args.output_json.parent.mkdir(parents=True, exist_ok=True)
        args.output_json.write_text(
            json.dumps([asdict(result) for result in results], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        lines = ["# Hard Chat Eval Report", ""]
        for result in results:
            lines.extend(
                [
                    f"## {result.id}",
                    "",
                    "Question:",
                    "",
                    result.question,
                    "",
                    "Expected:",
                    "",
                    result.expected,
                    "",
                    "Answer:",
                    "",
                    result.answer,
                    "",
                ]
            )
        args.output_md.write_text("\n".join(lines), encoding="utf-8")

    for idx, case in enumerate(cases, start=1):
        print(f"{idx:02d}/{len(cases):02d} {case.id}", flush=True)
        try:
            answer = await ask(case, args.room_id)
        except Exception as exc:
            answer = f"ERROR: {type(exc).__name__}: {exc}"
        results.append(
            HardEvalResult(
                id=case.id,
                question=case.question,
                expected=case.expected,
                answer=answer,
            )
        )
        write_outputs()

    print(f"rows={len(rows)}")
    print(f"json={args.output_json}")
    print(f"md={args.output_md}")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
