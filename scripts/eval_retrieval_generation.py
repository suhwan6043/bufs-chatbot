"""
검색/생성 분리 평가 스크립트.

검색 지표: Recall@5, MRR@5
생성 지표: Overall Contains-F1, EM, Answerable-F1, Unanswerable 정확도

사용법:
  python scripts/eval_retrieval_generation.py \
      --base-url http://localhost:8000 \
      --dataset data/eval/balanced_test_set.jsonl \
      --output reports/test_session/eval_split.json
"""

import argparse
import io
import json
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

from scripts.eval_f1_score import answer_metrics, correctness_flag

# ── GT source → 검색 결과 매칭 ──

_SOURCE_ALIASES = {
    "학사안내": ["학사안내", "학사 안내", "1학기학사안내"],
    "학사일정표": ["학사안내", "학사일정"],
    "수강신청 FAQ": ["faq", "FAQ"],
    "장학 안내": ["장학", "scholarship", "notice_sch"],
    "학생포털": ["portal", "포털", "guide"],
    "TA장학생": ["TA", "조교"],
}

_REFUSAL_KW = ["찾을 수 없", "확인하지 못", "문의하", "범위를 벗어", "관련 정보를 찾을 수 없"]


def _parse_gt_source(gt_source: str) -> dict:
    """GT source 문자열을 파싱. 예: '학사안내 p.25,27' → {keywords: [...], pages: [25,27]}"""
    if not gt_source or gt_source == "문서 밖":
        return {"keywords": [], "pages": []}

    keywords = []
    pages = []

    # 페이지 추출
    for m in re.finditer(r"p\.?(\d+)", gt_source):
        pages.append(int(m.group(1)))

    # 핵심 키워드 추출
    base = re.sub(r"\s*p\.?\d+.*", "", gt_source).strip()
    keywords.append(base)

    # 별칭 추가
    for alias_key, aliases in _SOURCE_ALIASES.items():
        if alias_key in gt_source:
            keywords.extend(aliases)

    return {"keywords": list(set(keywords)), "pages": pages}


def _source_matches(result: dict, gt_parsed: dict) -> bool:
    """검색 결과 1건이 GT source와 매칭되는지 판정."""
    if not gt_parsed["keywords"]:
        return False

    source = (result.get("source") or "").lower()
    doc_type = (result.get("doc_type") or "").lower()
    page = result.get("page_number", 0)

    # 키워드 매칭 (source path 또는 doc_type에 포함)
    kw_match = any(kw.lower() in source or kw.lower() in doc_type for kw in gt_parsed["keywords"])
    if not kw_match:
        return False

    # 페이지 매칭 (GT에 페이지 지정이 있으면 확인, 없으면 키워드만으로 통과)
    if gt_parsed["pages"]:
        return page in gt_parsed["pages"]
    return True


def retrieval_metrics(results: list[dict], gt_source: str, k: int = 5) -> dict:
    """Recall@k, MRR@k 계산."""
    gt_parsed = _parse_gt_source(gt_source)

    if not gt_parsed["keywords"]:
        return {"recall": None, "mrr": None, "hit_rank": None}

    top_k = results[:k]
    hit_rank = None
    for rank, r in enumerate(top_k, 1):
        if _source_matches(r, gt_parsed):
            hit_rank = rank
            break

    return {
        "recall": 1.0 if hit_rank is not None else 0.0,
        "mrr": 1.0 / hit_rank if hit_rank is not None else 0.0,
        "hit_rank": hit_rank,
    }


def is_refusal(text: str) -> bool:
    """답변이 거부 응답인지 판정."""
    return any(kw in text for kw in _REFUSAL_KW)


# ── 단일 문항 평가 ──

def evaluate_one(client: httpx.Client, session_id: str, item: dict) -> dict:
    question = item["question"]
    gt = item.get("answer", "")
    answerable = item.get("answerable", True)
    gt_source = item.get("source", "")

    t0 = time.monotonic()
    try:
        r = client.post(
            "/api/chat",
            params={"session_id": session_id, "question": question},
            timeout=180,
        )
        r.raise_for_status()
        data = r.json()
        prediction = data.get("answer", "")
        intent = data.get("intent", "")
        search_results = data.get("results", [])
        elapsed = time.monotonic() - t0
    except Exception as e:
        prediction = f"[ERROR] {e}"
        intent = ""
        search_results = []
        elapsed = time.monotonic() - t0

    # 생성 지표
    if answerable:
        gen_metrics = answer_metrics(prediction, gt)
    else:
        pred_is_refusal = is_refusal(prediction) or not prediction.strip()
        gen_metrics = {
            "exact_match": False,
            "contains_gt": pred_is_refusal,
            "token_precision": 0.0,
            "token_recall": 0.0,
            "token_f1": 0.0,
        }

    # 검색 지표
    ret_metrics = retrieval_metrics(search_results, gt_source, k=5)

    return {
        "id": item.get("id", ""),
        "question": question,
        "ground_truth": gt,
        "prediction": prediction,
        "answerable": answerable,
        "intent": intent,
        "difficulty": item.get("difficulty", ""),
        "gt_source": gt_source,
        "elapsed_s": round(elapsed, 2),
        "search_results_count": len(search_results),
        "retrieval": ret_metrics,
        **gen_metrics,
    }


# ── 요약 ──

def build_summary(results: list[dict]) -> dict:
    n = len(results)
    answerable = [r for r in results if r["answerable"]]
    unanswerable = [r for r in results if not r["answerable"]]

    # 생성 지표
    ans_contains = sum(1 for r in answerable if r.get("contains_gt"))
    ans_em = sum(1 for r in answerable if r.get("exact_match"))
    unans_correct = sum(1 for r in unanswerable if r.get("contains_gt"))
    total_contains = ans_contains + unans_correct
    total_em = ans_em + unans_correct

    # 검색 지표 (answerable만 — unanswerable은 GT source가 "문서 밖")
    ret_items = [r for r in answerable if r["retrieval"]["recall"] is not None]
    recall_5 = sum(r["retrieval"]["recall"] for r in ret_items) / len(ret_items) if ret_items else 0
    mrr_5 = sum(r["retrieval"]["mrr"] for r in ret_items) / len(ret_items) if ret_items else 0

    return {
        "retrieval": {
            "evaluated": len(ret_items),
            "recall_at_5": round(recall_5, 4),
            "mrr_at_5": round(mrr_5, 4),
        },
        "generation": {
            "total": n,
            "overall_contains_f1": round(total_contains / n, 4) if n else 0,
            "overall_em": round(total_em / n, 4) if n else 0,
            "answerable": {
                "total": len(answerable),
                "contains_correct": ans_contains,
                "contains_f1": round(ans_contains / len(answerable), 4) if answerable else 0,
                "em_correct": ans_em,
                "em_rate": round(ans_em / len(answerable), 4) if answerable else 0,
                "avg_token_f1": round(
                    sum(r.get("token_f1", 0) for r in answerable) / len(answerable), 4
                ) if answerable else 0,
            },
            "unanswerable": {
                "total": len(unanswerable),
                "correct_refusals": unans_correct,
                "accuracy": round(unans_correct / len(unanswerable), 4) if unanswerable else 0,
            },
        },
    }


def main():
    parser = argparse.ArgumentParser(description="검색/생성 분리 eval")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default=None)
    args = parser.parse_args()

    items = []
    with open(args.dataset, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))

    print(f"Dataset: {args.dataset} ({len(items)} questions)")
    print(f"API: {args.base_url}")

    client = httpx.Client(base_url=args.base_url, timeout=180)
    r = client.post("/api/session", json={"lang": "ko"})
    session_id = r.json()["session_id"]
    print(f"Session: {session_id}")
    print()

    results = []
    for i, item in enumerate(items, 1):
        result = evaluate_one(client, session_id, item)
        results.append(result)

        # 상태 표시
        gen_ok = result.get("contains_gt", False)
        ret_ok = result["retrieval"]["recall"]
        ret_str = f"R@5={'HIT' if ret_ok else 'MISS'}" if ret_ok is not None else "R@5=N/A"
        gen_str = "GEN=PASS" if gen_ok else "GEN=FAIL"
        print(f"[{i:02d}/{len(items)}] {result['id']:5s} {ret_str:10s} {gen_str:10s} tok_f1={result.get('token_f1',0):.3f} {result['elapsed_s']:.1f}s")

    # 요약
    summary = build_summary(results)
    print()
    print("=" * 70)
    print("  RETRIEVAL (Answerable only)")
    print(f"    Recall@5:  {summary['retrieval']['recall_at_5']:.4f}  ({summary['retrieval']['evaluated']} items)")
    print(f"    MRR@5:     {summary['retrieval']['mrr_at_5']:.4f}")
    print()
    print("  GENERATION")
    print(f"    Overall Contains-F1:  {summary['generation']['overall_contains_f1']:.4f}  ({summary['generation']['total']} items)")
    print(f"    Overall EM:           {summary['generation']['overall_em']:.4f}")
    g = summary['generation']
    a = g['answerable']
    u = g['unanswerable']
    print(f"    Answerable F1:        {a['contains_f1']:.4f}  ({a['contains_correct']}/{a['total']})")
    print(f"    Answerable EM:        {a['em_rate']:.4f}  ({a['em_correct']}/{a['total']})")
    print(f"    Answerable Token-F1:  {a['avg_token_f1']:.4f}")
    print(f"    Unanswerable Acc:     {u['accuracy']:.4f}  ({u['correct_refusals']}/{u['total']})")
    print("=" * 70)

    # 실패 항목 요약
    fails_ret = [r for r in results if r["answerable"] and r["retrieval"]["recall"] == 0.0]
    fails_gen = [r for r in results if not r.get("contains_gt", False)]
    print(f"\n검색 실패 ({len(fails_ret)}건):")
    for r in fails_ret:
        print(f"  {r['id']:5s} gt_source={r['gt_source'][:40]}")
    print(f"\n생성 실패 ({len(fails_gen)}건):")
    for r in fails_gen:
        reason = "[ERROR]" if "[ERROR]" in r["prediction"] else r["prediction"][:50]
        print(f"  {r['id']:5s} pred={reason}")

    # 저장
    if args.output:
        output_data = {"summary": summary, "results": results}
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
