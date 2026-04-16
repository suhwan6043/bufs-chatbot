"""
평가 1: 언어 감지 (detect_language)

판정 기준: 한국어 문자 수 / 전체 alpha 문자 수 >= 0.3 → 'ko', else 'en'
"""

import pytest
from app.pipeline.language_detector import detect_language


@pytest.mark.parametrize("text,expected,reason", [
    # ── 한국어 우세 ─────────────────────────────────────────────────────────
    (
        "수강신청 기간이 언제야?",
        "ko",
        "한글 100%",
    ),
    (
        "2023학번 졸업요건 알려줘",
        "ko",
        "한글 우세 (숫자·한글 혼합, 한글 비율 높음)",
    ),
    (
        "College English 수강 방법",
        "en",
        "실제 비율: 한글 4자(수강방법) / alpha 18자(College7+English7+수강2+방법2) = 0.222 < 0.3 → en",
    ),
    (
        "OCU 수강신청은 어떻게?",
        "ko",
        "한글 우세 ('수강신청'·'어떻게' 포함)",
    ),
    (
        "GPA 기준이 뭐야",
        "ko",
        "한글 우세 ('기준이뭐야' 한글 6자, 영어 3자 → ratio=6/9≈0.67)",
    ),
    # ── 기본값 케이스 ───────────────────────────────────────────────────────
    (
        "",
        "ko",
        "빈 문자열: alpha_count == 0 → 기본값 ko",
    ),
    (
        "2023 2024 120",
        "ko",
        "숫자만 → alpha_count == 0 → 기본값 ko",
    ),
    # ── 영어 우세 ─────────────────────────────────────────────────────────
    (
        "When is the course registration period?",
        "en",
        "한글 0%",
    ),
    (
        "micro major requirements",
        "en",
        "한글 0%",
    ),
    (
        "2023 student graduation credits",
        "en",
        "숫자+영어, 한글 없음",
    ),
])
def test_detect_language(text, expected, reason):
    result = detect_language(text)
    assert result == expected, (
        f"입력: {text!r}\n"
        f"기대: {expected!r}, 실제: {result!r}\n"
        f"판정 근거: {reason}"
    )


# ── College English 경계값 상세 검증 ───────────────────────────────────────
def test_college_english_boundary():
    """
    'College English 수강 방법'의 실제 비율 계산:
    - 한글: 수강방법 = 4자
    - alpha(한글+영어): College(7) + English(7) + 수강(2) + 방법(2) = 18
    - ratio = 4/18 ≈ 0.222 < 0.3 → 실제로는 'en' 반환

    이 테스트는 경계값 동작을 명시적으로 문서화합니다.
    기대값 'ko'로 설정했다가 실패하면 → 임계값 조정 또는 YAML 보강 필요.
    """
    text = "College English 수강 방법"
    result = detect_language(text)
    # 비율: 한글 4자 / alpha(영어13+한글4)=17자 = 0.235 < 0.3 → "en"
    assert result == "en", f"예상 외 값: {result!r} (비율 < 0.3 이므로 'en' 기대)"
    import re
    _KO_RE = re.compile(r"[가-힣ㄱ-ㅎㅏ-ㅣ]")
    _ALPHA_RE = re.compile(r"[a-zA-Z가-힣]")
    ko = len(_KO_RE.findall(text))
    alpha = len(_ALPHA_RE.findall(text))
    print(f"\n  '{text}' → ko={ko}, alpha={alpha}, ratio={ko/alpha:.3f}, result={result!r}")
