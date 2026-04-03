"""
RAGAS 평가 — 표준 RAG 품질 5대 지표 측정
LM Studio (OpenAI 호환 API) + LLM-as-Judge 방식으로 직접 구현.

지표 (RAGAS 논문 기준):
  1. Faithfulness       : 답변이 컨텍스트에만 근거하는가         (0.0~1.0)
  2. Answer Relevancy   : 답변이 질문 의도에 부합하는가          (0.0~1.0)
  3. Context Precision   : 검색된 컨텍스트 중 관련 비율           (0.0~1.0)
  4. Context Recall      : 정답 근거가 컨텍스트에 포함된 비율     (0.0~1.0)
  5. Answer Correctness  : 생성 답변이 정답과 일치하는 정도       (0.0~1.0)

실행:
  .venv/Scripts/python -X utf8 scripts/eval_ragas.py --n 5
  .venv/Scripts/python -X utf8 scripts/eval_ragas.py --n 10 --metrics faithfulness answer_relevancy context_precision context_recall answer_correctness
"""

import argparse
import asyncio
import io
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional

# ── Windows UTF-8 인코딩 픽스 ─────────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

import httpx

from app.config import settings
from app.graphdb import AcademicGraph
from app.pipeline import AnswerGenerator, ContextMerger, QueryAnalyzer, QueryRouter
from app.vectordb import ChromaStore

# ── 파이프라인 초기화 ────────────────────────────────────────────────────
store = ChromaStore()
analyzer = QueryAnalyzer()
graph = AcademicGraph()
router = QueryRouter(store, graph)
merger = ContextMerger()
generator = AnswerGenerator()

# 임베딩 모델 워밍업 — q008 segfault 방지 (lazy-load 대신 즉시 로드)
_ = store.embedder.embed_query("warmup")


# ═══════════════════════════════════════════════════════════════════════════
# LLM 호출 헬퍼
# ═══════════════════════════════════════════════════════════════════════════

async def llm_judge(system: str, prompt: str, client: httpx.AsyncClient) -> str:
    """LM Studio에 비스트리밍 요청을 보내고 content를 반환합니다."""
    payload = {
        "model": settings.llm.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": settings.llm.max_tokens,
        "temperature": 0.0,
    }
    resp = await client.post(
        f"{settings.llm.base_url}/v1/chat/completions",
        json=payload,
    )
    resp.raise_for_status()
    return resp.json()["choices"][0]["message"]["content"].strip()


def extract_json(text: str) -> Optional[dict]:
    """텍스트에서 JSON 객체를 추출합니다."""
    m = re.search(r"\{[^{}]*\}", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group())
        except json.JSONDecodeError:
            pass
    return None


def extract_float(text: str, key: str, default: float = 0.0) -> float:
    """JSON에서 float 값을 안전하게 추출합니다."""
    obj = extract_json(text)
    if obj and key in obj:
        try:
            return max(0.0, min(1.0, float(obj[key])))
        except (ValueError, TypeError):
            pass
    return default


# ═══════════════════════════════════════════════════════════════════════════
# RAGAS 5대 지표 구현
# ═══════════════════════════════════════════════════════════════════════════

FAITHFULNESS_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
생성된 답변이 오직 검색된 컨텍스트 내의 정보에만 근거하는지 평가합니다.

평가 기준:
- 답변의 모든 주장이 컨텍스트에서 확인 가능한가?
- 컨텍스트에 없는 정보를 지어냈는가?
- 1.0 = 모든 주장이 컨텍스트에 근거, 0.0 = 환각/지어낸 정보

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

ANSWER_RELEVANCY_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
생성된 답변이 질문자의 의도에 얼마나 정확히 부합하는지 평가합니다.

평가 기준:
- 답변이 질문에서 묻는 것을 정확히 답하는가?
- 불필요한 정보가 포함되어 있지 않은가?
- 1.0 = 완벽히 부합, 0.5 = 부분적 답변, 0.0 = 질문과 무관

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

CONTEXT_PRECISION_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
검색된 컨텍스트 중 질문에 답하는 데 실제로 유용한 정보의 비율을 평가합니다.

평가 기준:
- 컨텍스트에 질문과 관련된 정보가 얼마나 포함되어 있는가?
- 불필요한 노이즈가 많은가?
- 1.0 = 모든 컨텍스트가 관련있음, 0.0 = 전혀 관련없는 컨텍스트

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

CONTEXT_RECALL_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
정답(reference)을 도출하는 데 필요한 정보가 검색된 컨텍스트에 얼마나 포함되어 있는지 평가합니다.

평가 기준:
- 정답에 포함된 핵심 정보(날짜, 숫자, 조건 등)가 컨텍스트에 있는가?
- 정답을 완전히 도출할 수 있는가?
- 1.0 = 정답의 모든 근거가 컨텍스트에 있음, 0.0 = 근거 전혀 없음

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

ANSWER_CORRECTNESS_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
생성된 답변이 정답(reference)과 얼마나 일치하는지 평가합니다.

평가 기준:
- 답변의 핵심 정보(날짜, 숫자, 조건)가 정답과 일치하는가?
- 부분적으로 맞는 경우 비율로 점수화
- 1.0 = 정답과 완전 일치, 0.5 = 부분 일치, 0.0 = 완전 불일치

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

METRIC_CONFIG = {
    "faithfulness": {
        "system": FAITHFULNESS_SYSTEM,
        "prompt_template": "[검색된 컨텍스트]\n{context}\n\n[생성된 답변]\n{answer}\n\n위 답변이 컨텍스트에만 근거하는지 평가하여 JSON으로 응답하세요.",
        "needs": ["context", "answer"],
        "kr_name": "성실성 (Faithfulness)",
    },
    "answer_relevancy": {
        "system": ANSWER_RELEVANCY_SYSTEM,
        "prompt_template": "[질문]\n{question}\n\n[생성된 답변]\n{answer}\n\n위 답변이 질문 의도에 부합하는지 평가하여 JSON으로 응답하세요.",
        "needs": ["question", "answer"],
        "kr_name": "답변 관련성 (Answer Relevancy)",
    },
    "context_precision": {
        "system": CONTEXT_PRECISION_SYSTEM,
        "prompt_template": "[질문]\n{question}\n\n[정답]\n{reference}\n\n[검색된 컨텍스트]\n{context}\n\n위 컨텍스트가 질문에 답하는 데 유용한지 평가하여 JSON으로 응답하세요.",
        "needs": ["question", "reference", "context"],
        "kr_name": "컨텍스트 정밀도 (Context Precision)",
    },
    "context_recall": {
        "system": CONTEXT_RECALL_SYSTEM,
        "prompt_template": "[정답]\n{reference}\n\n[검색된 컨텍스트]\n{context}\n\n정답의 근거가 컨텍스트에 포함되어 있는지 평가하여 JSON으로 응답하세요.",
        "needs": ["reference", "context"],
        "kr_name": "컨텍스트 재현율 (Context Recall)",
    },
    "answer_correctness": {
        "system": ANSWER_CORRECTNESS_SYSTEM,
        "prompt_template": "[질문]\n{question}\n\n[정답]\n{reference}\n\n[생성된 답변]\n{answer}\n\n생성된 답변이 정답과 일치하는지 평가하여 JSON으로 응답하세요.",
        "needs": ["question", "reference", "answer"],
        "kr_name": "정답 정확도 (Answer Correctness)",
    },
}


async def evaluate_metric(
    metric_name: str,
    question: str,
    context: str,
    answer: str,
    reference: str,
    client: httpx.AsyncClient,
) -> tuple:
    """하나의 메트릭을 평가하고 (score, reason)을 반환합니다."""
    cfg = METRIC_CONFIG[metric_name]
    prompt = cfg["prompt_template"].format(
        question=question[:500],
        context=context[:800],
        answer=answer[:400],
        reference=reference[:300],
    )
    try:
        raw = await llm_judge(cfg["system"], prompt, client)
        score = extract_float(raw, "score", -1.0)
        obj = extract_json(raw)
        reason = obj.get("reason", "") if obj else ""
        return score, reason
    except Exception as e:
        return -1.0, f"평가 실패: {e}"


# ═══════════════════════════════════════════════════════════════════════════
# 파이프라인 실행
# ═══════════════════════════════════════════════════════════════════════════

async def run_pipeline(question: str, student_id: str = None) -> dict:
    """파이프라인 실행 → context, answer 반환."""
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

    # thinking marker 제거
    answer = answer.replace("\u23f3 _분석 중..._\n\n", "").replace("\x00CLEAR\x00", "")

    return {"context": context, "answer": answer.strip(), "intent": analysis.intent.value}


# ═══════════════════════════════════════════════════════════════════════════
# 메인 평가 루프
# ═══════════════════════════════════════════════════════════════════════════

async def evaluate_dataset(
    dataset_path: Path, n: int, metric_names: list, timeout: int
) -> list:
    """데이터셋 전체 평가."""
    items = []
    with dataset_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    items = items[:n]

    results = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i, item in enumerate(items, 1):
            q = item["question"]
            sid = item.get("student_id")
            reference = item.get("answer", "")
            print(f"\n[{i:02d}/{len(items)}] {item.get('id', '?')} — {q[:50]}")

            # ── 파이프라인 실행 ──
            t0 = time.perf_counter()
            pipe = await run_pipeline(q, sid)
            pipe_elapsed = time.perf_counter() - t0

            if not pipe["context"]:
                print(f"  → 컨텍스트 없음 (스킵)")
                results.append({**item, "skipped": True})
                continue

            print(f"  Intent={pipe['intent']}  answer={len(pipe['answer'])}자  ({pipe_elapsed:.1f}s)")

            # ── 각 메트릭 평가 ──
            scores = {}
            reasons = {}
            for m_name in metric_names:
                t1 = time.perf_counter()
                score, reason = await evaluate_metric(
                    m_name, q, pipe["context"], pipe["answer"], reference, client,
                )
                m_elapsed = time.perf_counter() - t1
                scores[m_name] = score
                reasons[m_name] = reason

                status = f"{score:.2f}" if score >= 0 else "ERR"
                print(f"  {METRIC_CONFIG[m_name]['kr_name']:<35}  {status}  ({m_elapsed:.0f}s)")

            total_elapsed = time.perf_counter() - t0
            results.append({
                **item,
                "intent": pipe["intent"],
                "context_preview": pipe["context"][:200],
                "answer_preview": pipe["answer"][:200],
                **scores,
                "reasons": reasons,
                "elapsed_s": round(total_elapsed, 2),
            })

    return results


def summarize(results: list, metric_names: list) -> dict:
    """유효한 결과를 집계합니다."""
    valid = [r for r in results if not r.get("skipped")]
    if not valid:
        return {}

    summary = {"n_evaluated": len(valid), "n_skipped": len(results) - len(valid)}

    for m_name in metric_names:
        vals = [r[m_name] for r in valid if r.get(m_name) is not None and r[m_name] >= 0]
        if vals:
            summary[m_name] = round(sum(vals) / len(vals), 4)
        else:
            summary[m_name] = None

    # 전체 평균
    scores = [v for k, v in summary.items() if k in metric_names and v is not None]
    summary["avg"] = round(sum(scores) / len(scores), 4) if scores else None

    return summary


async def main():
    parser = argparse.ArgumentParser(description="BUFS RAGAS 평가 스크립트")
    parser.add_argument(
        "--dataset",
        default="data/eval/user_eval_dataset_50.jsonl",
        help="평가 데이터셋 JSONL 경로",
    )
    parser.add_argument(
        "--n", type=int, default=10,
        help="평가할 최대 질문 수 (기본값: 10)",
    )
    parser.add_argument(
        "--metrics",
        nargs="+",
        default=["faithfulness", "answer_relevancy", "context_precision", "context_recall", "answer_correctness"],
        choices=list(METRIC_CONFIG.keys()),
        help="평가할 RAGAS 메트릭",
    )
    parser.add_argument(
        "--timeout", type=int, default=300,
        help="LLM 호출 타임아웃 초 (기본값: 300)",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = ROOT / dataset_path

    if not dataset_path.exists():
        print(f"[X] 데이터셋 없음: {dataset_path}")
        sys.exit(1)

    print("=" * 65)
    print("BUFS RAGAS 평가")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"데이터셋: {dataset_path.name}  (최대 {args.n}개)")
    print(f"메트릭: {args.metrics}")
    print(f"모델: {settings.llm.model} @ {settings.llm.base_url}")
    print("=" * 65)

    # ── 평가 실행 ────────────────────────────────────────────────────
    t_start = time.perf_counter()
    results = await evaluate_dataset(dataset_path, args.n, args.metrics, args.timeout)
    total_elapsed = time.perf_counter() - t_start

    summary = summarize(results, args.metrics)

    # ── 결과 출력 ────────────────────────────────────────────────────
    print(f"\n{'=' * 65}")
    print("RAGAS 평가 결과")
    print(f"{'─' * 65}")
    if summary:
        print(f"  평가 수: {summary['n_evaluated']}개  |  스킵: {summary['n_skipped']}개  |  소요: {total_elapsed:.0f}초")
        print(f"{'─' * 65}")
        for m_name in args.metrics:
            kr_name = METRIC_CONFIG[m_name]["kr_name"]
            score = summary.get(m_name)
            if score is not None:
                bar = "█" * int(score * 20) + "░" * (20 - int(score * 20))
                print(f"  {kr_name:<35}  {bar}  {score:.4f}")
            else:
                print(f"  {kr_name:<35}  {'░' * 20}  N/A")
        if summary.get("avg") is not None:
            print(f"{'─' * 65}")
            avg = summary["avg"]
            bar = "█" * int(avg * 20) + "░" * (20 - int(avg * 20))
            print(f"  {'종합 평균':<35}  {bar}  {avg:.4f}")
    print(f"{'=' * 65}")

    # ── 결과 저장 ────────────────────────────────────────────────────
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "reports"
    out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"ragas_eval_{ts}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": ts,
                "dataset": str(dataset_path),
                "model": settings.llm.model,
                "embedding": settings.embedding.model_name,
                "ragas_metrics": args.metrics,
                "n_evaluated": summary.get("n_evaluated", 0),
                "n_skipped": summary.get("n_skipped", 0),
                "elapsed_seconds": round(total_elapsed, 1),
                "summary": summary,
                "results": results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"결과 저장: {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
