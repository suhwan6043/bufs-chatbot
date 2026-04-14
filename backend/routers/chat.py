"""
채팅 SSE 스트리밍 + 논스트리밍 엔드포인트.

chat_app.py:generate_response_stream() (1701~1914줄) 로직을 1:1 이식.
파이프라인 함수 호출은 동일 — st.session_state만 session_store로 교체.
"""

import json
import logging
import re
import time
from typing import AsyncGenerator

from fastapi import APIRouter, Query
from sse_starlette.sse import EventSourceResponse

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


def _enrich_analysis(question: str, analysis, router_inst, session_data: dict):
    """
    chat_app.py:_enrich_analysis() 이식.
    st.session_state 대신 session_data dict 사용.
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
    if _profile.get("student_type") and _profile["student_type"] != "내국인":
        analysis.student_type = _profile["student_type"]

    # 성적표 기반 컨텍스트
    transcript_data = session_data.get("transcript")
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

    return analysis, transcript_context, student_context


def _serialize_results(results: list) -> list[dict]:
    """SearchResult 리스트를 JSON 직렬화 가능 dict로 변환."""
    items = []
    for r in results[:10]:  # 최대 10개
        items.append({
            "text": (r.text or "")[:200],
            "score": round(float(r.score or 0), 4),
            "source": r.source or "",
            "page_number": getattr(r, "page_number", 0) or 0,
            "doc_type": (r.metadata or {}).get("doc_type", ""),
            "in_context": bool((r.metadata or {}).get("in_context")),
        })
    return items


# ── SSE 스트리밍 엔드포인트 ──

@router.get("/stream")
async def chat_stream(
    session_id: str = Query(..., description="세션 ID"),
    question: str = Query(..., min_length=1, max_length=2000, description="질문"),
):
    """
    GET /api/chat/stream?session_id=X&question=Y → SSE 스트리밍.

    이벤트 타입:
    - token: {"token": "..."} — 토큰 단위 스트리밍
    - clear: {} — EN 원패스 전환 시 플레이스홀더 초기화
    - done: {answer, source_urls, results, intent, duration_ms} — 완료
    - error: {message} — 에러
    """

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

        # 연락처 단락 처리
        contact_answer = _format_contact_answer(question)
        if contact_answer:
            try:
                _cl = get_chat_logger()
                if not _cl:
                    from app.logging import ChatLogger
                    _cl = ChatLogger()
                _cl.log(
                    question=question,
                    answer=contact_answer,
                    session_id=sid,
                    intent="CONTACT",
                    student_id=None,
                    duration_ms=int((time.monotonic() - _t0) * 1000),
                )
            except Exception as e:
                logger.error("연락처 로그 기록 실패: %s", e)
            yield {"event": "done", "data": json.dumps({
                "answer": contact_answer,
                "source_urls": [],
                "results": [],
                "intent": "CONTACT",
                "duration_ms": int((time.monotonic() - _t0) * 1000),
            }, ensure_ascii=False)}
            return

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

        # Stage 1: 질문 분석
        _t1 = time.monotonic()
        analysis = analyzer.analyze(question)
        lang = session_data.get("lang", "ko")
        if lang == "en":
            analysis.lang = "en"
        analysis, transcript_context, student_context = _enrich_analysis(
            question, analysis, router_inst, session_data
        )
        _ms_analyze = int((time.monotonic() - _t1) * 1000)

        # Stage 2: 검색
        _t2 = time.monotonic()
        search_results = router_inst.route_and_search(question, analysis)
        _ms_search = int((time.monotonic() - _t2) * 1000)

        # Stage 3: 컨텍스트 병합
        _t3 = time.monotonic()
        merged = merger.merge(
            vector_results=search_results["vector_results"],
            graph_results=search_results["graph_results"],
            question=question,
            intent=analysis.intent,
            entities=analysis.entities,
            transcript_context=transcript_context,
            question_type=analysis.question_type,
        )
        _ms_merge = int((time.monotonic() - _t3) * 1000)

        # P4: 저신뢰 재시도 (1회)
        _t4 = time.monotonic()
        if (
            merged.context_confidence is not None
            and merged.context_confidence < 0.5
            and not merged.direct_answer
            and not transcript_context
        ):
            try:
                rewritten = await generator.rewrite_query(
                    question=question,
                    lang=analysis.lang or "ko",
                    intent=analysis.intent.value if analysis.intent else None,
                )
                if rewritten and rewritten != question:
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
                        question=question,
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
            _try_log(question, msg, sid, analysis, _t0)
            return

        # direct_answer 단락 응답 (KO only)
        if merged.direct_answer and analysis.lang != "en":
            yield {"event": "done", "data": json.dumps({
                "answer": merged.direct_answer,
                "source_urls": [{"title": u.get("title", ""), "url": u.get("url", "")} for u in (merged.source_urls or [])],
                "results": _serialize_results(merged.vector_results + merged.graph_results),
                "intent": analysis.intent.value if analysis.intent else "",
                "duration_ms": int((time.monotonic() - _t0) * 1000),
            }, ensure_ascii=False)}
            _try_log(question, merged.direct_answer, sid, analysis, _t0)
            return

        # Stage 5: LLM 스트리밍 생성
        _t5 = time.monotonic()
        full_answer = ""
        try:
            async for token in generator.generate(
                question=question,
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
            ):
                if token == "\x00CLEAR\x00":
                    full_answer = ""
                    yield {"event": "clear", "data": "{}"}
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
                        # Step 3: 이분법 완전성 검증
                        if not verify_completeness(question, full_answer, merged.formatted_context):
                            logger.debug("verify_completeness failed, fill_from_context로 보완")
                        # Fix D: 누락 unit 보충
                        target_entity = analysis.entities.get("department") if analysis.entities else None
                        full_answer = fill_from_context(
                            question, full_answer, merged.formatted_context,
                            target_entity=target_entity,
                        )
            except Exception as e:
                logger.debug("Phase4 후처리 실패, 원본 유지: %s", e)

        # Stage 6: 응답 검증
        _t6 = time.monotonic()
        all_results = search_results["vector_results"] + search_results["graph_results"]
        try:
            passed, warnings = validator.validate(
                answer=full_answer,
                context=merged.formatted_context,
                search_results=all_results,
            )
            if warnings:
                warning_text = "\n".join(f"- {w}" for w in warnings)
                full_answer += f"\n\n---\n*검증 경고:*\n{warning_text}"
        except Exception:
            pass
        _ms_val = int((time.monotonic() - _t6) * 1000)

        # 연락처 꼬리말
        footer = _get_contact_footer(analysis.intent, analysis.entities, question)
        if footer:
            full_answer += footer

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
        _try_log(question, full_answer, sid, analysis, _t0)

        # stage별 타이밍 로그
        _ms_total = int((time.monotonic() - _t0) * 1000)
        _timing_msg = (
            f"PIPELINE_TIMING total={_ms_total}ms analyze={_ms_analyze}ms search={_ms_search}ms merge={_ms_merge}ms "
            f"retry={_ms_retry}ms generate={_ms_gen}ms validate={_ms_val}ms "
            f"intent={analysis.intent.value if analysis.intent else '?'} "
            f"qt={analysis.question_type.value if analysis.question_type else '?'}"
        )
        print(_timing_msg, flush=True)

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

@router.post("", response_model=ChatResponse)
async def chat_sync(
    session_id: str = Query(...),
    question: str = Query(..., min_length=1, max_length=2000),
):
    """POST /api/chat — 논스트리밍 채팅 (테스트/평가용)."""
    _t0 = time.monotonic()
    sid, session_data = session_store.get_or_create(session_id)

    # 연락처 단락
    contact = _format_contact_answer(question)
    if contact:
        _try_log_simple(question, contact, sid, "CONTACT", _t0)
        return ChatResponse(answer=contact, intent="CONTACT",
                            duration_ms=int((time.monotonic() - _t0) * 1000))

    analyzer = get_analyzer()
    router_inst = get_router()
    merger = get_merger()
    generator = get_generator()
    validator = get_validator()

    analysis = analyzer.analyze(question)
    lang = session_data.get("lang", "ko")
    if lang == "en":
        analysis.lang = "en"
    analysis, transcript_context, student_context = _enrich_analysis(
        question, analysis, router_inst, session_data
    )

    search_results = router_inst.route_and_search(question, analysis)
    merged = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
        question=question,
        intent=analysis.intent,
        entities=analysis.entities,
        transcript_context=transcript_context,
        question_type=analysis.question_type,
    )

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
        _try_log(question, msg, sid, analysis, _t0)
        return ChatResponse(
            answer=msg,
            intent=analysis.intent.value if analysis.intent else "",
            duration_ms=int((time.monotonic() - _t0) * 1000),
        )

    if merged.direct_answer and analysis.lang != "en":
        _try_log(question, merged.direct_answer, sid, analysis, _t0)
        return ChatResponse(
            answer=merged.direct_answer,
            source_urls=[SourceURL(title=u.get("title", ""), url=u.get("url", ""))
                         for u in (merged.source_urls or [])],
            intent=analysis.intent.value if analysis.intent else "",
            duration_ms=int((time.monotonic() - _t0) * 1000),
        )

    # LLM 생성 (전체 수집)
    full_answer = ""
    async for token in generator.generate(
        question=question,
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
    ):
        if token == "\x00CLEAR\x00":
            full_answer = ""
            continue
        full_answer += token

    # 빈 응답 방어
    if not full_answer.strip():
        if analysis.lang == "en":
            full_answer = "Sorry, I couldn't generate a response. Please try again."
        else:
            full_answer = "죄송합니다. 응답을 생성하지 못했습니다. 다시 시도해 주세요."
        logger.warning("LLM 빈 응답 (sync): question='%s'", question[:50])

    # ~ 이스케이프 (마크다운 취소선 방지)
    full_answer = re.sub(r'(?<!~)~(?!~)', r'\~', full_answer)

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
                    if not verify_completeness(question, full_answer, merged.formatted_context):
                        logger.debug("verify_completeness failed (sync), fill_from_context로 보완")
                    target_entity = analysis.entities.get("department") if analysis.entities else None
                    full_answer = fill_from_context(
                        question, full_answer, merged.formatted_context,
                        target_entity=target_entity,
                    )
        except Exception as e:
            logger.debug("Phase4 후처리 실패 (sync), 원본 유지: %s", e)

    # 응답 검증
    all_results = search_results["vector_results"] + search_results["graph_results"]
    try:
        passed, warnings = validator.validate(
            answer=full_answer,
            context=merged.formatted_context,
            search_results=all_results,
        )
        if warnings:
            warning_text = "\n".join(f"- {w}" for w in warnings)
            full_answer += f"\n\n---\n*검증 경고:*\n{warning_text}"
    except Exception:
        pass

    # 연락처 꼬리말
    footer = _get_contact_footer(analysis.intent, analysis.entities, question)
    if footer:
        full_answer += footer

    # 메시지 이력 저장
    messages = session_data.get("messages", [])
    messages.append({"role": "user", "content": question})
    messages.append({"role": "assistant", "content": full_answer, "rated": False, "rating": None})
    session_store.update(sid, "messages", messages)

    # 로그
    _try_log(question, full_answer, sid, analysis, _t0)

    return ChatResponse(
        answer=full_answer,
        source_urls=[SourceURL(title=u.get("title", ""), url=u.get("url", ""))
                     for u in (merged.source_urls or [])],
        results=[SearchResultItem(**r) for r in _serialize_results(all_results)],
        intent=analysis.intent.value if analysis.intent else "",
        duration_ms=int((time.monotonic() - _t0) * 1000),
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


# ── helper ──

def _try_log(question: str, answer: str, sid: str, analysis, _t0: float):
    """Q&A 로그 기록. PIIRedactor 실패 시에도 원본으로 기록."""
    duration_ms = int((time.monotonic() - _t0) * 1000)
    intent_name = analysis.intent.name if analysis.intent else ""
    student_id = analysis.student_id

    # PIIRedactor가 있으면 마스킹, 없으면 원본 사용
    q_log, a_log = question, answer
    try:
        from app.transcript.security import PIIRedactor
        q_log = PIIRedactor.redact_for_log(question)
        a_log = PIIRedactor.redact_for_log(answer)
    except Exception:
        pass  # PIIRedactor 실패 시 원본 그대로 기록

    # 싱글톤 로거 먼저 시도, 없으면 직접 생성
    chat_logger = get_chat_logger()
    if not chat_logger:
        try:
            from app.logging import ChatLogger
            chat_logger = ChatLogger()
        except Exception as e:
            logger.error("ChatLogger 생성 실패: %s", e)
            return

    try:
        chat_logger.log(
            question=q_log,
            answer=a_log,
            session_id=sid,
            intent=intent_name,
            student_id=student_id,
            duration_ms=duration_ms,
        )
    except Exception as e:
        logger.error("대화 로그 기록 실패: %s", e)


def _try_log_simple(question: str, answer: str, sid: str, intent: str, _t0: float):
    """analysis 객체 없이 로그 기록 (연락처 단락 등)."""
    duration_ms = int((time.monotonic() - _t0) * 1000)
    chat_logger = get_chat_logger()
    if not chat_logger:
        try:
            from app.logging import ChatLogger
            chat_logger = ChatLogger()
        except Exception:
            return
    try:
        chat_logger.log(
            question=question, answer=answer,
            session_id=sid, intent=intent,
            student_id=None, duration_ms=duration_ms,
        )
    except Exception:
        pass
