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

    metrics = answer_metrics(answer, item["answer"])

    return {
        "id": item["id"],
        "difficulty": item.get("difficulty", "unknown"),
        "question": question,
        "ground_truth": item["answer"],
        "prediction": answer,
        "source": item.get("source"),
        "context": item.get("context"),
        "retrieved_vector": len(search_results.get("vector_results", [])),
        "retrieved_graph": len(search_results.get("graph_results", [])),
        "elapsed_s": round(time.perf_counter() - started, 2),
        **metrics,
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


def build_summary(records: list[dict[str, Any]], mode: str) -> dict[str, Any]:
    by_difficulty: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in records:
        by_difficulty[record["difficulty"]].append(record)

    summary = {
        "overall": {
            **classification_summary(records, mode),
            **average_summary(records),
        },
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
    print("\n" + "=" * 60)
    print("F1 evaluation summary")
    print("=" * 60)
    print(f"Mode       : {overall['mode']}")
    print(f"Total      : {overall['total']}")
    print(f"Precision  : {overall['precision']:.4f}")
    print(f"Recall     : {overall['recall']:.4f}")
    print(f"F1         : {overall['f1']:.4f}")
    print(f"Exact Match: {overall['exact_match']:.4f}")
    print(f"Contains GT: {overall['contains_gt']:.4f}")
    print(f"Avg Tok P  : {overall['avg_token_precision']:.4f}")
    print(f"Avg Tok R  : {overall['avg_token_recall']:.4f}")
    print(f"Avg Tok F1 : {overall['avg_token_f1']:.4f}")

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
    router = QueryRouter(chroma_store=chroma_store, academic_graph=academic_graph)
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

    summary = build_summary(results, args.mode)
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
