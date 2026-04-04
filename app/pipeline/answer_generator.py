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

# ── EN One-Pass 시스템 프롬프트 ───────────────────────────────────────────────
EN_ONE_PASS_SYSTEM_PROMPT = """\
You are an official academic administration AI chatbot for university students.
Answer the user's query based ONLY on the provided [Context].

[WORKFLOW INSTRUCTIONS]
Process the answer in two steps using the exact XML tags below:

Step 1: Write a concise Korean draft inside <ko_draft> tags.
- Cover all facts, dates, and conditions without conversational fillers.
- Aim for 3-5 sentences; never omit conditional clauses or cohort-specific rules.

Step 2: Translate the draft into {target_lang} inside <final_answer> tags.
- Use the exact English term names from [Mandatory Terms] if provided.

[STRICT PRECISION RULES]
1. Exact Extraction: Copy all numbers, dates, times, URLs, and proper nouns EXACTLY.
2. Conditional Information: Include any conditional clauses or exceptions \
(e.g., "However", "Except for", "Provided that").
3. Date and Time Precision: Explicitly include specific times if mentioned.
4. Period Precision: State both the exact start date and the end date.
5. Student Type Rules: If the context specifies different rules by student type \
(e.g., domestic vs. international, transfer students), list each group separately.
6. Cohort-Year Rules: If rules differ by enrollment year, state which cohort \
each rule applies to and list them all.
7. No Speculation: If the context contains no relevant answer, output exactly:
   <ko_draft>관련 정보를 찾을 수 없습니다.</ko_draft>
   <final_answer>Please contact the Academic Affairs Office at +82-51-509-5182.</final_answer>

{mandatory_terms_section}
[Output Format]
<ko_draft>
(Concise Korean draft here)
</ko_draft>
<final_answer>
(Official {target_lang} translation here)
</final_answer>\
"""

# ── KO 시스템 프롬프트 (기존 유지) ──────────────────────────────────────────
SYSTEM_PROMPT = """당신은 부산외국어대학교(BUFS) 학사 안내 AI입니다.

## 절대 규칙
1. 아래 [컨텍스트]에 있는 정보만으로 답변하세요.
2. 컨텍스트에 질문과 관련된 내용이 조금이라도 있으면 반드시 그것을 활용해 답변하세요. "확인되지 않는 정보입니다"는 컨텍스트가 완전히 비어있거나 질문과 전혀 무관한 내용뿐일 때만 사용하세요.
3. 숫자(학점·날짜·시간·금액·학점수), URL, 고유명사는 컨텍스트 원문을 그대로 복사하세요. 절대 바꾸거나 추측하지 마세요.
4. 학번별로 규정이 다를 경우 해당 학번 기준임을 명시하세요.
5. "단", "다만", "제외", "예외" 등 조건부 정보가 있으면 반드시 포함하세요.

## 답변 형식
- 질문에 대한 핵심 답을 첫 문장에 바로 쓰세요.
- 불필요한 서론·반복·부연 설명 없이 간결하게 답하세요.
- 컨텍스트에 있는 정보를 빠뜨리지 말되, 없는 정보를 만들어내지 마세요.
- 컨텍스트에 질문에 대한 답이 전혀 없으면, 추측하지 말고 "학사지원팀(051-509-5182)에 문의하시기 바랍니다."로 안내하세요.
- 목록이나 조건이 여러 개인 경우, 글머리 기호(-)를 사용하여 가독성 있게 정리하세요.

## 정확성 강화 규칙
- 날짜를 답할 때 시간 정보가 컨텍스트에 있으면 반드시 함께 포함하세요. (예: "3월 2일 오전 10시")
- 기간을 답할 때 시작일과 종료일을 모두 포함하세요. (예: "2월 9일~2월 12일")
- 1학년/재학생 등 대상별로 다른 기간이 있으면 모두 나열하세요.
- 컨텍스트에 명시적 숫자가 없으면 구체적 학점/과목 수를 절대 추측하지 마세요.
- "~일 것으로 보입니다", "~으로 추정됩니다" 같은 추측 표현을 사용하지 마세요.
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
        target_lang: str = "English",
        matched_terms: Optional[list] = None,
    ) -> str:
        """EN One-Pass 시스템 프롬프트를 구성합니다."""
        if matched_terms:
            term_list = "\n".join(
                f"- {t['en']} ({t['ko']})" for t in matched_terms
            )
            mandatory_section = f"[Mandatory Terms]\n{term_list}\n"
        else:
            mandatory_section = ""
        return EN_ONE_PASS_SYSTEM_PROMPT.format(
            target_lang=target_lang,
            mandatory_terms_section=mandatory_section,
        )

    def _build_prompt(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
        lang: Optional[str] = None,
    ) -> str:
        """LLM에 전달할 유저 프롬프트를 구성합니다."""
        parts = []

        if lang == "en":
            parts.append(f"[Context]\n{context}\n")
            parts.append(f"[Question] {question}")
        else:
            if student_id:
                parts.append(f"[학번] {student_id}학번 기준으로 답변하세요.\n")

            if question_focus == "period":
                parts.append(
                    "[주목] 이 질문은 날짜·기간·시간을 묻습니다. "
                    "컨텍스트에서 날짜, 시간, 기간 정보를 찾아 답하세요. "
                    "학점·수치가 아닌 날짜·기간으로 답해야 합니다.\n"
                )
            elif question_focus == "limit":
                parts.append(
                    "[주목] 이 질문은 학점·횟수·금액 등 한도·수치를 묻습니다. "
                    "컨텍스트에서 최대·최소 값을 찾아 답하세요.\n"
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

    async def generate(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
        lang: Optional[str] = None,
        matched_terms: Optional[list] = None,
    ) -> AsyncGenerator[str, None]:
        """스트리밍으로 답변을 생성합니다."""
        url = f"{self.base_url}/v1/chat/completions"

        if lang == "en":
            system = self._build_en_system_prompt(
                target_lang="English",
                matched_terms=matched_terms,
            )
        else:
            system = SYSTEM_PROMPT

        prompt = self._build_prompt(
            question, context, student_id, question_focus, lang
        )

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "max_tokens": settings.llm.max_tokens,
            "temperature": settings.llm.temperature,
            "top_p": settings.llm.top_p,
            "repeat_penalty": settings.llm.repeat_penalty,
            "think": False,  # qwen3 thinking 비활성화
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream("POST", url, json=payload) as response:
                    response.raise_for_status()

                    if lang == "en":
                        # One-Pass State Machine
                        async for chunk in self._stream_one_pass(response):
                            yield chunk
                    else:
                        # KO 기존 흐름
                        thinking = False
                        content_started = False
                        async for line in response.aiter_lines():
                            if not line or not line.startswith("data: "):
                                continue
                            data_str = line[len("data: "):]
                            if data_str.strip() == "[DONE]":
                                break
                            data = json.loads(data_str)
                            delta = data["choices"][0].get("delta", {})

                            if "reasoning_content" in delta and not thinking and not content_started:
                                thinking = True
                                yield "\u23f3 _분석 중..._\n\n"

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
    ) -> str:
        """전체 답변을 한 번에 반환합니다 (비스트리밍).

        EN One-Pass: CLEAR 신호 기준으로 ko_draft를 제거하고 final_answer만 반환.
        KO: 기존 방식 그대로.
        """
        parts = []
        async for token in self.generate(
            question, context, student_id, question_focus, lang, matched_terms
        ):
            parts.append(token)
        full = "".join(parts)

        # EN One-Pass: CLEAR 이후(final_answer)만 추출
        if "\x00CLEAR\x00" in full:
            full = full.split("\x00CLEAR\x00", 1)[1]
        else:
            # Fallback(CLEAR 없음): thinking 마커만 제거
            full = full.replace("\u23f3 _규정 원문 분석 중..._\n\n", "")

        return full.strip()

    async def health_check(self) -> bool:
        """Ollama 서버 상태를 확인합니다."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False
