"""
마커 기반 학업성적사정표(.xls) 파서.

⚠️ 보안:
  - 원본 XLS 바이트는 파싱 직후 메모리에서 해제
  - 임시 파일은 즉시 삭제
  - xlrd는 읽기 전용, 매크로 실행 없음

설계 원칙 (유연한 스키마):
  - 고정 행/열 인덱스 사용 금지
  - 한국어 마커 문자열("학번", "졸업(기준)" 등)로 셀 위치 탐색
  - 미지 필드는 extra_fields에 자동 포착
"""

import logging
import os
import re
import tempfile
from datetime import datetime
from typing import Optional

from .models import (
    CourseRecord,
    CreditCategory,
    CreditsSummary,
    StudentAcademicProfile,
    StudentProfile,
)
from .security import UploadValidator

logger = logging.getLogger(__name__)


def _cell_str(value, max_length: int = 500) -> str:
    """셀 값을 정규화된 문자열로 변환. 길이 제한 적용."""
    if value is None or value == "":
        return ""
    s = str(value).strip()
    s = s.replace("\n", " ").replace("\r", "")
    s = re.sub(r"\s{2,}", " ", s)
    if len(s) > max_length:
        s = s[:max_length]
    return s


def _cell_float(value) -> float:
    """셀 값을 float으로 변환. 실패 시 0.0."""
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(str(value).strip())
    except (ValueError, TypeError):
        return 0.0


def _cell_int(value) -> int:
    """셀 값을 int로 변환. 실패 시 0."""
    try:
        return int(float(value))
    except (ValueError, TypeError):
        return 0


class TranscriptParser:
    """
    마커 기반 XLS 성적표 파서.

    사용법:
        parser = TranscriptParser()
        profile = parser.parse(file_bytes, "transcript.xls")
    """

    # ── 프로필 마커 → 필드명 매핑 ──
    # 마커 문자열이 셀에 포함되면 → 인접 셀에서 값 추출
    PROFILE_MARKERS: dict[str, str] = {
        "학부(과)": "학부과",
        "학부과": "학부과",
        "학년": "학년",
        "학번": "학번",
        "성명": "성명",
        "이수학기": "이수학기",
        "이수 학기": "이수학기",
        "복수(융합)전공": "복수전공",
        "복수전공": "복수전공",
        "부전공": "부전공",
        "부전공(융합)": "부전공",
        "학생설계복수전공": "학생설계복수전공",
        "학생설계 복수전공": "학생설계복수전공",
        "융합모듈": "융합모듈",
        "융합모듈(마이크로전공)": "융합모듈",
        "마이크로전공": "융합모듈",
        "내외국인구분": "내외국인",
        "내외국인 구분": "내외국인",
        "학적상태": "학적상태",
        "학적 상태": "학적상태",
        "교직": "교직",
    }

    # 전공 행에서 전공명 추출용 (Row 3)
    MAJOR_MARKER = "전    공"

    # 취업커뮤니티 합격여부 마커
    EMPLOYMENT_MARKERS = ("취업커뮤니티", "취업커뮤니티필수과목합격여부")

    # ── 학점 요약표 마커 ──
    GRAD_REQ_MARKER = "졸업(기준)"
    EARNED_MARKER = "취득학점"
    MISSING_MARKER = "부족학점"

    # ── 이수과목 영역 마커 ──
    COURSE_HEADER_MARKERS = {"이수구분", "교과목번호", "교과목명", "학점", "성적"}

    # 섹션 헤더 패턴: "주전공 (취득 : 48.00)" or "복수전공 (스마트융합보안전공) (취득 : 24.00)"
    SECTION_RE = re.compile(r"(.+?)\s*\(\s*취득\s*:\s*([\d.]+)\s*\)")

    # ── 성적 코드 (유효한 성적값) ──
    VALID_GRADES = {
        "A+", "A", "A0", "B+", "B", "B0", "C+", "C", "C0",
        "D+", "D", "D0", "F", "P", "NP",
    }

    def parse(self, file_bytes: bytes, filename: str = "transcript.xls") -> StudentAcademicProfile:
        """
        XLS 바이트를 파싱하여 구조화된 학생 프로필 반환.

        Args:
            file_bytes: XLS 파일 원본 바이트
            filename: 파일명 (검증용)

        Returns:
            StudentAcademicProfile

        Raises:
            ValueError: 파일 검증 실패 또는 파싱 불가
        """
        # 1) 파일 보안 검증
        ok, err = UploadValidator.validate(file_bytes, filename)
        if not ok:
            raise ValueError(err)

        # 2) XLS → 2D 그리드
        grid = self._read_xls(file_bytes)

        # ⚠️ 원본 바이트 참조 해제 (메모리 잔류 방지)
        del file_bytes

        if not grid or len(grid) < 10:
            raise ValueError("성적표 데이터가 너무 적습니다.")

        # 3) 각 영역 파싱
        profile = self._extract_profile(grid)
        credits = self._extract_credits_summary(grid)
        courses = self._extract_courses(grid)

        # 4) 합계행에서 신청/취득 학점 추출
        self._extract_footer(grid, credits)

        # ⚠️ 그리드 메모리 명시적 해제 (메모리 잔류 방지)
        del grid

        return StudentAcademicProfile(
            profile=profile,
            credits=credits,
            courses=courses,
            source_filename=UploadValidator.sanitize_filename(filename),
            parse_timestamp=datetime.now().isoformat(),
        )

    # ── XLS 읽기 ──────────────────────────────────────

    def _read_xls(self, file_bytes: bytes) -> list[list]:
        """XLS(BIFF) 또는 HTML-in-XLS → 2D 그리드 통합 진입점.

        원칙 1(스키마 진화): 포맷 판단이 확장자·MIME이 아닌 **파일 바이트**에서
        자동 유도된다. 한국 대학 포털이 .xls로 내보내는 HTML 테이블도 같은 경로로 처리.
        """
        head = file_bytes[:2048].lstrip(b"\xef\xbb\xbf \t\r\n").lower()
        is_html = (
            head.startswith(b"<")
            or b"<html" in head
            or b"<table" in head
        )
        if is_html:
            return self._read_html_table(file_bytes)
        return self._read_biff_xls(file_bytes)

    def _read_biff_xls(self, file_bytes: bytes) -> list[list]:
        """레거시 BIFF .xls 경로 (xlrd). 임시 파일 즉시 삭제."""
        import xlrd

        fd, path = tempfile.mkstemp(suffix=".xls")
        try:
            os.write(fd, file_bytes)
            os.close(fd)
            wb = xlrd.open_workbook(path, on_demand=True)
            ws = wb.sheet_by_index(0)
            grid = []
            for r in range(ws.nrows):
                row = []
                for c in range(ws.ncols):
                    row.append(ws.cell_value(r, c))
                grid.append(row)
            wb.release_resources()
            return grid
        finally:
            # ⚠️ 임시 파일 즉시 삭제 + 삭제 확인
            try:
                os.unlink(path)
                if os.path.exists(path):
                    logger.warning("임시 파일 삭제 실패: %s", path)
            except OSError as e:
                logger.warning("임시 파일 삭제 오류: %s (%s)", path, e)

    def _read_html_table(self, file_bytes: bytes) -> list[list]:
        """HTML-in-XLS를 2D 그리드로 변환.

        전략: 가장 큰 <table>을 성적표 본체로 간주하고 <th>/<td> 텍스트를 행×열
        그리드로 평탄화. rowspan/colspan은 단순 복제해 BIFF 셀 좌표와 동일 시맨틱을
        유지(후속 _extract_* 메서드가 동일 그리드 구조만 기대하므로 수정 불필요).
        """
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(file_bytes, "lxml")
        tables = soup.find_all("table")
        if not tables:
            raise ValueError("HTML 성적표에 <table>이 없습니다.")

        main = max(tables, key=lambda t: len(t.find_all(["td", "th"])))
        grid: list[list] = []
        for tr in main.find_all("tr"):
            row: list = []
            for cell in tr.find_all(["td", "th"]):
                text = cell.get_text(" ", strip=True)
                try:
                    colspan = int(cell.get("colspan", 1))
                except (TypeError, ValueError):
                    colspan = 1
                for _ in range(max(1, colspan)):
                    row.append(text)
            if row:
                grid.append(row)
        return grid

    # ── 마커 탐색 유틸 ────────────────────────────────

    def _normalize_marker(self, text: str) -> str:
        """마커 비교용 정규화: 공백·줄바꿈 제거 후 비교."""
        return re.sub(r"\s+", "", text)

    def _scan_marker(
        self,
        grid: list[list],
        marker: str,
        start_row: int = 0,
        end_row: Optional[int] = None,
    ) -> Optional[tuple[int, int]]:
        """마커 문자열이 포함된 첫 번째 셀의 (row, col) 반환. 없으면 None."""
        end = end_row or len(grid)
        norm_marker = self._normalize_marker(marker)
        for r in range(start_row, min(end, len(grid))):
            for c in range(len(grid[r])):
                val = grid[r][c]
                if val == "" or val == 0 or val == 0.0:
                    continue
                norm_val = self._normalize_marker(str(val))
                if norm_marker in norm_val:
                    return (r, c)
        return None

    def _get_adjacent_value(
        self,
        grid: list[list],
        row: int,
        col: int,
        direction: str = "right",
        max_distance: int = 5,
    ):
        """마커 셀의 인접 셀에서 값 추출."""
        if direction == "right":
            for c in range(col + 1, min(col + 1 + max_distance, len(grid[row]))):
                val = grid[row][c]
                if val != "" and val != 0.0 and val != 0:
                    return val
        elif direction == "below":
            if row + 1 < len(grid):
                val = grid[row + 1][col]
                if val != "" and val != 0.0 and val != 0:
                    return val
        return None

    # ── 프로필 추출 ──────────────────────────────────

    def _extract_profile(self, grid: list[list]) -> StudentProfile:
        """
        학생 프로필 추출.

        XLS 상단 2-3행의 레이아웃:
          Row 2: 학부(과)|값|...|학년|값|학번|값|부전공(융합)|...|복수(융합)전공|값|...|내외국인구분|학적상태|값
          Row 3: 전  공  |값|...|이수학기|값|성명|값|...|융합모듈(마이크로전공)|값|...|내국인

        병합 셀이 많아 마커-값 매핑이 복잡하므로,
        각 행의 비어있지 않은 셀을 순회하며 "마커→값" 쌍을 추출합니다.
        """
        profile = StudentProfile()

        # 상단 6행에서 마커-값 쌍 추출
        all_markers = {
            "학부(과)": "학부과", "학부과": "학부과",
            "학년": "학년", "학번": "학번",
            "부전공": "부전공",
            "복수(융합)전공": "복수전공", "복수전공": "복수전공",
            "학생설계복수전공": "학생설계복수전공",
            "내외국인구분": "내외국인",
            "학적상태": "학적상태",
            "전공": "전공",  # "전    공" 포함 (공백 정규화)
            "이수학기": "이수학기",
            "성명": "성명",
            "융합모듈": "융합모듈", "마이크로전공": "융합모듈",
            "교직": "교직",
        }

        search_end = min(10, len(grid))

        for r in range(search_end):
            row = grid[r]
            non_empty = []
            for c in range(len(row)):
                val = row[c]
                if val != "" and val is not None:  # 0, 0.0도 유효한 값으로 취급
                    non_empty.append((c, val))

            # 연속된 (마커, 값) 쌍 추출
            for i, (c, val) in enumerate(non_empty):
                val_str = str(val).strip()
                norm = self._normalize_marker(val_str)

                # 이 셀이 마커인지 확인
                matched_field = None
                for marker, field in all_markers.items():
                    norm_marker = self._normalize_marker(marker)
                    if norm == norm_marker or (len(norm_marker) >= 2 and norm_marker in norm and len(norm) < len(norm_marker) + 10):
                        matched_field = field
                        break

                if matched_field is None:
                    continue

                # 값: 같은 행의 다음 비어있지 않은 셀
                next_val = None
                next_val_str = ""

                if i + 1 < len(non_empty):
                    next_c, next_v = non_empty[i + 1]
                    if next_c - c <= 5:  # 5칸 이내
                        next_val = next_v
                        next_val_str = _cell_str(next_v)
                        # 다음 값이 EXACT 마커인지 확인 (substring이 아님)
                        next_norm = self._normalize_marker(next_val_str)
                        is_exact_marker = any(
                            self._normalize_marker(m) == next_norm
                            for m in all_markers
                        )
                        if is_exact_marker:
                            next_val = None
                            next_val_str = ""

                # 오른쪽에 값이 없으면 아래쪽 시도
                if not next_val_str and r + 1 < search_end:
                    below_val = grid[r + 1][c] if c < len(grid[r + 1]) else ""
                    if below_val != "" and below_val != 0 and below_val != 0.0:
                        next_val = below_val
                        next_val_str = _cell_str(below_val)

                if not next_val_str:
                    continue

                # 필드에 값 설정
                self._set_profile_field(profile, matched_field, next_val, next_val_str)

        # 취업커뮤니티 합격여부 (별도 처리: 마커가 길고 복잡)
        for marker in self.EMPLOYMENT_MARKERS:
            pos = self._scan_marker(grid, marker, end_row=search_end)
            if pos:
                val = self._get_adjacent_value(grid, pos[0], pos[1], "right")
                if val:
                    v = _cell_str(val)
                    if v in ("Y", "N", "y", "n"):
                        profile.취커합격 = v.upper()
                break

        # student_group 자동 계산
        if profile.입학연도:
            try:
                from app.graphdb.academic_graph import get_student_group
                profile.student_group = get_student_group(profile.입학연도)
            except (ImportError, Exception):
                year = int(profile.입학연도)
                if year >= 2024:
                    profile.student_group = "2024_2025"
                elif year == 2023:
                    profile.student_group = "2023"
                elif year == 2022:
                    profile.student_group = "2022"
                elif year == 2021:
                    profile.student_group = "2021"
                elif year >= 2017:
                    profile.student_group = "2017_2020"
                else:
                    profile.student_group = "2016_before"

        return profile

    def _set_profile_field(
        self, profile: StudentProfile, field: str, raw_val, val_str: str,
    ) -> None:
        """프로필 필드에 값 설정. 타입 변환 포함."""
        if field == "학번":
            profile.학번 = str(_cell_int(raw_val)) if isinstance(raw_val, float) else val_str
            if len(profile.학번) >= 4:
                profile.입학연도 = profile.학번[:4]
        elif field == "학년":
            profile.학년 = _cell_int(raw_val)
        elif field == "이수학기":
            profile.이수학기 = _cell_int(raw_val)
        elif field == "내외국인":
            profile.내외국인 = val_str
            if "외국인" in val_str or "유학생" in val_str:
                profile.student_type = "외국인"
            elif "편입" in val_str:
                profile.student_type = "편입생"
            else:
                profile.student_type = "내국인"
        elif hasattr(profile, field):
            # 이미 값이 있으면 덮어쓰지 않음 (첫 매칭 우선)
            current = getattr(profile, field)
            if not current:
                setattr(profile, field, val_str)

    # ── 학점 요약표 추출 ─────────────────────────────

    def _extract_credits_summary(self, grid: list[list]) -> CreditsSummary:
        """졸업기준/취득학점/부족학점 행을 마커로 찾아 학점 요약 추출."""
        summary = CreditsSummary()

        # 졸업(기준), 취득학점, 부족학점 행 찾기 (첫 번째 출현만 사용 — 2페이지 중복 방지)
        grad_pos = self._scan_marker(grid, self.GRAD_REQ_MARKER, end_row=20)
        earned_pos = self._scan_marker(grid, self.EARNED_MARKER, end_row=20)
        missing_pos = self._scan_marker(grid, self.MISSING_MARKER, end_row=20)

        if not grad_pos or not earned_pos:
            logger.warning("학점 요약표를 찾을 수 없습니다.")
            return summary

        grad_row = grad_pos[0]
        earned_row = earned_pos[0]
        missing_row = missing_pos[0] if missing_pos else None

        # 헤더 구조 역추적: 졸업(기준) 행 위 2-3행이 다단 컬럼 헤더
        # 단순화: 졸업(기준) 행의 값을 카테고리별로 추출
        # 알려진 컬럼 위치는 마커로 탐색

        # 총계 컬럼 찾기
        total_col = None
        for c in range(len(grid[grad_row])):
            val = _cell_str(grid[grad_row - 3][c]) if grad_row >= 3 else ""
            if "총계" in val:
                total_col = c
                break
        # 대안: "총계"가 여러 행에 걸쳐 있을 수 있으므로 위 4행까지 탐색
        if total_col is None:
            for offset in range(1, 5):
                if grad_row >= offset:
                    for c in range(len(grid[grad_row - offset])):
                        if "총계" in _cell_str(grid[grad_row - offset][c]):
                            total_col = c
                            break
                if total_col is not None:
                    break

        # 총계 값 추출
        if total_col is not None:
            summary.총_졸업기준 = _cell_float(grid[grad_row][total_col])
            summary.총_취득학점 = _cell_float(grid[earned_row][total_col])
            if missing_row is not None:
                summary.총_부족학점 = _cell_float(grid[missing_row][total_col])

        # 평점평균 컬럼 찾기
        gpa_col = None
        for offset in range(1, 5):
            if grad_row >= offset:
                for c in range(len(grid[grad_row - offset])):
                    cell_val = _cell_str(grid[grad_row - offset][c])
                    if "평점" in cell_val and "평균" in cell_val:
                        gpa_col = c
                        break
            if gpa_col is not None:
                break

        if gpa_col is not None:
            summary.평점평균 = _cell_float(grid[grad_row][gpa_col])

        # 졸업시험 (주전공/복수전공)
        exam_col = None
        for offset in range(1, 5):
            if grad_row >= offset:
                for c in range(len(grid[grad_row - offset])):
                    if "졸업시험" in _cell_str(grid[grad_row - offset][c]):
                        exam_col = c
                        break
            if exam_col is not None:
                break

        if exam_col is not None:
            # 주전공, 복수전공 열이 연속으로 있음
            sub_headers = {}
            for offset in range(1, 4):
                if grad_row >= offset:
                    for c in range(exam_col, min(exam_col + 6, len(grid[grad_row - offset]))):
                        val = _cell_str(grid[grad_row - offset][c])
                        if "주" in val and "전공" in val:
                            sub_headers["주전공"] = c
                        elif "복수" in val and "전공" in val:
                            sub_headers["복수전공"] = c

            for label, c in sub_headers.items():
                val = _cell_str(grid[grad_row][c])
                if val:
                    summary.졸업시험[label] = val

        # 졸업인증 (기업정신/사회봉사)
        cert_col = None
        for offset in range(1, 5):
            if grad_row >= offset:
                for c in range(len(grid[grad_row - offset])):
                    if "졸업인증" in _cell_str(grid[grad_row - offset][c]):
                        cert_col = c
                        break
            if cert_col is not None:
                break

        if cert_col is not None:
            sub_headers = {}
            for offset in range(1, 4):
                if grad_row >= offset:
                    for c in range(cert_col, min(cert_col + 6, len(grid[grad_row - offset]))):
                        val = _cell_str(grid[grad_row - offset][c])
                        if "기업" in val:
                            sub_headers["기업정신"] = c
                        elif "사회" in val and "봉사" in val:
                            sub_headers["사회봉사"] = c

            for label, c in sub_headers.items():
                val = _cell_str(grid[grad_row][c])
                if val:
                    summary.졸업인증[label] = val

        # ── 카테고리별 상세 학점 추출 ──
        categories = self._extract_credit_categories(grid, grad_row, earned_row, missing_row)
        # 중복 제거 (같은 이름 + 같은 값이면 제거)
        seen = set()
        deduped = []
        for cat in categories:
            key = (cat.name, cat.졸업기준, cat.취득학점)
            if key not in seen:
                seen.add(key)
                deduped.append(cat)
        summary.categories = deduped

        return summary

    def _extract_credit_categories(
        self,
        grid: list[list],
        grad_row: int,
        earned_row: int,
        missing_row: Optional[int],
    ) -> list[CreditCategory]:
        """다단 헤더에서 카테고리별 학점 추출."""
        categories = []

        # 헤더 영역에서 카테고리 마커 탐색 (졸업기준 행 위 1~4행)
        category_markers = {
            "교양계": "교양_계",
            "교양 계": "교양_계",
            "전공기본": "전공_기본",
            "전공심화": "전공_심화",
            "전공선택": "전공_선택",
            "취업커뮤니티": "취업커뮤니티",
            "취업 커뮤 니티": "취업커뮤니티",
            "자유선택": "일반_자유선택",
        }

        for marker, cat_name in category_markers.items():
            col = None
            for offset in range(1, 5):
                if grad_row >= offset:
                    for c in range(len(grid[grad_row - offset])):
                        val = _cell_str(grid[grad_row - offset][c])
                        if marker.replace(" ", "") in val.replace(" ", ""):
                            col = c
                            break
                if col is not None:
                    break

            if col is not None:
                req = _cell_float(grid[grad_row][col])
                earned = _cell_float(grid[earned_row][col])
                missing = _cell_float(grid[missing_row][col]) if missing_row else max(0, req - earned)
                if req > 0 or earned > 0:
                    categories.append(CreditCategory(
                        name=cat_name,
                        졸업기준=req,
                        취득학점=earned,
                        부족학점=missing,
                    ))

        # "계" (전공 소계) 컬럼: 전공기본 이후 "계" 마커
        for offset in range(1, 5):
            if grad_row >= offset:
                for c in range(len(grid[grad_row - offset])):
                    val = _cell_str(grid[grad_row - offset][c])
                    if val == "계":
                        # 앞쪽에 "전공" 관련 마커가 있으면 전공_계
                        context_val = ""
                        for prev_offset in range(1, 5):
                            if grad_row >= prev_offset:
                                pv = _cell_str(grid[grad_row - prev_offset][c])
                                if pv:
                                    context_val = pv
                                    break
                        # 값 추출 시도
                        req = _cell_float(grid[grad_row][c])
                        earned = _cell_float(grid[earned_row][c])
                        if req > 0 and context_val:
                            categories.append(CreditCategory(
                                name=f"{context_val}_계" if "전공" in context_val else context_val,
                                졸업기준=req,
                                취득학점=earned,
                                부족학점=_cell_float(grid[missing_row][c]) if missing_row else max(0, req - earned),
                            ))

        # 다전공 계
        for offset in range(1, 5):
            if grad_row >= offset:
                for c in range(len(grid[grad_row - offset])):
                    val = _cell_str(grid[grad_row - offset][c])
                    if "다전공" in val or ("복전" in val and "융합" in val):
                        req = _cell_float(grid[grad_row][c])
                        earned = _cell_float(grid[earned_row][c])
                        if req > 0:
                            name = "다전공_복수전공" if "복전" in val else "다전공_계"
                            categories.append(CreditCategory(
                                name=name,
                                졸업기준=req,
                                취득학점=earned,
                                부족학점=_cell_float(grid[missing_row][c]) if missing_row else max(0, req - earned),
                            ))

        return categories

    # ── 이수 과목 추출 ────────────────────────────────

    def _extract_courses(self, grid: list[list]) -> list[CourseRecord]:
        """2열 레이아웃의 이수 과목 목록 추출."""
        courses = []

        # "이수구분" 헤더 행 찾기
        header_row = None
        for r in range(len(grid)):
            for c in range(len(grid[r])):
                if _cell_str(grid[r][c]) == "이수구분":
                    header_row = r
                    break
            if header_row is not None:
                break

        if header_row is None:
            logger.warning("이수과목 헤더를 찾을 수 없습니다.")
            return courses

        # 2열 레이아웃의 왼쪽/오른쪽 컬럼 오프셋 감지
        left_cols = self._detect_course_columns(grid, header_row, start_col=0, end_col=27)
        right_cols = self._detect_course_columns(grid, header_row, start_col=27, end_col=58)

        # 과목 행 파싱 (좌/우 섹션 독립 추적)
        left_section = ""
        right_section = ""

        # XLS가 여러 "페이지"로 구성될 수 있음 — 각 페이지 시작을 찾아 모두 파싱
        # 이수구분 헤더가 나타나는 모든 행을 찾기
        course_header_rows = []
        for r in range(len(grid)):
            for c in range(len(grid[r])):
                if _cell_str(grid[r][c]) == "이수구분":
                    course_header_rows.append(r)
                    break

        # 각 페이지의 과목 범위 계산
        page_ranges = []
        for idx, hr in enumerate(course_header_rows):
            start = hr + 1
            # 끝: 다음 페이지의 헤더 영역 시작 또는 그리드 끝
            if idx + 1 < len(course_header_rows):
                # 다음 이수구분 행 이전의 빈행/제목행까지
                end = course_header_rows[idx + 1]
                # 제목행(학업성적...)이 있으면 그 전까지
                for scan_r in range(start, end):
                    first_val = _cell_str(grid[scan_r][0]) if grid[scan_r] else ""
                    if "학업성적" in first_val or "사정표" in first_val:
                        end = scan_r
                        break
            else:
                end = len(grid)
            page_ranges.append((start, end))

        for start, end in page_ranges:
            # 이 페이지의 컬럼 감지 (같은 이수구분 행 기준)
            hr = start - 1
            page_left = self._detect_course_columns(grid, hr, 0, 27)
            page_right = self._detect_course_columns(grid, hr, 27, 58)

            for r in range(start, end):
                row = grid[r]

                # 왼쪽 열 파싱
                if page_left:
                    section, course = self._parse_course_row(row, page_left, left_section)
                    if section:
                        left_section = section
                    if course:
                        courses.append(course)

                # 오른쪽 열 파싱 (독립 섹션 추적)
                if page_right:
                    section, course = self._parse_course_row(row, page_right, right_section)
                    if section:
                        right_section = section
                    if course:
                        courses.append(course)

        return courses

    def _detect_course_columns(
        self,
        grid: list[list],
        header_row: int,
        start_col: int,
        end_col: int,
    ) -> Optional[dict]:
        """과목 헤더 행에서 컬럼 매핑 감지."""
        cols = {}
        row = grid[header_row]

        for c in range(start_col, min(end_col, len(row))):
            val = _cell_str(row[c])
            if val == "이수구분":
                cols["이수구분"] = c
            elif val == "교과목번호":
                cols["교과목번호"] = c
            elif val == "교과목명":
                cols["교과목명"] = c
            elif val == "이수학기":
                cols["이수학기"] = c
            elif val == "학점":
                cols["학점"] = c
            elif val == "성적":
                cols["성적"] = c

        # 최소 교과목명 + 학점은 있어야 유효한 열
        if "교과목명" in cols and "학점" in cols:
            return cols
        return None

    def _parse_course_row(
        self,
        row: list,
        cols: dict,
        current_section: str,
    ) -> tuple[str, Optional[CourseRecord]]:
        """
        한 행에서 과목 정보 추출.

        Returns:
            (새 섹션 헤더 or "", CourseRecord or None)
        """
        new_section = ""

        # 이수구분 또는 첫 번째 셀에서 섹션 헤더 확인
        first_col = cols.get("이수구분", cols.get("교과목명", 0))
        first_val = _cell_str(row[first_col]) if first_col < len(row) else ""

        # 섹션 헤더 패턴: "주전공 (취득 : 48.00)"
        section_match = self.SECTION_RE.search(first_val)
        if section_match:
            new_section = section_match.group(1).strip()
            return new_section, None

        # 과목 데이터 추출
        이수구분 = _cell_str(row[cols["이수구분"]]) if "이수구분" in cols and cols["이수구분"] < len(row) else ""
        교과목번호 = _cell_str(row[cols["교과목번호"]]) if "교과목번호" in cols and cols["교과목번호"] < len(row) else ""
        교과목명 = _cell_str(row[cols["교과목명"]]) if "교과목명" in cols and cols["교과목명"] < len(row) else ""
        이수학기 = _cell_str(row[cols["이수학기"]]) if "이수학기" in cols and cols["이수학기"] < len(row) else ""
        학점_raw = row[cols["학점"]] if "학점" in cols and cols["학점"] < len(row) else 0
        성적 = _cell_str(row[cols["성적"]]) if "성적" in cols and cols["성적"] < len(row) else ""

        학점 = _cell_float(학점_raw)

        # 유효한 과목인지 판정: 교과목명이 있고 학점이 0보다 크면
        if not 교과목명 or 학점 <= 0:
            # 섹션 헤더일 수 있는 경우 체크
            combined = first_val or 이수구분
            m = self.SECTION_RE.search(combined)
            if m:
                return m.group(1).strip(), None
            return new_section, None

        # [재] 마커 감지
        is_retake = False
        # 교과목명이나 별도 컬럼에 [재] 표시가 있으면
        if "[재]" in 교과목명 or "[재]" in 이수구분:
            is_retake = True
            교과목명 = 교과목명.replace("[재]", "").strip()
        # 이수구분과 교과목번호 사이에 [재] 마커가 있을 수 있음
        for c in range(cols.get("이수구분", 0), cols.get("이수학기", len(row))):
            if c < len(row) and "[재]" in _cell_str(row[c]):
                is_retake = True
                break

        # [인문] 등 마커도 별도 처리 (무시)

        course = CourseRecord(
            category=current_section or 이수구분,
            이수구분=이수구분,
            교과목번호=교과목번호,
            교과목명=교과목명,
            이수학기=이수학기,
            학점=학점,
            성적=성적,
            is_retake=is_retake,
        )

        return new_section, course

    # ── 합계행 추출 ───────────────────────────────────

    def _extract_footer(self, grid: list[list], credits: CreditsSummary) -> None:
        """마지막 행들에서 신청학점/취득학점 총계 추출."""
        for r in range(len(grid) - 1, max(0, len(grid) - 5), -1):
            for c in range(len(grid[r])):
                val = _cell_str(grid[r][c])
                if "신청학점" in val:
                    # "신청학점 : 128.50" 형식
                    m = re.search(r"([\d.]+)", val)
                    if m:
                        credits.신청학점 = float(m.group(1))
                if "취득학점" in val and "총" not in val:
                    m = re.search(r"([\d.]+)", val)
                    if m:
                        # 이미 summary에서 추출했으면 덮어쓰지 않음
                        if credits.총_취득학점 == 0:
                            credits.총_취득학점 = float(m.group(1))
