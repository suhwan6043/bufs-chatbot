"""137문 재질의 — H100 환경 (gemma4:26b 답변, gemma3:4b 분류 시도, SSH 터널 11434).

어제 측정(로컬 CPU + gemma4:e4b 답변)과 객관적 LLM 성능 비교용. 동일한
backend·retrieval·merging 파이프라인 위에서 답변 LLM만 차이.

출력: reports/eval_5_7/responses_h100.jsonl (resumable)
"""
from __future__ import annotations

import json
import time
import sys
from pathlib import Path

import requests

API = "http://localhost:8000"
HEADERS = {"X-Test-Mode": "1"}
TIMEOUT_S = 240  # H100 분류 LLM 폴백+답변 합산 최대 마진

BASE_DIR = Path(__file__).parent.parent
QUESTIONS_FILE = BASE_DIR / "reports" / "eval_5_7" / "questions_unique.jsonl"
OUT_FILE = BASE_DIR / "reports" / "eval_5_7" / "responses_h100.jsonl"


def create_session() -> str:
    r = requests.post(f"{API}/api/session", json={"lang": "ko"}, timeout=10)
    r.raise_for_status()
    return r.json()["session_id"]


def ask(session_id: str, question: str) -> dict:
    params = {"session_id": session_id, "question": question}
    r = requests.post(f"{API}/api/chat", params=params, headers=HEADERS, timeout=TIMEOUT_S)
    if r.status_code != 200:
        return {
            "answer": f"[HTTP {r.status_code}] {r.text[:200]}",
            "intent": "ERROR",
            "duration_ms": 0,
        }
    return r.json()


def main() -> int:
    questions: list[dict] = []
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))
    total = len(questions)
    print(f"총 {total}문 H100 재측정 시작...", flush=True)

    done_idxs: set[int] = set()
    if OUT_FILE.exists():
        with open(OUT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        done_idxs.add(json.loads(line)["idx"])
                    except Exception:
                        pass
        print(f"  → 이미 완료: {len(done_idxs)}문, 나머지 {total - len(done_idxs)}문", flush=True)

    out = open(OUT_FILE, "a", encoding="utf-8")
    t_start = time.monotonic()
    fail = 0

    try:
        for q in questions:
            idx = q["idx"]
            if idx in done_idxs:
                continue
            question = q["question"]
            t0 = time.monotonic()
            try:
                sid = create_session()
                data = ask(sid, question)
            except Exception as e:
                data = {"answer": f"[ERROR] {e}", "intent": "ERROR", "duration_ms": 0}
                fail += 1
            elapsed = int((time.monotonic() - t0) * 1000)
            record = {
                **q,
                "h100_answer": data.get("answer", ""),
                "h100_intent": data.get("intent", ""),
                "h100_source_urls": data.get("source_urls", []),
                "h100_duration_ms": data.get("duration_ms", elapsed),
                "http_status": 200 if data.get("intent") != "ERROR" else 0,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            cum = int(time.monotonic() - t_start)
            print(
                f"[{idx+1:3d}/{total}] {elapsed:6d}ms "
                f"old={q.get('old_intent','?'):16s} y={q.get('old_answer','')[:0]} "
                f"h100={data.get('intent','?'):24s} cum={cum}s",
                flush=True,
            )
    finally:
        out.close()

    total_s = int(time.monotonic() - t_start)
    print(f"\n완료. 총 {total_s}s, fail={fail}, 출력: {OUT_FILE}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
