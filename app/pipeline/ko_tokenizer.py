"""
경량 한국어 토큰화·조사 제거 유틸.

형태소 분석기(konlpy 등) 의존 없이, FAQ/그래프 키워드 매칭에 필요한 수준의
stem(어근) 추출만 담당합니다. 크기가 작아 import 비용이 없고, 결정론적.

목적: "제 2전공으로 교직신청 가능한가요?" 같은 질문에서 "전공으로" → "전공"으로
      정규화해 stopword 필터가 실제로 동작하도록 한다.

사용처:
- app.graphdb.academic_graph.search_faq
- app.pipeline.context_merger (FAQ 관련성 판정)
"""

from __future__ import annotations

import re
from typing import Iterable

# ── 토큰화 정규식 ────────────────────────────────────
_TOKEN_RE = re.compile(r"[가-힣A-Za-z]{2,}")

# ── 한국어 조사/어미 (길이 내림차순으로 매칭) ───────────
# 원칙: 형태소 정확도보다 "조사가 붙은 어절을 어근으로 되돌리는 것"에 집중.
# 잘못 잘라도 매칭에 쓰이는 데이터이므로 과감하게 strip.
_KO_SUFFIXES = tuple(sorted(
    [
        # 의문/종결 어미 (길이 큰 것 우선)
        "하나요", "있나요", "되나요", "할까요", "되는지요", "는지요", "인지요",
        "한가요", "인가요", "할까", "나요", "가요", "할까", "될까",
        "합니까", "입니까", "됩니까",
        "입니다", "습니다", "합니다", "됩니다",
        "이에요", "예요", "에요", "네요", "해요",
        "하는지", "되는지", "있는지", "인지", "인가", "는가",
        "해야", "해야죠",
        # 조사
        "으로서", "으로써", "에서도", "부터는", "까지도",
        "으로", "로써", "로서", "에서", "부터", "까지", "에게", "한테",
        "이랑", "하고",
        "에게", "한테", "께서", "에게서",
        "이나", "이며", "이고", "이든", "이던",
        "조차", "마저", "까지",
        # 짧은 조사 (마지막)
        "와", "과", "랑",
        "을", "를", "이", "가", "은", "는", "도", "만", "의", "에", "로",
        "면", "며", "니", "서", "려", "든",
    ],
    key=len,
    reverse=True,
))


def tokenize(text: str) -> list[str]:
    """단순 2글자+ 한글/영문 토큰 추출."""
    if not text:
        return []
    return _TOKEN_RE.findall(text)


def strip_suffix(token: str) -> str:
    """토큰 끝에서 가장 긴 한국어 조사/어미를 1회 제거한다."""
    for suf in _KO_SUFFIXES:
        if len(token) > len(suf) + 1 and token.endswith(suf):
            return token[: -len(suf)]
    return token


def stems(text: str) -> list[str]:
    """text → [조사 제거된 어근 토큰] (2글자+)"""
    out: list[str] = []
    for t in tokenize(text):
        s = strip_suffix(t)
        if len(s) >= 2:
            out.append(s)
    return out


def expand_compound(token: str) -> list[str]:
    """복합 명사(4글자 이상)를 2글자 bigram으로 분해해 검색 후보 확장.

    한국어에서 흔한 N글자 복합 명사("교직신청", "복수전공")를 2글자 단위 구성 부분으로 쪼개
    원래 어휘와 함께 매칭 후보에 포함시킨다. 이후 stopword 필터를 다시 거치므로
    "신청" 같은 범용 조각은 자연스럽게 제거된다.

    예: "교직신청" → ["교직신청", "교직", "직신", "신청"]
    """
    if len(token) < 4:
        return [token]
    out = [token]
    for i in range(len(token) - 1):
        bi = token[i:i + 2]
        if bi and bi not in out:
            out.append(bi)
    return out


def expand_tokens(tokens: Iterable[str], stopwords: Iterable[str]) -> set[str]:
    """tokens를 어근 확장 + stopword 제거해 매칭용 set으로 반환."""
    stop = set(stopwords)
    out: set[str] = set()
    for t in tokens:
        for part in expand_compound(t):
            if len(part) >= 2 and part not in stop:
                out.add(part)
    return out


def core_tokens(text: str, stopwords: Iterable[str]) -> list[str]:
    """stopword 제거까지 마친 핵심 토큰.

    매칭 경로에서 이 결과가 비어 있으면 "질문이 전부 범용어"라는 뜻이므로,
    호출부는 fallback(raw 토큰)을 쓸지 "매칭 불가"로 볼지 정책 결정 가능.
    """
    stop = set(stopwords)
    out: list[str] = []
    for t in tokenize(text):
        s = strip_suffix(t)
        if len(s) < 2:
            continue
        if s in stop:
            continue
        out.append(s)
    return out


# ── BM25 전용 토큰화 ──────────────────────────────────────
# BM25는 exact keyword match가 핵심이므로:
# 1) 조사 제거 (은/는/이/가/을/를 등) — strip_suffix 적용
# 2) 불용어 제거 — 검색에 무의미한 기능어 필터
# 3) 2글자 미만 토큰 제거

_BM25_STOPWORDS: frozenset[str] = frozenset({
    # 한국어 기능어/접속사/부사
    "그리고", "또한", "그래서", "하지만", "그러나", "따라서", "때문",
    "대해", "대한", "위해", "위한", "통해", "통한",
    "경우", "이상", "이하", "이내", "이후", "이전",
    "해당", "관련", "관한", "대하여", "있는", "없는", "하는", "되는",
    "것이", "수가", "바와", "점에", "중에",
    # 의문사/지시사 (질문에선 유의미하지만 문서 매칭에선 노이즈)
    "무엇", "어떤", "어떻게", "얼마", "언제", "어디",
    # 영어 불용어
    "the", "is", "at", "which", "and", "or", "of", "to", "in", "for",
    "on", "with", "as", "by", "an", "be", "this", "that", "it",
})


def tokenize_for_bm25(text: str) -> list[str]:
    """BM25 검색용 토큰화 — 조사 제거 + 불용어 필터 (명사 중심).

    파이프라인:
      1) tokenize() — 2글자+ 한글/영문 토큰 추출
      2) strip_suffix() — 한국어 조사·어미 제거 ("졸업에" → "졸업")
      3) 불용어 필터 — _BM25_STOPWORDS 제거 (기능어/접속사/의문사)
      4) 2글자 미만 제거

    복합명사 분해는 하지 않음 — BM25는 전체 토큰 exact match가 핵심이므로
    "신청기간"은 문서에서 "신청기간"이 있을 때 매칭되는 것이 더 정확.
    """
    if not text:
        return []
    tokens = tokenize(text)
    result: list[str] = []
    for t in tokens:
        s = strip_suffix(t)
        if len(s) < 2:
            continue
        if s in _BM25_STOPWORDS:
            continue
        result.append(s)
    return result


# 공용 stopword — recall 단계의 최소 필터 (어미 잔여만 유지)
# 원칙 4: 의미 토큰("신청","학점" 등)은 IDF 가중치로 자동 조절 → 하드코딩 제거
FAQ_STOPWORDS: frozenset[str] = frozenset({
    # 어미 잔여 (strip_suffix가 완벽하지 않을 때 대비)
    "하나요", "있나요", "되나요", "알려", "알려줘", "알려주세요",
    "해야", "인지", "인가", "는가",
    # 메타 단어 (질문/답변 그 자체를 지칭)
    "질문", "답변", "문의", "안내",
})


def compute_faq_idf(faq_texts: list[str]) -> dict[str, float]:
    """FAQ 코퍼스에서 IDF(역문서빈도) 가중치를 자동 계산합니다.

    원칙 1(유연한 스키마): FAQ 추가/삭제 시 자동 재계산.
    원칙 2(비용·지연 최적화): 인제스트 시 1회 실행, dict lookup O(1).
    원칙 4(하드코딩 금지): 토큰 가중치를 데이터로부터 결정.

    Returns: {토큰: IDF 가중치} (높을수록 희귀하고 구별력 높음)
    """
    from math import log

    doc_count = max(len(faq_texts), 1)
    df: dict[str, int] = {}
    for text in faq_texts:
        unique_tokens = set(stems(text))
        for token in unique_tokens:
            if len(token) >= 2:
                df[token] = df.get(token, 0) + 1

    # IDF = log(N / df) + 1.0 (smooth IDF, 최소 1.0 보장)
    return {token: log(doc_count / freq) + 1.0 for token, freq in df.items()}
