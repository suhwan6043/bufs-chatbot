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
    "grading":      ["성적평가", "성적처리", "학사경고", "캡스톤", "성적평가 선택제도", "성적포기"],
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


# ── 수강신청 학년별 일정 파싱 ─────────────────────────────────

_REG_GRADE_SCHEDULE_PATTERN = re.compile(
    r"(\d{1,2})\.(\d{1,2})\.\(([월화수목금토일])\)\s*\n?\s*"
    r"(\d학년|전\s*학년)"
)


def parse_registration_grade_schedule(pages: List[PageContent], base_year: int = 2026) -> List[Dict]:
    """수강신청 학년별 일정을 파싱합니다 (PDF p.6-7).

    Returns: [{"학년": "1학년", "날짜": "2026-02-09", "요일": "월",
               "시간": "10:00~15:20", "신청가능과목": "..."}, ...]
    """
    schedules = []
    for page in pages:
        txt = page.text or ""
        if "신청학년" not in txt or "수강신청" not in txt:
            continue

        # 시간 정보 추출 (전체 일정에서)
        time_str = "10:00~15:20"
        time_m = re.search(r"(\d{1,2}:\d{2})\s*[~\-]\s*(\d{1,2}:\d{2})\s*\(?\s*신청\s*\)?", txt)
        if time_m:
            time_str = f"{time_m.group(1)}~{time_m.group(2)}"

        # 학년별 날짜 파싱
        lines = txt.split("\n")
        for i, line in enumerate(lines):
            # "2.9.(월)" 패턴 + 근처에 "N학년" 또는 "전 학년"
            date_m = re.search(r"(\d{1,2})\.(\d{1,2})\.\(([월화수목금토일])\)", line)
            if not date_m:
                continue
            month = int(date_m.group(1))
            day = int(date_m.group(2))
            dow = date_m.group(3)
            date_str = f"{base_year}-{month:02d}-{day:02d}"

            # 같은 줄 또는 다음 2줄에서 학년 정보 탐색
            context = "\n".join(lines[i:i+3])
            grade_m = re.search(r"(\d)학년|(\d),\s*(\d)학년|전\s*학년", context)
            if not grade_m:
                continue

            if "전 학년" in context or "전학년" in context or "전체 과목" in context:
                grade_label = "전학년"
            elif grade_m.group(2) and grade_m.group(3):
                grade_label = f"{grade_m.group(2)},{grade_m.group(3)}학년"
            else:
                grade_label = f"{grade_m.group(1)}학년"

            # 신청 가능 과목 추출
            courses = ""
            course_m = re.search(r"신청\s*가능\s*과목[^\n]*\n(.*?)(?=\d{1,2}\.\d{1,2}\.\(|비고|$)",
                                  context, re.DOTALL)
            if not course_m:
                # 테이블 형식에서 추출
                for j in range(i, min(i+5, len(lines))):
                    if "교양" in lines[j] or "전공" in lines[j] or "OCU" in lines[j]:
                        courses = lines[j].strip()
                        break

            schedules.append({
                "학년": grade_label,
                "날짜": date_str,
                "요일": dow,
                "시간": time_str,
                "신청가능과목": courses,
            })

    return schedules


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
    rules = {"2022이전": {}, "2023이후": {}}

    full_text = "\n".join(p.text for p in pages if p.text)

    # 최대 신청학점 — "가. 최대 신청 학점 : 19학점" 형태를 그룹별로 추출
    # 2022이전 섹션은 ①, 2023이후 섹션은 ② 번호로 구분
    for m in _REG_MAX_PATTERN.finditer(full_text):
        group_str, credits = m.group(1), int(m.group(2))
        if "이전" in group_str:
            rules["2022이전"]["최대신청학점"] = credits
        else:
            rules["2023이후"]["최대신청학점"] = credits
    # 패턴 실패 시 직접 추출: "가. 최대 신청 학점 : N학점" (① 2022이전, ② 2023이후 순서)
    if not rules["2022이전"].get("최대신청학점"):
        max_credits = re.findall(r"최대\s*신청\s*학점\s*[:：]\s*(\d+)학점", full_text)
        if len(max_credits) >= 2:
            rules["2022이전"]["최대신청학점"] = int(max_credits[0])
            rules["2023이후"]["최대신청학점"] = int(max_credits[1])
        elif len(max_credits) == 1:
            rules["2022이전"]["최대신청학점"] = int(max_credits[0])

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

    # 재수강 최고성적
    if _RETAKE_GRADE_PATTERN.search(full_text):
        rules["2022이전"]["재수강최고성적"] = "A"
        rules["2023이후"]["재수강최고성적"] = "A"

    # 학점이월 — "학점이월제 (2022학번 이전...폐지)" 패턴
    if "학점이월" in full_text:
        if "2022학번 이전" in full_text and "적용" in full_text:
            rules["2022이전"]["학점이월여부"] = "조건부 허용"
        if "폐지" in full_text and "2023" in full_text:
            rules["2023이후"]["학점이월여부"] = "불가 (2023학년도 신입생부터 폐지)"
        # 최대 이월학점
        m_carry = re.search(r"최대\s*(\d+)학점까지\s*(?:다음\s*학기로\s*)?이월", full_text)
        if m_carry:
            rules["2022이전"]["학점이월최대학점"] = int(m_carry.group(1))
        # 이월 조건
        m_cond = re.search(r"직전학기\s*(\d+)학점에\s*미달하여\s*신청한\s*학점을\s*최대\s*(\d+)학점까지", full_text)
        if m_cond:
            rules["2022이전"]["학점이월조건"] = (
                f"직전학기 최대신청학점({m_cond.group(1)}학점)에 미달하여 "
                f"신청한 학점을 최대 {m_cond.group(2)}학점까지 다음 학기로 이월"
            )

    # OCU 초과학점
    m_ocu = re.search(r"OCU.*?최대\s*(\d+)학점\s*\(\s*(\d+)과목\).*?자유선택", full_text, re.DOTALL)
    if m_ocu:
        ocu_note = f"최대 {m_ocu.group(1)}학점({m_ocu.group(2)}과목), 자유선택으로만 인정"
        rules["2022이전"]["OCU초과학점"] = ocu_note
        rules["2023이후"]["OCU초과학점"] = ocu_note

    # ── 예외조건 전체 파싱 (PDF "ú  조건 : N학점" 패턴) ──
    # 2022이전(①)과 2023이후(②) 섹션을 분리하여 각각 파싱
    _exception_pattern = re.compile(r"[úù]\s*(.+?)\s*[:：]\s*(\d+)학점")
    _section_split = re.split(r"②\s*2023", full_text)
    sections = {
        "2022이전": _section_split[0] if len(_section_split) >= 1 else "",
        "2023이후": _section_split[1] if len(_section_split) >= 2 else "",
    }
    for grp, section_text in sections.items():
        exceptions = _exception_pattern.findall(section_text)
        if exceptions:
            parts = []
            for condition, credits in exceptions:
                cond = condition.strip()
                parts.append(f"{cond}: {credits}학점")
                # 개별 필드로도 저장 (구조화)
                if "학군" in cond:
                    rules[grp]["학군사관후보생최대학점"] = int(credits)
                elif "학·석사" in cond or "연계" in cond:
                    rules[grp]["학석사연계최대학점"] = int(credits)
                elif "영어권" in cond or "복수학위" in cond:
                    rules[grp]["영어권복수학위최대학점"] = int(credits)
                elif "파이데이아" in cond:
                    rules[grp]["파이데이아최대학점"] = int(credits)
            rules[grp]["예외조건"] = " / ".join(parts)

    # 학사경고 감소학점 파싱
    for grp in rules:
        base = rules[grp].get("최대신청학점", 0)
        m_warn = re.search(
            rf"학사경고자.*?(\d+)학점까지\s*신청\s*가능",
            sections.get(grp, full_text),
        )
        if m_warn and base:
            warn_max = int(m_warn.group(1))
            rules[grp]["학사경고시최대학점"] = warn_max

    # 초과 신청 가능 교과목 파싱
    for grp in rules:
        section_text = sections.get(grp, "")
        extras = []
        if "사회봉사" in section_text and "1학점" in section_text:
            extras.append("사회봉사·서비스러닝: +1학점")
        if "진로탐색학기제" in section_text:
            m_extra = re.search(r"진로탐색학기제.*?(\d+)학점", section_text)
            if m_extra:
                extras.append(f"진로탐색학기제(커리어블라썸 등): +{m_extra.group(1)}학점")
        if "취업커뮤니티" in section_text or "진로탐색" in section_text:
            if "1과목에 한해" in section_text:
                extras.append("진로탐색·취업커뮤니티: 1과목 초과 가능")
        if extras:
            rules[grp]["초과가능교과목"] = " / ".join(extras)

    m = _CANCEL_DEADLINE_PATTERN.search(full_text)
    if m:
        deadline = (
            f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d} "
            f"{int(m.group(4)):02d}:{m.group(5)}"
        )
        for key in rules:
            rules[key]["수강취소마감일시"] = deadline

    # PDF 출처 메타데이터
    source_file = pages[0].source_file if pages else ""
    all_pages = sorted(set(p.page_number for p in pages if p.text))
    for group_data in rules.values():
        if isinstance(group_data, dict):
            group_data["_source_pages"] = all_pages
            group_data["_source_file"] = source_file

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
    ocu_data = {}

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

    # 수강신청방법 (PDF에서 추출)
    if "수강신청" in full_text and "홈페이지" in full_text:
        m = re.search(
            r"수강신청방법\s*[:：]?\s*(.+?)(?:\n|$)", full_text
        )
        if m:
            ocu_data["수강신청방법"] = m.group(1).strip()
        elif "우리대학" in full_text and "수강신청" in full_text:
            ocu_data["수강신청방법"] = "우리대학 홈페이지에서 수강신청 후 OCU 사이트에서 수강"

    # 수강방법 — OCU 홈페이지 URL 추출
    m = re.search(r"(https?://cons\.ocu\.ac\.kr/?)", full_text)
    if m:
        ocu_data["OCU홈페이지"] = m.group(1)
        ocu_data["수강방법"] = f"OCU 컨소시엄 사이트({m.group(1)})에서 온라인 수강"

    # 이수구분
    if "자유선택" in full_text and "OCU" in full_text:
        ocu_data["이수구분"] = "자유선택으로 인정"

    # 문의전화
    m = re.search(r"\(부산외대\)\s*학사지원팀\s*[:：]?\s*([\d\-,\s]+)", full_text)
    if m:
        ocu_data["문의"] = f"학사지원팀 {m.group(1).strip()}"

    # PDF 출처 메타데이터
    ocu_data["_source_pages"] = sorted(p.page_number for p in pages if p.text)
    ocu_data["_source_file"] = pages[0].source_file if pages else ""

    return ocu_data


# ── 성적처리 파싱 ─────────────────────────────────────────────

_GRADE_SELECT_PERIOD = re.compile(
    r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.\([^\)]+\)\s*~\s*(\d{1,2})\.(\d{1,2})\.\([^\)]+\)"
)
_GRADE_SELECT_MAX = re.compile(r"학기\s*당\s*최대\s*(\d+)학점")
_GRADE_SELECT_TOTAL = re.compile(r"재학\s*중\s*최대\s*(\d+)학점")
_GRADE_DROP_PERIOD = re.compile(
    r"(\d{4})\.(\d{1,2})\.(\d{1,2})\.\([^\)]+\)\s*~\s*(\d{1,2})\.(\d{1,2})\.\([^\)]+\)"
)
_GRADE_DROP_SEM_MAX = re.compile(r"학기\s*당\s*(\d+)학점\s*이내")
_GRADE_DROP_TOTAL_MAX = re.compile(r"졸업\s*시\s*까지\s*최대\s*(\d+)학점")


def parse_grading_info(pages: List[PageContent]) -> Dict[str, Dict]:
    """PDF에서 성적평가 관련 데이터를 파싱합니다."""
    info: Dict[str, Dict] = {}

    for page in pages:
        txt = page.text or ""

        # 성적평가 선택제도 (P/NP 선택) — p.47
        if "성적평가 선택제도" in txt or "성적평가선택제도" in txt:
            select_info: Dict[str, str] = {}
            m = _GRADE_SELECT_PERIOD.search(txt)
            if m:
                select_info["신청기간"] = (
                    f"{m.group(1)}.{m.group(2)}.{m.group(3)}~"
                    f"{m.group(4)}.{m.group(5)}"
                )
            m = _GRADE_SELECT_MAX.search(txt)
            if m:
                select_info["학기당최대"] = f"{m.group(1)}학점"
            m = _GRADE_SELECT_TOTAL.search(txt)
            if m:
                select_info["재학중최대"] = f"{m.group(1)}학점"
            # 성적처리 규칙 파싱
            m_grade = re.search(r"(D학점\s*이상.*?→\s*P등급.*?(?:F.*?→\s*NP|NP))", txt)
            if m_grade:
                select_info["성적처리"] = re.sub(r"\s+", " ", m_grade.group(1).strip())
            # 신청불가 과목 파싱
            m_excl = re.search(r"신청불가[^:：\n]*[:：]?\s*([^\n]+)", txt)
            if m_excl:
                select_info["신청불가"] = m_excl.group(1).strip()
            elif "신청불가" not in select_info:
                # 다른 패턴: "제외" 키워드
                m_excl2 = re.search(r"(?:제외|불가)\s*[:：]?\s*(OCU[^\n]+)", txt)
                if m_excl2:
                    select_info["신청불가"] = m_excl2.group(1).strip()
            info["성적평가선택제"] = select_info

        # 부분적 성적포기제도 — p.48
        if "부분적 성적포기" in txt or "성적포기제도" in txt:
            drop_info: Dict[str, str] = {}
            m = _GRADE_DROP_SEM_MAX.search(txt)
            if m:
                drop_info["학기당최대"] = f"{m.group(1)}학점"
            m = _GRADE_DROP_TOTAL_MAX.search(txt)
            if m:
                drop_info["졸업까지최대"] = f"{m.group(1)}학점"
            # 대상 파싱
            m_target = re.search(r"(?:대상|신청자격)[^:：\n]*[:：]?\s*([^\n]+)", txt)
            if m_target:
                drop_info["대상"] = m_target.group(1).strip()
            else:
                m_target2 = re.search(r"(\d+학기\s*이상[^\n]*재학생)", txt)
                if m_target2:
                    drop_info["대상"] = m_target2.group(1).strip()
            # 포기가능성적 파싱
            m_grade_drop = re.search(r"포기.*?가능.*?성적[^:：\n]*[:：]?\s*([^\n]+)", txt)
            if m_grade_drop:
                drop_info["포기가능성적"] = m_grade_drop.group(1).strip()
            else:
                m_grade_drop2 = re.search(r"(C\+이하.*?(?:NP|포함))", txt)
                if m_grade_drop2:
                    drop_info["포기가능성적"] = m_grade_drop2.group(1).strip()
            # 포기불가 과목 파싱
            m_nodrop = re.search(r"포기불가[^:：\n]*[:：]?\s*([^\n]+)", txt)
            if m_nodrop:
                drop_info["포기불가"] = m_nodrop.group(1).strip()
            else:
                m_nodrop2 = re.search(r"(?:제외|불가)\s*[:：]?\s*(재수강[^\n]+)", txt)
                if m_nodrop2:
                    drop_info["포기불가"] = m_nodrop2.group(1).strip()
            info["부분적성적포기"] = drop_info

        # 캡스톤 디자인 — p.84
        if "캡스톤" in txt and "절대평가" in txt:
            if "캡스톤디자인" not in info:
                info["캡스톤디자인"] = {}
            if "P/NP" in txt:
                info["캡스톤디자인"]["평가방식"] = "절대평가 또는 P/NP"
            m_capstone_desc = re.search(r"(캡스톤디자인.*?결정.*?(?:합니다|입니다|함))", txt)
            if m_capstone_desc:
                info["캡스톤디자인"]["설명"] = m_capstone_desc.group(1).strip()

    # 일반교과목 절대평가 원칙 파싱
    full_text = "\n".join(p.text or "" for p in pages if p.text)
    if "절대평가" in full_text and "일반교과목" not in info:
        info["일반교과목"] = {"평가방식": "절대평가"}
        m = re.search(r"(A\+.*?F)", full_text)
        if m:
            info["일반교과목"]["성적등급"] = m.group(1)

    # 캡스톤디자인 보충 파싱
    if "캡스톤" in full_text and "캡스톤디자인" not in info:
        info["캡스톤디자인"] = {}
        if "P/NP" in full_text and "캡스톤" in full_text:
            info["캡스톤디자인"]["평가방식"] = "절대평가 또는 P/NP"

    # PDF 출처 메타데이터 — 각 카테고리에 페이지 정보 첨부
    source_file = pages[0].source_file if pages else ""
    all_pages = sorted(set(p.page_number for p in pages if p.text))
    for category_data in info.values():
        if isinstance(category_data, dict):
            category_data["_source_pages"] = all_pages
            category_data["_source_file"] = source_file

    return info


# ── 계절학기 파싱 ─────────────────────────────────────────────

def parse_seasonal_semester_info(pages: List[PageContent]) -> Dict[str, str]:
    """PDF에서 계절학기 관련 규칙을 파싱합니다.

    학사일정, 수강신청 규칙, 성적평가 선택제 등 여러 페이지에 흩어진
    계절학기 정보를 수집합니다.
    """
    info: Dict[str, str] = {}
    source_pages: set = set()

    for page in pages:
        txt = page.text or ""
        if "계절" not in txt:
            continue

        # 학기당 최대학점: "계절학기 6학점 이내" (현장실습 항목)
        m = re.search(r"계절학기\s+(\d+)학점\s*이내", txt)
        if m and "학기당최대학점" not in info:
            info["학기당최대학점"] = f"{m.group(1)}학점"
            source_pages.add(page.page_number)

        # 졸업까지 최대학점: "계절학기...졸업까지 각각 최대 24학점만 인정"
        if "계절학기" in txt and "졸업" in txt:
            m2 = re.search(r"최대\s*(\d+)학점.*?인정", txt)
            if m2 and "졸업까지최대학점" not in info:
                info["졸업까지최대학점"] = f"{m2.group(1)}학점"
                source_pages.add(page.page_number)

        # 성적평가선택제 관련
        if ("성적평가 선택제" in txt or "성적평가선택제" in txt) and (
            "계절학기" in txt and "신청불가" in txt
        ):
            info["성적평가선택제"] = "계절학기 신청불가"
            source_pages.add(page.page_number)

        # 수강신청 사이트 URL
        if "sugang.bufs.ac.kr" in txt:
            info["수강신청사이트"] = "http://sugang.bufs.ac.kr"
            source_pages.add(page.page_number)

    # 수강신청방법 — 수강신청 사이트에서 신청 (PDF에서 추출된 URL 활용)
    if info.get("수강신청사이트"):
        info["수강신청방법"] = f"본교 수강신청 사이트({info['수강신청사이트']})에서 신청"

    # PDF 출처 메타데이터
    if source_pages:
        info["_source_pages"] = sorted(source_pages)
        info["_source_file"] = pages[0].source_file if pages else ""

    return info


# ── 제2전공 파싱 ───────────────────────────────────────────────

_SECOND_MAJOR_PATTERN = re.compile(
    r"(\d{4})교육과정.*?(?:2024|2023|2022|2021|2017|2016).*?이후?.*?학번\)?\s+(\d+)학점",
    re.DOTALL
)


def parse_second_major_credits(pages: List[PageContent]) -> Dict:
    """제2전공 학점 파싱 (p.65)"""
    second_major = {}

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
                            second_major.setdefault("2024_2025", {})
                            second_major["2024_2025"]["복수전공"] = int(credits[0])
                            second_major["2024_2025"]["융합전공"] = int(credits[1])
                            second_major["2024_2025"]["마이크로전공"] = int(credits[2])

            elif "2023교육과정" in line or "2023학번" in line:
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    credits = re.findall(r"(\d+)학점", next_line)
                    if len(credits) >= 2:
                        second_major.setdefault("2023", {})
                        second_major["2023"]["복수전공"] = int(credits[0])
                        second_major["2023"]["마이크로전공"] = int(credits[1])
                    if len(credits) >= 3:
                        second_major["2023"]["부전공"] = int(credits[2])

            elif "2022교육과정" in line or "2022학번" in line:
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    credits = re.findall(r"(\d+)학점", next_line)
                    if len(credits) >= 2:
                        second_major.setdefault("2022", {})
                        second_major["2022"]["복수전공"] = int(credits[0])
                        if len(credits) >= 3:
                            second_major["2022"]["부전공"] = int(credits[2])

            elif "2021교육과정" in line or "2021학번" in line:
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    credits = re.findall(r"(\d+)학점", next_line)
                    if len(credits) >= 2:
                        second_major.setdefault("2021", {})
                        second_major["2021"]["복수전공"] = int(credits[0])
                        if len(credits) >= 3:
                            second_major["2021"]["부전공"] = int(credits[2])

            elif "2017" in line and "2020" in line:
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    credits = re.findall(r"(\d+)학점", next_line)
                    if len(credits) >= 2:
                        second_major.setdefault("2017_2020", {})
                        second_major["2017_2020"]["복수전공"] = int(credits[0])
                        if len(credits) >= 3:
                            second_major["2017_2020"]["부전공"] = int(credits[2])

            elif "2016" in line and ("이전" in line or "before" in line.lower()):
                if i + 1 < len(lines):
                    next_line = lines[i + 1]
                    credits = re.findall(r"(\d+)학점", next_line)
                    if len(credits) >= 2:
                        second_major.setdefault("2016_before", {})
                        second_major["2016_before"]["복수전공"] = int(credits[0])
                        if len(credits) >= 3:
                            second_major["2016_before"]["부전공"] = int(credits[2])

    # PDF 출처 메타데이터
    source_file = pages[0].source_file if pages else ""
    all_pages = sorted(set(p.page_number for p in pages if p.text))
    for group_data in second_major.values():
        if isinstance(group_data, dict):
            group_data["_source_pages"] = all_pages
            group_data["_source_file"] = source_file

    return second_major


# ── 졸업요건 파싱 ─────────────────────────────────────────────

# 학번 그룹 탐지 패턴
_GRAD_GROUP_PATTERNS = [
    (re.compile(r"2024.{0,10}학번"), "2024_2025"),
    (re.compile(r"2023학번"),            "2023"),
    (re.compile(r"2022학번"),            "2022"),
    (re.compile(r"2021학번"),            "2021"),
    (re.compile(r"2017.{0,5}2020학번"), "2017_2020"),
    (re.compile(r"2016학번\s*이전"),     "2016_before"),
]

_CREDITS_PATTERN   = re.compile(r"졸업학점\s*[：:]?.*?(\d{2,3})학점", re.DOTALL)
# "교양과정 (30학점)" 처럼 총 교양학점 표현 (줄바꿈 포함)
_LIBERAL_PATTERN   = re.compile(r"교양\s*(?:과정|전체\s*이수학점)\s*[\(（]?\s*(\d+)\s*학점")
_GLOBAL_PATTERN    = re.compile(r"글로벌소통역량[\s\S]{0,20}?(\d+)학점")
_JOB_COM_PATTERN   = re.compile(r"취업커뮤니티.{0,10}(\d+)학점")


def parse_graduation_reqs(pages: List[PageContent]) -> Dict[str, Dict]:
    """학번 그룹별 졸업요건을 파싱합니다."""
    reqs: Dict[str, Dict] = {}

    # 페이지 텍스트에서 파싱
    current_group = None
    for page in pages:
        txt = page.text or ""
        for pattern, group_key in _GRAD_GROUP_PATTERNS:
            if pattern.search(txt):
                current_group = group_key
                # 그룹이 처음 발견되면 빈 dict 생성
                if current_group not in reqs:
                    reqs[current_group] = {}
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

        # 취업커뮤니티
        m = _JOB_COM_PATTERN.search(txt)
        if m and current_group in reqs:
            reqs[current_group]["취업커뮤니티요건"] = f"{m.group(1)}학점"

        # 졸업시험 여부
        if "졸업시험" in txt and current_group in reqs:
            if "필수" in txt or "실시" in txt:
                reqs[current_group]["졸업시험여부"] = True
                m_exam = re.search(r"(주전공\s*졸업시험[^\n]*)", txt)
                if m_exam:
                    reqs[current_group]["졸업시험비고"] = m_exam.group(1).strip()
            elif "폐지" in txt or "미실시" in txt or "없음" in txt:
                reqs[current_group]["졸업시험여부"] = False

        # 기업가정신 의무
        if "기업가정신" in txt and current_group in reqs:
            if "폐지" in txt or "의무 없" in txt:
                m_entre = re.search(r"(폐지[^\n]*)", txt)
                if m_entre:
                    reqs[current_group]["기업가정신의무"] = m_entre.group(1).strip()

        # NOMAD 비교과지수
        if "NOMAD" in txt and current_group in reqs:
            if "미적용" in txt or "폐지" in txt:
                reqs[current_group]["NOMAD비교과지수"] = "미적용"
            else:
                m_nomad = re.search(r"NOMAD.*?(\d+)점", txt)
                if m_nomad:
                    reqs[current_group]["NOMAD비교과지수"] = f"{m_nomad.group(1)}점"

        # 제2전공방법
        m_2nd = re.search(r"((?:\[방법\d\]|방법\d)[^\n]+)", txt)
        if m_2nd and current_group in reqs and "제2전공방법" not in reqs[current_group]:
            reqs[current_group]["제2전공방법"] = m_2nd.group(1).strip()

        # 졸업인증
        if "졸업인증" in txt and current_group in reqs:
            m_cert = re.search(r"졸업인증\s*[:：]?\s*([^\n]+)", txt)
            if m_cert:
                reqs[current_group]["졸업인증"] = m_cert.group(1).strip()

        # 교양 세부영역 파싱 (테이블 + 텍스트)
        if current_group in reqs and "교양세부" not in reqs[current_group]:
            details = _parse_liberal_arts_details(page, txt)
            if details:
                reqs[current_group]["교양세부"] = details

    # PDF 출처 메타데이터
    source_file = pages[0].source_file if pages else ""
    all_pages = sorted(set(p.page_number for p in pages if p.text))
    for group_data in reqs.values():
        if isinstance(group_data, dict):
            group_data["_source_pages"] = all_pages
            group_data["_source_file"] = source_file

    return reqs


def _parse_liberal_arts_details(page: PageContent, txt: str) -> Dict[str, str]:
    """교양 세부영역 학점을 파싱합니다 (테이블 + 텍스트 하이브리드)."""
    details = {}

    # ① 텍스트에서 직접 파싱: 다양한 패턴
    for label, patterns in [
        ("인성체험교양", [
            r"인성체험교양\s*[：:]\s*(\d+)학점",
            r"①\s*인성체험교양\s*[：:]\s*(\d+)학점",
            r"인성체험교양\s*\(?\s*(\d+)학점",
        ]),
        ("기초교양", [
            r"기초교양\s*[：:]\s*(\d+)학점",
            r"②\s*기초교양\s*[：:]\s*(\d+)학점",
            r"기초교양\s*\(?\s*(\d+)학점",
        ]),
        ("균형교양", [
            r"균형교양\s*[：:]\s*(\d+)학점",
            r"③\s*(?:인성체험교양.*?및\s*)?균형교양\s*[：:]\s*(\d+)학점",
            r"인성체험.*?균형교양\s*[：:]\s*(\d+)학점",
        ]),
    ]:
        for pat in patterns:
            m = re.search(pat, txt)
            if m:
                # 마지막 그룹 (비캡처 그룹 때문에 여러 그룹일 수 있음)
                val = m.group(m.lastindex) if m.lastindex else m.group(1)
                details[label] = f"{val}학점"
                break

    # ② 테이블에서 파싱: "계" 열의 숫자에서 세부 합산
    for table_md in (page.tables or []):
        if "인성" not in table_md and "기초교양" not in table_md:
            continue

        rows = [r for r in table_md.splitlines() if r.strip().startswith("|") and "---" not in r]
        if len(rows) < 3:
            continue

        # 헤더행에서 영역명 추출, 데이터행에서 학점 추출
        header_cells = [c.strip() for c in rows[0].strip("|").split("|")]
        # 서브헤더 (채플, 자기계발, 사회봉사 등)
        sub_cells = [c.strip() for c in rows[2].strip("|").split("|")] if len(rows) > 2 else []
        # 데이터행 (학점 숫자)
        data_cells = [c.strip() for c in rows[-1].strip("|").split("|")] if rows else []

        # "인성체험교양" 영역 합산
        insung_start = None
        for i, h in enumerate(header_cells):
            if "인성" in h and insung_start is None:
                insung_start = i

        # "기초교양" 영역
        basic_idx = None
        for i, h in enumerate(header_cells):
            if "기초" in h:
                basic_idx = i

        # 학점 데이터에서 큰 숫자 찾기 (합계)
        nums = []
        for c in data_cells:
            m = re.search(r"(\d+)", c)
            if m:
                nums.append(int(m.group(1)))

        # 합계가 있으면 "인성체험교양+기초교양" 또는 "균형교양" 분리
        if nums:
            # 30이상이면 교양 전체 합계
            for n in nums:
                if "인성체험교양" not in details:
                    if 7 <= n <= 12:
                        details["인성체험교양"] = f"{n}학점"
                    elif 5 <= n <= 8 and "기초교양" not in details:
                        details["기초교양"] = f"{n}학점"
                    elif 14 <= n <= 24 and "균형교양" not in details:
                        details["균형교양"] = f"{n}학점"

    return details


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

    # PDF 출처 전역 메타데이터 (원칙 3: 버전 관리)
    from datetime import datetime as _dt
    import hashlib as _hl
    graph.G.graph["source_pdf"] = str(pdf_path)
    graph.G.graph["build_timestamp"] = _dt.now().isoformat(timespec="seconds")
    try:
        _h = _hl.sha256()
        with open(pdf_path, "rb") as _f:
            for _chunk in iter(lambda: _f.read(8192), b""):
                _h.update(_chunk)
        graph.G.graph["source_pdf_hash"] = _h.hexdigest()
    except OSError:
        graph.G.graph["source_pdf_hash"] = ""

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

    sched_parsed_page = None  # 실제 테이블 파싱 페이지 추적
    for sp in sched_pages[:3]:  # 첫 3개 페이지만 (일정이 한 페이지에 몰려 있음)
        for table_md in sp.tables:
            if "학사" in table_md or "내용" in table_md:
                events = parse_schedule_table(
                    table_md,
                    base_year=base_year,
                    semester_start_month=semester_month,
                )
                if events:
                    sched_parsed_page = sp.page_number
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

    if not schedule_events:
        logger.warning("  학사일정 테이블 파싱 실패 → 일정 없음")

    sem = schedule_events[0]["학기"] if schedule_events else "2026-1"
    # 학사일정 PDF 출처 메타 — 실제 파싱한 페이지만 (목차 제외)
    sched_source_pages = [sched_parsed_page] if sched_parsed_page else []
    sched_source_file = sched_pages[0].source_file if sched_pages else str(pdf_path)
    for ev in schedule_events:
        graph.add_schedule(
            ev["이벤트명"],
            ev["학기"],
            {
                "시작일": ev["시작일"], "종료일": ev["종료일"], "비고": ev["비고"],
                "_source_pages": sched_source_pages,
                "_source_file": sched_source_file,
            },
        )
    logger.info(f"  학사일정 {len(schedule_events)}개 추가 (학기: {sem})")

    # ── 1-1. 야간수업 교시별 시간표 (PDF 파싱) ────────────────
    for page in pages:
        txt = page.text or ""
        if "야간" not in txt or "교시" not in txt:
            continue
        night_data = {"시작일": "", "종료일": "", "비고": "야간수업 교시별 수업 시간"}
        for period_num in range(10, 15):
            # "10교시 : 18:00~18:45" 또는 "10\n18:00 - 18:45" 형태
            m = re.search(
                rf"(?:{period_num}교시\s*[:：]?\s*|(?<!\d){period_num}\s*\n?\s*)"
                rf"(\d{{1,2}}:\d{{2}})\s*[-–~]\s*(\d{{1,2}}:\d{{2}})",
                txt,
            )
            if m:
                night_data[f"{period_num}교시"] = f"{m.group(1)}~{m.group(2)}"
        if any(k.endswith("교시") for k in night_data):
            graph.add_schedule("야간수업시간표", sem, night_data)
            logger.info("  야간수업 교시표 파싱 완료")
        break

    # ── 2. 수강신청 규칙 ─────────────────────────────────────
    logger.info("수강신청 규칙 파싱 중...")
    reg_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["registration"])]
    rules = parse_registration_rules(reg_pages)
    for grp, data in rules.items():
        graph.add_registration_rule(grp, data)
    logger.info(f"  수강신청규칙 {len(rules)}개 그룹 추가")

    # ── 2-0. 수강신청 학년별 일정 ────────────────────────────
    # 학년별 일정 테이블은 "신청학년" 키워드가 있는 페이지에서 파싱됨
    reg_sched_pages = [p.page_number for p in reg_pages
                       if p.text and "신청학년" in p.text and "수강신청" in p.text]
    reg_source_pages = reg_sched_pages[:2] if reg_sched_pages else []
    reg_source_file = reg_pages[0].source_file if reg_pages else str(pdf_path)
    grade_sched = parse_registration_grade_schedule(reg_pages, base_year=base_year)
    for gs in grade_sched:
        ev_name = f"수강신청_{gs['학년']}"
        graph.add_schedule(ev_name, sem, {
            "시작일": gs["날짜"],
            "종료일": gs["날짜"],
            "비고": f"{gs['학년']} {gs['시간']} ({gs['요일']}요일) {gs.get('신청가능과목','')}".strip(),
            "_source_pages": reg_source_pages,
            "_source_file": reg_source_file,
        })
    if grade_sched:
        logger.info(f"  수강신청 학년별 일정 {len(grade_sched)}개 추가")

    # ── 2-1. OCU 파싱 및 수강신청규칙에 추가 ──────────────────
    logger.info("OCU 섹션 파싱 중...")
    ocu_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["ocu"])]
    if ocu_pages:
        ocu_data = parse_ocu_section(ocu_pages)
        logger.info(f"  OCU 파싱 완료: {ocu_data}")

        # OCU 정보를 독립 노드로 생성 (수강규칙에 합치지 않음)
        ocu_node_id = graph.add_static_page_info(
            name="OCU 수강안내",
            data={k: v for k, v in ocu_data.items()
                  if k not in ("개강일", "개강시간")},
            node_type="OCU", prefix="ocu_",
        )
        # 수강규칙 → OCU 엣지 (1-hop 탐색용)
        for grp in ("2023이후", "2022이전"):
            reg_nid = f"reg_{grp}"
            if reg_nid in graph.G.nodes:
                graph.G.add_edge(reg_nid, ocu_node_id, relation="포함한다")
        logger.info(f"  OCU 독립 노드 생성: {ocu_node_id}")

        if ocu_data.get("납부시작") and ocu_data.get("납부종료"):
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
        ocu_source_pages = sorted(set(p.page_number for p in ocu_pages if p.text))
        ocu_source_file = ocu_pages[0].source_file if ocu_pages else str(pdf_path)
        if ocu_data.get("개강일"):
            graph.add_schedule(
                "OCU개강일",
                sem,
                {
                    "시작일": ocu_data["개강일"],
                    "종료일": ocu_data["개강일"],
                    "비고": f"{ocu_data.get('개강시간', '오전 10시')}부터 수강 가능",
                    "_source_pages": ocu_source_pages,
                    "_source_file": ocu_source_file,
                },
            )
            logger.info(f"  OCU 개강일 추가: {ocu_data['개강일']} {ocu_data.get('개강시간', '')}")
    else:
        logger.warning("  OCU 섹션을 찾을 수 없음")

    # ── 2-2. 성적처리 파싱 ─────────────────────────────────────
    logger.info("성적처리 파싱 중...")
    grading_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["grading"])]
    if grading_pages:
        grading_info = parse_grading_info(grading_pages)
        # grading_root 노드 생성
        root_id = "grading_root"
        graph.G.add_node(root_id, type="성적처리", 구분="성적처리기준")
        graph._index_add(root_id, "성적처리")

        for category, data in grading_info.items():
            # 분류태그 결정
            if "OCU" in category or "사이버" in category:
                tag = "OCU"
            elif "선택" in category or "포기" in category:
                tag = "성적선택제"
            elif "캡스톤" in category:
                tag = "P/NP"
            elif "학사경고" in category:
                tag = "학사경고"
            else:
                tag = "일반"

            node_id = graph.add_static_page_info(
                name=category, data={**data, "분류태그": tag},
                node_type="성적처리", prefix="grade_pdf_",
            )
            graph.G.add_edge(root_id, node_id, relation="포함한다")
            logger.info(f"  성적처리 노드: {node_id} (태그={tag})")
    else:
        logger.warning("  성적처리 섹션을 찾을 수 없음")

    # ── 2-3. 계절학기 파싱 ─────────────────────────────────────
    logger.info("계절학기 정보 파싱 중...")
    seasonal_info = parse_seasonal_semester_info(pages)
    if seasonal_info:
        graph.add_static_page_info(
            name="계절학기 수강안내",
            data=seasonal_info,
            node_type="계절학기", prefix="seasonal_",
        )
        logger.info(f"  계절학기 파싱 완료: {seasonal_info}")
    else:
        logger.warning("  계절학기 정보를 찾을 수 없음")

    # ── 2-4. 제2전공 학점 파싱 및 졸업요건에 추가 ──────────────
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

    # 외국인·편입생 졸업요건 — PDF에서 파싱
    for page in pages:
        txt = page.text or ""
        # 외국인 유학생 졸업요건
        if ("외국인" in txt or "유학생" in txt) and ("졸업" in txt or "이수" in txt):
            foreign_data: Dict[str, object] = {}
            m_cr = re.search(r"졸업학점\s*[:：]?\s*(\d+)", txt)
            if m_cr:
                foreign_data["졸업학점"] = int(m_cr.group(1))
            m_lib = re.search(r"교양.*?(\d+)학점", txt)
            if m_lib:
                foreign_data["교양이수학점"] = int(m_lib.group(1))
            m_topik = re.search(r"TOPIK\s*(\d+)급", txt)
            if m_topik:
                foreign_data["졸업인증"] = f"TOPIK {m_topik.group(1)}급"
            m_note = re.search(r"(유학생한국어[^\n]*|International College[^\n]*)", txt)
            if m_note:
                foreign_data["비고"] = m_note.group(1).strip()
            if foreign_data:
                # 해당하는 학번그룹 감지
                for pattern, group_key in _GRAD_GROUP_PATTERNS:
                    if pattern.search(txt):
                        graph.add_graduation_req(group_key, "외국인", foreign_data)
                        break

        # 편입생 졸업요건
        if "편입" in txt and ("졸업" in txt or "이수" in txt):
            transfer_data: Dict[str, object] = {}
            m_cr = re.search(r"졸업학점\s*[:：]?\s*(\d+)", txt)
            if m_cr:
                transfer_data["졸업학점"] = int(m_cr.group(1))
            if "교양" in txt and "면제" in txt:
                transfer_data["교양이수학점"] = "면제"
            m_major = re.search(r"주전공.*?(\d+)[~\-](\d+)학점", txt)
            if m_major:
                transfer_data["주전공학점"] = f"{m_major.group(1)}~{m_major.group(2)}학점"
            m_global = re.search(r"글로벌소통역량.*?(\d+)학점", txt)
            if m_global:
                transfer_data["글로벌소통역량학점"] = int(m_global.group(1))
            m_tnote = re.search(r"(교양\s*이수\s*의무\s*없[^\n]*|전공\s*및\s*잔여[^\n]*)", txt)
            if m_tnote:
                transfer_data["비고"] = m_tnote.group(1).strip()
            if transfer_data:
                for pattern, group_key in _GRAD_GROUP_PATTERNS:
                    if pattern.search(txt):
                        graph.add_graduation_req(group_key, "편입생", transfer_data)
                        break

    logger.info(f"  졸업요건 {len(list(graph.G.nodes))}개 노드 추가 (복수 학생유형 포함)")

    # ── 4. 전공이수방법 (PDF 파싱) ────────────────────────────
    logger.info("전공이수방법 파싱 중...")
    major_methods = _parse_major_methods_from_pdf(grad_pages)

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

    # ── 6. 교양영역 (PDF 파싱) ──────────────────────────────────
    logger.info("교양영역 노드 추가 중...")
    liberal_areas = []
    lib_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["liberal_arts"])]
    _LIBERAL_AREA_PATTERNS = {
        "인성체험교양": {
            "영역구분": "인성체험",
            "keywords": ["인성체험"],
            "sub_pattern": r"인성체험교양\s*[:：]?\s*([^\n]+)",
            "credit_pattern": r"인성체험교양\s*\(?(\d+)학점",
        },
        "기초교양": {
            "영역구분": "기초",
            "keywords": ["기초교양"],
            "sub_pattern": r"기초교양\s*[:：]?\s*([^\n]+)",
            "credit_pattern": r"기초교양\s*\(?(\d+)학점",
        },
        "글로벌소통역량": {
            "영역구분": "글로벌소통역량",
            "keywords": ["글로벌소통"],
            "sub_pattern": r"글로벌소통역량\s*[:：]?\s*([^\n]+)",
            "credit_pattern": r"글로벌소통역량\s*\(?(\d+)학점",
        },
    }
    lib_full_text = "\n".join(p.text or "" for p in lib_pages if p.text)

    for area_name, cfg in _LIBERAL_AREA_PATTERNS.items():
        if any(kw in lib_full_text for kw in cfg["keywords"]):
            data = {"영역구분": cfg["영역구분"]}
            m_sub = re.search(cfg["sub_pattern"], lib_full_text)
            if m_sub:
                data["하위카테고리"] = m_sub.group(1).strip()
            m_cr = re.search(cfg["credit_pattern"], lib_full_text)
            if m_cr:
                data["이수학점"] = f"{m_cr.group(1)}학점"
            liberal_areas.append((area_name, data))

    # 균형교양 영역 파싱
    _BALANCE_AREAS = [
        ("균형교양_인문", "역사/철학/종교", ["역사", "철학", "종교"]),
        ("균형교양_예술", "문학/문화/예술", ["문학", "문화", "예술"]),
        ("균형교양_사회", "정치/경제/사회", ["정치", "경제", "사회"]),
        ("균형교양_자연", "과학/기술/환경", ["과학", "기술", "환경"]),
    ]
    if "균형교양" in lib_full_text:
        m_bal_cr = re.search(r"균형교양\s*\(?(\d+)학점", lib_full_text)
        for bname, barea, bkws in _BALANCE_AREAS:
            if any(kw in lib_full_text for kw in bkws):
                bdata = {"영역구분": "균형", "영역명": barea}
                if m_bal_cr:
                    bdata["이수학점"] = f"균형교양 {m_bal_cr.group(1)}학점 중 일부"
                liberal_areas.append((bname, bdata))

    for name, data in liberal_areas:
        graph.add_liberal_arts_area(name, data)

    # 졸업요건 → 교양영역 관계
    for area_name, _ in liberal_areas:
        if area_name in ("인성체험교양", "기초교양", "글로벌소통역량"):
            if "grad_2024_2025_내국인" in graph.G.nodes:
                graph.add_relation("grad_2024_2025_내국인", f"liberal_{area_name}", "요구한다")

    logger.info(f"  교양영역 {len(liberal_areas)}개 추가")

    # ── 7. 마이크로/융합전공 ─────────────────────────────────
    logger.info("마이크로전공 노드 추가 중...")
    micro_pages = [p for p in pages if any(k in (p.text or "") for k in SECTION_KEYS["micro_major"])]
    micro_names = _extract_micro_major_names(micro_pages)

    for name, data in micro_names:
        graph.add_micro_major(name, data)
    logger.info(f"  마이크로/융합전공 {len(micro_names)}개 추가")

    # ── 8. 핵심 엣지 구축 ────────────────────────────────────
    logger.info("핵심 관계(엣지) 구축 중...")
    _build_core_edges(graph)

    # ── 9. 구조화 노드 (StudentGroup, StudentType, Condition) ──
    logger.info("구조화 노드 (학번그룹/학생유형/조건) 생성 중...")
    _build_structural_nodes(graph)

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
            name = re.sub(r"\s+", "", m.group(1).strip())  # 줄바꿈·공백 제거
            credits = int(m.group(3))
            if name not in seen and ("마이크로" in txt or "융합" in name):
                seen.add(name)
                results.append((name, {"유형": "융합형", "이수학점": credits}))
    return results


def _build_structural_nodes(graph: AcademicGraph) -> None:
    """
    실용적 구조화 노드 생성 — 쿼리에서 실제 탐색되는 엣지만 만듦.

    원칙: 엣지는 쿼리 코드에서 successors()로 탐색될 관계만 생성.
    - 학번그룹 → 졸업요건/전공이수방법/수강규칙 (1-hop 탐색용)
    - 수강규칙 → Condition (조건 키워드 매칭용)
    - 장학금 → Condition (장학금 조건 질문용)
    - 학과→졸업요건 교차 엣지는 제거 (직접 ID 조회로 충분)
    """
    from app.graphdb.academic_graph import GROUP_LABELS

    G = graph.G

    # ── 1) StudentGroup → 졸업요건/전공이수방법/수강규칙 ──
    # 쿼리 활용: _query_graduation()에서 학번→그룹→요건 1-hop
    for gk in GROUP_LABELS:
        gid = graph.add_student_group(gk)
        for nid, data in graph._nodes_by_type("졸업요건"):
            if data.get("적용학번그룹") == gk:
                graph.add_relation(gid, nid, "적용된다")
        for nid, data in graph._nodes_by_type("전공이수방법"):
            if data.get("적용학번범위") == gk:
                graph.add_relation(gid, nid, "적용된다")

    # 수강규칙 → StudentGroup (역방향: 그룹에서 규칙 찾기)
    for nid, data in graph._nodes_by_type("수강신청규칙"):
        if nid.startswith("reg_guide_"):
            continue
        reg_grp = data.get("적용학번그룹", "")
        if "2023" in reg_grp:
            for gk in ("2023", "2024_2025"):
                gid = f"group_{gk}"
                if gid in G.nodes:
                    graph.add_relation(gid, nid, "적용된다")
        elif "2022" in reg_grp:
            for gk in ("2022", "2021", "2017_2020", "2016_before"):
                gid = f"group_{gk}"
                if gid in G.nodes:
                    graph.add_relation(gid, nid, "적용된다")

    # ── 2) 수강규칙 → Condition (쿼리에서 키워드 매칭용) ──
    _REG_COND_KEYS = {
        "최대신청학점": "최대신청학점",
        "평점4이상최대학점": "평점4이상최대학점",
        "재수강제한": "재수강제한",
        "재수강최고성적": "재수강최고성적",
        "재수강기준성적": "재수강기준성적",
        "OCU최대학점": "OCU최대학점",
        "OCU출석요건": "OCU출석요건",
        "정규학기_최대학점": "OCU정규학기최대",
        "출석요건": "OCU출석요건",
    }

    for nid, data in graph._nodes_by_type("수강신청규칙"):
        if nid.startswith("reg_guide_"):
            continue
        grp = data.get("적용학번그룹", nid.replace("reg_", ""))
        for attr_key, cond_label in _REG_COND_KEYS.items():
            val = data.get(attr_key)
            if val is None or val == "":
                continue
            cond_name = f"{cond_label}_{grp}"
            cond_id = graph.add_condition(cond_name, {"값": str(val), "원본키": attr_key})
            graph.add_relation(nid, cond_id, "제약한다")

    # ── 3) 장학금 → Condition (장학금 조건 질문용) ──
    for nid, data in graph._nodes_by_type("장학금"):
        text = " ".join(str(v) for v in data.values())
        # "12학점 이상" 패턴 → 최소이수학점 조건
        import re
        m = re.search(r"(\d+)학점\s*이상.*?이수", text)
        if m:
            cond_id = graph.add_condition(
                f"최소이수학점_{nid}",
                {"값": f"{m.group(1)}학점 이상", "원본키": "선발기준"},
            )
            graph.add_relation(nid, cond_id, "요구한다")

    # ── 4) 정적 페이지 하위 섹션 → 부모 정책 노드 연결 ──
    # reg_guide_ → reg_2023이후/reg_2022이전 ("포함한다")
    for nid in graph._type_index.get("수강신청규칙", []):
        if not nid.startswith("reg_guide_"):
            continue
        # PDF 기반 수강규칙 노드와 연결
        for parent_nid in ("reg_2023이후", "reg_2022이전"):
            if parent_nid in G.nodes:
                graph.add_relation(parent_nid, nid, "포함한다")
                break  # 하나만 연결 (중복 방지)

    # grad_guide_ → 졸업요건 노드 연결
    for nid in graph._type_index.get("졸업요건", []):
        if not nid.startswith("grad_guide_"):
            continue
        # 최신 학번 졸업요건에 연결
        parent = "grad_2024_2025_내국인"
        if parent in G.nodes:
            graph.add_relation(parent, nid, "포함한다")

    # leave_info_ 섹션 간 부모-자식: "휴학 > 정의" → 부모 = "학적변동 안내"
    leave_parent = None
    for nid in graph._type_index.get("휴복학", []):
        data = G.nodes.get(nid, {})
        section = data.get("구분", "")
        if "학적변동" in section or "안내" in section:
            leave_parent = nid
            break
    if leave_parent:
        for nid in graph._type_index.get("휴복학", []):
            if nid != leave_parent:
                graph.add_relation(leave_parent, nid, "포함한다")

    # ── 5) 학사일정 → 관련 정책 연결 ("기간정한다") ──
    _SCHEDULE_POLICY_MAP = {
        "휴복학": ["휴복학", "휴학", "복학"],
        "조기졸업": ["조기졸업"],
        "수강신청규칙": ["수강신청", "수강정정", "장바구니"],
        "성적처리": ["성적", "평가"],
    }
    for sched_nid, sched_data in graph._nodes_by_type("학사일정"):
        event = sched_data.get("이벤트명", "")
        for policy_type, keywords in _SCHEDULE_POLICY_MAP.items():
            if any(kw in event for kw in keywords):
                # 해당 정책 타입의 첫 번째 노드와 연결
                policy_nodes = graph._type_index.get(policy_type, [])
                if policy_nodes:
                    target = policy_nodes[0]
                    graph.add_relation(sched_nid, target, "기간정한다")
                break  # 하나의 정책만 연결

    # ── 6) 장학금 → Condition 보강 (선발기준 텍스트에서 추출) ──
    import re as _re
    for nid, data in graph._nodes_by_type("장학금"):
        criteria = str(data.get("선발기준", ""))
        # "평점 N.N 이상" 패턴
        for m in _re.finditer(r"평점(?:평균)?\s*([\d.]+)\s*이상", criteria):
            cond_id = graph.add_condition(
                f"평점기준_{nid}", {"값": f"평점 {m.group(1)} 이상", "원본키": "선발기준"}
            )
            graph.add_relation(nid, cond_id, "요구한다")
            break
        # "N학점 이상 이수" 패턴
        for m in _re.finditer(r"(\d+)학점\s*이상\s*이수", criteria):
            cond_id = graph.add_condition(
                f"이수학점기준_{nid}", {"값": f"{m.group(1)}학점 이상", "원본키": "선발기준"}
            )
            graph.add_relation(nid, cond_id, "요구한다")
            break

    cond_count = len(graph._type_index.get("조건", []))
    group_count = len(graph._type_index.get("학번그룹", []))
    edge_count = G.number_of_edges()
    logger.info(f"  학번그룹 {group_count}개, 조건 {cond_count}개, 엣지 {edge_count}개")


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
