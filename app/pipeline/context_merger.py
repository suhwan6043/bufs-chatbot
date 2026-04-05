"""
컨텍스트 통합기 - Vector/Graph 검색 결과를 하나의 프롬프트용 컨텍스트로 병합
CPU 전용, ~2ms 처리

원칙 2: 인텐트별 적응형 RRF 가중치 + 컨텍스트 예산
"""

import logging
import re
from typing import List, Optional

from app.models import SearchResult, MergedContext, Intent

logger = logging.getLogger(__name__)

# 대략적인 토큰 추정: 한국어 1글자 ≈ 1.5 토큰 (즉 1토큰 ≈ 0.67글자)
TOKENS_PER_CHAR = 1.5
_DEFAULT_CONTEXT_TOKENS = 1200

# ── 원칙 2: 인텐트별 적응형 설정 ────────────────────────────────
# (graph_weight, vector_weight) — 인텐트에 따라 검색 채널 가중치 조정
_INTENT_WEIGHTS = {
    Intent.SCHEDULE:         (2.0, 0.5),   # 그래프(학사일정) 강력 우선
    Intent.ALTERNATIVE:      (1.5, 1.0),   # FAQ가 핵심 소스 → 벡터 가중치 정상화
    Intent.GRADUATION_REQ:   (1.8, 1.0),
    Intent.REGISTRATION:     (1.5, 1.0),
    Intent.EARLY_GRADUATION: (1.5, 1.0),
    Intent.MAJOR_CHANGE:     (1.5, 1.0),
    Intent.SCHOLARSHIP:      (1.0, 1.2),   # 벡터(크롤링 공지) 우선
    Intent.LEAVE_OF_ABSENCE: (1.5, 0.8),
    Intent.COURSE_INFO:      (0.8, 1.5),   # 벡터(시간표 청크) 우선
    Intent.GENERAL:          (0.5, 1.5),
}
_DEFAULT_WEIGHTS = (1.5, 1.0)

# 인텐트별 컨텍스트 토큰 예산 — 단답형은 작게, 복합형은 크게
_INTENT_BUDGET = {
    Intent.SCHEDULE:       700,
    Intent.ALTERNATIVE:    800,
    Intent.GRADUATION_REQ: 1400,
    Intent.REGISTRATION:   1200,
    Intent.COURSE_INFO:    1200,
    Intent.SCHOLARSHIP:    1000,
    Intent.LEAVE_OF_ABSENCE: 1000,
    Intent.EARLY_GRADUATION: 1200,
    Intent.MAJOR_CHANGE:   1200,
    Intent.TRANSCRIPT:     1600,
}

# RRF 상수
_RRF_K = 10


def _rrf_merge(
    graph_results: List[SearchResult],
    vector_results: List[SearchResult],
    graph_weight: float = 1.5,
    vector_weight: float = 1.0,
) -> List[SearchResult]:
    """
    Weighted Reciprocal Rank Fusion으로 그래프·벡터 결과를 병합합니다.
    score_rrf(d) = w_graph / (k + rank_graph(d)) + w_vector / (k + rank_vector(d))

    원칙 2: graph_weight/vector_weight는 인텐트에 따라 동적 조정됩니다.
    """
    rrf_scores: dict = {}   # id(result) → rrf_score
    result_map: dict = {}   # id(result) → SearchResult

    for rank, r in enumerate(graph_results, start=1):
        r.metadata["source_type"] = "graph"
        rid = id(r)
        rrf_scores[rid] = rrf_scores.get(rid, 0.0) + graph_weight / (_RRF_K + rank)
        result_map[rid] = r

    for rank, r in enumerate(vector_results, start=1):
        r.metadata["source_type"] = "vector"
        rid = id(r)
        rrf_scores[rid] = rrf_scores.get(rid, 0.0) + vector_weight / (_RRF_K + rank)
        result_map[rid] = r

    merged = sorted(result_map.values(), key=lambda r: rrf_scores[id(r)], reverse=True)
    for r in merged:
        r.score = rrf_scores[id(r)]
    return merged


class ContextMerger:
    """
    [역할] Vector/Graph 검색 결과를 LLM 프롬프트용 컨텍스트로 통합
    [핵심] RRF(k=60)로 두 검색 채널을 순위 기반 병합 후 토큰 제한 내 선별
    """

    def merge(
        self,
        vector_results: List[SearchResult],
        graph_results: List[SearchResult],
        question: str = "",
        intent: Optional[Intent] = None,
        entities: Optional[dict] = None,
        transcript_context: str = "",
    ) -> MergedContext:
        """검색 결과를 통합된 컨텍스트로 병합합니다.

        원칙 2: intent에 따라 RRF 가중치와 컨텍스트 예산을 동적 조정합니다.
        transcript_context가 있으면 합성 SearchResult로 선두 삽입합니다.
        """
        # 성적표 컨텍스트 주입 (PII 제거 상태)
        if transcript_context:
            from app.models import SearchResult as SR
            transcript_result = SR(
                text=transcript_context,
                score=2.0,
                source="transcript",
                metadata={"source_type": "transcript"},
            )
            graph_results = [transcript_result] + list(graph_results)

        # 인텐트별 가중치 + 예산 결정
        gw, vw = _INTENT_WEIGHTS.get(intent, _DEFAULT_WEIGHTS)
        budget = _INTENT_BUDGET.get(intent, _DEFAULT_CONTEXT_TOKENS)
        # 성적표 컨텍스트가 있으면 예산 확장
        if transcript_context:
            budget = max(budget, 2000)

        # 그래프 direct_answer 존재 시 벡터 노이즈 억제
        # focused handler(≤3 결과)가 정확한 답을 제공 → 벡터 완전 차단
        # 다수 결과(>3) → 벡터 최소 보조만 허용
        direct_results = [r for r in graph_results if r.metadata.get("direct_answer")]
        if direct_results:
            if len(graph_results) <= 3:
                vw = 0.2   # PDF 출처 확보용 벡터 최소 유지
            else:
                vw = min(vw, 0.3)

        # RRF로 그래프·벡터 결과 병합 (rank 기반, 인텐트별 가중치 적용)
        all_results = _rrf_merge(graph_results, vector_results, gw, vw)

        # 원칙 2: 엔티티 기반 필터 — 단일 토픽 쿼리에서 무관 청크 차단
        all_results = self._filter_by_entity(all_results, entities)

        # 원칙 1: FAQ 청크 조건부 최우선 배치
        # FAQ Q/A는 질문-답변 매칭이 명확해 LLM이 정확히 활용 가능 → 관련성이 확인된 FAQ는 맨 앞.
        # 그러나 doc_type="faq"만 보고 무조건 끌어올리면 "제2전공+교직" 같은 교차 질문에서
        # 공통 어휘("전공")만 걸린 무관한 FAQ가 컨텍스트 최상단에 박혀 LLM이 오답을 확신하는 회귀 발생.
        # → 관련성 신호(direct_answer 또는 질문 핵심 토큰 매칭)가 있는 FAQ만 최상단으로,
        #    나머지는 RRF 순서를 그대로 따라간다.
        if all_results:
            from app.pipeline.ko_tokenizer import stems, expand_tokens, FAQ_STOPWORDS
            q_key = expand_tokens(stems(question or ""), FAQ_STOPWORDS)

            def _faq_is_relevant(r: SearchResult) -> bool:
                # direct_answer가 부여된 FAQ는 그래프 단에서 강한 매칭으로 판정된 것 → 신뢰
                if r.metadata.get("direct_answer"):
                    return True
                if not q_key:
                    return True  # 질문이 전부 범용어면 기존처럼 FAQ 우선
                hay_key = expand_tokens(stems(r.text or ""), FAQ_STOPWORDS)
                return bool(q_key & hay_key)

            relevant_faq, stale_faq, non_faq = [], [], []
            for r in all_results:
                is_faq = (
                    r.metadata.get("doc_type") == "faq"
                    or r.metadata.get("node_type") == "FAQ"
                )
                if not is_faq:
                    non_faq.append(r)
                elif _faq_is_relevant(r):
                    relevant_faq.append(r)
                else:
                    stale_faq.append(r)
            # 관련 FAQ를 최상단으로, 무관 FAQ는 non-FAQ 뒤로 밀어 노이즈 억제
            all_results = relevant_faq + non_faq + stale_faq

        # 토큰 제한 내에서 컨텍스트 구성
        context_parts = []
        total_chars = 0
        max_chars = int(budget / TOKENS_PER_CHAR)

        selected_vector = []
        selected_graph = []
        direct_answer = ""

        # 원칙 1: FAQ direct_answer 우선 선택
        # FAQ에 정답이 있는데 학사일정 등 다른 노드의 direct_answer가 먼저 선택되어
        # 틀린 날짜/숫자가 답변되는 회귀 방지. FAQ는 큐레이션된 정답이므로 최우선.
        for result in all_results:
            is_faq = (
                result.metadata.get("doc_type") == "faq"
                or result.metadata.get("node_type") == "FAQ"
            )
            if is_faq and result.metadata.get("direct_answer"):
                direct_answer = result.metadata["direct_answer"]
                break

        for result in all_results:
            if not result.text:
                continue

            # FAQ direct_answer가 없을 때만 다른 소스의 direct_answer 수락
            if not direct_answer and result.metadata.get("direct_answer"):
                direct_answer = result.metadata["direct_answer"]

            text_len = len(result.text)
            remaining = max_chars - total_chars

            if remaining <= 80:
                break

            if text_len > remaining:
                # 남은 공간에 맞게 자르기 — skip 대신 truncate 후 continue
                truncated = result.text[:remaining] + "..."
                context_parts.append(self._format_result(result, truncated))
                total_chars += remaining
                result.metadata["in_context"] = True
                if result.metadata.get("source_type") == "graph":
                    selected_graph.append(result)
                else:
                    selected_vector.append(result)
                continue

            context_parts.append(self._format_result(result))
            total_chars += text_len
            result.metadata["in_context"] = True

            if result.metadata.get("source_type") == "graph":
                selected_graph.append(result)
            else:
                selected_vector.append(result)

        formatted = "\n\n".join(context_parts)
        token_estimate = int(len(formatted) * TOKENS_PER_CHAR)

        # 공지사항 출처 URL 수집 (중복 제거)
        source_urls = self._collect_source_urls(selected_vector + selected_graph)

        # direct_answer가 없으면 컨텍스트에서 팩트 자동 추출 시도
        if not direct_answer and formatted and question:
            extracted = self._try_extract_direct_answer(question, formatted)
            if extracted:
                direct_answer = extracted

        return MergedContext(
            vector_results=selected_vector,
            graph_results=selected_graph,
            formatted_context=formatted,
            total_tokens_estimate=token_estimate,
            direct_answer=direct_answer,
            source_urls=source_urls,
        )

    @staticmethod
    def _try_extract_direct_answer(question: str, context: str) -> str:
        """
        질문 유형에 따라 컨텍스트에서 핵심 팩트를 정규식으로 직접 추출.
        추출 성공 시 LLM을 우회하여 정확도 향상 + 지연 시간 절감.
        추출 실패 시 빈 문자열 → 기존 LLM 경로 유지.

        주의: 오탐 방지를 위해 질문+컨텍스트 동시 매칭만 수행.
        """
        q = re.sub(r"\s+", "", question.lower())

        # ── 1) URL 추출: "사이트", "주소", "홈페이지" ──
        if any(kw in q for kw in ("사이트", "주소", "홈페이지", "url")):
            urls = re.findall(r"https?://[^\s)\]가-힣]+", context)
            if urls:
                return f"{urls[0]}입니다."
            # 그래프 속성에서 URL 추출 (수강신청사이트 필드)
            m = re.search(r"수강신청사이트[:\s]*(\S+bufs\S+)", context)
            if m:
                return f"{m.group(1)}입니다."

        # ── 2) 재수강 최고 성적 (매우 구체적 질문만) ──
        if "최고성적" in q or ("재수강" in q and "성적" in q and "최고" in q):
            m = re.search(r"재수강최고성적[은는:]?\s*([A-Da-d][+]?)", context)
            if m:
                return f"{m.group(1)}입니다."

        # ── 3) 재수강 가능 성적 기준 ──
        if "재수강" in q and ("가능" in q or "기준" in q or "몇" in q):
            m = re.search(r"재수강기준성적[:\s]*([A-Da-d][+]?이하)", context)
            if m:
                return f"{m.group(1)} 과목만 재수강 가능합니다."
            m = re.search(r"([A-Da-d][+]?)\s*이하.*?(?:과목|가능|재수강)", context)
            if m:
                return f"{m.group(1)} 이하의 과목만 가능합니다."

        # ── 4) 출석 요건 (분수/비율) ──
        if "출석" in q and any(kw in q for kw in ("요건", "조건", "기준")):
            m = re.search(r"(\d+/\d+)\s*이상", context)
            if m:
                return f"전체 출석일수의 {m.group(1)} 이상을 충족해야 합니다."
            m = re.search(r"출석요건[:\s]*(\d+/\d+)", context)
            if m:
                return f"전체 출석일수의 {m.group(1)} 이상을 충족해야 합니다."

        # ── 5) 졸업 최소 학점 (학번 특정) ──
        if ("졸업" in q or "필요한" in q) and ("최소" in q or "학점" in q):
            m = re.search(r"졸업학점[은는:]?\s*(\d{2,3})", context)
            if m:
                return f"{m.group(1)}학점 이상입니다."

        # ── 6) OCU 개강일/시간 ──
        if "ocu" in q and any(kw in q for kw in ("개강", "시작", "언제")):
            m_date = re.search(r"개강일[:\s]*([\d\-]+)", context)
            m_time = re.search(r"개강시간[:\s]*(오[전후]\s*\d+시)", context)
            if m_date:
                answer = f"OCU 개강일은 {m_date.group(1)}"
                if m_time:
                    answer += f" {m_time.group(1)}"
                return answer + "입니다."

        # ── 7) 마감 시간 ("몇 시", "마감 시간") ──
        if any(kw in q for kw in ("몇시", "마감시간", "마감몇시")):
            m = re.search(r"마감[^:]*?(\d{1,2}:\d{2})", context)
            if not m:
                m = re.search(r"(\d{1,2}:\d{2}).*?마감", context)
            if not m:
                m = re.search(r"시간[:\s]*(\d{1,2}:\d{2})", context)
            if m:
                return f"{m.group(1)}입니다."

        # ── 8) 로그인 오픈 시간 ──
        if "로그인" in q and any(kw in q for kw in ("시간", "오픈", "언제")):
            m = re.search(r"로그인오픈시간[:\s]*(.+?)(?:\n|$)", context)
            if not m:
                m = re.search(r"(\d+분?\s*전).*?로그인", context)
            if m:
                return f"로그인은 {m.group(1).strip()} 오픈됩니다."

        return ""

    @staticmethod
    def _extract_core_tokens(question: str) -> list[str]:
        """질문에서 조사 제거 + stopword 필터한 어근 토큰. FAQ 관련성 판정용.

        공용 ko_tokenizer를 사용해 academic_graph.search_faq와 동일한 기준으로
        매칭 여부를 판정한다 → 두 레이어의 결과가 엇갈리지 않는다.
        """
        from app.pipeline.ko_tokenizer import core_tokens, FAQ_STOPWORDS
        return core_tokens(question, FAQ_STOPWORDS)

    @staticmethod
    def _filter_by_entity(
        results: List[SearchResult], entities: Optional[dict]
    ) -> List[SearchResult]:
        """엔티티 기반 필터: 단일 토픽 쿼리에서 무관 청크를 제거합니다.

        원칙 2: 검색 정밀도 향상을 위한 동적 필터링.
        최소 1개 결과는 항상 보장합니다.
        """
        if not entities or not results:
            return results

        # OCU 토픽 필터
        if entities.get("ocu"):
            _OCU_KW = ("ocu", "열린사이버", "컨소시엄", "cons.ocu")
            filtered = [
                r for r in results
                if r.metadata.get("source_type") == "graph"
                or any(kw in r.text.lower() for kw in _OCU_KW)
            ]
            if filtered:
                return filtered

        return results

    @staticmethod
    def _collect_source_urls(results: list) -> list:
        """
        검색 결과에서 공지사항 출처 URL을 수집합니다.

        doc_type 이 "notice" 또는 "notice_attachment" 인 결과만 대상으로 하며
        동일 URL의 중복 항목은 제거합니다.

        Returns:
            [{"title": "공지 제목", "url": "https://..."}, ...]
        """
        _NOTICE_TYPES = {"notice", "notice_attachment"}
        seen: set = set()
        urls: list = []

        for result in results:
            meta = result.metadata or {}
            doc_type = meta.get("doc_type", "")
            url = meta.get("source_url", "")

            if doc_type not in _NOTICE_TYPES:
                continue
            if not url or not url.startswith("http"):
                continue
            if url in seen:
                continue

            seen.add(url)
            urls.append({
                "title": meta.get("title", "") or url,
                "url": url,
            })

        return urls

    @staticmethod
    def _format_result(result: SearchResult, text: str = None) -> str:
        """검색 결과를 포맷팅합니다."""
        text = text or result.text
        source_info = ""

        doc_type = result.metadata.get("doc_type", "")
        source_url = result.metadata.get("source_url", "")

        if doc_type in ("notice", "notice_attachment") and source_url:
            # 공지사항: URL을 출처로 표시하여 LLM이 참조 가능하게 함
            source_info = f" [{source_url}]"
        elif result.page_number:
            source_info = f" [p.{result.page_number}]"
        elif result.source:
            source_info = f" [{result.source}]"

        return f"---{source_info}\n{text}"
