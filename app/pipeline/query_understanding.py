"""
통합 쿼리 이해 모듈 — multi-task 1 (2026-05-11).

기존 룰 3종(follow_up_detector / query_rewriter / query_analyzer)을 gemma3:4b
단일 JSON 호출로 통합. 분류·재작성·자립화를 한 번에 처리한다.

원칙 2(비용·지연 최적화): 1차 ~500ms, 메인 LLM 생성 ~5000ms = 총 ~5500ms 유지.
원칙 4(하드코딩 금지): 모델·타임아웃·프롬프트 임계치 모두 settings 기반.

3단계 폴백 (사용자 결정 2026-05-11):
  1차: gemma3:4b (CONV_UNDERSTAND_MODEL, default = CONV_REWRITE_MODEL)
  2차: 메인 답변 모델 settings.llm.model (qwen3:8b 등)
  3차: 룰 3종 (기존 follow_up_detector + query_rewriter + analyzer)

LLM이 부분 성공해도 학번/학과는 항상 룰 후행으로 보강 — LLM은 분류·정규화에 집중.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional

import httpx

from app.config import settings
from app.models import Intent, QueryAnalysis, QuestionType
from app.pipeline.follow_up_detector import FollowUpSignal, detect as rule_detect
from app.pipeline.query_analyzer import QueryAnalyzer
from app.pipeline.query_rewriter import rewrite as rule_rewrite

logger = logging.getLogger(__name__)


# ── 통합 출력 ──────────────────────────────────────────────────────

@dataclass(frozen=True)
class UnderstandingResult:
    """understand() 통합 출력. chat.py에서 분해해서 기존 follow_up_signal·analysis·search_query 흐름으로 매핑."""
    follow_up_signal: FollowUpSignal
    rewritten_query: str
    analysis: QueryAnalysis
    source: str               # "llm" | "llm_fallback" | "rule_fallback"
    latency_ms: int
    intent_confidence: float  # 0.0~1.0. rule fallback은 0.0


# ── 글로벌 룰 폴백용 analyzer 인스턴스 (lazy) ─────────────────────

_analyzer_singleton: Optional[QueryAnalyzer] = None


def _get_analyzer() -> QueryAnalyzer:
    global _analyzer_singleton
    if _analyzer_singleton is None:
        _analyzer_singleton = QueryAnalyzer()
    return _analyzer_singleton


# ── 신 Intent enum 18개 카테고리 — 프롬프트에 포함 ─────────────────
# 인덱스·라벨·정의를 한 곳에 정리. analyzer 동일 매핑.

_INTENT_DEFINITIONS: list[tuple[str, str]] = [
    ("GRADUATION_REQ",            "졸업 요건, 졸업 학점, 졸업 인증, 졸업사정"),
    ("EARLY_GRADUATION",          "조기졸업(6학기·7학기) 자격·신청·기준"),
    ("REGISTRATION_GENERAL",      "수강신청 일반·OCU·장바구니·신청기간·신청가능학점"),
    ("GRADE_OPTION",              "P/NP·성적포기·등급제(A/B/C)·성적 처리 옵션"),
    ("REREGISTRATION",            "재수강·이수구분 변경(전공↔교양 등)"),
    ("SCHEDULE",                  "학사일정·마감일·시험기간·개강·종강·계절학기 일정"),
    ("COURSE_INFO",               "과목·강의 정보·시간표·온라인/대면·강의실·교과목"),
    ("MAJOR_CHANGE",              "복수전공·부전공·전과·이수방법1/2/3·연계전공"),
    ("ALTERNATIVE",               "대체과목·동일과목·폐지과목·인정과목"),
    ("SCHOLARSHIP_APPLY",         "장학금 신청·기간·서류·접수처"),
    ("SCHOLARSHIP_QUALIFICATION", "장학금 자격·기준·금액·종류·선발조건"),
    ("TUITION_BENEFIT",           "등록금 반환·납부·분납·면제·환불"),
    ("LEAVE_OF_ABSENCE",          "휴학·복학·자퇴·제적·재입학·유예"),
    ("TRANSCRIPT",                "성적표 기반 개인 질문(내 성적·내 학점·내 재수강 추천)"),
    ("CERTIFICATE",               "증명서 발급(재학·성적·졸업·수료 증명서)"),
    ("CONTACT",                   "학과사무실·교직원·연락처·문의처"),
    ("FACILITY",                  "캠퍼스 시설·학생포털(LMS·sugang)·계정·도서관"),
    ("GENERAL",                   "위 분류 어디에도 명확히 속하지 않는 경우만"),
]


_QTYPE_DEFINITIONS: list[tuple[str, str]] = [
    ("overview",   "짧은 토픽 쿼리 (예: '수강신청', '졸업요건')"),
    ("factoid",    "단순 사실 — 날짜·수치·금액"),
    ("procedural", "절차·방법 — 단계·서류·신청법"),
    ("reasoning",  "조건 기반 추론 — '~인 경우', '~면 어떻게'"),
]


# ── 프롬프트 ───────────────────────────────────────────────────────

def _build_system_prompt() -> str:
    intents_block = "\n".join(f"- {k}: {desc}" for k, desc in _INTENT_DEFINITIONS)
    qtypes_block = "\n".join(f"- {k}: {desc}" for k, desc in _QTYPE_DEFINITIONS)
    return (
        "당신은 부산외국어대학교 학사 RAG 챗봇의 쿼리 이해 모듈입니다.\n"
        "사용자 질문을 분석해 아래 JSON 스키마로만 출력하세요. JSON 외 어떤 텍스트도 출력 금지.\n\n"
        "JSON 스키마:\n"
        "{\n"
        "  \"is_follow_up\": boolean,         // 이전 대화 맥락에 의존하는지\n"
        "  \"standalone_query\": string,      // is_follow_up=true면 자립적 한 문장으로 재작성, 아니면 원문 그대로\n"
        "  \"intent\": string,                // 아래 Intent 카테고리 중 정확히 하나\n"
        "  \"intent_confidence\": number,     // 0.0~1.0, 본인의 분류 확신도\n"
        "  \"question_type\": string,         // overview | factoid | procedural | reasoning\n"
        "  \"lang\": string,                  // ko 또는 en\n"
        "  \"entities\": {                    // 추출된 개체 (없으면 빈 문자열/false)\n"
        "    \"department\": string,          // 학과명 (예: '컴퓨터공학', 'AI빅데이터')\n"
        "    \"course_name\": string,         // 과목명\n"
        "    \"liberal_arts_area\": string,   // 교양영역 (예: '인성체험교양')\n"
        "    \"major_method\": string,        // 이수방법1/2/3\n"
        "    \"asks_url\": boolean,           // URL·사이트·페이지 위치를 묻는지\n"
        "    \"question_focus\": string       // period|limit|method|amount|range|other\n"
        "  }\n"
        "}\n\n"
        f"Intent 카테고리:\n{intents_block}\n\n"
        f"QuestionType:\n{qtypes_block}\n\n"
        "원칙:\n"
        "- GENERAL은 정말 다른 17개 어디에도 안 들어가는 경우에만. 증명서·연락처·시설은 각 전용 카테고리로.\n"
        "- 분할된 카테고리(REGISTRATION_*, SCHOLARSHIP_*, TUITION_BENEFIT)는 절대 구 키(REGISTRATION, SCHOLARSHIP)로 답하지 말 것.\n"
        "- is_follow_up=false면 standalone_query는 원문을 그대로.\n"
        "- 학번·연도는 entities에 넣지 말 것 (별도 룰이 추출).\n"
        "- JSON 외 어떤 설명·코드펜스도 출력하지 말 것."
    )


_FEW_SHOTS: list[tuple[str, str, str]] = [
    # (history_text, query, json_output)
    (
        "",
        "P/NP로 들었는데 나중에 등급제로 바꿀 수 있나요?",
        json.dumps({
            "is_follow_up": False,
            "standalone_query": "P/NP로 들었는데 나중에 등급제로 바꿀 수 있나요?",
            "intent": "GRADE_OPTION",
            "intent_confidence": 0.92,
            "question_type": "procedural",
            "lang": "ko",
            "entities": {"department": "", "course_name": "", "liberal_arts_area": "", "major_method": "", "asks_url": False, "question_focus": "method"},
        }, ensure_ascii=False),
    ),
    (
        "User: TA장학생 자격이 뭔가요?\nAssistant: TA장학생은 직전학기 평점 3.5 이상이고 학과장 추천이 필요합니다…",
        "그럼 신청은 언제 해요?",
        json.dumps({
            "is_follow_up": True,
            "standalone_query": "TA장학생 신청은 언제 하나요?",
            "intent": "SCHOLARSHIP_APPLY",
            "intent_confidence": 0.9,
            "question_type": "factoid",
            "lang": "ko",
            "entities": {"department": "", "course_name": "", "liberal_arts_area": "", "major_method": "", "asks_url": False, "question_focus": "period"},
        }, ensure_ascii=False),
    ),
    (
        "",
        "재학증명서 어디서 떼나요?",
        json.dumps({
            "is_follow_up": False,
            "standalone_query": "재학증명서 어디서 떼나요?",
            "intent": "CERTIFICATE",
            "intent_confidence": 0.95,
            "question_type": "procedural",
            "lang": "ko",
            "entities": {"department": "", "course_name": "", "liberal_arts_area": "", "major_method": "", "asks_url": True, "question_focus": "method"},
        }, ensure_ascii=False),
    ),
    (
        "",
        "How can I apply for early graduation?",
        json.dumps({
            "is_follow_up": False,
            "standalone_query": "How can I apply for early graduation?",
            "intent": "EARLY_GRADUATION",
            "intent_confidence": 0.93,
            "question_type": "procedural",
            "lang": "en",
            "entities": {"department": "", "course_name": "", "liberal_arts_area": "", "major_method": "", "asks_url": False, "question_focus": "method"},
        }, ensure_ascii=False),
    ),
]


def _format_history(history: Optional[list[dict]], max_turns: int) -> str:
    if not history:
        return ""
    pairs: list[tuple[str, str]] = []
    current_user: Optional[str] = None
    for msg in history:
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if not content:
            continue
        if role == "user":
            current_user = content
        elif role == "assistant" and current_user is not None:
            pairs.append((current_user, content))
            current_user = None
    pairs = pairs[-max_turns:]
    lines: list[str] = []
    for u, a in pairs:
        lines.append(f"User: {u}")
        a_trim = a if len(a) <= 200 else a[:200] + "…"
        lines.append(f"Assistant: {a_trim}")
    return "\n".join(lines)


def _build_messages(query: str, history_text: str) -> list[dict]:
    system = _build_system_prompt()
    messages: list[dict] = [{"role": "system", "content": system}]
    # few-shot — 4건 (history 있는 1건, 없는 3건)
    for hist, q, out in _FEW_SHOTS:
        user_block = (
            f"[이전 대화]\n{hist}\n\n[현재 질문]\n{q}" if hist
            else f"[현재 질문]\n{q}"
        )
        messages.append({"role": "user", "content": user_block})
        messages.append({"role": "assistant", "content": out})
    # 실제 호출
    real_user = (
        f"[이전 대화]\n{history_text}\n\n[현재 질문]\n{query}" if history_text
        else f"[현재 질문]\n{query}"
    )
    messages.append({"role": "user", "content": real_user})
    return messages


# ── LLM 호출 ──────────────────────────────────────────────────────

async def _call_llm(
    *,
    model: str,
    base_url: str,
    messages: list[dict],
    timeout_sec: float,
    max_tokens: int,
) -> Optional[dict]:
    """LLM 1회 호출 → JSON 파싱된 dict 반환. 실패 시 None.

    OpenAI 호환 /v1/chat/completions + response_format=json_object.
    """
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    payload = {
        "model": model,
        "messages": messages,
        "stream": False,
        "max_tokens": max_tokens,
        "temperature": 0.0,
        "top_p": 0.9,
        "think": False,
        "response_format": {"type": "json_object"},
    }
    try:
        async with httpx.AsyncClient(timeout=timeout_sec) as client:
            response = await client.post(url, json=payload)
            response.raise_for_status()
            data = response.json()
            content = (
                data.get("choices", [{}])[0].get("message", {}).get("content", "").strip()
            )
    except (httpx.TimeoutException, asyncio.TimeoutError):
        logger.debug("understand LLM 타임아웃 (%.2fs, model=%s)", timeout_sec, model)
        return None
    except Exception as e:
        logger.debug("understand LLM 호출 실패 (model=%s): %s", model, e)
        return None

    return _parse_json_response(content)


_JSON_BLOCK_RE = re.compile(r"\{[\s\S]*\}", re.MULTILINE)


def _parse_json_response(content: str) -> Optional[dict]:
    """LLM 응답에서 JSON 추출. 코드 펜스·접두사 제거."""
    if not content:
        return None
    text = content.strip()
    # 코드 펜스 제거
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:].strip()
    # 가장 바깥 {} 블록 추출
    m = _JSON_BLOCK_RE.search(text)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None


# ── LLM dict → UnderstandingResult 변환 ────────────────────────────

def _coerce_intent(raw: str) -> Optional[Intent]:
    if not raw:
        return None
    raw = raw.strip().upper()
    try:
        return Intent(raw)
    except ValueError:
        return None


def _coerce_question_type(raw: str) -> QuestionType:
    if not raw:
        return QuestionType.FACTOID
    raw = raw.strip().lower()
    try:
        return QuestionType(raw)
    except ValueError:
        return QuestionType.FACTOID


def _llm_dict_to_result(
    data: dict,
    *,
    original_query: str,
    history: Optional[list[dict]],
    source: str,
    started_at: float,
) -> Optional[UnderstandingResult]:
    """LLM JSON → UnderstandingResult. 필수 필드 누락 시 None."""
    if not isinstance(data, dict):
        return None

    intent = _coerce_intent(str(data.get("intent", "")))
    if intent is None:
        return None  # 필수 필드 누락 → fallback

    is_follow_up = bool(data.get("is_follow_up", False))
    standalone = str(data.get("standalone_query") or original_query).strip() or original_query
    if not history:
        # history 없으면 follow-up 불가능 — 보정
        is_follow_up = False
        standalone = original_query

    qtype = _coerce_question_type(str(data.get("question_type", "")))
    lang = str(data.get("lang") or "ko").strip().lower()
    if lang not in ("ko", "en"):
        lang = "ko"

    raw_entities = data.get("entities") or {}
    if not isinstance(raw_entities, dict):
        raw_entities = {}
    # entities는 비어있는 문자열·False 정리. 남는 키만 유지 (downstream이 .get()으로 안전 접근).
    entities: dict = {}
    for k, v in raw_entities.items():
        if v is None:
            continue
        if isinstance(v, str):
            stripped = v.strip()
            if stripped:
                entities[k] = stripped
        elif isinstance(v, bool):
            if v:
                entities[k] = True
        else:
            entities[k] = v

    confidence_raw = data.get("intent_confidence", 0.0)
    try:
        confidence = float(confidence_raw)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    # 학번·학생유형은 LLM이 아닌 룰로 보강 (정규식 100% 신뢰).
    analyzer = _get_analyzer()
    student_groups = analyzer._extract_student_groups(standalone)
    student_id = analyzer._extract_student_id(standalone, student_groups)
    student_type = analyzer._extract_student_type(standalone) or "내국인"
    if student_groups:
        entities.setdefault("student_groups", student_groups)

    follow_up_signal = FollowUpSignal(
        is_follow_up=is_follow_up,
        skip_rule_stage=False,
        reason=f"llm:{source}",
    )

    # requires_graph / requires_vector — 기본 룰 (downstream router가 추가 결정)
    requires_vector = True
    requires_graph = intent in {
        Intent.GRADUATION_REQ, Intent.EARLY_GRADUATION, Intent.SCHEDULE,
        Intent.MAJOR_CHANGE, Intent.LEAVE_OF_ABSENCE, Intent.ALTERNATIVE,
        Intent.COURSE_INFO,
    }

    analysis = QueryAnalysis(
        intent=intent,
        student_id=student_id,
        student_type=student_type,
        entities=entities,
        requires_graph=requires_graph,
        requires_vector=requires_vector,
        missing_info=[],
        lang=lang,
        question_type=qtype,
        normalized_query=standalone if standalone != original_query else None,
    )

    latency_ms = int((time.monotonic() - started_at) * 1000)
    return UnderstandingResult(
        follow_up_signal=follow_up_signal,
        rewritten_query=standalone,
        analysis=analysis,
        source=source,
        latency_ms=latency_ms,
        intent_confidence=confidence,
    )


# ── 룰 폴백 ────────────────────────────────────────────────────────

async def _rule_fallback(
    query: str,
    history: Optional[list[dict]],
    *,
    lang: str,
    started_at: float,
) -> UnderstandingResult:
    """기존 룰 3종 그대로 호출. 신 Intent 카테고리 미사용 — 구 Intent 그대로 반환."""
    signal = rule_detect(query, history)

    if settings.conversation.rewrite_enabled and signal.is_follow_up:
        try:
            rewritten = await rule_rewrite(
                query, history or [],
                skip_rule_stage=signal.skip_rule_stage,
                lang=lang,
            )
        except Exception as e:
            logger.debug("rule rewrite 실패: %s", e)
            rewritten = query
    else:
        rewritten = query

    analyzer = _get_analyzer()
    analysis = analyzer.analyze(rewritten)
    if lang == "en":
        analysis.lang = "en"

    latency_ms = int((time.monotonic() - started_at) * 1000)
    return UnderstandingResult(
        follow_up_signal=signal,
        rewritten_query=rewritten,
        analysis=analysis,
        source="rule_fallback",
        latency_ms=latency_ms,
        intent_confidence=0.0,
    )


# ── 메인 엔트리 ────────────────────────────────────────────────────

async def understand(
    query: str,
    history: Optional[list[dict]],
    *,
    lang: str = "ko",
) -> UnderstandingResult:
    """통합 쿼리 이해. 3단계 폴백 (LLM 1차 → LLM 2차 → 룰).

    flag OFF 시 호출자가 직접 분기 — 이 함수는 항상 LLM 시도부터.
    """
    started_at = time.monotonic()
    conv_cfg = settings.conversation

    if not query or not query.strip():
        # 빈 쿼리는 룰 단축 — 안전
        return await _rule_fallback(query or "", history, lang=lang, started_at=started_at)

    history_text = _format_history(history, conv_cfg.rewrite_max_input_turns)
    messages = _build_messages(query, history_text)

    # 1차 LLM (gemma3:4b)
    primary_model = conv_cfg.understand_model or conv_cfg.rewrite_model
    primary_base = (
        conv_cfg.understand_base_url
        or conv_cfg.rewrite_base_url
        or settings.llm.base_url
    )
    data = await _call_llm(
        model=primary_model,
        base_url=primary_base,
        messages=messages,
        timeout_sec=conv_cfg.understand_timeout_sec,
        max_tokens=conv_cfg.understand_max_tokens,
    )
    if data is not None:
        result = _llm_dict_to_result(
            data, original_query=query, history=history,
            source="llm", started_at=started_at,
        )
        if result is not None:
            return result
        logger.debug("understand 1차 LLM JSON 필수 필드 누락 → 2차 폴백")

    # 2차 LLM (메인 답변 모델)
    fallback_model = conv_cfg.understand_fallback_model or settings.llm.model
    fallback_base = (
        conv_cfg.understand_fallback_base_url or settings.llm.base_url
    )
    data2 = await _call_llm(
        model=fallback_model,
        base_url=fallback_base,
        messages=messages,
        timeout_sec=conv_cfg.understand_fallback_timeout_sec,
        max_tokens=conv_cfg.understand_max_tokens,
    )
    if data2 is not None:
        result2 = _llm_dict_to_result(
            data2, original_query=query, history=history,
            source="llm_fallback", started_at=started_at,
        )
        if result2 is not None:
            return result2
        logger.debug("understand 2차 LLM JSON 필수 필드 누락 → 룰 폴백")

    # 3차 룰
    logger.info("understand 3단계 폴백 → rule_fallback (primary=%s, fallback=%s)",
                primary_model, fallback_model)
    return await _rule_fallback(query, history, lang=lang, started_at=started_at)
