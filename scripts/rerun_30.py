"""30건 측정 — 5/13 H100 정상 환경에서 v1 prompt 검증.

구성:
- refusal 15: 직전 v0/v1 측정과 동일 (비교 가능성)
  idx: 6, 21, 43, 44, 54, 55, 62, 68, 86, 89, 90, 95, 110, 119, 132
- 회귀 5: 직전과 동일
  idx: 4, 11, 15, 28, 57
- 새 10건: intent + verdict 다양화 (137문 중)
  idx: 0, 7, 17, 23, 27, 46, 80, 96, 108, 113

출력: reports/eval_5_7/responses_30.jsonl (resumable, X-Test-Mode=1)
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
NEW_10 = [0, 7, 17, 23, 27, 46, 80, 96, 108, 113]
TARGETS = REFUSAL_15 + REGRESSION_5 + NEW_10

BASE = Path(__file__).parent.parent
H100_FILE = BASE / "reports" / "eval_5_7" / "responses_h100.jsonl"
GRADED_FILE = BASE / "reports" / "eval_5_7" / "graded_h100.jsonl"
TUNED_FILE = BASE / "reports" / "eval_5_7" / "responses_tuned.jsonl"
RED_FILE = BASE / "reports" / "eval_5_7" / "responses_redesigned.jsonl"
OUT_FILE = BASE / "reports" / "eval_5_7" / "responses_30.jsonl"


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

    tuned_by, red_by = {}, {}
    for path, store in [(TUNED_FILE, tuned_by), (RED_FILE, red_by)]:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        obj = json.loads(line)
                        store[obj["idx"]] = obj
                    except Exception:
                        pass

    questions = []
    for tidx in TARGETS:
        if tidx not in h_by:
            print(f"WARN: idx {tidx} not in h100", flush=True)
            continue
        r = h_by[tidx]
        cls = ("refusal_15" if tidx in REFUSAL_15 else
               ("regression_5" if tidx in REGRESSION_5 else "new_10"))
        questions.append({
            "idx": tidx,
            "question": r["question"],
            "yesterday_intent": r.get("h100_intent"),
            "yesterday_verdict": g_by.get(tidx, {}).get("verdict"),
            "yesterday_gt": g_by.get(tidx, {}).get("ground_truth", "")[:500],
            "v0_tuned_answer": tuned_by.get(tidx, {}).get("tuned_answer", "")[:500],
            "v1_red_answer": red_by.get(tidx, {}).get("v1_answer", "")[:500],
            "_target_class": cls,
        })

    print(f"총 {len(questions)}건 측정 시작 — 5/13 H100 정상 환경 v1", flush=True)

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
            "today_answer": data.get("answer", ""),
            "today_intent": data.get("intent", ""),
            "today_duration_ms": data.get("duration_ms", elapsed),
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
