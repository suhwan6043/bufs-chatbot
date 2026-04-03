"""
RAG Triad 평가 — 맥락 적절성·성실성·답변 관련성 3축 자동 측정
LLM-as-a-Judge 방식으로 LM Studio로 채점.

실행:
  .venv/Scripts/python -X utf8 scripts/eval_rag_triad.py
  .venv/Scripts/python -X utf8 scripts/eval_rag_triad.py --dataset data/eval/user_eval_dataset_50.jsonl --n 20

지표:
  Context Relevance  : 검색된 컨텍스트가 질문에 유용한가  (목표 ≥ 0.8)
  Faithfulness       : 답변이 컨텍스트에만 근거하는가     (목표 ≥ 0.9)
  Answer Relevance   : 답변이 질문 의도에 부합하는가       (목표 ≥ 0.8)
"""

import argparse
import asyncio
import io
import json
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.CRITICAL)

from app.graphdb import AcademicGraph
from app.pipeline import AnswerGenerator, ContextMerger, QueryAnalyzer, QueryRouter
from app.vectordb import ChromaStore

# ── 파이프라인 초기화 ──────────────────────────────────────────────────────
store     = ChromaStore()
analyzer  = QueryAnalyzer()
graph     = AcademicGraph()
router    = QueryRouter(store, graph)
merger    = ContextMerger()
generator = AnswerGenerator()

# ── Judge 프롬프트 ────────────────────────────────────────────────────────
_JUDGE_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
주어진 [질문], [검색된 컨텍스트], [생성된 답변]을 보고 아래 3가지 항목을 각각 0.0~1.0 사이의 점수로 평가하세요.
반드시 JSON 형식으로만 응답하세요. 다른 설명은 쓰지 마세요.

평가 기준:
- context_relevance: 검색된 컨텍스트가 질문에 답하는 데 실제로 유용한 정보를 포함하고 있는가?
  (1.0=매우 관련있음, 0.5=일부 관련, 0.0=전혀 무관)
- faithfulness: 생성된 답변이 오직 검색된 컨텍스트 내의 정보에만 근거하는가?
  컨텍스트에 없는 정보를 지어냈다면 감점. (1.0=완전히 근거있음, 0.0=환각)
- answer_relevance: 생성된 답변이 질문자의 의도에 정확히 부합하는가?
  (1.0=완벽히 답변, 0.5=부분적 답변, 0.0=질문과 무관)

응답 형식:
{"context_relevance": 0.0, "faithfulness": 0.0, "answer_relevance": 0.0, "reason": "한 줄 이유"}
"""


async def judge_one(question: str, context: str, answer: str) -> dict:
    """LLM으로 RAG Triad 점수를 측정합니다."""
    import httpx
    from app.config import settings

    prompt = f"""[질문]
{question}

[검색된 컨텍스트]
{context[:800]}

[생성된 답변]
{answer[:400]}

위 내용을 평가하여 JSON으로만 응답하세요."""

    payload = {
        "model": settings.llm.model,
        "messages": [
            {"role": "system", "content": _JUDGE_SYSTEM},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": 2048,
        "temperature": 0.0,
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.llm.base_url}/v1/chat/completions",
                json=payload,
            )
            resp.raise_for_status()
            raw = resp.json()["choices"][0]["message"]["content"].strip()

            # JSON 추출 (마크다운 코드블록 처리)
            import re
            m = re.search(r"\{[^{}]+\}", raw, re.DOTALL)
            if m:
                scores = json.loads(m.group())
                return {
                    "context_relevance": float(scores.get("context_relevance", 0.0)),
                    "faithfulness":      float(scores.get("faithfulness", 0.0)),
                    "answer_relevance":  float(scores.get("answer_relevance", 0.0)),
                    "reason":            scores.get("reason", ""),
                }
    except Exception as e:
        pass

    return {"context_relevance": -1.0, "faithfulness": -1.0, "answer_relevance": -1.0, "reason": "judge_error"}


async def run_one(question: str, student_id: str = None) -> dict:
    """파이프라인을 실행하고 (context, answer)를 반환합니다."""
    analysis = analyzer.analyze(question)
    if student_id and not analysis.student_id:
        analysis.student_id = student_id

    search_results = router.route_and_search(question, analysis)
    merged = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
    )

    context = merged.formatted_context.strip()
    if not context:
        return {"context": "", "answer": "컨텍스트 없음", "intent": analysis.intent.value}

    if merged.direct_answer:
        answer = merged.direct_answer
    else:
        answer = await generator.generate_full(
            question=question,
            context=context,
            student_id=analysis.student_id,
            question_focus=analysis.entities.get("question_focus"),
        )

    return {"context": context, "answer": answer, "intent": analysis.intent.value}


async def evaluate_dataset(dataset_path: Path, n: int) -> list:
    items = []
    with dataset_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    items = items[:n]

    results = []
    for i, item in enumerate(items, 1):
        q = item["question"]
        sid = item.get("student_id")
        print(f"\n[{i:02d}/{len(items)}] {item.get('id','?')} — {q[:50]}")

        t0 = time.perf_counter()
        pipe = await run_one(q, sid)
        elapsed = time.perf_counter() - t0

        if not pipe["context"]:
            print(f"  → 컨텍스트 없음 (스킵)")
            results.append({**item, "skipped": True,
                             "context_relevance": None, "faithfulness": None, "answer_relevance": None})
            continue

        scores = await judge_one(q, pipe["context"], pipe["answer"])
        print(
            f"  Intent={pipe['intent']}  "
            f"CR={scores['context_relevance']:.2f}  "
            f"Faith={scores['faithfulness']:.2f}  "
            f"AR={scores['answer_relevance']:.2f}  "
            f"({elapsed:.1f}s)"
        )
        if scores["reason"]:
            print(f"  reason: {scores['reason'][:80]}")

        results.append({
            **item,
            "intent": pipe["intent"],
            "context_preview": pipe["context"][:200],
            "answer_preview": pipe["answer"][:200],
            **scores,
            "elapsed_s": round(elapsed, 2),
        })

    return results


def summarize(results: list) -> dict:
    valid = [r for r in results if not r.get("skipped") and r.get("faithfulness") is not None
             and r.get("faithfulness", -1) >= 0]
    if not valid:
        return {}
    cr   = sum(r["context_relevance"] for r in valid) / len(valid)
    faith= sum(r["faithfulness"]      for r in valid) / len(valid)
    ar   = sum(r["answer_relevance"]  for r in valid) / len(valid)
    return {
        "n_evaluated":       len(valid),
        "n_skipped":         len(results) - len(valid),
        "context_relevance": round(cr,    3),
        "faithfulness":      round(faith, 3),
        "answer_relevance":  round(ar,    3),
        "avg":               round((cr + faith + ar) / 3, 3),
        "meets_faithfulness_target": faith >= 0.9,
        "meets_cr_target":           cr    >= 0.8,
        "meets_ar_target":           ar    >= 0.8,
    }


async def main(dataset_path: Path, n: int):
    print("=" * 65)
    print("BUFS RAG Triad 평가")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"데이터셋: {dataset_path}  (최대 {n}개)")
    print("=" * 65)

    results = await evaluate_dataset(dataset_path, n)
    summary = summarize(results)

    print("\n" + "=" * 65)
    print("=== 최종 집계 ===")
    if summary:
        print(f"  평가 수: {summary['n_evaluated']}개 (스킵: {summary['n_skipped']}개)")
        print(f"  맥락 적절성  (Context Relevance):  {summary['context_relevance']:.3f}  {'✅' if summary['meets_cr_target'] else '❌'} (목표 ≥ 0.80)")
        print(f"  성실성       (Faithfulness):        {summary['faithfulness']:.3f}  {'✅' if summary['meets_faithfulness_target'] else '❌'} (목표 ≥ 0.90)")
        print(f"  답변 관련성  (Answer Relevance):    {summary['answer_relevance']:.3f}  {'✅' if summary['meets_ar_target'] else '❌'} (목표 ≥ 0.80)")
        print(f"  종합 평균:                          {summary['avg']:.3f}")
    print("=" * 65)

    # 결과 저장
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"rag_triad_{ts}.json"
    with open(out_path, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"결과 저장: {out_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="RAG Triad 평가")
    parser.add_argument("--dataset", default="data/eval/user_eval_dataset_50.jsonl",
                        help="평가 데이터셋 경로")
    parser.add_argument("--n", type=int, default=20,
                        help="평가할 최대 질문 수 (기본값: 20)")
    args = parser.parse_args()

    asyncio.run(main(Path(args.dataset), args.n))
