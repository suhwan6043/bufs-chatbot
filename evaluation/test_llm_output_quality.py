"""
평가 4: LLM 영어 출력 품질 (Ollama 실행 필요)

실행 방법:
    # Ollama 서버 실행 후 (콜드 스타트 대비 --timeout=120 권장):
    pytest evaluation/test_llm_output_quality.py -v -s --timeout=120

    # Ollama 없이 오프라인 테스트만:
    pytest evaluation/test_llm_output_quality.py -v -m offline

주의:
    1. 응답 시간 — EXAONE 7.8B 콜드 스타트 기준 첫 응답 30초 이상.
       --timeout=120 없이 실행하면 기본 타임아웃에 걸릴 수 있음.

    2. 비결정론 — temperature=0.1이라도 LLM 출력은 매 실행마다 미세하게 다름.
       · 한국어 포함 여부(이진 판정)는 안정적.
       · 숫자·날짜 정확도는 컨텍스트에 해당 청크가 실제로 검색됐는지에 따라 달라짐.
         → 실패 시 ChromaDB 검색 결과(vector_results)를 먼저 확인할 것.
         → 이 테스트는 컨텍스트를 직접 주입하므로 ChromaDB 의존성 없음.
            단, generate_full() 내부에서 컨텍스트가 잘렸을 경우 숫자 누락 가능.

마커:
    @pytest.mark.llm  — Ollama 연결이 필요한 테스트
    @pytest.mark.offline — Ollama 없이 실행 가능한 테스트

환경 변수:
    SKIP_LLM_TESTS=1  → LLM 테스트 스킵 (CI 용)
"""

import os
import re
import asyncio
import pytest

from app.pipeline.answer_generator import AnswerGenerator

SKIP_LLM = os.getenv("SKIP_LLM_TESTS", "0") == "1"
pytestmark = pytest.mark.skipif(SKIP_LLM, reason="SKIP_LLM_TESTS=1")


# ────────────────────────────────────────────────────────────────────────────
# 헬퍼
# ────────────────────────────────────────────────────────────────────────────
def run(coro):
    return asyncio.run(coro)


_KO_WORD_RE = re.compile(r"[가-힣]{2,}")  # 2글자 이상 연속 한국어

# 괄호·따옴표 안 한국어 병기 패턴
# EN(KO) 허용: "(마이크로전공)", "복수전공", '휴학'
# KO(EN) 허용: "복수전공 (Multiple Major)", "36학점 (36 credits)"
_FRAMED_KO_RE = re.compile(
    r'(?:'
    # ── EN 뒤에 붙는 (KO) 형태 ───────────────────────────────
    r'\([^)]*[가-힣]+[^)]*\)'                   # (한국어)
    r'|"[^"]*[가-힣]+[^"]*"'                    # "한국어"
    r"|'[^']*[가-힣]+[^']*'"                    # '한국어'
    r'|「[^」]*[가-힣]+[^」]*」'                  # 「한국어」
    r'|『[^』]*[가-힣]+[^』]*』'                  # 『한국어』
    # ── KO (EN) 역방향 병기 — EXAONE 출력 패턴 ───────────────
    # "복수전공 (Multiple Major)", "36학점 (36 credits)",
    # "**매 학기 수강신청 기간** (course registration period...)"
    # 마크다운 bold(**) 안에 있어도 처리: \*{0,2}KO\*{0,2} (EN)
    r'|\*{0,2}[가-힣][가-힣\s]*\*{0,2}\s*\([^)]*[a-zA-Z][^)]*\)'  # 한국어 (영어)
    r')'
)


def _strip_framed_ko(text: str) -> str:
    """
    괄호·따옴표로 감싼 한국어 병기를 제거합니다.

    LLM이 영어 답변에서 한국어를 보조 표기하는 방식:
      허용: "micro major (마이크로전공)", 'referred to as "복수전공"'
      차단: 본문에 단독 등장하는 한국어 단어
    """
    return _FRAMED_KO_RE.sub("", text)


# 하위 호환 alias
_strip_paren_ko = _strip_framed_ko


def _has_korean(text: str) -> bool:
    """프레임 밖 본문에 한국어 단어가 있으면 True."""
    return bool(_KO_WORD_RE.search(_strip_framed_ko(text)))


@pytest.fixture(scope="module")
def generator():
    return AnswerGenerator()


@pytest.fixture(scope="module")
def ollama_available(generator):
    import httpx
    from app.config import settings

    # 1) 서버 생존 확인
    try:
        resp = httpx.get(f"{settings.ollama.base_url}/api/tags", timeout=5)
        resp.raise_for_status()
    except Exception:
        pytest.skip(
            f"Ollama 서버에 연결할 수 없습니다. 'ollama serve' 후 재실행하세요.\n"
            f"  URL: {settings.ollama.base_url}"
        )

    # 2) 대상 모델 로드 여부 확인
    tags = resp.json()
    available_models = [m["name"] for m in tags.get("models", [])]
    model = settings.ollama.model
    if model not in available_models:
        pytest.skip(
            f"모델 '{model}'이 로드되어 있지 않습니다.\n"
            f"  실행: ollama pull {model}\n"
            f"  현재 로드된 모델: {available_models or '없음'}"
        )

    return True


# ────────────────────────────────────────────────────────────────────────────
# 4-A: 기본 답변 가능 여부
# ────────────────────────────────────────────────────────────────────────────
CONTEXT_GRADUATION_2023 = """---[p.1]
2023학번 이후 졸업이수학점: 총 130학점
- 교양필수: 21학점
- 전공필수: 36학점
- 전공선택: 18학점
- 자유선택: 55학점
"""

CONTEXT_MICRO_MAJOR = """---[p.3]
마이크로전공 신청 방법:
1. 학사정보시스템 로그인
2. 학적 > 다전공 신청 메뉴 선택
3. 신청 학기 중 마이크로전공 선택 후 제출
신청 자격: 2학년 이상, 직전학기 평점 2.0 이상
"""

CONTEXT_REGISTRATION_PERIOD = """---[p.5]
2026학년도 1학기 수강신청 일정:
- 예비 수강신청(장바구니): 2026.02.03 ~ 2026.02.07
- 본 수강신청: 2026.02.09 ~ 2026.02.12
- 수강신청 정정: 2026.03.03 ~ 2026.03.07
"""

CONTEXT_MAX_CREDIT = """---[p.2]
수강 신청 학점 상한:
- 기본: 18학점
- 직전학기 평점 4.0 이상인 경우: 21학점 (2018학번 이후)
- 직전학기 평점 4.0 이상인 경우: 22학점 (2019학번 이후)
"""

CONTEXT_INTERNATIONAL_LOA = """---[p.7]
외국인 유학생 휴학 규정:
- 신청 자격: 외국인 유학생도 휴학 가능
- 최대 휴학 기간: 총 4학기 (2학기씩 2회)
- 필요 서류: 휴학신청서, 지도교수 확인서, 비자 유지 확인서
- 신청 방법: 학사정보시스템 → 학적 → 휴학신청
"""


@pytest.mark.timeout(120)
@pytest.mark.parametrize("question,context,check_absent_ko,check_present_en,description", [
    (
        "What are the graduation requirements for a 2023 student?",
        CONTEXT_GRADUATION_2023,
        True,
        ["2023 cohort", "130"],
        "2023학번 졸업요건: 한국어 없음, 학점 숫자 정확, cohort 명시",
    ),
    (
        "How do I apply for a micro major?",
        CONTEXT_MICRO_MAJOR,
        True,
        ["micro major"],  # 대소문자 무관 체크 (LLM이 소문자 출력 허용)
        "마이크로전공 신청: micro major 표기 포함, 절차 영어",
    ),
    (
        "When is the course registration period for 2026 spring semester?",
        CONTEXT_REGISTRATION_PERIOD,
        True,
        ["02.09", "02.12"],
        "수강신청 기간: 날짜 숫자 그대로, 영어 답변",
    ),
    (
        "What are the maximum credit registration limits per semester for students?",
        CONTEXT_MAX_CREDIT,
        True,
        ["18", "21", "22"],
        "최대 수강학점: 기본 18, GPA 조건부 21/22 세 값 모두 언급",
    ),
    (
        "Can international students take a leave of absence?",
        CONTEXT_INTERNATIONAL_LOA,
        True,
        ["international student", "leave of absence"],  # 대소문자 무관 체크
        "외국인 유학생 휴학: international student·leave of absence 포함, 조건 영어",
    ),
])
def test_basic_answer_quality(
    ollama_available, generator,
    question, context, check_absent_ko, check_present_en, description,
):
    answer = run(generator.generate_full(question, context, lang="en"))

    # 괄호 밖 본문에 한국어 단어 미포함
    # (괄호 안 병기 예: "Micro Major (마이크로전공)"는 허용)
    if check_absent_ko:
        ko_matches = _KO_WORD_RE.findall(_strip_paren_ko(answer))
        assert not ko_matches, (
            f"[{description}]\n"
            f"답변에 한국어 단어 포함됨 (괄호 밖): {ko_matches}\n"
            f"답변:\n{answer}"
        )

    # 필수 영어 표현 포함 (대소문자 무관)
    answer_lower = answer.lower()
    for phrase in check_present_en:
        assert phrase.lower() in answer_lower, (
            f"[{description}]\n"
            f"필수 표현 미포함 (대소문자 무관): {phrase!r}\n"
            f"답변:\n{answer}"
        )


# ────────────────────────────────────────────────────────────────────────────
# 4-B: 언어 일관성 (한국어 섞임 여부)
# ────────────────────────────────────────────────────────────────────────────
CONTEXT_DOUBLE_MAJOR = """---[p.4]
복수전공 이수 요건:
- 신청 자격: 2학년 이상 재학생
- 이수 학점: 36학점 이상
- 신청 기간: 매 학기 수강신청 기간 중
"""

CONTEXT_CREDITS_TO_GRADUATE = """---[p.1]
졸업이수학점: 130학점
- 교양: 45학점 이상
- 전공: 54학점 이상
- 자유선택: 잔여학점
"""


@pytest.mark.timeout(120)
@pytest.mark.parametrize("question,context,forbidden_ko_words,description", [
    (
        "What is a double major?",
        CONTEXT_DOUBLE_MAJOR,
        ["복수전공", "학점", "신청"],
        "이중전공 설명: 한국어 학사 용어 미등장",
    ),
    (
        "How many credits to graduate?",
        CONTEXT_CREDITS_TO_GRADUATE,
        ["이수학점", "졸업요건", "교양", "전공"],
        "졸업학점 안내: 한국어 학사 용어 미등장",
    ),
])
def test_no_korean_in_english_answer(
    ollama_available, generator,
    question, context, forbidden_ko_words, description,
):
    answer = run(generator.generate_full(question, context, lang="en"))
    answer_no_paren = _strip_paren_ko(answer)

    found = [w for w in forbidden_ko_words if w in answer_no_paren]
    assert not found, (
        f"[{description}]\n"
        f"금지 한국어 단어 등장 (괄호 밖): {found}\n"
        f"답변:\n{answer}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 4-C: 컨텍스트 없을 때 폴백 메시지
# ────────────────────────────────────────────────────────────────────────────
FALLBACK_EXPECTED_EN = "This information could not be confirmed in the available documents."


@pytest.mark.timeout(120)
@pytest.mark.parametrize("question,context,description", [
    (
        "What is the school cafeteria menu today?",
        "",
        "컨텍스트 없음 → 영어 고정 폴백 문구",
    ),
    (
        "What are the parking rules for students?",
        "이 문서는 수강신청 안내입니다.",  # 전혀 관련 없는 컨텍스트
        "무관한 컨텍스트 → 영어 폴백",
    ),
])
def test_fallback_english_message(
    ollama_available, generator,
    question, context, description,
):
    answer = run(generator.generate_full(question, context, lang="en"))

    assert FALLBACK_EXPECTED_EN in answer, (
        f"[{description}]\n"
        f"기대 폴백 문구: {FALLBACK_EXPECTED_EN!r}\n"
        f"실제 답변:\n{answer}"
    )

    # 폴백 상황에서도 괄호 밖 한국어 미포함
    ko_matches = _KO_WORD_RE.findall(_strip_paren_ko(answer))
    assert not ko_matches, (
        f"[{description}] 폴백 답변에 한국어 포함 (괄호 밖): {ko_matches}\n답변:\n{answer}"
    )


# ────────────────────────────────────────────────────────────────────────────
# 4-D: 연결 오류 처리 (Ollama 없이 실행 가능)
# ────────────────────────────────────────────────────────────────────────────
@pytest.mark.offline
def test_connection_error_returns_user_message():
    """Ollama 서버가 없을 때 generate_full()이 사용자 친화적 메시지를 반환하는지"""
    import httpx
    from unittest.mock import AsyncMock, patch

    gen = AnswerGenerator()
    gen.base_url = "http://localhost:19999"  # 존재하지 않는 포트

    answer = run(gen.generate_full("test", "context", lang="en"))
    assert "connect" in answer.lower() or "server" in answer.lower() or "ollama" in answer.lower(), (
        f"연결 실패 메시지가 사용자 친화적이지 않음: {answer!r}"
    )
