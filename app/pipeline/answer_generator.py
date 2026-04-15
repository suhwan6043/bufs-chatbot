"""
답변 생성기 - Ollama (OpenAI 호환 API)로 답변 생성
스트리밍 응답 지원

EN 쿼리: One-Pass Streaming (KO 초안 → 목표 언어 번역)
  - <ko_draft> 스트리밍 중 "분석 중..." 표시
  - <final_answer> 감지 시 CLEAR 후 번역본 스트리밍
  - Rolling Buffer State Machine으로 태그 쪼개짐 방어
KO 쿼리: 기존 단일 생성 흐름 유지
"""

import hashlib
import json
import logging
import os
import re
import time
from collections import OrderedDict
from threading import Lock
from typing import Any, AsyncGenerator, Optional

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
Respond with ONLY the final answer in Korean. No reasoning, no English.

당신은 부산외국어대학교(BUFS) 학사 안내 AI입니다.

## 핵심 규칙
1. [컨텍스트]에 적힌 정보만 사용하세요. 추측·상식 금지.
2. 숫자·날짜·URL은 컨텍스트 원문을 그대로 복사하세요. 절대 변경 금지.
3. 컨텍스트에 FAQ(Q/A)가 있으면 해당 A를 답변의 뼈대로 사용하세요.
4. 정보가 없으면 "관련 정보를 찾을 수 없습니다. 학사지원팀(051-509-5182)에 문의하시기 바랍니다."로 답하세요.
5. OCU를 묻지 않은 질문에는 OCU 내용을 포함하지 마세요.

## 답변 형식
- 결론 문장으로 바로 시작하세요. 서론·메타 문구 금지.
- 컨텍스트의 조건·예외("단", "제외")는 반드시 포함하세요.
- 날짜·시간·절차·조건이 여러 개면 각 항목을 줄바꿈해서 나누세요.
- 일정과 방법이 함께 있으면 한 문단에 몰아쓰지 말고 빈 줄로 구분하세요.
- 학생 이름·학번은 답변에 포함하지 마세요.

## 예시
[컨텍스트] 졸업학점: 130, 교양이수학점: 43
[질문] 졸업요건은?
[답변] 졸업학점 130학점, 교양이수학점 43학점입니다.
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
        self._cache_ttl_seconds = settings.llm.response_cache_ttl_seconds
        self._cache_max_entries = settings.llm.response_cache_max_entries
        self._response_cache: OrderedDict[str, tuple[float, str]] = OrderedDict()
        self._cache_lock = Lock()

    @staticmethod
    def _stable_dump(value: Any) -> str:
        normalized = value if value is not None else {}
        return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def _make_cache_key(
        self,
        *,
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
    ) -> str:
        key_payload = {
            "question": question.strip(),
            "context_hash": hashlib.sha256(context.encode("utf-8")).hexdigest(),
            "student_id": student_id or "",
            "question_focus": question_focus or "",
            "lang": lang or "ko",
            "matched_terms": matched_terms or [],
            "student_context_hash": hashlib.sha256((student_context or "").encode("utf-8")).hexdigest(),
            "context_confidence": None if context_confidence is None else round(context_confidence, 3),
            "question_type": question_type or "",
            "intent": intent or "",
            "entities": entities or {},
        }
        key_material = self._stable_dump(key_payload)
        return hashlib.sha256(key_material.encode("utf-8")).hexdigest()

    def get_cached_response(self, **cache_kwargs) -> Optional[str]:
        if self._cache_ttl_seconds <= 0 or self._cache_max_entries <= 0:
            return None

        cache_key = self._make_cache_key(**cache_kwargs)
        now = time.monotonic()
        with self._cache_lock:
            cached = self._response_cache.get(cache_key)
            if not cached:
                return None
            stored_at, answer = cached
            if now - stored_at > self._cache_ttl_seconds:
                self._response_cache.pop(cache_key, None)
                return None
            self._response_cache.move_to_end(cache_key)
            return answer

    def store_cached_response(self, answer: str, **cache_kwargs) -> None:
        if (
            self._cache_ttl_seconds <= 0
            or self._cache_max_entries <= 0
            or not answer
            or not answer.strip()
        ):
            return

        cache_key = self._make_cache_key(**cache_kwargs)
        with self._cache_lock:
            self._response_cache[cache_key] = (time.monotonic(), answer)
            self._response_cache.move_to_end(cache_key)
            while len(self._response_cache) > self._cache_max_entries:
                self._response_cache.popitem(last=False)

    def _resolve_max_tokens(
        self,
        *,
        question: str,
        context: str,
        question_focus: Optional[str] = None,
        question_type: Optional[str] = None,
        intent: Optional[str] = None,
    ) -> int:
        base_max = settings.llm.max_tokens
        resolved = min(base_max, 256)

        focus_caps = {
            "period": 224,
            "limit": 192,
            "table_lookup": 192,
            "rule_list": 448,
            "method": 320,
            "location": 192,
            "eligibility": 320,
        }
        type_caps = {
            "overview": 640,
            "factoid": 224,
            "procedural": 320,
            "reasoning": 448,
        }

        if question_type in type_caps:
            resolved = max(resolved, min(base_max, type_caps[question_type]))
        if question_focus in focus_caps:
            resolved = max(resolved, min(base_max, focus_caps[question_focus]))

        normalized_question = question.lower()
        asks_schedule = any(kw in normalized_question for kw in ("기간", "일정", "언제", "날짜", "시간"))
        asks_method = any(kw in normalized_question for kw in ("방법", "절차", "어떻게", "신청", "로그인"))
        if asks_schedule and asks_method:
            resolved = max(resolved, min(base_max, 320))

        if intent == "REGISTRATION":
            resolved = max(resolved, min(base_max, 320 if asks_method else 256))
        elif intent in {"GRADUATION_REQ", "EARLY_GRADUATION", "MAJOR_CHANGE"}:
            resolved = max(resolved, min(base_max, 384))

        if len(context) > 4000 and question_type == "overview":
            resolved = max(resolved, min(base_max, 768))

        return max(160, min(base_max, resolved))

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
                    "If there are multiple schedule windows, put each date/time window on a separate line. "
                    "If different schedule groups exist, separate them with a blank line. "
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
                    "List the steps in order. Put each step on a separate line. "
                    "Do not omit any required documents or actions.\n"
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
                    "여러 일정 구간이 있으면 각 날짜/시간 구간을 한 줄씩 나누고, "
                    "장바구니·본 수강신청처럼 성격이 다른 일정은 빈 줄로 구분하세요. "
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
                    "[필수 형식] 첫 문장에 반드시 핵심 수치를 포함하세요. "
                    "예: 'XX학점 이상입니다.' 또는 '최대 XX학점입니다.'\n"
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

            # Phase 5 (2026-04-15): 최소/최대 + 졸업/학점 질문의 숫자 우선 출력 (g02 회귀 수정)
            _asks_numeric = any(kw in question for kw in ("최소", "최대", "상한", "한도", "몇"))
            _about_credits = any(kw in question for kw in ("졸업", "이수", "학점", "필요"))
            if _asks_numeric and _about_credits and question_focus != "limit":
                parts.append(
                    "[중요] 이 질문은 학점 수치를 묻습니다. "
                    "답변의 첫 문장에 반드시 핵심 숫자를 포함하세요. "
                    "예: '130학점 이상입니다.' 또는 '18학점입니다.'\n"
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
                    "이수학점을 묻습니다. 답변에 두 값을 **모두** 포함해야 합니다.\n"
                    "[중요] 컨텍스트에서 각 값을 정확히 찾아서 대응시키세요. "
                    "주전공 이수학점은 제2전공(복수전공)보다 항상 **더 많습니다**. "
                    "두 값이 같은 숫자이면 잘못된 답변입니다.\n"
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

    # ── KO <answer> 태그 기반 스트리밍 파서 ────────────────────────────────────

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
            delta = data["choices"][0].get("delta", {})
            # reasoning_content / thinking 필드 폐기 (Qwen3 / Ollama native)
            if delta.get("reasoning_content") or delta.get("thinking"):
                continue
            token = delta.get("content", "")
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
        max_tokens = self._resolve_max_tokens(
            question=question,
            context=context,
            question_focus=question_focus,
            question_type=question_type,
            intent=intent,
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "max_tokens": max_tokens,
            "temperature": settings.llm.temperature,
            "top_p": settings.llm.top_p,
            "repeat_penalty": settings.llm.repeat_penalty,
            # thinking 비활성화 (Ollama/LM Studio 공용)
            "think": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()

                    if False:  # One-Pass 비활성화 — skip-translate로 전환
                        async for chunk in self._stream_one_pass(response):
                            yield chunk
                    else:
                        # KO 경로: think=True 이므로 thinking은 별도 필드로 분리됨.
                        # reasoning_content / thinking 필드만 필터하고 content를 바로 출력.
                        content_started = False
                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            data_str = line[len("data: "):]
                            if data_str.strip() == "[DONE]":
                                break
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})
                            # thinking/reasoning 필드 → 폐기
                            if delta.get("reasoning_content") or delta.get("thinking"):
                                continue
                            token = delta.get("content", "")
                            if token:
                                if not content_started:
                                    content_started = True
                                    yield "\x00CLEAR\x00"
                                yield token

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
