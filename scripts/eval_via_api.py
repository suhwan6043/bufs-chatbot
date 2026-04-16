"""
FastAPI HTTP API 경유 164문항 eval 스크립트.

기존 eval_f1_score.py의 answer_metrics()를 재사용하여 동일 메트릭 계산.
파이프라인 직접 호출 대신 POST /api/chat → HTTP 응답으로 정답률 검증.

사용법:
  # 1. 서버 기동
  uvicorn backend.main:app --port 8000

  # 2. eval 실행
  python scripts/eval_via_api.py \\
      --base-url http://localhost:8000 \\
      --dataset data/eval/balanced_test_set.jsonl \\
      --output reports/test_session/f1_eval_api_balanced.json
"""

import argparse
import json
import sys
import time
from pathlib import Path

import httpx

# eval_f1_score.py 의 메트릭 함수 재사용
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval_f1_score import answer_metrics, correctness_flag


def load_dataset(path: str) -> list[dict]:
    """JSONL 데이터셋 로드."""
    items = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                items.append(json.loads(line))
    return items


def evaluate_one(client: httpx.Client, session_id: str, item: dict) -> dict:
    """단일 문항 HTTP 평가."""
    question = item["question"]
    gt = item.get("answer", "")
    answerable = item.get("answerable", True)

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
        elapsed = time.monotonic() - t0
    except Exception as e:
        prediction = f"[ERROR] {e}"
        intent = ""
        elapsed = time.monotonic() - t0

    # 메트릭 계산 (기존 eval_f1_score.py와 동일)
    if answerable:
        metrics = answer_metrics(prediction, gt)
    else:
        # 답변 불가 문항: prediction이 refusal인지 확인
        metrics = answer_metrics(prediction, gt) if gt else {
            "exact_match": False,
            "contains_gt": not bool(prediction.strip()) or any(
                kw in prediction for kw in ["찾을 수 없", "확인하지 못", "문의", "범위를 벗어"]
            ),
            "token_precision": 0.0, "token_recall": 0.0, "token_f1": 0.0,
        }

    return {
        "id": item.get("id", ""),
        "question": question,
        "ground_truth": gt,
        "prediction": prediction,
        "answerable": answerable,
        "intent": intent,
        "difficulty": item.get("difficulty", ""),
        "elapsed_s": round(elapsed, 2),
        **metrics,
    }


def build_summary(results: list[dict], mode: str = "contains") -> dict:
    """전체 결과 요약 (eval_f1_score.py와 동일 구조)."""
    n = len(results)
    answerable = [r for r in results if r["answerable"]]
    unanswerable = [r for r in results if not r["answerable"]]

    ans_ok = sum(1 for r in answerable if correctness_flag(r, mode))
    unans_ok = sum(1 for r in unanswerable if not r.get("contains_gt", True))
    total_ok = ans_ok + unans_ok

    return {
        "total": n,
        "correct": total_ok,
        "accuracy": round(total_ok / n, 4) if n else 0,
        "answerable": {"total": len(answerable), "correct": ans_ok,
                        "accuracy": round(ans_ok / len(answerable), 4) if answerable else 0},
        "unanswerable": {"total": len(unanswerable), "correct": unans_ok,
                          "accuracy": round(unans_ok / len(unanswerable), 4) if unanswerable else 0},
        "avg_token_f1": round(sum(r.get("token_f1", 0) for r in results) / n, 4) if n else 0,
    }


def main():
    parser = argparse.ArgumentParser(description="HTTP API 경유 eval")
    parser.add_argument("--base-url", default="http://localhost:8000")
    parser.add_argument("--dataset", required=True)
    parser.add_argument("--output", default=None)
    parser.add_argument("--mode", default="contains", choices=["contains", "exact", "token_f1"])
    args = parser.parse_args()

    items = load_dataset(args.dataset)
    print(f"Dataset: {args.dataset} ({len(items)} questions)")
    print(f"API: {args.base_url}")

    client = httpx.Client(
        base_url=args.base_url, timeout=180,
        headers={"X-Test-Mode": "1"},  # 실사용자 로그(JSONL + chat_messages DB) 오염 방지
    )

    # 세션 생성
    r = client.post("/api/session", json={"lang": "ko"})
    session_id = r.json()["session_id"]
    print(f"Session: {session_id}")
    print()

    results = []
    for i, item in enumerate(items, 1):
        result = evaluate_one(client, session_id, item)
        results.append(result)

        ok = correctness_flag(result, args.mode) if result["answerable"] else not result.get("contains_gt", True)
        marker = "PASS" if ok else "FAIL"
        print(f"[{i:02d}/{len(items)}] {marker} {result['id']:5s} tok_f1={result.get('token_f1',0):.4f} {result['elapsed_s']:.1f}s")

    # 요약
    summary = build_summary(results, args.mode)
    print()
    print("=" * 60)
    print(f"Total: {summary['correct']}/{summary['total']} = {summary['accuracy']:.1%}")
    print(f"Answerable: {summary['answerable']['correct']}/{summary['answerable']['total']} = {summary['answerable']['accuracy']:.1%}")
    print(f"Unanswerable: {summary['unanswerable']['correct']}/{summary['unanswerable']['total']} = {summary['unanswerable']['accuracy']:.1%}")
    print(f"Avg Token-F1: {summary['avg_token_f1']:.4f}")
    print("=" * 60)

    # 저장
    output_data = {"summary": summary, "results": results}
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        with open(args.output, "w", encoding="utf-8") as f:
            json.dump(output_data, f, ensure_ascii=False, indent=2)
        print(f"\nSaved: {args.output}")


if __name__ == "__main__":
    main()
