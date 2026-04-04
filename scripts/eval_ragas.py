"""
RAGAS 평가 — 표준 RAG 품질 5대 지표 측정
LM Studio (OpenAI 호환 API) + LLM-as-Judge 방식으로 직접 구현.

2-Phase 실행: 생성 모델과 평가(judge) 모델을 분리하여 자기 편향 방지.
  Phase 1: 전체 파이프라인 실행 (생성 모델로 답변 생성)
  Phase 2: 전체 평가 실행 (평가 모델로 judge)

지표 (RAGAS 논문 기준):
  1. Faithfulness       : 답변이 컨텍스트에만 근거하는가         (0.0~1.0)
  2. Answer Relevancy   : 답변이 질문 의도에 부합하는가          (0.0~1.0)
  3. Context Precision   : 검색된 컨텍스트 중 관련 비율           (0.0~1.0)
  4. Context Recall      : 정답 근거가 컨텍스트에 포함된 비율     (0.0~1.0)
  5. Answer Correctness  : 생성 답변이 정답과 일치하는 정도       (0.0~1.0)

실행:
  # 동일 모델 (기존 호환)
  .venv/Scripts/python -X utf8 scripts/eval_ragas.py --n 50

  # 생성=Qwen, 평가=EXAONE (별도 judge 모델)
  .venv/Scripts/python -X utf8 scripts/eval_ragas.py --n 50 --judge-model exaone3.5-7.8b-instruct
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
from typing import Optional

# ── Windows UTF-8 인코딩 픽스 ─────────────────────────────────────────────
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
logging.disable(logging.WARNING)

# .env 파일에서 환경변수 로드 (GOOGLE_API_KEY 등)
from dotenv import load_dotenv
load_dotenv(ROOT / ".env")

import httpx

# Claude API judge 지원
_CLAUDE_MODELS = {
    "claude-sonnet": "claude-sonnet-4-20250514",
    "claude-haiku": "claude-haiku-4-5-20251001",
    "claude-opus": "claude-opus-4-20250514",
}
_anthropic_client = None


def _get_anthropic_client():
    global _anthropic_client
    if _anthropic_client is None:
        import anthropic
        _anthropic_client = anthropic.Anthropic()  # ANTHROPIC_API_KEY env var
    return _anthropic_client


async def claude_judge(system: str, prompt: str, model_alias: str) -> str:
    """Anthropic Claude API로 judge 호출."""
    import asyncio
    client = _get_anthropic_client()
    model_id = _CLAUDE_MODELS.get(model_alias, model_alias)

    def _call():
        resp = client.messages.create(
            model=model_id,
            max_tokens=256,
            temperature=0.0,
            system=system,
            messages=[{"role": "user", "content": prompt}],
        )
        return resp.content[0].text.strip()

    return await asyncio.to_thread(_call)


# Gemini API judge 지원
_GEMINI_MODELS = {
    "gemini-flash": "gemini-2.5-flash",
    "gemini-pro": "gemini-2.5-pro",
    "gemini-flash-lite": "gemini-2.5-flash-lite",
}
_genai_client = None


def _get_genai_client():
    global _genai_client
    if _genai_client is None:
        import os
        from google import genai
        _genai_client = genai.Client(api_key=os.environ.get("GOOGLE_API_KEY"))
    return _genai_client


async def gemini_judge(system: str, prompt: str, model_alias: str) -> str:
    """Google Gemini API로 judge 호출. 429 에러 시 최대 5회 재시도."""
    import asyncio
    from google.genai import types
    client = _get_genai_client()
    model_id = _GEMINI_MODELS.get(model_alias, model_alias)

    def _call():
        for attempt in range(5):
            try:
                resp = client.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        system_instruction=system,
                        temperature=0.0,
                        max_output_tokens=256,
                        thinking_config=types.ThinkingConfig(thinking_budget=0),
                    ),
                )
                # Gemini 2.5는 thinking 파트가 있을 수 있으므로 text 파트만 추출
                text = resp.text if resp.text else ""
                if not text and resp.candidates:
                    for part in resp.candidates[0].content.parts:
                        if hasattr(part, "text") and part.text:
                            text = part.text
                            break
                return text.strip()
            except Exception as e:
                if "429" in str(e) and attempt < 4:
                    wait = min(15 * (2 ** attempt), 120)
                    print(f"    ⏳ Rate limit, {wait}s 대기 (retry {attempt+1}/4)", flush=True)
                    import time; time.sleep(wait)
                else:
                    raise

    return await asyncio.to_thread(_call)


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

# 임베딩 모델 워밍업 — segfault 방지 (lazy-load 대신 즉시 로드)
_ = store.embedder.embed_query("warmup")


# ═══════════════════════════════════════════════════════════════════════════
# LLM 호출 헬퍼
# ═══════════════════════════════════════════════════════════════════════════

async def llm_judge(
    system: str,
    prompt: str,
    client: httpx.AsyncClient,
    model: str = None,
    base_url: str = None,
) -> str:
    """LM Studio에 비스트리밍 요청을 보내고 content를 반환합니다."""
    payload = {
        "model": model or settings.llm.model,
        "messages": [
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        "stream": False,
        "max_tokens": settings.llm.max_tokens,
        "temperature": 0.0,
    }
    url = base_url or settings.llm.base_url
    resp = await client.post(f"{url}/v1/chat/completions", json=payload)
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

점수 기준:
- 1.0: 모든 주장이 컨텍스트에 근거
- 0.8~0.9: 대부분 근거하나 사소한 추론 포함 (예: 날짜 형식 변환)
- 0.5~0.7: 일부 주장이 컨텍스트에 없음
- 0.0~0.3: 핵심 정보를 지어냄 (환각)

중요: "컨텍스트에 정보가 없어 문의 바랍니다"라는 답변은 환각이 아닙니다.
이 경우 컨텍스트에 실제로 해당 정보가 없다면 1.0점입니다.

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

ANSWER_RELEVANCY_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
생성된 답변이 질문자의 의도에 얼마나 정확히 부합하는지 평가합니다.

핵심 원칙: 질문이 요구하는 핵심 정보(날짜/숫자/조건)를 정확히 포함하면 0.8 이상입니다.
부가 정보가 있더라도 핵심이 정확하면 감점하지 마세요.

점수 기준:
- 0.9~1.0: 핵심 정보를 정확히 답변하고, 한정어(학번/학기 등)도 반영
- 0.8: 핵심 정보는 정확하나 한정어 일부 누락이거나 부가 정보가 다소 포함됨
- 0.6~0.7: 핵심 정보를 부분적으로만 답변하거나 핵심 한정어 누락
- 0.4~0.5: 관련은 있으나 핵심 정보를 직접 답하지 못함 (예: "문의하세요"만 답변)
- 0.2~0.3: 질문과 다른 내용을 답변
- 0.0: 질문과 완전히 무관

예시:
Q: "수업일수 1/2선은 언제인가?" A: "수업일수 1/2선은 2026년 4월 22일입니다." → 0.9
Q: "최대 학점은?" A: "18학점입니다. 단, 교직복수전공자는 21학점..." → 0.8
Q: "장바구니 기간은?" A: "해당 정보는 확인되지 않아 문의 바랍니다." → 0.4
Q: "재수강 기준은?" A: "최고성적 A입니다." → 0.3 (질문은 "기준"이지만 답은 "최고성적")

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

CONTEXT_PRECISION_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
검색된 컨텍스트 중 질문에 답하는 데 실제로 유용한 정보의 비율을 평가합니다.

평가 기준:
- 컨텍스트에 질문과 관련된 정보가 얼마나 포함되어 있는가?
- 불필요한 노이즈가 많은가?

점수 기준 (0.0~1.0 연속값 사용):
- 1.0: 모든 컨텍스트가 질문에 직접 관련된 유용한 정보
- 0.7~0.9: 핵심 정보가 포함되어 있으나 일부 관련 없는 내용도 섞여 있음
- 0.4~0.6: 관련 정보와 무관한 노이즈가 비슷한 비율
- 0.1~0.3: 대부분 무관한 내용이며 관련 정보가 소량
- 0.0: 전혀 관련없는 컨텍스트

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

CONTEXT_RECALL_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
정답(reference)을 도출하는 데 필요한 정보가 검색된 컨텍스트에 얼마나 포함되어 있는지 평가합니다.

핵심 원칙: 정답의 핵심 사실(날짜/숫자/조건)이 컨텍스트에서 확인 가능하면 0.8 이상입니다.
표현이 다르더라도 동일한 사실이면 "포함"으로 간주하세요.

점수 기준:
- 0.9~1.0: 정답의 모든 핵심 정보가 컨텍스트에 있음
- 0.8: 핵심 정보는 있으나 세부 조건이 일부 누락
- 0.5~0.7: 핵심 정보 중 일부만 있음
- 0.2~0.4: 관련 내용은 있으나 정답 도출에 부족
- 0.0: 관련 정보 전혀 없음

예시:
정답: "2026년 3월 3일이다" 컨텍스트: "수업시작일: 2026-03-03" → 0.9 (같은 정보)
정답: "C+ 이하이다" 컨텍스트: "재수강기준성적: C+이하" → 1.0

반드시 아래 JSON 형식으로만 응답하세요:
{"score": 0.0, "reason": "한 줄 이유"}"""

ANSWER_CORRECTNESS_SYSTEM = """당신은 RAG 시스템 평가 전문가입니다.
생성된 답변이 정답(reference)과 얼마나 일치하는지 평가합니다.

평가 기준:
- 답변의 핵심 정보(날짜, 숫자, 조건)가 정답과 일치하는가?
- 부분적으로 맞는 경우 일치 비율로 점수화

점수 기준 (0.0~1.0 연속값 사용):
- 1.0: 정답의 핵심 정보(날짜, 숫자, 조건)가 모두 일치
- 0.7~0.9: 핵심 정보는 맞으나 세부 조건·시간·범위 일부 누락
- 0.4~0.6: 일부 핵심 정보만 일치하고 나머지 누락 또는 불일치
- 0.1~0.3: 관련은 있으나 핵심 정보 대부분 불일치
- 0.0: 정답과 완전히 불일치하거나 답변 없음

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
    judge_model: str = None,
    judge_url: str = None,
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
        # 외부 API judge 라우팅
        if judge_model and (judge_model.startswith("claude") or judge_model in _CLAUDE_MODELS):
            raw = await claude_judge(cfg["system"], prompt, judge_model)
        elif judge_model and (judge_model.startswith("gemini") or judge_model in _GEMINI_MODELS):
            raw = await gemini_judge(cfg["system"], prompt, judge_model)
        else:
            raw = await llm_judge(cfg["system"], prompt, client, model=judge_model, base_url=judge_url)
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
# 2-Phase 평가 루프
# ═══════════════════════════════════════════════════════════════════════════

async def evaluate_dataset(
    dataset_path: Path,
    n: int,
    metric_names: list,
    timeout: int,
    judge_model: str = None,
    judge_url: str = None,
) -> list:
    """2-Phase 평가: Phase 1(생성) → Phase 2(평가)."""
    items = []
    with dataset_path.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))
    items = items[:n]

    # ── Phase 1: 전체 파이프라인 실행 (생성 모델) ──
    print(f"\n{'─' * 65}")
    print(f"Phase 1: 파이프라인 실행 (생성 모델: {settings.llm.model})")
    print(f"{'─' * 65}")

    pipe_results = []
    for i, item in enumerate(items, 1):
        q = item["question"]
        sid = item.get("student_id")
        print(f"  [{i:02d}/{len(items)}] {item.get('id', '?')} — {q[:50]}", end="", flush=True)

        t0 = time.perf_counter()
        pipe = await run_pipeline(q, sid)
        elapsed = time.perf_counter() - t0

        if not pipe["context"]:
            print(f"  → 컨텍스트 없음 (스킵)")
            pipe_results.append({"item": item, "pipe": pipe, "skipped": True})
        else:
            print(f"  ({elapsed:.1f}s, {len(pipe['answer'])}자)")
            pipe_results.append({"item": item, "pipe": pipe, "skipped": False})

    # ── Phase 2: 전체 평가 실행 (judge 모델) ──
    judge_name = judge_model or settings.llm.model
    judge_base = judge_url or settings.llm.base_url
    print(f"\n{'─' * 65}")
    print(f"Phase 2: 메트릭 평가 (judge 모델: {judge_name})")
    print(f"{'─' * 65}")

    results = []
    async with httpx.AsyncClient(timeout=timeout) as client:
        for i, pr in enumerate(pipe_results, 1):
            item = pr["item"]
            pipe = pr["pipe"]
            q = item["question"]
            reference = item.get("answer", "")

            if pr["skipped"]:
                results.append({**item, "skipped": True})
                continue

            print(f"\n[{i:02d}/{len(items)}] {item.get('id', '?')} — {q[:50]}")
            print(f"  Intent={pipe['intent']}  answer={len(pipe['answer'])}자")

            scores = {}
            reasons = {}
            is_api_judge = judge_model and (
                judge_model.startswith("gemini") or judge_model in _GEMINI_MODELS
                or judge_model.startswith("claude") or judge_model in _CLAUDE_MODELS
            )
            for m_idx, m_name in enumerate(metric_names):
                t1 = time.perf_counter()
                score, reason = await evaluate_metric(
                    m_name, q, pipe["context"], pipe["answer"], reference, client,
                    judge_model=judge_model, judge_url=judge_url,
                )
                m_elapsed = time.perf_counter() - t1
                scores[m_name] = score
                reasons[m_name] = reason

                status = f"{score:.2f}" if score >= 0 else "ERR"
                print(f"  {METRIC_CONFIG[m_name]['kr_name']:<35}  {status}  ({m_elapsed:.0f}s)")

                # API judge 사용 시 rate limit 방지를 위한 딜레이
                if is_api_judge and m_idx < len(metric_names) - 1:
                    await asyncio.sleep(4)

            results.append({
                **item,
                "intent": pipe["intent"],
                "context_preview": pipe["context"][:200],
                "answer_preview": pipe["answer"][:200],
                **scores,
                "reasons": reasons,
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
    parser.add_argument(
        "--judge-model", type=str, default=None,
        help="평가(judge) 모델명 (미지정 시 생성 모델과 동일)",
    )
    parser.add_argument(
        "--judge-url", type=str, default=None,
        help="평가(judge) 모델 서버 URL (미지정 시 생성 모델 서버와 동일)",
    )
    args = parser.parse_args()

    dataset_path = Path(args.dataset)
    if not dataset_path.is_absolute():
        dataset_path = ROOT / dataset_path

    if not dataset_path.exists():
        print(f"[X] 데이터셋 없음: {dataset_path}")
        sys.exit(1)

    judge_model = args.judge_model
    judge_url = args.judge_url
    judge_name = judge_model or settings.llm.model
    if judge_model and (judge_model.startswith("gemini") or judge_model in _GEMINI_MODELS):
        judge_base = "Gemini API"
    elif judge_model and (judge_model.startswith("claude") or judge_model in _CLAUDE_MODELS):
        judge_base = "Anthropic API"
    else:
        judge_base = judge_url or settings.llm.base_url

    print("=" * 65)
    print("BUFS RAGAS 평가")
    print(f"시작: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"데이터셋: {dataset_path.name}  (최대 {args.n}개)")
    print(f"메트릭: {args.metrics}")
    print(f"생성 모델: {settings.llm.model} @ {settings.llm.base_url}")
    print(f"평가 모델: {judge_name} @ {judge_base}")
    print("=" * 65)

    # ── 2-Phase 평가 실행 ─────────────────────────────────────────────
    t_start = time.perf_counter()
    results = await evaluate_dataset(
        dataset_path, args.n, args.metrics, args.timeout,
        judge_model=judge_model, judge_url=judge_url,
    )
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
                "generation_model": settings.llm.model,
                "judge_model": judge_name,
                "judge_url": judge_base,
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
