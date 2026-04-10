"""
컨텍스트 통합기 - Vector/Graph 검색 결과를 하나의 프롬프트용 컨텍스트로 병합
CPU 전용, ~2ms 처리

원칙 2: 인텐트별 적응형 RRF 가중치 + 컨텍스트 예산
"""

import logging
import re
from typing import List, Optional

from app.models import SearchResult, MergedContext, Intent, QuestionType

logger = logging.getLogger(__name__)

# 대략적인 토큰 추정: 한국어 1글자 ≈ 1.5 토큰 (즉 1토큰 ≈ 0.67글자)
TOKENS_PER_CHAR = 1.5
_DEFAULT_CONTEXT_TOKENS = 1200

# ── 원칙 2: 인텐트별 적응형 설정 ────────────────────────────────
# (graph_weight, vector_weight) — Tier 1 (domestic+guide 벡터) 우선 정책 반영
# 벡터에 Tier 1 공식 자료가 포함되므로 벡터 가중치를 그래프와 동등 이상으로 설정
_INTENT_WEIGHTS = {
    Intent.SCHEDULE:         (2.0, 0.8),   # 그래프(학사일정) 우선, 벡터도 참고
    Intent.ALTERNATIVE:      (1.2, 1.2),   # FAQ·PDF 동등
    Intent.GRADUATION_REQ:   (1.5, 1.2),
    Intent.REGISTRATION:     (1.2, 1.5),   # Tier 1 벡터(PDF) 우선
    Intent.EARLY_GRADUATION: (1.5, 1.2),
    Intent.MAJOR_CHANGE:     (1.2, 1.2),
    Intent.SCHOLARSHIP:      (1.0, 1.2),   # 벡터(크롤링 공지) 우선
    Intent.LEAVE_OF_ABSENCE: (1.2, 1.2),   # PDF 가이드와 그래프 동등
    Intent.COURSE_INFO:      (0.8, 1.5),   # 벡터(시간표 청크) 우선
    Intent.GENERAL:          (0.8, 1.5),   # Tier 1 벡터 우선
}
_DEFAULT_WEIGHTS = (1.2, 1.2)

# ── 원칙 2: QuestionType별 가중치 변조 (graph_mod, vector_mod) ──
# 토픽 Intent 가중치에 곱하여 질문 유형에 따라 vector/graph 밸런스를 동적 조정
_QT_WEIGHT_MODIFIERS = {
    QuestionType.OVERVIEW:    (0.8, 1.2),   # 개요 → 벡터(PDF) 강화, 그래프↓
    QuestionType.FACTOID:     (1.0, 1.0),   # 사실 → 기본값
    QuestionType.PROCEDURAL:  (0.9, 1.1),   # 절차 → 벡터 약간↑
    QuestionType.REASONING:   (1.3, 0.7),   # 추론 → 그래프↑ 벡터↓
}
_DEFAULT_QT_MOD = (1.0, 1.0)

# 인텐트별 컨텍스트 토큰 예산 — 단답형은 작게, 복합형은 크게
_INTENT_BUDGET = {
    Intent.SCHEDULE:       1000,
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
        if r.metadata.get("source_type") != "transcript":
            r.metadata["source_type"] = "graph"
        rid = id(r)
        rrf_scores[rid] = rrf_scores.get(rid, 0.0) + graph_weight / (_RRF_K + rank)
        # transcript는 항상 최상위 — RRF 점수에 큰 가산
        if r.metadata.get("source_type") == "transcript":
            rrf_scores[rid] += 10.0
        result_map[rid] = r

    # Tier 1 doc_type (공식 학사 자료)은 RRF 점수 boost
    _TIER1_TYPES = frozenset({"domestic", "guide"})
    for rank, r in enumerate(vector_results, start=1):
        r.metadata["source_type"] = "vector"
        rid = id(r)
        base = vector_weight / (_RRF_K + rank)
        # Tier 1 공식 자료 boost: RRF 점수 +30%
        if r.metadata.get("doc_type") in _TIER1_TYPES:
            base *= 1.3
        # 고정공지(📌) boost: 그래프/FAQ(Tier 2)와 동등 → +15%
        elif r.metadata.get("doc_type") == "notice" and r.metadata.get("is_pinned"):
            base *= 1.15
        rrf_scores[rid] = rrf_scores.get(rid, 0.0) + base
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
        question_type: Optional[QuestionType] = None,
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
                score=10.0,  # RRF merge에서 항상 최상위 유지
                source="transcript",
                metadata={"source_type": "transcript"},
            )
            graph_results = [transcript_result] + list(graph_results)

        # 인텐트별 가중치 + 예산 결정
        gw, vw = _INTENT_WEIGHTS.get(intent, _DEFAULT_WEIGHTS)
        budget = _INTENT_BUDGET.get(intent, _DEFAULT_CONTEXT_TOKENS)

        # 원칙 2: QuestionType 변조 — 질문 유형에 따라 vector/graph 밸런스 조정
        if question_type:
            g_mod, v_mod = _QT_WEIGHT_MODIFIERS.get(question_type, _DEFAULT_QT_MOD)
            gw *= g_mod
            vw *= v_mod
            # OVERVIEW → 컨텍스트 예산 확장 (전반 안내에 더 많은 소스 필요)
            if question_type == QuestionType.OVERVIEW:
                budget = max(budget, 1400)
        # 성적표 컨텍스트가 있으면 예산 확장
        if transcript_context:
            budget = max(budget, 2000)

        # 그래프/FAQ direct_answer 존재 시 벡터 노이즈 억제
        # focused handler(≤3 결과)가 정확한 답을 제공 → 벡터 최소 보조만 허용
        direct_results = [r for r in graph_results if r.metadata.get("direct_answer")]
        if direct_results:
            if len(graph_results) <= 3:
                vw = 0.2   # PDF 출처 확보용 벡터 최소 유지
            else:
                vw = min(vw, 0.3)

        # 원칙 2: RRF 병합 전 원래 점수 캡처 (confidence 계산용)
        # RRF merge가 r.score를 순위 점수로 덮어쓰므로, 실제 관련성 점수를 보존
        _pre_rrf_vector_top = max((r.score for r in vector_results), default=0.0)
        _pre_rrf_graph_top = max((r.score for r in graph_results), default=0.0)

        # RRF로 그래프·벡터 결과 병합 (rank 기반, 인텐트별 가중치 적용)
        all_results = _rrf_merge(graph_results, vector_results, gw, vw)

        # 원칙 2: 엔티티 기반 필터 — 단일 토픽 쿼리에서 무관 청크 차단
        all_results = self._filter_by_entity(all_results, entities)

        # 원칙 2: FAQ 승격은 하이브리드 시스템의 자체 신호(IDF·RRF·Cross-Encoder)에 위임
        #
        # direct_answer가 부여된 FAQ = 그래프 검색에서 IDF 가중치 + 특이성 페널티 +
        # stem 커버리지 게이트를 모두 통과 → 고신뢰 → 컨텍스트 최상단.
        # 그 외 FAQ는 RRF 점수 그대로 경쟁 (키워드 매칭으로 재판정하지 않음).
        # 리다이렉트 FAQ("어디서 확인/문의")는 본문 재료가 아님 → 항상 후순위.
        from app.graphdb.academic_graph import _is_redirect_answer as _is_redirect_answer_heur

        def _faq_answer_text(r: SearchResult) -> str:
            txt = r.text or ""
            idx = txt.find("A:")
            return txt[idx + 2:].strip() if idx >= 0 else txt

        def _is_redirect_faq(r: SearchResult) -> bool:
            if r.metadata.get("answer_type") == "redirect":
                return True
            return _is_redirect_answer_heur(_faq_answer_text(r), r.metadata)

        if all_results:
            promoted_faq, redirect_faq, rest = [], [], []
            for r in all_results:
                is_faq = (
                    r.metadata.get("doc_type") == "faq"
                    or r.metadata.get("node_type") == "FAQ"
                )
                if is_faq and _is_redirect_faq(r):
                    redirect_faq.append(r)
                elif is_faq and r.metadata.get("direct_answer"):
                    # 그래프 IDF·특이성·커버리지 게이트를 모두 통과한 FAQ만 승격
                    # B1/B2 threshold 강화로 OVERVIEW 별도 억제 불필요
                    promoted_faq.append(r)
                else:
                    # FAQ 포함 모든 결과: RRF 순서 유지 (하이브리드 점수 신뢰)
                    rest.append(r)
            all_results = promoted_faq + rest + redirect_faq

        # 토큰 제한 내에서 컨텍스트 구성
        context_parts = []
        total_chars = 0
        max_chars = int(budget / TOKENS_PER_CHAR)

        selected_vector = []
        selected_graph = []
        direct_answer = ""

        # 그래프·FAQ direct_answer를 동등하게 경쟁시킴 (RRF 순위 기반)
        # all_results는 이미 RRF 점수순으로 정렬되어 있으므로,
        # 가장 높은 RRF 점수를 받은 direct_answer를 채택한다.
        for result in all_results:
            if not result.metadata.get("direct_answer"):
                continue
            if _is_redirect_faq(result):
                continue
            direct_answer = result.metadata["direct_answer"]
            break

        # OCU 섹션 트리밍 플래그 (질문에 OCU 미언급 시 활성화)
        _trim_ocu = bool(entities and not entities.get("ocu"))

        for result in all_results:
            if not result.text:
                continue

            # FAQ direct_answer가 없을 때만 다른 소스의 direct_answer 수락
            if not direct_answer and result.metadata.get("direct_answer"):
                direct_answer = result.metadata["direct_answer"]

            # 원칙 2: OCU 미언급 쿼리에서 혼합 청크의 OCU 섹션 동적 트리밍
            if _trim_ocu and result.metadata.get("source_type") != "graph":
                result_text = self._strip_ocu_section(result.text)
            else:
                result_text = result.text

            if not result_text or not result_text.strip():
                continue

            text_len = len(result_text)
            remaining = max_chars - total_chars

            if remaining <= 80:
                break

            if text_len > remaining:
                # 남은 공간에 맞게 자르기 — skip 대신 truncate 후 continue
                truncated = result_text[:remaining] + "..."
                context_parts.append(self._format_result(result, truncated))
                total_chars += remaining
                result.metadata["in_context"] = True
                if result.metadata.get("source_type") == "graph":
                    selected_graph.append(result)
                else:
                    selected_vector.append(result)
                continue

            context_parts.append(self._format_result(result, result_text))
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

        # 원칙 2: 하이브리드 시스템 자체 신호로 context 관련성 신뢰도 산출
        # RRF 이전의 원래 점수를 사용 (RRF 점수는 절대 관련성을 표현하지 않음)
        #
        # 주의: 그래프 핸들러 결과는 score ≥ 1.0 (기본값)이나 관련성 신호가 아님.
        # FAQ 정규화 점수(0~1)와 벡터 점수만 실제 관련성을 반영함.
        # 원칙 2: context_confidence = 컨텍스트 충분성 신뢰도 (0.0~1.0)
        # direct_answer 있으면 1.0, 선택된 결과 수 기반 산출
        if direct_answer:
            confidence = 1.0
        elif selected_vector or selected_graph:
            n_selected = len(selected_vector) + len(selected_graph)
            if n_selected >= 3:
                confidence = 0.8
            elif n_selected >= 2:
                confidence = 0.6
            else:
                confidence = 0.4
        else:
            confidence = 0.0

        return MergedContext(
            vector_results=selected_vector,
            graph_results=selected_graph,
            formatted_context=formatted,
            total_tokens_estimate=token_estimate,
            direct_answer=direct_answer,
            source_urls=source_urls,
            context_confidence=confidence,
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

        # ── 9) 이론/실습 학점 추출 ──
        if any(kw in q for kw in ("이론", "실습", "이수과목")):
            m = re.search(r"이론\s*(\d+)\s*(?:학점)?\s*실습\s*(\d+)", context)
            if m:
                return f"이론 {m.group(1)}학점, 실습 {m.group(2)}학점입니다."

        # ── 10) 금액 추출 ("수강료", "비용", "금액", "얼마") ──
        if any(kw in q for kw in ("수강료", "비용", "금액", "얼마")) and "초과" not in q:
            m = re.search(r"(\d{1,3}(?:,\d{3})*)\s*원", context)
            if m:
                return f"{m.group(1)}원입니다."

        # ── 11) 초과 수강료 ──
        if "초과" in q and any(kw in q for kw in ("수강료", "비용", "금액")):
            m = re.search(r"초과[^:]*?(\d{1,3}(?:,\d{3})*)\s*원", context)
            if m:
                return f"초과 수강료는 {m.group(1)}원입니다."

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

        # OCU 토픽 필터 (정방향): OCU 쿼리 → OCU 청크만 유지
        if entities.get("ocu"):
            _OCU_KW = ("ocu", "열린사이버", "컨소시엄", "cons.ocu")
            filtered = [
                r for r in results
                if r.metadata.get("source_type") == "graph"
                or any(kw in r.text.lower() for kw in _OCU_KW)
            ]
            if filtered:
                return filtered

        # OCU 역방향 필터: OCU 미언급 시 OCU 전용 청크를 제거
        # 혼합 청크(본교+OCU 같은 페이지)는 유지 — LLM 프롬프트에서 OCU 무시 지시
        if not entities.get("ocu"):
            _OCU_PRIMARY_KW = ("열린사이버", "cons.ocu", "컨소시엄")
            non_ocu = []
            ocu_pure = []
            for r in results:
                txt_lower = (r.text or "").lower()
                if r.metadata.get("source_type") == "graph":
                    non_ocu.append(r)
                # OCU가 주제인 청크만 제거 (제목/첫 100자에 OCU 핵심어)
                elif any(kw in txt_lower[:200] for kw in _OCU_PRIMARY_KW):
                    ocu_pure.append(r)
                else:
                    non_ocu.append(r)
            if non_ocu:
                return non_ocu

        return results

    @staticmethod
    def _strip_ocu_section(text: str) -> str:
        """혼합 청크에서 OCU 섹션을 동적으로 트리밍합니다.

        원칙 2: PDF 페이지에 본교+OCU 내용이 혼합된 경우,
        OCU 섹션 시작 지점 이후 텍스트를 제거하여 본교 내용만 남김.

        경계 패턴:
        - "기타사항 안내" + "OCU" (p.23 구조)
        - "상대평가 기준" (OCU 고유 — 본교는 2023-2부터 절대평가)
        - "OCU 개설 교과목" / "OCU 홈페이지"
        - "CS강의" / "On-line강의" (OCU 섹션 제목)
        """
        if not text:
            return text

        # OCU 섹션 시작점을 찾아서 그 이전까지만 남김
        _OCU_SECTION_MARKERS = (
            "기타사항 안내",
            "상대평가 기준",
            "나. 상대평가",
            "OCU 개설 교과목",
            "OCU 홈페이지",
            "CS강의",
            "On-line강의",
        )

        earliest_pos = len(text)
        for marker in _OCU_SECTION_MARKERS:
            pos = text.find(marker)
            if pos != -1 and pos < earliest_pos:
                earliest_pos = pos

        if earliest_pos < len(text):
            trimmed = text[:earliest_pos].rstrip()
            # 최소 100자 이상 남아야 의미 있는 컨텍스트
            if len(trimmed) >= 100:
                return trimmed

        return text

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
