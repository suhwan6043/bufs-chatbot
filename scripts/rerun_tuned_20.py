"""KO SYSTEM_PROMPT 4a/4b/4c 튜닝 후 20건 검증 재측정.

타깃:
- refusal_unacc 15건: idx 6, 21, 43, 44, 54, 55, 62, 68, 86, 89, 90, 95, 110, 119, 132
- 회귀 검증 5건 (어제 correct + h100 correct): idx 4, 11, 15, 28, 57

각 idx의 질문은 responses_h100.jsonl 에서 추출 (같은 5/7 원본 질문).

출력: reports/eval_5_7/responses_tuned.jsonl (resumable, X-Test-Mode=1)
"""
from __future__ import annotations
import json, sys, time
from pathlib import Path
import requests

API = "http://localhost:8000"
HEADERS = {"X-Test-Mode": "1"}
TIMEOUT_S = 240

REFUSAL_15 = [6, 21, 43, 44, 54, 55, 62, 68, 86, 89, 90, 95, 110, 119, 132]
REGRESSION_5 = [4, 11, 15, 28, 57]
TARGETS = REFUSAL_15 + REGRESSION_5

BASE = Path(__file__).parent.parent
H100_FILE = BASE / "reports" / "eval_5_7" / "responses_h100.jsonl"
GRADED_FILE = BASE / "reports" / "eval_5_7" / "graded_h100.jsonl"
OUT_FILE = BASE / "reports" / "eval_5_7" / "responses_tuned.jsonl"


def load_concat(path: Path) -> list[dict]:
    txt = path.read_text(encoding="utf-8").strip()
    dec = json.JSONDecoder()
    pos, items = 0, []
    while pos < len(txt):
        while pos < len(txt) and txt[pos] in " \t\n\r":
            pos += 1
        if pos >= len(txt):
            break
        obj, end = dec.raw_decode(txt, pos)
        items.append(obj)
        pos = end
    return items


def main() -> int:
    h_rows = [json.loads(l) for l in H100_FILE.read_text(encoding="utf-8").splitlines() if l.strip()]
    h_by = {r["idx"]: r for r in h_rows}
    g_rows = load_concat(GRADED_FILE)
    g_by = {r["idx"]: r for r in g_rows}

    questions = []
    for tidx in TARGETS:
        if tidx not in h_by:
            print(f"WARN: idx {tidx} not in h100", flush=True)
            continue
        r = h_by[tidx]
        questions.append({
            "idx": tidx,
            "question": r["question"],
            "h100_old_intent": r.get("h100_intent"),
            "h100_old_answer": r.get("h100_answer", ""),
            "h100_old_duration_ms": r.get("h100_duration_ms"),
            "yesterday_verdict": g_by.get(tidx, {}).get("verdict"),
            "yesterday_gt": g_by.get(tidx, {}).get("ground_truth", "")[:500],
            "yesterday_reason": g_by.get(tidx, {}).get("reason", ""),
            "_target_class": "refusal_15" if tidx in REFUSAL_15 else "regression_5",
        })

    print(f"총 {len(questions)}건 재측정 시작 (4a/4b/4c 튜닝 후)", flush=True)

    done = set()
    if OUT_FILE.exists():
        for line in OUT_FILE.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    done.add(json.loads(line)["idx"])
                except Exception:
                    pass
        print(f"  → 이미 완료: {len(done)}", flush=True)

    out = OUT_FILE.open("a", encoding="utf-8")
    t_start = time.monotonic()
    fail = 0

    for q in questions:
        idx = q["idx"]
        if idx in done:
            continue
        t0 = time.monotonic()
        try:
            sess = requests.post(f"{API}/api/session", json={}, timeout=10).json()["session_id"]
            url = f"{API}/api/chat?session_id={sess}&question={requests.utils.quote(q['question'])}"
            r = requests.post(url, headers=HEADERS, timeout=TIMEOUT_S)
            elapsed = int((time.monotonic() - t0) * 1000)
            if r.status_code != 200:
                fail += 1
                data = {"answer": f"[HTTP {r.status_code}]", "intent": "ERROR", "duration_ms": elapsed}
            else:
                data = r.json()
        except Exception as e:
            fail += 1
            elapsed = int((time.monotonic() - t0) * 1000)
            data = {"answer": f"[ERROR] {type(e).__name__}: {e}", "intent": "ERROR", "duration_ms": elapsed}

        rec = {
            **q,
            "tuned_answer": data.get("answer", ""),
            "tuned_intent": data.get("intent", ""),
            "tuned_duration_ms": data.get("duration_ms", elapsed),
            "http_status": 200 if data.get("intent") != "ERROR" else 0,
        }
        out.write(json.dumps(rec, ensure_ascii=False) + "\n")
        out.flush()
        cum = int(time.monotonic() - t_start)
        print(
            f"[{idx:3d}] {q['_target_class']:12s} {elapsed:6d}ms "
            f"old_v={q.get('yesterday_verdict','?'):20s} "
            f"intent={data.get('intent','?'):24s} cum={cum}s",
            flush=True,
        )

    out.close()
    total = int(time.monotonic() - t_start)
    print(f"\n완료. {total}s, fail={fail}, 출력: {OUT_FILE}", flush=True)
    return 0 if fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
