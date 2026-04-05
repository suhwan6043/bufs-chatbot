"""
TranscriptAnalyzer 기능 테스트.

실제 질문 시나리오 검증:
  - "졸업까지 뭐가 부족해?" → graduation_gap()
  - "이번 학기 뭐 들어?"   → current_semester_courses()
  - "재수강 추천 과목?"     → retake_candidates()
  - "복수전공 얼마나 했어?" → dual_major_status()
  - 포맷터 출력에 PII 없는가

실제 XLS 파일(20260404205953601.xls)이 있을 때만 실행.
"""

from pathlib import Path

import pytest

from app.transcript.analyzer import TranscriptAnalyzer, _GRADE_ORDER
from app.transcript.models import StudentAcademicProfile, StudentProfile, CreditsSummary

_XLS_PATH = Path("C:/Users/User/Downloads/20260404205953601.xls")


# ══════════════════════════════════════════════════════
# 공통 fixture
# ══════════════════════════════════════════════════════

@pytest.fixture(scope="module")
def parsed():
    """XLS 파싱 결과 — 모듈 당 한 번만 실행."""
    from app.transcript.parser import TranscriptParser
    with open(_XLS_PATH, "rb") as f:
        data = f.read()
    return TranscriptParser().parse(data, _XLS_PATH.name)


@pytest.fixture(scope="module")
def analyzer(parsed):
    """그래프 없는 TranscriptAnalyzer (그래프 의존성 배제)."""
    return TranscriptAnalyzer(parsed, graph=None)


# ══════════════════════════════════════════════════════
# "졸업까지 뭐가 부족해?" — graduation_gap()
# ══════════════════════════════════════════════════════

@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestAnalyzerGraduationGap:
    def test_gap_totals(self, analyzer):
        gap = analyzer.graduation_gap()
        assert gap["총_취득학점"] == 126.5
        assert gap["총_부족학점"] == 3.5
        assert gap["총_졸업기준"] == 130.0

    def test_gap_gpa(self, analyzer):
        gap = analyzer.graduation_gap()
        assert gap["평점평균"] == 3.97

    def test_gap_categories_present(self, analyzer):
        """카테고리 목록이 비어 있지 않아야 한다."""
        gap = analyzer.graduation_gap()
        assert len(gap["categories"]) > 0

    def test_gap_deficient_categories_include_dual_major(self, analyzer):
        """부족 카테고리에 복수전공 관련 항목이 있어야 한다."""
        gap = analyzer.graduation_gap()
        deficient_names = [
            c["name"] for c in gap["categories"] if c["상태"] == "부족"
        ]
        assert any(
            "복수전공" in name or "다전공" in name
            for name in deficient_names
        ), f"복수전공 부족 카테고리 없음: {deficient_names}"

    def test_gap_exam_status(self, analyzer):
        gap = analyzer.graduation_gap()
        assert gap["졸업시험"].get("주전공") == "N"

    def test_gap_cert_status(self, analyzer):
        gap = analyzer.graduation_gap()
        assert gap["졸업인증"].get("기업정신") == "Y"

    def test_gap_memoization(self, analyzer):
        """두 번 호출하면 같은 객체를 반환해야 한다 (캐시 검증)."""
        gap1 = analyzer.graduation_gap()
        gap2 = analyzer.graduation_gap()
        assert gap1 is gap2

    def test_gap_no_negative_shortage(self, analyzer):
        """부족학점이 음수인 카테고리가 없어야 한다."""
        gap = analyzer.graduation_gap()
        for cat in gap["categories"]:
            assert cat["부족"] >= 0, f"음수 부족학점 발견: {cat}"


# ══════════════════════════════════════════════════════
# "이번 학기 뭐 들어?" — current_semester_courses()
# ══════════════════════════════════════════════════════

@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestAnalyzerCurrentSemester:
    def test_current_semester_count(self, analyzer):
        courses = analyzer.current_semester_courses()
        assert len(courses) >= 7, f"2026/1 과목수 부족: {len(courses)}"

    def test_current_semester_label(self, analyzer):
        """반환된 모든 과목이 최신 학기에 속해야 한다."""
        courses = analyzer.current_semester_courses()
        assert len(courses) > 0
        assert all(c.이수학기 == "2026/1" for c in courses), \
            f"2026/1 이외 학기 포함: {[c.이수학기 for c in courses]}"

    def test_current_semester_has_in_progress(self, analyzer):
        """수강중(성적 미확정) 과목이 적어도 하나 있어야 한다."""
        courses = analyzer.current_semester_courses()
        in_progress = [c for c in courses if not c.성적]
        assert len(in_progress) >= 1, "수강중 과목 없음"

    def test_current_semester_has_course_numbers(self, analyzer):
        """교과목번호가 비어 있지 않아야 한다."""
        courses = analyzer.current_semester_courses()
        assert all(c.교과목번호 for c in courses), "교과목번호 빈 항목 발견"

    def test_current_semester_credits_positive(self, analyzer):
        """각 과목의 학점이 0보다 커야 한다."""
        courses = analyzer.current_semester_courses()
        assert all(c.학점 > 0 for c in courses), "학점 0인 과목 발견"


# ══════════════════════════════════════════════════════
# "재수강 추천 과목?" — retake_candidates()
# ══════════════════════════════════════════════════════

@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestAnalyzerRetakeCandidates:
    def test_retake_has_results(self, analyzer):
        candidates = analyzer.retake_candidates()
        assert len(candidates) >= 1, "재수강 후보 과목 없음"

    def test_retake_no_pnp_grades(self, analyzer):
        """P/NP 과목은 재수강 후보에 포함되면 안 된다."""
        candidates = analyzer.retake_candidates()
        for c in candidates:
            assert c.성적 not in ("P", "NP", ""), \
                f"P/NP/미확정 과목 포함: {c.교과목명} ({c.성적})"

    def test_retake_grade_threshold(self, analyzer):
        """B0 이하(rank <= 7) 과목만 포함되어야 한다."""
        candidates = analyzer.retake_candidates(threshold="B0")
        for c in candidates:
            rank = _GRADE_ORDER.get(c.성적, -1)
            assert rank <= 7, \
                f"B+ 이상 과목이 후보에 포함됨: {c.교과목명} ({c.성적}, rank={rank})"

    def test_retake_known_course_present(self, analyzer):
        """C 성적 이하의 알려진 과목이 후보에 있어야 한다."""
        candidates = analyzer.retake_candidates()
        names = [c.교과목명 for c in candidates]
        found = any(
            "English" in n or "IOT" in n or "프로그래밍논리" in n
            for n in names
        )
        assert found, f"알려진 저성적 과목 없음: {names[:10]}"

    def test_retake_sorted_worst_first(self, analyzer):
        """성적이 낮은 순(rank 오름차순)으로 정렬되어야 한다."""
        candidates = analyzer.retake_candidates()
        if len(candidates) < 2:
            pytest.skip("후보 1개 이하라 정렬 검증 불가")
        ranks = [_GRADE_ORDER.get(c.성적, 0) for c in candidates]
        assert ranks == sorted(ranks), f"정렬 오류: {ranks}"

    def test_retake_strict_threshold(self, analyzer):
        """threshold=C 로 좁히면 후보 수가 줄어야 한다."""
        candidates_b0 = analyzer.retake_candidates(threshold="B0")
        candidates_c = analyzer.retake_candidates(threshold="C")
        assert len(candidates_c) <= len(candidates_b0)


# ══════════════════════════════════════════════════════
# "복수전공 얼마나 했어?" — dual_major_status()
# ══════════════════════════════════════════════════════

@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestAnalyzerDualMajor:
    def test_dual_active(self, analyzer):
        status = analyzer.dual_major_status()
        assert status["active"] is True

    def test_dual_major_name(self, analyzer):
        status = analyzer.dual_major_status()
        assert "스마트융합보안" in status["전공명"], \
            f"전공명 불일치: {status['전공명']}"

    def test_dual_earned_credits_positive(self, analyzer):
        status = analyzer.dual_major_status()
        assert status["취득학점"] > 0

    def test_dual_shortage_non_negative(self, analyzer):
        """부족학점이 0 이상이어야 한다 (9학점 부족 예상)."""
        status = analyzer.dual_major_status()
        assert status["부족학점"] >= 0

    def test_dual_course_count(self, analyzer):
        """복수전공 과목이 적어도 1개 이상 있어야 한다."""
        status = analyzer.dual_major_status()
        assert status["과목수"] >= 1

    def test_dual_shortage_matches_xls(self, analyzer):
        """
        dual_major_status() 부족학점이 XLS 카테고리 직접값과 일치해야 한다.
        (FIX-4: XLS 직접값을 truth source로 사용)
        """
        status = analyzer.dual_major_status()
        credits = analyzer.profile.credits
        cat_shortage = next(
            (c.부족학점 for c in credits.categories
             if "복수전공" in c.name or "다전공" in c.name),
            None,
        )
        assert cat_shortage is not None, "복수전공 카테고리 없음"
        assert cat_shortage > 0, f"복수전공 부족학점 0: {cat_shortage}"
        # FIX-4: dual_major_status()가 XLS 값을 직접 사용하므로 정확히 일치
        assert status["부족학점"] == cat_shortage, \
            f"불일치: status={status['부족학점']}, cat={cat_shortage}"


# ══════════════════════════════════════════════════════
# 포맷터 PII 검증 — format_*_safe()
# ══════════════════════════════════════════════════════

@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestAnalyzerSafeFormatters:
    def test_gap_context_no_name(self, analyzer):
        text = analyzer.format_gap_context_safe()
        assert "박수환" not in text, "이름 유출"

    def test_gap_context_no_student_id(self, analyzer):
        text = analyzer.format_gap_context_safe()
        assert "20201877" not in text, "8자리 학번 유출"

    def test_gap_context_has_major_info(self, analyzer):
        text = analyzer.format_gap_context_safe()
        assert "복수전공" in text, "복수전공 정보 없음"

    def test_gap_context_has_shortage(self, analyzer):
        text = analyzer.format_gap_context_safe()
        assert "부족" in text, "부족 정보 없음"

    def test_gap_context_has_credits(self, analyzer):
        text = analyzer.format_gap_context_safe()
        assert "126.5" in text or "3.5" in text, "학점 수치 없음"

    def test_courses_context_no_pii(self, analyzer):
        courses = analyzer.current_semester_courses()
        text = analyzer.format_courses_context_safe(courses)
        assert "박수환" not in text, "과목 컨텍스트에 이름 유출"
        assert "20201877" not in text, "과목 컨텍스트에 학번 유출"

    def test_courses_context_has_course_names(self, analyzer):
        courses = analyzer.current_semester_courses()
        text = analyzer.format_courses_context_safe(courses)
        assert len(text) > 0
        # 과목명 중 하나라도 텍스트에 포함되어야 함
        assert any(c.교과목명 in text for c in courses[:3])

    def test_courses_context_empty_list(self, analyzer):
        """빈 목록이면 빈 문자열 반환."""
        text = analyzer.format_courses_context_safe([])
        assert text == ""

    def test_profile_summary_no_pii(self, analyzer):
        text = analyzer.format_profile_summary_safe()
        assert "박수환" not in text, "요약에 이름 유출"
        assert "20201877" not in text, "요약에 학번 유출"

    def test_profile_summary_has_gpa(self, analyzer):
        text = analyzer.format_profile_summary_safe()
        assert "3.97" in text, "GPA 없음"

    def test_profile_summary_has_department(self, analyzer):
        text = analyzer.format_profile_summary_safe()
        assert "소프트웨어" in text, "학부/전공 정보 없음"


# ══════════════════════════════════════════════════════
# 그래프 없는 환경에서도 작동하는가
# ══════════════════════════════════════════════════════

@pytest.mark.skipif(not _XLS_PATH.exists(), reason="XLS 파일 없음")
class TestAnalyzerWithoutGraph:
    def test_graduation_gap_no_graph(self, parsed):
        """그래프 없어도 graduation_gap()이 예외 없이 반환."""
        tx = TranscriptAnalyzer(parsed, graph=None)
        gap = tx.graduation_gap()
        assert gap["총_취득학점"] == 126.5

    def test_registration_limit_no_graph(self, parsed):
        """그래프 없으면 적용_최대학점이 None."""
        tx = TranscriptAnalyzer(parsed, graph=None)
        limit = tx.registration_limit()
        assert limit["적용_최대학점"] is None
        assert limit["현재_평점"] == 3.97  # GPA는 성적표에서 직접 읽음

    def test_dual_status_no_graph(self, parsed):
        """dual_major_status()는 그래프 독립 — 그래프 없어도 active=True."""
        tx = TranscriptAnalyzer(parsed, graph=None)
        status = tx.dual_major_status()
        assert status["active"] is True

    def test_current_semester_no_graph(self, parsed):
        """current_semester_courses()는 그래프 독립."""
        tx = TranscriptAnalyzer(parsed, graph=None)
        courses = tx.current_semester_courses()
        assert len(courses) >= 7

    def test_retake_candidates_no_graph(self, parsed):
        """retake_candidates()는 그래프 독립."""
        tx = TranscriptAnalyzer(parsed, graph=None)
        candidates = tx.retake_candidates()
        assert len(candidates) >= 1


# ══════════════════════════════════════════════════════
# 그래프 없이 초기화한 경우 단순 프로파일 테스트
# (XLS 없어도 실행 가능)
# ══════════════════════════════════════════════════════

class TestAnalyzerNoXLS:
    """XLS 파일 없는 환경에서도 실행되는 단위 테스트."""

    def _make_profile(self) -> StudentAcademicProfile:
        from app.transcript.models import (
            CourseRecord, CreditCategory, CreditsSummary, StudentProfile
        )
        return StudentAcademicProfile(
            profile=StudentProfile(
                성명="테스트",
                학번="20201234",
                입학연도="2020",
                학부과="소프트웨어학부",
                전공="소프트웨어전공",
                복수전공="테스트전공",
                student_group="2017_2020",
                학년=4,
                이수학기=8,
            ),
            credits=CreditsSummary(
                총_졸업기준=130.0,
                총_취득학점=110.0,
                총_부족학점=20.0,
                평점평균=3.50,
                categories=[
                    CreditCategory(name="전공_기본", 졸업기준=18, 취득학점=18, 부족학점=0),
                    CreditCategory(name="복수전공", 졸업기준=33, 취득학점=15, 부족학점=18),
                ],
                졸업시험={"주전공": "N"},
                졸업인증={"기업정신": "N"},
            ),
            courses=[
                CourseRecord(교과목명="자료구조", 교과목번호="CSE201", 이수학기="2022/1",
                             학점=3, 성적="A+", category="주전공"),
                CourseRecord(교과목명="운영체제", 교과목번호="CSE301", 이수학기="2022/1",
                             학점=3, 성적="C+", category="주전공"),
                CourseRecord(교과목명="알고리즘", 교과목번호="CSE302", 이수학기="2026/1",
                             학점=3, 성적="", category="주전공"),
                CourseRecord(교과목명="보안개론", 교과목번호="SEC101", 이수학기="2026/1",
                             학점=3, 성적="", category="복수전공", 이수구분="복전"),
            ],
        )

    def test_graduation_gap_basic(self):
        tx = TranscriptAnalyzer(self._make_profile())
        gap = tx.graduation_gap()
        assert gap["총_취득학점"] == 110.0
        assert gap["총_부족학점"] == 20.0

    def test_graduation_gap_deficient(self):
        tx = TranscriptAnalyzer(self._make_profile())
        gap = tx.graduation_gap()
        deficient = [c for c in gap["categories"] if c["상태"] == "부족"]
        assert any("복수전공" in c["name"] for c in deficient)

    def test_current_semester_courses(self):
        tx = TranscriptAnalyzer(self._make_profile())
        courses = tx.current_semester_courses()
        assert len(courses) == 2
        assert all(c.이수학기 == "2026/1" for c in courses)

    def test_retake_candidates_basic(self):
        tx = TranscriptAnalyzer(self._make_profile())
        candidates = tx.retake_candidates(threshold="B0")
        names = [c.교과목명 for c in candidates]
        assert "운영체제" in names  # C+ → rank 6 ≤ 7

    def test_retake_no_in_progress(self):
        """수강중(성적 없음) 과목은 재수강 후보에 포함되지 않아야 한다."""
        tx = TranscriptAnalyzer(self._make_profile())
        candidates = tx.retake_candidates()
        assert all(c.성적 for c in candidates)

    def test_dual_major_status_basic(self):
        tx = TranscriptAnalyzer(self._make_profile())
        status = tx.dual_major_status()
        assert status["active"] is True
        assert status["전공명"] == "테스트전공"
        assert status["부족학점"] >= 0

    def test_format_gap_context_safe_no_pii(self):
        tx = TranscriptAnalyzer(self._make_profile())
        text = tx.format_gap_context_safe()
        assert "테스트" not in text or "20201234" not in text  # 이름 또는 학번 제거
        # 핵심: 8자리 학번은 반드시 없어야 함
        assert "20201234" not in text

    def test_format_profile_summary_safe_no_pii(self):
        tx = TranscriptAnalyzer(self._make_profile())
        text = tx.format_profile_summary_safe()
        assert "20201234" not in text
        assert "3.5" in text  # GPA는 있어야 함

    def test_registration_limit_without_graph(self):
        tx = TranscriptAnalyzer(self._make_profile(), graph=None)
        limit = tx.registration_limit()
        assert limit["현재_평점"] == 3.50
        assert limit["적용_최대학점"] is None
