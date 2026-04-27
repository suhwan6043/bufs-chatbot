"""
되묻기(Clarification) 게이트.

목적: 답변에 필요한 필수 필드(학번·학과·학생유형 등)가 없을 때, 잘못된 답을
생성하기 전에 사용자에게 되묻는다. 세션당 필드당 1회로 제한(안전장치)하고,
이미 물었던 필드는 soft 모드로 전환(경고 문구 + 일반 답변).

설계 원칙:
- 팀원 작성 KO 경로 코드(chat.py 프로필 주입 블록)는 건드리지 않는다.
- 이 모듈이 `missing_info`를 독립적으로 재계산하므로 query_analyzer 수정 불필요.
- KO/EN 문구를 한 파일에서 관리하여 충돌 최소화.

환경변수:
- CLARIFICATION_ENABLED=true   (기본값, false면 기능 전체 off — 긴급 롤백용)
- CLARIFICATION_MAX_LOG=5      (세션당 저장 최대 필드 수 — 서킷 브레이커)
"""
from __future__ import annotations

import os
import re
import time
from typing import Optional

from app.models import Intent, QueryAnalysis


# ── 안전장치 ────────────────────────────────────────────────────────────────
# 2026-04-24 내부 테스트 직전: 미검증 feature라 기본 OFF. 검증 후 "true"로 변경 예정.
# 긴급 활성화: export CLARIFICATION_ENABLED=true
ENABLED: bool = os.getenv("CLARIFICATION_ENABLED", "false").lower() == "true"
MAX_LOG_ENTRIES: int = int(os.getenv("CLARIFICATION_MAX_LOG", "5"))


# ── 필수 필드 매트릭스 ──────────────────────────────────────────────────────
# 키: Intent — 값: 쿼리 정확한 답변을 위해 필요한 필드 집합.
# 기본값 "내국인"만 있는 student_type은 트리거하지 않는다(명시적으로 외국인/편입이면
# 다른 경로로 감지됨).
REQUIRED_FIELDS: dict[Intent, set[str]] = {
    Intent.GRADUATION_REQ:    {"student_id", "department"},
    Intent.EARLY_GRADUATION:  {"student_id"},
    Intent.MAJOR_CHANGE:      {"student_id"},
    Intent.REGISTRATION:      {"student_id"},   # 학점 한도·학년 계산에 필요
    Intent.TRANSCRIPT:        {"transcript"},   # 성적표 업로드 필요
    # SCHEDULE/COURSE_INFO/ALTERNATIVE/SCHOLARSHIP/LEAVE_OF_ABSENCE/GENERAL는 일반 답변 가능
}


# ── 문구 템플릿 (KO/EN) ────────────────────────────────────────────────────
# 키: (Intent, "ko" | "en", frozenset[필드]) — 상세 매칭이 있으면 이것 사용.
# 부재 시 `_FALLBACK_TEMPLATES`의 필드별 일반 문구로 fallback.
CLARIFICATION_TEMPLATES: dict[tuple[Intent, str, frozenset], str] = {
    # 졸업요건 — 학번+학과
    (Intent.GRADUATION_REQ, "ko", frozenset({"student_id", "department"})):
        "졸업요건은 학번과 학과에 따라 다릅니다. "
        "학번(예: 2024)과 소속 학과(예: 소프트웨어학부)를 알려주세요.",
    (Intent.GRADUATION_REQ, "en", frozenset({"student_id", "department"})):
        "Graduation requirements vary by admission year and department. "
        "Please tell me your admission year (e.g., 2024) and your department (e.g., Software).",
    # 졸업요건 — 학번만
    (Intent.GRADUATION_REQ, "ko", frozenset({"student_id"})):
        "졸업요건은 학번에 따라 다릅니다. 학번(예: 2024)을 알려주세요.",
    (Intent.GRADUATION_REQ, "en", frozenset({"student_id"})):
        "Graduation requirements vary by admission year. "
        "Please tell me your admission year (e.g., 2024).",
    # 졸업요건 — 학과만
    (Intent.GRADUATION_REQ, "ko", frozenset({"department"})):
        "졸업요건은 학과에 따라 다릅니다. 소속 학과를 알려주세요.",
    (Intent.GRADUATION_REQ, "en", frozenset({"department"})):
        "Graduation requirements vary by department. Please tell me your department.",
    # 조기졸업 — 학번
    (Intent.EARLY_GRADUATION, "ko", frozenset({"student_id"})):
        "조기졸업 평점 기준은 학번에 따라 다릅니다(2005년 이전 / 2006년 / 2007년 이후). "
        "학번을 알려주세요.",
    (Intent.EARLY_GRADUATION, "en", frozenset({"student_id"})):
        "Early graduation GPA requirements differ by admission year "
        "(before 2005 / 2006 / 2007 onwards). Please tell me your admission year.",
    # 전공 변경 — 학번
    (Intent.MAJOR_CHANGE, "ko", frozenset({"student_id"})):
        "전공 변경·복수전공 요건은 학번에 따라 다릅니다. 학번을 알려주세요.",
    (Intent.MAJOR_CHANGE, "en", frozenset({"student_id"})):
        "Major change and double major requirements vary by admission year. "
        "Please tell me your admission year.",
    # 수강신청 — 학번
    (Intent.REGISTRATION, "ko", frozenset({"student_id"})):
        "수강신청 최대 학점·신청 기간은 학번과 학년에 따라 다릅니다. 학번을 알려주세요.",
    (Intent.REGISTRATION, "en", frozenset({"student_id"})):
        "Course registration limits and schedules vary by admission year and grade level. "
        "Please tell me your admission year.",
    # 성적표 — 업로드 필요
    (Intent.TRANSCRIPT, "ko", frozenset({"transcript"})):
        "성적 관련 답변은 성적표가 필요합니다. "
        "오른쪽 상단 '성적표 업로드'에서 학업성적사정표 파일을 올려주세요.",
    (Intent.TRANSCRIPT, "en", frozenset({"transcript"})):
        "For grade-related questions, please upload your academic transcript "
        "using the 'Upload Transcript' option in the top-right menu.",
}

_FALLBACK_TEMPLATES: dict[tuple[str, str], str] = {
    ("student_id", "ko"): "정확한 답변을 위해 학번을 알려주세요.",
    ("student_id", "en"): "Please tell me your admission year for an accurate answer.",
    ("department", "ko"): "정확한 답변을 위해 소속 학과를 알려주세요.",
    ("department", "en"): "Please tell me your department for an accurate answer.",
    ("student_type", "ko"): "본인이 내국인/외국인/편입생 중 어느 쪽인지 알려주세요.",
    ("student_type", "en"): "Please tell me whether you are a domestic, international, or transfer student.",
    ("transcript", "ko"): "정확한 답변을 위해 성적표를 업로드해주세요.",
    ("transcript", "en"): "Please upload your transcript for an accurate answer.",
}


# ── Soft 경고 문구 (이미 물었는데 답변 안 준 경우) ──────────────────────────
SOFT_WARNING_TEMPLATES: dict[tuple[str, str], str] = {
    ("student_id", "ko"): "※ 학번 정보가 없어 일반 기준으로 답변드립니다. 정확한 답변을 위해 학번을 알려주세요.",
    ("student_id", "en"): "※ Without your admission year, this is a general answer. Please provide your year for more accurate information.",
    ("department", "ko"): "※ 학과 정보가 없어 일반 기준으로 답변드립니다.",
    ("department", "en"): "※ Without your department, this is a general answer.",
    ("student_type", "ko"): "※ 학생 유형 정보가 없어 내국인 기준으로 답변드립니다.",
    ("student_type", "en"): "※ Without your student type, this is a domestic student answer.",
    ("transcript", "ko"): "※ 성적표 업로드가 없어 일반 안내만 드립니다.",
    ("transcript", "en"): "※ Without your transcript, I can only provide general information.",
}


# ── 공개 API ───────────────────────────────────────────────────────────────
def check_required_fields(
    analysis: QueryAnalysis,
    profile: Optional[dict],
    clarification_log: Optional[dict],
    transcript_present: bool = False,
) -> list[str]:
    """
    현재 intent에서 필요한 필드 중 누락된 것(이미 물었던 건 제외) 리스트 반환.

    Args:
        analysis: QueryAnalysis 객체 (intent, student_id, student_type, entities 포함)
        profile: 세션의 user_profile (None 가능)
        clarification_log: {field_name: timestamp} — 이미 물었던 필드
        transcript_present: 세션에 성적표 데이터 유무

    Returns:
        누락 필드 리스트. 빈 리스트면 clarification 불필요.
    """
    if not ENABLED:
        return []

    required = REQUIRED_FIELDS.get(analysis.intent, set())
    if not required:
        return []

    log = clarification_log or {}
    profile = profile or {}

    missing: list[str] = []
    for field in required:
        if field in log:
            # 이미 물었음 → soft 모드로 전환 대상
            continue
        if _field_present(field, analysis, profile, transcript_present):
            continue
        missing.append(field)
    return missing


def get_already_asked_missing(
    analysis: QueryAnalysis,
    profile: Optional[dict],
    clarification_log: Optional[dict],
    transcript_present: bool = False,
) -> list[str]:
    """이미 물었는데 여전히 미제공인 필드 — soft 경고 주입용."""
    if not ENABLED:
        return []
    required = REQUIRED_FIELDS.get(analysis.intent, set())
    if not required:
        return []
    log = clarification_log or {}
    profile = profile or {}
    already = []
    for field in required:
        if field not in log:
            continue
        if not _field_present(field, analysis, profile, transcript_present):
            already.append(field)
    return already


def build_clarification_message(
    intent: Intent, lang: str, missing: list[str],
) -> str:
    """단락 회로용 clarification 메시지 조립."""
    lang = lang if lang in ("ko", "en") else "ko"
    key = (intent, lang, frozenset(missing))
    if key in CLARIFICATION_TEMPLATES:
        return CLARIFICATION_TEMPLATES[key]
    # fallback: 필드별 일반 문구 조합
    parts = [_FALLBACK_TEMPLATES.get((f, lang), "") for f in missing]
    return " ".join(p for p in parts if p).strip()


def build_soft_warning(fields: list[str], lang: str) -> str:
    """일반 답변에 prepend할 경고 문구."""
    lang = lang if lang in ("ko", "en") else "ko"
    parts = [SOFT_WARNING_TEMPLATES.get((f, lang), "") for f in fields]
    return "\n".join(p for p in parts if p).strip()


def update_log(
    clarification_log: Optional[dict], fields: list[str],
) -> dict:
    """필드 기록 갱신 + 서킷 브레이커 적용."""
    log = dict(clarification_log or {})
    now = time.time()
    for f in fields:
        log[f] = now
    # 서킷 브레이커: 오래된 항목부터 제거하여 MAX_LOG_ENTRIES 이내로 유지
    if len(log) > MAX_LOG_ENTRIES:
        sorted_items = sorted(log.items(), key=lambda x: x[1])
        log = dict(sorted_items[-MAX_LOG_ENTRIES:])
    return log


# ── 후속 턴 — 사용자 응답에서 필드 추출 ─────────────────────────────────────
_STUDENT_ID_PATTERNS = [
    # KO: 한글 뒤 \b는 비정상 동작 → 후행 \b 제거, 대신 숫자 선행은 \b 유지
    re.compile(r"(?<!\d)(20\d{2})\s*학번"),                       # "2024학번"
    re.compile(r"학번\s*(20\d{2})(?!\d)"),                         # "학번 2024"
    re.compile(r"(?<!\d)(20\d{2})\s*(?:년)?\s*(?:입학|입학자|들어왔)"),
    re.compile(r"\bclass\s+of\s+(20\d{2})\b", re.IGNORECASE),     # EN
    re.compile(r"\bcohort\s+of\s+(20\d{2})\b", re.IGNORECASE),
    re.compile(r"\badmitted\s+in\s+(20\d{2})\b", re.IGNORECASE),
    re.compile(r"^\s*(20\d{2})\s*\.?\s*$"),                        # 단독 4자리
    re.compile(r"(?<!\d)(\d{2})\s*학번"),                          # 22학번 → 2022
]

_STUDENT_TYPE_PATTERNS_KO = {
    "외국인": re.compile(r"외국인|유학생"),
    "편입생": re.compile(r"편입생?|편입학"),
    "내국인": re.compile(r"내국인|한국인"),
}
_STUDENT_TYPE_PATTERNS_EN = {
    "외국인": re.compile(r"international|foreign|exchange", re.IGNORECASE),
    "편입생": re.compile(r"transfer", re.IGNORECASE),
    "내국인": re.compile(r"domestic|korean", re.IGNORECASE),
}


def detect_clarification_reply(
    text: str, last_asked: list[str], lang: str = "ko",
) -> dict:
    """
    이전 턴이 clarification이었을 때, 현 턴 응답에서 필드 추출.

    Returns:
        {"student_id": "2024", ...} — 추출된 필드만 포함. 빈 dict면 실패.
    """
    if not last_asked:
        return {}

    extracted: dict = {}

    if "student_id" in last_asked:
        for pat in _STUDENT_ID_PATTERNS:
            m = pat.search(text)
            if m:
                year = m.group(1)
                if len(year) == 2:
                    # 2자리 학번 변환 (20 이상이면 20YY, 미만이면 유지)
                    year = f"20{year}" if int(year) >= 5 else f"200{year}"
                extracted["student_id"] = year
                break

    if "student_type" in last_asked:
        patterns = _STUDENT_TYPE_PATTERNS_EN if lang == "en" else _STUDENT_TYPE_PATTERNS_KO
        for stype, pat in patterns.items():
            if pat.search(text):
                extracted["student_type"] = stype
                break

    if "department" in last_asked:
        # 학과는 자유 입력 — 간단한 휴리스틱으로 "~학부/~학과" 패턴 추출
        m = re.search(r"([가-힣A-Za-z]+(?:학부|학과|전공|department|dept))", text)
        if m:
            extracted["department"] = m.group(1).strip()
        else:
            # 폴백: "소속: 영어영문학" 같은 패턴
            m2 = re.search(r"(?:소속|학과|전공|department)\s*[:：]\s*([^\s,]+)", text, re.IGNORECASE)
            if m2:
                extracted["department"] = m2.group(1).strip()

    return extracted


# ── 내부 헬퍼 ───────────────────────────────────────────────────────────────
def _field_present(
    field: str, analysis: QueryAnalysis, profile: dict, transcript_present: bool,
) -> bool:
    """필드가 analysis / profile / transcript 중 어디에라도 있으면 True."""
    if field == "student_id":
        return bool(analysis.student_id) or bool(profile.get("student_id"))
    if field == "department":
        return bool(analysis.entities.get("department")) or bool(profile.get("department"))
    if field == "student_type":
        st = analysis.student_type or profile.get("student_type")
        # "내국인" 기본값은 명시적으로 질문한 게 아닐 수 있어 인정(트리거 안 함)
        return bool(st)
    if field == "transcript":
        return transcript_present
    return False
