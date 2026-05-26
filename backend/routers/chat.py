"""
채팅 SSE 스트리밍 + 논스트리밍 엔드포인트.

chat_app.py:generate_response_stream() (1701~1914줄) 로직을 1:1 이식.
파이프라인 함수 호출은 동일 — st.session_state만 session_store로 교체.
"""

import asyncio
import json
import logging
import os
import re
import time
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Query, Request
from sse_starlette.sse import EventSourceResponse

from app.config import settings
from backend.dependencies import (
    get_analyzer,
    get_chat_logger,
    get_generator,
    get_merger,
    get_router,
    get_validator,
)
from backend.session import session_store
from backend.schemas.chat import ChatResponse, SearchResultItem, SourceURL

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/chat", tags=["chat"])

# 동시 LLM 스트리밍 상한 (백엔드 측 백프레셔, OOM 방어).
# Ollama OLLAMA_NUM_PARALLEL과 일치시키는 것이 권장 — 그 이상 동시 요청이 들어와도
# 여기서 큐잉되어 Ollama의 KV 캐시 슬롯이 초과되지 않게 막는다. 12GB VRAM 기준 2.
_LLM_SEMAPHORE = asyncio.Semaphore(settings.llm.max_concurrent)


def _resolve_user_id(access_token: Optional[str]) -> Optional[int]:
    """SSE·POST 쿼리 파라미터로 전달된 JWT 토큰을 검증하고 user_id 반환.

    비로그인(토큰 없음)이나 만료·서명 불일치면 None — 채팅은 정상 진행되지만
    개인 DB 저장·알림 구독은 스킵된다.
    """
    if not access_token:
        return None
    try:
        from backend.routers.user import _verify_user_token
        payload = _verify_user_token(access_token)
        if payload and "user_id" in payload:
            return int(payload["user_id"])
    except Exception as exc:
        logger.debug("JWT 검증 실패: %s", exc)
    return None


# ── 연락처 단락 처리 (LLM 없이 즉시 응답) ──

def _format_contact_answer(question: str) -> str:
    """chat_app.py:_format_contact_answer() 동일 로직."""
    try:
        from app.contacts import get_dept_searcher
        searcher = get_dept_searcher()
        if not searcher.is_contact_query(question):
            return ""
        results = searcher.search(question, top_k=3)
        if not results:
            return ""
        lines = ["\U0001f4de **연락처 안내**\n"]
        for r in results:
            college_info = f" ({r.college})" if r.college else ""
            office_info = f" | 사무실: {r.office}" if r.office else ""
            lines.append(
                f"- **{r.name}**{college_info}: "
                f"`내선 {r.extension}` / {r.phone}{office_info}"
            )
        return "\n".join(lines)
    except Exception:
        return ""


def _get_contact_footer(intent, entities: dict, question: str) -> str:
    """chat_app.py:_get_contact_footer() 동일 로직."""
    try:
        from app.models import Intent
        from app.contacts import get_dept_searcher

        _DEPT_KW = ("졸업시험", "과 행사", "학과 행사", "과행사", "학과행사")
        if any(kw in question for kw in _DEPT_KW):
            dept = entities.get("department", "")
            if dept:
                results = get_dept_searcher().search(dept, top_k=1)
                if results:
                    r = results[0]
                    return f"\n\n---\n\U0001f4de **{r.name}** 문의: `{r.phone}`"

        _ACADEMIC = {
            Intent.GRADUATION_REQ, Intent.EARLY_GRADUATION,
            Intent.REGISTRATION, Intent.SCHEDULE,
            Intent.COURSE_INFO, Intent.MAJOR_CHANGE,
            Intent.ALTERNATIVE,
        }
        if intent in _ACADEMIC:
            haksa = get_dept_searcher().search("학사지원팀", top_k=1)
            if haksa:
                return f"\n\n---\n\U0001f4de 학사 문의: **{haksa[0].name}** `{haksa[0].phone}`"
    except Exception:
        pass
    return ""


def _enrich_analysis(question: str, analysis, router_inst, session_data: dict,
                      user_id: Optional[int] = None):
    """
    chat_app.py:_enrich_analysis() 이식.
    st.session_state 대신 session_data dict 사용.

    2026-04-16: 로그인 사용자 + 세션 transcript 없음 → DB에서 자동 복원.
    transcript_context 반환 전에 PIIRedactor.redact_for_llm() 명시 적용.
    """
    transcript_context = ""
    student_context = ""

    # 프로필 주입
    _profile = session_data.get("user_profile") or {}
    if analysis.student_id is None and _profile.get("student_id"):
        analysis.student_id = _profile["student_id"]
        if hasattr(analysis, "missing_info") and "student_id" in (analysis.missing_info or []):
            analysis.missing_info.remove("student_id")
    if not analysis.entities.get("department") and _profile.get("department"):
        analysis.entities["department"] = _profile["department"]
    if _profile.get("student_type"):
        analysis.student_type = _profile["student_type"]  # 내국인도 포함 (버그 수정)

    # 학년 추정: 학번(입학연도) 기반 자동 계산, 프롬프트 컨텍스트 전용
    if getattr(analysis, "grade", None) is None and analysis.student_id:
        try:
            from app.config import settings as _settings
            adm = int(analysis.student_id)
            est = _settings.admin_faq.current_academic_year - adm + 1
            if 1 <= est <= 6:
                analysis.grade = est
        except (ValueError, TypeError, AttributeError):
            pass

    # 성적표 기반 컨텍스트
    transcript_data = session_data.get("transcript")

    # (2026-04-16) 세션에 transcript 없음 + 로그인 사용자 → DB에서 on-demand 복원
    if not transcript_data and user_id:
        try:
            from backend.database import get_user_transcript
            row = get_user_transcript(user_id)
            if row and row.get("parsed_json"):
                transcript_data = _rehydrate_transcript_from_json(row["parsed_json"])
                if transcript_data is not None:
                    session_data["transcript"] = transcript_data
                    logger.info("transcript DB 복원: user_id=%s", user_id)
        except Exception as exc:
            logger.warning("transcript DB 복원 실패 user_id=%s: %s", user_id, exc)

    if transcript_data:
        try:
            from app.transcript.analyzer import TranscriptAnalyzer
            from app.models import Intent

            tp = transcript_data.profile
            if analysis.student_id is None and tp.입학연도:
                analysis.student_id = tp.입학연도
            if not analysis.entities.get("department") and tp.전공:
                analysis.entities["department"] = tp.전공
            analysis.entities["has_transcript"] = True

            _TX_INTENTS = {Intent.GRADUATION_REQ, Intent.REGISTRATION, Intent.TRANSCRIPT}
            _TX_KW = ("부족", "재수강", "평점", "이번 학기", "수강 가능", "몇 학점", "내 성적", "내 학점", "졸업")

            if analysis.intent in _TX_INTENTS or any(kw in question for kw in _TX_KW):
                tx = TranscriptAnalyzer(transcript_data, getattr(router_inst, 'academic_graph', None))

                if "부족" in question or "졸업" in question or analysis.intent == Intent.GRADUATION_REQ:
                    transcript_context = tx.format_gap_context_safe()
                elif "재수강" in question or "평점 올" in question:
                    transcript_context = tx.format_courses_context_safe(tx.retake_candidates())
                elif "이번 학기" in question or "현재 수강" in question:
                    transcript_context = tx.format_courses_context_safe(tx.current_semester_courses())
                elif "수강 가능" in question or "몇 학점" in question:
                    reg = tx.registration_limit()
                    transcript_context = (
                        f"[수강신청 학점 한도]\n"
                        f"- 기본 최대: {reg.get('기본_최대학점', '미확인')}\n"
                        f"- 현재 평점: {reg.get('현재_평점', 0)}"
                    )
                else:
                    transcript_context = tx.format_profile_summary_safe()

                student_context = tx.format_profile_summary_safe()
        except Exception as e:
            logger.warning("성적표 분석 실패: %s", e)

    # 성적표 컨텍스트가 없을 때 프로필 정보(학번·유형·학년)를 student_context로 구성
    if not student_context and (analysis.student_id or analysis.student_type):
        parts = []
        if analysis.student_id:
            parts.append(f"{analysis.student_id}학번")
        if analysis.student_type:
            parts.append(analysis.student_type)
        if getattr(analysis, "grade", None):
            parts.append(f"{analysis.grade}학년(추정)")
        if parts:
            student_context = "[학생 정보] " + " ".join(parts)

    # (2026-04-16) LLM 전송 전 PII 재마스킹 명시 호출 — 이름·학번 유출 최종 방어.
    if transcript_context or student_context:
        try:
            from app.transcript.security import PIIRedactor
            if transcript_context:
                transcript_context = PIIRedactor.redact_for_llm(transcript_context)
            if student_context:
                student_context = PIIRedactor.redact_for_llm(student_context)
        except Exception as exc:
            logger.warning("PIIRedactor.redact_for_llm 실패: %s", exc)

    return analysis, transcript_context, student_context


def _rehydrate_transcript_from_json(parsed_json: str):
    """
    DB에 저장된 PII 마스킹 JSON을 StudentAcademicProfile 객체로 복원.
    user_transcripts.parsed_json → 분석 가능한 객체.
    실패 시 None.
    """
    import json as _json
    try:
        from app.transcript.models import (
            StudentAcademicProfile, StudentProfile, CreditSummary, CreditCategory,
        )
    except Exception:
        return None
    try:
        raw = _json.loads(parsed_json)
    except Exception:
        return None

    def _build_profile(p: dict) -> StudentProfile:
        fields = {f.name: p.get(f.name) for f in StudentProfile.__dataclass_fields__.values()}
        # None 필드는 기본값 유지하도록 제거
        fields = {k: v for k, v in fields.items() if v is not None}
        return StudentProfile(**fields)

    def _build_credits(c: dict) -> CreditSummary:
        cats = []
        for cat in c.get("categories", []) or []:
            try:
                cats.append(CreditCategory(**{
                    k: cat.get(k) for k in CreditCategory.__dataclass_fields__
                    if cat.get(k) is not None
                }))
            except Exception:
                continue
        fields = {
            k: c.get(k) for k in CreditSummary.__dataclass_fields__
            if k != "categories" and c.get(k) is not None
        }
        return CreditSummary(categories=cats, **fields)

    try:
        return StudentAcademicProfile(
            profile=_build_profile(raw.get("profile", {}) or {}),
            credits=_build_credits(raw.get("credits", {}) or {}),
            courses=raw.get("courses", []) or [],
        )
    except Exception as exc:
        logger.warning("transcript JSON → dataclass 복원 실패: %s", exc)
        return None


def _serialize_results(results: list) -> list[dict]:
    """SearchResult 리스트를 JSON 직렬화 가능 dict로 변환."""
    items = []
    for r in results[:10]:  # 최대 10개
        meta = r.metadata or {}
        items.append({
            "text": r.text or "",  # 전체 텍스트 (하이라이팅용)
            "score": round(float(r.score or 0), 4),
            "source": r.source or "",
            "page_number": getattr(r, "page_number", 0) or 0,
            "doc_type": meta.get("doc_type", ""),
            "in_context": bool(meta.get("in_context")),
            "section_path": meta.get("section_path", ""),
            "source_url": meta.get("source_url", ""),
            "title": meta.get("title", ""),
            "post_date": meta.get("post_date", ""),
            "faq_id": meta.get("faq_id", ""),
            "faq_question": meta.get("faq_question", ""),
            "faq_answer": meta.get("faq_answer", ""),
        })
    return items


def _build_generation_cache_kwargs(
    question: str,
    merged,
    analysis,
    student_context: str,
    history: list[dict] | None = None,
    is_follow_up: bool = False,
) -> dict:
    """
    2026-04-22 (플랜 wild-splashing-volcano Phase C.4):
    `share_across_sessions` 를 여기서 결정한다.

    규칙:
      - follow-up 질문이 아니고
      - 로그인 사용자의 개인 컨텍스트가 없으며 (student_context 빈 문자열)
      - 학번 특수화가 없는 경우
      → 세션 간 cache hit 허용.

    그 외(멀티턴 대화 · 개인화 답변)는 기존 동작 유지 — 세션별 키 격리.
    """
    has_personal_context = bool((student_context or "").strip())
    has_student_id = bool((analysis.student_id or "").strip())
    share_across_sessions = (
        not is_follow_up
        and not has_personal_context
        and not has_student_id
    )

    return {
        "question": question,
        "context": merged.formatted_context,
        "student_id": analysis.student_id,
        "question_focus": analysis.entities.get("question_focus"),
        "lang": analysis.lang,
        "matched_terms": analysis.matched_terms,
        "student_context": student_context,
        "context_confidence": merged.context_confidence,
        "question_type": analysis.question_type.value if analysis.question_type else None,
        "intent": analysis.intent.value if analysis.intent else None,
        "entities": analysis.entities,
        "history": history,
        "share_across_sessions": share_across_sessions,
    }


# ── SSE 스트리밍 엔드포인트 ──

@router.get("/stream")
async def chat_stream(
    request: Request,
    session_id: str = Query(..., description="세션 ID"),
    question: str = Query(..., min_length=1, max_length=2000, description="질문"),
    access_token: Optional[str] = Query(
        None, description="선택: 로그인 사용자 JWT. 있으면 chat_messages에 저장되어 FAQ 알림 구독 가능."
    ),
):
    """
    GET /api/chat/stream?session_id=X&question=Y → SSE 스트리밍.

    이벤트 타입:
    - token: {"token": "..."} — 토큰 단위 스트리밍
    - clear: {} — EN 원패스 전환 시 플레이스홀더 초기화
    - done: {answer, source_urls, results, intent, duration_ms} — 완료
    - error: {message} — 에러

    X-Test-Mode 헤더가 "1"/"true"이면 JSONL 로그 + chat_messages DB 양쪽 모두 기록하지 않음.
    """
    # 평가·회귀 스크립트가 실사용자 로그를 오염시키지 않도록
    from app.logging.chat_logger import set_skip_log
    _is_test = request.headers.get("X-Test-Mode", "").strip().lower() in {"1", "true", "yes", "on"}
    set_skip_log(_is_test)

    async def event_generator() -> AsyncGenerator[dict, None]:
        _t0 = time.monotonic()

        try:
            async for event in _inner_generator(_t0):
                yield event
        except Exception as e:
            logger.error("채팅 파이프라인 오류: %s", e, exc_info=True)
            yield {"event": "error", "data": json.dumps(
                {"message": "처리 중 오류가 발생했습니다. 다시 시도해 주세요."},
                ensure_ascii=False,
            )}

    async def _inner_generator(_t0: float) -> AsyncGenerator[dict, None]:
        # 세션 확인/생성
        sid, session_data = session_store.get_or_create(session_id)

        # JWT 토큰이 있으면 user_id 추출 (비로그인은 None — 개인 DB 저장 스킵)
        user_id = _resolve_user_id(access_token)

        # ── TRACE: [chat-IN] — 요청 진입 ──
        _sid_short = sid[:8] if sid else "-"
        _prior_n = len(session_data.get("messages") or [])
        logger.info(
            "[chat-IN] sid=%s path=stream q_chars=%d q=%r user=%s prior_msgs=%d lang=%s",
            _sid_short, len(question), question[:80], user_id or "anon",
            _prior_n, session_data.get("lang", "ko"),
        )

        # 연락처 단락 처리
        contact_answer = _format_contact_answer(question)
        if contact_answer:
            _try_log_simple(question, contact_answer, sid, "CONTACT", _t0, user_id=user_id)
            logger.info(
                "[chat-OUT] sid=%s path=contact_short_circuit answer_chars=%d total_ms=%d",
                _sid_short, len(contact_answer), int((time.monotonic() - _t0) * 1000),
            )
            yield {"event": "done", "data": json.dumps({
                "answer": contact_answer,
                "source_urls": [],
                "results": [],
                "intent": "CONTACT",
                "duration_ms": int((time.monotonic() - _t0) * 1000),
            }, ensure_ascii=False)}
            return

        # ── TRACE: [lang-detect] — 언어 판정 ──
        try:
            from app.pipeline.language_detector import detect_language as _detect_lang
            _detected_lang = _detect_lang(question)
            logger.info(
                "[lang-detect] sid=%s detected=%s session_lang=%s",
                _sid_short, _detected_lang, session_data.get("lang", "ko"),
            )
        except Exception as _e:
            logger.debug("lang-detect 실패: %s", _e)

        # 파이프라인 컴포넌트
        analyzer = get_analyzer()
        router_inst = get_router()
        merger = get_merger()
        generator = get_generator()
        validator = get_validator()

        if not all([analyzer, router_inst, merger, generator, validator]):
            yield {"event": "error", "data": json.dumps(
                {"message": "파이프라인이 아직 초기화되지 않았습니다."},
                ensure_ascii=False,
            )}
            return

        # 멀티턴: follow-up 감지 + 쿼리 재작성 (retrieval/generation 양쪽에 사용)
        from app.pipeline import follow_up_detector, query_rewriter
        from app.config import settings as _settings
        _conv_cfg = _settings.conversation
        prior_messages = session_data.get("messages") or []
        search_query = question  # retrieval·LLM에 실제로 건네는 쿼리 (잠재적으로 rewritten)
        lang = session_data.get("lang", "ko")

        if _conv_cfg.understanding_enabled:
            # ── multi-task 1 (2026-05-11): 통합 쿼리 이해 (gemma3:4b JSON) ──
            # follow_up_detector + query_rewriter + analyzer 룰 3종을 단일 LLM 호출로 통합.
            # 실패 시 메인 LLM 폴백 → 룰 폴백 (3단계 폴백).
            from app.pipeline import query_understanding
            logger.info(
                "[understand-IN] sid=%s mode=integrated_llm question_chars=%d history_msgs=%d lang=%s",
                _sid_short, len(question), _prior_n, lang,
            )
            _t_u = time.monotonic()
            _understand = await query_understanding.understand(
                question, prior_messages, lang=lang,
            )
            _ms_understand = int((time.monotonic() - _t_u) * 1000)
            follow_up_signal = _understand.follow_up_signal
            search_query = _understand.rewritten_query
            analysis = _understand.analysis
            if lang == "en":
                analysis.lang = "en"
            analysis, transcript_context, student_context = _enrich_analysis(
                search_query, analysis, router_inst, session_data, user_id=user_id
            )
            # 통합 호출은 분리 timing 불가 — 전체를 rewrite_ms로 기록 (PIPELINE_TIMING 호환)
            _ms_follow_up = 0
            _ms_rewrite = _ms_understand
            _ms_analyze = 0
            logger.info(
                "[understand-OUT] sid=%s source=%s elapsed_ms=%d intent=%s qt=%s confidence=%.2f rewrite_changed=%s",
                _sid_short, _understand.source, _ms_understand,
                analysis.intent.value if analysis.intent else "?",
                analysis.question_type.value if analysis.question_type else "?",
                _understand.intent_confidence, search_query != question,
            )
            if search_query != question:
                logger.info(
                    "follow-up[%s] rewrite: '%s' → '%s'",
                    follow_up_signal.reason, question[:60], search_query[:60],
                )
        else:
            # ── TRACE: [follow_up-IN/OUT] ──
            _t_fu = time.monotonic()
            follow_up_signal = follow_up_detector.detect(question, prior_messages)
            _ms_follow_up = int((time.monotonic() - _t_fu) * 1000)
            logger.info(
                "[follow_up] sid=%s is_follow_up=%s reason=%s skip_rule=%s elapsed_ms=%d",
                _sid_short, follow_up_signal.is_follow_up, follow_up_signal.reason,
                follow_up_signal.skip_rule_stage, _ms_follow_up,
            )
            _ms_rewrite = 0
            if _conv_cfg.rewrite_enabled and follow_up_signal.is_follow_up:
                logger.info(
                    "[rewrite-IN] sid=%s mode=%s lang=%s history_msgs=%d",
                    _sid_short,
                    "llm" if not follow_up_signal.skip_rule_stage else "llm_only_no_rule",
                    lang, _prior_n,
                )
                _t_rw = time.monotonic()
                try:
                    search_query = await query_rewriter.rewrite(
                        question,
                        prior_messages,
                        skip_rule_stage=follow_up_signal.skip_rule_stage,
                        lang=lang,
                    )
                except Exception as e:
                    logger.debug("query_rewriter 실패, 원본 사용: %s", e)
                    search_query = question
                _ms_rewrite = int((time.monotonic() - _t_rw) * 1000)
                logger.info(
                    "[rewrite-OUT] sid=%s elapsed_ms=%d changed=%s new_q=%r",
                    _sid_short, _ms_rewrite, search_query != question, search_query[:80],
                )
                if search_query != question:
                    logger.info(
                        "follow-up[%s] rewrite: '%s' → '%s'",
                        follow_up_signal.reason, question[:60], search_query[:60],
                    )

            # ── TRACE: [analyze-IN/OUT] ──
            logger.info(
                "[analyze-IN] sid=%s search_q=%r lang=%s",
                _sid_short, search_query[:80], lang,
            )
            _t1 = time.monotonic()
            analysis = analyzer.analyze(search_query)
            if lang == "en":
                analysis.lang = "en"
            analysis, transcript_context, student_context = _enrich_analysis(
                search_query, analysis, router_inst, session_data, user_id=user_id
            )
            _ms_analyze = int((time.monotonic() - _t1) * 1000)
            _ents = analysis.entities or {}
            logger.info(
                "[analyze-OUT] sid=%s intent=%s qt=%s entities={dept=%s,student_id=%s,semester=%s,focus=%s} matched_terms=%d normalized_q=%r elapsed_ms=%d",
                _sid_short,
                analysis.intent.value if analysis.intent else "?",
                analysis.question_type.value if analysis.question_type else "?",
                _ents.get("department"), _ents.get("student_id"),
                _ents.get("semester"), _ents.get("question_focus"),
                len(analysis.matched_terms or []),
                (analysis.normalized_query or "")[:60], _ms_analyze,
            )

        # Stage 2: 검색 (glossary 정규화된 쿼리 사용 — 학식→학생식당 등)
        # search_query는 이미 follow-up rewrite를 거친 상태 → 그 위에 glossary 레이어 적용
        _search_query = analysis.normalized_query or search_query
        # ── TRACE: [router-IN] ──
        logger.info(
            "[router-IN] sid=%s search_q=%r intent=%s dept_filter=%s",
            _sid_short, _search_query[:80],
            analysis.intent.value if analysis.intent else "?",
            (analysis.entities or {}).get("department"),
        )
        _t2 = time.monotonic()
        search_results = router_inst.route_and_search(_search_query, analysis)
        _ms_search = int((time.monotonic() - _t2) * 1000)

        # ── 실험: FAQ 비활성 토글 — env FAQ_DISABLED=1 또는 헤더 X-FAQ-Disabled: 1 ──
        # 매 요청마다 평가되어 백엔드 재시작 없이 즉시 효과 (2026-05-19).
        # FAQ가 노이즈인지 검증용.
        _faq_disabled = (
            os.getenv("FAQ_DISABLED", "").lower() in ("1", "true", "yes", "on")
            or request.headers.get("X-FAQ-Disabled", "").lower() in ("1", "true", "yes", "on")
        )
        if _faq_disabled:
            def _is_faq(r):
                m = r.metadata or {}
                return m.get("doc_type") == "faq" or m.get("node_type") == "FAQ"
            _orig_v = len(search_results.get("vector_results") or [])
            _orig_g = len(search_results.get("graph_results") or [])
            search_results["vector_results"] = [
                r for r in (search_results.get("vector_results") or []) if not _is_faq(r)
            ]
            search_results["graph_results"] = [
                r for r in (search_results.get("graph_results") or []) if not _is_faq(r)
            ]
            logger.info(
                "[faq-disabled] sid=%s vector=%d→%d graph=%d→%d",
                _sid_short, _orig_v, len(search_results["vector_results"]),
                _orig_g, len(search_results["graph_results"]),
            )

        # ── TRACE: [router-OUT] (vector/graph 검색 후보 + 상위 점수) ──
        _vr = search_results.get("vector_results") or []
        _gr = search_results.get("graph_results") or []
        _vt = (_vr[0].score if _vr else 0.0)
        _gt = (_gr[0].score if _gr else 0.0)
        _g_has_direct = any(
            (r.metadata or {}).get("direct_answer") for r in _gr
        )
        logger.info(
            "[router-OUT] sid=%s vector_n=%d vector_top=%.2f graph_n=%d graph_top=%.2f "
            "graph_has_direct_answer=%s elapsed_ms=%d",
            _sid_short, len(_vr), _vt, len(_gr), _gt, _g_has_direct, _ms_search,
        )

        # Stage 3: 컨텍스트 병합
        # ── TRACE: [merger-IN] ──
        logger.info(
            "[merger-IN] sid=%s vector_n=%d graph_n=%d intent=%s qt=%s has_transcript=%s",
            _sid_short, len(_vr), len(_gr),
            analysis.intent.value if analysis.intent else "?",
            analysis.question_type.value if analysis.question_type else "?",
            bool(transcript_context),
        )
        _t3 = time.monotonic()
        merged = merger.merge(
            vector_results=search_results["vector_results"],
            graph_results=search_results["graph_results"],
            question=search_query,
            intent=analysis.intent,
            entities=analysis.entities,
            transcript_context=transcript_context,
            question_type=analysis.question_type,
        )
        _ms_merge = int((time.monotonic() - _t3) * 1000)
        # ── TRACE: [merger-OUT] ──
        _selected_v = len(merged.vector_results or [])
        _selected_g = len(merged.graph_results or [])
        _src_urls = len(merged.source_urls or [])
        _direct_ans_chars = len(merged.direct_answer or "")
        logger.info(
            "[merger-OUT] sid=%s selected_vector=%d selected_graph=%d source_urls=%d "
            "direct_answer_chars=%d ctx_chars=%d ctx_tokens_est=%d confidence=%.2f elapsed_ms=%d",
            _sid_short, _selected_v, _selected_g, _src_urls, _direct_ans_chars,
            len(merged.formatted_context or ""), merged.total_tokens_estimate,
            merged.context_confidence, _ms_merge,
        )

        # P4: 저신뢰 재시도 (1회)
        _t4 = time.monotonic()
        if (
            merged.context_confidence is not None
            and merged.context_confidence < 0.3
            and not merged.direct_answer
            and not transcript_context
        ):
            try:
                rewritten = await generator.rewrite_query(
                    question=search_query,
                    lang=analysis.lang or "ko",
                    intent=analysis.intent.value if analysis.intent else None,
                )
                if rewritten and rewritten != search_query:
                    retry_results = router_inst.route_and_search(rewritten, analysis)
                    # 기존 결과와 병합 (중복 제거)
                    seen = set()
                    combined_vector = []
                    for r in search_results["vector_results"] + retry_results["vector_results"]:
                        key = (r.text or "")[:120]
                        if key and key not in seen:
                            seen.add(key)
                            combined_vector.append(r)
                    combined_graph = []
                    seen_g = set()
                    for r in search_results["graph_results"] + retry_results["graph_results"]:
                        key = (r.text or "")[:120]
                        if key and key not in seen_g:
                            seen_g.add(key)
                            combined_graph.append(r)

                    merged_retry = merger.merge(
                        vector_results=combined_vector,
                        graph_results=combined_graph,
                        question=search_query,
                        intent=analysis.intent,
                        entities=analysis.entities,
                        transcript_context=transcript_context,
                        question_type=analysis.question_type,
                    )
                    if merged_retry.context_confidence > merged.context_confidence:
                        logger.info(
                            "P4 retry 채택: confidence %.2f -> %.2f (rewritten='%s')",
                            merged.context_confidence,
                            merged_retry.context_confidence,
                            (rewritten or "")[:60],
                        )
                        merged = merged_retry
                        search_results = {
                            "vector_results": combined_vector,
                            "graph_results": combined_graph,
                        }
            except Exception as e:
                logger.warning("P4 retry 실패: %s", e)
        _ms_retry = int((time.monotonic() - _t4) * 1000)

        # 빈 컨텍스트 → 거부 응답
        if not merged.formatted_context.strip():
            if analysis.lang == "en":
                msg = (
                    "I'm sorry, but I couldn't find any relevant information "
                    "in the academic regulations.\n\n"
                    "Please contact the Academic Affairs Office at +82-51-509-5182."
                )
            else:
                msg = (
                    "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
                    "다음을 확인해 주세요:\n"
                    "- PDF 학사 안내 자료가 등록되어 있는지\n"
                    "- 질문에 학번을 포함했는지 (예: 2023학번)"
                )
            yield {"event": "done", "data": json.dumps({
                "answer": msg, "source_urls": [], "results": [],
                "intent": analysis.intent.value if analysis.intent else "",
                "duration_ms": int((time.monotonic() - _t0) * 1000),
            }, ensure_ascii=False)}
            _try_log(question, msg, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
            return

        # M7 (2026-04-27): direct_answer 단락 응답(LLM 우회)은 기본 OFF.
        # direct_answer는 컨텍스트의 일부로만 사용되고, LLM이 항상 생성한다.
        # .env DIRECT_ANSWER_BYPASS_LLM=true 시 (구) 우회 동작 복구.
        if (
            settings.pipeline.direct_answer_bypass_llm
            and merged.direct_answer
            and analysis.lang != "en"
        ):
            # 멀티턴 컨텍스트 보존: direct_answer도 session history에 append.
            messages = session_data.get("messages", [])
            messages.append({"role": "user", "content": question})
            messages.append({
                "role": "assistant",
                "content": merged.direct_answer,
                "rated": False,
                "rating": None,
            })
            session_store.update(sid, "messages", messages)
            yield {"event": "done", "data": json.dumps({
                "answer": merged.direct_answer,
                "source_urls": [{"title": u.get("title", ""), "url": u.get("url", "")} for u in (merged.source_urls or [])],
                "results": _serialize_results(merged.vector_results + merged.graph_results),
                "intent": analysis.intent.value if analysis.intent else "",
                "duration_ms": int((time.monotonic() - _t0) * 1000),
            }, ensure_ascii=False)}
            _try_log(question, merged.direct_answer, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
            return

        all_results = search_results["vector_results"] + search_results["graph_results"]
        # history는 직전 대화만 (현재 user 메시지는 아직 prior_messages에 없음)
        llm_history = prior_messages if _conv_cfg.history_enabled else None
        cache_kwargs = _build_generation_cache_kwargs(
            search_query, merged, analysis, student_context, history=llm_history,
            is_follow_up=follow_up_signal.is_follow_up,
        )
        cached_answer = generator.get_cached_response(**cache_kwargs)
        if cached_answer:
            messages = session_data.get("messages", [])
            messages.append({"role": "user", "content": question})
            messages.append({
                "role": "assistant",
                "content": cached_answer,
                "rated": False,
                "rating": None,
            })
            session_store.update(sid, "messages", messages)
            _try_log(question, cached_answer, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
            yield {"event": "done", "data": json.dumps({
                "answer": cached_answer,
                "source_urls": [
                    {"title": u.get("title", ""), "url": u.get("url", "")}
                    for u in (merged.source_urls or [])
                ],
                "results": _serialize_results(all_results),
                "intent": analysis.intent.value if analysis.intent else "",
                "duration_ms": int((time.monotonic() - _t0) * 1000),
            }, ensure_ascii=False)}
            return

        # Stage 5: LLM 스트리밍 생성
        # ── TRACE: [generator-IN] + [evidence] (cite_key 통합) ──
        from app.pipeline import evidence_trace as _ev_tr
        _sid_short = sid[:8] if sid else "-"
        _history_turns = (len(llm_history) // 2) if llm_history else 0
        _evidence_list = _ev_tr.build_evidence_list(all_results, max_items=16)
        _notice_url_n = sum(1 for ev in _evidence_list if ev.cite_key.startswith("URL:"))
        _pdf_pages = sorted({ev.page for ev in _evidence_list if ev.page})
        logger.info(
            "[generator-IN] sid=%s model=%s prompt_ctx_chars=%d intent=%s qt=%s lang=%s "
            "history_turns=%d search_n=%d notice_urls=%d pdf_pages=%s",
            _sid_short, settings.llm.model,
            len(merged.formatted_context or ""),
            analysis.intent.value if analysis.intent else "?",
            analysis.question_type.value if analysis.question_type else "?",
            analysis.lang or "ko", _history_turns, len(all_results),
            _notice_url_n, _pdf_pages,
        )
        for _ev in _evidence_list:
            _preview = (_ev.text or "").replace("\n", " ")[:60]
            logger.info(
                "[evidence] sid=%s #%d cite=%s doc=%r page=%s section=%r type=%s score=%.2f url=%s text=%r",
                _sid_short, _ev.chunk_idx + 1, _ev.cite_key, _ev.doc,
                _ev.page if _ev.page else "-", _ev.section, _ev.source_type,
                _ev.score, _ev.url or "-", _preview,
            )

        _t5 = time.monotonic()
        full_answer = ""
        try:
            # 백프레셔: 동시 LLM 스트리밍 상한 도달 시 여기서 큐 대기 → OOM 방어.
            async with _LLM_SEMAPHORE:
                async for token in generator.generate(
                    question=search_query,
                    context=merged.formatted_context,
                    student_id=analysis.student_id,
                    question_focus=analysis.entities.get("question_focus"),
                    lang=analysis.lang,
                    matched_terms=analysis.matched_terms,
                    student_context=student_context,
                    context_confidence=merged.context_confidence,
                    question_type=analysis.question_type.value if analysis.question_type else None,
                    intent=analysis.intent.value,
                    entities=analysis.entities,
                    history=llm_history,
                ):
                    if token == "\x00CLEAR\x00":
                        full_answer = ""
                        yield {"event": "clear", "data": "{}"}
                        # EN 경로: CLEAR 직후 warning 주입 (한 번만)
                        if _soft_warn_text and not _soft_warn_emitted:
                            _pref = _soft_warn_text + "\n\n"
                            full_answer += _pref
                            yield {"event": "token", "data": json.dumps({"token": _pref}, ensure_ascii=False)}
                            _soft_warn_emitted = True
                        continue
                    full_answer += token
                    yield {"event": "token", "data": json.dumps(
                        {"token": token}, ensure_ascii=False
                    )}
        except Exception as e:
            logger.error("LLM 생성 오류: %s", e)
            yield {"event": "error", "data": json.dumps(
                {"message": f"답변 생성 중 오류: {e}"},
                ensure_ascii=False,
            )}
            return
        _ms_gen = int((time.monotonic() - _t5) * 1000)

        # ── TRACE: [generator-OUT] — 답변 텍스트 미리보기 ──
        _ans_preview = (full_answer or "").replace("\n", " | ")[:200]
        logger.info(
            "[generator-OUT] sid=%s answer_chars=%d elapsed_ms=%d preview=%r",
            _sid_short, len(full_answer or ""), _ms_gen, _ans_preview,
        )
        # ── TRACE: [answer-evidence-map] — 답변 내 각 단위값의 출처 cite_key 매핑 ──
        try:
            _ans_map = _ev_tr.map_answer_to_evidence(full_answer or "", _evidence_list)
            for _item in _ans_map:
                logger.info(
                    "[answer-evidence-map] sid=%s item=%s in_ctx=%s cites=%s",
                    _sid_short, _item["item"], _item["in_context"],
                    _item["matched_cites"] or ["NONE"],
                )
            if not _ans_map:
                logger.info(
                    "[answer-evidence-map] sid=%s item=NO_UNITS in_ctx=- cites=- "
                    "(non-numeric/qualitative answer)", _sid_short,
                )
        except Exception as _e:
            logger.debug("answer-evidence-map 실패: %s", _e)
        # ── TRACE: [answer-manifest] — intent별 required/optional/excluded 충족도 ──
        try:
            _mf = _ev_tr.check_answer_manifest(
                full_answer or "",
                analysis.intent.value if analysis.intent else "",
                question=search_query,
            )
            logger.info(
                "[answer-manifest] sid=%s %s required_missing=%s optional_missing=%s excluded_violations=%s",
                _sid_short, _mf.summary(),
                _mf.required_missing, _mf.optional_missing, _mf.excluded_violations,
            )
        except Exception as _e:
            logger.debug("answer-manifest 실패: %s", _e)

        # 빈 응답 방어
        if not full_answer.strip():
            if analysis.lang == "en":
                full_answer = "Sorry, I couldn't generate a response. Please try again."
            else:
                full_answer = "죄송합니다. 응답을 생성하지 못했습니다. 다시 시도해 주세요."
            logger.warning("LLM 빈 응답: question='%s'", question[:50])

        # ~ 이스케이프 (마크다운 취소선 방지)
        full_answer = re.sub(r'(?<!~)~(?!~)', r'\~', full_answer)

        # Phase 4 품질 게이트 (KO only, generate_full() lines 776-819 이식)
        if analysis.lang != "en" and full_answer.strip():
            try:
                from app.pipeline.answer_units import (
                    fill_from_context, verify_answer_against_context,
                    verify_completeness,
                )
                from app.pipeline.response_validator import ResponseValidator as _RV

                # refusal 응답이면 후처리 건너뛰기 (버그 #5)
                _rv = _RV()
                if not _rv._is_no_context_response(full_answer):
                    # Step 2-C: 환각 탐지
                    ok, reason = verify_answer_against_context(full_answer, merged.formatted_context)
                    if not ok:
                        logger.warning("answer-context mismatch: %s", reason)
                        full_answer = (
                            "제공된 자료에서 해당 내용을 정확히 확인하지 못했습니다. "
                            "학사지원팀(051-509-5182)에 문의하시기 바랍니다."
                        )
                    else:
                        # Step 3: 이분법 완전성 검증 — 재작성된 쿼리 기준
                        if not verify_completeness(search_query, full_answer, merged.formatted_context):
                            logger.debug("verify_completeness failed, fill_from_context로 보완")
                        # Fix D: 누락 unit 보충
                        target_entity = analysis.entities.get("department") if analysis.entities else None
                        full_answer = fill_from_context(
                            search_query, full_answer, merged.formatted_context,
                            target_entity=target_entity,
                        )
            except Exception as e:
                logger.debug("Phase4 후처리 실패, 원본 유지: %s", e)

        # Stage 6: 응답 검증
        # ── TRACE: [validator-IN] — 검증 입력 명세 (8 후보 요약) ──
        logger.info(
            "[validator-IN] sid=%s answer_chars=%d ctx_chars=%d search_n=%d",
            _sid_short, len(full_answer or ""),
            len(merged.formatted_context or ""), len(all_results),
        )
        _t6 = time.monotonic()
        passed = None
        warnings = []
        try:
            passed, warnings = validator.validate(
                answer=full_answer,
                context=merged.formatted_context,
                search_results=all_results,
            )
            if warnings:
                warning_text = "\n".join(f"- {w}" for w in warnings)
                full_answer += f"\n\n---\n*검증 경고:*\n{warning_text}"
        except Exception as _e:
            logger.debug("validator 실패: %s", _e)
        _ms_val = int((time.monotonic() - _t6) * 1000)
        # ── TRACE: [validator-OUT] ──
        logger.info(
            "[validator-OUT] sid=%s passed=%s warnings=%d elapsed_ms=%d",
            _sid_short, passed, len(warnings or []), _ms_val,
        )

        # 연락처 꼬리말
        footer = _get_contact_footer(analysis.intent, analysis.entities, question)
        if footer:
            full_answer += footer

        generator.store_cached_response(full_answer, **cache_kwargs)

        # 메시지 이력에 추가
        messages = session_data.get("messages", [])
        messages.append({"role": "user", "content": question})
        messages.append({
            "role": "assistant",
            "content": full_answer,
            "rated": False,
            "rating": None,
        })
        session_store.update(sid, "messages", messages)

        # 로그
        # ── TRACE: [log-IN] ──
        logger.info(
            "[log-IN] sid=%s persist=jsonl+sqlite is_test=%s user=%s",
            _sid_short, _is_test, user_id or "anon",
        )
        _try_log(question, full_answer, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
        logger.info("[log-OUT] sid=%s saved=%s", _sid_short, not _is_test)

        # stage별 타이밍 로그
        _ms_total = int((time.monotonic() - _t0) * 1000)
        _timing_msg = (
            f"PIPELINE_TIMING total={_ms_total}ms follow_up={_ms_follow_up}ms rewrite={_ms_rewrite}ms "
            f"analyze={_ms_analyze}ms search={_ms_search}ms merge={_ms_merge}ms "
            f"retry={_ms_retry}ms generate={_ms_gen}ms validate={_ms_val}ms "
            f"intent={analysis.intent.value if analysis.intent else '?'} "
            f"qt={analysis.question_type.value if analysis.question_type else '?'} "
            f"follow_up={follow_up_signal.reason}"
        )
        print(_timing_msg, flush=True)
        # ── TRACE: [chat-OUT] — 최종 응답 요약 ──
        logger.info(
            "[chat-OUT] sid=%s path=stream intent=%s answer_chars=%d source_urls=%d "
            "ctx_confidence=%.2f follow_up=%s total_ms=%d",
            _sid_short,
            analysis.intent.value if analysis.intent else "?",
            len(full_answer), len(merged.source_urls or []),
            merged.context_confidence, follow_up_signal.reason, _ms_total,
        )

        # 완료 이벤트
        yield {"event": "done", "data": json.dumps({
            "answer": full_answer,
            "source_urls": [
                {"title": u.get("title", ""), "url": u.get("url", "")}
                for u in (merged.source_urls or [])
            ],
            "results": _serialize_results(all_results),
            "intent": analysis.intent.value if analysis.intent else "",
            "duration_ms": _ms_total,
            "timing": {
                "follow_up_ms": _ms_follow_up,
                "rewrite_ms": _ms_rewrite,
                "analyze_ms": _ms_analyze,
                "search_ms": _ms_search,
                "merge_ms": _ms_merge,
                "retry_ms": _ms_retry,
                "generate_ms": _ms_gen,
                "validate_ms": _ms_val,
            },
        }, ensure_ascii=False)}

    return EventSourceResponse(event_generator())


# ── 논스트리밍 폴백 ──

def _log_chat_sync_timing(*, t0, path, follow_up_ms=0, rewrite_ms=0,
                          analyze_ms=0, search_ms=0, merge_ms=0,
                          generate_ms=0, validate_ms=0, intent="?",
                          question_type="?", follow_up_reason="?"):
    """chat_stream의 PIPELINE_TIMING 형식 + endpoint=sync, path 라벨로 분기 표시."""
    total_ms = int((time.monotonic() - t0) * 1000)
    print(
        f"PIPELINE_TIMING total={total_ms}ms follow_up={follow_up_ms}ms "
        f"rewrite={rewrite_ms}ms analyze={analyze_ms}ms search={search_ms}ms "
        f"merge={merge_ms}ms retry=0ms generate={generate_ms}ms validate={validate_ms}ms "
        f"intent={intent} qt={question_type} follow_up={follow_up_reason} "
        f"endpoint=sync path={path}",
        flush=True,
    )
    return total_ms


@router.post("", response_model=ChatResponse)
async def chat_sync(
    request: Request,
    session_id: str = Query(...),
    question: str = Query(..., min_length=1, max_length=2000),
    access_token: Optional[str] = Query(None),
):
    """POST /api/chat — 논스트리밍 채팅 (테스트/평가용).

    X-Test-Mode 헤더가 참이면 JSONL 로그 + chat_messages DB 저장 모두 건너뜀.
    """
    from app.logging.chat_logger import set_skip_log
    _is_test = request.headers.get("X-Test-Mode", "").strip().lower() in {"1", "true", "yes", "on"}
    set_skip_log(_is_test)

    _t0 = time.monotonic()
    _ms_follow_up = _ms_rewrite = _ms_analyze = 0
    _ms_search = _ms_merge = _ms_gen = _ms_val = 0
    sid, session_data = session_store.get_or_create(session_id)
    user_id = _resolve_user_id(access_token)

    # 연락처 단락
    contact = _format_contact_answer(question)
    if contact:
        _try_log_simple(question, contact, sid, "CONTACT", _t0, user_id=user_id)
        total_ms = _log_chat_sync_timing(t0=_t0, path="contact", intent="CONTACT")
        return ChatResponse(answer=contact, intent="CONTACT", duration_ms=total_ms)

    analyzer = get_analyzer()
    router_inst = get_router()
    merger = get_merger()
    generator = get_generator()
    validator = get_validator()

    # 멀티턴: follow-up 감지 + 재작성 (스트리밍 엔드포인트와 동일 로직)
    from app.pipeline import follow_up_detector, query_rewriter
    from app.config import settings as _settings
    _conv_cfg = _settings.conversation
    prior_messages = session_data.get("messages") or []
    search_query = question
    lang = session_data.get("lang", "ko")
    _ms_rewrite = 0

    if _conv_cfg.understanding_enabled:
        # ── multi-task 1 (2026-05-11): 통합 쿼리 이해 (gemma3:4b JSON) ──
        from app.pipeline import query_understanding
        _t_u = time.monotonic()
        _understand = await query_understanding.understand(
            question, prior_messages, lang=lang,
        )
        _ms_understand = int((time.monotonic() - _t_u) * 1000)
        follow_up_signal = _understand.follow_up_signal
        search_query = _understand.rewritten_query
        analysis = _understand.analysis
        if lang == "en":
            analysis.lang = "en"
        analysis, transcript_context, student_context = _enrich_analysis(
            search_query, analysis, router_inst, session_data, user_id=user_id
        )
        _ms_follow_up = 0
        _ms_rewrite = _ms_understand
        _ms_analyze = 0
        logger.info(
            "understand[%s] %dms intent=%s confidence=%.2f",
            _understand.source, _ms_understand,
            analysis.intent.value, _understand.intent_confidence,
        )
    else:
        _t1 = time.monotonic()
        follow_up_signal = follow_up_detector.detect(question, prior_messages)
        _ms_follow_up = int((time.monotonic() - _t1) * 1000)
        if _conv_cfg.rewrite_enabled and follow_up_signal.is_follow_up:
            _t2 = time.monotonic()
            try:
                search_query = await query_rewriter.rewrite(
                    question,
                    prior_messages,
                    skip_rule_stage=follow_up_signal.skip_rule_stage,
                    lang=lang,
                )
            except Exception as e:
                logger.debug("query_rewriter 실패 (sync), 원본 사용: %s", e)
                search_query = question
            _ms_rewrite = int((time.monotonic() - _t2) * 1000)

        _t3 = time.monotonic()
        analysis = analyzer.analyze(search_query)
        if lang == "en":
            analysis.lang = "en"
        analysis, transcript_context, student_context = _enrich_analysis(
            search_query, analysis, router_inst, session_data, user_id=user_id
        )
        _ms_analyze = int((time.monotonic() - _t3) * 1000)

    # glossary 정규화된 쿼리 우선. follow-up rewrite된 search_query 위에 glossary 레이어.
    _search_query = analysis.normalized_query or search_query
    _t4 = time.monotonic()
    search_results = router_inst.route_and_search(_search_query, analysis)
    _ms_search = int((time.monotonic() - _t4) * 1000)

    # ── 실험: FAQ 비활성 토글 — env 또는 헤더 (sync path) ──
    _faq_disabled = (
        os.getenv("FAQ_DISABLED", "").lower() in ("1", "true", "yes", "on")
        or request.headers.get("X-FAQ-Disabled", "").lower() in ("1", "true", "yes", "on")
    )
    if _faq_disabled:
        _sid_short_sync = sid[:8] if sid else "-"
        def _is_faq(r):
            m = r.metadata or {}
            return m.get("doc_type") == "faq" or m.get("node_type") == "FAQ"
        _orig_v = len(search_results.get("vector_results") or [])
        _orig_g = len(search_results.get("graph_results") or [])
        search_results["vector_results"] = [
            r for r in (search_results.get("vector_results") or []) if not _is_faq(r)
        ]
        search_results["graph_results"] = [
            r for r in (search_results.get("graph_results") or []) if not _is_faq(r)
        ]
        logger.info(
            "[faq-disabled] sid=%s vector=%d→%d graph=%d→%d (sync)",
            _sid_short_sync, _orig_v, len(search_results["vector_results"]),
            _orig_g, len(search_results["graph_results"]),
        )
    _t5 = time.monotonic()
    merged = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
        question=search_query,
        intent=analysis.intent,
        entities=analysis.entities,
        transcript_context=transcript_context,
        question_type=analysis.question_type,
    )
    _ms_merge = int((time.monotonic() - _t5) * 1000)

    _intent_str = analysis.intent.value if analysis.intent else "?"
    _qt_str = analysis.question_type.value if analysis.question_type else "?"

    if not merged.formatted_context.strip():
        if analysis.lang == "en":
            msg = (
                "I'm sorry, but I couldn't find any relevant information "
                "in the academic regulations.\n\n"
                "Please contact the Academic Affairs Office at +82-51-509-5182."
            )
        else:
            msg = (
                "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
                "다음을 확인해 주세요:\n"
                "- PDF 학사 안내 자료가 등록되어 있는지\n"
                "- 질문에 학번을 포함했는지 (예: 2023학번)"
            )
        _try_log(question, msg, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
        total_ms = _log_chat_sync_timing(
            t0=_t0, path="empty_context",
            follow_up_ms=_ms_follow_up, rewrite_ms=_ms_rewrite,
            analyze_ms=_ms_analyze, search_ms=_ms_search, merge_ms=_ms_merge,
            intent=_intent_str, question_type=_qt_str,
            follow_up_reason=follow_up_signal.reason,
        )
        return ChatResponse(
            answer=msg,
            intent=analysis.intent.value if analysis.intent else "",
            duration_ms=total_ms,
        )

    # M7 (2026-04-27): 논스트리밍 경로도 동일 — direct_answer LLM 우회 기본 OFF.
    # .env DIRECT_ANSWER_BYPASS_LLM=true 시 (구) 우회 동작 복구.
    if (
        settings.pipeline.direct_answer_bypass_llm
        and merged.direct_answer
        and analysis.lang != "en"
    ):
        # 멀티턴 컨텍스트 보존: direct_answer도 세션 history에 저장해야
        # 다음 턴 follow-up 감지·rewrite가 이전 주제를 참조할 수 있다.
        messages = session_data.get("messages", [])
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": merged.direct_answer, "rated": False, "rating": None})
        session_store.update(sid, "messages", messages)
        _try_log(question, merged.direct_answer, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
        total_ms = _log_chat_sync_timing(
            t0=_t0, path="direct_answer",
            follow_up_ms=_ms_follow_up, rewrite_ms=_ms_rewrite,
            analyze_ms=_ms_analyze, search_ms=_ms_search, merge_ms=_ms_merge,
            intent=_intent_str, question_type=_qt_str,
            follow_up_reason=follow_up_signal.reason,
        )
        return ChatResponse(
            answer=merged.direct_answer,
            source_urls=[SourceURL(title=u.get("title", ""), url=u.get("url", ""))
                         for u in (merged.source_urls or [])],
            intent=analysis.intent.value if analysis.intent else "",
            duration_ms=total_ms,
        )

    all_results = search_results["vector_results"] + search_results["graph_results"]
    llm_history = prior_messages if _conv_cfg.history_enabled else None
    cache_kwargs = _build_generation_cache_kwargs(
        search_query, merged, analysis, student_context, history=llm_history,
        is_follow_up=follow_up_signal.is_follow_up,
    )
    cached_answer = generator.get_cached_response(**cache_kwargs)
    if cached_answer:
        messages = session_data.get("messages", [])
        messages.append({"role": "user", "content": question})
        messages.append({"role": "assistant", "content": cached_answer, "rated": False, "rating": None})
        session_store.update(sid, "messages", messages)
        _try_log(question, cached_answer, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)
        total_ms = _log_chat_sync_timing(
            t0=_t0, path="cached",
            follow_up_ms=_ms_follow_up, rewrite_ms=_ms_rewrite,
            analyze_ms=_ms_analyze, search_ms=_ms_search, merge_ms=_ms_merge,
            intent=_intent_str, question_type=_qt_str,
            follow_up_reason=follow_up_signal.reason,
        )
        return ChatResponse(
            answer=cached_answer,
            source_urls=[SourceURL(title=u.get("title", ""), url=u.get("url", ""))
                         for u in (merged.source_urls or [])],
            results=[SearchResultItem(**r) for r in _serialize_results(all_results)],
            intent=analysis.intent.value if analysis.intent else "",
            duration_ms=total_ms,
        )

    # ── TRACE: [generator-IN] + [evidence] (sync path) ──
    from app.pipeline import evidence_trace as _ev_tr
    _sid_short = sid[:8] if sid else "-"
    _history_turns = (len(llm_history) // 2) if llm_history else 0
    _evidence_list = _ev_tr.build_evidence_list(all_results, max_items=16)
    _notice_url_n = sum(1 for ev in _evidence_list if ev.cite_key.startswith("URL:"))
    _pdf_pages = sorted({ev.page for ev in _evidence_list if ev.page})
    logger.info(
        "[generator-IN] sid=%s model=%s prompt_ctx_chars=%d intent=%s qt=%s lang=%s "
        "history_turns=%d search_n=%d notice_urls=%d pdf_pages=%s",
        _sid_short, settings.llm.model,
        len(merged.formatted_context or ""),
        analysis.intent.value if analysis.intent else "?",
        analysis.question_type.value if analysis.question_type else "?",
        analysis.lang or "ko", _history_turns, len(all_results),
        _notice_url_n, _pdf_pages,
    )
    for _ev in _evidence_list:
        _preview = (_ev.text or "").replace("\n", " ")[:60]
        logger.info(
            "[evidence] sid=%s #%d cite=%s doc=%r page=%s section=%r type=%s score=%.2f url=%s text=%r",
            _sid_short, _ev.chunk_idx + 1, _ev.cite_key, _ev.doc,
            _ev.page if _ev.page else "-", _ev.section, _ev.source_type,
            _ev.score, _ev.url or "-", _preview,
        )

    # LLM 생성 (전체 수집) — 백프레셔로 동시 LLM 호출 상한 적용 (OOM 방어).
    _t6 = time.monotonic()
    full_answer = ""
    async with _LLM_SEMAPHORE:
        async for token in generator.generate(
            question=search_query,
            context=merged.formatted_context,
            student_id=analysis.student_id,
            question_focus=analysis.entities.get("question_focus"),
            lang=analysis.lang,
            matched_terms=analysis.matched_terms,
            student_context=student_context,
            context_confidence=merged.context_confidence,
            question_type=analysis.question_type.value if analysis.question_type else None,
            intent=analysis.intent.value,
            entities=analysis.entities,
            history=llm_history,
        ):
            if token == "\x00CLEAR\x00":
                full_answer = ""
                continue
            full_answer += token
    _ms_gen = int((time.monotonic() - _t6) * 1000)

    # ── TRACE: [generator-OUT] (sync path) ──
    _ans_preview = (full_answer or "").replace("\n", " | ")[:200]
    logger.info(
        "[generator-OUT] sid=%s answer_chars=%d elapsed_ms=%d preview=%r",
        _sid_short, len(full_answer or ""), _ms_gen, _ans_preview,
    )
    # ── TRACE: [answer-evidence-map] + [answer-manifest] (sync path) ──
    try:
        _ans_map = _ev_tr.map_answer_to_evidence(full_answer or "", _evidence_list)
        for _item in _ans_map:
            logger.info(
                "[answer-evidence-map] sid=%s item=%s in_ctx=%s cites=%s",
                _sid_short, _item["item"], _item["in_context"],
                _item["matched_cites"] or ["NONE"],
            )
        if not _ans_map:
            logger.info(
                "[answer-evidence-map] sid=%s item=NO_UNITS in_ctx=- cites=- "
                "(non-numeric/qualitative answer)", _sid_short,
            )
    except Exception as _e:
        logger.debug("answer-evidence-map 실패 (sync): %s", _e)
    try:
        _mf = _ev_tr.check_answer_manifest(
            full_answer or "",
            analysis.intent.value if analysis.intent else "",
            question=search_query,
        )
        logger.info(
            "[answer-manifest] sid=%s %s required_missing=%s optional_missing=%s excluded_violations=%s",
            _sid_short, _mf.summary(),
            _mf.required_missing, _mf.optional_missing, _mf.excluded_violations,
        )
    except Exception as _e:
        logger.debug("answer-manifest 실패 (sync): %s", _e)

    # 빈 응답 방어
    if not full_answer.strip():
        if analysis.lang == "en":
            full_answer = "Sorry, I couldn't generate a response. Please try again."
        else:
            full_answer = "죄송합니다. 응답을 생성하지 못했습니다. 다시 시도해 주세요."
        logger.warning("LLM 빈 응답 (sync): question='%s'", question[:50])

    # ~ 이스케이프 (마크다운 취소선 방지)
    full_answer = re.sub(r'(?<!~)~(?!~)', r'\~', full_answer)

    _t7 = time.monotonic()
    # Phase 4 품질 게이트 (KO only)
    if analysis.lang != "en" and full_answer.strip():
        try:
            from app.pipeline.answer_units import (
                fill_from_context, verify_answer_against_context,
                verify_completeness,
            )
            from app.pipeline.response_validator import ResponseValidator as _RV

            _rv = _RV()
            if not _rv._is_no_context_response(full_answer):
                ok, reason = verify_answer_against_context(full_answer, merged.formatted_context)
                if not ok:
                    logger.warning("answer-context mismatch (sync): %s", reason)
                    full_answer = (
                        "제공된 자료에서 해당 내용을 정확히 확인하지 못했습니다. "
                        "학사지원팀(051-509-5182)에 문의하시기 바랍니다."
                    )
                else:
                    if not verify_completeness(search_query, full_answer, merged.formatted_context):
                        logger.debug("verify_completeness failed (sync), fill_from_context로 보완")
                    target_entity = analysis.entities.get("department") if analysis.entities else None
                    full_answer = fill_from_context(
                        search_query, full_answer, merged.formatted_context,
                        target_entity=target_entity,
                    )
        except Exception as e:
            logger.debug("Phase4 후처리 실패 (sync), 원본 유지: %s", e)

    # 응답 검증
    # ── TRACE: [validator-IN] (sync path) ──
    logger.info(
        "[validator-IN] sid=%s answer_chars=%d ctx_chars=%d search_n=%d",
        _sid_short, len(full_answer or ""),
        len(merged.formatted_context or ""), len(all_results),
    )
    passed = None
    warnings = []
    try:
        passed, warnings = validator.validate(
            answer=full_answer,
            context=merged.formatted_context,
            search_results=all_results,
        )
        if warnings:
            warning_text = "\n".join(f"- {w}" for w in warnings)
            full_answer += f"\n\n---\n*검증 경고:*\n{warning_text}"
    except Exception as _e:
        logger.debug("validator 실패 (sync): %s", _e)
    _ms_val = int((time.monotonic() - _t7) * 1000)
    # ── TRACE: [validator-OUT] (sync path) ──
    logger.info(
        "[validator-OUT] sid=%s passed=%s warnings=%d elapsed_ms=%d",
        _sid_short, passed, len(warnings or []), _ms_val,
    )

    # 연락처 꼬리말
    footer = _get_contact_footer(analysis.intent, analysis.entities, question)
    if footer:
        full_answer += footer

    generator.store_cached_response(full_answer, **cache_kwargs)

    # 메시지 이력 저장
    messages = session_data.get("messages", [])
    messages.append({"role": "user", "content": question})
    messages.append({"role": "assistant", "content": full_answer, "rated": False, "rating": None})
    session_store.update(sid, "messages", messages)

    # 로그
    _try_log(question, full_answer, sid, analysis, _t0, context_confidence=merged.context_confidence, user_id=user_id)

    total_ms = _log_chat_sync_timing(
        t0=_t0, path="generated",
        follow_up_ms=_ms_follow_up, rewrite_ms=_ms_rewrite,
        analyze_ms=_ms_analyze, search_ms=_ms_search, merge_ms=_ms_merge,
        generate_ms=_ms_gen, validate_ms=_ms_val,
        intent=_intent_str, question_type=_qt_str,
        follow_up_reason=follow_up_signal.reason,
    )
    return ChatResponse(
        answer=full_answer,
        source_urls=[SourceURL(title=u.get("title", ""), url=u.get("url", ""))
                     for u in (merged.source_urls or [])],
        results=[SearchResultItem(**r) for r in _serialize_results(all_results)],
        intent=analysis.intent.value if analysis.intent else "",
        duration_ms=total_ms,
    )


# ── 대화 이력 ──

@router.get("/history")
async def chat_history(session_id: str = Query(...)):
    """세션 대화 이력 조회."""
    data = session_store.get(session_id)
    if data is None:
        return {"messages": []}
    return {"messages": data.get("messages", [])}


@router.delete("/history")
async def clear_history(session_id: str = Query(...)):
    """세션 대화 이력 초기화."""
    session_store.update(session_id, "messages", [])
    return {"ok": True}


# ── FAQ 단건 조회 (알림 → 상세 답변 표시) ──

@router.get("/faq/{faq_id}")
async def get_faq_by_id(faq_id: str):
    """알림 클릭 시 수정된 FAQ 답변을 채팅 화면에 표시하기 위한 경량 조회.

    FAQ 본문은 공개 지식이므로 별도 인증 없음. id 매칭만 수행.
    faq_id 형식: ADMIN-YYYYMMDD-NNNN (관리자 추가) 또는 기존 FAQ-*.
    """
    import json as _json
    from pathlib import Path
    from app.config import settings

    def _load(path: Path) -> list:
        if not path.exists():
            return []
        try:
            data = _json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "faq" in data:
                data = data["faq"]
            return data if isinstance(data, list) else []
        except Exception:
            return []

    # codex P1: library/취업/외국학생지원팀 등 별도 도메인 FAQ까지 조회 대상에 포함.
    # 라이브러리 FAQ 코퍼스 상세 조회가 누락되던 회귀 차단.
    items = (
        _load(Path(settings.admin_faq.academic_faq_path))
        + _load(Path(settings.admin_faq.admin_faq_path))
        + _load(Path(settings.admin_faq.library_faq_path))
    )
    for it in items:
        if it.get("id") == faq_id:
            return {
                "id": faq_id,
                "category": it.get("category", ""),
                "question": it.get("question", ""),
                "answer": it.get("answer", ""),
            }
    return {"id": faq_id, "category": "", "question": "", "answer": ""}


# ── helper ──

def _persist_user_message(
    user_id: Optional[int],
    sid: str,
    question: str,
    answer: str,
    intent: str,
) -> Optional[int]:
    """로그인 사용자면 chat_messages 테이블에 저장하고 row id 반환.

    user_id=None 이거나 저장 실패 시 None 반환 (채팅 자체는 영향 없음).
    원본 질문·답변(PII 마스킹 전)을 저장 — 본인만 조회 가능하므로 허용.

    X-Test-Mode 헤더로 should_skip_log()가 참이면 DB 저장도 건너뛴다
    (평가/회귀 테스트가 실사용자 이력을 오염시키지 않도록).
    """
    from app.logging.chat_logger import should_skip_log
    if should_skip_log():
        return None
    if user_id is None:
        return None
    try:
        from backend.database import insert_chat_message
        return insert_chat_message(
            user_id=user_id,
            session_id=sid,
            question=question,
            answer=answer,
            intent=intent or "",
        )
    except Exception as exc:
        logger.error("chat_messages 저장 실패 (user_id=%s): %s", user_id, exc)
        return None


def _try_log(
    question: str,
    answer: str,
    sid: str,
    analysis,
    _t0: float,
    context_confidence: float | None = None,
    user_id: Optional[int] = None,
) -> Optional[int]:
    """Q&A 로그 기록. PIIRedactor 실패 시에도 원본으로 기록.

    user_id 가 있으면 chat_messages DB에도 저장 (원본, 본인만 조회 가능).
    JSONL 로그에는 PII 마스킹된 버전 + user_id/chat_message_id 필드 포함.
    반환값: chat_messages row id (비로그인은 None).
    """
    duration_ms = int((time.monotonic() - _t0) * 1000)
    intent_name = analysis.intent.name if analysis.intent else ""
    student_id = analysis.student_id

    # 1) 개인 DB 저장 (로그인 사용자만) — 원본 그대로
    chat_message_id = _persist_user_message(user_id, sid, question, answer, intent_name)

    # 2) PIIRedactor가 있으면 JSONL 로그용 마스킹
    q_log, a_log = question, answer
    try:
        from app.transcript.security import PIIRedactor
        q_log = PIIRedactor.redact_for_log(question)
        a_log = PIIRedactor.redact_for_log(answer)
    except Exception:
        pass

    chat_logger = get_chat_logger()
    if not chat_logger:
        try:
            from app.logging import ChatLogger
            chat_logger = ChatLogger()
        except Exception as e:
            logger.error("ChatLogger 생성 실패: %s", e)
            return chat_message_id

    try:
        chat_logger.log(
            question=q_log,
            answer=a_log,
            session_id=sid,
            intent=intent_name,
            student_id=student_id,
            duration_ms=duration_ms,
            context_confidence=context_confidence,
            user_id=user_id,
            chat_message_id=chat_message_id,
        )
    except Exception as e:
        logger.error("대화 로그 기록 실패: %s", e)

    return chat_message_id


def _try_log_simple(
    question: str,
    answer: str,
    sid: str,
    intent: str,
    _t0: float,
    user_id: Optional[int] = None,
) -> Optional[int]:
    """analysis 객체 없이 로그 기록 (연락처 단락 등)."""
    duration_ms = int((time.monotonic() - _t0) * 1000)
    chat_message_id = _persist_user_message(user_id, sid, question, answer, intent or "")
    chat_logger = get_chat_logger()
    if not chat_logger:
        try:
            from app.logging import ChatLogger
            chat_logger = ChatLogger()
        except Exception:
            return chat_message_id
    try:
        chat_logger.log(
            question=question, answer=answer,
            session_id=sid, intent=intent,
            student_id=None, duration_ms=duration_ms,
            user_id=user_id,
            chat_message_id=chat_message_id,
        )
    except Exception:
        pass
    return chat_message_id
