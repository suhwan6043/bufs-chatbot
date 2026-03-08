"""
BUFS Academic Chatbot - Streamlit 채팅 UI
스트리밍 응답 지원, 학번 필터링, 대화 이력 관리
"""

import asyncio
import logging

import streamlit as st

from app.config import settings
from app.embedding import Embedder
from app.vectordb import ChromaStore
from app.graphdb import AcademicGraph
from app.pipeline import (
    QueryAnalyzer,
    QueryRouter,
    ContextMerger,
    AnswerGenerator,
    ResponseValidator,
)

logger = logging.getLogger(__name__)


def init_components():
    """파이프라인 컴포넌트를 초기화합니다 (세션당 1회)."""
    if "initialized" not in st.session_state:
        with st.spinner("시스템 초기화 중..."):
            embedder = Embedder()
            chroma_store = ChromaStore(embedder=embedder)
            academic_graph = AcademicGraph()

            st.session_state.analyzer = QueryAnalyzer()
            st.session_state.router = QueryRouter(
                chroma_store=chroma_store,
                academic_graph=academic_graph,
            )
            st.session_state.merger = ContextMerger()
            st.session_state.generator = AnswerGenerator()
            st.session_state.validator = ResponseValidator()
            st.session_state.chroma_store = chroma_store
            st.session_state.messages = []
            st.session_state.initialized = True


async def generate_response(question: str) -> str:
    """질문에 대한 응답을 생성합니다."""
    analyzer = st.session_state.analyzer
    router = st.session_state.router
    merger = st.session_state.merger
    generator = st.session_state.generator
    validator = st.session_state.validator

    # 1. 쿼리 분석
    analysis = analyzer.analyze(question)
    logger.info(f"Intent: {analysis.intent}, Student ID: {analysis.student_id}")

    # 2. 검색 라우팅
    search_results = router.route_and_search(question, analysis)

    # 3. 컨텍스트 병합
    merged = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
    )

    # 컨텍스트가 비어있는 경우
    if not merged.formatted_context.strip():
        return (
            "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
            "다음을 확인해 주세요:\n"
            "- PDF 학사 안내 자료가 등록되어 있는지\n"
            "- 질문에 학번을 포함했는지 (예: 2023학번)"
        )

    if merged.direct_answer:
        return merged.direct_answer

    # 4. 답변 생성 (전체 응답)
    answer = await generator.generate_full(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
    )

    # 5. 응답 검증
    all_results = search_results["vector_results"] + search_results["graph_results"]
    passed, warnings = validator.validate(
        answer=answer,
        context=merged.formatted_context,
        search_results=all_results,
    )

    if warnings:
        warning_text = "\n".join(f"- {w}" for w in warnings)
        answer += f"\n\n---\n*검증 경고:*\n{warning_text}"

    return answer


async def generate_response_stream(question: str, placeholder):
    """스트리밍으로 응답을 생성합니다."""
    analyzer = st.session_state.analyzer
    router = st.session_state.router
    merger = st.session_state.merger
    generator = st.session_state.generator
    validator = st.session_state.validator

    # 1~3단계: 분석, 검색, 병합
    analysis = analyzer.analyze(question)
    search_results = router.route_and_search(question, analysis)
    merged = merger.merge(
        vector_results=search_results["vector_results"],
        graph_results=search_results["graph_results"],
    )

    if not merged.formatted_context.strip():
        msg = (
            "죄송합니다. 해당 질문에 대한 관련 정보를 찾을 수 없습니다.\n\n"
            "다음을 확인해 주세요:\n"
            "- PDF 학사 안내 자료가 등록되어 있는지\n"
            "- 질문에 학번을 포함했는지 (예: 2023학번)"
        )
        placeholder.markdown(msg)
        return msg

    if merged.direct_answer:
        placeholder.markdown(merged.direct_answer)
        return merged.direct_answer

    # 4. 스트리밍 답변 생성
    full_answer = ""
    async for token in generator.generate(
        question=question,
        context=merged.formatted_context,
        student_id=analysis.student_id,
    ):
        full_answer += token
        placeholder.markdown(full_answer + "▌")

    placeholder.markdown(full_answer)

    # 5. 응답 검증
    all_results = search_results["vector_results"] + search_results["graph_results"]
    passed, warnings = validator.validate(
        answer=full_answer,
        context=merged.formatted_context,
        search_results=all_results,
    )

    if warnings:
        warning_text = "\n".join(f"- {w}" for w in warnings)
        full_answer += f"\n\n---\n*검증 경고:*\n{warning_text}"
        placeholder.markdown(full_answer)

    return full_answer


def main():
    st.set_page_config(
        page_title="BUFS 학사 안내 챗봇",
        page_icon="🎓",
        layout="centered",
    )

    st.title("BUFS 학사 안내 챗봇")
    st.caption("부산외국어대학교 학사 안내 AI - 학사규정, 졸업요건, 수강신청 등을 물어보세요")

    # 사이드바
    with st.sidebar:
        st.header("설정")

        # DB 상태 표시
        try:
            init_components()
            chunk_count = st.session_state.chroma_store.count()
            st.success(f"시스템 정상 | 등록 문서: {chunk_count}개 청크")
        except Exception as e:
            st.error(f"초기화 오류: {e}")
            return

        # Ollama 상태
        async def check_ollama():
            return await st.session_state.generator.health_check()

        ollama_ok = asyncio.run(check_ollama())
        if ollama_ok:
            st.success(f"Ollama 연결됨 ({settings.ollama.model})")
        else:
            st.warning("Ollama 서버 미연결 - `ollama serve` 실행 필요")

        st.divider()

        if st.button("대화 초기화"):
            st.session_state.messages = []
            st.rerun()

        st.markdown(
            "---\n"
            f"**모델:** {settings.ollama.model}\n\n"
            f"**임베딩:** {settings.embedding.model_name}\n\n"
            f"**컨텍스트:** {settings.ollama.num_ctx} 토큰"
        )

    # 대화 이력 표시
    for msg in st.session_state.messages:
        with st.chat_message(msg["role"]):
            st.markdown(msg["content"])

    # 사용자 입력
    if prompt := st.chat_input("질문을 입력하세요 (예: 2023학번 졸업요건 알려줘)"):
        # 사용자 메시지 표시
        st.session_state.messages.append({"role": "user", "content": prompt})
        with st.chat_message("user"):
            st.markdown(prompt)

        # AI 응답 생성
        with st.chat_message("assistant"):
            placeholder = st.empty()
            answer = asyncio.run(
                generate_response_stream(prompt, placeholder)
            )
            st.session_state.messages.append(
                {"role": "assistant", "content": answer}
            )


if __name__ == "__main__":
    main()
