"""
응답 검증기 - LLM 답변이 컨텍스트 기반인지 검증
CPU 전용, ~5ms 처리
"""

import re
import logging
from typing import List, Tuple

from app.models import SearchResult

logger = logging.getLogger(__name__)


class ResponseValidator:
    """
    [역할] LLM 답변이 제공된 컨텍스트에 근거하는지 검증
    [핵심] 할루시네이션 감지, 출처 존재 여부 확인
    [성능] CPU, ~5ms
    """

    # 답변에서 출처 표기를 찾는 패턴
    SOURCE_PATTERN = re.compile(r"\[(?:출처|p\.|페이지)[:\s]*(\d+)\]")

    # 컨텍스트 부재 응답 키워드
    # 2026-04-11 수정: 패턴 추가.
    # - "찾을 수 없"(접두어 독립) — "해당 **일정** 정보를 찾을 수 없습니다" 같은
    #   중간 단어가 끼어든 변형 매칭
    # - "문의하시기 바랍니다" — 거절 응답의 꼬리말 표준 패턴
    NO_CONTEXT_PHRASES = [
        # KO 거절 패턴
        "확인되지 않는 정보",
        "제공된 컨텍스트에 없",
        "관련 정보를 찾을 수 없",
        "해당 정보가 없",
        "찾을 수 없습니다",           # 일반 거절 표현
        "찾지 못했습니다",             # "정확히 확인하지 못했습니다"
        "문의하시기 바랍니다",         # refusal 꼬리말
        # EN 거절 패턴
        "couldn't find relevant",
        "could not find relevant",
        "no relevant information",
        "not available in the provided",
        "please contact the academic affairs",
        "+82-51-509-5182",
    ]

    def validate(
        self,
        answer: str,
        context: str,
        search_results: List[SearchResult],
    ) -> Tuple[bool, List[str]]:
        """
        답변을 검증합니다.

        Returns:
            (validation_passed, warnings) 튜플
        """
        warnings = []

        # 빈 답변 체크
        if not answer or not answer.strip():
            return False, ["답변이 비어 있습니다."]

        # 컨텍스트 부재 응답은 검증 통과 (정직한 응답)
        if self._is_no_context_response(answer):
            return True, []

        # 출처 표기 확인
        if not self._has_source_reference(answer):
            warnings.append("답변에 출처(페이지 번호)가 명시되지 않았습니다.")

        # 숫자 교차 검증 (컨텍스트에 있는 숫자인지)
        hallucinated_numbers = self._check_numbers(answer, context)
        if hallucinated_numbers:
            warnings.append(
                f"컨텍스트에서 확인되지 않는 숫자: {', '.join(hallucinated_numbers)}"
            )

        # 경고만 있고 치명적 문제가 없으면 통과
        passed = len(hallucinated_numbers) == 0
        return passed, warnings

    def _is_no_context_response(self, answer: str) -> bool:
        """컨텍스트에 정보가 없다는 정직한 응답인지 확인합니다."""
        return any(phrase in answer for phrase in self.NO_CONTEXT_PHRASES)

    def _has_source_reference(self, answer: str) -> bool:
        """답변에 출처 표기가 있는지 확인합니다."""
        return bool(self.SOURCE_PATTERN.search(answer))

    def _check_numbers(self, answer: str, context: str) -> List[str]:
        """답변의 핵심 숫자가 컨텍스트에 존재하는지 확인합니다."""
        # 학점, 학번 등 핵심 숫자 패턴
        credit_pattern = re.compile(r"(\d+)\s*학점")
        year_pattern = re.compile(r"(20[0-2]\d)학번")

        context_numbers = set()
        for m in re.finditer(r"\d+", context):
            context_numbers.add(m.group())

        hallucinated = []

        # 학점 숫자 검증
        for m in credit_pattern.finditer(answer):
            num = m.group(1)
            if num not in context_numbers:
                hallucinated.append(f"{num}학점")

        # 학번 숫자는 사용자 입력일 수 있으므로 제외

        return hallucinated
