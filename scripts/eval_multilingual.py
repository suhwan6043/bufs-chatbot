"""
다국어(KO/EN) RAG 평가 스크립트
eval_ko.jsonl / eval_en.jsonl 기반 자동 평가

평가 지표:
  생성(Generation):
    - Overall-F1      : 토큰 수준 F1 (SQuAD 방식)
    - Answerable-F1   : fallback 제외 항목의 F1
    - NotAnswerable-F1: fallback 항목에서 모델이 "모른다"고 답한 비율

  검색(Retrieval):
    - Recall@5        : evidence_page 가 top-5 청크에 포함된 비율
    - MRR@5           : Mean Reciprocal Rank (top-5 기준)
    * evidence_page 가 비어 있는 항목은 검색 지표 계산에서 제외

사용법:
    # KO + EN 통합 실행
    python scripts/eval_multilingual.py

    # 언어 지정
    python scripts/eval_multilingual.py --lang ko
    python scripts/eval_multilingual.py --lang en

    # LLM Judge 없이 빠른 실행
    python scripts/eval_multilingual.py --no-judge

    # 소량 테스트
    python scripts/eval_multilingual.py --limit 5

데이터셋 스키마:
    id, category, question, ground_truth, key_facts,
    difficulty, lang, evidence_page
"""

import argparse
import asyncio
import collections
import io
import json
import logging
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

# Windows cp949 콘솔 한글 깨짐 방지
if sys.stdout.encoding and sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
if sys.stderr.encoding and sys.stderr.encoding.lower() != "utf-8":
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.config import settings
from app.embedding import Embedder
from app.graphdb import AcademicGraph
from app.pipeline import QueryAnalyzer, QueryRouter, ContextMerger, AnswerGenerator, ContextTranslator
from app.vectordb import ChromaStore

logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
logger = logging.getLogger(__name__)

DATA_DIR = ROOT / "data" / "eval_multilingual"
# 통일 스키마 데이터셋 우선 사용 (없으면 기존 파일로 폴백)
KO_DATASET = DATA_DIR / "eval_ko_unified.jsonl"
EN_DATASET = DATA_DIR / "eval_en_unified.jsonl"
if not KO_DATASET.exists():
    KO_DATASET = DATA_DIR / "eval_ko.jsonl"
if not EN_DATASET.exists():
    EN_DATASET = DATA_DIR / "eval_en.jsonl"

# Not-Answerable 판정 기준 (category 또는 ground_truth 포함 문자열)
_FALLBACK_CATEGORIES = {"fallback", "language_consistency"}
_FALLBACK_GT_PATTERNS = [
    "could not be confirmed",
    "확인되지 않",
    "정보가 없",
]
# 모델이 "모른다"고 답한 것으로 인정하는 패턴
_NOT_ANSWERABLE_PRED_PATTERNS = [
    # KO 거절 패턴
    "could not be confirmed",
    "확인되지 않",
    "정보가 없",
    "찾지 못",
    "문의하시기",
    "학사지원팀",
    "담당 부서",
    "직접 확인",
    # EN 거절 패턴
    "not available",
    "no information",
    "no relevant information",
    "couldn't find relevant",
    "could not find relevant",
    "please contact the academic affairs",
    "i'm sorry",
    "i couldn't find",
    "i could not find",
    "+82-51-509-5182",
]


# ─────────────────────────────────────────────────────────────────────────────
# 텍스트 정규화 (KO/EN 공통)
# ─────────────────────────────────────────────────────────────────────────────

def _normalize_ko(text: str, fallback_year: Optional[str] = None) -> str:
    """한국어 날짜·숫자를 정규화합니다."""
    text = text.lower()
    text = text.translate(str.maketrans("（）．～·", "().~·"))

    year_m = re.search(r"(\d{4})년", text)
    year_ctx = year_m.group(1) if year_m else fallback_year

    # YYYY년 M월 D일 → 8자리
    text = re.sub(
        r"(\d{4})년\s*(\d{1,2})월\s*(\d{1,2})일",
        lambda m: f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
        text,
    )
    if year_ctx:
        text = re.sub(
            r"(?<!\d)(\d{1,2})월\s*(\d{1,2})일(?!\s*[년\d])",
            lambda m: f"{year_ctx}{int(m.group(1)):02d}{int(m.group(2)):02d}",
            text,
        )
    # YYYY.MM.DD → 8자리
    text = re.sub(
        r"(\d{4})[.\-/](\d{1,2})[.\-/](\d{1,2})",
        lambda m: f"{m.group(1)}{int(m.group(2)):02d}{int(m.group(3)):02d}",
        text,
    )
    # HH:MM 시간 표기 → "HH시" / "HH시 MM분" 변환 (colon 제거 전 처리)
    # 예: "18:00" → "18시", "22:05" → "22시 05분"
    text = re.sub(
        r"(\d{1,2}):(\d{2})(?!\d)",
        lambda m: (
            f"{int(m.group(1))}시"
            if int(m.group(2)) == 0
            else f"{int(m.group(1))}시 {int(m.group(2)):02d}분"
        ),
        text,
    )
    text = re.sub(r"[\s().,~·:;!\[\]]", "", text)
    text = text.replace(",", "")
    return text


def _normalize_en(text: str) -> str:
    """영어 답변 EM 비교용 정규화 (공백 제거 — EM 전용)."""
    text = text.lower()
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    for name, num in months.items():
        text = text.replace(name, num)
    text = re.sub(r"[\s().,~·:;!\[\]]", "", text)
    text = text.replace(",", "")
    return text


def _tokenize_en(text: str) -> List[str]:
    """
    영어 F1 토큰화: 정규화 전에 단어 분리.
    월 이름 치환 → 소문자 → 알파벳/숫자 단어 추출.
    공백을 먼저 제거하면 단어가 붙어버리므로 정규화와 분리.
    """
    text = text.lower()
    months = {
        "january": "01", "february": "02", "march": "03", "april": "04",
        "may": "05", "june": "06", "july": "07", "august": "08",
        "september": "09", "october": "10", "november": "11", "december": "12",
    }
    for name, num in months.items():
        text = text.replace(name, num)
    # 구두점을 공백으로 치환 후 단어 분리
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    return [t for t in text.split() if t]


def _normalize(text: str, lang: str = "ko", fallback_year: Optional[str] = None) -> str:
    if lang == "en":
        return _normalize_en(text)
    return _normalize_ko(text, fallback_year=fallback_year)


def _extract_year(text: str) -> Optional[str]:
    m = re.search(r"\b(20\d{2})\b", text)
    return m.group(1) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# 토큰화 (F1 계산용)
# ─────────────────────────────────────────────────────────────────────────────

def _tokenize(text: str, lang: str) -> List[str]:
    """
    F1 계산을 위한 토큰화.
    - KO: 정규화 후 음절+숫자 단위
    - EN: 정규화 전 단어 분리 (_tokenize_en 사용)
    """
    if lang == "en":
        return _tokenize_en(text)
    text = _normalize(text, lang=lang)
    if lang == "en":
        return re.findall(r"[a-z0-9]+", text)
    # KO: 정규화된 문자열을 1자 단위 (syllable-level) + 연속 숫자는 그룹
    tokens = re.findall(r"\d+|[가-힣a-z]", text)
    return tokens


def _token_f1(pred: str, gt: str, lang: str) -> float:
    """SQuAD 방식 토큰 F1."""
    pred_tokens = _tokenize(pred, lang)
    gt_tokens = _tokenize(gt, lang)
    if not pred_tokens or not gt_tokens:
        return 1.0 if pred_tokens == gt_tokens else 0.0
    pred_counter = collections.Counter(pred_tokens)
    gt_counter = collections.Counter(gt_tokens)
    overlap = sum((pred_counter & gt_counter).values())
    precision = overlap / len(pred_tokens)
    recall = overlap / len(gt_tokens)
    if precision + recall == 0:
        return 0.0
    return 2 * precision * recall / (precision + recall)


# ─────────────────────────────────────────────────────────────────────────────
# Not-Answerable 판정
# ─────────────────────────────────────────────────────────────────────────────

def _is_not_answerable(item: Dict) -> bool:
    """GT 기준으로 이 항목이 Not-Answerable(fallback)인지 판정.

    통일 스키마(eval_*_unified.jsonl)는 answerable 필드를 직접 사용한다.
    구버전 데이터셋은 category / ground_truth 패턴으로 판정한다.
    """
    # 통일 스키마: answerable 필드 직접 사용
    if "answerable" in item:
        return not item["answerable"]
    # 구버전 폴백: category 또는 GT 패턴
    if item.get("category", "") in _FALLBACK_CATEGORIES:
        return True
    gt = item.get("ground_truth", "").lower()
    return any(p in gt for p in _FALLBACK_GT_PATTERNS)


def _pred_is_not_answerable(pred: str) -> bool:
    """모델 출력이 'Not Answerable'(모른다)에 해당하는지 판정."""
    pred_l = pred.lower()
    return any(p in pred_l for p in _NOT_ANSWERABLE_PRED_PATTERNS)


# ─────────────────────────────────────────────────────────────────────────────
# 검색 지표 (Recall@K, MRR@K)
# ─────────────────────────────────────────────────────────────────────────────

_GRAPH_ONLY_INTENTS = {"SCHEDULE", "ALTERNATIVE"}


def _coerce_page(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().isdigit():
        return int(value.strip())
    return None


def _result_pages(result: Any) -> set[int]:
    pages: set[int] = set()

    page = _coerce_page(getattr(result, "page_number", None))
    if page is not None:
        pages.add(page)

    metadata = getattr(result, "metadata", None) or {}
    for key in ("page_number", "source_page"):
        page = _coerce_page(metadata.get(key))
        if page is not None:
            pages.add(page)

    for key in ("source_pages", "_source_pages", "evidence_pages"):
        raw_pages = metadata.get(key)
        if isinstance(raw_pages, (list, tuple, set)):
            for raw_page in raw_pages:
                page = _coerce_page(raw_page)
                if page is not None:
                    pages.add(page)

    return pages


def _first_hit_rank(results: list, evidence_pages: set[int], k: int) -> Optional[int]:
    for rank, result in enumerate(results[:k], start=1):
        if _result_pages(result) & evidence_pages:
            return rank
    return None


def _retrieval_metrics(
    vector_results: list,
    evidence_pages: List[int],
    intent: str = "",
    graph_results: Optional[list] = None,
    k: int = 5,
) -> Dict[str, Optional[float]]:
    """
    하이브리드 Recall@k, MRR@k 계산.

    그래프 전용 intent에서 evidence_page가 없는 레거시 항목은 기존처럼 그래프 결과
    존재 여부로 계산한다. evidence_page가 있는 항목은 모든 intent에서 vector/graph
    양쪽 top-k 페이지를 독립적으로 확인한다.
    """
    graph_results = graph_results or []
    evidence_page_set = {
        page for raw_page in evidence_pages
        if (page := _coerce_page(raw_page)) is not None
    }

    if intent in _GRAPH_ONLY_INTENTS and not evidence_page_set:
        hit = bool(graph_results)
        val = 1.0 if hit else 0.0
        return {"recall_at_k": val, "mrr_at_k": val, "retrieval_source": "graph"}

    if not evidence_page_set:
        retrieval_source = "hybrid" if graph_results else "vector"
        return {"recall_at_k": None, "mrr_at_k": None, "retrieval_source": retrieval_source}

    vector_rank = _first_hit_rank(vector_results, evidence_page_set, k)
    graph_rank = _first_hit_rank(graph_results, evidence_page_set, k)
    best_rank = min(rank for rank in (vector_rank, graph_rank) if rank is not None) if (
        vector_rank is not None or graph_rank is not None
    ) else None

    recall = 1.0 if best_rank is not None else 0.0
    mrr = 1.0 / best_rank if best_rank is not None else 0.0

    if vector_rank is not None and graph_rank is not None:
        retrieval_source = "hybrid"
    elif graph_rank is not None:
        retrieval_source = "graph"
    elif vector_rank is not None:
        retrieval_source = "vector"
    else:
        retrieval_source = "hybrid" if graph_results else "vector"

    return {"recall_at_k": recall, "mrr_at_k": mrr, "retrieval_source": retrieval_source}


# ─────────────────────────────────────────────────────────────────────────────
# 단일 질문 평가
# ─────────────────────────────────────────────────────────────────────────────

async def _translate_query_to_ko(question: str) -> str:
    """
    EN 질문을 KO로 번역합니다 (Ollama API 직접 호출, 실험용).
    번역 실패 시 원문 반환.
    """
    import httpx as _httpx
    prompt = (
        "Translate the following academic question to Korean.\n"
        "Output the Korean translation only. No explanation.\n\n"
        f"English: {question}\n\n"
        "Korean:"
    )
    payload = {
        "model": settings.translator.model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.0, "num_ctx": 256},
    }
    try:
        async with _httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{settings.ollama.base_url}/api/generate", json=payload
            )
            resp.raise_for_status()
            translated = resp.json().get("response", "").strip()
            return translated if translated else question
    except Exception as e:
        logger.warning("EN→KO 쿼리 번역 실패 (원문 사용): %s", e)
        return question


async def evaluate_one(
    item: Dict[str, Any],
    analyzer: QueryAnalyzer,
    router: QueryRouter,
    merger: ContextMerger,
    translator: ContextTranslator,
    generator: AnswerGenerator,
    k: int = 5,
    query_ko: Optional[str] = None,
    skip_translate: bool = False,
) -> Dict[str, Any]:
    question = item["question"]
    ground_truth = item.get("ground_truth", "")
    lang = item.get("lang", "ko")
    evidence_pages: List[int] = item.get("evidence_page", [])
    not_answerable = _is_not_answerable(item)

    record: Dict[str, Any] = {
        "id":             item["id"],
        "category":       item.get("category", ""),
        "difficulty":     item.get("difficulty", "—"),
        "lang":           lang,
        "question":       question,
        "ground_truth":   ground_truth,
        "not_answerable": not_answerable,
    }

    # ── 1. 쿼리 분석 ──────────────────────────────────────────────────────────
    analysis = analyzer.analyze(question)
    record["intent"] = analysis.intent.value
    record["student_id"] = analysis.student_id

    # ── 2. 검색 ───────────────────────────────────────────────────────────────
    # query_ko: EN 질문을 KO로 번역한 버전 (--query-translate 플래그 시 제공)
    # 검색에만 사용; 생성(answer_generator)은 원래 EN 질문 그대로 사용
    search_question = query_ko if query_ko else question
    if query_ko:
        record["query_ko"] = query_ko
    t0 = time.perf_counter()
    search_results = router.route_and_search(search_question, analysis)
    retrieval_ms = (time.perf_counter() - t0) * 1000

    vector_results = search_results.get("vector_results", [])
    graph_results  = search_results.get("graph_results", [])

    record["retrieval_ms"]   = round(retrieval_ms, 1)
    record["num_retrieved"]  = len(vector_results) + len(graph_results)

    # ── 3. 검색 지표 (Recall@k, MRR@k) ───────────────────────────────────────
    ret_metrics = _retrieval_metrics(
        vector_results, evidence_pages,
        intent=analysis.intent.value,
        graph_results=graph_results,
        k=k,
    )
    record["recall_at_k"]       = ret_metrics["recall_at_k"]
    record["mrr_at_k"]          = ret_metrics["mrr_at_k"]
    record["retrieval_source"]  = ret_metrics["retrieval_source"]

    # ── 4. 컨텍스트 병합 ──────────────────────────────────────────────────────
    merged  = merger.merge(
        vector_results,
        graph_results,
        question=search_question,
        intent=analysis.intent,
        entities=analysis.entities,
        question_type=analysis.question_type,
    )
    record["retrieved_contexts"] = [merged.formatted_context]  # RAGAS용

    # ── 5. 컨텍스트 번역 ─────────────────────────────────────────────────────
    # EN은 skip-translate 기본: gemma4가 KO 컨텍스트를 직접 읽고 EN 답변 생성
    # --force-translate 플래그로 기존 번역 방식 강제 가능
    use_skip = (lang == "en") and not skip_translate  # skip_translate → force_translate로 의미 반전
    if use_skip:
        context = merged.formatted_context  # KO 원문 그대로
        record["context_translated"] = False
    else:
        context = await translator.translate_if_needed(
            merged.formatted_context, target_lang=lang
        )
        record["context_translated"] = (context != merged.formatted_context)

    # ── 6. 답변 생성 ──────────────────────────────────────────────────────────
    # context_lang: 번역 생략 시 "ko" 전달 → SYSTEM_PROMPT_EN_KO_CTX 사용
    context_lang = "ko" if use_skip else None

    if merged.direct_answer:
        if lang == "ko":
            pred = merged.direct_answer
        elif use_skip:
            # skip_translate 모드: KO direct_answer도 생성 모델로 번역 (일관성)
            pred = await generator.generate_full(
                question=question,
                context=merged.direct_answer,
                lang=lang,
                context_lang="ko",
            )
        else:
            # EN: 그래프 direct_answer(한국어)를 번역 후 즉시 반환 (LLM 생성 생략)
            pred = await translator.translate_if_needed(
                merged.direct_answer, target_lang=lang
            )
        generation_ms = 0.0
    else:
        t1 = time.perf_counter()
        fallback_ctx = (
            "No relevant information found."
            if lang == "en"
            else "관련 정보를 찾지 못했습니다."
        )
        pred = await generator.generate_full(
            question=question,
            context=context if context.strip() else fallback_ctx,
            student_id=analysis.student_id,
            question_focus=analysis.entities.get("question_focus"),
            lang=lang,
            context_lang=context_lang,
        )
        generation_ms = (time.perf_counter() - t1) * 1000

    record["generation_ms"] = round(generation_ms, 1)
    record["total_ms"]      = round(retrieval_ms + generation_ms, 1)
    record["pred"]          = pred

    # ── 6. 생성 지표 ──────────────────────────────────────────────────────────
    key_facts: List[str] = item.get("key_facts") or []
    if not_answerable:
        # Not-Answerable: 모델이 "모른다"고 답하면 1 (Not-Answerable F1)
        record["f1"]          = None
        record["contains_f1"] = None
        record["na_correct"]  = 1 if _pred_is_not_answerable(pred) else 0
    else:
        record["f1"]         = round(_token_f1(pred, ground_truth, lang), 4)
        record["na_correct"] = None
        # Contains-F1: key_facts 토큰이 pred에 포함되는 비율 (짧은 GT에 유리)
        if key_facts:
            pred_norm = pred.lower()
            hits = sum(1 for kf in key_facts if kf.lower() in pred_norm)
            record["contains_f1"] = round(hits / len(key_facts), 4)
        else:
            record["contains_f1"] = None

    return record


# ─────────────────────────────────────────────────────────────────────────────
# 집계 통계
# ─────────────────────────────────────────────────────────────────────────────

def _avg(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 4) if vals else None


def _pct(vals: list) -> Optional[float]:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals) * 100, 1) if vals else None


def _percentile(vals: list, p: float) -> Optional[float]:
    """p번째 퍼센타일 (0~100). 선형 보간."""
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    if len(vals) == 1:
        return round(vals[0], 1)
    idx = (p / 100) * (len(vals) - 1)
    lo, hi = int(idx), min(int(idx) + 1, len(vals) - 1)
    frac = idx - lo
    return round(vals[lo] + frac * (vals[hi] - vals[lo]), 1)


def compute_summary(results: List[Dict]) -> Dict:
    """언어×난이도 단위로 지표를 집계합니다."""
    langs = sorted(set(r["lang"] for r in results))
    diffs = sorted(set(r["difficulty"] for r in results))

    def _metrics(items: List[Dict]) -> Dict:
        answerable     = [r for r in items if not r["not_answerable"]]
        not_answerable = [r for r in items if r["not_answerable"]]
        has_ret        = [r for r in items if r["recall_at_k"] is not None]

        # retrieval_source 집계: graph 전용이면 "graph", vector 전용이면 "vector", 혼합이면 "mixed"
        sources = set(r.get("retrieval_source", "vector") for r in items)
        ret_src = sources.pop() if len(sources) == 1 else "mixed"

        return {
            "n":                    len(items),
            # ── 생성 지표 ──
            "Overall_F1":           _avg([r["f1"] for r in answerable]),
            "Answerable_F1":        _avg([r["f1"] for r in answerable]),
            "Contains_F1":          _avg([r["contains_f1"] for r in answerable if r.get("contains_f1") is not None]),
            "NotAnswerable_F1_%":   _pct([r["na_correct"] for r in not_answerable]),
            "n_answerable":         len(answerable),
            "n_not_answerable":     len(not_answerable),
            # ── 검색 지표 ──
            "Recall@5":             _avg([r["recall_at_k"] for r in has_ret]),
            "MRR@5":                _avg([r["mrr_at_k"]    for r in has_ret]),
            "n_with_evidence":      len(has_ret),
            "retrieval_source":     ret_src,
            # ── 속도 (생성 기준) ──
            "avg_retrieval_ms":     _avg([r["retrieval_ms"]  for r in items]),
            "avg_generation_ms":    _avg([r["generation_ms"] for r in items]),
            "avg_total_ms":         _avg([r["total_ms"]      for r in items]),
            "p50_generation_ms":    _percentile([r["generation_ms"] for r in items], 50),
            "p90_generation_ms":    _percentile([r["generation_ms"] for r in items], 90),
            "p95_generation_ms":    _percentile([r["generation_ms"] for r in items], 95),
            "min_generation_ms":    _percentile([r["generation_ms"] for r in items],  0),
            "max_generation_ms":    _percentile([r["generation_ms"] for r in items], 100),
        }

    summary: Dict[str, Any] = {}

    # 전체
    summary["all"] = _metrics(results)

    # 언어별
    for lang in langs:
        lang_items = [r for r in results if r["lang"] == lang]
        summary[f"lang:{lang}"] = _metrics(lang_items)
        # 언어×난이도
        for diff in diffs:
            sub = [r for r in lang_items if r["difficulty"] == diff]
            if sub:
                summary[f"lang:{lang}|diff:{diff}"] = _metrics(sub)

    # 카테고리별 (전체)
    categories = sorted(set(r["category"] for r in results))
    for cat in categories:
        sub = [r for r in results if r["category"] == cat]
        summary[f"cat:{cat}"] = _metrics(sub)

    return summary


# ─────────────────────────────────────────────────────────────────────────────
# 출력 포맷
# ─────────────────────────────────────────────────────────────────────────────

def _fmt(v: Optional[float], pct: bool = False) -> str:
    if v is None:
        return "  N/A"
    if pct:
        return f"{v:5.1f}%"
    return f"{v:.4f}"


def print_summary(summary: Dict, k: int = 5) -> None:
    # ── 헤더 그룹만 출력 (all, lang:ko, lang:en) ──
    main_keys = ["all"] + [k_ for k_ in summary if k_.startswith("lang:") and "|" not in k_]
    print()
    print("=" * 95)
    print(f"{'Group':<25} {'n':>4}  {'Ovr-F1':>7}  {'Ans-F1':>7}  "
          f"{'Cnt-F1':>7}  {'NA-F1':>7}  {'Rec@5':>7}  {'MRR@5':>7}")
    print("-" * 95)
    for key in main_keys:
        m = summary[key]
        print(
            f"{key:<25} {m['n']:>4}  "
            f"{_fmt(m['Overall_F1']):>7}  "
            f"{_fmt(m['Answerable_F1']):>7}  "
            f"{_fmt(m.get('Contains_F1')):>7}  "
            f"{_fmt(m['NotAnswerable_F1_%'], pct=True):>7}  "
            f"{_fmt(m['Recall@5']):>7}  "
            f"{_fmt(m['MRR@5']):>7}"
        )
    print("=" * 95)

    # ── 언어×난이도 세부 ──
    diff_keys = [k_ for k_ in summary if "|diff:" in k_]
    if diff_keys:
        print()
        print(f"{'Group':<35} {'n':>4}  {'Ovr-F1':>7}  {'Rec@5':>7}  {'MRR@5':>7}")
        print("-" * 65)
        for key in sorted(diff_keys):
            m = summary[key]
            print(
                f"{key:<35} {m['n']:>4}  "
                f"{_fmt(m['Overall_F1']):>7}  "
                f"{_fmt(m['Recall@5']):>7}  "
                f"{_fmt(m['MRR@5']):>7}"
            )
        print("-" * 65)

    # ── 카테고리 세부 ──
    cat_keys = [k_ for k_ in summary if k_.startswith("cat:")]
    if cat_keys:
        print()
        print(f"{'Category':<35} {'n':>4}  {'Ovr-F1':>7}  {'Rec@5':>7}  {'src':>6}")
        print("-" * 65)
        for key in sorted(cat_keys):
            m = summary[key]
            src = m.get("retrieval_source", "mixed")
            print(
                f"{key:<35} {m['n']:>4}  "
                f"{_fmt(m['Overall_F1']):>7}  "
                f"{_fmt(m['Recall@5']):>7}  "
                f"{src:>6}"
            )
        print("-" * 65)

    # ── 생성 지연시간 (generation latency) ──
    print()
    print("── Generation Latency (ms) " + "─" * 63)
    print(f"{'Group':<25} {'avg':>8}  {'p50':>8}  {'p90':>8}  {'p95':>8}  {'min':>8}  {'max':>8}")
    print("-" * 90)

    def _ms(v: Optional[float]) -> str:
        return f"{v:8.1f}" if v is not None else "     N/A"

    latency_keys = main_keys
    for key in latency_keys:
        m = summary[key]
        print(
            f"{key:<25} "
            f"{_ms(m['avg_generation_ms'])}  "
            f"{_ms(m['p50_generation_ms'])}  "
            f"{_ms(m['p90_generation_ms'])}  "
            f"{_ms(m['p95_generation_ms'])}  "
            f"{_ms(m['min_generation_ms'])}  "
            f"{_ms(m['max_generation_ms'])}"
        )
    print("─" * 90)


# ─────────────────────────────────────────────────────────────────────────────
# 메인 루프
# ─────────────────────────────────────────────────────────────────────────────

async def run(args: argparse.Namespace) -> None:
    # ── 데이터셋 로딩 ──────────────────────────────────────────────────────────
    datasets: List[Path] = []
    lang_filter = getattr(args, "lang", None)
    if lang_filter in (None, "ko"):
        datasets.append(KO_DATASET)
    if lang_filter in (None, "en"):
        datasets.append(EN_DATASET)

    items: List[Dict] = []
    for path in datasets:
        if not path.exists():
            logger.warning("데이터셋 없음: %s", path)
            continue
        # 파일명 기준으로 lang 강제 지정 (파일 내 필드 누락 방어)
        file_lang = "ko" if "ko" in path.stem else "en" if "en" in path.stem else None
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                item = json.loads(line)
                if file_lang and not item.get("lang"):
                    item["lang"] = file_lang
                items.append(item)

    if args.limit:
        items = items[: args.limit]

    if not items:
        print("평가할 데이터가 없습니다.")
        return

    print(f"평가 시작: {len(items)}개 질문 (KO/EN 혼합)")

    # ── 파이프라인 초기화 ──────────────────────────────────────────────────────
    embedder   = Embedder()
    store      = ChromaStore(embedder)
    graph      = AcademicGraph()
    analyzer   = QueryAnalyzer()
    router     = QueryRouter(store, graph)
    merger     = ContextMerger()
    translator = ContextTranslator()
    generator  = AnswerGenerator()

    # 생성 모델 오버라이드 (--model 플래그)
    override_model = getattr(args, "model", None)
    if override_model:
        generator.model = override_model
        print(f"생성 모델 오버라이드: {override_model}")

    use_query_translate = getattr(args, "query_translate", False)
    use_skip_translate  = getattr(args, "skip_translate", False)

    if use_skip_translate:
        print("번역 생략 모드: KO 컨텍스트 → Qwen 직접 EN 생성 (SYSTEM_PROMPT_EN_KO_CTX)")

    # ── 평가 루프 ─────────────────────────────────────────────────────────────
    results: List[Dict] = []
    for i, item in enumerate(items, 1):
        try:
            # EN 질문을 KO로 번역하여 검색 품질 개선 (--query-translate 플래그)
            query_ko = None
            if use_query_translate and item.get("lang") == "en":
                query_ko = await _translate_query_to_ko(item["question"])
                logger.debug("EN→KO 쿼리 번역: %r → %r", item["question"], query_ko)
            rec = await evaluate_one(
                item, analyzer, router, merger, translator, generator, k=5,
                query_ko=query_ko,
                skip_translate=use_skip_translate,
            )
            results.append(rec)
            lang  = rec["lang"]
            f1    = rec.get("f1")
            rec5  = rec.get("recall_at_k")
            na    = rec.get("na_correct")

            status_parts = [f"[{i:3d}/{len(items)}]", f"[{lang}]", rec["id"]]
            if f1 is not None:
                status_parts.append(f"F1={f1:.3f}")
            if rec5 is not None:
                status_parts.append(f"Rec@5={'✓' if rec5 else '✗'}")
            if na is not None:
                status_parts.append(f"NA={'✓' if na else '✗'}")

            print("  ".join(status_parts))

        except Exception as e:
            logger.error("평가 실패 [%s]: %s", item.get("id"), e)

    if not results:
        print("평가 결과 없음")
        return

    # ── 집계 및 출력 ──────────────────────────────────────────────────────────
    summary = compute_summary(results)
    print_summary(summary)

    # ── 결과 저장 ─────────────────────────────────────────────────────────────
    out_dir = ROOT / "evaluation" / "results"
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    lang_tag   = lang_filter or "all"
    model_tag  = getattr(args, "model", None)
    model_slug = model_tag.replace(":", "-").replace(".", "") if model_tag else ""
    skip_slug  = "_notrans" if getattr(args, "skip_translate", False) else ""
    suffix     = f"_{model_slug}{skip_slug}" if (model_slug or skip_slug) else ""
    out_path   = out_dir / f"eval_multilingual_{lang_tag}{suffix}_{ts}.json"

    with out_path.open("w", encoding="utf-8") as f:
        json.dump(
            {"summary": summary, "results": results},
            f,
            ensure_ascii=False,
            indent=2,
        )
    print(f"\n결과 저장: {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description="다국어 RAG 평가")
    parser.add_argument(
        "--lang", choices=["ko", "en"], default=None,
        help="평가 언어 지정 (기본: KO+EN 모두)",
    )
    parser.add_argument(
        "--limit", type=int, default=None,
        help="평가할 최대 항목 수",
    )
    parser.add_argument(
        "--no-judge", action="store_true",
        help="LLM Judge 생략 (미사용, 호환성 유지)",
    )
    parser.add_argument(
        "--query-translate", action="store_true",
        help="EN 질문을 KO로 번역하여 vector 검색 (실험적, Ollama 필요)",
    )
    parser.add_argument(
        "--model", type=str, default=None,
        help="생성 모델 오버라이드 (예: qwen2.5:7b). 기본값: 설정 파일 모델",
    )
    parser.add_argument(
        "--skip-translate", action="store_true",
        help="EN 쿼리에서 KO→EN 번역 생략. Qwen이 KO 컨텍스트를 직접 읽고 EN 답변 생성.",
    )
    args = parser.parse_args()
    asyncio.run(run(args))


if __name__ == "__main__":
    main()
