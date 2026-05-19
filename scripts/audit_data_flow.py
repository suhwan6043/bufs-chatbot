"""Step 4 — 데이터 흐름 스냅샷: 4 case × 9 stage live capture.

X-Test-Mode 헤더로 실로그 오염 방지하고, /api/chat/stream을 호출해
각 stage(clarification/contact/understand/rewrite/search/merge/gen/val/post)별로
timestamp + sample을 JSONL로 저장.

수동 백엔드 + H100 터널 필요. 본 스크립트는 라이브 실행용. 백엔드 OFF인 경우
synthetic_summary.md 처럼 PIPELINE_TIMING 로그 기반 추출로 대체.

사용:
    python scripts/audit_data_flow.py \\
        --base-url http://localhost:8000 \\
        --session test-audit-step4 \\
        --out reports/code_audit/04_data_flow/case_01.jsonl \\
        --question "영어전공 학과사무실 전화번호" \\
        --case-id case_01_contact

플랜의 4 case 라벨:
  case_01_contact         — direct_answer 트리거 (CONTACT)
  case_02_faq             — FAQ 직접답변/캐시 hit
  case_03_multi_intent    — 장학금 + 부서
  case_04_complex         — 학번별 졸업요건
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from urllib.parse import quote

try:
    import httpx
except Exception:
    print("[err] httpx 필요. pip install httpx", file=sys.stderr)
    sys.exit(1)


# 9 stage canonical labels — chat.py의 _ms_* 변수와 매핑
STAGES = [
    "clarification",   # _handle_clarification_reply, _check_clarification_gate
    "contact",         # _format_contact_answer
    "understand",      # query_understanding.understand (combined mode)
    "rewrite",         # query_rewriter.rewrite (rule mode)
    "search",          # router_inst.route_and_search
    "merge",           # merger.merge
    "generate",        # generator.generate
    "validate",        # validator.validate + Phase 4
    "post",            # footer + cache + session update + log
]


def run_one_case(
    base_url: str, session_id: str, question: str, *, case_id: str,
) -> dict:
    """SSE 스트림 한 번 실행 + 모든 이벤트 수집."""
    url = f"{base_url}/api/chat/stream"
    params = {"session_id": session_id, "question": question}
    snapshot = {
        "case_id": case_id,
        "question": question,
        "session_id": session_id,
        "t_started": time.time(),
        "events": [],
        "timing": None,
        "tokens": 0,
        "final_answer_chars": 0,
        "intent": None,
        "results_count": 0,
        "source_urls_count": 0,
        "error": None,
    }
    try:
        with httpx.stream(
            "GET", url, params=params,
            headers={"X-Test-Mode": "1", "Accept": "text/event-stream"},
            timeout=120.0,
        ) as r:
            r.raise_for_status()
            current_event = None
            for raw_line in r.iter_lines():
                if not raw_line:
                    current_event = None
                    continue
                line = raw_line if isinstance(raw_line, str) else raw_line.decode("utf-8", "replace")
                if line.startswith("event:"):
                    current_event = line.split(":", 1)[1].strip()
                elif line.startswith("data:"):
                    data_str = line.split(":", 1)[1].strip()
                    snapshot["events"].append({"event": current_event, "t": time.time(), "data_chars": len(data_str)})
                    if current_event == "token":
                        snapshot["tokens"] += 1
                    elif current_event == "done":
                        try:
                            d = json.loads(data_str)
                            snapshot["timing"] = d.get("timing")
                            snapshot["intent"] = d.get("intent")
                            snapshot["final_answer_chars"] = len(d.get("answer", ""))
                            snapshot["results_count"] = len(d.get("results", []))
                            snapshot["source_urls_count"] = len(d.get("source_urls", []))
                        except Exception:
                            pass
    except Exception as e:
        snapshot["error"] = f"{type(e).__name__}: {e}"
    snapshot["t_ended"] = time.time()
    snapshot["elapsed_sec"] = snapshot["t_ended"] - snapshot["t_started"]
    return snapshot


def expand_to_stage_rows(snapshot: dict) -> list[dict]:
    """case 1건 스냅샷을 9 stage row로 펼친다."""
    rows = []
    timing = snapshot.get("timing") or {}
    case_id = snapshot["case_id"]
    intent = snapshot.get("intent", "?")

    # contact 단락은 timing dict가 없음 (조기 종료)
    timing_map = {
        "clarification": 0,
        "contact": 0,  # contact 분기는 event=done + intent=CONTACT으로 식별
        "understand": timing.get("rewrite_ms", 0) if timing else 0,  # understand가 rewrite_ms에 통합 기록됨
        "rewrite": timing.get("rewrite_ms", 0) if timing else 0,
        "search": timing.get("search_ms", 0) if timing else 0,
        "merge": timing.get("merge_ms", 0) if timing else 0,
        "generate": timing.get("generate_ms", 0) if timing else 0,
        "validate": timing.get("validate_ms", 0) if timing else 0,
        "post": 0,  # post는 별도 timing 없음 — total - sum others로 추정
    }

    elapsed_total_ms = int(snapshot["elapsed_sec"] * 1000)
    # post = total - (모든 stage 합)
    sum_stages = sum(timing_map[s] for s in STAGES if s != "post")
    timing_map["post"] = max(0, elapsed_total_ms - sum_stages)

    for stage in STAGES:
        rows.append({
            "case_id": case_id,
            "question": snapshot["question"][:80],
            "intent": intent,
            "stage": stage,
            "elapsed_ms": timing_map[stage],
            "type": "ms",
            "size_bytes": -1,
            "sample": "",
            "error": snapshot.get("error"),
        })
    # 추가 row: 전체 사이즈 정보
    rows.append({
        "case_id": case_id,
        "question": snapshot["question"][:80],
        "intent": intent,
        "stage": "summary",
        "elapsed_ms": elapsed_total_ms,
        "type": "summary",
        "size_bytes": snapshot["final_answer_chars"],
        "sample": f"tokens={snapshot['tokens']} results={snapshot['results_count']} urls={snapshot['source_urls_count']}",
        "error": snapshot.get("error"),
    })
    return rows


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--base-url", default="http://localhost:8000")
    p.add_argument("--session", required=True)
    p.add_argument("--question", required=True)
    p.add_argument("--case-id", required=True)
    p.add_argument("--out", required=True)
    args = p.parse_args()

    snapshot = run_one_case(
        args.base_url, args.session, args.question, case_id=args.case_id,
    )
    rows = expand_to_stage_rows(snapshot)

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        # snapshot meta + rows
        f.write(json.dumps({"_meta": {
            "case_id": args.case_id,
            "elapsed_sec": snapshot["elapsed_sec"],
            "error": snapshot.get("error"),
        }}, ensure_ascii=False) + "\n")
        for row in rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(f"[saved] {out} ({len(rows)} stage rows)", file=sys.stderr)
    print(json.dumps(snapshot, ensure_ascii=False, indent=2)[:800])
    return 0


if __name__ == "__main__":
    sys.exit(main())
