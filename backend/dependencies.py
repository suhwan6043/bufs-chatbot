"""
파이프라인 싱글톤 의존성 주입 — app/shared_resources.py 래핑.

기존 파이프라인 코드를 **수정 없이** 그대로 사용한다.
FastAPI lifespan에서 init_all()을 호출하여 워밍업.
"""

import logging
import threading

logger = logging.getLogger(__name__)

# ── 모듈 레벨 싱글톤 (한 번만 초기화) ──
_analyzer = None
_router = None
_merger = None
_generator = None
_validator = None
_chat_logger = None
_initialized = False
_lock = threading.Lock()


def init_all():
    """FastAPI lifespan startup에서 호출. 모든 파이프라인 컴포넌트 초기화."""
    global _analyzer, _router, _merger, _generator, _validator, _chat_logger, _initialized

    if _initialized:
        return

    with _lock:
        if _initialized:
            return

        logger.info("파이프라인 컴포넌트 초기화 시작...")

        from app.shared_resources import get_chroma_store, get_embedder, get_bm25_index
        from app.pipeline.query_analyzer import QueryAnalyzer
        from app.pipeline.query_router import QueryRouter
        from app.pipeline.context_merger import ContextMerger
        from app.pipeline.answer_generator import AnswerGenerator
        from app.pipeline.response_validator import ResponseValidator
        from app.graphdb.academic_graph import AcademicGraph
        from app.logging import ChatLogger

        chroma_store = get_chroma_store()
        embedder = get_embedder()
        bm25_index = get_bm25_index()
        academic_graph = AcademicGraph()

        _analyzer = QueryAnalyzer(embedder=embedder)
        _router = QueryRouter(
            chroma_store=chroma_store,
            academic_graph=academic_graph,
            bm25_index=bm25_index,
        )
        _merger = ContextMerger()
        _generator = AnswerGenerator()
        _validator = ResponseValidator()
        _chat_logger = ChatLogger()

        # ── 모든 ML 모델을 시작 시 강제 로드 (lazy-load segfault 방지) ──
        # Embedder 모델 강제 로드 (lazy property 트리거)
        _ = embedder.model
        logger.info("Embedder 모델 강제 로드 완료")

        # Reranker 모델 강제 로드 + QueryRouter에 주입
        from app.shared_resources import get_reranker_model
        from app.pipeline.reranker import Reranker as _Rr
        _preloaded_model = get_reranker_model()
        _rr = _Rr(model=_preloaded_model)
        _router._reranker = _rr
        logger.info("Reranker 모델 강제 로드 완료 (QueryRouter에 주입됨)")

        # Reranker warm prediction (첫 추론 segfault 방지)
        try:
            _preloaded_model.predict([["test", "test"]])
            logger.info("Reranker warm prediction 완료")
        except Exception as e:
            logger.warning("Reranker warm prediction 실패: %s", e)

        # ChromaDB warm query (ONNX 내장 모델 + HNSW 인덱스 사전 로드)
        try:
            test_emb = embedder.embed_query("테스트")
            chroma_store.search(test_emb.tolist(), n_results=1)
            logger.info("ChromaDB warm query 완료")
        except Exception as e:
            logger.warning("ChromaDB warm query 실패: %s", e)

        # 스케줄러 자동 시작 (Streamlit의 @st.cache_resource 패턴과 동일)
        try:
            from app.scheduler import get_scheduler
            get_scheduler().start()
        except Exception as e:
            logger.warning("스케줄러 시작 실패 (수동 트리거는 작동): %s", e)

        _initialized = True
        logger.info("파이프라인 컴포넌트 초기화 완료")


# ── FastAPI Depends() 용 getter ──

def get_analyzer():
    return _analyzer


def get_router():
    return _router


def get_merger():
    return _merger


def get_generator():
    return _generator


def get_validator():
    return _validator


def get_chat_logger():
    return _chat_logger
