"""
성적표 분석 엔진 (Lazy 계산).

원칙 2: 동적 최적화 — 질문 의도에 따라 필요한 분석만 수행.
모든 포맷터는 PII를 제거한 안전한 텍스트를 반환합니다.
"""

import logging
from typing import TYPE_CHECKING, Optional

from .models import CourseRecord, StudentAcademicProfile
from .security import PIIRedactor

if TYPE_CHECKING:
    from app.graphdb.academic_graph import AcademicGraph

logger = logging.getLogger(__name__)

# 성적 순위 (높은 게 좋음)
_GRADE_ORDER = {
    "A+": 10, "A": 9, "A0": 9,
    "B+": 8, "B": 7, "B0": 7,
    "C+": 6, "C": 5, "C0": 5,
    "D+": 4, "D": 3, "D0": 3,
    "F": 0, "P": -1, "NP": -2,
}


class TranscriptAnalyzer:
    """
    세션 레벨 lazy 분석기.

    - graduation_gap(): 졸업기준 vs 취득학점 교차 비교
    - current_semester_courses(): 현재 학기 수강 과목
    - retake_candidates(): 재수강 추천
    - dual_major_status(): 복수전공 현황
    - registration_limit(): 수강신청 최대학점

    모든 format_*_safe() 메서드는 PIIRedactor를 거칩니다.
    """

    def __init__(
        self,
        profile: StudentAcademicProfile,
        graph: Optional["AcademicGraph"] = None,
    ):
        self.profile = profile
        self.graph = graph
        self._gap_cache: Optional[dict] = None

    # ── 졸업 갭 분석 ─────────────────────────────────

    def graduation_gap(self) -> dict:
        """
        성적표 취득학점 vs 그래프 졸업기준 교차 비교.

        Returns:
            {
                "총_졸업기준": float,
                "총_취득학점": float,
                "총_부족학점": float,
                "평점평균": float,
                "categories": [{"name", "기준", "취득", "부족", "상태"}],
                "졸업시험": dict,
                "졸업인증": dict,
                "graph_requirements": dict,  # 그래프에서 가져온 졸업요건
            }
        """
        if self._gap_cache is not None:
            return self._gap_cache

        credits = self.profile.credits
        result = {
            "총_졸업기준": credits.총_졸업기준,
            "총_취득학점": credits.총_취득학점,
            "총_부족학점": credits.총_부족학점,
            "평점평균": credits.평점평균,
            "categories": [],
            "졸업시험": credits.졸업시험,
            "졸업인증": credits.졸업인증,
            "graph_requirements": {},
        }

        # 카테고리별 상태 판정
        for cat in credits.categories:
            status = "충족" if cat.부족학점 <= 0 else "부족"
            result["categories"].append({
                "name": cat.name,
                "기준": cat.졸업기준,
                "취득": cat.취득학점,
                "부족": cat.부족학점,
                "상태": status,
            })

        # 그래프에서 졸업요건 가져오기 (있으면)
        if self.graph and self.profile.profile.student_group:
            try:
                grad_req = self.graph.get_graduation_req(
                    self.profile.profile.student_group,
                    self.profile.profile.student_type or "내국인",
                )
                if grad_req:
                    result["graph_requirements"] = grad_req
            except Exception as e:
                logger.debug("그래프 졸업요건 조회 실패: %s", e)

        self._gap_cache = result
        return result

    # ── 현재 학기 과목 ────────────────────────────────

    def current_semester_courses(self) -> list[CourseRecord]:
        """가장 최근 이수학기의 과목 (성적 미확정 포함)."""
        if not self.profile.courses:
            return []

        # 최신 학기 탐색
        semesters = set()
        for c in self.profile.courses:
            if c.이수학기:
                semesters.add(c.이수학기)

        if not semesters:
            return []

        latest = max(semesters)
        return [c for c in self.profile.courses if c.이수학기 == latest]

    # ── 재수강 후보 ───────────────────────────────────

    def retake_candidates(self, threshold: str = "B0") -> list[CourseRecord]:
        """
        재수강 가능 과목 추출.

        기준: 성적이 threshold 이하 & P/NP가 아닌 과목.
        그래프 수강신청규칙에서 재수강기준성적/최고성적도 참조.
        """
        threshold_rank = _GRADE_ORDER.get(threshold, 7)
        candidates = []

        for course in self.profile.courses:
            if not course.성적:
                continue
            rank = _GRADE_ORDER.get(course.성적, -1)
            if rank < 0:  # P, NP 등
                continue
            if rank <= threshold_rank:
                candidates.append(course)

        # 성적 낮은 순 정렬
        candidates.sort(key=lambda c: _GRADE_ORDER.get(c.성적, 0))
        return candidates

    # ── 복수전공 현황 ─────────────────────────────────

    def dual_major_status(self) -> dict:
        """복수전공 이수 현황 상세."""
        p = self.profile.profile
        if not p.복수전공:
            return {"active": False, "message": "복수전공 없음"}

        # 복수전공 과목 필터 (과목수/수강중 계산용)
        dual_courses = [
            c for c in self.profile.courses
            if "복수전공" in c.category or "복전" in c.이수구분
        ]
        in_progress = sum(c.학점 for c in dual_courses if not c.성적)

        # 원칙 1: XLS가 직접 제공한 카테고리 값을 truth source로 사용
        # (과목 필터보다 XLS 학점 요약표가 정확)
        required = 0.0
        earned = 0.0
        shortage = 0.0
        from_category = False

        for cat in self.profile.credits.categories:
            if "복수전공" in cat.name or "다전공" in cat.name:
                required = cat.졸업기준
                earned = cat.취득학점
                shortage = cat.부족학점
                from_category = True
                break

        # 폴백: 카테고리에 없으면 과목 합산
        if not from_category:
            earned = sum(c.학점 for c in dual_courses if c.성적 and c.성적 != "NP")
            shortage = max(0, required - earned)

        return {
            "active": True,
            "전공명": p.복수전공,
            "기준학점": required,
            "취득학점": earned,
            "수강중": in_progress,
            "부족학점": shortage,
            "과목수": len(dual_courses),
        }

    # ── 수강신청 학점 한도 ─────────────────────────────

    def registration_limit(self) -> dict:
        """수강신청 최대학점 (그래프 규칙 + 현재 GPA 기반)."""
        result = {
            "기본_최대학점": None,
            "우수_최대학점": None,
            "현재_평점": self.profile.credits.평점평균,
            "평점_우수_기준": 4.0,
            "적용_최대학점": None,
        }

        if not self.graph:
            return result

        try:
            from app.graphdb.academic_graph import get_reg_group
            reg_group = get_reg_group(self.profile.profile.입학연도)

            # 그래프에서 수강신청규칙 조회
            reg_nodes = [
                nid for nid in self.graph.graph.nodes
                if self.graph.graph.nodes[nid].get("type") == "수강신청규칙"
                and reg_group in str(self.graph.graph.nodes[nid].get("적용학번그룹", ""))
            ]

            for nid in reg_nodes:
                data = self.graph.graph.nodes[nid]
                max_credits = data.get("최대신청학점")
                gpa_max = data.get("평점4이상최대학점")

                if max_credits:
                    result["기본_최대학점"] = max_credits
                if gpa_max:
                    result["우수_최대학점"] = gpa_max

            # 적용 판단
            gpa = self.profile.credits.평점평균
            if result["우수_최대학점"] and gpa >= 4.0:
                result["적용_최대학점"] = result["우수_최대학점"]
            elif result["기본_최대학점"]:
                result["적용_최대학점"] = result["기본_최대학점"]

        except Exception as e:
            logger.debug("수강신청 규칙 조회 실패: %s", e)

        return result

    # ── 보안 포맷터 (PII 제거) ────────────────────────

    def format_gap_context_safe(self) -> str:
        """졸업 갭 분석 → PII 없는 LLM 컨텍스트 텍스트."""
        gap = self.graduation_gap()
        lines = []

        lines.append("[학생 학점 현황 — 졸업 갭 분석]")
        lines.append(f"- 학부/전공: {self.profile.profile.학부과} / {self.profile.profile.전공}")
        lines.append(f"- 입학연도: {self.profile.profile.입학연도}학번 ({self.profile.profile.student_group} 그룹)")
        if self.profile.profile.복수전공:
            lines.append(f"- 복수전공: {self.profile.profile.복수전공}")
        lines.append(f"- 총 졸업기준: {gap['총_졸업기준']}학점")
        lines.append(f"- 총 취득학점: {gap['총_취득학점']}학점")
        lines.append(f"- 총 부족학점: {gap['총_부족학점']}학점")
        lines.append(f"- 평점평균: {gap['평점평균']}")

        # 카테고리별 현황
        deficient = [c for c in gap["categories"] if c["상태"] == "부족"]
        if deficient:
            lines.append("\n[부족 영역]")
            for c in deficient:
                lines.append(f"- {c['name']}: 기준 {c['기준']}학점, 취득 {c['취득']}학점, 부족 {c['부족']}학점")

        # 졸업시험/인증
        if gap["졸업시험"]:
            lines.append(f"\n[졸업시험] {gap['졸업시험']}")
        if gap["졸업인증"]:
            lines.append(f"[졸업인증] {gap['졸업인증']}")

        # 그래프 요건 (있으면)
        if gap["graph_requirements"]:
            lines.append("\n[그래프DB 졸업요건 참조]")
            for k, v in gap["graph_requirements"].items():
                if v and k not in ("type", "id"):
                    lines.append(f"- {k}: {v}")

        # 복수전공 상세
        dual = self.dual_major_status()
        if dual.get("active"):
            lines.append(f"\n[복수전공 현황: {dual['전공명']}]")
            lines.append(f"- 기준: {dual['기준학점']}학점, 취득: {dual['취득학점']}학점, 부족: {dual['부족학점']}학점")
            if dual["수강중"] > 0:
                lines.append(f"- 현재 수강중: {dual['수강중']}학점")

        text = "\n".join(lines)
        return PIIRedactor.redact_for_llm(text, self.profile)

    def format_courses_context_safe(self, courses: list[CourseRecord]) -> str:
        """과목 목록 → PII 없는 LLM 컨텍스트."""
        if not courses:
            return ""

        lines = [f"[이수 과목 목록 ({len(courses)}개)]"]
        for c in courses:
            grade_str = c.성적 if c.성적 else "(수강중)"
            retake_str = " [재수강]" if c.is_retake else ""
            lines.append(
                f"- {c.교과목명} ({c.교과목번호}) | {c.category} | "
                f"{c.이수학기} | {c.학점}학점 | {grade_str}{retake_str}"
            )

        text = "\n".join(lines)
        return PIIRedactor.redact_for_llm(text, self.profile)

    def format_profile_summary_safe(self) -> str:
        """학점 현황 요약 (200토큰 이내) — 이름 제외."""
        p = self.profile.profile
        c = self.profile.credits

        lines = [
            "[학생 학점 현황 요약]",
            f"- 학부/전공: {p.학부과} / {p.전공}",
            f"- 입학연도: {p.입학연도}학번, 학년: {p.학년}, 이수학기: {p.이수학기}",
            f"- 총 취득학점: {c.총_취득학점} / 졸업기준: {c.총_졸업기준} (부족: {c.총_부족학점})",
            f"- 평점평균: {c.평점평균}",
        ]

        if p.복수전공:
            lines.append(f"- 복수전공: {p.복수전공}")
        if p.학적상태:
            lines.append(f"- 학적상태: {p.학적상태}")

        text = "\n".join(lines)
        return PIIRedactor.redact_for_llm(text, self.profile)
