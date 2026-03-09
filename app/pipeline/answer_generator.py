"""
답변 생성기 - Ollama의 EXAONE 3.5 7.8B로 답변 생성
스트리밍 응답 지원, 2048 토큰 컨텍스트 제한
"""

import json
import logging
from typing import AsyncGenerator, Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """당신은 부산외국어대학교(BUFS) 학사 안내 AI입니다.

## 절대 규칙
1. 아래 [컨텍스트]에 있는 정보만으로 답변하세요.
2. 컨텍스트에 숫자(학점·날짜·시간·금액)가 하나라도 있으면 반드시 그 값을 바탕으로 답변하세요. 숫자가 있을 때는 "확인되지 않는 정보입니다"를 절대 사용하지 마세요. 컨텍스트가 완전히 비어있거나 질문과 전혀 무관한 내용뿐일 때만 "확인되지 않는 정보입니다"를 사용하세요.
3. 모든 숫자(학점, 날짜)는 컨텍스트 원문 그대로 사용하세요.
4. 답변 끝에 [출처: 페이지 번호]를 명시하세요.
5. 학번별로 규정이 다를 경우 반드시 해당 학번 기준임을 명시하세요.
6. 조건이 여러 개이거나 학번에 따라 규정이 다를 경우, 모든 조건을 빠짐없이 나열하세요.
"""


class AnswerGenerator:
    """
    [역할] Ollama의 EXAONE 3.5 7.8B로 답변 생성
    [핵심] 스트리밍 응답, 2048 토큰 컨텍스트 제한
    [주의] num_ctx=2048 고정, temperature=0.1 (사실 정확성 최우선)
    """

    def __init__(self):
        self.base_url = settings.ollama.base_url
        self.model = settings.ollama.model
        self.timeout = settings.ollama.timeout

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
                "컨텍스트에서 최대·최소 값을 찾아 답하세요.\n"
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
        url = f"{self.base_url}/api/generate"

        payload = {
            "model": self.model,
            "prompt": prompt,
            "system": SYSTEM_PROMPT,
            "stream": True,
            "options": {
                "num_ctx": settings.ollama.num_ctx,
                "temperature": settings.ollama.temperature,
                "top_p": settings.ollama.top_p,
                "repeat_penalty": settings.ollama.repeat_penalty,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                async with client.stream(
                    "POST", url, json=payload
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if line:
                            data = json.loads(line)
                            token = data.get("response", "")
                            if token:
                                yield token
                            if data.get("done", False):
                                break
        except httpx.ConnectError:
            logger.error(
                "Ollama 서버 연결 실패. "
                "'ollama serve' 명령으로 서버를 시작하세요."
            )
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
    ) -> str:
        """전체 답변을 한 번에 반환합니다 (비스트리밍)."""
        parts = []
        async for token in self.generate(question, context, student_id, question_focus):
            parts.append(token)
        return "".join(parts)

    async def health_check(self) -> bool:
        """Ollama 서버 상태를 확인합니다."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.base_url}/api/tags")
                return resp.status_code == 200
        except Exception:
            return False
