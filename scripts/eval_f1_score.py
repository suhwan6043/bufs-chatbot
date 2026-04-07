import argparse
import asyncio
import io
import json
import re
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any


if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.embedding import Embedder
from app.graphdb import AcademicGraph
from app.pipeline import AnswerGenerator, ContextMerger, QueryAnalyzer, QueryRouter
from app.vectordb import ChromaStore


def normalize_text(text: str) -> str:
    text = text.lower().strip()
    text = re.sub(r"\([^)]*\)", " ", text)
    text = re.sub(r"\[.*?\]", " ", text)
    text = text.replace(",", "")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def canonicalize_dates(text: str) -> str:
    text = normalize_text(text)
    # 날짜: 2026년 4월 20일 → 20260420
    text = re.sub(
        r"(20\d{2})년\s*(\d{1,2})월\s*(\d{1,2})일",
        lambda m: f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
        text,
    )
    # 시간: "18시 05분" → "18:05", "18시" → "18:00"
    text = re.sub(
        r"(\d{1,2})시\s*(\d{1,2})분",
        lambda m: f"{int(m.group(1)):02d}:{int(m.group(2)):02d}",
        text,
    )
    text = re.sub(
        r"(\d{1,2})시(?!\d)",
        lambda m: f"{int(m.group(1)):02d}:00",
        text,
    )
    # 시간: "18:00" 형태를 통일 (이미 맞는 경우 유지)
    text = re.sub(
        r"(\d{1,2}):(\d{2})",
        lambda m: f"{int(m.group(1)):02d}:{m.group(2)}",
        text,
    )
    # 성적등급 정규화: "a이다" → "a", "a등급" → "a"
    text = re.sub(r"\b([a-d][+]?)\s*(?:이다|입니다|등급)", r"\1", text)
    return text


def tokenize(text: str) -> list[str]:
    normalized = canonicalize_dates(text)
    return re.findall(r"http://\S+|https://\S+|[a-z0-9가-힣+/.:]+", normalized)


def extract_key_tokens(text: str) -> list[str]:
    normalized = canonicalize_dates(text)
    tokens: list[str] = []

    patterns = [
        r"https?://\S+",
        r"20\d{6}",
        r"20\d{2}학번",
        r"\d+학점",
        r"\d+원",
        r"\d+급",
        r"\d+과목",
        r"\d+교시",
        r"\d{2}:\d{2}",
        r"\d+시\d+분",
        r"\d+시",
        r"\d+분",
        r"\d+/\d+",
        r"[a-d][+]",
        r"\b[a-d]\b",
        r"topik",
        r"bufs",
        r"[a-z0-9.-]+\.[a-z]{2,}",
    ]

    for pattern in patterns:
        tokens.extend(re.findall(pattern, normalized))

    if not tokens:
        tokens = tokenize(text)

    deduped: list[str] = []
    seen = set()
    for token in tokens:
        if token not in seen:
            seen.add(token)
            deduped.append(token)
    return deduped


def answer_metrics(prediction: str, ground_truth: str) -> dict[str, Any]:
    pred_norm = normalize_text(prediction)
    gt_norm = normalize_text(ground_truth)

    exact_match = pred_norm == gt_norm
    pred_keys = set(extract_key_tokens(prediction))
    gt_keys = extract_key_tokens(ground_truth)
    contains_gt = bool(gt_keys) and all(token in pred_keys for token in gt_keys)

    pred_tokens = tokenize(prediction)
    gt_tokens = tokenize(ground_truth)

    pred_counter = Counter(pred_tokens)
    gt_counter = Counter(gt_tokens)
    common = pred_counter & gt_counter
    num_same = sum(common.values())

    if not pred_tokens and not gt_tokens:
        precision = recall = f1 = 1.0
    elif not pred_tokens or not gt_tokens or num_same == 0:
        precision = recall = f1 = 0.0
    else:
        precision = num_same / len(pred_tokens)
        recall = num_same / len(gt_tokens)
        f1 = 2 * precision * recall / (precision + recall)

    return {
        "exact_match": exact_match,
        "contains_gt": contains_gt,
        "token_precision": precision,
        "token_recall": recall,
        "token_f1": f1,
    }


def correctness_flag(metrics: dict[str, Any], mode: str) -> bool:
    if mode == "exact":
        return bool(metrics["exact_match"])
    if mode == "contains":
        return bool(metrics["contains_gt"])
    if mode == "token_f1":
        return metrics["token_f1"] >= 0.8
    raise ValueError(f"Unsupported mode: {mode}")


async def evaluate_one(
    item: dict[str, Any],
    analyzer: QueryAnalyzer,
    router: QueryRouter,
    merger: ContextMerger,
    generator: AnswerGenerator,
) -> dict[str, Any]:
    question = item["question"]
    started = time.perf_counter()

    analysis = analyzer.analyze(question)
    search_results = router.route_and_search(question, analysis)
    merged = merger.merge(
        vector_results=search_results.get("vector_results", []),
        graph_results=search_results.get("graph_results", []),
        question=question,
    )

    if merged.direct_answer:
        answer = merged.direct_answer.strip()
    else:
        context = merged.formatted_context.strip() or "관련 정보를 찾지 못했습니다."
        answer = (
            await generator.generate_full(
                question=question,
                context=context,
                student_id=analysis.student_id,
                question_focus=analysis.entities.get("question_focus"),
            )
        ).strip()

    # 검색 결과 페이지 번호 수집 (Recall@5, MRR@5 계산용)
    # 그래프 노드의 source_pages 메타데이터도 활용 (sp[0]만이 아닌 전체 페이지)
    all_results = search_results.get("graph_results", []) + search_results.get("vector_results", [])
    retrieved_pages = []
    for r in all_results[:5]:
        sp = r.metadata.get("source_pages", []) if r.metadata else []
        if sp:
            retrieved_pages.extend(sp)
        elif r.page_number:
            retrieved_pages.append(r.page_number)

    answerable = item.get("answerable", True)
    gold_page = item.get("source_page", 0)

    if answerable and item.get("answer"):
        metrics = answer_metrics(answer, item["answer"])
    else:
        # 대답불가 질문: 거부 응답이면 정답
        metrics = answer_metrics(answer, item.get("answer", ""))

    return {
        "id": item["id"],
        "difficulty": item.get("difficulty", "unknown"),
        "answerable": answerable,
        "question": question,
        "ground_truth": item.get("answer", ""),
        "prediction": answer,
        "source": item.get("source"),
        "context": item.get("context"),
        "source_page": gold_page,
        "retrieved_pages": retrieved_pages,
        "retrieved_vector": len(search_results.get("vector_results", [])),
        "retrieved_graph": len(search_results.get("graph_results", [])),
        "elapsed_s": round(time.perf_counter() - started, 2),
        **metrics,
    }


# ── 검색 지표 (Retrieval Metrics) ────────────────────────────

def recall_at_k(
    retrieved_pages: list[int], gold_page: int, k: int = 5, tolerance: int = 0,
) -> float:
    """상위 k개 검색 결과의 페이지에 정답이 포함되는지.
    retrieved_pages는 이미 top-k results에서 수집된 페이지 목록이므로 전체를 탐색."""
    if not gold_page:
        return 0.0
    for rp in retrieved_pages:
        if abs(rp - gold_page) <= tolerance:
            return 1.0
    return 0.0


def mrr_at_k(
    retrieved_pages: list[int], gold_page: int, k: int = 5, tolerance: int = 0,
) -> float:
    """정답 페이지가 처음 나타나는 순위의 역수.
    retrieved_pages는 이미 top-k results에서 수집된 페이지 목록이므로 전체를 탐색."""
    if not gold_page:
        return 0.0
    for i, page in enumerate(retrieved_pages):
        if abs(page - gold_page) <= tolerance:
            return 1.0 / (i + 1)
    return 0.0


def retrieval_summary(
    records: list[dict[str, Any]], k: int = 5, tolerance: int = 0,
) -> dict[str, Any]:
    """검색 지표 요약 (answerable 문항만). tolerance: 인접 페이지 허용 범위."""
    answerable = [r for r in records if r.get("answerable", True) and r.get("source_page")]
    if not answerable:
        return {"recall_at_5": 0.0, "mrr_at_5": 0.0, "n_evaluated": 0}

    recalls = [recall_at_k(r.get("retrieved_pages", []), r["source_page"], k, tolerance) for r in answerable]
    mrrs = [mrr_at_k(r.get("retrieved_pages", []), r["source_page"], k, tolerance) for r in answerable]

    return {
        "recall_at_5": round(sum(recalls) / len(recalls), 4),
        "mrr_at_5": round(sum(mrrs) / len(mrrs), 4),
        "n_evaluated": len(answerable),
    }


# ── 환각 방지 지표 (Unanswerable) ────────────────────────────

_REFUSAL_PATTERNS = [
    "학사지원팀", "문의", "확인되지 않", "찾을 수 없", "관련 정보를 찾",
    "해당 정보가 없", "답변할 수 없", "제공된 정보에",
]


def is_refusal(prediction: str) -> bool:
    """LLM 응답이 거부/문의 안내인지 판단."""
    pred_lower = prediction.lower().strip()
    if not pred_lower:
        return True
    return any(pat in pred_lower for pat in _REFUSAL_PATTERNS)


def unanswerable_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    """대답불가 문항의 환각 방지 성능."""
    unanswerable = [r for r in records if not r.get("answerable", True)]
    if not unanswerable:
        return {"unanswerable_f1": 0.0, "n_unanswerable": 0, "correct_refusals": 0, "hallucinations": 0}

    correct = sum(1 for r in unanswerable if is_refusal(r["prediction"]))
    halluc = len(unanswerable) - correct

    return {
        "unanswerable_f1": round(correct / len(unanswerable), 4),
        "n_unanswerable": len(unanswerable),
        "correct_refusals": correct,
        "hallucinations": halluc,
    }


def answerable_summary(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    """대답가능 문항만의 F1."""
    answerable = [r for r in records if r.get("answerable", True)]
    if not answerable:
        return {"answerable_f1": 0.0, "n_answerable": 0}

    correct = sum(1 for r in answerable if correctness_flag(r, mode))
    return {
        "answerable_f1": round(correct / len(answerable), 4),
        "n_answerable": len(answerable),
    }


def classification_summary(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    tp = fp = fn = 0

    for record in records:
        predicted_positive = bool(record["prediction"].strip())
        actually_correct = correctness_flag(record, mode)

        if predicted_positive and actually_correct:
            tp += 1
        elif predicted_positive and not actually_correct:
            fp += 1
            fn += 1
        elif not predicted_positive:
            fn += 1

    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0

    return {
        "mode": mode,
        "total": len(records),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(precision, 4),
        "recall": round(recall, 4),
        "f1": round(f1, 4),
    }


def average_summary(records: list[dict[str, Any]]) -> dict[str, Any]:
    if not records:
        return {
            "exact_match": 0.0,
            "contains_gt": 0.0,
            "avg_token_precision": 0.0,
            "avg_token_recall": 0.0,
            "avg_token_f1": 0.0,
        }

    n = len(records)
    return {
        "exact_match": round(sum(1 for r in records if r["exact_match"]) / n, 4),
        "contains_gt": round(sum(1 for r in records if r["contains_gt"]) / n, 4),
        "avg_token_precision": round(sum(r["token_precision"] for r in records) / n, 4),
        "avg_token_recall": round(sum(r["token_recall"] for r in records) / n, 4),
        "avg_token_f1": round(sum(r["token_f1"] for r in records) / n, 4),
    }


def build_summary(
    records: list[dict[str, Any]], mode: str, tolerance: int = 0,
) -> dict[str, Any]:
    by_difficulty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_difficulty[record["difficulty"]].append(record)

    summary = {
        "overall": {
            **classification_summary(records, mode),
            **average_summary(records),
        },
        "retrieval": retrieval_summary(records, tolerance=tolerance),
        "answerable": answerable_summary(records, mode),
        "unanswerable": unanswerable_summary(records),
        "by_difficulty": {},
    }

    for difficulty, items in sorted(by_difficulty.items()):
        summary["by_difficulty"][difficulty] = {
            **classification_summary(items, mode),
            **average_summary(items),
        }

    return summary


def print_summary(summary: dict[str, Any]) -> None:
    overall = summary["overall"]
    retrieval = summary.get("retrieval", {})
    ans = summary.get("answerable", {})
    unans = summary.get("unanswerable", {})

    print("\n" + "=" * 60)
    print("Retrieval Metrics (검색)")
    print("=" * 60)
    print(f"Recall@5   : {retrieval.get('recall_at_5', 0):.4f}")
    print(f"MRR@5      : {retrieval.get('mrr_at_5', 0):.4f}")
    print(f"Evaluated  : {retrieval.get('n_evaluated', 0)} questions (with source_page)")

    print("\n" + "=" * 60)
    print("Generation Metrics (생성)")
    print("=" * 60)
    print(f"Overall-F1      : {overall['f1']:.4f}  (n={overall['total']})")
    print(f"Exact Match     : {overall['exact_match']:.4f}")
    print(f"Answerable-F1   : {ans.get('answerable_f1', 0):.4f}  (n={ans.get('n_answerable', 0)})")
    print(f"Unanswerable-F1 : {unans.get('unanswerable_f1', 0):.4f}  "
          f"(n={unans.get('n_unanswerable', 0)}, "
          f"거부={unans.get('correct_refusals', 0)}, "
          f"환각={unans.get('hallucinations', 0)})")
    print(f"Avg Tok F1      : {overall['avg_token_f1']:.4f}")

    print("\nBy difficulty")
    for difficulty, item in summary["by_difficulty"].items():
        print(
            f"- {difficulty}: n={item['total']}  "
            f"P={item['precision']:.4f}  "
            f"R={item['recall']:.4f}  "
            f"F1={item['f1']:.4f}  "
            f"EM={item['exact_match']:.4f}  "
            f"TokF1={item['avg_token_f1']:.4f}"
        )


async def main(argv: list[str]) -> None:
    parser = argparse.ArgumentParser(description="Evaluate BUFS QA set with Precision/Recall/F1")
    parser.add_argument(
        "--dataset",
        default=str(ROOT / "data" / "eval" / "rag_eval_dataset_2026_1.jsonl"),
        help="JSONL dataset path",
    )
    parser.add_argument(
        "--mode",
        choices=["contains", "exact", "token_f1"],
        default="contains",
        help="Correctness criterion for TP/FP/FN",
    )
    parser.add_argument("--limit", type=int, default=None, help="Limit number of questions")
    parser.add_argument(
        "--tolerance", type=int, default=0,
        help="Page tolerance for retrieval metrics (0=exact, 1=±1 page)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output JSON path",
    )
    args = parser.parse_args(argv)

    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        raise FileNotFoundError(f"Dataset not found: {dataset_path}")

    items: list[dict[str, Any]] = []
    with open(dataset_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    if args.limit:
        items = items[: args.limit]

    embedder = Embedder()
    chroma_store = ChromaStore(embedder=embedder)
    academic_graph = AcademicGraph()
    analyzer = QueryAnalyzer()
    from app.vectordb.bm25_index import BM25Index
    bm25 = BM25Index(chroma_store)
    bm25.build()
    router = QueryRouter(chroma_store=chroma_store, academic_graph=academic_graph, bm25_index=bm25)
    merger = ContextMerger()
    generator = AnswerGenerator()

    if not await generator.health_check():
        raise RuntimeError("Ollama is not available")

    results = []
    for idx, item in enumerate(items, start=1):
        print(f"[{idx:02d}/{len(items)}] {item['id']} {item['question']}")
        record = await evaluate_one(item, analyzer, router, merger, generator)
        results.append(record)
        print(
            f"  exact={record['exact_match']} contains={record['contains_gt']} "
            f"tok_f1={record['token_f1']:.4f} time={record['elapsed_s']:.2f}s"
        )

    summary = build_summary(results, args.mode, tolerance=args.tolerance)
    print_summary(summary)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(
        args.output or ROOT / "reports" / f"f1_eval_{timestamp}.json"
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "dataset": str(dataset_path),
                "summary": summary,
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\nSaved: {output_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
