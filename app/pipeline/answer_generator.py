"""
답변 생성기 - LM Studio (OpenAI 호환 API)로 답변 생성
스트리밍 응답 지원
"""

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """당신은 부산외국어대학교(BUFS) 학사 안내 AI입니다.

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

## 답변 구성
- 첫 문장: 질문에 대한 직접적 결론 (예/아니요, 숫자, 날짜, 방법명 등).
- 이어지는 1~4문장: 컨텍스트에 있는 필수 조건·예외·결과·이후 절차. 반드시 포함.
- 절차·단계·목록(-)으로 정리할 수 있는 정보는 글머리 기호를 사용.
- 서론("아래와 같습니다", "다음과 같이 안내드립니다")·메타 문구는 쓰지 마세요.
- 컨텍스트가 질문과 완전히 무관하면 "학사지원팀(051-509-5182)에 문의하시기 바랍니다."로만 답하세요.

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

## 간결성 vs 완전성 균형
- 간결성은 **메타 문구·서론 제거**이지 사실 정보 삭제가 아닙니다.
- 컨텍스트의 조건·예외·결과를 "간결성"을 이유로 빠뜨리면 답이 틀린 것으로 간주됩니다.
- 한 문장으로 충분하면 한 문장, 여러 조건이 있으면 모두 포함된 4문장까지 허용됩니다.
"""


class AnswerGenerator:
    """
    [역할] LM Studio (OpenAI 호환 API)로 답변 생성
    [핵심] SSE 스트리밍 응답
    [주의] temperature=0.1 (사실 정확성 최우선)
    """

    def __init__(self):
        self.base_url = settings.llm.base_url
        self.model = settings.llm.model
        self.timeout = settings.llm.timeout

    def _build_prompt(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
    ) -> str:
        """LLM에 전달할 프롬프트를 구성합니다."""
        parts = []

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
                "핵심 숫자 하나로 먼저 답하고, 예외 조건은 간략히만 언급하세요. "
                "질문에서 묻지 않은 다른 조건까지 나열하지 마세요.\n"
            )

        parts.append(f"[컨텍스트]\n{context}\n")
        parts.append(f"[질문] {question}")

        return "\n".join(parts)

    async def generate(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
    ) -> AsyncGenerator[str, None]:
        """스트리밍으로 답변을 생성합니다."""
        prompt = self._build_prompt(question, context, student_id, question_focus)
        url = f"{self.base_url}/v1/chat/completions"

        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "stream": True,
            "max_tokens": settings.llm.max_tokens,
            "temperature": settings.llm.temperature,
            "top_p": settings.llm.top_p,
            "repeat_penalty": settings.llm.repeat_penalty,
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", url, json=payload
                ) as response:
                    response.raise_for_status()
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

                        # thinking 시작 시 UI에 표시
                        if "reasoning_content" in delta and not thinking and not content_started:
                            thinking = True
                            yield "\u23f3 _분석 중..._\n\n"

                        # 실제 답변 토큰
                        token = delta.get("content", "")
                        if token:
                            if not content_started:
                                content_started = True
                                # thinking 표시를 지우고 답변 시작
                                yield "\x00CLEAR\x00"
                            yield token
        except httpx.ConnectError:
            logger.error(
                "LM Studio 서버 연결 실패. "
                "LM Studio를 실행하고 모델을 로드해주세요."
            )
            yield "LM Studio 서버에 연결할 수 없습니다. 서버가 실행 중인지 확인해주세요."
        except Exception as e:
            logger.error(f"답변 생성 실패: {e}")
            yield f"답변 생성 중 오류가 발생했습니다: {e}"

    async def generate_full(
        self,
        question: str,
        context: str,
        student_id: Optional[str] = None,
        question_focus: Optional[str] = None,
    ) -> str:
        """전체 답변을 한 번에 반환합니다 (비스트리밍)."""
        parts = []
        async for token in self.generate(question, context, student_id, question_focus):
            parts.append(token)
        return "".join(parts)

    async def health_check(self) -> bool:
        """LM Studio 서버 상태를 확인합니다."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/v1/models")
                return resp.status_code == 200
        except Exception:
            return False
