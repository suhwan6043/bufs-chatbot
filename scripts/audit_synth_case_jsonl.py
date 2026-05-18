"""Step 4 — 백엔드 OFF 상태에서 5/7 실측 PIPELINE_TIMING 로그를 4 case JSONL로 변환.

라이브 실행은 audit_data_flow.py. 본 스크립트는 백엔드 OFF 환경의 대체 경로.
4 case는 path 라벨 + intent 기준 선별:
  - case_01_contact            : path=contact, intent=CONTACT
  - case_02_cached             : path=cached, generate=0
  - case_03_multi_intent       : intent=SCHOLARSHIP qt=factoid (다부서 질문)
  - case_04_complex            : intent=GRADUATION_REQ qt=factoid + 학번
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).parent.parent
TIMING_FILE = ROOT / "reports/eval_5_7/pipeline_timing_all172.txt"
RESPONSES = ROOT / "reports/eval_5_7/responses_h100.jsonl"
OUT_DIR = ROOT / "reports/code_audit/04_data_flow"

STAGES = [
    "clarification", "contact", "understand", "rewrite",
    "search", "merge", "generate", "validate", "post",
]

# regex 파싱
_RE = re.compile(
    r"PIPELINE_TIMING total=(?P<total>\d+)ms follow_up=(?P<fu>\d+)ms rewrite=(?P<rw>\d+)ms "
    r"analyze=(?P<an>\d+)ms search=(?P<sr>\d+)ms merge=(?P<mg>\d+)ms retry=(?P<rt>\d+)ms "
    r"generate=(?P<gn>\d+)ms validate=(?P<vl>\d+)ms intent=(?P<intent>\S+) qt=(?P<qt>\S+) "
    r"follow_up=(?P<reason>\S+) endpoint=(?P<ep>\S+)(?: path=(?P<path>\S+))?"
)


def parse_timings():
    rows = []
    for line in TIMING_FILE.read_text(encoding="utf-8").splitlines():
        m = _RE.search(line)
        if not m:
            continue
        d = m.groupdict()
        rows.append({k: int(v) if v and v.isdigit() else v for k, v in d.items()})
    return rows


def find_case(rows, *, intent=None, path=None, gt_gen=None):
    for r in rows:
        if intent and r.get("intent") != intent:
            continue
        if path and r.get("path") != path:
            continue
        if gt_gen is not None and r.get("gn", 0) < gt_gen:
            continue
        return r
    return None


def to_stage_rows(timing: dict, *, case_id: str, question: str, answer_chars: int):
    """timing dict → 9 stage row + summary row."""
    # contact 단락은 모든 ms = 0 (특수 분기)
    rewrite_ms = timing.get("rw", 0)
    if timing.get("path") == "contact":
        # contact 단락은 0ms — chat.py L1031-1034에서 즉시 return
        ms_map = {s: 0 for s in STAGES}
        ms_map["contact"] = timing.get("total", 0)  # contact 자체 elapsed (보통 <50ms, 로그에는 0)
    else:
        # understanding_enabled=true 경로: rewrite_ms == understand_ms
        ms_map = {
            "clarification": 0,
            "contact": 0,
            "understand": rewrite_ms,  # 통합 호출 elapsed
            "rewrite": 0,  # rule 경로일 때만 별도, understand 경로는 통합되어 0
            "search": timing.get("sr", 0),
            "merge": timing.get("mg", 0),
            "generate": timing.get("gn", 0),
            "validate": timing.get("vl", 0),
        }
        # post = total - 모든 stage 합
        sum_others = sum(ms_map.values())
        ms_map["post"] = max(0, timing.get("total", 0) - sum_others)

    rows = []
    for stage in STAGES:
        # 각 stage별 type/sample 메타 추가
        meta = {
            "clarification": {"type": "gate", "sample": "_check_clarification_gate / pending fields"},
            "contact": {"type": "shortcut", "sample": "_format_contact_answer → departments.json"},
            "understand": {"type": "llm_combined", "sample": "gemma3:4b JSON understand"},
            "rewrite": {"type": "rule", "sample": "query_rewriter (rule 경로일 때만)"},
            "search": {"type": "retrieval", "sample": "BM25 + ChromaDB + GraphDB → SearchResults"},
            "merge": {"type": "merge", "sample": "RRF + adaptive_cutoff + budget → formatted_context"},
            "generate": {"type": "llm_stream", "sample": "AnswerGenerator.generate (qwen3:8b/gemma4:26b)"},
            "validate": {"type": "post_llm", "sample": "verify_answer_against_context + Validator"},
            "post": {"type": "io", "sample": "footer + cache.store + session.update + log"},
        }[stage]
        rows.append({
            "case_id": case_id,
            "question": question[:80],
            "intent": timing.get("intent", "?"),
            "qt": timing.get("qt", "?"),
            "path": timing.get("path", "?"),
            "stage": stage,
            "elapsed_ms": ms_map[stage],
            "type": meta["type"],
            "sample": meta["sample"],
            "source": "PIPELINE_TIMING_5_7",
        })
    # summary row
    rows.append({
        "case_id": case_id,
        "stage": "summary",
        "elapsed_ms": timing.get("total", 0),
        "type": "summary",
        "intent": timing.get("intent", "?"),
        "qt": timing.get("qt", "?"),
        "path": timing.get("path", "?"),
        "follow_up_reason": timing.get("reason", "?"),
        "endpoint": timing.get("ep", "?"),
        "answer_chars": answer_chars,
        "source": "PIPELINE_TIMING_5_7",
    })
    return rows


def main():
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    timings = parse_timings()
    responses = [json.loads(l) for l in RESPONSES.read_text(encoding="utf-8").splitlines() if l.strip()]
    by_q = {r["question"]: r for r in responses}
    print(f"[parse] timing_lines={len(timings)} responses={len(responses)}", file=sys.stderr)

    # case 정의
    cases = [
        ("case_01_contact",       find_case(timings, intent="CONTACT", path="contact"),
         "영어전공 학과사무실 전화번호"),
        ("case_02_cached",        find_case(timings, path="cached", intent="GRADUATION_REQ"),
         "2020학번 졸업요건 알려줘"),
        ("case_03_multi_intent",  find_case(timings, intent="SCHOLARSHIP", path="generated", gt_gen=1000),
         "국가장학금을 받아서 등록금을 납부하려고 하는데, 어느 부서에 물어봐야 할까?"),
        ("case_04_complex",       find_case(timings, intent="GRADUATION_REQ", path="generated", gt_gen=5000),
         "2020학번 졸업학점 영역별로 알려줘"),
    ]

    for case_id, t, q in cases:
        if not t:
            print(f"[warn] {case_id}: no matching timing line", file=sys.stderr)
            continue
        answer_chars = len(by_q.get(q, {}).get("h100_answer", ""))
        rows = to_stage_rows(t, case_id=case_id, question=q, answer_chars=answer_chars)
        out = OUT_DIR / f"{case_id}.jsonl"
        with open(out, "w", encoding="utf-8") as f:
            f.write(json.dumps({"_meta": {
                "case_id": case_id,
                "question": q,
                "source_log": str(TIMING_FILE.relative_to(ROOT)),
                "raw_timing": t,
                "answer_chars": answer_chars,
            }}, ensure_ascii=False) + "\n")
            for row in rows:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        print(f"[saved] {out.relative_to(ROOT)} stages={len(STAGES)+1}", file=sys.stderr)


if __name__ == "__main__":
    main()
