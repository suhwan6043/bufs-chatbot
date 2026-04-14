"""Answer Unit — 질문·답변의 '의미 단위'를 다루는 공통 유틸.

원칙:
- 병목 A/C/D가 모두 "질문이 요구하는 단위 vs 실제 제공된 단위"의 불일치 문제.
- 이를 개별 예외 분기로 처리하면 관리 불가능. 단일 개념 AnswerUnit로 통합한다.

구성:
1. `expected_units(question)`  — 질문에서 기대 단위 집합 추출
2. `present_units(text)`        — 텍스트에 실존하는 단위 + 추출된 값 쌍
3. `aligns(question, answer)`   — 답변이 질문의 기대 단위를 충족하는가
4. `missing(question, answer, context)` — 답변에서 빠졌으나 컨텍스트에 있는 단위

단위 종류: credit (학점), won (원), course (과목), date, time, location, url, grade

이 유틸은 병목 A·C·D 세 곳에서 재사용된다:
- A: ContextMerger가 graph-sourced direct_answer를 수락하기 전 `aligns()` 호출
- C: _try_extract_direct_answer가 department+location/phone 요청 시 컨텍스트에서
     `present_units(line)`로 해당 행의 전화/호실만 추출
- D: AnswerGenerator.generate_full이 답변 생성 후 `missing()`로 누락 단위를
     컨텍스트에서 찾아 주입
"""

from __future__ import annotations

import re
from typing import Optional


# ── 단위 패턴 (extract) ─────────────────────────────────────────────
# 각 단위는 정규식 하나로 텍스트에서 값을 찾아낸다.

_PATTERNS = {
    "credit":   re.compile(r"(\d{1,3})\s*학점"),
    "won":      re.compile(r"(\d{1,3}(?:,\d{3})*)\s*원"),
    "course":   re.compile(r"(\d+)\s*과목"),
    # 날짜: '2026년 3월 2일' / '3월 2일' / '2.9' 형태만. '일'이 있거나 한국어 월 필수.
    # 숫자만 나열('51-50')이 오탐되지 않도록 '월'/'년'/'일' 앵커 요구.
    "date":     re.compile(
        r"(?:\d{4}\s*년\s*)?\d{1,2}\s*월\s*\d{1,2}\s*일"
        r"|\d{4}\s*년\s*\d{1,2}\s*월"
        r"|(?<!\d)\d{1,2}\.\d{1,2}\.?(?!\d)"
    ),
    "time":     re.compile(r"\d{1,2}:\d{2}|\d{1,2}\s*시(?:\s*\d{1,2}\s*분)?"),
    # 전화: '051-509-XXXX' 풀 포맷 또는 target_entity 라인 내 '(\d{4})' 내선.
    # target_entity가 없을 때 오탐을 막기 위해 기본 패턴은 풀 포맷만 요구.
    # 내선 단독 추출은 `extract_phone_in_line` 헬퍼에서 수행 (Fix C용).
    "phone":    re.compile(r"051-?509-?(\d{4})"),
    "room":     re.compile(r"\b([A-Z]\d{3}(?:-\d+)?)\b"),
    "url":      re.compile(r"https?://[^\s)\]가-힣]+"),
    "grade":    re.compile(r"\b([A-Da-d][+]?)\b\s*(?:이하|이상|등급|학점)?"),
}

# 단위 트리거 — 키워드 → 기대 단위 집합. 모두 동등 가중치로 union.
# "얼마"는 ambiguous하므로 별도 처리: strong 단위가 있을 때만 의미를 결정.
_UNIT_TRIGGERS: list[tuple[tuple[str, ...], set[str]]] = [
    (("학점",), {"credit"}),
    (("몇 과목", "몇과목"), {"course"}),
    (("전화", "번호", "연락처"), {"phone"}),
    (("사이트", "주소", "홈페이지", "url", "URL", "포털"), {"url"}),
    (("사무실", "호실", "건물", "어느 동"), {"room"}),
    (("장소", "위치"), {"room", "url"}),
    (("언제", "며칠", "날짜", "기간"), {"date", "time"}),
    (("몇 시", "몇시"), {"time"}),
    (("성적", "등급"), {"grade"}),
    # 금액 전용 명시 키워드 — "얼마"와 독립적으로 매칭
    (("수강료", "수수료", "비용", "금액", "납부"), {"won"}),
    # 방법/링크 질문 (method 류) — URL은 종종 필수 팩트
    (("어떻게", "어떻게 해", "방법", "신청은"), {"url"}),
    (("어디",), {"room", "url"}),
]

# ambiguous 단독 키워드 — 다른 힌트가 없을 때만 won으로 해석
_AMBIGUOUS_TO_WON = ("얼마",)


def expected_units(question: str) -> set[str]:
    """질문이 요구하는 답변 단위 집합을 반환.

    규칙:
    1) 모든 트리거를 evaluate해서 union. (학점 + 수강료 둘 다 있는 질문은 둘 다 기대)
    2) "얼마" 같은 ambiguous 키워드는 strong 단위가 하나도 없을 때만 won으로 해석.
       학점/원/과목/날짜 등이 명시돼 있으면 "얼마"는 그 단위의 수량 의미로 흡수.
    """
    if not question:
        return set()
    q = question.lower()

    units: set[str] = set()
    for keywords, unit_set in _UNIT_TRIGGERS:
        if any(kw in q for kw in keywords):
            units.update(unit_set)

    # 모든 구체 단위가 하나도 매칭 안 되면 "얼마"를 won으로 fallback 해석
    if not units and any(kw in q for kw in _AMBIGUOUS_TO_WON):
        units.add("won")

    return units


def present_units(text: str) -> dict[str, list[str]]:
    """텍스트에서 각 단위의 실제 발견값을 반환.

    예) "18학점이다. 단 4학년은 9학점" → {"credit": ["18", "9"]}
        "http://sugang.bufs.ac.kr"     → {"url": ["http://sugang.bufs.ac.kr"]}

    매칭 없는 단위는 dict에 포함되지 않는다.
    반환되는 값은 패턴에 따라 "값" (e.g. "18") 또는 "full match" 형태.
    """
    if not text:
        return {}
    result: dict[str, list[str]] = {}
    for unit, pattern in _PATTERNS.items():
        cleaned: list[str] = []
        for m in pattern.finditer(text):
            # group이 있으면 그룹 중 첫 번째 non-empty, 없으면 전체 매칭
            if m.groups():
                val = next((g for g in m.groups() if g), m.group(0))
            else:
                val = m.group(0)
            val = val.strip()
            if val:
                cleaned.append(val)
        if cleaned:
            result[unit] = cleaned
    return result


# "대체 불가" 필수 단위 — 질문의 기대에 포함되면 답변에도 반드시 있어야 한다.
# 근거: 이들은 질문의 핵심 정보이며, 다른 단위로 대체 설명이 불가능한 "고유 값".
#   - won   : 금액은 문장으로 풀어 써도 오답 (예: "가능합니다" ≠ "120,000원")
#   - url   : 링크는 다른 표현으로 대체 불가
#   - phone : 전화번호는 숫자 자체
#   - room  : 호실 번호는 숫자 자체
#   - grade : 성적 등급
_REQUIRED_WHEN_EXPECTED = frozenset({"won", "url", "phone", "room", "grade"})


# ── Keyword Anchor Gate (버그 #1 해결) ──────────────────────────────
#
# 단위 정합만으론 부족하다. "2025 전기 학위수여식" vs "2025 후기 학위수여식"은
# 둘 다 date 단위를 갖지만 의미가 정반대. 질문의 '구별자(discriminator)'가
# 답변과 어긋나면 의미 불일치로 간주한다.
#
# 구별자는 categorical / exclusive 어휘 — 한 카테고리 안에서 여러 값 중 하나만
# 참이 되는 것:
#   - 연도:  2025, 2026, 2027
#   - 학기:  전기/후기, 상반기/하반기, 1~4학기
#   - 학번:  2016학번, ..., 2026학번
#   - 이분법: 제한/가능, 자격/기간

_DISCRIMINATOR_CATEGORIES: list[tuple[str, re.Pattern, str]] = [
    # (category_name, pattern, policy)
    # policy:
    #   "exclusive_strict" — 답변의 값은 질문 값의 **부분집합**이어야 통과.
    #                         답변에 질문 밖의 값이 있으면 거부.
    #                         year_4, cohort 등 "연도/학번 정확 일치" 필수 카테고리.
    #   "exclusive"        — 답변에 해당 카테고리 값이 있고, 질문과 교집합 **없으면** 거부.
    #                         전기/후기 같은 이분법에서 답변이 둘 다 언급 가능.
    #   "intent"           — 질문 의도 명사 vs 답변 주제. 교차 충돌 감지.
    #
    # 주의: year_4는 cohort("20XX학번")와 충돌할 수 있으므로 negative lookahead로 제외
    ("cohort",        re.compile(r"20\d{2}\s*학번"),        "exclusive_strict"),
    ("year_4",        re.compile(r"(?<!\d)20\d{2}(?!\s*학번)"), "exclusive_strict"),
    ("semester_half", re.compile(r"(전기|후기|상반기|하반기)"),   "exclusive"),
    ("semester_ord",  re.compile(r"([1-4])\s*학기"),           "exclusive"),
    ("permission",    re.compile(r"(제한|불가|금지|가능|허용)"),  "exclusive"),
    # 의도 카테고리 — 질문의 의도 명사(자격/기간/자격/신청)와 답변의 주제가 충돌 감지.
    ("intent_qualification", re.compile(r"(자격|요건|기준)"), "intent"),
    ("intent_period",        re.compile(r"(기간|언제|날짜|일정)"), "intent"),
    ("intent_action",        re.compile(r"(신청방법|취소방법|등록방법|제출방법|발급방법|어떻게)"), "intent"),
]


def _category_values(text: str) -> dict[str, tuple[set[str], str]]:
    """텍스트에서 각 구별자 카테고리의 발견 값 + 정책을 반환.

    Returns: {category_name: ({value1, ...}, policy)}
    """
    if not text:
        return {}
    out: dict[str, tuple[set[str], str]] = {}
    for name, pat, policy in _DISCRIMINATOR_CATEGORIES:
        found = {m.group(0).strip() for m in pat.finditer(text) if m.group(0).strip()}
        if found:
            out[name] = (found, policy)
    return out


def _discriminator_mismatch(question: str, answer: str) -> Optional[str]:
    """질문의 구별자와 답변의 구별자가 어긋나는 카테고리를 반환.

    정책별 검증:
    - **exclusive**: 답변에 이 카테고리 값이 있고, 질문과 교집합 없으면 거부.
      (질문에 "2027"이 있고 답변에 "2026"이 있으면 거부)
    - **intent**: 질문의 intent 카테고리 vs 답변의 intent 카테고리.
      답변에 **다른 intent 카테고리 값**이 있는데 질문의 intent는 없으면 거부.
      예: 질문 "자격 요건?" + 답변 "...기간 2026년..." → `intent_qualification` 없고
          `intent_period` 있음 → 거부. 반면 답변이 "3.7 이상"처럼 intent 카테고리
          어휘가 전혀 없으면 통과 (자연스러운 수치 답변 허용).
    """
    q_cats = _category_values(question)
    if not q_cats:
        return None
    a_cats = _category_values(answer)

    # exclusive / exclusive_strict 먼저 처리
    for cat, (q_vals, policy) in q_cats.items():
        if policy not in ("exclusive", "exclusive_strict"):
            continue
        a_entry = a_cats.get(cat)
        if not a_entry:
            continue  # 답변에 해당 카테고리 없음 → 허용
        a_vals = a_entry[0]
        if policy == "exclusive_strict":
            # 답변 값이 질문 값의 부분집합이어야 함 (답변에 extra 값 있으면 거부)
            extra = a_vals - q_vals
            if extra:
                return (
                    f"{cat} [exclusive_strict]: q={sorted(q_vals)} "
                    f"vs a_extra={sorted(extra)}"
                )
        else:  # "exclusive"
            if q_vals & a_vals:
                continue  # 교집합 있음 → OK
            return f"{cat} [exclusive]: q={sorted(q_vals)} vs a={sorted(a_vals)}"

    # intent 카테고리: 질문 intent vs 답변 intent 교차 검증
    # 질문 intent 카테고리들 추출
    q_intent_cats = {
        name for name, (_, pol) in q_cats.items() if pol == "intent"
    }
    if q_intent_cats:
        # 답변 intent 카테고리들 추출
        a_intent_cats = {
            name for name, (_, pol) in a_cats.items() if pol == "intent"
        }
        if a_intent_cats and not (q_intent_cats & a_intent_cats):
            # 답변이 **다른** intent 카테고리의 어휘만 가짐 → 주제 교차 오답
            return (
                f"intent-cross: q_intent={sorted(q_intent_cats)} "
                f"vs a_intent={sorted(a_intent_cats)}"
            )

    return None


def aligns(question: str, answer: str) -> bool:
    """answer가 question의 기대 단위 + 구별자와 정합하는지 검증.

    사용처: direct_answer 수락 여부 gate (Fix A) + rule 결과 검증 (Fix #3).

    판정 규칙:
    0) **Keyword Anchor Gate (신규, 버그 #1)**: 질문의 구별자 값(연도/학기/학번/이분법)이
       답변과 어긋나면 즉시 False. "2025 전기" vs "2025 후기" 교차 오답 차단.
    1) 기대 단위 집합이 비면 True (검증 대상 아님, 통과)
    2) 기대 단위 중 "대체 불가" 단위(_REQUIRED_WHEN_EXPECTED)는 모두 답변에 실존해야 함
    3) 나머지 단위(credit/course/date/time)는 하나 이상 실존하면 통과
    4) 답변에 아무 단위도 없으면 False
    """
    # 0) Keyword Anchor Gate
    if _discriminator_mismatch(question, answer):
        return False

    expected = expected_units(question)
    if not expected:
        return True  # 단위 요구 없음 → 보수적으로 통과
    present_keys = set(present_units(answer).keys())
    if not present_keys:
        return False  # 답변에 아무 단위도 없음 → 거부

    # 1) 필수 단위: 기대에 포함된 필수 단위는 모두 있어야 한다
    required = expected & _REQUIRED_WHEN_EXPECTED
    if required and not required.issubset(present_keys):
        return False

    # 2) 선택 단위 중에서도 최소 1개는 있어야 함 (필수 단위만으로는 부족할 때 체크)
    optional_expected = expected - _REQUIRED_WHEN_EXPECTED
    if optional_expected and not (optional_expected & present_keys):
        if not required:
            return False

    return True


def missing_units(question: str, answer: str) -> set[str]:
    """question의 기대 단위 중 answer에 없는 것 반환.

    사용처: 생성 답변에서 누락된 팩트 감지 (Fix D).
    """
    expected = expected_units(question)
    if not expected:
        return set()
    present = set(present_units(answer).keys())
    return expected - present


_EXT_PHONE_IN_LINE = re.compile(r"(?<!\d)(\d{4})(?!\d)")


def _extract_phone_in_entity_line(context: str, target_entity: str) -> Optional[str]:
    """target_entity가 포함된 라인에서만 4자리 내선을 추출.

    전화번호 패턴은 오탐이 쉬워서 일반 `present_units`에 넣을 수 없지만,
    학과 행이라는 **컨텍스트 제약**이 있으면 안전하게 내선 추출이 가능하다.
    """
    for line in context.split("\n"):
        if target_entity not in line:
            continue
        # 풀 포맷 우선
        m_full = re.search(r"051-?509-?(\d{4})", line)
        if m_full:
            return m_full.group(1)
        # 내선 fallback: 4자리 수치 (학과 행에서만)
        m_short = _EXT_PHONE_IN_LINE.search(line)
        if m_short:
            return m_short.group(1)
    return None


def fill_from_context(
    question: str,
    answer: str,
    context: str,
    *,
    target_entity: Optional[str] = None,
) -> str:
    """answer에서 누락된 기대 단위를 context에서 찾아 문장 끝에 주입.

    원칙:
    - answer 본문 앞부분은 LLM 생성 그대로 유지 (덮어쓰지 않음).
    - 누락 단위만 컨텍스트에서 추출해 "[참고]" 블록으로 **추가**.
    - target_entity(예: 학과명)가 주어지면 해당 엔티티가 포함된 라인에서만 추출
      → 다른 학과의 전화번호가 오염되지 않는다.

    사용처: Fix C (학과 행), Fix D (URL/날짜 일반 주입)
    """
    missing = missing_units(question, answer)
    if not missing or not context:
        return answer

    # target_entity가 있으면 해당 라인만 사용. 매칭 라인이 없으면 주입을 포기한다.
    # (전체 context로 폴백하면 다른 학과의 번호가 오염될 수 있다 — q060 회귀 방어)
    search_pool = context
    if target_entity:
        matching_lines = [ln for ln in context.split("\n") if target_entity in ln]
        if not matching_lines:
            return answer  # 해당 엔티티 라인이 없으면 주입 없음 (잘못된 값 오염 방지)
        search_pool = "\n".join(matching_lines)

    found = present_units(search_pool)
    parts: list[str] = []

    # phone: 일반 패턴 → target_entity 라인에서 내선 fallback
    phone_val: Optional[str] = None
    if "phone" in missing:
        if found.get("phone"):
            phone_val = found["phone"][0]
        elif target_entity:
            phone_val = _extract_phone_in_entity_line(context, target_entity)
        if phone_val:
            parts.append(f"전화: 051-509-{phone_val}")

    if "url" in missing and found.get("url"):
        parts.append(f"사이트: {found['url'][0]}")
    if "room" in missing and found.get("room"):
        parts.append(f"사무실: {found['room'][0]}")
    if "date" in missing and found.get("date"):
        parts.append(f"날짜: {found['date'][0]}")
    if "time" in missing and found.get("time"):
        parts.append(f"시간: {found['time'][0]}")
    if "credit" in missing and found.get("credit"):
        parts.append(f"학점: {found['credit'][0]}학점")
    if "won" in missing and found.get("won"):
        parts.append(f"금액: {found['won'][0]}원")

    if not parts:
        return answer

    return answer.rstrip() + "\n\n[참고] " + " / ".join(parts)


# ── Answer-Context Consistency Verifier (버그 #7 해결) ──────────────
#
# LLM이 컨텍스트에 없는 근접 값을 합성하는 환각 패턴을 감지한다.
# 예시:
#   - c01: context="m.bufs.ac.kr", answer="sugang.bufs.ac.kr" → mismatch
#   - sc02: context="12학점(4학년 9학점)", answer="15학점" → mismatch
#   - l02: context="4회(4년)", answer="4학기" → 단위 혼동
#
# 검증 원칙:
#   답변에서 추출된 각 값이 context에도 문자열로 존재해야 한다.
#   존재하지 않으면 "LLM이 지어낸 값"으로 간주.
#
# 대상 단위: 숫자·URL 기반(won/credit/course/grade/url). credit은 숫자+학점으로
# 재구성하여 정확 일치 검증. 전화/호실/날짜/시간은 false positive 우려로 제외
# (같은 값이지만 다른 위치의 실존 가능성).

_CONSISTENCY_CHECK_UNITS = frozenset({"won", "url", "grade"})


def verify_answer_against_context(answer: str, context: str) -> tuple[bool, Optional[str]]:
    """답변의 핵심 값(금액/URL/성적)이 컨텍스트에 실제로 존재하는지 검증.

    사용처: `AnswerGenerator.generate_full()` 직후 환각 감지.

    반환:
        (True, None)  — 답변의 모든 검증 대상 단위가 컨텍스트에 존재
        (False, reason) — 불일치 발견. reason은 "{unit}:{value} not in context"

    주의:
    - credit/course: "12학점"이 context에 있어야 답변 "12학점"도 유효. 하지만
      "12"라는 숫자만 있는 경우도 허용 (표 셀 단위 기준).
    - won: "120,000원" 또는 "120000" 형태 모두 인정.
    - url: 완전 일치 필수.
    - date/time/phone/room: 검증 대상 아님 (컨텍스트에서 위치가 유동적).
    """
    if not answer or not context:
        return True, None

    a_units = present_units(answer)
    if not a_units:
        return True, None

    # URL 검증
    for url in a_units.get("url", []):
        if url not in context:
            return False, f"url:{url} not in context"

    # 금액(won) 검증 — 쉼표 유무 모두 시도
    for won_val in a_units.get("won", []):
        candidates = {won_val, won_val.replace(",", ""), f"{won_val}원"}
        if not any(c in context for c in candidates):
            return False, f"won:{won_val} not in context"

    # 성적(grade) 검증
    for grade_val in a_units.get("grade", []):
        if grade_val not in context:
            return False, f"grade:{grade_val} not in context"

    # Phase 3+ (2026-04-12): 학점(credit) 검증.
    # u08 "대학원 박사과정 3학점" 같은 환각을 방지.
    # 허용 형태: "12학점", "12 학점", "12" (숫자만), "12학점 이상" 등.
    # 졸업요건·이수학점은 표·조건문에 다양한 형태로 존재하므로 관대하게 검증.
    ctx_stripped = context.replace(" ", "")  # 공백 제거 버전도 함께 검사
    for credit_val in a_units.get("credit", []):
        in_normal = (f"{credit_val}학점" in context or credit_val in context)
        in_stripped = (f"{credit_val}학점" in ctx_stripped or credit_val in ctx_stripped)
        if not in_normal and not in_stripped:
            return False, f"credit:{credit_val} not in context"

    return True, None


# ── Phase 3 Step 3 (2026-04-12): 답변 완전성 가드 ────────────────
# g04 "복수전공 최소 이수학점" 같이 질문이 여러 값(주전공/제2전공)을 기대하는
# bi-value 케이스에서 LLM이 한 값만 답변하는 문제를 방지.
#
# 설계:
# - _BI_VALUE_PATTERNS: 질문 또는 컨텍스트에서 "두 개의 카테고리가 병렬로 제시됨"을
#   감지하는 정규식. 패턴 매칭 시 답변도 두 값을 포함해야 완전으로 판정.
# - expected_value_count(): 질문/컨텍스트 분석해 기대 값 개수를 반환.
# - verify_completeness(): present_units와 결합해 답변의 값 개수 검증.

_BI_VALUE_PATTERNS = (
    # 주전공/제2전공, 주전공/부전공 (이분 전공)
    re.compile(r"(?:주\s*전공|제1\s*전공|본\s*전공).*?(?:제2\s*전공|부\s*전공|복수\s*전공|융합\s*전공)"),
    # 이론/실습 이수학점
    re.compile(r"이론.*?실습|실습.*?이론"),
    # 최대/최소
    re.compile(r"최대.*?최소|최소.*?최대"),
    # 학년별 구분 (예: "3학년 9학점, 4학년 6학점")
    re.compile(r"\d\s*학년.*?\d\s*학년"),
)

# 질문 키워드 기반 bi-value 트리거 (컨텍스트 검사가 실패해도 질문 자체가 2값을 요구)
_BI_VALUE_QUESTION_KWS = (
    ("복수전공", "이수학점"),  # g04
    ("복수전공", "최소"),      # g04 변형
    ("부전공", "이수학점"),
    ("제2전공", "이수학점"),
)


def expected_value_count(question: str, context: str = "") -> int:
    """질문이 요구하는 값 개수를 반환.

    - 질문이 bi-value 패턴(주/제2, 이론/실습 등)에 매칭되면 2
    - 질문 키워드가 bi-value 트리거(복수전공+이수학점 등)에 매칭되면 2
    - context에 bi-value 패턴이 실재하면 2 (LLM이 참고할 값이 둘이므로)
    - 그 외에는 1 (기본)
    """
    if not question:
        return 1

    # 1. 질문 키워드 조합
    for kw_pair in _BI_VALUE_QUESTION_KWS:
        if all(kw in question for kw in kw_pair):
            return 2

    # 2. 질문 자체의 bi-value 패턴
    for pat in _BI_VALUE_PATTERNS:
        if pat.search(question):
            return 2

    # 3. 컨텍스트 내 bi-value 패턴 — 컨텍스트에 "주전공 36학점 ... 제2전공 27학점"
    # 같이 두 값이 병렬로 제시된 경우에도 완전성 요구.
    if context:
        for pat in _BI_VALUE_PATTERNS:
            if pat.search(context):
                return 2

    return 1


def verify_completeness(question: str, answer: str, context: str = "") -> bool:
    """답변이 질문이 요구하는 값 개수를 충족하는지 검증.

    사용처: `AnswerGenerator.generate_full()` 말미에서 verify_answer_against_context
    직후 호출. 실패 시 fill_from_context 재시도 또는 context에서 누락 값 보완.

    반환:
        True  — 답변의 값 개수가 기대치 이상 (완전)
        False — 기대치 미달 (부분 답변, 보완 필요)
    """
    expected = expected_value_count(question, context)
    if expected <= 1:
        return True

    if not answer:
        return False

    a_units = present_units(answer)
    # 기본 검증: credit(학점) 카테고리에서 값 개수 확인
    credit_values = a_units.get("credit", [])
    if len(credit_values) >= expected:
        return True

    # credit이 없으면 course(과목) 카테고리도 인정
    course_values = a_units.get("course", [])
    if len(credit_values) + len(course_values) >= expected:
        return True

    return False
