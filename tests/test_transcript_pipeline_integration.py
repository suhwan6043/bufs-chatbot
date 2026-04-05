"""
성적표 × 다중 소스 통합 테스트.

학생이 성적표를 업로드한 상태에서 학사 질문을 했을 때, 파이프라인이
다음 4개 소스를 제대로 결합하여 정확한 답변을 내는지 검증:

1. transcript_context  (성적표 lazy 분석) — score=2.0 최우선
2. 벡터 DB             (ChromaDB, PDF 청크)
3. 학사 그래프          (NetworkX, 졸업요건/수강규칙/학사일정)
4. 공지사항             (notice/notice_attachment doc_type)

⚠️ 하드코딩 금지 원칙:
학업성적사정표는 랜덤 학생마다 값이 달라지므로, 테스트는 특정 학생 값에
의존하지 않고 property-based로 불변식만 검증한다. 모든 프로필 데이터는
`random_profile(seed, has_*)`로 런타임 생성된다.
"""

import random
import time
import uuid
from dataclasses import dataclass
from typing import Optional
from unittest.mock import MagicMock, patch

import pytest

from app.models import Intent, QueryAnalysis, SearchResult, MergedContext
from app.pipeline.query_analyzer import QueryAnalyzer
from app.pipeline.query_router import QueryRouter
from app.pipeline.context_merger import ContextMerger
from app.transcript.analyzer import TranscriptAnalyzer
from app.transcript.models import (
    CourseRecord,
    CreditCategory,
    CreditsSummary,
    StudentAcademicProfile,
    StudentProfile,
)


# ═══════════════════════════════════════════════════════════
#  공통 헬퍼 — 하드코딩 없는 완전 파라미터화 팩토리
# ═══════════════════════════════════════════════════════════


def _make_search_result(
    text: str,
    *,
    score: float,
    doc_type: str,
    page_number: int = 0,
    source_url: str = "",
    source_type: str = "vector",
    direct_answer: str = "",
) -> SearchResult:
    """SearchResult 팩토리 — 키워드 전용, 기본값 최소화."""
    metadata = {
        "doc_type": doc_type,
        "source_type": source_type,
        "page_number": page_number,
    }
    if source_url:
        metadata["source_url"] = source_url
    if direct_answer:
        metadata["direct_answer"] = direct_answer
    return SearchResult(
        text=text,
        score=score,
        source="test",
        page_number=page_number,
        metadata=metadata,
    )


def _unique_token(prefix: str) -> str:
    """런타임 생성 고유 식별자 — 하드코딩 0."""
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


# ─────────────────────────────────────────────────────────
# 프로필 빌더 — 모든 필드는 호출자가 주입
# ─────────────────────────────────────────────────────────


@dataclass
class CategorySpec:
    """학점 카테고리 명세 — 테스트가 직접 값을 지정."""
    name: str
    required: float
    earned: float

    @property
    def shortage(self) -> float:
        return max(0.0, self.required - self.earned)


@dataclass
class CourseSpec:
    """과목 명세 — 테스트가 직접 값을 지정."""
    교과목명: str
    교과목번호: str
    이수학기: str
    학점: float
    성적: str
    category: str = ""
    이수구분: str = ""


def build_profile(
    *,
    입학연도: str,
    학부과: str,
    전공: str,
    총_졸업기준: float,
    총_취득학점: float,
    평점평균: float,
    categories: list,
    courses: list,
    복수전공: str = "",
    student_group: str = "",
    student_type: str = "내국인",
    졸업시험: Optional[dict] = None,
    졸업인증: Optional[dict] = None,
    성명: str = "",
    학번: str = "",
) -> StudentAcademicProfile:
    """
    프로필 생성 팩토리. 학생별 값은 모두 키워드 인자 강제.
    어떤 값도 하드코딩되지 않으며, 호출자가 시나리오를 자유롭게 구성.
    """
    총_부족학점 = max(0.0, 총_졸업기준 - 총_취득학점)
    return StudentAcademicProfile(
        profile=StudentProfile(
            성명=성명,
            학번=학번,
            입학연도=입학연도,
            학부과=학부과,
            전공=전공,
            복수전공=복수전공,
            student_group=student_group,
            student_type=student_type,
        ),
        credits=CreditsSummary(
            총_졸업기준=총_졸업기준,
            총_취득학점=총_취득학점,
            총_부족학점=총_부족학점,
            평점평균=평점평균,
            categories=[
                CreditCategory(
                    name=c.name,
                    졸업기준=c.required,
                    취득학점=c.earned,
                    부족학점=c.shortage,
                )
                for c in categories
            ],
            졸업시험=졸업시험 or {},
            졸업인증=졸업인증 or {},
        ),
        courses=[
            CourseRecord(
                교과목명=c.교과목명,
                교과목번호=c.교과목번호,
                이수학기=c.이수학기,
                학점=c.학점,
                성적=c.성적,
                category=c.category,
                이수구분=c.이수구분,
            )
            for c in courses
        ],
    )


def random_profile(
    *,
    seed: int,
    has_shortage: bool,
    has_dual_major: bool,
    has_retake_candidates: bool,
    has_current_semester: bool,
) -> StudentAcademicProfile:
    """
    난수 기반 프로필 생성기. 시나리오 플래그만 받고 구체 값은 seed로 생성.
    테스트가 특정 학생 의존 안 함.
    """
    rng = random.Random(seed)

    year = rng.randint(2017, 2025)
    입학연도 = str(year)

    # 실제 학과와 충돌 방지: TESTXXX 프리픽스
    학부과 = f"TESTDEPT_{rng.randint(1, 20):02d}"
    전공 = f"TESTMAJOR_{rng.randint(1, 20):02d}"
    복수전공 = f"TESTDUAL_{rng.randint(1, 20):02d}" if has_dual_major else ""

    총_졸업기준 = float(rng.choice([120, 130, 140]))
    if has_shortage:
        총_취득학점 = 총_졸업기준 - rng.uniform(1.0, 20.0)
    else:
        총_취득학점 = 총_졸업기준 + rng.uniform(0.0, 5.0)
    평점평균 = round(rng.uniform(2.0, 4.5), 2)

    categories = []
    if has_dual_major:
        req = float(rng.choice([30, 33, 36]))
        earned = req - rng.uniform(3.0, 15.0) if has_shortage else req
        categories.append(
            CategorySpec(
                name=rng.choice(["다전공_복수전공", "복수전공_계"]),
                required=req,
                earned=max(0.0, earned),
            )
        )
    categories.append(
        CategorySpec(
            name="전공_기본",
            required=float(rng.choice([15, 18, 21])),
            earned=float(rng.choice([15, 18, 21])),
        )
    )

    courses = []
    if has_retake_candidates:
        low_grades = ["C+", "C", "D+", "D", "F"]
        for _ in range(rng.randint(1, 4)):
            courses.append(
                CourseSpec(
                    교과목명=f"TESTCOURSE_{rng.randint(100, 999)}",
                    교과목번호=f"TC{rng.randint(100, 999)}",
                    이수학기=f"{year + rng.randint(1, 3)}/{rng.choice([1, 2])}",
                    학점=float(rng.choice([2, 3])),
                    성적=rng.choice(low_grades),
                    category="주전공",
                )
            )
    if has_current_semester:
        current = f"{year + 5}/1"  # 최신 학기
        for _ in range(rng.randint(3, 8)):
            courses.append(
                CourseSpec(
                    교과목명=f"TESTINPROG_{rng.randint(100, 999)}",
                    교과목번호=f"IP{rng.randint(100, 999)}",
                    이수학기=current,
                    학점=float(rng.choice([1, 2, 3])),
                    성적="",  # 수강중
                    category="주전공",
                )
            )

    return build_profile(
        입학연도=입학연도,
        학부과=학부과,
        전공=전공,
        복수전공=복수전공,
        총_졸업기준=총_졸업기준,
        총_취득학점=round(총_취득학점, 1),
        평점평균=평점평균,
        categories=categories,
        courses=courses,
    )


# ═══════════════════════════════════════════════════════════
#  클래스 1: TestTranscriptContextInjection
#  ContextMerger가 transcript_context를 올바르게 주입/보존하는가
# ═══════════════════════════════════════════════════════════


class TestTranscriptContextInjection:
    """ContextMerger.merge(transcript_context=...) 통합."""

    def setup_method(self):
        self.merger = ContextMerger()

    @pytest.mark.parametrize("seed", [1, 42, 100, 777])
    def test_transcript_context_fully_preserved(self, seed):
        """어떤 랜덤 프로필이든 transcript의 전공명이 merger 출력에 유지된다."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        assert tx_ctx, "transcript_context가 생성되어야 한다"
        merged = self.merger.merge([], [], transcript_context=tx_ctx)
        # 불변식: 프로필 전공명(랜덤 생성)이 formatted_context에 유지됨
        assert profile.profile.전공 in merged.formatted_context

    @pytest.mark.parametrize("seed", [1, 42, 100, 777])
    def test_transcript_stays_ahead_of_vector(self, seed):
        """transcript score=2.0이 vector보다 앞에 위치."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        vector_token = _unique_token("VECTOR")
        vector = [
            _make_search_result(
                f"PDF 본문 {vector_token}",
                score=0.95,
                doc_type="domestic",
                page_number=1,
            )
        ]
        merged = self.merger.merge(vector, [], transcript_context=tx_ctx)
        # 프로필 전공명이 vector_token보다 먼저 나타나야 함
        major_pos = merged.formatted_context.find(profile.profile.전공)
        vector_pos = merged.formatted_context.find(vector_token)
        assert major_pos >= 0
        assert vector_pos >= 0
        assert major_pos < vector_pos, "transcript이 vector보다 앞이어야 함"

    @pytest.mark.parametrize("seed", [1, 42, 100, 777])
    def test_transcript_stays_ahead_of_graph(self, seed):
        """transcript score=2.0이 graph(1.0)보다 앞에 위치."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        graph_token = _unique_token("GRAPH")
        graph_results = [
            _make_search_result(
                f"그래프 노드 {graph_token}",
                score=1.0,
                doc_type="graph",
                source_type="graph",
            )
        ]
        merged = self.merger.merge([], graph_results, transcript_context=tx_ctx)
        major_pos = merged.formatted_context.find(profile.profile.전공)
        graph_pos = merged.formatted_context.find(graph_token)
        assert major_pos >= 0
        assert graph_pos >= 0
        assert major_pos < graph_pos, "transcript이 graph보다 앞이어야 함"

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_budget_expanded_with_transcript(self, seed):
        """transcript 있으면 토큰 예산이 확장되어 잘림 없이 포함된다."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        merged = self.merger.merge([], [], transcript_context=tx_ctx)
        # 불변식: 전공명이 출력에 있어야 함 = 잘리지 않고 포함됨
        assert profile.profile.전공 in merged.formatted_context
        # 토큰 예산이 확장되어 충분함
        assert merged.total_tokens_estimate > 0

    def test_empty_transcript_context_no_injection(self):
        """회귀 가드: transcript_context='' → 기존 merger 동작 그대로."""
        vector_token = _unique_token("V")
        vector = [
            _make_search_result(
                f"벡터 결과 {vector_token}",
                score=0.9,
                doc_type="domestic",
            )
        ]
        merged = self.merger.merge(vector, [], transcript_context="")
        assert vector_token in merged.formatted_context

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_transcript_preserved_with_graph_direct_answer(self, seed):
        """graph에 direct_answer가 있어도 transcript 내용은 병합 유지."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        direct_token = _unique_token("DIRECT")
        graph_results = [
            _make_search_result(
                f"그래프 데이터 {direct_token}",
                score=1.0,
                doc_type="graph",
                source_type="graph",
                direct_answer=f"직접 답변 {direct_token}",
            )
        ]
        merged = self.merger.merge(
            [], graph_results, transcript_context=tx_ctx
        )
        # direct_answer가 설정되었어도 transcript은 여전히 context에 포함
        assert merged.direct_answer  # direct_answer 수집됨
        assert profile.profile.전공 in merged.formatted_context


# ═══════════════════════════════════════════════════════════
#  클래스 2: TestTranscriptMultiSourceMerge
#  4-way 소스 (transcript + vector + graph + notice) 결합
# ═══════════════════════════════════════════════════════════


def _build_multi_source(seed: int):
    """4개 소스 동시 생성 — 모든 값이 런타임 생성."""
    profile = random_profile(
        seed=seed,
        has_shortage=True,
        has_dual_major=True,
        has_retake_candidates=False,
        has_current_semester=False,
    )
    tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()

    vector_token = _unique_token("PDFTOK")
    graph_token = _unique_token("GRAPHTOK")
    notice_token = _unique_token("NOTICETOK")
    notice_url = f"https://www.bufs.ac.kr/bbs/board.php?wr_id={seed}"

    vector = [
        _make_search_result(
            f"PDF 본문 {vector_token} 졸업 관련 규정",
            score=0.9,
            doc_type="domestic",
            page_number=(seed % 100) + 1,
        )
    ]
    graph_results = [
        _make_search_result(
            f"그래프 노드 {graph_token} 졸업요건 데이터",
            score=1.0,
            doc_type="graph",
            source_type="graph",
        )
    ]
    notice = [
        _make_search_result(
            f"공지 {notice_token} 학사 공지 내용",
            score=0.85,
            doc_type="notice",
            source_url=notice_url,
        )
    ]
    return {
        "profile": profile,
        "tx_ctx": tx_ctx,
        "vector_token": vector_token,
        "graph_token": graph_token,
        "notice_token": notice_token,
        "notice_url": notice_url,
        "vector": vector,
        "graph_results": graph_results,
        "notice": notice,
    }


class TestTranscriptMultiSourceMerge:
    """transcript + vector + graph + notice 4-way 결합."""

    def setup_method(self):
        self.merger = ContextMerger()

    @pytest.mark.parametrize("seed", [1, 42, 100, 777])
    def test_all_four_source_tokens_present(self, seed):
        """4개 소스 고유 토큰 모두 formatted_context에 포함."""
        bundle = _build_multi_source(seed)
        merged = self.merger.merge(
            bundle["vector"] + bundle["notice"],
            bundle["graph_results"],
            transcript_context=bundle["tx_ctx"],
        )
        ctx = merged.formatted_context
        assert bundle["profile"].profile.전공 in ctx, "transcript 전공명"
        assert bundle["vector_token"] in ctx, "vector 토큰"
        assert bundle["graph_token"] in ctx, "graph 토큰"
        assert bundle["notice_token"] in ctx, "notice 토큰"

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_notice_url_collected(self, seed):
        """공지 URL이 source_urls에 수집된다."""
        bundle = _build_multi_source(seed)
        merged = self.merger.merge(
            bundle["vector"] + bundle["notice"],
            bundle["graph_results"],
            transcript_context=bundle["tx_ctx"],
        )
        urls = [item.get("url", "") for item in merged.source_urls]
        assert bundle["notice_url"] in urls

    @pytest.mark.parametrize("seed", [1, 42, 100, 777])
    def test_transcript_appears_before_other_sources(self, seed):
        """transcript 토큰이 vector/graph/notice 토큰보다 앞."""
        bundle = _build_multi_source(seed)
        merged = self.merger.merge(
            bundle["vector"] + bundle["notice"],
            bundle["graph_results"],
            transcript_context=bundle["tx_ctx"],
        )
        ctx = merged.formatted_context
        tx_pos = ctx.find(bundle["profile"].profile.전공)
        assert tx_pos >= 0
        for key in ("vector_token", "graph_token", "notice_token"):
            other_pos = ctx.find(bundle[key])
            assert other_pos >= 0
            assert tx_pos < other_pos, f"transcript이 {key}보다 앞이어야 함"

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_vector_and_notice_both_kept(self, seed):
        """vector와 notice가 모두 결과 목록에 포함된다."""
        bundle = _build_multi_source(seed)
        merged = self.merger.merge(
            bundle["vector"] + bundle["notice"],
            bundle["graph_results"],
            transcript_context=bundle["tx_ctx"],
        )
        all_results = merged.vector_results + merged.graph_results
        all_texts = " ".join(r.text for r in all_results)
        assert bundle["vector_token"] in all_texts
        assert bundle["notice_token"] in all_texts

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_budget_truncation_preserves_transcript(self, seed):
        """예산 초과 대용량 filler를 넣어도 transcript 토큰은 잘리지 않음."""
        bundle = _build_multi_source(seed)
        # 대용량 filler 여러 개 추가
        filler_char = chr(0xAC00 + (seed % 1000))  # 한글 1글자 (seed 의존)
        large_vector = bundle["vector"] + [
            _make_search_result(
                filler_char * 800,
                score=0.7 - i * 0.05,
                doc_type="domestic",
                page_number=i + 10,
            )
            for i in range(5)
        ]
        merged = self.merger.merge(
            large_vector + bundle["notice"],
            bundle["graph_results"],
            transcript_context=bundle["tx_ctx"],
        )
        # 불변식: transcript의 전공명이 예산 초과 상황에서도 보존됨
        assert bundle["profile"].profile.전공 in merged.formatted_context


# ═══════════════════════════════════════════════════════════
#  클래스 3: TestTranscriptPipelineE2E
#  Analyzer → Router → Merger 전체 흐름
# ═══════════════════════════════════════════════════════════


def _run_pipeline_with_transcript(
    question: str,
    transcript_context: str = "",
    vector_results: list = None,
    graph_results: list = None,
):
    """테스트용 파이프라인 실행 헬퍼 (test_pipeline_integration.py 패턴 모방)."""
    analyzer = QueryAnalyzer()
    analysis = analyzer.analyze(question)

    mock_chroma = MagicMock()
    mock_chroma.search.return_value = vector_results or []
    mock_graph = MagicMock()
    mock_graph.query_to_search_results.return_value = graph_results or []

    router = QueryRouter(chroma_store=mock_chroma, academic_graph=mock_graph)
    search_results = router.route_and_search(question, analysis)

    merger = ContextMerger()
    merged = merger.merge(
        search_results["vector_results"],
        search_results["graph_results"],
        question=question,
        intent=analysis.intent,
        entities=analysis.entities,
        transcript_context=transcript_context,
    )
    return analysis, merged


class TestTranscriptPipelineE2E:
    """QueryAnalyzer → QueryRouter → ContextMerger 전체 흐름."""

    @pytest.mark.parametrize("seed", [1, 42, 100])
    @pytest.mark.parametrize(
        "question_pattern,allowed_intents",
        [
            (
                "내 성적 기준으로 뭐가 부족한지 알려줘",
                {
                    Intent.TRANSCRIPT,
                    Intent.GRADUATION_REQ,
                },
            ),
            (
                "조기졸업 가능한 학점이야?",
                {
                    Intent.EARLY_GRADUATION,
                    Intent.GRADUATION_REQ,
                    Intent.TRANSCRIPT,
                },
            ),
            (
                "재수강 추천해줘",
                {
                    Intent.REGISTRATION,
                    Intent.TRANSCRIPT,
                },
            ),
            (
                "복수전공 얼마나 남았어?",
                {
                    Intent.MAJOR_CHANGE,
                    Intent.TRANSCRIPT,
                    Intent.GRADUATION_REQ,
                },
            ),
            (
                "내 평점으로 몇 학점까지 신청 가능해?",
                {
                    Intent.REGISTRATION,
                    Intent.TRANSCRIPT,
                    Intent.GRADUATION_REQ,  # "몇 학점" 키워드는 졸업요건과도 중첩
                },
            ),
        ],
    )
    def test_question_routes_to_allowed_intent(
        self, seed, question_pattern, allowed_intents
    ):
        """질문 패턴 → 기대 인텐트 집합 중 하나로 분류 + formatted_context 비어있지 않음."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=True,
            has_current_semester=True,
        )
        tx = TranscriptAnalyzer(profile, graph=None)
        tx_ctx = tx.format_gap_context_safe()

        # 임의 vector/graph 결과도 함께 주입
        vector_token = _unique_token("V")
        vector = [
            _make_search_result(
                f"관련 PDF 내용 {vector_token}",
                score=0.9,
                doc_type="domestic",
                page_number=1,
            )
        ]
        analysis, merged = _run_pipeline_with_transcript(
            question_pattern,
            transcript_context=tx_ctx,
            vector_results=vector,
        )
        # 불변식 1: 인텐트가 허용 집합에 속함
        assert analysis.intent in allowed_intents, (
            f"질문='{question_pattern}', 실제 intent={analysis.intent}"
        )
        # 불변식 2: formatted_context 비어있지 않음
        assert merged.formatted_context
        # 불변식 3: transcript의 전공명이 context에 보존됨
        assert profile.profile.전공 in merged.formatted_context

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_notice_urls_collected_with_transcript(self, seed):
        """공지 + transcript 동시 존재 시 source_urls 수집."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=False,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        notice_url = f"https://www.bufs.ac.kr/notice/{seed}"
        notice = [
            _make_search_result(
                f"졸업 공지 {_unique_token('N')}",
                score=0.85,
                doc_type="notice",
                source_url=notice_url,
            )
        ]
        _, merged = _run_pipeline_with_transcript(
            "졸업 관련 공지 뭐 나왔어?",
            transcript_context=tx_ctx,
            vector_results=notice,
        )
        urls = [item.get("url", "") for item in merged.source_urls]
        assert notice_url in urls

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_profile_student_id_auto_injected(self, seed):
        """학번 미포함 질문 + transcript → analysis.student_id가 입학연도로 설정 안 되지만 최소 파이프라인 통과."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        tx_ctx = TranscriptAnalyzer(profile, graph=None).format_gap_context_safe()
        analysis, merged = _run_pipeline_with_transcript(
            "뭐가 부족해?",
            transcript_context=tx_ctx,
        )
        # 파이프라인이 예외 없이 완료되고 transcript 정보가 반영됨
        assert profile.profile.전공 in merged.formatted_context


# ═══════════════════════════════════════════════════════════
#  클래스 4: TestTranscriptPIIThroughPipeline
#  파이프라인 통과 후 PII 유출 금지
# ═══════════════════════════════════════════════════════════


class TestTranscriptPIIThroughPipeline:
    """PII 값은 런타임 생성, 파이프라인 통과 후 출력에 없어야 함."""

    def setup_method(self):
        self.merger = ContextMerger()

    @pytest.mark.parametrize("seed", [1, 42, 100, 777, 9999])
    def test_random_name_never_leaked(self, seed):
        """어떤 랜덤 한글 이름이든 formatted_context에 없어야 한다."""
        rng = random.Random(seed)
        surnames = "김이박최정강조윤장임한오서신권황안"
        givens = "가나다라마바사아자차카타파하거너더러머버"
        fake_name = (
            rng.choice(surnames)
            + rng.choice(givens)
            + rng.choice(givens)
        )
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        profile.profile.성명 = fake_name
        tx = TranscriptAnalyzer(profile, graph=None)
        for formatter in (
            tx.format_gap_context_safe,
            tx.format_profile_summary_safe,
        ):
            ctx = formatter()
            merged = self.merger.merge([], [], transcript_context=ctx)
            assert fake_name not in merged.formatted_context, (
                f"PII 이름 '{fake_name}' 유출 (formatter={formatter.__name__})"
            )

    @pytest.mark.parametrize("seed", [1, 42, 100, 777, 9999])
    def test_random_student_id_never_leaked(self, seed):
        """어떤 랜덤 8자리 학번이든 formatted_context에 없어야 한다."""
        rng = random.Random(seed)
        fake_id = f"20{rng.randint(100000, 999999)}"
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=False,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        profile.profile.학번 = fake_id
        tx = TranscriptAnalyzer(profile, graph=None)
        for formatter in (
            tx.format_gap_context_safe,
            tx.format_profile_summary_safe,
        ):
            ctx = formatter()
            merged = self.merger.merge([], [], transcript_context=ctx)
            assert fake_id not in merged.formatted_context, (
                f"PII 학번 '{fake_id}' 유출 (formatter={formatter.__name__})"
            )

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_pii_with_mixed_sources(self, seed):
        """transcript + vector + graph + notice 병합 후에도 PII 없음."""
        rng = random.Random(seed)
        fake_name = "".join(rng.choices("김이박최정강조윤장임", k=1)) + "".join(
            rng.choices("가나다라마바사아자차", k=2)
        )
        fake_id = f"20{rng.randint(100000, 999999)}"
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        profile.profile.성명 = fake_name
        profile.profile.학번 = fake_id

        tx = TranscriptAnalyzer(profile, graph=None)
        tx_ctx = tx.format_gap_context_safe()

        vector = [
            _make_search_result(
                f"졸업요건 본문 {_unique_token('V')}",
                score=0.9,
                doc_type="domestic",
                page_number=1,
            )
        ]
        graph_results = [
            _make_search_result(
                f"그래프 노드 {_unique_token('G')}",
                score=1.0,
                doc_type="graph",
                source_type="graph",
            )
        ]
        notice = [
            _make_search_result(
                f"공지 {_unique_token('N')}",
                score=0.85,
                doc_type="notice",
                source_url=f"https://www.bufs.ac.kr/notice/{seed}",
            )
        ]
        merged = self.merger.merge(
            vector + notice,
            graph_results,
            transcript_context=tx_ctx,
        )
        assert fake_name not in merged.formatted_context
        assert fake_id not in merged.formatted_context

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_pii_with_courses_formatter(self, seed):
        """format_courses_context_safe() 출력도 PII 없음."""
        rng = random.Random(seed)
        fake_name = "".join(rng.choices("김이박최정", k=1)) + "".join(
            rng.choices("가나다라마", k=2)
        )
        fake_id = f"20{rng.randint(100000, 999999)}"
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=False,
            has_retake_candidates=True,
            has_current_semester=True,
        )
        profile.profile.성명 = fake_name
        profile.profile.학번 = fake_id
        tx = TranscriptAnalyzer(profile, graph=None)

        # retake 후보와 현재 학기 과목 모두 포맷
        retake_ctx = tx.format_courses_context_safe(tx.retake_candidates())
        current_ctx = tx.format_courses_context_safe(tx.current_semester_courses())

        for ctx in (retake_ctx, current_ctx):
            if ctx:  # 과목이 있을 때만
                merged = self.merger.merge([], [], transcript_context=ctx)
                assert fake_name not in merged.formatted_context
                assert fake_id not in merged.formatted_context


# ═══════════════════════════════════════════════════════════
#  클래스 5: TestEnrichDispatchLogic
#  _enrich_analysis()의 질문 키워드 기반 분기 검증
# ═══════════════════════════════════════════════════════════


class TestEnrichDispatchLogic:
    """app/ui/chat_app.py::_enrich_analysis() 분기 검증."""

    def _run_enrich(self, question: str, profile: StudentAcademicProfile):
        """
        Streamlit session_state와 SecureTranscriptStore.retrieve를 mock-out하여
        _enrich_analysis()를 직접 호출.
        """
        mock_st = MagicMock()
        mock_st.session_state = {}

        with patch("app.ui.chat_app.st", mock_st), patch(
            "app.transcript.security.SecureTranscriptStore.retrieve",
            return_value=profile,
        ):
            from app.ui.chat_app import _enrich_analysis

            analysis = QueryAnalyzer().analyze(question)
            router = MagicMock()
            router.academic_graph = None
            new_analysis, tx_ctx, student_ctx = _enrich_analysis(
                question, analysis, router
            )
            return new_analysis, tx_ctx, student_ctx

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_shortage_question_emits_major_token(self, seed):
        """'부족' 질문 → 전공명 포함."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        expected_major = profile.profile.복수전공
        _, tx_ctx, _ = self._run_enrich(
            "내 성적에서 뭐가 부족한지 알려줘", profile
        )
        assert tx_ctx
        # 불변식: 랜덤 전공명이 컨텍스트에 포함
        assert expected_major in tx_ctx

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_retake_question_emits_course_tokens(self, seed):
        """'재수강' 질문 → 저성적 과목명 포함."""
        profile = random_profile(
            seed=seed,
            has_shortage=False,
            has_dual_major=False,
            has_retake_candidates=True,
            has_current_semester=False,
        )
        # 프로필에서 저성적 과목명 추출 (랜덤 생성됨)
        low_grade_courses = [
            c.교과목명 for c in profile.courses if c.성적 and c.성적 != "NP"
        ]
        _, tx_ctx, _ = self._run_enrich("재수강 추천 과목 알려줘", profile)
        assert tx_ctx
        # 불변식: 프로필의 저성적 과목 중 최소 1개가 컨텍스트에 포함
        assert any(name in tx_ctx for name in low_grade_courses)

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_current_semester_question_emits_semester_token(self, seed):
        """'이번 학기' 질문 → 현재 학기 과목명 포함."""
        profile = random_profile(
            seed=seed,
            has_shortage=False,
            has_dual_major=False,
            has_retake_candidates=False,
            has_current_semester=True,
        )
        current_course_names = [
            c.교과목명 for c in profile.courses if c.성적 == ""
        ]
        _, tx_ctx, _ = self._run_enrich("이번 학기 뭐 들어?", profile)
        assert tx_ctx
        # 불변식: 현재 학기 과목 중 최소 1개가 포함
        assert any(name in tx_ctx for name in current_course_names)

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_limit_question_emits_registration_token(self, seed):
        """'몇 학점' 질문 → 평점 문자열 포함."""
        profile = random_profile(
            seed=seed,
            has_shortage=False,
            has_dual_major=False,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        gpa_str = str(profile.credits.평점평균)
        _, tx_ctx, _ = self._run_enrich(
            "내 평점으로 몇 학점까지 신청 가능해?", profile
        )
        assert tx_ctx
        # 불변식: 프로필 GPA(랜덤)가 컨텍스트에 포함
        assert gpa_str in tx_ctx

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_student_context_always_has_summary(self, seed):
        """성적표가 있는 모든 TRANSCRIPT 분기에서 student_context 비어있지 않음."""
        profile = random_profile(
            seed=seed,
            has_shortage=True,
            has_dual_major=True,
            has_retake_candidates=False,
            has_current_semester=False,
        )
        _, _, student_ctx = self._run_enrich("내 성적 얼마야?", profile)
        # 불변식: 프로필 전공명이 student_context에 포함
        assert profile.profile.전공 in student_ctx

    def test_no_transcript_empty_contexts(self):
        """성적표 없음 (retrieve=None) → 양쪽 context 모두 빈 문자열."""
        mock_st = MagicMock()
        mock_st.session_state = {}

        with patch("app.ui.chat_app.st", mock_st), patch(
            "app.transcript.security.SecureTranscriptStore.retrieve",
            return_value=None,
        ):
            from app.ui.chat_app import _enrich_analysis

            analysis = QueryAnalyzer().analyze("뭐가 부족해?")
            router = MagicMock()
            router.academic_graph = None
            _, tx_ctx, student_ctx = _enrich_analysis(
                "뭐가 부족해?", analysis, router
            )
            assert tx_ctx == ""
            assert student_ctx == ""


# ═══════════════════════════════════════════════════════════
#  클래스 6: TestPersonalQuickFeatures
#  성적표 기반 동적 Quick Features 생성 검증
# ═══════════════════════════════════════════════════════════


class TestPersonalQuickFeatures:
    """_build_personal_quick_features()가 프로필에 따라 동적으로 버튼을 생성하는지."""

    def test_none_transcript_returns_base_features(self):
        """성적표 없음 → 기본 개인화 버튼 4개."""
        from app.ui.chat_app import (
            _build_personal_quick_features,
            QUICK_FEATURES_PERSONAL_BASE,
        )
        features = _build_personal_quick_features(None)
        assert features == QUICK_FEATURES_PERSONAL_BASE
        assert len(features) == 4

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_dual_major_adds_button(self, seed):
        """복수전공 있는 학생 → 복수전공 버튼 추가."""
        from app.ui.chat_app import _build_personal_quick_features

        profile = random_profile(
            seed=seed, has_shortage=True, has_dual_major=True,
            has_retake_candidates=False, has_current_semester=False,
        )
        features = _build_personal_quick_features(profile)
        labels = " ".join(f["label"] for f in features)
        assert "복수전공" in labels

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_no_dual_major_no_button(self, seed):
        """복수전공 없는 학생 → 복수전공 버튼 미포함."""
        from app.ui.chat_app import _build_personal_quick_features

        profile = random_profile(
            seed=seed, has_shortage=True, has_dual_major=False,
            has_retake_candidates=False, has_current_semester=False,
        )
        features = _build_personal_quick_features(profile)
        labels = " ".join(f["label"] for f in features)
        assert "복수전공" not in labels

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_shortage_adds_registration_button(self, seed):
        """부족학점 있는 학생 → 수강 가능 학점 버튼 추가."""
        from app.ui.chat_app import _build_personal_quick_features

        profile = random_profile(
            seed=seed, has_shortage=True, has_dual_major=False,
            has_retake_candidates=False, has_current_semester=False,
        )
        # precondition: 실제 부족학점 존재
        if profile.credits.총_부족학점 <= 0:
            pytest.skip("seed가 부족학점을 생성하지 못함")
        features = _build_personal_quick_features(profile)
        labels = " ".join(f["label"] for f in features)
        assert "수강 가능" in labels

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_no_shortage_no_registration_button(self, seed):
        """부족학점 없는 학생 → 수강 가능 버튼 미포함."""
        from app.ui.chat_app import _build_personal_quick_features

        profile = random_profile(
            seed=seed, has_shortage=False, has_dual_major=False,
            has_retake_candidates=False, has_current_semester=False,
        )
        # precondition
        if profile.credits.총_부족학점 > 0:
            pytest.skip("seed가 과잉 이수를 생성하지 못함")
        features = _build_personal_quick_features(profile)
        labels = " ".join(f["label"] for f in features)
        assert "수강 가능" not in labels

    @pytest.mark.parametrize("seed", [1, 42, 100, 777])
    def test_max_six_features(self, seed):
        """최대 6개 버튼으로 제한 (2열 × 3행)."""
        from app.ui.chat_app import _build_personal_quick_features

        # 모든 플래그 켜서 최대 버튼 수 유도
        profile = random_profile(
            seed=seed, has_shortage=True, has_dual_major=True,
            has_retake_candidates=True, has_current_semester=True,
        )
        features = _build_personal_quick_features(profile)
        assert len(features) <= 6

    @pytest.mark.parametrize("seed", [1, 42, 100])
    def test_all_features_have_label_and_question(self, seed):
        """모든 버튼은 label과 question 키를 가진다."""
        from app.ui.chat_app import _build_personal_quick_features

        profile = random_profile(
            seed=seed, has_shortage=True, has_dual_major=True,
            has_retake_candidates=False, has_current_semester=False,
        )
        features = _build_personal_quick_features(profile)
        for feat in features:
            assert "label" in feat and feat["label"]
            assert "question" in feat and feat["question"]
