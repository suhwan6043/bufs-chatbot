"""
수업시간표 PDF 전용 파서

수업시간표 표를 감지하고 컬럼 매핑 + 병합 셀 복원 + 메타데이터 추출을 수행합니다.

실제 BUFS 수업시간표 컬럼 (스크린샷 기준):
    이수구분 | 학년 | 과목번호 | 분반 | 과목명 | 학점 | 시수 | 시간 | 강의실 | 교수명 | 수업정보 | 교수법

병합 셀:
    이수구분, 학년, 과목번호, 과목명은 동일 그룹 첫 행에만 값이 있고
    나머지 행은 None → carry-forward(앞 행 값 이어받기)로 복원
"""

import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# ── 교시 → 시간 변환 ────────────────────────────────────────────
# 1교시 = 09:00~10:00, 2교시 = 10:00~11:00, ...
# 즉 N교시 시작 시각 = (8 + N)시 정각
_PERIOD_BASE_HOUR = 8

_DAY_MAP = {
    "월": "월요일",
    "화": "화요일",
    "수": "수요일",
    "목": "목요일",
    "금": "금요일",
    "토": "토요일",
    "일": "일요일",
}

# "수1,2,목3" 같은 토큰 파싱용
# 요일 문자(선택) + 교시 숫자 1~2자리
_PERIOD_TOKEN_RE = re.compile(r"([월화수목금토일]?)(\d{1,2})")

# ── 수업시간표 표 감지 ──────────────────────────────────────────
# 헤더 행에 이 키워드 중 2개 이상이면 수업시간표 표로 판별
_TIMETABLE_KEYWORDS = {
    "교과목", "과목", "강좌", "교수", "담당", "강사",
    "학점", "시간", "요일", "강의실", "이수", "학년", "분반",
}
_MIN_KEYWORD_MATCH = 2

# ── 컬럼별 감지 패턴 ───────────────────────────────────────────
# 주의: 순서대로 매핑되므로 더 구체적인 패턴을 앞에 배치
_COL_PATTERNS: dict[str, re.Pattern] = {
    # 이수구분
    "course_type":      re.compile(r"이수구분|이수유형"),
    # 학년
    "year":             re.compile(r"^학년$"),
    # 과목번호 (course_code)
    "course_code":      re.compile(r"과목번호|교과목코드|학수번호|학수"),
    # 분반 — "분반" 셀 전용
    "section":          re.compile(r"^분반$"),
    # 과목명 — "과목번호"에 매핑되지 않도록 `과목번호` 제외
    "course_name":      re.compile(r"교과목명|과목명|강좌명"),
    # 학점
    "credits":          re.compile(r"^학점$"),
    # 시수 — 시간(요일·교시)과 분리
    "credit_hours":     re.compile(r"^시수$|시간수"),
    # 강의 요일·교시 시간표 — "시수"를 잡지 않도록 독립 패턴
    "day_time":         re.compile(r"^시간$|강의시간|요일"),
    # 강의실
    "classroom":        re.compile(r"강의실|호실|장소"),
    # 교수명
    "professor":        re.compile(r"교수명?|담당교수|담당|강사"),
    # 수업정보 (온라인여부, 분반정보 등 비고)
    "class_info":       re.compile(r"수업정보|비고"),
    # 교수법
    "teaching_method":  re.compile(r"교수법|교수방법"),
    # 학과/전공 컬럼이 표 안에 있는 경우
    "department":       re.compile(r"학과명?|전공명?|학부명?"),
    # 수강정원
    "capacity":         re.compile(r"수강인원|수강정원|정원|인원"),
}

# 병합 셀 carry-forward 대상 필드
# (표에서 동일 그룹 첫 행에만 값이 있고 나머지는 None인 필드)
_CARRY_FORWARD_FIELDS = {"course_type", "year", "course_code", "course_name"}

# 학과명 추출 패턴 (페이지 헤더/텍스트)
_DEPT_PATTERN = re.compile(r"([\w가-힣]+(?:학과|학부|전공|대학원|학원))")

# 정규화 키워드 목록 (QueryAnalyzer.DEPARTMENT_KEYWORDS와 동기화)
# "소프트웨어학부(소프트웨어전공)" → "소프트웨어" 로 압축
_DEPT_KEYWORDS: list[str] = [
    # IT·공학
    "컴퓨터공학", "소프트웨어", "빅데이터", "인공지능",
    "스마트융합보안", "스마트에너지", "전자",
    # 어문
    "영어", "일본어", "중국어", "한국어",
    "독일어", "프랑스어", "스페인어", "러시아어",
    "베트남어", "태국어", "미얀마어", "아랍",
    "인도네시아", "인도어", "터키어", "이탈리아어",
    # 사회·경상
    "경영", "경제", "금융", "회계", "무역", "마케팅",
    "관광", "호텔", "항공", "외교", "행정",
    "사회복지", "상담심리", "사이버경찰",
    # 문화·체육
    "영상콘텐츠", "체육", "스포츠", "운동건강",
    # 기타
    "국제개발", "글로벌창업", "비서",
]

# 텍스트 출력 시 표시 순서
_DISPLAY_ORDER = [
    "course_type", "year", "course_code", "section",
    "course_name", "credits", "credit_hours",
    "day_time", "classroom", "professor", "class_info",
]

_LABELS = {
    "course_type":     "이수구분",
    "year":            "학년",
    "course_code":     "과목번호",
    "section":         "분반",
    "course_name":     "교과목",
    "credits":         "학점",
    "credit_hours":    "시수",
    "day_time":        "시간",
    "classroom":       "강의실",
    "professor":       "교수",
    "class_info":      "수업정보",
    "teaching_method": "교수법",
    "department":      "학과",
    "capacity":        "정원",
}


# ── 공개 API ──────────────────────────────────────────────────

def decode_day_time(day_time_str: str) -> str:
    """
    수업시간표 시간 문자열을 사람이 읽기 쉬운 형태로 변환합니다.

    규칙:
        N교시 시작 = (8 + N)시 정각, 종료 = (8 + N + 1)시 정각
        1교시 = 09:00~10:00, 6교시 = 14:00~15:00, ...

    연속된 교시는 하나의 구간으로 합칩니다.

    Examples:
        "수1"       → "수요일 09:00~10:00"
        "화6,7,8"   → "화요일 14:00~17:00"
        "수1,2,목3" → "수요일 09:00~11:00, 목요일 11:00~12:00"
        ""          → ""  (원본 반환)
    """
    if not day_time_str or not day_time_str.strip():
        return day_time_str

    # 토큰 분리: 콤마·공백·슬래시 등
    tokens = re.split(r"[,\s/]+", day_time_str.strip())

    # (요일 char, 교시 int) 리스트 구성
    day_periods: list[tuple[str, int]] = []
    current_day: Optional[str] = None

    for token in tokens:
        m = _PERIOD_TOKEN_RE.match(token.strip())
        if not m:
            continue
        day_char, period_str = m.group(1), m.group(2)
        if day_char:
            current_day = day_char
        if current_day is None:
            continue
        day_periods.append((current_day, int(period_str)))

    if not day_periods:
        return day_time_str  # 파싱 실패 → 원본 반환

    # 연속 교시를 [요일, 시작교시, 끝교시] 구간으로 합치기
    groups: list[list] = []
    for day, period in day_periods:
        if groups and groups[-1][0] == day and groups[-1][2] == period - 1:
            groups[-1][2] = period  # 구간 연장
        else:
            groups.append([day, period, period])  # 새 구간

    # 구간 → "요일 HH:MM~HH:MM" 문자열
    parts: list[str] = []
    for day, start_p, end_p in groups:
        day_name = _DAY_MAP.get(day, day)
        start_h = _PERIOD_BASE_HOUR + start_p
        end_h   = _PERIOD_BASE_HOUR + end_p + 1
        parts.append(f"{day_name} {start_h:02d}:00~{end_h:02d}:00")

    return ", ".join(parts)


def normalize_dept_keyword(dept_str: str) -> str:
    """
    전체 학과명 문자열에서 검색 키워드를 추출합니다.

    Examples:
        "소프트웨어학부(소프트웨어전공)" → "소프트웨어"
        "컴퓨터공학부(컴퓨터공학전공)"  → "컴퓨터공학"
        "알수없는학과"                   → "알수없는학과"  (원본 반환)
    """
    for kw in _DEPT_KEYWORDS:
        if kw in dept_str:
            return kw
    return dept_str


def is_timetable_table(table: list) -> bool:
    """표의 첫 행(헤더)을 보고 수업시간표 표인지 판별합니다."""
    if not table or not table[0]:
        return False
    header_text = " ".join(str(c or "").strip() for c in table[0])
    matched = sum(1 for kw in _TIMETABLE_KEYWORDS if kw in header_text)
    return matched >= _MIN_KEYWORD_MATCH


def map_columns(header_row: list) -> dict[str, int]:
    """
    헤더 행에서 필드명 → 컬럼 인덱스 매핑을 반환합니다.

    중복 매핑 방지: 먼저 매핑된 필드는 재등록하지 않습니다.

    Returns:
        {"course_type": 0, "year": 1, "course_code": 2, ...}
    """
    mapping: dict[str, int] = {}
    for i, cell in enumerate(header_row):
        cell_str = str(cell or "").replace("\n", "").strip()
        for field_name, pattern in _COL_PATTERNS.items():
            if field_name not in mapping and pattern.search(cell_str):
                mapping[field_name] = i
    return mapping


def _get_cell(row: list, col_map: dict, field: str) -> str:
    """row에서 field에 해당하는 셀 값을 반환합니다 (None → 빈 문자열)."""
    idx = col_map.get(field)
    if idx is None or idx >= len(row):
        return ""
    return str(row[idx] or "").replace("\n", " ").strip()


def _iter_rows_with_carry(table: list, col_map: dict):
    """
    테이블 데이터 행을 순회하며 병합 셀(None) carry-forward를 적용합니다.

    Yields:
        dict — 필드명 → 셀 값 (carry-forward 포함)
    """
    prev: dict[str, str] = {}

    for row in table[1:]:
        # 빈 행 skip (모든 셀이 None 또는 공백)
        if not any(str(c or "").strip() for c in row):
            continue

        row_data: dict[str, str] = {}
        for field in col_map:
            val = _get_cell(row, col_map, field)
            if val:
                row_data[field] = val
                if field in _CARRY_FORWARD_FIELDS:
                    prev[field] = val          # carry-forward 업데이트
            elif field in _CARRY_FORWARD_FIELDS and field in prev:
                row_data[field] = prev[field]  # 병합 셀 복원

        yield row_data


def extract_timetable_meta(table: list, department_hint: str = "") -> dict:
    """
    수업시간표 표 전체에서 집계 메타데이터를 추출합니다.
    ChromaDB metadata에 저장되므로 string/int/float만 사용합니다.

    Returns:
        {
          "content_type": "timetable",
          "department":   "컴퓨터공학부",
          "course_names": "시스템분석및설계,AI프로그래밍,...",  # 최대 30개
          "professors":   "이성진,유영중,...",                  # 중복 제거, 최대 15개
          "course_types": "전공심화실무,취업커뮤니티",           # 이수구분 목록
          "row_count":    5,
        }
    """
    if not table or len(table) < 2:
        return {"content_type": "timetable"}

    col_map = map_columns(table[0])

    course_names: list[str] = []
    professors: list[str] = []
    seen_profs: set[str] = set()
    course_types: list[str] = []
    seen_types: set[str] = set()
    dept = department_hint

    for row_data in _iter_rows_with_carry(table, col_map):
        name = row_data.get("course_name", "")
        if name:
            course_names.append(name)

        prof = row_data.get("professor", "")
        if prof and prof not in seen_profs:
            professors.append(prof)
            seen_profs.add(prof)

        ctype = row_data.get("course_type", "")
        if ctype and ctype not in seen_types:
            course_types.append(ctype)
            seen_types.add(ctype)

        if not dept:
            dept = row_data.get("department", "")

    return {
        "content_type":     "timetable",
        "department":       normalize_dept_keyword(dept),   # 필터용 정규화 키워드
        "department_raw":   dept,                           # 원본 전체 학과명
        "course_names":     ",".join(course_names[:30]),
        "professors":       ",".join(professors[:15]),
        "course_types":     ",".join(course_types),
        "row_count":        len(course_names),
    }


def timetable_table_to_text(table: list, department: str = "") -> str:
    """
    수업시간표 표를 임베딩에 유리한 구조적 텍스트로 변환합니다.
    병합 셀(None)은 carry-forward로 복원합니다.

    예시 출력:
        [수업시간표] 컴퓨터공학부(컴퓨터공학전공)
        이수구분:전공심화실무 | 학년:4 | 과목번호:COM241 | 분반:01 | 교과목:시스템분석및설계 | 학점:3.0 | 시수:3.0 | 시간:화6,7,8 | 강의실:I312 | 교수:이성진
        이수구분:전공심화실무 | 학년:4 | 과목번호:COM462 | 분반:01 | 교과목:AI프로그래밍 | 학점:3.0 | 시수:3.0 | 시간:수1,2,목3 | 강의실:I312 | 교수:유영중
        ...
    """
    if not table or len(table) < 2:
        return ""

    col_map = map_columns(table[0])
    if not col_map:
        return ""

    header = f"[수업시간표] {department}" if department else "[수업시간표]"
    lines = [header]

    for row_data in _iter_rows_with_carry(table, col_map):
        parts = []
        for field_name in _DISPLAY_ORDER:
            val = row_data.get(field_name, "")
            if not val:
                continue
            # 시간 필드는 교시 → 실제 시각으로 변환 (원본도 병기)
            if field_name == "day_time":
                decoded = decode_day_time(val)
                display = f"{decoded} ({val})" if decoded != val else val
            else:
                display = val
            parts.append(f"{_LABELS[field_name]}:{display}")
        if parts:
            lines.append(" | ".join(parts))

    return "\n".join(lines)


def extract_department_from_context(headers: list, text: str) -> str:
    """
    페이지 헤더 텍스트 또는 페이지 본문 앞부분에서 학과명을 추출합니다.

    우선순위:
      1. 큰 폰트 헤더 (page.headers)
      2. 페이지 텍스트 앞 200자
    """
    for h in headers:
        m = _DEPT_PATTERN.search(str(h))
        if m:
            return m.group(1)

    if text:
        m = _DEPT_PATTERN.search(text[:200])
        if m:
            return m.group(1)

    return ""
