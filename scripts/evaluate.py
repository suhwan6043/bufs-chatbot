"""
BUFS RAG 평가 스크립트
rag_eval_dataset_100.jsonl 기반 자동 평가

평가 지표:
  - 정확도(Accuracy):  exact_match, contains_gt
  - 검색(Retrieval):   hit_rate, num_retrieved, top_vector_score
  - 속도(Latency):     retrieval_ms, generation_ms, total_ms
  - 답변 품질:         answer_ok, has_citation
  - LLM-as-Judge:     correctness(0/1), relevance(1-5), faithfulness(1-5)

데이터셋 필드:
  id, question, answer(정답), difficulty, context_type(메타), source

사용법:
    # 전체 평가 (LLM Judge 포함)
    python scripts/evaluate.py

    # 빠른 평가 (Judge 없이, Reranker 없이)
    python scripts/evaluate.py --no-judge --no-rerank

    # 난이도별
    python scripts/evaluate.py --difficulty easy --no-judge --no-rerank

    # 소량 테스트
    python scripts/evaluate.py --no-judge --no-rerank --limit 10
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
from typing import Any, Dict, List, Optional

# Windows cp949 콘솔에서 한글 깨짐 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

# 프로젝트 루트를 PATH에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.embedding import Embedder
from app.graphdb import AcademicGraph
from app.pipeline import (
    QueryAnalyzer,
    QueryRouter,
    ContextMerger,
    AnswerGenerator,
)
from app.vectordb import ChromaStore

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────────────────────────────────────
# 정답 비교 유틸리티
# ──────────────────────────────────────────────────────────────────────────────

def _normalize(text: str, fallback_year: Optional[str] = None) -> str:
    """
    비교용 정규화.
    1) 한국어 날짜 '2025년 9월 1일' → '20250901'
    2) 연도 컨텍스트 전파: 앞서 나온 4자리 연도를 'N월 N일'에 적용
       예) '2025년 8월 18일부터 8월 21일까지' → '20250818부터20250821까지'
    3) 연도가 텍스트에 없으면 fallback_year를 사용 (GT에서 추출한 연도)
       예) LLM이 '9월 3일부터 9월 5일까지'만 출력했을 때 GT 연도로 보완
    4) 서양식 날짜 '2025.09.01' / '2025-09-01' → '20250901'
    5) 전각→반각, 공백·괄호·구두점·콤마·물결 제거, 소문자
    """
    text = text.lower()
    text = text.translate(str.maketrans("（）．～·", "().~·"))

    # ① 연도 컨텍스트 확보 (텍스트 내 첫 4자리 연도, 없으면 fallback)
    year_m = re.search(r"(\d{4})년", text)
    year_ctx = year_m.group(1) if year_m else fallback_year

    # ② 완전한 한국어 날짜 (YYYY년 M월 D일) → 8자리
    text = re.sub(
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        lambda m: f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
        text,
    )

    # ③ 연도 없는 'N월 N일' → year_ctx + MM + DD  (같은 해 기준)
    if year_ctx:
        text = re.sub(
            r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일(?!\s*[년\d])",
            lambda m: f"{year_ctx}{int(m.group(1)):02d}{int(m.group(2)):02d}",
            text,
        )

    # ④ 서양식 날짜 (YYYY.MM.DD / YYYY-MM-DD) → 8자리
    text = re.sub(
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
        lambda m: f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
        text,
    )

    # ⑤ 공백·괄호·구두점·콤마·물결 제거
    text = re.sub(r"[\s().,~·:;!\[\]]", "", text)
    text = text.replace(",", "")
    return text


def _strip_weekday(text: str) -> str:
    """
    괄호로 감싼 요일 표기만 제거: (월), (목요일), （수） 등.
    '8월', '9월' 의 '월'은 건드리지 않음.
    """
    return re.sub(r"[(\(（][월화수목금토일](?:요일)?[)\)）]", "", text)


def _extract_year(text: str) -> Optional[str]:
    """텍스트에서 4자리 연도를 추출합니다 (YYYY년 또는 YYYY.MM 등)."""
    m = re.search(r"\b(20\d{2})\b", text)
    return m.group(1) if m else None


def _strip_parenthetical(norm_text: str) -> str:
    """
    정규화된 텍스트에서 괄호 안 설명(부연 설명)을 제거합니다.
    예) "직전학기신청학점이19학점에미달했을경우최대3학점까지이월가능단2022학번이전에만적용"
      → 이미 괄호가 제거된 상태이므로, 정규화 전 원문에서 처리해야 함
    """
    # 정규화 전에 괄호 안 내용 제거 (evaluate.py 내부에서만 사용하므로
    # 정규화 이전 단계에서 적용해야 함. 여기서는 이미 정규화됐으므로
    # 원문 기준으로 처리하는 별도 경로가 필요. 이 함수는 미사용.)
    return norm_text


def _extract_key_tokens(raw_gt_part: str, norm_gt_part: str) -> List[str]:
    """
    GT 파트에서 핵심 값 토큰을 추출합니다.
    전체 GT 문자열이 너무 길어 gen에서 찾을 수 없을 때 fallback으로 사용.

    추출 대상:
      - 8자리 날짜 (20250901 등)
      - 숫자+단위 (3학점, 24000원, 3시간, 45분제, 2과목 등)
      - HHMM 시간 (0945, 1530 등 — 06:00~19:59 범위만, 20xx 연도와 혼동 방지)
      - 학번 연도 (2022학번 등 — 괄호 설명 제외 부분에서만)
      - 성적 코드 (c+이하, a까지 등)

    괄호 설명 처리:
      raw_gt_part (정규화 전)에서 괄호 내 내용을 제거한 뒤 추출하여
      부연 설명의 토큰을 필수 조건으로 취급하는 오류를 방지.
    """
    # 괄호 설명 제거 후 정규화 (필수 조건 범위 한정)
    raw_main = re.sub(r"\([^)]*\)|（[^）]*）", "", raw_gt_part).strip()
    norm_main = _normalize(raw_main)

    tokens: List[str] = []

    # 1. 8자리 날짜 (이미 정규화됨)
    tokens += re.findall(
        r"202\d(?:0[1-9]|1[0-2])(?:0[1-9]|[12]\d|3[01])", norm_main
    )
    # 2. 숫자+한글 단위 (학점, 시간, 원, 과목, 주, 회, 분제, 분, 개)
    tokens += re.findall(
        r"\d+(?:학점|시간|원|과목|주|회|분제|분|개)", norm_main
    )
    # 3. 학번 연도 (2022학번 등)
    tokens += re.findall(r"202[0-9]학번", norm_main)
    # 4. HHMM 시간 패턴 (normalize 후 ":" 제거돼 4자리)
    #    06:00~19:59 범위만 — 20xx(년도), 20:xx 혼동 방지
    tokens += re.findall(r"(?:0[6-9]|1[0-9])[0-5]\d", norm_main)
    # 5. 성적 코드: c+, a+ 등이 이하/이상/까지 앞에 올 때
    tokens += re.findall(r"[a-d][+]?(?=이하|이상|까지)", norm_main)

    # 중복 제거 (순서 유지)
    seen: set = set()
    result = []
    for t in tokens:
        if t and t not in seen:
            seen.add(t)
            result.append(t)
    return result


def check_answer_match(generated: str, ground_truth: str) -> Dict[str, bool]:
    """
    생성된 답변과 정답을 비교합니다.

    전략:
      1) exact_match : 정규화 후 완전 일치 (요일 제거 버전도 시도)
      2) contains_gt : GT를 '~' 및 ', ' (목록 구분 쉼표)로 분리하여 각 구성요소 확인
         - 1차: 정규화 문자열 직접 포함 여부
         - 2차(fallback): 핵심 값 토큰(날짜·숫자+단위·시간 등)만 gen에 있는지
           ※ 괄호 안 부연 설명은 필수 조건에서 제외

    GT 연도 fallback:
      GT에 연도가 있고 생성 답변에 없을 때 GT 연도를 _normalize에 주입.
      예) LLM이 "9월 3일부터 9월 5일까지"로 답해도 정확히 비교 가능.

    쉼표 처리:
      "24,000원" 등 숫자 안 쉼표는 구분자로 취급하지 않음.
      ", " (쉼표+공백)만 목록 구분자로 분리.
    """
    if not ground_truth:
        return {"exact_match": False, "contains_gt": False}

    # GT에서 연도 추출 → 생성 답변에 연도가 없을 때 보완
    gt_year = _extract_year(ground_truth)

    # 요일 제거 후 정규화한 생성 답변 (GT 연도를 fallback으로)
    gen_loose = _normalize(_strip_weekday(generated), fallback_year=gt_year)

    # GT를 '~' 및 ', ' (쉼표+공백, 숫자 안 쉼표 제외) 구분자로 분리
    gt_no_weekday = _strip_weekday(ground_truth)
    gt_raw_parts = re.split(r"~|,\s+(?!\d{3})", gt_no_weekday)

    gt_parts_with_raw = [
        (raw.strip(), _normalize(raw, fallback_year=gt_year))
        for raw in gt_raw_parts
        if raw.strip()
    ]

    def _part_matches(raw_part: str, norm_part: str) -> bool:
        """단일 GT 파트가 gen_loose에 포함되는지 (직접 or 핵심 토큰 fallback)."""
        if not norm_part:
            return True
        # 1차: 정규화 문자열 직접 포함
        if norm_part in gen_loose:
            return True
        # 2차: 핵심 값 토큰만 gen에 있는지 (괄호 설명 제외)
        key_tokens = _extract_key_tokens(raw_part, norm_part)
        if key_tokens:
            return all(tok in gen_loose for tok in key_tokens)
        # 토큰도 없으면 직접 포함 실패 = False
        return False

    # contains: 모든 gt 구성요소가 gen에 포함 (직접 or fallback)
    contains = bool(gt_parts_with_raw) and all(
        _part_matches(r, n) for r, n in gt_parts_with_raw
    )

    # exact: 전체 정규화 완전 일치 (요일 포함 / 제거 둘 다 시도)
    exact = (
        _normalize(generated, fallback_year=gt_year) == _normalize(ground_truth)
        or gen_loose == _normalize(_strip_weekday(ground_truth))
    )

    return {
        "exact_match": exact,
        "contains_gt": contains,
    }


# ──────────────────────────────────────────────────────────────────────────────
# LLM-as-Judge  (정답 포함 버전)
# ──────────────────────────────────────────────────────────────────────────────

JUDGE_SYSTEM = "당신은 AI 응답 품질을 평가하는 전문 평가자입니다. 반드시 JSON만 출력하세요."

JUDGE_PROMPT = """아래 정보를 보고 세 가지 기준으로 평가하세요.

[질문]
{question}

[모범 답안]
{ground_truth}

[모범 답안의 근거 문장]
{golden_context}

[AI 응답]
{answer}

평가 기준:
1. 정확성(correctness): AI 응답이 [모범 답안]과 같은 내용을 담고 있는가?
   1=정답과 일치 또는 동일한 내용 포함, 0=틀리거나 관련 없음

2. 관련성(relevance): 응답이 질문에 직접 답변하는가?
   5=완전히 답변, 4=대부분 답변, 3=부분적, 2=거의 미답변, 1=전혀 무관

3. 충실성(faithfulness): 응답 내용이 [모범 답안의 근거 문장]에 근거하며 허구 정보가 없는가?
   5=완전히 기반, 4=대부분 기반, 3=일부 추정, 2=많은 추정, 1=근거 무시

반드시 아래 JSON 형식으로만 응답하세요 (다른 텍스트 없이):
{{"correctness": <0 or 1>, "relevance": <1-5>, "faithfulness": <1-5>}}"""


async def llm_judge(
    question: str,
    ground_truth: str,
    golden_context: str,
    answer: str,
) -> Dict[str, Optional[float]]:
    """Ollama로 응답 품질을 평가합니다. 실패 시 None 반환."""
    import httpx

    prompt = JUDGE_PROMPT.format(
        question=question,
        ground_truth=ground_truth,
        golden_context=golden_context[:300],
        answer=answer[:400],
    )
    payload = {
        "model": settings.ollama.model,
        "prompt": prompt,
        "system": JUDGE_SYSTEM,
        "stream": False,
        "options": {"num_ctx": 1024, "temperature": 0.0},
    }

    try:
        async with httpx.AsyncClient(timeout=60) as client:
            resp = await client.post(
                f"{settings.ollama.base_url}/api/generate", json=payload
            )
            resp.raise_for_status()
            raw = resp.json().get("response", "").strip()
            # 마크다운 코드블록 제거
            if "```" in raw:
                raw = raw.split("```")[1].replace("json", "").strip()
            # JSON만 추출 (앞뒤 텍스트 있을 경우)
            m = re.search(r"\{[^}]+\}", raw)
            if m:
                raw = m.group()
            parsed = json.loads(raw)
            return {
                "judge_correctness": float(parsed.get("correctness", 0)),
                "judge_relevance": float(parsed.get("relevance", 0)),
                "judge_faithfulness": float(parsed.get("faithfulness", 0)),
            }
    except Exception as e:
        logger.warning(f"LLM Judge 실패: {e}")
        return {
            "judge_correctness": None,
            "judge_relevance": None,
            "judge_faithfulness": None,
        }


# ──────────────────────────────────────────────────────────────────────────────
# 단일 질문 평가
# ──────────────────────────────────────────────────────────────────────────────

async def evaluate_one(
    item: Dict[str, Any],
    analyzer: QueryAnalyzer,
    router: QueryRouter,
    merger: ContextMerger,
    generator: AnswerGenerator,
    use_judge: bool = True,
) -> Dict[str, Any]:
    """한 질문에 대해 전체 파이프라인을 실행하고 지표를 수집합니다."""
    question = item["question"]
    ground_truth = item.get("answer", "")       # 실제 정답
    golden_context = item.get("context", "")    # 정답 근거 문장 (데이터셋 제공)
    difficulty = item.get("difficulty", "—")    # 없으면 "—"

    record: Dict[str, Any] = {
        "id": item["id"],
        "difficulty": difficulty,
        "question": question,
        "ground_truth": ground_truth,
        "golden_context": golden_context,
    }

    # ── 1. 쿼리 분석 ──────────────────────────────────────────────
    analysis = analyzer.analyze(question)
    record["intent"] = analysis.intent.value
    record["student_id"] = analysis.student_id

    # ── 2. 검색 ───────────────────────────────────────────────────
    t0 = time.perf_counter()
    search_results = router.route_and_search(question, analysis)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    vector_results = search_results.get("vector_results", [])
    graph_results = search_results.get("graph_results", [])

    record["retrieval_ms"] = round(retrieval_ms, 1)
    record["num_vector"] = len(vector_results)
    record["num_graph"] = len(graph_results)
    record["total_retrieved"] = len(vector_results) + len(graph_results)
    record["hit_rate"] = 1 if record["total_retrieved"] > 0 else 0
    record["top_vector_score"] = (
        round(vector_results[0].score, 4) if vector_results else None
    )

    # ── 3. 컨텍스트 병합 ──────────────────────────────────────────
    merged = merger.merge(vector_results, graph_results)
    context = merged.formatted_context
    record["context_tokens_est"] = merged.total_tokens_estimate
    record["context_empty"] = len(context.strip()) == 0

    # ── 4. 답변 생성 ──────────────────────────────────────────────
    if merged.direct_answer:
        answer = merged.direct_answer
        generation_ms = 0.0
    else:
        t1 = time.perf_counter()
        answer = await generator.generate_full(
            question=question,
            context=context if context.strip() else "관련 정보를 찾지 못했습니다.",
            student_id=analysis.student_id,
            question_focus=analysis.entities.get("question_focus"),
        )
        generation_ms = (time.perf_counter() - t1) * 1000

    record["generation_ms"] = round(generation_ms, 1)
    record["total_ms"] = round(retrieval_ms + generation_ms, 1)
    record["answer"] = answer
    record["answer_length"] = len(answer)

    # ── 5. 휴리스틱 품질 지표 ─────────────────────────────────────
    record["has_citation"] = "[출처" in answer or "[p." in answer
    record["has_uncertainty"] = (
        "확인되지 않" in answer or "정보가 없" in answer or "찾지 못" in answer
    )
    record["answer_ok"] = (
        len(answer) > 20
        and "오류가 발생" not in answer
        and "연결할 수 없" not in answer
    )

    # ── 6. 정답 일치 여부 (문자열 기반) ──────────────────────────
    if ground_truth:
        match = check_answer_match(answer, ground_truth)
        record["exact_match"] = match["exact_match"]
        record["contains_gt"] = match["contains_gt"]
    else:
        record["exact_match"] = None
        record["contains_gt"] = None

    # ── 7. LLM-as-Judge (골든 컨텍스트 기반) ─────────────────────
    if use_judge and record["answer_ok"]:
        scores = await llm_judge(question, ground_truth, golden_context, answer)
        record["judge_correctness"] = scores["judge_correctness"]
        record["judge_relevance"] = scores["judge_relevance"]
        record["judge_faithfulness"] = scores["judge_faithfulness"]
    else:
        record["judge_correctness"] = None
        record["judge_relevance"] = None
        record["judge_faithfulness"] = None

    return record


# ──────────────────────────────────────────────────────────────────────────────
# 통계 집계 및 출력
# ──────────────────────────────────────────────────────────────────────────────

def _avg(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 3) if vals else None


def _pct(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals) * 100, 1) if vals else None


def compute_summary(results: List[Dict]) -> Dict:
    """난이도별 집계 통계를 계산합니다."""
    # 실제 데이터에 존재하는 difficulty만 동적 추출 (없으면 "—" 하나만)
    difficulties = sorted(set(r.get("difficulty", "—") for r in results))
    # difficulty가 "—" 하나뿐이면 "all"만 표시
    if difficulties == ["—"]:
        group_keys = ["all"]
    else:
        group_keys = difficulties + ["all"]

    groups: Dict[str, List[Dict]] = {k: [] for k in group_keys}
    for r in results:
        diff = r.get("difficulty", "—")
        if diff in groups:
            groups[diff].append(r)
        groups["all"].append(r)

    summary = {}
    for group, items in groups.items():
        if not items:
            continue

        has_gt = [r for r in items if r.get("exact_match") is not None]

        summary[group] = {
            "n": len(items),
            # ── 정확도 (정답 있는 항목만) ──
            "exact_match_%": _pct([int(r["exact_match"]) for r in has_gt]),
            "contains_gt_%": _pct([int(r["contains_gt"]) for r in has_gt]),
            "n_with_gt": len(has_gt),
            # ── 검색 ──
            "hit_rate_%": _pct([r["hit_rate"] for r in items]),
            "avg_total_retrieved": _avg([r["total_retrieved"] for r in items]),
            "avg_num_vector": _avg([r["num_vector"] for r in items]),
            "avg_num_graph": _avg([r["num_graph"] for r in items]),
            "avg_top_vector_score": _avg(
                [r["top_vector_score"] for r in items if r["top_vector_score"] is not None]
            ),
            # ── 속도 ──
            "avg_retrieval_ms": _avg([r["retrieval_ms"] for r in items]),
            "avg_generation_ms": _avg([r["generation_ms"] for r in items]),
            "avg_total_ms": _avg([r["total_ms"] for r in items]),
            # ── 답변 품질 ──
            "answer_ok_%": _pct([int(r["answer_ok"]) for r in items]),
            "has_citation_%": _pct([int(r["has_citation"]) for r in items]),
            "has_uncertainty_%": _pct([int(r["has_uncertainty"]) for r in items]),
            "avg_answer_length": _avg([r["answer_length"] for r in items]),
            # ── LLM Judge ──
            "judge_correctness_avg": _avg(
                [r["judge_correctness"] for r in items if r.get("judge_correctness") is not None]
            ),
            "judge_relevance_avg": _avg(
                [r["judge_relevance"] for r in items if r.get("judge_relevance") is not None]
            ),
            "judge_faithfulness_avg": _avg(
                [r["judge_faithfulness"] for r in items if r.get("judge_faithfulness") is not None]
            ),
            "judge_n": sum(1 for r in items if r.get("judge_correctness") is not None),
        }
    return summary


def print_summary(summary: Dict, use_judge: bool) -> None:
    all_groups = [g for g in summary if g != "all"] + ["all"]

    print()
    print("=" * 68)
    print("  BUFS RAG 평가 결과 요약")
    print("=" * 68)

    for g in all_groups:
        s = summary.get(g)
        if not s:
            continue
        if g == "all":
            print("─" * 68)
        label = g.upper() if g != "all" else "전체"
        print(f"  [{label}]  n={s['n']}")

        # 정확도 (핵심 지표 - 맨 위)
        print(f"    {'--- 정확도 (실제 정답 기준) ---'}")
        em = f"{s['exact_match_%']}%" if s['exact_match_%'] is not None else "N/A"
        cg = f"{s['contains_gt_%']}%" if s['contains_gt_%'] is not None else "N/A"
        print(f"    {'Exact Match':<28}: {em}  (n={s['n_with_gt']})")
        print(f"    {'Contains GT':<28}: {cg}")

        # 검색
        print(f"    {'--- 검색 ---'}")
        print(f"    {'Hit Rate':<28}: {s['hit_rate_%']}%")
        print(f"    {'평균 검색 결과 수':<28}: {s['avg_total_retrieved']}  "
              f"(vec={s['avg_num_vector']}, graph={s['avg_num_graph']})")
        print(f"    {'평균 Top Vector Score':<28}: {s['avg_top_vector_score']}")

        # 속도
        print(f"    {'--- 속도 ---'}")
        print(f"    {'검색 시간':<28}: {s['avg_retrieval_ms']} ms")
        print(f"    {'생성 시간':<28}: {s['avg_generation_ms']} ms")
        print(f"    {'전체 시간':<28}: {s['avg_total_ms']} ms")

        # 답변 품질
        print(f"    {'--- 답변 품질 ---'}")
        print(f"    {'답변 정상률':<28}: {s['answer_ok_%']}%")
        print(f"    {'출처 인용률':<28}: {s['has_citation_%']}%")
        print(f"    {'평균 답변 길이':<28}: {s['avg_answer_length']} 자")

        # LLM Judge
        if use_judge and s["judge_n"] > 0:
            print(f"    {'--- LLM Judge (n={s[\"judge_n\"]}) ---'}")
            print(f"    {'Correctness (0/1)':<28}: {s['judge_correctness_avg']}")
            print(f"    {'Relevance (1-5)':<28}: {s['judge_relevance_avg']}")
            print(f"    {'Faithfulness (1-5)':<28}: {s['judge_faithfulness_avg']}")
        print()

    print("=" * 68)


def print_failures(results: List[Dict]) -> None:
    """정답 불일치 또는 검색 실패 케이스를 출력합니다."""
    # contains_gt=False이면서 정답이 있는 경우만
    wrong = [
        r for r in results
        if r.get("contains_gt") is False  # 정답 있고 불일치
    ]
    no_hit = [r for r in results if r["hit_rate"] == 0]

    if not wrong and not no_hit:
        print("\n[OK] 모든 질문 정상 답변 완료")
        return

    if no_hit:
        print(f"\n[!] 검색 실패 ({len(no_hit)}개):")
        for r in no_hit:
            print(f"  [{r['id']}] {r['question'][:45]}")

    if wrong:
        print(f"\n[!] 정답 불일치 ({len(wrong)}개)  (contains_gt=False):")
        for r in wrong:
            gt = r.get("ground_truth", "")
            ans = r.get("answer", "")[:60]
            print(f"  [{r['id']}] Q: {r['question'][:35]}")
            print(f"           GT : {gt}")
            print(f"           Gen: {ans}...")


# ──────────────────────────────────────────────────────────────────────────────
# 메인
# ──────────────────────────────────────────────────────────────────────────────

async def main(argv: List[str]) -> None:
    parser = argparse.ArgumentParser(
        description="BUFS RAG 평가 스크립트",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--dataset",
        default=str(ROOT / "data" / "eval" / "rag_eval_dataset_100.jsonl"),
        help="평가 데이터셋 JSONL 경로",
    )
    parser.add_argument(
        "--no-judge",
        action="store_true",
        help="LLM-as-Judge 비활성화 (속도 향상)",
    )
    parser.add_argument(
        "--no-rerank",
        action="store_true",
        help="Reranker 비활성화 (CPU에서 약 50초/쿼리 → 수초/쿼리로 단축)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="평가할 최대 질문 수",
    )
    parser.add_argument(
        "--difficulty",
        default=None,
        help="특정 난이도만 평가 (easy / medium / hard 등)",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="결과 JSON 저장 경로",
    )
    args = parser.parse_args(argv)

    # ── 데이터셋 로드 ──────────────────────────────────────────────
    dataset_path = Path(args.dataset)
    if not dataset_path.exists():
        fallback = Path.home() / "Downloads" / "rag_eval_dataset_100.jsonl"
        if fallback.exists():
            dataset_path = fallback
        else:
            print(f"[X] 데이터셋 파일 없음: {dataset_path}")
            sys.exit(1)

    items: List[Dict] = []
    with open(dataset_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                items.append(json.loads(line))

    if args.difficulty:
        items = [i for i in items if i.get("difficulty", "—") == args.difficulty]
    if args.limit:
        items = items[: args.limit]

    use_judge = not args.no_judge
    use_rerank = not args.no_rerank

    difficulties = sorted(set(i.get("difficulty", "—") for i in items))
    has_gt = sum(1 for i in items if i.get("answer", ""))
    has_ctx = sum(1 for i in items if i.get("context", ""))

    print(f"[*] 평가 대상    : {len(items)}개 질문")
    if difficulties != ["—"]:
        print(f"   난이도        : {difficulties}")
    print(f"   정답 있음     : {has_gt}개")
    print(f"   골든 컨텍스트 : {has_ctx}개")
    print(f"   LLM Judge    : {'ON' if use_judge else 'OFF (--no-judge)'}")
    print(f"   Reranker     : {'ON' if use_rerank else 'OFF (--no-rerank)'}")

    # ── 파이프라인 초기화 ──────────────────────────────────────────
    print("\n[+] 파이프라인 초기화 중...")
    embedder = Embedder()
    chroma_store = ChromaStore(embedder=embedder)

    n_docs = chroma_store.count()
    print(f"   ChromaDB: {n_docs}개 청크")
    if n_docs == 0:
        print("   [!] ChromaDB 비어있음. python scripts/ingest_pdf.py 실행 필요")

    graph_path = Path(settings.graph.graph_path)
    academic_graph = None
    if graph_path.exists():
        academic_graph = AcademicGraph()
        print(f"   그래프: {academic_graph.G.number_of_nodes()}노드 / "
              f"{academic_graph.G.number_of_edges()}엣지")
    else:
        print("   [!] 그래프 파일 없음 - 벡터 검색만 사용")

    analyzer = QueryAnalyzer()
    merger = ContextMerger()
    generator = AnswerGenerator()

    if not await generator.health_check():
        print("[X] Ollama 연결 실패. 'ollama serve' 실행 필요")
        sys.exit(1)
    print(f"   Ollama ({settings.ollama.model}) OK")

    # ── 워밍업 (모델 로딩 시간 측정 제외) ──────────────────────────
    warmup_label = "BGE-M3" + (" + Reranker" if use_rerank else "")
    print(f"   워밍업 ({warmup_label})...", end="", flush=True)
    t_warm = time.perf_counter()
    embedder.embed_query("워밍업 쿼리")

    warmed_reranker = None
    if use_rerank and settings.reranker.enabled:
        try:
            from app.pipeline.reranker import Reranker as _Reranker
            from app.models import SearchResult as _SR
            warmed_reranker = _Reranker()
            warmed_reranker.rerank("test", [_SR(text="test")], top_k=1)
        except Exception as e:
            print(f"\n   [!] Reranker 워밍업 실패: {e}")
            warmed_reranker = False  # False → 라우터가 재시도하지 않음
    elif not use_rerank:
        warmed_reranker = False  # type: ignore

    print(f" {time.perf_counter() - t_warm:.1f}초\n")

    router = QueryRouter(
        chroma_store=chroma_store,
        academic_graph=academic_graph,
        reranker=warmed_reranker,
    )

    # ── 평가 실행 ──────────────────────────────────────────────────
    all_results: List[Dict] = []
    eval_start = time.perf_counter()

    for idx, item in enumerate(items, 1):
        diff_label = item.get('difficulty', '')
        diff_str = f" ({diff_label})" if diff_label and diff_label != "—" else ""
        print(f"[{idx:3d}/{len(items)}] {item['id']}{diff_str} "
              f"{item['question'][:35]}...")

        try:
            rec = await evaluate_one(
                item=item,
                analyzer=analyzer,
                router=router,
                merger=merger,
                generator=generator,
                use_judge=use_judge,
            )
        except Exception as e:
            logger.error(f"평가 실패 {item['id']}: {e}", exc_info=True)
            rec = {
                "id": item["id"],
                "difficulty": item.get("difficulty", "—"),
                "question": item["question"],
                "ground_truth": item.get("answer", ""),
                "golden_context": item.get("context", ""),
                "error": str(e),
                "intent": "ERROR",
                "student_id": None,
                "retrieval_ms": 0.0,
                "num_vector": 0,
                "num_graph": 0,
                "total_retrieved": 0,
                "hit_rate": 0,
                "top_vector_score": None,
                "context_tokens_est": 0,
                "context_empty": True,
                "generation_ms": 0.0,
                "total_ms": 0.0,
                "answer": "",
                "answer_length": 0,
                "has_citation": False,
                "has_uncertainty": False,
                "answer_ok": False,
                "exact_match": None,
                "contains_gt": None,
                "judge_correctness": None,
                "judge_relevance": None,
                "judge_faithfulness": None,
            }

        all_results.append(rec)

        # 진행 상황
        hit_mark = "[+]" if rec["hit_rate"] else "[-]"
        gt_mark = ""
        if rec.get("contains_gt") is True:
            gt_mark = " GT=OK"
        elif rec.get("contains_gt") is False:
            gt_mark = " GT=NG"

        judge_str = ""
        if rec.get("judge_correctness") is not None:
            judge_str = (
                f" | J: C={rec['judge_correctness']:.0f}"
                f" R={rec['judge_relevance']:.0f}"
                f" F={rec['judge_faithfulness']:.0f}"
            )

        print(
            f"         {hit_mark} "
            f"vec={rec['num_vector']} graph={rec['num_graph']}"
            f"{gt_mark}"
            f" | {rec['total_ms']:.0f}ms"
            f" | {rec['answer_length']}자"
            f"{judge_str}"
        )

    total_elapsed = time.perf_counter() - eval_start
    print(f"\n[T] 전체 평가 시간: {total_elapsed:.1f}초")

    # ── 요약 출력 ──────────────────────────────────────────────────
    summary = compute_summary(all_results)
    print_summary(summary, use_judge)
    print_failures(all_results)

    # ── 저장 ──────────────────────────────────────────────────────
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_path = Path(
        args.output
        or (ROOT / "data" / "eval" / f"eval_results_{timestamp}.json")
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(
            {
                "timestamp": timestamp,
                "dataset": str(dataset_path),
                "model": settings.ollama.model,
                "embedding": settings.embedding.model_name,
                "reranker": settings.reranker.model_name if use_rerank else "disabled",
                "total_questions": len(items),
                "use_judge": use_judge,
                "use_rerank": use_rerank,
                "summary": summary,
                "results": all_results,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"\n[S] 결과 저장: {output_path}")


if __name__ == "__main__":
    asyncio.run(main(sys.argv[1:]))
