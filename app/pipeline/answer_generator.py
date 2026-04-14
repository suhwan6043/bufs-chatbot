"""
답변 생성기 - Ollama (OpenAI 호환 API)로 답변 생성
스트리밍 응답 지원

EN 쿼리: One-Pass Streaming (KO 초안 → 목표 언어 번역)
  - <ko_draft> 스트리밍 중 "분석 중..." 표시
  - <final_answer> 감지 시 CLEAR 후 번역본 스트리밍
  - Rolling Buffer State Machine으로 태그 쪼개짐 방어
KO 쿼리: 기존 단일 생성 흐름 유지
"""

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# ── Intent → 기본 EN 용어 (matched_terms 비었을 때 Mandatory Terms 최소 보장) ──
# EN UI에서 KO 타이핑 시 FlashText가 동작하지 않아 matched_terms=[]가 됨.
# intent로 최소 1개의 학사 용어를 주입해 LLM 번역 일관성을 확보한다.
_INTENT_FALLBACK_TERM: dict[str, dict] = {
    "GRADUATION_REQ":    {"ko": "졸업요건",   "en": "Graduation Requirements"},
    "EARLY_GRADUATION":  {"ko": "조기졸업",   "en": "Early Graduation"},
    "REGISTRATION":      {"ko": "수강신청",   "en": "Course Registration"},
    "SCHEDULE":          {"ko": "학사일정",   "en": "Academic Calendar"},
    "MAJOR_CHANGE":      {"ko": "복수전공",   "en": "Double Major"},
    "LEAVE_OF_ABSENCE":  {"ko": "휴학",       "en": "Leave of Absence"},
    "SCHOLARSHIP":       {"ko": "장학금",     "en": "Scholarship"},
    "COURSE_INFO":       {"ko": "교과목",     "en": "Course"},
    "ALTERNATIVE":       {"ko": "대체과목",   "en": "Alternative Course"},
    "TRANSCRIPT":        {"ko": "성적표",     "en": "Transcript"},
}

# ── EN One-Pass 시스템 프롬프트 ───────────────────────────────────────────────
EN_SKIP_TRANSLATE_SYSTEM_PROMPT = """/no_think

You are an official academic administration AI chatbot for BUFS (Busan University of Foreign Studies).
Answer the user's query in English based ONLY on the provided [Context].

[Context Language Notice]
The [Context] is written in Korean. You MUST read and understand it directly.
- Korean tables (표), schedules (학사일정), and bullet lists contain the answer — parse them carefully.
- Convert Korean date formats to English: "3(월)" → "March", "5월 20일" → "May 20", "화" → "Tue".
- Convert Korean day-of-week abbreviations: 월=Mon, 화=Tue, 수=Wed, 목=Thu, 금=Fri, 토=Sat, 일=Sun.
- Use the [Term Guide] below to translate Korean academic terms into correct English equivalents.

[STRICT PRECISION RULES]
1. Exact Numbers: Copy all numbers, credits, amounts, and URLs exactly from the context.
2. Conditional Information: Include exceptions ("However", "Except for", "Provided that").
3. Date Precision: State both start and end dates. Convert all dates to English format.
4. Student Type / Cohort Rules: If rules differ by group, list each separately.
5. Tables: When the context has a table, find the row/column that matches the question and extract the value.
6. Only refuse when the context is TRULY irrelevant: If the context contains ANY information related to the question — even partial — extract and present what is available. Reserve refusal ONLY for completely unrelated contexts (e.g., question about cafeteria but context is about course registration).
7. Refusal format: "I'm sorry, but I couldn't find relevant information. Please contact the Academic Affairs Office at +82-51-509-5182."

{term_guide_section}
[Output Format]
- Start with the direct answer (number, date, yes/no, etc.).
- Follow with 1-4 sentences covering conditions, exceptions, and procedures.
- Do NOT output any thinking process, Korean draft, or XML tags.
- Answer directly in English.\
"""

# ── KO 시스템 프롬프트 (기존 유지) ──────────────────────────────────────────
SYSTEM_PROMPT = """/no_think

당신은 부산외국어대학교(BUFS) 학사 안내 AI입니다.

## 출력 형식 (최우선 규칙)
- **사고 과정(thinking)·추론 과정·분석 단계를 절대 출력하지 마세요.**
- "Thinking Process", "Analyze the Request", "Step 1", "Let me think", "First, I will" 같은 메타 문구 금지.
- 영어 내부 독백 금지. 최종 답변만 **한국어**로 바로 작성하세요.
- `<think>`, `<thinking>`, `</think>` 같은 태그도 출력에 포함하지 마세요.
- 답변은 곧바로 결론 문장으로 시작해야 합니다.

## 절대 규칙 (위반 금지)
1. 답변은 반드시 [컨텍스트]에 실제로 적혀 있는 정보로만 구성하세요. 컨텍스트 밖 지식·상식·추측은 일체 금지입니다.
2. 숫자(학점·날짜·시간·금액·일수), URL, 고유명사, 절차 단계는 컨텍스트 원문을 정확히 복사하세요. 한 글자라도 바꾸지 마세요.
3. "단", "다만", "제외", "예외", "반드시 ~해야", "~면 무효", "유의" 같은 조건·결과 문장은 답변의 일부로 반드시 포함하세요. 이것들을 빠뜨리는 것이 가장 큰 감점 요인입니다.
4. 학번·학생유형·학기별로 다른 규정이 있으면 해당 조건을 답변 첫 문장 또는 바로 뒤에 명시하세요.
5. 질문이 언급하지 않았더라도 컨텍스트의 중요 조건·결과는 누락하지 마세요. "질문 안 했으니 생략" 금지.

## FAQ 우선 활용 규칙 (가장 중요)
컨텍스트 앞부분에 `[카테고리] Q: ... A: ...` 형태의 FAQ 블록이 있으면:
- **그 Q가 사용자 질문과 의미적으로 일치하는지 먼저 판단하세요.**
- 일치하면 해당 A의 전체 내용을 답변의 뼈대로 사용하세요 (자연스러운 말투로 전달 가능하지만 사실·숫자·조건은 변경 금지).
- FAQ의 A를 두고 다른 청크의 일정·날짜·일반 안내로 답하지 마세요. FAQ A가 정답입니다.
- 여러 FAQ가 있으면 질문에 가장 근접한 것을 고르세요.

## 개인정보 보호 규칙 (필수)
- [학생 학점 현황]이 제공된 경우, 학생의 실제 취득학점·성적 데이터를 기반으로 구체적으로 답변하세요.
- 졸업요건 부족학점, 재수강 후보, 수강신청 가능학점 등을 안내할 때 [학생 학점 현황]의 수치를 활용하세요.
- 학생 이름과 학번을 답변에 절대 포함하지 마세요. "귀하의", "현재" 등 비식별 표현을 사용하세요.

## 답변 구성
- 첫 문장: 질문에 대한 직접적 결론 (예/아니요, 숫자, 날짜, 방법명 등).
- 이어지는 1~4문장: 컨텍스트에 있는 필수 조건·예외·결과·이후 절차. 반드시 포함.
- 절차·단계·목록(-)으로 정리할 수 있는 정보는 글머리 기호를 사용.
- 서론("아래와 같습니다", "다음과 같이 안내드립니다")·메타 문구는 쓰지 마세요.
- 컨텍스트가 질문에 직접 답하지 않으면 "관련 정보를 찾을 수 없습니다. 학사지원팀(051-509-5182)에 문의하시기 바랍니다."로만 답하세요. 비슷한 주제라도 질문의 핵심을 답하지 못하면 "정보 없음"으로 처리하세요.

## 질문 유형별 지침
- **Yes/No + 조건 질문** ("~가 가능한가요?"): 먼저 "가능합니다/불가합니다"로 답하고, 그 뒤에 "단, ~" 형태로 컨텍스트의 모든 조건·결과를 이어 쓰세요. (예: "휴학생도 가능합니다. 단, 다음 정규 학기에 반드시 복학해야 하며 미복학 시 성적 무효 처리됩니다.")
- **"언제" 질문**: 컨텍스트의 날짜/기간/회차/일차 정보만 답하세요. 질문에 맞지 않는 다른 날짜는 절대 섞지 마세요. 대상별(학년별·신입생별) 다른 시기가 있으면 모두 나열.
- **"차이점" 질문**: 두 항목을 모두 각각 설명하세요. 한 쪽만 답하는 것은 오답입니다.
- **수치 한도 질문** ("최대 몇 학점"): 핵심 숫자 한 문장 + "단, ~" 예외 조건 한 문장.
- **"무엇인가요" / 정의 질문**: 컨텍스트의 정의 문장을 그대로 답 + 관련 특징 1~2개.
- **절차 질문** ("어떻게 하나요"): 단계를 순서대로 모두 나열.

## 정확성 강화
- 날짜에 시간이 있으면 함께 쓰세요 ("3월 2일 오전 10시").
- 기간은 시작일~종료일 모두 ("2월 9일~2월 12일").
- 컨텍스트에 숫자가 없으면 구체 숫자를 지어내지 마세요.
- "~일 것으로 보입니다", "~으로 추정됩니다" 같은 추측 표현 금지.
- 질문이 A를 묻는데 컨텍스트 앞부분이 A와 무관한 B(일정·일반 안내 등)여도, 뒤에 A를 직접 답하는 FAQ/청크가 있으면 그쪽을 우선 사용하세요.

## 주제 분리 규칙 (매우 중요)
- **기본 원칙**: "OCU"를 명시하지 않은 질문은 모두 **부산외대(본교)** 기준으로 답하세요.
- 질문이 OCU를 묻지 않았으면 OCU 관련 내용(OCU 수강방법, 시스템사용료, OCU 홈페이지, OCU 성적처리, 상대평가 등)을 답변에 **절대 포함하지 마세요**. 본교 내용만 답하세요.
- 반대로 질문이 OCU를 명시적으로 물었으면 OCU 관련 내용만 답하세요.
- **성적처리 구분**: 본교 수업은 2023학년도 2학기부터 상대평가에서 **과정중심 절대평가**로 변경되었습니다. OCU 수업은 상대평가입니다. 컨텍스트에 "상대평가"가 나오면 OCU 관련 내용이므로, OCU를 묻지 않은 질문에는 해당 부분을 건너뛰세요. 성적처리 관련 답변 시 "2023학년도 2학기부터 절대평가로 변경" 사실을 반드시 언급하세요.

## 간결성 vs 완전성 균형
- 간결성은 **메타 문구·서론 제거**이지 사실 정보 삭제가 아닙니다.
- 컨텍스트의 조건·예외·결과를 "간결성"을 이유로 빠뜨리면 답이 틀린 것으로 간주됩니다.
- 한 문장으로 충분하면 한 문장, 여러 조건이 있으면 모두 포함된 4문장까지 허용됩니다.

## 참고 예시 (반드시 따를 것)

### 예시 1: 표/숫자 추출 → 컨텍스트의 숫자를 그대로 답변
[컨텍스트] 이수과목1(이론+실습) 이론 36 실습 27 커뮤니티 2 (2023학번 기준)
[질문] 2023학번 이수과목1의 이론/실습 학점은?
[모범 답변] 이론 36학점, 실습 27학점입니다.

### 예시 2: 여러 조건 나열 → 하나도 빠뜨리지 않고 전부 나열
[컨텍스트] 최대 신청 학점: 공학대학 GPA 4.0 이상 21학점, 복지보건계열 20학점, 인문사회계열 21학점
[질문] 학과별 최대 신청 학점은?
[모범 답변] 학과에 따라 다릅니다.
- 공학대학: GPA 4.0 이상, 21학점
- 복지보건계열: 20학점
- 인문사회계열: 21학점

### 예시 3: 금액 추출 → 핵심 숫자로 직접 답변
[컨텍스트] OCU 시스템사용료: 과목당 120,000원 (초과 수강 시 과목당 별도)
[질문] OCU 시스템사용료는 얼마인가요?
[모범 답변] 과목당 120,000원입니다.
"""

# 태그 최대 길이 기준 홀딩 버퍼 크기
_TAG_HOLD = 16  # "<final_answer>" = 14자 + 여유 2자


class AnswerGenerator:
    """
    [역할] Ollama (OpenAI 호환 API)로 답변 생성
    [핵심] SSE 스트리밍 응답
    [EN]  One-Pass (KO 초안 → 목표 언어 번역) + Rolling Buffer State Machine
    [주의] temperature=0.1 (사실 정확성 최우선)
    """

    def __init__(self):
        self.base_url = settings.llm.base_url
        self.model = settings.llm.model
        self.timeout = settings.llm.timeout

    # ── 프롬프트 빌더 ─────────────────────────────────────────────────────────

    def _build_en_system_prompt(
        self,
        matched_terms: Optional[list] = None,
        context_terms: Optional[list] = None,
        intent: Optional[str] = None,
    ) -> str:
        """EN skip-translate 시스템 프롬프트를 구성합니다.

        matched_terms (쿼리 추출) + context_terms (컨텍스트 추출)를
        병합·중복 제거하여 [Term Guide]로 주입한다.
        """
        # 쿼리 용어 + 컨텍스트 용어 병합 (쿼리 우선)
        seen: set[str] = set()
        terms: list[dict] = []
        for t in (matched_terms or []):
            if t["ko"] not in seen:
                seen.add(t["ko"])
                terms.append(t)
        for t in (context_terms or []):
            if t["ko"] not in seen:
                seen.add(t["ko"])
                terms.append(t)

        # intent fallback
        if not terms and intent:
            fallback = _INTENT_FALLBACK_TERM.get(intent)
            if fallback:
                terms = [fallback]

        if terms:
            term_list = "\n".join(f"- {t['ko']} → {t['en']}" for t in terms)
            term_section = f"[Term Guide]\n{term_list}\n"
        else:
            term_section = ""

        return EN_SKIP_TRANSLATE_SYSTEM_PROMPT.format(
            term_guide_section=term_section,
        )

    def _build_prompt(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
        lang: Optional[str] = None,
        student_context: Optional[str] = None,
        context_confidence: Optional[float] = None,
        question_type: Optional[str] = None,
        entities: Optional[dict] = None,
        intent: Optional[str] = None,
    ) -> str:
        """LLM에 전달할 유저 프롬프트를 구성합니다."""
        parts = []

        if lang == "en":
            if student_context:
                parts.append(f"[Student Info]\n{student_context}\n")

            # Gap 1-a: Structured pre-answer checklist for low-confidence contexts
            # Previous version was a soft "warning + suggestion" that the LLM
            # often ignored. Structured checklist forces explicit verification
            # before attempting to answer.
            if context_confidence is not None and context_confidence < 0.5:
                parts.append(
                    f"[Relevance {context_confidence:.0%} — Mandatory Pre-Answer Check]\n"
                    "Before generating an answer, verify ALL of the following:\n"
                    "  1) Does the context contain the **exact topic keyword** "
                    "     from the question (e.g., 'double major', 'OCU', 'start date')?\n"
                    "  2) Do the relevant sentences in the context address the "
                    "     **same specific topic** as the question? "
                    "     (e.g., 'registration period' ≠ 'grade check period')\n"
                    "  3) Do the numbers/dates/names in the context **match what the "
                    "     question asks for**?\n"
                    "\n"
                    "If ANY answer is 'no', DO NOT guess. Respond with EXACTLY this sentence:\n"
                    "  → 'I'm sorry, but I couldn't find relevant information. "
                    "Please contact the Academic Affairs Office at +82-51-509-5182.'\n"
                    "\n"
                    "Warning: Similar keywords on different topics are wrong answers. "
                    "(e.g., 'OCU system fee' vs 'summer session fee')\n"
                )

            # Gap 1-b: question_type hint
            if question_type == "overview":
                parts.append(
                    "[Note] This question asks for a general overview. "
                    "Synthesize the key rules, schedules, and procedures from the context "
                    "into a comprehensive summary. Do not limit your answer to a single FAQ entry.\n"
                )

            # Gap 1-c: question_focus hints
            if question_focus == "period":
                parts.append(
                    "[Note] This question asks about dates or periods. "
                    "Find the specific schedule (e.g., registration window, deadline) "
                    "in the context and answer with exact dates and times. "
                    "Do not answer with credits or numeric limits.\n"
                )
            elif question_focus == "limit":
                parts.append(
                    "[Note] This question asks about a numeric limit (credits, count, amount). "
                    "Lead with the single key number, then briefly note any exceptions.\n"
                )
            elif question_focus == "table_lookup":
                parts.append(
                    "[Note] This question asks about specific numbers from a table. "
                    "Find the exact values (credits, hours, amounts) in the context "
                    "and answer with those numbers directly. Do not provide general explanations.\n"
                )
            elif question_focus == "rule_list":
                parts.append(
                    "[Note] This question asks about a list of requirements or conditions. "
                    "List ALL conditions from the context without omitting any. "
                    "Present each condition as a separate bullet point.\n"
                )
            elif question_focus == "method":
                parts.append(
                    "[Note] This question asks how to do something (procedure or method). "
                    "List the steps in order. Do not omit any required documents or actions.\n"
                )
            elif question_focus == "location":
                parts.append(
                    "[Note] This question asks about a location or office. "
                    "State the specific building, room, website, or contact point directly.\n"
                )
            elif question_focus == "eligibility":
                parts.append(
                    "[Note] This question asks about eligibility or qualification. "
                    "State clearly who qualifies and who does not, including all conditions.\n"
                )

            # Gap 1-d: OCU contamination warning
            _ctx_lower = context.lower()
            _has_ocu = "ocu" in _ctx_lower or "열린사이버" in _ctx_lower
            _asks_ocu = "ocu" in question.lower() or "열린사이버" in question.lower()
            if _has_ocu and not _asks_ocu:
                parts.append(
                    "[Warning — OCU Content] The context contains content about OCU "
                    "(Korea Open CyberUniversity). This question does NOT ask about OCU. "
                    "Ignore all OCU-related content and answer using BUFS (Busan University of "
                    "Foreign Studies) information only.\n"
                )

            # Gap 1-e: student cohort hint
            if student_id:
                parts.append(
                    f"[Student Cohort] Base your answer on enrollment year {student_id}. "
                    "If rules differ by cohort, prioritize the rule that applies to this year.\n"
                )

            parts.append(f"[Context]\n{context}\n")
            parts.append(f"[Question] {question}")
        else:
            if student_id:
                parts.append(f"[학번] {student_id}학번 기준으로 답변하세요.\n")

            # 원칙 2: 저신뢰 컨텍스트 → 구조화된 거절 절차 강제
            # 이전 버전은 "경고 + 제안" 형태라 LLM이 무시하고 답변을 시도했음.
            # 사전 체크리스트 + 정확한 템플릿 문장을 제공해 거절 성공률을 높인다.
            if context_confidence is not None and context_confidence < 0.5:
                parts.append(
                    f"[검색 관련성 {context_confidence:.0%} — 답변 전 필수 체크]\n"
                    "답변을 생성하기 전에 다음 3가지를 반드시 확인하세요:\n"
                    "  1) 컨텍스트에 질문의 **핵심 주제어**(예: '복수전공', 'OCU', '개강일')가 "
                    "     실제로 포함되어 있는가?\n"
                    "  2) 컨텍스트의 해당 문장이 **질문과 정확히 같은 주제**를 다루는가? "
                    "     (예: '수강신청 기간' 질문에 '성적조회 기간'은 틀린 주제)\n"
                    "  3) 숫자·날짜·명칭이 **질문이 요구하는 것과 일치**하는가?\n"
                    "\n"
                    "위 3개 중 하나라도 '아니오'면 추측하지 말고 아래 문장 하나로만 답하세요:\n"
                    "  → '관련 정보를 찾을 수 없습니다. "
                    "학사지원팀(051-509-5182)에 문의하시기 바랍니다.'\n"
                    "\n"
                    "주의: 비슷한 키워드가 있어도 주제가 다르면 오답입니다. "
                    "(예: 'OCU 시스템사용료' vs '계절학기 수강료', "
                    "'수업일수 1/4선' vs '수강신청 기간')\n"
                )

            # 성적표 기반 학생 학점 현황 (PII 제거됨)
            if student_context:
                parts.append(f"{student_context}\n")

            # QuestionType별 LLM 힌트
            if question_type == "overview":
                parts.append(
                    "[주목] 이 질문은 주제의 전반적 안내를 요청합니다. "
                    "컨텍스트에서 **핵심 규칙·일정·절차**를 종합하여 "
                    "주제 전체를 아우르는 요약 안내를 제공하세요. "
                    "특정 FAQ 한 건으로만 답하지 말고, 여러 소스를 통합해 답하세요.\n"
                )

            if question_focus == "period":
                parts.append(
                    "[주목] 이 질문은 날짜·기간·시간을 묻습니다. "
                    "컨텍스트에서 **질문이 묻는 바로 그 일정**(예: 성적 확정, 수강신청 등)의 "
                    "날짜·시간·기간 정보를 찾아 답하세요. "
                    "학점·수치가 아닌 날짜·기간으로 답해야 합니다. "
                    "컨텍스트에 해당 일정의 날짜가 없으면 "
                    "'해당 일정 정보를 찾을 수 없습니다. "
                    "학사지원팀(051-509-5182)에 문의하시기 바랍니다.'로 답하세요.\n"
                )
            elif question_focus == "limit":
                # q028 회귀 교훈: 질문이 '재수강 제한 기준'을 묻는데 LLM이
                # 같은 페이지의 '재수강 가능 성적(C+)' 문장을 참조해 오답 생성.
                # "제한/최대/초과"를 묻는 질문에서는 제한을 서술한 문장을 우선 참조.
                _asks_limit = any(kw in question for kw in ("제한", "최대", "초과", "이상", "상한"))
                parts.append(
                    "[주목] 이 질문은 학점·횟수·금액 등 한도·수치를 묻습니다. "
                    "핵심 숫자 하나로 먼저 답하고, 예외 조건은 간략히만 언급하세요. "
                    "질문에서 묻지 않은 다른 조건까지 나열하지 마세요.\n"
                )
                if _asks_limit:
                    parts.append(
                        "[중요] 질문이 '제한/최대/초과/상한'을 묻습니다. "
                        "컨텍스트에서 **제한·상한을 서술한 문장**(예: '최대 X학점으로 제한', "
                        "'X학점 초과 시', '한도는 X')을 우선 인용하세요. "
                        "'가능하다'·'조건을 충족하면' 같은 가능·조건 문장은 보조 정보로만 사용합니다.\n"
                    )
            elif question_focus == "table_lookup":
                parts.append(
                    "[주목] 이 질문은 표/숫자 데이터를 묻습니다. "
                    "컨텍스트에서 해당 숫자(학점·시간·금액 등)를 찾아 "
                    "그대로 답하세요. 일반적인 설명이나 안내 대신 "
                    "정확한 숫자만 제공하세요.\n"
                )
            elif question_focus == "rule_list":
                parts.append(
                    "[주목] 이 질문은 자격 요건/조건 목록을 묻습니다. "
                    "컨텍스트에 있는 조건을 하나도 빠뜨리지 말고 전부 나열하세요. "
                    "요약하지 말고 각 조건을 별도 항목(-)으로 제시하세요.\n"
                )

            # Phase 3 Step 4 (2026-04-12): asks_url 엔티티 기반 URL 강제 힌트 (l01, c01)
            # 질문이 "어디서/어디에서/사이트/신청 기관"을 묻는 경우 답변에 구체 URL을 반드시 포함.
            # Phase 2에서 query_analyzer._URL_SEEKING_KWS로 asks_url=True 감지됨.
            if entities and entities.get("asks_url"):
                parts.append(
                    "[중요] 이 질문은 접속 URL/사이트/신청 기관을 묻습니다. "
                    "답변에 반드시 **구체적인 URL 또는 도메인**(예: m.bufs.ac.kr, "
                    "sugang.bufs.ac.kr, www.kosaf.go.kr)을 포함하세요. "
                    "'학생포털시스템', '홈페이지' 같은 일반 명사만으로는 부족합니다. "
                    "컨텍스트에서 URL을 찾아 그대로 인용하세요.\n"
                )

            # Phase 3 Step 4 (2026-04-12): limit 류 질문 단위 정합성 (l02)
            # GT "4회(4년)" vs LLM "4 학기" 같은 단위 환산 오류 방지.
            # 수치 답변 시 컨텍스트 원문 표기를 그대로 사용하도록 강제.
            if question_focus == "limit":
                parts.append(
                    "[단위 일치] 수치를 답할 때 컨텍스트 원문에 적힌 **단위 표기를 그대로** "
                    "사용하세요. '회 → 학기', '년 → 학기' 같은 임의 환산 금지. "
                    "컨텍스트가 '4회(4년)'이면 답변도 '4회(4년)'로, "
                    "'12학점'이면 '12학점'으로 인용하세요.\n"
                )

            # Phase 3 Step 3 (2026-04-12): bi-value 질문 완전성 힌트 (g04)
            # 복수전공/부전공/제2전공 관련 질문은 주전공 값과 제2전공 값 둘 다 답해야 함.
            _bi_value_triggers = (
                ("복수전공" in question and any(kw in question for kw in ("학점", "이수", "몇"))),
                ("제2전공" in question and "학점" in question),
                ("부전공" in question and "학점" in question),
            )
            if any(_bi_value_triggers):
                parts.append(
                    "[완전성] 이 질문은 **주전공과 제2전공(또는 복수·부전공)** 각각의 "
                    "이수학점을 묻습니다. 답변에 두 값을 **모두** 포함해야 합니다. "
                    "예: '주전공 36학점, 제2전공 27학점입니다.' "
                    "한 값만 답하면 불완전한 답변입니다.\n"
                )

            # Phase 3+ 튜닝 (2026-04-12): Intent-aware field selection 힌트.
            # EARLY_GRADUATION 질문에서 "자격/조건"을 묻는데 LLM이 "기간/날짜"로 답하는
            # e02 문제 해결. 또한 e01 "정의"를 묻는 질문에 간결한 1문장 답변 유도.
            if intent and intent.upper() in ("EARLY_GRADUATION",):
                _asks_definition = any(kw in question for kw in ("정의", "무엇", "뭐", "어떤 제도", "의미"))
                _asks_qualification = any(kw in question for kw in ("자격", "요건", "조건", "기준", "성적", "평점"))
                if _asks_qualification:
                    parts.append(
                        "[중요 — 자격 요건 질문] 이 질문은 조기졸업의 **자격 조건/성적 기준**을 묻습니다. "
                        "답변에 반드시 **평점(GPA) 기준, 이수학점 조건** 등 자격 요건을 포함하세요. "
                        "신청 기간·날짜·절차가 아닌 **자격 요건**을 최우선으로 답하세요. "
                        "컨텍스트에서 '평점 3.7 이상' 또는 '이수학점 충족' 같은 조건 문장을 찾아 인용하세요.\n"
                    )
                elif _asks_definition:
                    parts.append(
                        "[중요 — 정의 질문] 이 질문은 조기졸업이 **무엇인지 정의**를 묻습니다. "
                        "핵심을 1~2문장으로 간결하게 답하세요: '정규 8학기보다 일찍(6 또는 7학기에) "
                        "졸업하는 제도'라는 핵심만 전달하면 됩니다. "
                        "세부 절차나 조건은 별도 질문으로 다루므로 여기서는 생략하세요.\n"
                    )

            # Phase 3+ 튜닝: MAJOR_CHANGE intent 힌트 (m02 자유전공제)
            if intent and intent.upper() == "MAJOR_CHANGE":
                if "자유전공" in question:
                    parts.append(
                        "[중요 — 자유전공제] 이 질문은 자유전공제 학생의 전공 변경에 대해 묻습니다. "
                        "'자유전공제로 입학한 학생'이 전공을 변경하는 방법(전부(과) vs 전공 변경 신청)의 "
                        "차이점을 명확히 답하세요. 컨텍스트에서 '자유전공' 또는 '전공변경'을 찾아 인용하세요.\n"
                    )

            # 원칙 2: OCU 혼입 감지 → 동적 경고 삽입
            # PDF 페이지가 본교+OCU 혼합인 경우, 질문이 OCU를 묻지 않았으면 무시 지시
            _ctx_lower = context.lower()
            _has_ocu_in_ctx = ("ocu" in _ctx_lower or "상대평가" in _ctx_lower
                               or "열린사이버" in _ctx_lower)
            _query_mentions_ocu = ("ocu" in question.lower()
                                   or "열린사이버" in question)
            if _has_ocu_in_ctx and not _query_mentions_ocu:
                parts.append(
                    "[경고 — OCU 혼입] 아래 컨텍스트에 OCU(한국열린사이버대학교) 내용이 "
                    "섞여 있습니다. 이 질문은 OCU를 묻지 않았으므로 "
                    "OCU 관련 부분(상대평가, OCU 홈페이지, OCU 전화번호, "
                    "시스템사용료 등)은 **완전히 건너뛰고** "
                    "부산외대 본교 내용만으로 답하세요.\n"
                )

            parts.append(f"[컨텍스트]\n{context}\n")
            parts.append(f"[질문] {question}")

        return "\n".join(parts)

    # ── One-Pass State Machine 스트리밍 파서 ──────────────────────────────────

    async def _stream_one_pass(
        self, response
    ) -> AsyncGenerator[str, None]:
        """
        One-Pass 스트리밍 State Machine.

        State DRAFT: <ko_draft> 내용을 흐린 인디케이터와 함께 스트리밍.
                     <final_answer> 태그 감지 시 FINAL로 전환.
        State FINAL: <final_answer> 내용을 메인 답변으로 스트리밍.
                     </final_answer> 감지 시 정상 종료.
        Fallback:    스트림 종료까지 <final_answer>가 없으면 KO 초안 그대로 표시.

        Rolling Buffer로 태그가 여러 토큰에 걸쳐 쪼개져 오는 현상을 방어합니다.
        """
        buffer = ""
        state = "DRAFT"
        yield "\u23f3 _규정 원문 분석 중..._\n\n"

        async for line in response.aiter_lines():
            if not line or not line.startswith("data: "):
                continue
            data_str = line[len("data: "):]
            if data_str.strip() == "[DONE]":
                break
            try:
                data = json.loads(data_str)
            except json.JSONDecodeError:
                continue
            token = data["choices"][0].get("delta", {}).get("content", "")
            if not token:
                continue

            buffer += token

            if state == "DRAFT":
                if "<final_answer>" in buffer:
                    yield "\x00CLEAR\x00"
                    state = "FINAL"
                    after_tag = buffer.split("<final_answer>", 1)[1]
                    # 짧은 답변: </final_answer>가 이미 버퍼에 포함된 경우
                    if "</final_answer>" in after_tag:
                        yield after_tag.split("</final_answer>", 1)[0].strip()
                        return
                    buffer = after_tag
                elif len(buffer) > _TAG_HOLD * 2:
                    safe = buffer[:-_TAG_HOLD]
                    safe = (
                        safe
                        .replace("<ko_draft>", "")
                        .replace("</ko_draft>", "")
                    )
                    if safe:
                        yield safe
                    buffer = buffer[-_TAG_HOLD:]

            elif state == "FINAL":
                if "</final_answer>" in buffer:
                    yield buffer.split("</final_answer>", 1)[0].replace("</final_answer>", "")
                    return
                elif len(buffer) > _TAG_HOLD * 2:
                    yield buffer[:-_TAG_HOLD]
                    buffer = buffer[-_TAG_HOLD:]

        # ── Fallback (State 3) ───────────────────────────────────────────────
        if state == "DRAFT":
            # <final_answer> 태그 없이 스트림 종료 → KO 초안 그대로 표시
            if buffer:
                yield (
                    buffer
                    .replace("<ko_draft>", "")
                    .replace("</ko_draft>", "")
                    .replace("<final_answer>", "")
                )
            yield "\n\n_⚠️ 영문 번역이 지연되었습니다. 위 한국어 원문을 참고해 주세요._"
            logger.warning("One-Pass: <final_answer> 태그 미감지 — KO 초안으로 fallback")
        elif state == "FINAL" and buffer:
            # 정상 종료 태그 없이 스트림 끊긴 경우
            yield buffer.replace("</final_answer>", "")

    # ── 공개 인터페이스 ───────────────────────────────────────────────────────

    async def rewrite_query(
        self,
        question: str,
        lang: str = "ko",
        intent: Optional[str] = None,
    ) -> str:
        """저신뢰 컨텍스트 케이스에서 질문을 검색 친화적으로 재작성.

        원칙 2(비용·지연 최적화): 짧은 프롬프트 + max_tokens=80 + 5초 타임아웃.
        실패·공백·지나친 변형 시 원본 반환 (안전 폴백).

        사용 시점: `context_confidence < 0.5`일 때 chat_app에서 1회 호출.
        """
        if not question or not question.strip():
            return question

        if lang == "en":
            system = (
                "You rewrite user questions into a more specific Korean search query "
                "for a Korean university academic-affairs chatbot. "
                "Output only the rewritten Korean query, no explanation, no quotes."
            )
            user = f"Original: {question}\nRewritten Korean query:"
        else:
            system = (
                "당신은 부산외국어대학교 학사 챗봇의 검색 쿼리 재작성기입니다. "
                "주어진 질문을 학사 규정 문서에서 찾기 쉽도록 "
                "핵심 키워드 중심의 한국어 쿼리로 재작성하세요. "
                "재작성된 쿼리만 한 줄로 출력하고, 설명이나 따옴표는 붙이지 마세요."
            )
            user = f"원본: {question}\n재작성된 쿼리:"

        url = f"{self.base_url}/v1/chat/completions"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "max_tokens": 80,
            "temperature": 0.1,
            "top_p": 0.9,
            "think": False,
        }

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(url, json=payload)
                response.raise_for_status()
                data = response.json()
                content = (
                    data.get("choices", [{}])[0]
                    .get("message", {})
                    .get("content", "")
                    .strip()
                )
        except Exception as e:
            logger.warning("쿼리 재작성 실패, 원본 사용: %s", e)
            return question

        # 한 줄만 취하고, 접두사 제거 후 따옴표 제거
        rewritten = content.split("\n")[0].strip()
        for prefix in ("재작성된 쿼리:", "쿼리:", "Rewritten:", "Query:"):
            if rewritten.startswith(prefix):
                rewritten = rewritten[len(prefix):].strip()
        rewritten = rewritten.strip('"').strip("'").strip("`").strip()

        # 지나치게 짧거나 길면 원본 유지
        if not rewritten or len(rewritten) < 3 or len(rewritten) > len(question) * 3:
            return question

        logger.info("쿼리 재작성: '%s' → '%s'", question[:60], rewritten[:60])
        return rewritten

    async def generate(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
        lang: Optional[str] = None,
        matched_terms: Optional[list] = None,
        student_context: Optional[str] = None,
        context_confidence: Optional[float] = None,
        question_type: Optional[str] = None,
        intent: Optional[str] = None,
        entities: Optional[dict] = None,
    ) -> AsyncGenerator[str, None]:
        """스트리밍으로 답변을 생성합니다."""
        url = f"{self.base_url}/v1/chat/completions"

        if lang == "en":
            # KO 컨텍스트에서 학사 용어를 추출하여 Term Guide로 주입
            from app.pipeline.query_analyzer import EnTermMapper
            context_terms = EnTermMapper.get().extract_from_ko_context(context)
            system = self._build_en_system_prompt(
                matched_terms=matched_terms,
                context_terms=context_terms,
                intent=intent,
            )
        else:
            system = SYSTEM_PROMPT

        prompt = self._build_prompt(
            question, context, student_id, question_focus, lang,
            student_context, context_confidence=context_confidence,
            question_type=question_type, entities=entities, intent=intent,
        )

        # Qwen3 계열 thinking 다층 차단 (2026-04-11 LM Studio thinking 버그 대응):
        # - Layer 1: system prompt 최상단 "/no_think" + "thinking 출력 금지" 지시 (별도 편집)
        # - Layer 2a: user prompt 말미에 "/no_think" (Qwen3 공식 trigger)
        # - Layer 2b: payload에 chat_template_kwargs.enable_thinking=False (LM Studio/vLLM)
        # - Layer 2c: payload에 reasoning_effort=none (OpenAI 호환 일부 구현)
        # - Layer 3: 스트림 파싱에서 reasoning_content silent drop (아래 루프)
        # - Layer 4: content 없이 reasoning만 오면 refusal로 대체 (사용자에게 사고 과정 비노출)
        user_content = prompt + "\n\n/no_think"
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_content},
            ],
            "stream": True,
            "max_tokens": settings.llm.max_tokens,
            "temperature": settings.llm.temperature,
            "top_p": settings.llm.top_p,
            "repeat_penalty": settings.llm.repeat_penalty,
            # Ollama 전용
            "think": False,
            # LM Studio / vLLM (Qwen3 chat template enable_thinking 끄기)
            "chat_template_kwargs": {"enable_thinking": False},
            # OpenAI 호환 일부 구현 (LM Studio 최신)
            "reasoning_effort": "none",
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()

                    if False:  # One-Pass 비활성화 — skip-translate로 전환
                        async for chunk in self._stream_one_pass(response):
                            yield chunk
                    else:
                        # KO 기존 흐름
                        thinking_detected = False
                        content_started = False
                        reasoning_len = 0
                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            data_str = line[len("data: "):]
                            if data_str.strip() == "[DONE]":
                                break
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})

                            # Layer 3: reasoning_content는 완전히 폐기 (사용자에게 노출 X)
                            # Qwen3 thinking이 새어 나오면 로그만 남기고 토큰은 drop.
                            rc = delta.get("reasoning_content", "")
                            if rc:
                                thinking_detected = True
                                reasoning_len += len(rc)
                                # 사용자 측에 "분석 중" 마커도 더 이상 노출하지 않음.
                                # 이유: LM Studio의 thinking은 수백~수천 자라 latency가 길어져서
                                # UI에 "분석 중"이 뜨면 오히려 혼란. content가 오기 시작하면 바로 출력.
                                continue

                            token = delta.get("content", "")
                            if token:
                                if not content_started:
                                    content_started = True
                                    yield "\x00CLEAR\x00"
                                yield token

                        # Layer 4: thinking만 있고 content가 비었으면 refusal로 대체
                        # (reasoning_buf를 사용자에게 보여주던 이전 fallback은 제거)
                        if thinking_detected and not content_started:
                            logger.error(
                                "LLM thinking-only 응답 감지 — refusal로 대체. "
                                "reasoning_len=%d. SYSTEM_PROMPT의 /no_think + "
                                "chat_template_kwargs.enable_thinking=False 다층 차단이 "
                                "모두 실패한 상황. LM Studio 버전 업데이트나 모델 변경 검토 필요.",
                                reasoning_len,
                            )
                            yield "\x00CLEAR\x00"
                            yield (
                                "죄송합니다. 답변 생성 중 문제가 발생했습니다. "
                                "학사지원팀(051-509-5182)에 문의하시기 바랍니다."
                            )

        except httpx.ConnectError:
            logger.error("Ollama 서버 연결 실패. Ollama를 실행해주세요.")
            yield "Ollama 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요."
        except Exception as e:
            logger.error(f"답변 생성 실패: {e}")
            yield f"답변 생성 중 오류가 발생했습니다: {e}"

    async def generate_full(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
        lang: Optional[str] = None,
        matched_terms: Optional[list] = None,
        context_lang: Optional[str] = None,
        student_context: Optional[str] = None,
        context_confidence: Optional[float] = None,
        question_type: Optional[str] = None,
        intent: Optional[str] = None,
        entities: Optional[dict] = None,
    ) -> str:
        """전체 답변을 한 번에 반환합니다 (비스트리밍).

        EN One-Pass: CLEAR 신호 기준으로 ko_draft를 제거하고 final_answer만 반환.
        KO: 기존 방식 그대로.

        Fix D (2026-04-11): 생성 후 AnswerUnit 누락 검사.
        질문이 요구하는 단위(URL/날짜/학점 등)가 생성 답변에 빠졌으면
        컨텍스트에서 그 단위를 찾아 "[참고] ..." 블록으로 주입한다.
        entities.department가 있으면 해당 학과 행에서만 팩트를 뽑음.
        """
        parts = []
        async for token in self.generate(
            question, context, student_id, question_focus, lang, matched_terms,
            student_context, context_confidence=context_confidence,
            question_type=question_type, intent=intent, entities=entities,
        ):
            parts.append(token)
        full = "".join(parts)

        # EN One-Pass: CLEAR 이후(final_answer)만 추출
        if "\x00CLEAR\x00" in full:
            full = full.split("\x00CLEAR\x00", 1)[1]
        else:
            # Fallback(CLEAR 없음): thinking 마커만 제거
            full = full.replace("\u23f3 _규정 원문 분석 중..._\n\n", "")

        full = full.strip()

        # 2026-04-11 수정:
        # Step 2-C: Answer-Context Consistency Verifier (버그 #7)
        # Step 3:   refusal 응답엔 fill_from_context 건너뛰기 (버그 #5)
        if lang != "en" and full:
            try:
                from app.pipeline.answer_units import (
                    fill_from_context, verify_answer_against_context,
                    verify_completeness,
                )
                from app.pipeline.response_validator import ResponseValidator

                # Step 3: refusal 응답이면 post-processing 전체 건너뛰기
                # 이유: "관련 정보를 찾을 수 없습니다" 같은 본문에 "[참고] 시간: 10:00" 같은
                # 컨텍스트 잔재가 주입되면 사용자가 혼란. 환각 검증도 의미 없음.
                _validator = ResponseValidator()
                if _validator._is_no_context_response(full):
                    return full

                # Step 2-C: 답변의 값(URL/금액/성적)이 컨텍스트에 존재하는지 검증
                # 환각 감지 시 refusal fallback으로 대체 (사용자에 잘못된 정보 전달 방지).
                ok, reason = verify_answer_against_context(full, context)
                if not ok:
                    logger.warning("answer-context mismatch detected: %s", reason)
                    return (
                        "제공된 자료에서 해당 내용을 정확히 확인하지 못했습니다. "
                        "학사지원팀(051-509-5182)에 문의하시기 바랍니다."
                    )

                # Phase 3 Step 3 (2026-04-12): 답변 완전성 가드 (g04 bi-value).
                # "복수전공 최소 이수학점" 같이 질문이 두 값(주/제2)을 요구하는 경우
                # 답변이 한 값만 담고 있으면 fill_from_context로 누락 값을 보완.
                if not verify_completeness(question, full, context):
                    logger.debug(
                        "verify_completeness failed (bi-value partial), "
                        "fill_from_context로 보완 시도"
                    )
                    # fill_from_context가 credit 누락분을 context에서 찾아 주입

                # Fix D: 누락 단위 감지 & 주입 (refusal이 아니고 검증 통과한 경우만)
                target_entity = (entities or {}).get("department") if entities else None
                full = fill_from_context(
                    question, full, context, target_entity=target_entity,
                )
            except Exception as e:
                logger.debug("post-processing 실패, 원본 유지: %s", e)

        return full

    async def health_check(self) -> bool:
        """Ollama 서버 상태를 확인합니다."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False
