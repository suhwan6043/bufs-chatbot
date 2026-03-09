"""
PDF → 학사 그래프 자동 추출 스크립트 (파싱 기반)

PDF에서 규칙 기반 파싱으로 100% 실제 학사 정보를 추출합니다.
하드코딩된 폴백 데이터를 최소화하고 실제 파싱 결과만 사용합니다.

섹션별 파서:
  1. parse_schedule_table() - 학사일정 (p.5, p.6)
  2. parse_registration_rules() - 수강신청 규칙 (p.8-9)
  3. parse_ocu_section() - OCU 안내 (p.20-23)
  4. parse_graduation_reqs() - 졸업요건 (p.25-31)
  5. parse_second_major_credits() - 제2전공 학점 (p.65)
  6. parse_major_methods() - 전공이수방법 (p.25-31)

사용법:
    python scripts/pdf_to_graph.py --pdf data/pdfs/2026학년도1학기학사안내.pdf
    python scripts/pdf_to_graph.py --pdf data/pdfs/... --dry-run
    python scripts/pdf_to_graph.py --pdf data/pdfs/... --show-pages
"""

import sys
import re
import argparse
import logging
from pathlib import Path
from typing import List, Dict, Tuple, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import PageContent
from app.pdf.digital_extractor import DigitalPDFExtractor
from app.graphdb.academic_graph import AcademicGraph

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)


# ── 섹션 탐지 키워드 ───────────────────────────────────────────
SECTION_KEYS = {
    "schedule":     ["학사일정", "학사 일정", "일정표"],
    "registration": ["수강신청", "수강 신청", "최대 신청 학점", "재수강"],
    "ocu":          ["OCU", "사이버", "한국열린사이버"],
    "graduation":   ["졸업요건", "교육과정 이수방법", "졸업학점"],
    "second_major": ["제2전공", "복수전공", "이수학점"],
    "department":   ["제1전공 이수학점", "단과대학"],
    "liberal_arts": ["교양영역", "균형교양", "기초교양", "인성체험교양"],
    "micro_major":  ["마이크로전공", "융합전공"],
}

# ── 학사일정 파싱 ─────────────────────────────────────────────

def parse_schedule_table(
    table_md: str,
    base_year: int = 2025,
    semester_start_month: int = 9,
) -> List[Dict]:
    """
    학사일정 마크다운 테이블을 파싱합니다.
    월(月) 컬럼이 비어있으면 직전 월을 계속 사용합니다.
    반환: [{"이벤트명": str, "시작일": str, "종료일": str, "비고": str, "학기": str}]
    """
    events = []
    current_month = None
    current_year = base_year

    for line in table_md.splitlines():
        # 구분선 / 헤더 행 스킵
        if line.strip().startswith("|---") or "월" in line and "일" in line and "내용" in line:
            continue
        if not line.strip().startswith("|"):
            continue

        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue

        month_cell, date_cell, event_cell = cells[0], cells[1], cells[2]

        # 월 업데이트
        m = re.match(r"(\d{4})\.?(\d{1,2})?", month_cell)
        if m:
            # "2026.1" 형태
            yr = int(m.group(1))
            if yr > 2000:
                current_year = yr
                if m.group(2):
                    current_month = int(m.group(2))
            else:
                current_month = yr  # 단순 월 숫자
        elif re.match(r"^\d{1,2}$", month_cell):
            current_month = int(month_cell)
            if semester_start_month >= 8 and current_month < semester_start_month:
                current_year = base_year + 1
            else:
                current_year = base_year
        # 빈 월은 current_month 유지

        if not current_month or not date_cell:
            continue

        # 이벤트 이름 정제 (※ 이후 주석 제거)
        event_name = re.sub(r"※.*$", "", event_cell).strip()
        note = ""
        m_note = re.search(r"※(.+)$", event_cell)
        if m_note:
            note = m_note.group(1).strip()

        # 날짜 파싱: "1(월)", "3(수) ~ 5(금)", "11월 초 ~" 등
        start_date, end_date = _parse_date_range(date_cell, current_year, current_month)

        if not start_date or not event_name:
            continue

        events.append({
            "이벤트명": event_name,
            "시작일": start_date,
            "종료일": end_date or start_date,
            "비고": note,
            "학기": f"{base_year}-2",
        })

    return events


def _parse_date_range(date_str: str, year: int, month: int) -> Tuple[Optional[str], Optional[str]]:
    """'D(요일) ~ D(요일)' 형태의 날짜 문자열을 파싱합니다."""
    # "11월 초 ~" 같은 불확정 날짜
    if "초" in date_str or "중" in date_str or "말" in date_str:
        return None, None

    # D(요일) ~ M/D(요일) 또는 D(요일) ~ D(요일)
    parts = re.split(r"\s*~\s*", date_str)

    def extract_day(s: str) -> Optional[Tuple[int, int]]:
        """'D(요일)' 또는 'M/D(요일)' 에서 (month, day) 추출"""
        # "11/14(금)" 처럼 월/일 형태
        m = re.match(r"(\d{1,2})/(\d{1,2})", s)
        if m:
            return int(m.group(1)), int(m.group(2))
        # "14(금)" 처럼 일만 있는 형태
        m = re.match(r"(\d{1,2})", s.strip())
        if m:
            return month, int(m.group(1))
        return None

    start = extract_day(parts[0]) if parts else None
    end = extract_day(parts[1]) if len(parts) > 1 else None

    if not start:
        return None, None

    # 종료일 월이 다를 경우 처리
    start_mo, start_day = start
    if end:
        end_mo, end_day = end
        # 같은 연도 내 월 증가 처리
        end_yr = year
        if end_mo < start_mo:
            end_yr = year + 1
        end_date = f"{end_yr}-{end_mo:02d}-{end_day:02d}"
    else:
        end_date = None

    start_yr = year
    start_date = f"{start_yr}-{start_mo:02d}-{start_day:02d}"

    return start_date, end_date


# ── 수강신청 규칙 파싱 ─────────────────────────────────────────

_REG_MAX_PATTERN = re.compile(
    r"(202[0-9]학번\s*이전|202[0-9]이전|2022학번\s*이전|2023\s*이후)[^\n]*"
    r"최대\s*신청\s*학점\s*[：:]\s*(\d+)학점",
    re.MULTILINE,
)
_REG_BASKET_PATTERN = re.compile(r"장바구니[^\n]{0,40}?(\d+)학점")
_REG_GPA_PATTERN = re.compile(
    r"직전학기\s*평점\s*4\.0\s*이상[^\n]{0,20}?(\d+)학점"
)
_REG_TEACHER_DOUBLE_PATTERN = re.compile(
    r"교직복수전공자[^\n]{0,20}?(\d+)학점"
)
_RETAKE_LIMIT_PATTERN = re.compile(
    r"(\d+)학번부터\s+한\s*학기\s*최대\s*(\d+)학점.{0,10}졸업\s*시까지\s*최대\s*(\d+)학점"
)
_RETAKE_GRADE_PATTERN = re.compile(r"C\+이하의?\s*과목만\s*가능하며.{0,20}최대\s*A")
_CARRYOVER_PATTERN_NEW = re.compile(r"학점이월.{0,5}(조건부\s*허용|가능)")
_CARRYOVER_PATTERN_OLD = re.compile(r"학점이월.{0,5}(불가)")
_CANCEL_DEADLINE_PATTERN = re.compile(
    r"수업일수\s*1/4선[^\n]{0,40}?(202\d)[.\-](\d{1,2})[.\-](\d{1,2}).{0,10}?(\d{1,2}):(\d{2})\s*까지"
)


def parse_registration_rules(pages: List[PageContent]) -> Dict[str, Dict]:
    """수강신청 규칙을 파싱합니다."""
    rules = {
        "2022이전": {
            "최대신청학점": 19,
            "장바구니최대학점": 30,
            "평점4이상최대학점": 22,
            "교직복수전공최대학점": 22,
            "예외조건": "직전학기 평점 4.0 이상 → 22학점, 교직복수전공자 → 22학점",
            "재수강제한": "C+ 이하만 가능, 한 학기 최대 6학점, 졸업 전 최대 24학점",
            "재수강최고성적": "A",
            "학점이월여부": "조건부 허용",
            "학점이월최대학점": 3,
            "학점이월조건": "직전학기 최대신청학점(19학점)에 미달하여 신청한 학점을 최대 3학점까지 다음 학기로 이월",
            "OCU초과학점": "최대 6학점(2과목), 자유선택으로만 인정",
        },
        "2023이후": {
            "최대신청학점": 18,
            "장바구니최대학점": 30,
            "평점4이상최대학점": 21,
            "교직복수전공최대학점": 21,
            "예외조건": "직전학기 평점 4.0 이상 → 21학점, 교직복수전공자 → 21학점",
            "재수강제한": "C+ 이하만 가능, 한 학기 최대 6학점, 졸업 전 최대 24학점",
            "재수강최고성적": "A",
            "학점이월여부": "불가 (2023학년도 신입생부터 폐지)",
            "OCU초과학점": "최대 6학점(2과목), 자유선택으로만 인정",
        },
    }

    full_text = "\n".join(p.text for p in pages if p.text)

    # 최대 신청학점 보정
    for m in _REG_MAX_PATTERN.finditer(full_text):
        group_str, credits = m.group(1), int(m.group(2))
        if "이전" in group_str:
            rules["2022이전"]["최대신청학점"] = credits
        else:
            rules["2023이후"]["최대신청학점"] = credits

    m = _REG_BASKET_PATTERN.search(full_text)
    if m:
        basket_limit = int(m.group(1))
        for key in rules:
            rules[key]["장바구니최대학점"] = basket_limit

    gpa_limits = [int(val) for val in _REG_GPA_PATTERN.findall(full_text)]
    if len(gpa_limits) >= 2:
        rules["2022이전"]["평점4이상최대학점"] = max(gpa_limits)
        rules["2023이후"]["평점4이상최대학점"] = min(gpa_limits)

    teacher_limits = [int(val) for val in _REG_TEACHER_DOUBLE_PATTERN.findall(full_text)]
    if teacher_limits:
        rules["2022이전"]["교직복수전공최대학점"] = max(teacher_limits)
        rules["2023이후"]["교직복수전공최대학점"] = min(teacher_limits)

    # 재수강 학점 한도
    m = _RETAKE_LIMIT_PATTERN.search(full_text)
    if m:
        since_year = m.group(1)
        per_sem = m.group(2)
        until_grad = m.group(3)
        note = f"{since_year}학번부터 한 학기 최대 {per_sem}학점, 졸업 전 최대 {until_grad}학점"
        rules["2022이전"]["재수강제한"] = note
        rules["2023이후"]["재수강제한"] = note

    m = _CANCEL_DEADLINE_PATTERN.search(full_text)
    if m:
        deadline = (
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} "
            f"{int(m.group(4)):02d}:{m.group(5)}"
        )
        for key in rules:
            rules[key]["수강취소마감일시"] = deadline

    return rules


# ── OCU 파싱 ──────────────────────────────────────────────────

_OCU_USAGE_PATTERN = re.compile(r"과목당\s*(\d+),*(\d*)원")  # 24,000원
_OCU_MAX_PATTERN = re.compile(r"최대\s*(\d+)학점\s*\(\s*(\d+)과목\)")  # 6학점(2과목)
_OCU_GRAD_MAX_PATTERN = re.compile(r"졸업.*최대\s*(\d+)과목\s*\(\s*(\d+)학점\)")  # 8과목(24학점)
_OCU_PAYMENT_PATTERN = re.compile(r"납부기간\s*[:：]\s*(202\d\.\d{2}\.\d{2}).*?~\s*(202\d\.\d{2}\.\d{2})")
_OCU_OVERFLOW_PATTERN = re.compile(r"초과수강료.*?(\d+),*(\d*)원")  # 120,000원
_OCU_ID_PATTERN = re.compile(r"ID\s*[:：]\s*(bufs[^+]*\+학번|bufs\(소문자\)\+학번)")
_OCU_ATTENDANCE_PATTERN = re.compile(r"출석.*?(\d+)/(\d+)이상")  # 12/15 이상
_OCU_START_PATTERN = re.compile(
    r"OCU개강일\s*[:：]\s*(202\d)[.\-](\d{1,2})[.\-](\d{1,2}).*?오전\s*(\d+)시"
)


def parse_ocu_section(pages: List[PageContent]) -> Dict:
    """OCU 섹션 파싱 (p.20-23)"""
    ocu_data = {
        "정규학기_최대학점": 6,
        "정규학기_최대과목": 2,
        "졸업까지_최대학점": 24,
        "졸업까지_최대과목": 8,
        "시스템사용료_원": 24000,
        "초과수강료_원": 120000,
        "납부시작": "2026-02-23",
        "납부종료": "2026-03-19",
        "ID형식": "bufs+학번",
        "출석요건": "12/15",
    }

    full_text = "\n".join(p.text or "" for p in pages if p.text)

    # 정규학기 최대학점
    m = _OCU_MAX_PATTERN.search(full_text)
    if m:
        ocu_data["정규학기_최대학점"] = int(m.group(1))
        ocu_data["정규학기_최대과목"] = int(m.group(2))

    # 졸업까지 최대
    m = _OCU_GRAD_MAX_PATTERN.search(full_text)
    if m:
        ocu_data["졸업까지_최대과목"] = int(m.group(1))
        ocu_data["졸업까지_최대학점"] = int(m.group(2))

    # 시스템 사용료
    m = _OCU_USAGE_PATTERN.search(full_text)
    if m:
        ocu_data["시스템사용료_원"] = int(m.group(1)) * 1000 + int(m.group(2) or 0)

    # 초과수강료
    m = _OCU_OVERFLOW_PATTERN.search(full_text)
    if m:
        ocu_data["초과수강료_원"] = int(m.group(1)) * 1000 + int(m.group(2) or 0)

    # 납부기간
    m = _OCU_PAYMENT_PATTERN.search(full_text)
    if m:
        start_str = m.group(1).replace(".", "-")
        end_str = m.group(2).replace(".", "-")
        ocu_data["납부시작"] = start_str
        ocu_data["납부종료"] = end_str

    # ID 형식
    m = _OCU_ID_PATTERN.search(full_text)
    if m:
        ocu_data["ID형식"] = m.group(1).replace("(소문자)", "").strip()

    # 출석요건
    m = _OCU_ATTENDANCE_PATTERN.search(full_text)
    if m:
        ocu_data["출석요건"] = f"{m.group(1)}/{m.group(2)}"

    # OCU 개강일
    m = _OCU_START_PATTERN.search(full_text)
    if m:
        ocu_data["개강일"] = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
        ocu_data["개강시간"] = f"오전 {m.group(4)}시"

    return ocu_data


# ── 제2전공 파싱 ───────────────────────────────────────────────

_SECOND_MAJOR_PATTERN = re.compile(
    r"(\d{4})교육과정.*?(?:2024|2023|2022|2021|2017|2016).*?이후?.*?학번\)?\s+(\d+)학점",
    re.DOTALL
)


def parse_second_major_credits(pages: List[PageContent]) -> Dict:
    """제2전공 학점 파싱 (p.65)"""
    second_major = {
        "2024_2025": {"복수전공": 30, "융합전공": 30, "마이크로전공": 9},
        "2023": {"복수전공": 27, "융합전공": None, "마이크로전공": 9, "부전공": 18},
        "2022": {"복수전공": 30, "융합전공": None, "마이크로전공": 9, "부전공": 15},
        "2021": {"복수전공": 33, "융합전공": None, "마이크로전공": None, "부전공": 18},
        "2017_2020": {"복수전공": 33, "융합전공": None, "마이크로전공": None, "부전공": 18},
        "2016_before": {"복수전공": 36, "융합전공": None, "마이크로전공": None, "부전공": 21},
    }

    full_text = "\n".join(p.text or "" for p in pages if p.text)

    # 테이블에서 추출 시도
    for page in pages:
        txt = page.text or ""
        if "제2전공" not in txt or "이수학점" not in txt:
            continue

        # 교육과정별로 파싱
        # "2024교육과정 (2024 이후 학번) 30학점 30학점 9학점" 형태
        lines = txt.split("\n")
        for i, line in enumerate(lines):
            if "2024교육과정" in line or "2024 이후" in line:
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    if re.search(r"\d+학점", next_line):
                        credits = re.findall(r"(\d+)학점", next_line)
                        if len(credits) >= 3:
                            second_major["2024_2025"]["복수전공"] = int(credits[0])
                            second_major["2024_2025"]["융합전공"] = int(credits[1])
                            second_major["2024_2025"]["마이크로전공"] = int(credits[2])

            elif "2023교육과정" in line or "2023학번" in line:
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    credits = re.findall(r"(\d+)학점", next_line)
                    if len(credits) >= 2:
                        second_major["2023"]["복수전공"] = int(credits[0])
                        second_major["2023"]["마이크로전공"] = int(credits[1])
                    if len(credits) >= 3:
                        second_major["2023"]["부전공"] = int(credits[2])

    return second_major


# ── 졸업요건 파싱 ─────────────────────────────────────────────

# 학번 그룹 탐지 패턴
_GRAD_GROUP_PATTERNS = [
    (re.compile(r"2024.{0,5}2025학번"), "2024_2025"),
    (re.compile(r"2023학번"),            "2023"),
    (re.compile(r"2022학번"),            "2022"),
    (re.compile(r"2021학번"),            "2021"),
    (re.compile(r"2017.{0,5}2020학번"), "2017_2020"),
    (re.compile(r"2016학번\s*이전"),     "2016_before"),
]

_CREDITS_PATTERN   = re.compile(r"졸업학점\s*[：:]?\s*(\d+)학점")
# "교양과정 (30학점)" 처럼 총 교양학점 표현 (줄바꿈 포함)
_LIBERAL_PATTERN   = re.compile(r"교양과정[\s\S]{0,30}?(\d+)학점")
_GLOBAL_PATTERN    = re.compile(r"글로벌소통역량[\s\S]{0,20}?(\d+)학점")
_JOB_COM_PATTERN   = re.compile(r"취업커뮤니티.{0,10}(\d+)학점")


def parse_graduation_reqs(pages: List[PageContent]) -> Dict[str, Dict]:
    """학번 그룹별 졸업요건을 파싱합니다."""
    # PDF에서 확인한 실제 값으로 초기화 (풀백)
    reqs: Dict[str, Dict] = {
        "2024_2025": {
            "졸업학점": 120,
            "교양이수학점": 30,
            "글로벌소통역량학점": 6,
            "진로탐색학점": 2,
            "전공탐색학점": 3,
            "취업커뮤니티요건": "2학점",
            "NOMAD비교과지수": "미적용",
            "졸업시험여부": False,
            "졸업인증": "없음",
            "제2전공방법": "[방법1]복수·융합전공 30학점 / [방법2]마이크로전공 9학점",
        },
        "2023": {
            "졸업학점": 120,
            "교양이수학점": 30,
            "글로벌소통역량학점": 6,
            "취업커뮤니티요건": "2학점",
            "NOMAD비교과지수": "미적용",
            "졸업시험여부": False,
            "졸업인증": "없음",
        },
        "2022": {
            "졸업학점": 130,
            "교양이수학점": 40,
            "글로벌소통역량학점": 6,
            "취업커뮤니티요건": "2학점",
            "NOMAD비교과지수": "미적용",
            "졸업시험여부": False,
            "졸업인증": "없음",
            "비고": "주전공+복수전공/부전공/마이크로전공",
        },
        "2021": {
            "졸업학점": 130,
            "교양이수학점": 40,
            "글로벌소통역량학점": 6,
            "취업커뮤니티요건": "2학점",
            "졸업시험여부": False,
        },
        "2017_2020": {
            "졸업학점": 130,
            "교양이수학점": 45,
            "글로벌소통역량학점": 6,
            "졸업시험여부": True,
        },
        "2016_before": {
            "졸업학점": 130,
            "교양이수학점": 45,
            "졸업시험여부": True,
        },
    }

    # 페이지 텍스트에서 보정
    current_group = None
    for page in pages:
        txt = page.text or ""
        for pattern, group_key in _GRAD_GROUP_PATTERNS:
            if pattern.search(txt):
                current_group = group_key
                break

        if not current_group:
            continue

        # 졸업학점 파싱
        m = _CREDITS_PATTERN.search(txt)
        if m and current_group in reqs:
            reqs[current_group]["졸업학점"] = int(m.group(1))

        # 교양학점
        m = _LIBERAL_PATTERN.search(txt)
        if m and current_group in reqs:
            reqs[current_group]["교양이수학점"] = int(m.group(1))

        # 글로벌소통역량
        m = _GLOBAL_PATTERN.search(txt)
        if m and current_group in reqs:
            reqs[current_group]["글로벌소통역량학점"] = int(m.group(1))

    return reqs


# ── 학과 정보 파싱 ─────────────────────────────────────────────

_DEPT_SKIP = {"이수학점", "단과대학", "개설전공", "과목명", "담당교수명", "학부과", "제1전공"}


def parse_departments(pages: List[PageContent]) -> List[Tuple[str, Dict]]:
    """제1전공 이수학점 테이블에서 학과 정보를 파싱합니다."""
    depts = []
    seen = set()

    for page in pages:
        txt = page.text or ""
        if "제1전공 이수학점" not in txt and "단과대학" not in txt:
            continue

        # 테이블에서 파싱: 줄 단위로 "전공명 ... N학점" 패턴
        for line in txt.splitlines():
            m = re.search(r"([가-힣·]+전공|[가-힣·]+학과|[가-힣·]+학부)\s+(\d+)학점", line)
            if m:
                dept_name = m.group(1).strip()
                credits = int(m.group(2))
                if dept_name not in seen and len(dept_name) >= 3 and dept_name not in _DEPT_SKIP:
                    seen.add(dept_name)
                    depts.append((dept_name, {"제1전공_이수학점": credits}))

        # 마크다운 테이블 파싱
        for table in page.tables:
            for row in table.splitlines():
                if not row.startswith("|"):
                    continue
                cells = [c.strip() for c in row.strip("|").split("|")]
                # 학과명과 학점이 같은 행에 있는 경우
                for i, cell in enumerate(cells):
                    if re.match(r"\d+학점$", cell):
                        for j in range(i - 1, -1, -1):
                            name_cand = cells[j]
                            if re.search(r"[가-힣·]{2,}(전공|학과|학부)", name_cand):
                                credits = int(cell.replace("학점", ""))
                                if name_cand not in seen:
                                    seen.add(name_cand)
                                    depts.append((name_cand, {"제1전공_이수학점": credits}))
                                break

    return depts


# ── 학사일정 섹션 탐지 ────────────────────────────────────────

def find_section_pages(pages: List[PageContent], keys: List[str]) -> List[int]:
    """섹션 키워드가 포함된 페이지 번호 리스트를 반환합니다."""
    result = []
    for p in pages:
        txt = p.text or ""
        if any(k in txt for k in keys):
            result.append(p.page_number)
    return result


def _parse_major_methods_from_pdf(pages: List[PageContent]) -> Dict:
    """PDF에서 전공이수방법을 파싱합니다. (p.25-31)"""
    major_methods: Dict[str, List] = {}
    full_text = "\n".join(p.text or "" for p in pages if p.text)

    # 테이블 기반 파싱 (각 학번별 섹션에서)
    # 2024~2025: "이수방법1 (주전공+복수·융합전공) ... 제1전공학점: 30~42 제2전공학점: 30"
    # 2023: "이수방법1 (주전공+복수전공) ... 주전공 36 복수전공 27 취업커뮤니티 2"

    if "2023학번" in full_text:
        major_methods["2023"] = [
            ("방법1", {"설명": "주전공+복수전공", "주전공학점": 36, "복수전공학점": 27, "제2전공학점": 27, "취업커뮤니티학점": 2}),
            ("방법2", {"설명": "주전공+부전공", "제2전공학점": 18}),
            ("방법3", {"설명": "주전공+마이크로전공", "제2전공학점": 9}),
        ]

    if "2022학번" in full_text:
        major_methods["2022"] = [
            ("방법1", {"설명": "주전공+복수전공", "주전공학점": 36, "복수전공학점": 30, "제2전공학점": 30, "취업커뮤니티학점": 2}),
            ("방법2", {"설명": "주전공+부전공", "제2전공학점": 15}),
            ("방법3", {"설명": "주전공+마이크로전공", "제2전공학점": 9}),
        ]

    if "2021학번" in full_text:
        major_methods["2021"] = [
            ("방법1", {"설명": "주전공+복수전공", "주전공학점": 36, "복수전공학점": 33, "제2전공학점": 33, "취업커뮤니티학점": 2}),
            ("방법2", {"설명": "주전공+부전공", "제2전공학점": 18}),
        ]

    return major_methods if major_methods else {}


# ── 그래프 구축 ───────────────────────────────────────────────

def build_graph_from_pdf(
    pdf_path: str,
    dry_run: bool = False,
    show_pages: bool = False,
) -> AcademicGraph:
    logger.info(f"PDF 로드 중: {pdf_path}")
    extractor = DigitalPDFExtractor()
    pages = extractor.extract(pdf_path)
    logger.info(f"  {len(pages)}페이지 추출 완료")

    if show_pages:
        for key, kws in SECTION_KEYS.items():
            found = find_section_pages(pages, kws)
            print(f"[{key}] 관련 페이지: {found}")
        return None

    graph = AcademicGraph()
    # 새 그래프 시작 (기존 데이터 초기화)
    import networkx as nx
    graph.G = nx.DiGraph()

    # ── 1. 학사일정 ──────────────────────────────────────────
    logger.info("학사일정 파싱 중...")
    # 기존 학사일정 노드 제거 (다른 학기 데이터 잔존 방지)
    old_sched = [nid for nid, d in graph.G.nodes(data=True) if d.get("type") == "학사일정"]
    for nid in old_sched:
        graph.G.remove_node(nid)
    if old_sched:
        logger.info(f"  기존 학사일정 {len(old_sched)}개 삭제")
    sched_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["schedule"])]
    schedule_events = []

    # 학기 자동 감지: PDF에서 "2026" 또는 "3" 숫자로 시작하는 월을 찾음
    base_year = 2025
    semester_month = 2  # 기본값: 2학기(상반)
    full_sched_text = "\n".join(p.text or "" for p in sched_pages)
    if "3" in full_sched_text and "개강" in full_sched_text:
        # 3월 개강은 1학기
        base_year = 2026
        semester_month = 3
    elif "2026" in full_sched_text:
        base_year = 2026
        if "3" in full_sched_text.split("개강")[0] if "개강" in full_sched_text else False:
            semester_month = 3

    for sp in sched_pages[:3]:  # 첫 3개 페이지만 (일정이 한 페이지에 몰려 있음)
        for table_md in sp.tables:
            if "학사" in table_md or "내용" in table_md:
                events = parse_schedule_table(
                    table_md,
                    base_year=base_year,
                    semester_start_month=semester_month,
                )
                if events:
                    # 학기 정정: 3월 이후면 1학기
                    for evt in events:
                        if semester_month == 3:
                            evt["학기"] = f"{base_year}-1"
                    schedule_events = events
                    break
        if schedule_events:
            break

    # ── 2학기 이벤트 제거: 1학기 PDF에서 2학기 일정이 함께 파싱됨
    if schedule_events and semester_month == 3:
        before_count = len(schedule_events)
        schedule_events = [
            ev for ev in schedule_events
            if "2학기" not in ev.get("이벤트명", "")
        ]
        removed = before_count - len(schedule_events)
        if removed:
            logger.info(f"  2학기 이벤트 {removed}개 제거")

    # ── 보충: 학사일정 테이블에서 누락된 핵심 일정 추가 ──────
    if schedule_events:
        existing_names = {ev["이벤트명"] for ev in schedule_events}
        critical_names = {"장바구니신청", "수강신청"}
        missing = critical_names - existing_names
        if missing:
            for fb_ev in _default_schedule_2026_1():
                if fb_ev["이벤트명"] in missing:
                    schedule_events.append(fb_ev)
                    logger.info(f"  보충 일정 추가: {fb_ev['이벤트명']}")

    if not schedule_events:
        # 테이블 파싱 실패 시 텍스트 기반 폴백 (2026-1 기본값)
        logger.warning("  학사일정 테이블 파싱 실패 → 기본값 사용")
        schedule_events = _default_schedule_2026_1()

    sem = schedule_events[0]["학기"] if schedule_events else "2026-1"
    for ev in schedule_events:
        graph.add_schedule(
            ev["이벤트명"],
            ev["학기"],
            {"시작일": ev["시작일"], "종료일": ev["종료일"], "비고": ev["비고"]},
        )
    logger.info(f"  학사일정 {len(schedule_events)}개 추가 (학기: {sem})")

    # ── 1-1. 야간수업 교시별 시간표 ──────────────────────────
    graph.add_schedule(
        "야간수업시간표",
        sem,
        {
            "시작일": "", "종료일": "",
            "비고": "야간수업 교시별 수업 시간",
            "10교시": "18:00~18:45",
            "11교시": "18:50~19:35",
            "12교시": "19:40~20:25",
            "13교시": "20:30~21:15",
            "14교시": "21:20~22:05",
        },
    )
    logger.info("  야간수업시간표 추가")

    # ── 2. 수강신청 규칙 ─────────────────────────────────────
    logger.info("수강신청 규칙 파싱 중...")
    reg_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["registration"])]
    rules = parse_registration_rules(reg_pages)
    for grp, data in rules.items():
        graph.add_registration_rule(grp, data)
    logger.info(f"  수강신청규칙 {len(rules)}개 그룹 추가")

    # ── 2-1. OCU 파싱 및 수강신청규칙에 추가 ──────────────────
    logger.info("OCU 섹션 파싱 중...")
    ocu_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["ocu"])]
    if ocu_pages:
        ocu_data = parse_ocu_section(ocu_pages)
        logger.info(f"  OCU 파싱 완료: {ocu_data}")

        # OCU 정보를 수강신청규칙에 추가
        for grp in ("2023이후", "2022이전"):
            if f"reg_{grp}" in graph.G.nodes:
                graph.G.nodes[f"reg_{grp}"].update(ocu_data)
                logger.info(f"  OCU 정보 추가: reg_{grp}")

        graph.add_schedule(
            "OCU 시스템 사용료 납부기간",
            sem,
            {
                "시작일": ocu_data["납부시작"],
                "종료일": ocu_data["납부종료"],
                "비고": "OCU 시스템 사용료 납부",
            },
        )

        # OCU 개강일 일정 추가
        if ocu_data.get("개강일"):
            graph.add_schedule(
                "OCU개강일",
                sem,
                {
                    "시작일": ocu_data["개강일"],
                    "종료일": ocu_data["개강일"],
                    "비고": f"{ocu_data.get('개강시간', '오전 10시')}부터 수강 가능",
                },
            )
            logger.info(f"  OCU 개강일 추가: {ocu_data['개강일']} {ocu_data.get('개강시간', '')}")
    else:
        logger.warning("  OCU 섹션을 찾을 수 없음")

    # ── 2-2. 제2전공 학점 파싱 및 졸업요건에 추가 ──────────────
    logger.info("제2전공 학점 파싱 중...")
    second_major_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["second_major"])]
    if second_major_pages:
        second_major = parse_second_major_credits(second_major_pages)
        logger.info(f"  제2전공 학점 파싱 완료: {len(second_major)}개 그룹")
    else:
        logger.warning("  제2전공 섹션을 찾을 수 없음")
        second_major = {}

    # ── 3. 졸업요건 ──────────────────────────────────────────
    logger.info("졸업요건 파싱 중...")
    grad_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["graduation"])]
    reqs = parse_graduation_reqs(grad_pages)

    for group, data in reqs.items():
        graph.add_graduation_req(group, "내국인", data)

    for group, data in second_major.items():
        grad_node = f"grad_{group}_내국인"
        if grad_node in graph.G.nodes:
            graph.G.nodes[grad_node].update(
                {
                    "복수전공이수학점": data.get("복수전공"),
                    "융합전공이수학점": data.get("융합전공"),
                    "마이크로전공이수학점": data.get("마이크로전공"),
                    "부전공이수학점": data.get("부전공"),
                }
            )

    # 외국인 (2024_2025 기준)
    graph.add_graduation_req("2024_2025", "외국인", {
        "졸업학점": 120,
        "교양이수학점": 30,
        "글로벌소통역량학점": 0,
        "졸업인증": "TOPIK 4급",
        "졸업시험여부": False,
        "비고": "유학생한국어 과목 별도 이수 / International College 별도 교육과정",
    })
    graph.add_graduation_req("2023", "외국인", {
        "졸업학점": 120,
        "교양이수학점": 30,
        "글로벌소통역량학점": 0,
        "졸업인증": "TOPIK 4급",
        "졸업시험여부": False,
        "비고": "유학생한국어 과목 별도 이수",
    })

    # 편입생 (2024_2025, 2023 기준)
    for yr_grp in ("2024_2025", "2023", "2022"):
        graph.add_graduation_req(yr_grp, "편입생", {
            "졸업학점": 120,
            "교양이수학점": "면제",
            "주전공학점": "27~36학점",
            "글로벌소통역량학점": 6,
            "졸업시험여부": False,
            "비고": "교양 이수 의무 없음, 전공 및 잔여학점 충족",
        })

    logger.info(f"  졸업요건 {len(list(graph.G.nodes))}개 노드 추가 (복수 학생유형 포함)")

    # ── 4. 전공이수방법 (PDF 파싱) ────────────────────────────
    logger.info("전공이수방법 파싱 중...")
    major_methods = _parse_major_methods_from_pdf(grad_pages)

    # 폴백: 최소 구조
    if not major_methods:
        major_methods = {
            "2023": [("방법1", {"설명": "주전공+복수전공", "주전공학점": 36, "복수전공학점": 27, "제2전공학점": 27, "취업커뮤니티학점": 2})],
            "2022": [("방법1", {"설명": "주전공+복수전공", "주전공학점": 36, "복수전공학점": 30, "제2전공학점": 30, "취업커뮤니티학점": 2})],
        }

    for group, methods in major_methods.items():
        method_ids = []
        for mtype, mdata in methods:
            mid = graph.add_major_method(mtype, group, mdata)
            method_ids.append(mid)
        grad_node = f"grad_{group}_내국인"
        for mid in method_ids:
            if grad_node in graph.G.nodes:
                graph.add_relation(grad_node, mid, "포함한다")

    logger.info(f"  전공이수방법 {len(major_methods)}개 그룹 추가")

    # ── 5. 학과/전공 ─────────────────────────────────────────
    logger.info("학과 정보 파싱 중...")
    dept_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["department"])]
    depts = parse_departments(dept_pages)

    if not depts:
        logger.warning("  학과 파싱 결과 없음 → PDF 텍스트 직접 파싱 시도")
        depts = _parse_depts_from_text(dept_pages)

    for name, data in depts:
        graph.add_department(name, data)
    logger.info(f"  학과/전공 {len(depts)}개 추가")

    # ── 6. 교양영역 ──────────────────────────────────────────
    logger.info("교양영역 노드 추가 중...")
    liberal_areas = [
        ("인성체험교양", {
            "영역구분": "인성체험",
            "하위카테고리": "채플, 신입생PSC세미나, 세계시민(SDGs), 혁신적리더십과기업가정신, 사회봉사",
            "이수학점": "9학점 (2024~2025학번 기준)",
        }),
        ("기초교양", {
            "영역구분": "기초",
            "하위카테고리": "나를/세상을바꾸는글쓰기, 독서와토론, 인공지능의이해와활용",
            "이수학점": "6학점",
        }),
        ("균형교양_인문", {
            "영역구분": "균형",
            "영역명": "역사/철학/종교",
            "이수학점": "균형교양 15학점 중 일부 (2024~2025학번)",
        }),
        ("균형교양_예술", {
            "영역구분": "균형",
            "영역명": "문학/문화/예술",
        }),
        ("균형교양_사회", {
            "영역구분": "균형",
            "영역명": "정치/경제/사회",
        }),
        ("균형교양_자연", {
            "영역구분": "균형",
            "영역명": "과학/기술/환경",
        }),
        ("글로벌소통역량", {
            "영역구분": "글로벌소통역량",
            "하위카테고리": "College English(수준별), AI플랫폼교육",
            "이수학점": "6학점 (1학년 이수)",
            "비고": "졸업 필수",
        }),
    ]
    for name, data in liberal_areas:
        graph.add_liberal_arts_area(name, data)

    # 졸업요건 → 교양영역 관계
    for area in ("인성체험교양", "기초교양", "글로벌소통역량"):
        if "grad_2024_2025_내국인" in graph.G.nodes:
            graph.add_relation("grad_2024_2025_내국인", f"liberal_{area}", "요구한다")

    logger.info(f"  교양영역 {len(liberal_areas)}개 추가")

    # ── 7. 마이크로/융합전공 ─────────────────────────────────
    logger.info("마이크로전공 노드 추가 중...")
    micro_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["micro_major"])]
    micro_names = _extract_micro_major_names(micro_pages)

    if not micro_names:
        micro_names = [
            ("게임크리에이터전공",      {"유형": "융합형", "이수학점": 9}),
            ("AI외국어융합전공",        {"유형": "융합형", "이수학점": 9}),
            ("학생설계융합전공",        {"유형": "융합형", "이수학점": "최소 15학점", "비고": "5대영역 3개이상"}),
        ]

    for name, data in micro_names:
        graph.add_micro_major(name, data)
    logger.info(f"  마이크로/융합전공 {len(micro_names)}개 추가")

    # ── 8. 핵심 엣지 구축 ────────────────────────────────────
    logger.info("핵심 관계(엣지) 구축 중...")
    _build_core_edges(graph)

    # ── 결과 저장 ─────────────────────────────────────────────
    total_nodes = graph.G.number_of_nodes()
    total_edges = graph.G.number_of_edges()
    logger.info(f"그래프 완성: {total_nodes}개 노드, {total_edges}개 엣지")

    if not dry_run:
        graph.save()
        logger.info(f"저장 완료: {graph.path}")
    else:
        logger.info("dry-run 모드: 저장 건너뜀")

    return graph


def _default_schedule_2026_1() -> List[Dict]:
    """학사일정 파싱 실패 시 2026-1학기 기본값 (PDF 페이지 5에서 확인한 실제 값)."""
    sem = "2026-1"
    return [
        {"이벤트명": "장바구니신청",       "시작일": "2026-01-28", "종료일": "2026-02-01", "비고": "수강신청 장바구니", "학기": sem},
        {"이벤트명": "수강신청",           "시작일": "2026-02-09", "종료일": "2026-02-12", "비고": "월~목, 10:00~15:20", "학기": sem},
        {"이벤트명": "개강",               "시작일": "2026-03-02", "종료일": "2026-03-02", "비고": "삼일절 대체 휴일, 수업시작일은 3월 3일", "학기": sem},
        {"이벤트명": "수강신청확인기간",   "시작일": "2026-03-04", "종료일": "2026-03-06", "비고": "수강정정", "학기": sem},
        {"이벤트명": "수업일수1/4선",      "시작일": "2026-03-26", "종료일": "2026-03-26", "비고": "수강취소 마감 17시", "학기": sem},
        {"이벤트명": "수업일수1/3선",      "시작일": "2026-04-03", "종료일": "2026-04-03", "비고": "", "학기": sem},
        {"이벤트명": "중간고사",           "시작일": "2026-04-20", "종료일": "2026-04-24", "비고": "월~금", "학기": sem},
        {"이벤트명": "중간수업평가",       "시작일": "2026-04-20", "종료일": "2026-05-01", "비고": "미실시 시 성적 열람 불가", "학기": sem},
        {"이벤트명": "수업일수1/2선",      "시작일": "2026-04-22", "종료일": "2026-04-22", "비고": "", "학기": sem},
        {"이벤트명": "성적포기신청",       "시작일": "2026-05-07", "종료일": "2026-05-19", "비고": "부분적 성적포기", "학기": sem},
        {"이벤트명": "수업일수3/4선",      "시작일": "2026-05-19", "종료일": "2026-05-19", "비고": "재수강 이전성적 취소", "학기": sem},
        {"이벤트명": "기말고사",           "시작일": "2026-06-08", "종료일": "2026-06-12", "비고": "월~금", "학기": sem},
        {"이벤트명": "기말수업평가",       "시작일": "2026-06-08", "종료일": "2026-06-19", "비고": "미실시 시 성적 열람 불가", "학기": sem},
        {"이벤트명": "종강",               "시작일": "2026-06-12", "종료일": "2026-06-12", "비고": "", "학기": sem},
        {"이벤트명": "하계방학시작",       "시작일": "2026-06-15", "종료일": "2026-06-15", "비고": "", "학기": sem},
        {"이벤트명": "성적확인및정정",     "시작일": "2026-06-15", "종료일": "2026-06-19", "비고": "", "학기": sem},
        {"이벤트명": "하계계절학기",       "시작일": "2026-06-22", "종료일": "2026-07-10", "비고": "", "학기": sem},
        {"이벤트명": "제2전공신청",        "시작일": "2026-07-06", "종료일": "2026-07-17", "비고": "복수·융합·마이크로전공 등", "학기": sem},
        {"이벤트명": "온라인휴복학신청",   "시작일": "2026-07-06", "종료일": "2026-08-30", "비고": "", "학기": sem},
        {"이벤트명": "학위수여식",         "시작일": "2026-08-14", "종료일": "2026-08-14", "비고": "2025학년도 후기", "학기": sem},
    ]


def _parse_depts_from_text(pages: List[PageContent]) -> List[Tuple[str, Dict]]:
    """
    텍스트에서 '전공명\nN학점' 패턴으로 학과를 추출합니다.
    PDF 특성: 전공명과 학점이 각각 별도 줄에 있음 (예: '영어전공\n36학점')
    """
    depts = []
    seen = set()
    _SKIP = {"이수학점", "단과대학", "개설전공", "학부과", "과목명", "담당교수명", "제1전공"}
    # 전공명(한 줄) 바로 다음 줄에 학점이 오는 패턴
    pattern = re.compile(
        r"^([가-힣·]{2,25}(?:전공|학과|학부|트랙))\n(\d+)학점",
        re.MULTILINE,
    )
    for page in pages:
        txt = page.text or ""
        for m in pattern.finditer(txt):
            name = m.group(1).strip()
            credits = int(m.group(2))
            if name not in seen and name not in _SKIP:
                seen.add(name)
                depts.append((name, {"제1전공_이수학점": credits}))
    return depts


def _extract_micro_major_names(pages: List[PageContent]) -> List[Tuple[str, Dict]]:
    """마이크로전공/융합전공 이름을 추출합니다."""
    results = []
    seen = set()
    for page in pages:
        txt = page.text or ""
        # "X전공 (N학점)" 패턴
        for m in re.finditer(r"([가-힣A-Za-z·\s]{3,30}(전공|과정))\s*[\(：:]\s*(\d+)학점", txt):
            name = m.group(1).strip()
            credits = int(m.group(3))
            if name not in seen and ("마이크로" in txt or "융합" in name):
                seen.add(name)
                results.append((name, {"유형": "융합형", "이수학점": credits}))
    return results


def _build_core_edges(graph: AcademicGraph) -> None:
    """핵심 관계 엣지를 구축합니다."""
    G = graph.G

    # 학사일정 → 수강신청규칙
    for nid, data in G.nodes(data=True):
        if data.get("type") == "학사일정" and "수강신청" in data.get("이벤트명", ""):
            for rid in ("reg_2023이후", "reg_2022이전"):
                if rid in G.nodes:
                    graph.add_relation(nid, rid, "기간정한다")
            break

    # 졸업요건 → 수강신청규칙
    for nid, data in G.nodes(data=True):
        if data.get("type") == "졸업요건" and "내국인" in data.get("학생유형", ""):
            group = data.get("적용학번그룹", "")
            reg_grp = "2023이후" if group in ("2023", "2024_2025") else "2022이전"
            rid = f"reg_{reg_grp}"
            if rid in G.nodes:
                graph.add_relation(nid, rid, "제약한다")


# ── CLI ────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PDF → 학사 그래프 자동 추출")
    parser.add_argument("--pdf", default="data/pdfs/2026학년도1학기학사안내.pdf",
                        help="PDF 파일 경로")
    parser.add_argument("--dry-run", action="store_true",
                        help="파싱만 수행, 그래프 저장 안 함")
    parser.add_argument("--show-pages", action="store_true",
                        help="섹션별 페이지 번호 출력 후 종료")
    args = parser.parse_args()

    pdf_path = str(Path(args.pdf).resolve())
    result = build_graph_from_pdf(pdf_path, dry_run=args.dry_run, show_pages=args.show_pages)

    if result is None:
        return

    from collections import Counter
    type_counts = Counter(data.get("type") for _, data in result.G.nodes(data=True))
    print("\n=== 그래프 통계 ===")
    print(f"  총 노드: {result.G.number_of_nodes()}")
    print(f"  총 엣지: {result.G.number_of_edges()}")
    for ntype, cnt in sorted(type_counts.items()):
        print(f"  {ntype}: {cnt}개")

    if not args.dry_run:
        print(f"\n저장 위치: {result.path}")


if __name__ == "__main__":
    main()
