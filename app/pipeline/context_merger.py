"""
컨텍스트 통합기 - Vector/Graph 검색 결과를 하나의 프롬프트용 컨텍스트로 병합
CPU 전용, ~2ms 처리
"""

import logging
from typing import List

from app.models import SearchResult, MergedContext

logger = logging.getLogger(__name__)

# 대략적인 토큰 추정: 한국어 1글자 ≈ 1.5 토큰 (즉 1토큰 ≈ 0.67글자)
TOKENS_PER_CHAR = 1.5
MAX_CONTEXT_TOKENS = 1200  # 시스템 프롬프트(~300) + 질문(~100) + 답변(~400) 제외


class ContextMerger:
    """
    [역할] Vector/Graph 검색 결과를 LLM 프롬프트용 컨텍스트로 통합
    [핵심] 2048 토큰 제한 내에서 가장 관련성 높은 정보를 선별
    """

    def merge(
        self,
        vector_results: List[SearchResult],
        graph_results: List[SearchResult],
    ) -> MergedContext:
        """검색 결과를 통합된 컨텍스트로 병합합니다."""
        # 그래프 결과 우선 (구조화된 정보가 더 정확)
        all_results = []
        for r in graph_results:
            r.metadata["source_type"] = "graph"
            all_results.append(r)
        for r in vector_results:
            r.metadata["source_type"] = "vector"
            all_results.append(r)

        # 점수 기준 정렬 (그래프 결과는 score=1.0)
        all_results.sort(key=lambda x: x.score, reverse=True)

        # 토큰 제한 내에서 컨텍스트 구성
        context_parts = []
        total_chars = 0
        max_chars = int(MAX_CONTEXT_TOKENS / TOKENS_PER_CHAR)

        selected_vector = []
        selected_graph = []
        direct_answer = ""

        for result in all_results:
            if not direct_answer and result.metadata.get("direct_answer"):
                direct_answer = result.metadata["direct_answer"]

            text_len = len(result.text)
            if total_chars + text_len > max_chars:
                # 남은 공간에 맞게 자르기
                remaining = max_chars - total_chars
                if remaining > 100:
                    truncated = result.text[:remaining] + "..."
                    context_parts.append(self._format_result(result, truncated))
                break

            context_parts.append(self._format_result(result))
            total_chars += text_len

            if result.metadata.get("source_type") == "graph":
                selected_graph.append(result)
            else:
                selected_vector.append(result)

        formatted = "\n\n".join(context_parts)
        token_estimate = int(len(formatted) * TOKENS_PER_CHAR)

        return MergedContext(
            vector_results=selected_vector,
            graph_results=selected_graph,
            formatted_context=formatted,
            total_tokens_estimate=token_estimate,
            direct_answer=direct_answer,
        )

    @staticmethod
    def _format_result(result: SearchResult, text: str = None) -> str:
        """검색 결과를 포맷팅합니다."""
        text = text or result.text
        source_info = ""
        if result.page_number:
            source_info = f" [p.{result.page_number}]"
        elif result.source:
            source_info = f" [{result.source}]"
        return f"---{source_info}\n{text}"
