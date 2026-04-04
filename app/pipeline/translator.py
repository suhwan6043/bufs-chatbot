"""
컨텍스트 번역기 (DKM-RAG 방식)

역할:
  - 병합된 한국어 컨텍스트를 쿼리 언어로 번역
  - target_lang == "ko"이거나 컨텍스트에 한국어가 없으면 번역 생략 (pass-through)
  - 번역 후 컨텍스트를 EXAONE에 전달 → 영어/다국어 답변 품질 향상

백엔드 선택 (TRANSLATOR_BACKEND 환경변수):
  - "m2m100" (기본): Meta M2M-100 418M — CPU 전용, MIT 라이선스
      · GPU 경합 없음 → Generator가 GPU를 독점 사용
      · 단일 모델로 100개 언어 지원
      · CPU 속도 ~1,200ms
  - "ollama": Qwen2.5:7b — GPU 실행, Apache 2.0 라이선스
      · 학사 용어 번역 품질 높음 (LLM 기반)
      · Generator와 GPU 공유 → 경합 발생 가능

지원 언어 확장:
  - SUPPORTED_TARGET_LANGS 에 ISO 언어 코드 추가만으로 확장 가능
  - M2M-100은 100개 언어 지원 (추가 모델 다운로드 불필요)
"""

import asyncio
import logging
import re
from functools import lru_cache
from typing import Optional

import httpx

from app.config import settings

logger = logging.getLogger(__name__)

# 번역이 필요한 언어 코드 집합 (source 언어는 항상 한국어)
SUPPORTED_TARGET_LANGS: set[str] = {"en", "ja", "zh", "vi", "fr", "de", "es"}

# 언어 코드 → Ollama 번역 프롬프트용 언어 이름
_LANG_NAMES: dict[str, str] = {
    "en": "English",
    "ja": "Japanese",
    "zh": "Chinese (Simplified)",
    "vi": "Vietnamese",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
}

_KO_PATTERN = re.compile(r"[가-힣]")


@lru_cache(maxsize=1)
def _load_m2m100():
    """M2M-100 모델을 최초 호출 시 한 번만 로드합니다 (lazy loading)."""
    try:
        from transformers import M2M100ForConditionalGeneration, M2M100Tokenizer
        model_name = settings.translator.m2m100_model
        device = settings.translator.device
        logger.info("M2M-100 모델 로드 중: %s (device=%s)", model_name, device)
        tokenizer = M2M100Tokenizer.from_pretrained(model_name)
        model = M2M100ForConditionalGeneration.from_pretrained(model_name)
        model = model.to(device)
        model.eval()
        logger.info("M2M-100 모델 로드 완료")
        return tokenizer, model
    except Exception as e:
        logger.error("M2M-100 모델 로드 실패: %s", e)
        raise


class ContextTranslator:
    """
    병합된 컨텍스트를 목표 언어로 번역합니다.

    백엔드는 TRANSLATOR_BACKEND 환경변수로 선택합니다.
      - "m2m100" (기본): CPU 전용, GPU 경합 없음
      - "ollama":         Qwen2.5:7b, GPU 실행

    사용 예시:
        translator = ContextTranslator()
        translated = await translator.translate_if_needed(context, lang="en")
    """

    def __init__(self) -> None:
        # settings.translator/ollama가 없는 환경(팀원 config)을 위한 폴백
        import os
        if hasattr(settings, "translator"):
            self._cfg = settings.translator
        else:
            from types import SimpleNamespace
            self._cfg = SimpleNamespace(
                enabled=os.getenv("TRANSLATOR_ENABLED", "true").lower() == "true",
                backend=os.getenv("TRANSLATOR_BACKEND", "ollama"),
                model=os.getenv("TRANSLATOR_MODEL", "qwen2.5:7b"),
                num_ctx=int(os.getenv("TRANSLATOR_NUM_CTX", "1024")),
                temperature=0.1,
                timeout=int(os.getenv("TRANSLATOR_TIMEOUT", "120")),
                m2m100_model=os.getenv("TRANSLATOR_M2M100_MODEL", "facebook/m2m100_418M"),
                device=os.getenv("TRANSLATOR_DEVICE", "cpu"),
            )
        if hasattr(settings, "ollama"):
            self._base_url = settings.ollama.base_url
        else:
            self._base_url = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

    def warmup(self) -> None:
        """
        M2M-100 모델을 미리 로드합니다 (cold start 방지).

        backend="m2m100" 일 때만 동작합니다.
        서버 시작 시 백그라운드 스레드에서 호출하면 첫 번째 사용자 요청 전에
        모델 로드(~1.6GB 다운로드 포함)가 완료됩니다.
        """
        if self._cfg.backend != "m2m100" or not self._cfg.enabled:
            return
        try:
            _load_m2m100()
        except Exception as e:
            logger.error("M2M-100 워밍업 실패: %s", e)

    def _has_korean(self, text: str) -> bool:
        return bool(_KO_PATTERN.search(text))

    # ── 공통 진입점 ──────────────────────────────────────────────────────────

    async def translate_if_needed(
        self,
        context: str,
        target_lang: str,
    ) -> str:
        """
        번역이 필요한 경우에만 번역하여 반환합니다.

        번역 생략 조건:
          1. settings.translator.enabled == False
          2. target_lang 이 SUPPORTED_TARGET_LANGS 에 없음 (ko 포함)
          3. 컨텍스트에 한국어 문자가 없음 (이미 번역됐거나 영어 문서)
        """
        if not context.strip():
            return context

        if not self._cfg.enabled:
            return context

        if target_lang not in SUPPORTED_TARGET_LANGS:
            return context  # "ko" 또는 미지원 언어 → pass-through

        if not self._has_korean(context):
            return context  # 이미 번역된 컨텍스트 → pass-through

        if self._cfg.backend == "ollama":
            return await self._call_ollama(context, target_lang)
        else:
            # M2M-100: CPU 블로킹 작업을 스레드풀에서 실행
            return await asyncio.get_event_loop().run_in_executor(
                None, self._call_m2m100, context, target_lang
            )

    # ── M2M-100 백엔드 ────────────────────────────────────────────────────────

    def _call_m2m100(self, text: str, target_lang: str) -> str:
        """M2M-100 418M으로 번역합니다 (동기, CPU). 실패 시 원문 반환."""
        import time
        t0 = time.perf_counter()
        try:
            tokenizer, model = _load_m2m100()
            tokenizer.src_lang = "ko"
            inputs = tokenizer(
                text,
                return_tensors="pt",
                truncation=True,
                max_length=512,
            ).to(self._cfg.device)
            forced_bos = tokenizer.get_lang_id(target_lang)
            output_ids = model.generate(
                **inputs,
                forced_bos_token_id=forced_bos,
                num_beams=4,
                max_new_tokens=512,
                repetition_penalty=self._cfg.m2m100_repetition_penalty,
                no_repeat_ngram_size=self._cfg.m2m100_no_repeat_ngram,
            )
            translated = tokenizer.decode(output_ids[0], skip_special_tokens=True)
            if not translated:
                logger.warning("M2M-100 번역 결과가 비어있음 — 원문 사용")
                return text
            elapsed = (time.perf_counter() - t0) * 1000
            logger.debug(
                "M2M-100 번역 완료 (target=%s, chars: %d→%d, %.0fms)",
                target_lang, len(text), len(translated), elapsed,
            )
            return translated
        except Exception as e:
            logger.error("M2M-100 번역 실패: %s — 원문 컨텍스트 사용", e)
            return text

    # ── Ollama 백엔드 ─────────────────────────────────────────────────────────

    def _build_ollama_prompt(self, text: str, target_lang: str) -> str:
        lang_name = _LANG_NAMES.get(target_lang, target_lang.upper())
        return (
            f"Translate the following Korean university document excerpt to {lang_name}.\n"
            "Rules:\n"
            "- Keep all numbers, dates, credit values, and percentages exactly as-is.\n"
            "- Translate Korean academic terms to standard equivalents "
            "(e.g. 졸업요건→Graduation Requirements, 수강신청→Course Registration, "
            "학점→credits, 복수전공→Double Major, 부전공→Minor, 휴학→Leave of Absence).\n"
            "- Output the translation only. No explanation, no preamble.\n\n"
            f"Korean:\n{text}\n\n"
            f"{lang_name}:"
        )

    async def _call_ollama(self, text: str, target_lang: str) -> str:
        """Qwen2.5:7b로 번역을 수행합니다 (Ollama API). 실패 시 원문 반환."""
        prompt = self._build_ollama_prompt(text, target_lang)
        payload = {
            "model": self._cfg.model,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_ctx": self._cfg.num_ctx,
                "temperature": self._cfg.temperature,
            },
        }
        try:
            async with httpx.AsyncClient(timeout=self._cfg.timeout) as client:
                resp = await client.post(
                    f"{self._base_url}/api/generate", json=payload
                )
                resp.raise_for_status()
                translated = resp.json().get("response", "").strip()
                if not translated:
                    logger.warning("Ollama 번역 결과가 비어있음 — 원문 사용")
                    return text
                logger.debug(
                    "Ollama 번역 완료 (target=%s, chars: %d→%d)",
                    target_lang, len(text), len(translated),
                )
                return translated
        except httpx.ConnectError:
            logger.error(
                "Ollama 연결 실패 (번역 모델: %s) — 원문 컨텍스트 사용",
                self._cfg.model,
            )
            return text
        except httpx.TimeoutException:
            logger.error(
                "번역 타임아웃 (model=%s, timeout=%ds) — 원문 컨텍스트 사용",
                self._cfg.model, self._cfg.timeout,
            )
            return text
        except Exception as e:
            logger.error("Ollama 번역 실패: %s — 원문 컨텍스트 사용", e)
            return text
