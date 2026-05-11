"""
137문 재질의 스크립트 (5/7 평가용)
- questions_unique.jsonl 각 질문을 /api/chat (논스트리밍)에 전송
- 각 질문마다 독립 세션 생성 (follow-up context 격리)
- 결과를 reports/eval_5_7/responses_new.jsonl 에 저장
"""
import json
import time
import sys
import requests
from pathlib import Path

API = "http://localhost:8000"
HEADERS = {"X-Test-Mode": "1"}  # DB 저장 스킵

BASE_DIR = Path(__file__).parent.parent
QUESTIONS_FILE = BASE_DIR / "reports" / "eval_5_7" / "questions_unique.jsonl"
OUT_FILE = BASE_DIR / "reports" / "eval_5_7" / "responses_new.jsonl"

def create_session():
    r = requests.post(f"{API}/api/session", json={"lang": "ko"}, timeout=10)
    r.raise_for_status()
    return r.json()["session_id"]

def ask(session_id: str, question: str) -> dict:
    params = {"session_id": session_id, "question": question}
    r = requests.post(f"{API}/api/chat", params=params, headers=HEADERS, timeout=120)
    if r.status_code != 200:
        return {"answer": f"[HTTP {r.status_code}] {r.text[:200]}", "intent": "ERROR", "duration_ms": 0}
    return r.json()

def main():
    questions = []
    with open(QUESTIONS_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                questions.append(json.loads(line))

    total = len(questions)
    print(f"총 {total}문 재질의 시작...", flush=True)

    # 이미 처리된 idx 확인 (재시작 지원)
    done_idxs = set()
    if OUT_FILE.exists():
        with open(OUT_FILE, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        done_idxs.add(json.loads(line)["idx"])
                    except Exception:
                        pass
        print(f"  → 이미 완료: {len(done_idxs)}문, 나머지 {total - len(done_idxs)}문 진행")

    out = open(OUT_FILE, "a", encoding="utf-8")
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

            elapsed = int((time.monotonic() - t0) * 1000)
            record = {
                **q,
                "new_answer": data.get("answer", ""),
                "new_intent": data.get("intent", ""),
                "new_duration_ms": data.get("duration_ms", elapsed),
                "http_status": 200 if "answer" in data else -1,
            }
            out.write(json.dumps(record, ensure_ascii=False) + "\n")
            out.flush()
            print(f"[{idx+1:3d}/{total}] old={q.get('old_intent','?'):20s} → new={data.get('intent','?'):20s}  {elapsed}ms", flush=True)
    finally:
        out.close()

    print(f"\n완료! → {OUT_FILE}", flush=True)

if __name__ == "__main__":
    main()
