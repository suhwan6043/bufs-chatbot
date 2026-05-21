"""grounding.is_grounded() 단위 테스트.

검증:
  - fact 0개 direct_answer → (True, [])
  - 빈 direct_answer → (True, [])
  - 빈 chunks + fact 있는 answer → (False, [...])
  - chunks에 fact 실존 → (True, [])
  - chunks에 fact 없음 → (False, [...])
  - 숫자 정규화 (전화번호 포맷 변종) 매칭
  - 공백 정규화 ("120 학점" ↔ "120학점") 매칭
"""

from __future__ import annotations

from typing import Optional

import pytest

from app.models import SearchResult
from app.pipeline.grounding import is_grounded, FACT_PATTERNS


def _chunk(text: str) -> SearchResult:
    return SearchResult(text=text, score=1.0, source="test", metadata={})


# ── 기본 동작 ──────────────────────────────────────────────────────

def test_empty_answer_passes():
    grounded, missing = is_grounded("", [_chunk("뭐든")])
    assert grounded is True
    assert missing == []


def test_whitespace_only_answer_passes():
    grounded, missing = is_grounded("   \n  ", [_chunk("뭐든")])
    assert grounded is True


def test_no_fact_tokens_passes():
    """관련 없는 단문 ('관련 정보를 찾을 수 없습니다.') → fact 토큰 0개 → 통과."""
    grounded, missing = is_grounded(
        "관련 정보를 찾을 수 없습니다.",
        [_chunk("뭐든")],
    )
    assert grounded is True
    assert missing == []


# ── fact 매칭 ──────────────────────────────────────────────────────

def test_credit_in_chunks_passes():
    answer = "졸업학점은 120학점입니다."
    chunks = [_chunk("학사안내 ... 졸업학점은 120학점 ...")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is True, f"missing: {missing}"


def test_credit_not_in_chunks_fails():
    """답변에 '999학점' (날조) 있는데 chunks에 없음 → reject."""
    answer = "졸업학점은 999학점입니다."
    chunks = [_chunk("학사안내 ... 졸업학점은 120학점 ...")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is False
    assert any("credit:999" in m for m in missing)


def test_empty_chunks_with_fact_fails():
    answer = "졸업학점은 120학점입니다."
    grounded, missing = is_grounded(answer, [])
    assert grounded is False
    assert len(missing) >= 1


def test_cohort_mismatch_substring_limitation():
    """알려진 한계: substring 매칭은 cohort 오매칭을 못 잡음.

    답변이 '2024학번 졸업학점 130학점' 환각인데, chunks에 다른 학번의
    '130학점'이 있으면 grounded 통과해버린다. 이건 의도된 한계
    (grounding.py docstring 참조 — AnswerUnit + PR #25 rerank gate가 책임).
    """
    answer = "2024학번 졸업학점은 130학점입니다."
    # chunks에 2024학번이 없고, 130학점은 다른 맥락(2022학번)에 있음
    chunks = [_chunk("2022학번 졸업학점은 130학점 ... 2024학번 졸업학점은 120학점")]
    grounded, _ = is_grounded(answer, chunks)
    # substring 매칭은 통과시킴 — 본 게이트의 한계 명시
    assert grounded is True, (
        "cohort 오매칭은 substring 매칭이 잡지 못하는 것이 의도된 한계."
    )


# ── 정규화 ──────────────────────────────────────────────────────

def test_whitespace_normalization():
    """답변 '120 학점' (공백 포함)이 chunks의 '120학점'에 매칭."""
    answer = "졸업학점은 120 학점입니다."
    chunks = [_chunk("졸업학점은 120학점입니다.")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is True, f"공백 정규화 실패: {missing}"


def test_phone_digit_normalization():
    """답변 '051-509-5182'가 chunks의 '0515095182'에 매칭 (숫자만 비교 보조 경로)."""
    answer = "문의: 051-509-5182"
    chunks = [_chunk("학사지원팀 0515095182")]
    grounded, missing = is_grounded(answer, chunks)
    # 1차 substring 실패 → 2차 digit-only 매칭 통과
    assert grounded is True, f"숫자 정규화 실패: {missing}"


def test_phone_complete_fabrication_fails():
    answer = "문의: 999-888-7777"
    chunks = [_chunk("학사지원팀 051-509-5182")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is False
    assert any("phone:999-888-7777" in m for m in missing)


# ── URL ──────────────────────────────────────────────────────

def test_url_bufs_in_chunks_passes():
    answer = "수강신청 사이트: sugang.bufs.ac.kr"
    chunks = [_chunk("https://sugang.bufs.ac.kr/...")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is True, f"URL 매칭 실패: {missing}"


def test_url_fake_domain_fails():
    answer = "수강신청: fake.bufs.ac.kr"
    chunks = [_chunk("진짜 URL은 sugang.bufs.ac.kr")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is False


# ── 복합 fact ──────────────────────────────────────────────────────

def test_multi_fact_all_present_passes():
    answer = "2023학번 졸업학점은 120학점이며 4학년 기준입니다."
    chunks = [_chunk("학사안내 ... 2023학번 ... 120학점 ... 4학년 ... 졸업요건")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is True, f"복합 fact 매칭 실패: {missing}"


def test_multi_fact_one_missing_fails():
    answer = "2023학번 졸업학점은 120학점이며 9학년 기준입니다."  # 9학년 환각
    chunks = [_chunk("학사안내 ... 2023학번 ... 120학점 ... 4학년")]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is False
    assert any("grade:9학년" in m for m in missing)


# ── 회귀 케이스 (Path #1 골든 시나리오) ──

def test_path1_golden_passes():
    """Path #1 (졸업학점 알려줘) direct_answer가 graph FAQ 청크와 grounding 통과해야."""
    answer = "2023학번 내국인 학생의 졸업학점은 120학점입니다."
    # 실제 graph FAQ 청크 본문은 비슷한 형태일 것 (가정)
    chunks = [_chunk(
        "Q: 졸업학점이 얼마인가요?\n"
        "A: 2023학번 내국인 학생의 졸업학점은 120학점입니다."
    )]
    grounded, missing = is_grounded(answer, chunks)
    assert grounded is True, f"Path #1 회귀: {missing}"
