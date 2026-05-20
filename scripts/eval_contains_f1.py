"""
BUFS 챗봇 rule-based Contains-F1 평가 (4/13 기준선과 apples-to-apples 비교용)
==========================================================================
- 생성 모델: /api/chat 경유 (Docker 컨테이너의 qwen + GPU Reranker)
- 채점 방식: scripts/eval_f1_score.py 의 answer_metrics() (토큰 기반 Contains-F1)
- 검색 지표: Recall@5, MRR@5  (scripts/eval_full.py 와 동일)
- 생성 지표: Contains-F1, EM, Answerable-F1, Unanswerable-F1, avg Token-F1

사용법:
  python -X utf8 scripts/eval_contains_f1.py \
      --datasets data/eval/balanced_test_set.jsonl \
                 data/eval/rag_eval_dataset_2026_1.jsonl \
                 data/eval/user_eval_dataset_50.jsonl \
      --base-url http://localhost:8000 \
      --output reports/eval_contains_f1
"""

import argparse
import io
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

import httpx

# ── UTF-8 출력 강제 ──
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# ── 프로젝트 루트 path 등록 (eval_f1_score 헬퍼 재사용) ──
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from scripts.eval_f1_score import (  # noqa: E402
    answer_metrics,
    is_refusal as rule_is_refusal,
)

# ── GT source 파싱: eval_full.py 와 동일 로직 ──

_SOURCE_ALIASES = {
    "학사안내": ["학사안내", "학사 안내", "1학기학사안내", "2026학년도1학기학사안내"],
    "학사일정표": ["학사안내", "학사일정"],
    "수강신청 FAQ": ["faq", "FAQ"],
    "장학 안내": ["장학", "scholarship", "notice_sch"],
    "학생포털": ["portal", "포털", "guide"],
    "TA장학생": ["TA", "조교"],
}


def _parse_gt_source(gt_source: str) -> dict:
    if not gt_source or gt_source in ("문서 밖", ""):
        return {"keywords": [], "pages": []}
    keywords, pages = [], []
    for m in re.finditer(r"p\.?(\d+)", gt_source):
        pages.append(int(m.group(1)))
    base = re.sub(r"\s*p\.?\d+.*", "", gt_source).strip()
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    if base:
        keywords.append(base)
    for alias_key, aliases in _SOURCE_ALIASES.items():
        if alias_key in gt_source:
            keywords.extend(aliases)
    return {"keywords": list(set(keywords)), "pages": pages}


def _source_matches_keywords(result: dict, gt_parsed: dict) -> bool:
    if not gt_parsed["keywords"]:
        return False
    source = (result.get("source") or "").lower()
    doc_type = (result.get("doc_type") or "").lower()
    page = int(result.get("page_number", 0) or 0)
    kw_match = any(kw.lower() in source or kw.lower() in doc_type for kw in gt_parsed["keywords"])
    if not kw_match:
        return False
    if gt_parsed["pages"]:
        return page in gt_parsed["pages"]
    return True


def _recall_mrr_keywords(results: list[dict], gt_source: str, k: int = 5) -> dict:
    gt_parsed = _parse_gt_source(gt_source)
    if not gt_parsed["keywords"]:
        return {"recall": None, "mrr": None, "hit_rank": None}
    top_k = results[:k]
    hit_rank = None
    for rank, r in enumerate(top_k, 1):
        if _source_matches_keywords(r, gt_parsed):
            hit_rank = rank
            break
    return {
        "recall": 1.0 if hit_rank else 0.0,
        "mrr": 1.0 / hit_rank if hit_rank else 0.0,
        "hit_rank": hit_rank,
    }


def _recall_mrr_page(results: list[dict], gold_page: int, k: int = 5, tol: int = 1) -> dict:
    if not gold_page:
        return {"recall": None, "mrr": None, "hit_rank": None}
    pages = [int(r.get("page_number", 0) or 0) for r in results[:k]]
    hit_rank = None
    for rank, pg in enumerate(pages, 1):
        if abs(pg - gold_page) <= tol:
            hit_rank = rank
            break
    return {
        "recall": 1.0 if hit_rank else 0.0,
        "mrr": 1.0 / hit_rank if hit_rank else 0.0,
        "hit_rank": hit_rank,
    }


# ── 단일 문항 평가 ──

def evaluate_one(
    chat_client: httpx.Client,
    session_id: str,
    item: dict,
    dataset_type: str,
) -> dict:
    question = item["question"]
    gt = item.get("answer", "")
    answerable = item.get("answerable", True)
    gt_source = item.get("source", "")
    gold_page = int(item.get("source_page", 0) or 0)

    t0 = time.monotonic()
    try:
        r = chat_client.post(
            "/api/chat",
            params={"session_id": session_id, "question": question},
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        prediction = data.get("answer", "")
        intent = data.get("intent", "")
        results = data.get("results", [])
        elapsed = round(time.monotonic() - t0, 2)
    except Exception as e:
        prediction = f"[ERROR] {e}"
        intent = ""
        results = []
        elapsed = round(time.monotonic() - t0, 2)

    # 검색 지표
    if dataset_type == "balanced":
        ret = _recall_mrr_keywords(results, gt_source, k=5)
    else:
        ret = _recall_mrr_page(results, gold_page, k=5, tol=1)

    # 생성 지표 — rule-based Contains-F1 (eval_f1_score.answer_metrics)
    is_error = prediction.startswith("[ERROR]")
    if is_error:
        metrics = {"exact_match": False, "contains_gt": False,
                   "token_precision": 0.0, "token_recall": 0.0, "token_f1": 0.0}
    elif answerable and gt:
        metrics = answer_metrics(prediction, gt)
    else:
        # 대답불가: 거부 응답이면 정답 — eval_f1_score.is_refusal 사용 (동일 기준)
        refused = rule_is_refusal(prediction)
        metrics = {"exact_match": False, "contains_gt": refused,
                   "token_precision": 0.0, "token_recall": 0.0, "token_f1": 0.0}

    return {
        "id": item.get("id", ""),
        "question": question,
        "ground_truth": gt,
        "prediction": prediction,
        "answerable": answerable,
        "intent": intent,
        "difficulty": item.get("difficulty", ""),
        "gt_source": gt_source,
        "gold_page": gold_page,
        "elapsed_s": elapsed,
        "results_count": len(results),
        "retrieval": ret,
        **metrics,
    }


# ── 요약 (eval_f1_score 와 동일 산식) ──

def build_summary(records: list[dict]) -> dict:
    n = len(records)
    answerable = [r for r in records if r["answerable"]]
    unanswerable = [r for r in records if not r["answerable"]]

    # 검색
    ret_items = [r for r in answerable if r["retrieval"]["recall"] is not None]
    recall5 = sum(r["retrieval"]["recall"] for r in ret_items) / len(ret_items) if ret_items else 0.0
    mrr5 = sum(r["retrieval"]["mrr"] for r in ret_items) / len(ret_items) if ret_items else 0.0

    # 생성
    total_contains = sum(1 for r in records if r["contains_gt"])
    ans_contains = sum(1 for r in answerable if r["contains_gt"])
    unans_contains = sum(1 for r in unanswerable if r["contains_gt"])
    em_total = sum(1 for r in records if r["exact_match"])
    avg_tok_f1 = sum(r["token_f1"] for r in answerable) / len(answerable) if answerable else 0.0

    return {
        "retrieval": {
            "recall_at_5": round(recall5, 4),
            "mrr_at_5": round(mrr5, 4),
            "evaluated": len(ret_items),
            "total_answerable": len(answerable),
        },
        "generation": {
            "total": n,
            "overall_contains_f1": round(total_contains / n, 4) if n else 0.0,
            "em_rate": round(em_total / n, 4) if n else 0.0,
            "answerable_contains_f1": round(ans_contains / len(answerable), 4) if answerable else 0.0,
            "unanswerable_contains_f1": round(unans_contains / len(unanswerable), 4) if unanswerable else 0.0,
            "answerable_n": len(answerable),
            "unanswerable_n": len(unanswerable),
            "answerable_correct": ans_contains,
            "unanswerable_correct": unans_contains,
            "avg_token_f1": round(avg_tok_f1, 4),
        },
    }


def print_summary(dataset_name: str, summary: dict) -> None:
    r = summary["retrieval"]
    g = summary["generation"]
    print()
    print("=" * 65)
    print(f"  {dataset_name}  (rule-based Contains-F1)")
    print("=" * 65)
    print(f"  [검색]  Recall@5={r['recall_at_5']:.4f}  MRR@5={r['mrr_at_5']:.4f}"
          f"  (평가={r['evaluated']}/{r['total_answerable']})")
    print(f"  [생성]  Overall Contains-F1 = {g['overall_contains_f1']:.4f}"
          f"  EM = {g['em_rate']:.4f}")
    print(f"          Answerable-F1  = {g['answerable_contains_f1']:.4f}"
          f"  ({g['answerable_correct']}/{g['answerable_n']})")
    print(f"          Unanswerable-F1= {g['unanswerable_contains_f1']:.4f}"
          f"  ({g['unanswerable_correct']}/{g['unanswerable_n']})")
    print(f"          Avg Token-F1   = {g['avg_token_f1']:.4f}")
    print("=" * 65)


# ── 메인 ──

def main() -> None:
    parser = argparse.ArgumentParser(
        description="BUFS rule-based Contains-F1 평가 (Docker /api/chat 경유)"
    )
    parser.add_argument("--datasets", nargs="+", required=True, help="JSONL 데이터셋 경로")
    parser.add_argument("--base-url", default="http://localhost:8000", help="챗봇 API")
    parser.add_argument("--output", default="reports/eval_contains_f1", help="결과 저장 디렉토리")
    parser.add_argument("--limit", type=int, default=None, help="각 데이터셋 최대 질문 수")
    parser.add_argument("--tag", default=None, help="결과 파일명에 붙는 태그 (예: slicing_on, slicing_off)")
    # P1.1 픽스(2026-05-18): 평가 재현성 옵션
    parser.add_argument(
        "--per-question-session", action="store_true",
        help="질문마다 새 session_id 생성. 세션 내 LLM 응답 캐시 누적·이력 오염을 차단해 재현성↑.",
    )
    parser.add_argument(
        "--no-cache", action="store_true",
        help="평가 시작·각 데이터셋 사이에 /api/admin/cache/clear 호출하여 응답 캐시 비활성화. "
             "ADMIN_PASSWORD 환경변수 필요.",
    )
    parser.add_argument(
        "--seed", type=int, default=None,
        help="LLM_SEED 환경변수에 값을 설정하여 backend LLM 호출을 결정적으로 만듦. "
             "이 변수는 backend 프로세스 시작 시 읽히므로, 효과를 보려면 backend도 같은 LLM_SEED로 띄워야 함.",
    )
    args = parser.parse_args()

    # seed 환경변수 전달 (현재 프로세스에서 import되는 모듈에만 영향. backend는 별 프로세스라
    # 시작 시점에 LLM_SEED를 env로 받아야 함. 이 옵션은 결과 파일 메타에 기록되어 추적용으로 사용).
    if args.seed is not None:
        os.environ["LLM_SEED"] = str(args.seed)

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    tag = f"_{args.tag}" if args.tag else ""
    chat_client = httpx.Client(
        base_url=args.base_url, timeout=180,
        headers={"X-Test-Mode": "1"},  # 실사용자 로그(JSONL + chat_messages DB) 오염 방지
    )

    # --no-cache: 평가 시작 시 응답 캐시 비우기 (ADMIN_PASSWORD 필요)
    def _clear_response_cache(label: str = "") -> None:
        if not args.no_cache:
            return
        admin_pw = os.environ.get("ADMIN_PASSWORD", "")
        if not admin_pw:
            print(f"  ⚠ --no-cache 지정됐으나 ADMIN_PASSWORD 환경변수 없음 — 캐시 클리어 스킵 ({label})")
            return
        try:
            # admin 인증 토큰 발급
            auth = chat_client.post(
                "/api/admin/auth/login", json={"password": admin_pw}, timeout=10,
            )
            if auth.status_code != 200:
                print(f"  ⚠ admin 로그인 실패 ({auth.status_code}) — 캐시 클리어 스킵 ({label})")
                return
            token = auth.json().get("access_token") or auth.json().get("token")
            r = chat_client.post(
                "/api/admin/cache/clear?scope=all",
                headers={"Authorization": f"Bearer {token}"}, timeout=10,
            )
            if r.status_code < 300:
                print(f"  캐시 클리어 OK ({label}, status={r.status_code})")
            else:
                print(f"  ⚠ 캐시 클리어 실패 ({r.status_code}) ({label})")
        except Exception as e:
            print(f"  ⚠ 캐시 클리어 예외: {e} ({label})")

    _clear_response_cache("eval 시작")

    all_results = {}

    for dataset_path_str in args.datasets:
        dataset_path = Path(dataset_path_str)
        if not dataset_path.exists():
            print(f"\n⚠ 데이터셋 없음: {dataset_path} — 건너뜀")
            continue

        items = []
        with open(dataset_path, encoding="utf-8") as f:
            for line in f:
                if line.strip():
                    items.append(json.loads(line))

        if args.limit:
            items = items[: args.limit]

        first = items[0] if items else {}
        has_source_page = "source_page" in first
        source_val = first.get("source", "")
        is_page_only_source = bool(re.match(r"^p\.?\d+", source_val.strip()))
        dataset_type = "page_based" if (has_source_page or is_page_only_source) else "balanced"

        if dataset_type == "page_based" and not has_source_page:
            for item in items:
                src = item.get("source", "")
                m = re.search(r"p\.?(\d+)", src)
                if m:
                    item["source_page"] = int(m.group(1))

        dname = dataset_path.stem
        print(f"\n{'='*65}")
        print(f"데이터셋: {dname}  ({len(items)}문항, type={dataset_type})")
        print(f"{'='*65}")

        _clear_response_cache(f"데이터셋 {dname}")

        # 기본 세션 (per-question 모드가 아닐 때 재사용)
        default_session_id = None
        if not args.per_question_session:
            try:
                sess_r = chat_client.post("/api/session", json={"lang": "ko"})
                default_session_id = sess_r.json()["session_id"]
            except Exception as e:
                print(f"세션 생성 실패: {e}")
                continue

        records = []
        for idx, item in enumerate(items, 1):
            # P1.1 픽스: 질문마다 새 세션 생성하여 캐시·이력 누적 차단
            if args.per_question_session:
                try:
                    sess_r = chat_client.post("/api/session", json={"lang": "ko"})
                    session_id = sess_r.json()["session_id"]
                except Exception as e:
                    print(f"  세션 생성 실패 (질문 {idx}): {e}")
                    continue
            else:
                session_id = default_session_id

            print(f"  [{idx:02d}/{len(items)}] {item.get('id', '')}"
                  f" Q={item['question'][:40]}...", end=" ", flush=True)
            rec = evaluate_one(chat_client, session_id, item, dataset_type)
            records.append(rec)
            ret_str = (f"R@5={'HIT' if rec['retrieval']['recall'] else 'MISS'}"
                       if rec["retrieval"]["recall"] is not None else "R@5=N/A")
            c_str = "✓" if rec["contains_gt"] else "✗"
            print(f"{ret_str} contains={c_str} tok_f1={rec['token_f1']:.2f} {rec['elapsed_s']}s", flush=True)

        summary = build_summary(records)
        print_summary(dname, summary)

        out_path = out_dir / f"{dname}{tag}_{timestamp}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"dataset": str(dataset_path), "summary": summary, "results": records},
                      f, ensure_ascii=False, indent=2)
        print(f"  저장: {out_path}")

        all_results[dname] = summary

    # P1.1 픽스(2026-05-18): 평가 옵션 메타 기록 — 재현성 추적용
    eval_options = {
        "per_question_session": args.per_question_session,
        "no_cache": args.no_cache,
        "seed": args.seed,
        "limit": args.limit,
        "base_url": args.base_url,
    }

    combined_path = out_dir / f"combined{tag}_{timestamp}.json"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": timestamp, "tag": args.tag,
                   "eval_options": eval_options,
                   "datasets": all_results},
                  f, ensure_ascii=False, indent=2)

    print()
    print("=" * 75)
    print("  [최종 요약 · rule-based Contains-F1]")
    print(f"  {'데이터셋':<30} {'Rec@5':>7} {'MRR@5':>7} {'Cnt-F1':>7} {'Ans-F1':>7} {'Unans-F1':>9} {'EM':>6}")
    print("  " + "-" * 73)
    for dname, s in all_results.items():
        r = s["retrieval"]
        g = s["generation"]
        print(f"  {dname:<30} {r['recall_at_5']:>7.4f} {r['mrr_at_5']:>7.4f}"
              f" {g['overall_contains_f1']:>7.4f} {g['answerable_contains_f1']:>7.4f}"
              f" {g['unanswerable_contains_f1']:>9.4f} {g['em_rate']:>6.4f}")
    print("=" * 75)
    print(f"\n전체 결과: {combined_path}")


if __name__ == "__main__":
    main()
