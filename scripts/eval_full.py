"""
BUFS 챗봇 종합 평가 스크립트
================================
- 생성 모델:  qwen/qwen3.5-9b  (챗봇 파이프라인 — /api/chat 엔드포인트)
- 평가 모델:  exaone-3.0-7.8b-instruct  (LLM-as-a-Judge)
- 검색 지표:  Recall@5, MRR@5
- 생성 지표:  Overall-F1, EM, Answerable-F1, Unanswerable-F1

사용법:
  python -X utf8 scripts/eval_full.py \
      --datasets data/eval/balanced_test_set.jsonl \
                 data/eval/rag_eval_dataset_2026_1.jsonl \
                 data/eval/user_eval_dataset_50.jsonl \
      --base-url http://localhost:8000 \
      --judge-url http://localhost:1234 \
      --judge-model exaone-3.0-7.8b-instruct \
      --output reports/eval_full
"""

import argparse
import io
import json
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

# ── GT source 파싱 (balanced_test_set용: "학사안내 p.25,27") ──

_SOURCE_ALIASES = {
    "학사안내": ["학사안내", "학사 안내", "1학기학사안내", "2026학년도1학기학사안내"],
    "학사일정표": ["학사안내", "학사일정"],
    "수강신청 FAQ": ["faq", "FAQ"],
    "장학 안내": ["장학", "scholarship", "notice_sch"],
    "학생포털": ["portal", "포털", "guide"],
    "TA장학생": ["TA", "조교"],
}

_REFUSAL_PATTERNS = [
    "학사지원팀", "문의", "확인되지 않", "찾을 수 없", "관련 정보를 찾",
    "해당 정보가 없", "답변할 수 없", "제공된 정보에", "확인하지 못", "범위를 벗어",
]


def _parse_gt_source(gt_source: str) -> dict:
    """GT source 문자열을 파싱. 예: '학사안내 p.25,27' → {keywords, pages}"""
    if not gt_source or gt_source in ("문서 밖", ""):
        return {"keywords": [], "pages": []}
    keywords = []
    pages = []
    for m in re.finditer(r"p\.?(\d+)", gt_source):
        pages.append(int(m.group(1)))
    base = re.sub(r"\s*p\.?\d+.*", "", gt_source).strip()
    # 파일명에서 PDF 확장자 제거
    base = re.sub(r"\.pdf$", "", base, flags=re.IGNORECASE)
    if base:
        keywords.append(base)
    for alias_key, aliases in _SOURCE_ALIASES.items():
        if alias_key in gt_source:
            keywords.extend(aliases)
    return {"keywords": list(set(keywords)), "pages": pages}


def _source_matches_keywords(result: dict, gt_parsed: dict) -> bool:
    """검색 결과 1건이 keyword+page 기반 GT와 매칭되는지."""
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
    """키워드+페이지 기반 Recall@k, MRR@k (balanced_test_set용)."""
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
    """page_number 기반 Recall@k, MRR@k (rag_eval_dataset / user_eval_dataset용)."""
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


# ── Rule-based Exact Match & Token-F1 ──

def _normalize(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"(?:이다|입니다|합니다|됩니다|있습니다|않습니다)[.]?\s*$", "", text)
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _tokenize(text: str) -> list:
    norm = _normalize(text)
    # 날짜 정규화
    norm = re.sub(
        r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일",
        lambda m: f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
        norm,
    )
    return re.findall(r"[a-z0-9가-힣+/.:]+", norm)


def _em(pred: str, gt: str) -> bool:
    return _normalize(pred) == _normalize(gt)


def _token_f1(pred: str, gt: str) -> float:
    from collections import Counter
    p_tok = _tokenize(pred)
    g_tok = _tokenize(gt)
    if not p_tok and not g_tok:
        return 1.0
    if not p_tok or not g_tok:
        return 0.0
    common = Counter(p_tok) & Counter(g_tok)
    n = sum(common.values())
    if n == 0:
        return 0.0
    prec = n / len(p_tok)
    rec = n / len(g_tok)
    return 2 * prec * rec / (prec + rec)


def _is_refusal(text: str) -> bool:
    t = text.lower()
    return any(p in t for p in _REFUSAL_PATTERNS)


# ── LLM Judge (exaone) ──

JUDGE_PROMPT = """\
다음은 학사 챗봇의 평가 과제입니다.

질문: {question}
정답(Ground Truth): {ground_truth}
AI 답변: {prediction}

위 AI 답변이 정답의 핵심 내용을 올바르게 포함하고 있으면 "정답",
그렇지 않으면 "오답"으로만 답하세요. 다른 설명 없이 "정답" 또는 "오답"만 출력하세요.
"""


def _judge_with_llm(
    judge_client: httpx.Client,
    judge_model: str,
    question: str,
    ground_truth: str,
    prediction: str,
    max_retries: int = 2,
) -> bool | None:
    """exaone에게 정답/오답 판정을 맡깁니다. None = 판정 실패."""
    prompt = JUDGE_PROMPT.format(
        question=question,
        ground_truth=ground_truth,
        prediction=prediction,
    )
    for attempt in range(max_retries):
        try:
            resp = judge_client.post(
                "/v1/chat/completions",
                json={
                    "model": judge_model,
                    "messages": [{"role": "user", "content": prompt}],
                    "max_tokens": 10,
                    "temperature": 0.0,
                },
                timeout=60,
            )
            resp.raise_for_status()
            verdict = resp.json()["choices"][0]["message"]["content"].strip()
            if "정답" in verdict:
                return True
            if "오답" in verdict:
                return False
            # 한국어가 아닌 응답 처리 (correct/yes → True)
            vl = verdict.lower()
            if any(k in vl for k in ("correct", "yes", "true")):
                return True
            if any(k in vl for k in ("incorrect", "wrong", "no", "false")):
                return False
        except Exception as e:
            print(f"    [judge retry {attempt+1}] {e}", flush=True)
            time.sleep(2)
    return None  # 판정 실패


# ── 단일 문항 평가 ──

def evaluate_one(
    chat_client: httpx.Client,
    judge_client: httpx.Client,
    judge_model: str,
    session_id: str,
    item: dict,
    dataset_type: str,  # "balanced" | "page_based"
) -> dict:
    question = item["question"]
    gt = item.get("answer", "")
    answerable = item.get("answerable", True)
    gt_source = item.get("source", "")
    gold_page = int(item.get("source_page", 0) or 0)

    # ── 챗봇 호출 (qwen) ──
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

    # ── 검색 지표 ──
    if dataset_type == "balanced":
        ret = _recall_mrr_keywords(results, gt_source, k=5)
    else:
        ret = _recall_mrr_page(results, gold_page, k=5, tol=1)

    # ── 생성 지표 (rule-based 기본) ──
    is_error = prediction.startswith("[ERROR]")
    exact = False if is_error else _em(prediction, gt) if answerable else False
    tok_f1 = 0.0 if is_error else _token_f1(prediction, gt) if answerable else 0.0

    # ── LLM Judge (exaone) ──
    judge_verdict = None  # None = 채점 불가
    if not is_error and answerable and gt:
        judge_verdict = _judge_with_llm(judge_client, judge_model, question, gt, prediction)
    elif not is_error and not answerable:
        # 대답불가 문항: 거부 응답이면 정답
        judge_verdict = _is_refusal(prediction)

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
        "exact_match": exact,
        "token_f1": round(tok_f1, 4),
        "judge_correct": judge_verdict,  # True/False/None
    }


# ── 데이터셋 요약 ──

def build_summary(records: list[dict]) -> dict:
    n = len(records)
    answerable = [r for r in records if r["answerable"]]
    unanswerable = [r for r in records if not r["answerable"]]

    # --- 검색 (answerable, retrieval 값 있는 것만) ---
    ret_items = [r for r in answerable if r["retrieval"]["recall"] is not None]
    recall5 = sum(r["retrieval"]["recall"] for r in ret_items) / len(ret_items) if ret_items else 0.0
    mrr5 = sum(r["retrieval"]["mrr"] for r in ret_items) / len(ret_items) if ret_items else 0.0

    # --- EM ---
    em_total = sum(1 for r in records if r["exact_match"])
    em_ans = sum(1 for r in answerable if r["exact_match"])

    # --- Token-F1 (answerable) ---
    avg_tok_f1 = sum(r["token_f1"] for r in answerable) / len(answerable) if answerable else 0.0

    # --- LLM Judge 기반 F1 ---
    judged = [r for r in records if r["judge_correct"] is not None]
    judged_ans = [r for r in answerable if r["judge_correct"] is not None]
    judged_unans = [r for r in unanswerable if r["judge_correct"] is not None]

    overall_f1_judge = sum(1 for r in judged if r["judge_correct"]) / len(judged) if judged else 0.0
    ans_f1_judge = sum(1 for r in judged_ans if r["judge_correct"]) / len(judged_ans) if judged_ans else 0.0
    unans_f1_judge = sum(1 for r in judged_unans if r["judge_correct"]) / len(judged_unans) if judged_unans else 0.0

    # judge 실패율
    judge_fail = sum(1 for r in records if r["judge_correct"] is None)

    return {
        "retrieval": {
            "recall_at_5": round(recall5, 4),
            "mrr_at_5": round(mrr5, 4),
            "evaluated": len(ret_items),
            "total_answerable": len(answerable),
        },
        "generation": {
            "total": n,
            "em_rate": round(em_total / n, 4) if n else 0.0,
            "answerable_em": round(em_ans / len(answerable), 4) if answerable else 0.0,
            "avg_token_f1": round(avg_tok_f1, 4),
            "judge_model": "exaone-3.0-7.8b-instruct",
            "judge_evaluated": len(judged),
            "judge_failed": judge_fail,
            "overall_f1": round(overall_f1_judge, 4),
            "answerable_f1": round(ans_f1_judge, 4),
            "unanswerable_f1": round(unans_f1_judge, 4),
            "answerable_n": len(answerable),
            "unanswerable_n": len(unanswerable),
            "answerable_judged": len(judged_ans),
            "unanswerable_judged": len(judged_unans),
        },
    }


def print_summary(dataset_name: str, summary: dict) -> None:
    r = summary["retrieval"]
    g = summary["generation"]
    print()
    print("=" * 65)
    print(f"  {dataset_name}")
    print("=" * 65)
    print(f"  [검색]  Recall@5={r['recall_at_5']:.4f}  MRR@5={r['mrr_at_5']:.4f}"
          f"  (평가={r['evaluated']}/{r['total_answerable']})")
    print()
    print(f"  [생성]  Overall-F1={g['overall_f1']:.4f}  EM={g['em_rate']:.4f}")
    print(f"          Answerable-F1={g['answerable_f1']:.4f}  (n={g['answerable_judged']}/{g['answerable_n']})")
    print(f"          Unanswerable-F1={g['unanswerable_f1']:.4f}  (n={g['unanswerable_judged']}/{g['unanswerable_n']})")
    print(f"          Avg Token-F1={g['avg_token_f1']:.4f}  Judge실패={g['judge_failed']}건")
    print("=" * 65)


# ── 메인 ──

def main() -> None:
    parser = argparse.ArgumentParser(description="BUFS 종합 평가 (qwen 생성 + exaone 채점)")
    parser.add_argument("--datasets", nargs="+", required=True, help="JSONL 데이터셋 경로 목록")
    parser.add_argument("--base-url", default="http://localhost:8000", help="챗봇 API 서버 URL")
    parser.add_argument("--judge-url", default="http://localhost:1234", help="LM Studio URL (judge)")
    parser.add_argument("--judge-model", default="exaone-3.0-7.8b-instruct", help="Judge 모델 ID")
    parser.add_argument("--output", default="reports/eval_full", help="결과 저장 디렉토리")
    parser.add_argument("--limit", type=int, default=None, help="각 데이터셋 최대 질문 수")
    args = parser.parse_args()

    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    chat_client = httpx.Client(
        base_url=args.base_url, timeout=180,
        headers={"X-Test-Mode": "1"},  # 실사용자 로그(JSONL + chat_messages DB) 오염 방지
    )
    judge_client = httpx.Client(base_url=args.judge_url, timeout=90)

    # LM Studio judge 모델 확인
    try:
        models_resp = judge_client.get("/v1/models", timeout=10)
        model_ids = [m["id"] for m in models_resp.json().get("data", [])]
        print(f"LM Studio 모델: {model_ids}")
        if args.judge_model not in model_ids:
            print(f"  ⚠ judge model '{args.judge_model}' 목록에 없음 — 계속 진행")
    except Exception as e:
        print(f"LM Studio 연결 확인 실패: {e}")

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

        # 데이터셋 타입 감지
        # - source_page 필드 있음 → page_based
        # - source가 "p.5" 형태(파일명 없이 페이지만) → page_based
        # - source가 "학사안내 p.25,27" 처럼 파일명+페이지 → balanced (keyword matching)
        first = items[0] if items else {}
        has_source_page = "source_page" in first
        source_val = first.get("source", "")
        is_page_only_source = bool(re.match(r"^p\.?\d+", source_val.strip()))
        dataset_type = "page_based" if (has_source_page or is_page_only_source) else "balanced"

        # page_based인데 source_page 필드가 없으면 source에서 추출해서 보완
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

        # 세션 생성
        try:
            sess_r = chat_client.post("/api/session", json={"lang": "ko"})
            session_id = sess_r.json()["session_id"]
        except Exception as e:
            print(f"세션 생성 실패: {e}")
            continue

        records = []
        for idx, item in enumerate(items, 1):
            print(f"  [{idx:02d}/{len(items)}] {item.get('id', '')} Q={item['question'][:40]}...", end=" ", flush=True)
            rec = evaluate_one(
                chat_client, judge_client, args.judge_model,
                session_id, item, dataset_type,
            )
            records.append(rec)
            verdict_str = "✓" if rec["judge_correct"] else ("✗" if rec["judge_correct"] is False else "?")
            ret_str = f"R@5={'HIT' if rec['retrieval']['recall'] else 'MISS'}" if rec["retrieval"]["recall"] is not None else "R@5=N/A"
            print(f"{ret_str} judge={verdict_str} tok_f1={rec['token_f1']:.2f} {rec['elapsed_s']}s", flush=True)

        summary = build_summary(records)
        print_summary(dname, summary)

        # 저장
        out_path = out_dir / f"{dname}_{timestamp}.json"
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump({"dataset": str(dataset_path), "summary": summary, "results": records},
                      f, ensure_ascii=False, indent=2)
        print(f"  저장: {out_path}")

        all_results[dname] = summary

    # 전체 요약 저장
    combined_path = out_dir / f"combined_{timestamp}.json"
    with open(combined_path, "w", encoding="utf-8") as f:
        json.dump({"timestamp": timestamp, "datasets": all_results}, f, ensure_ascii=False, indent=2)

    # 최종 요약 표 출력
    print()
    print("=" * 65)
    print("  [최종 요약]")
    print(f"  {'데이터셋':<30} {'Rec@5':>6} {'MRR@5':>6} {'Ovr-F1':>7} {'Ans-F1':>7} {'Unans-F1':>9} {'EM':>6}")
    print("  " + "-" * 63)
    for dname, s in all_results.items():
        r = s["retrieval"]
        g = s["generation"]
        print(f"  {dname:<30} {r['recall_at_5']:>6.4f} {r['mrr_at_5']:>6.4f}"
              f" {g['overall_f1']:>7.4f} {g['answerable_f1']:>7.4f}"
              f" {g['unanswerable_f1']:>9.4f} {g['em_rate']:>6.4f}")
    print("=" * 65)
    print(f"\n전체 결과: {combined_path}")


if __name__ == "__main__":
    main()
