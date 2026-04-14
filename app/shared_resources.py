"""
프로세스 전역 공유 리소스 싱글톤

목적:
  Streamlit 메인 스레드(chat_app.py)와 APScheduler 백그라운드 스레드(crawl_scheduler.py)가
  ChromaStore / Embedder를 동일한 인스턴스로 공유합니다.

문제:
  각 스레드가 별도의 ChromaStore 인스턴스를 만들면 같은 data/chromadb/ 디렉토리에
  동시에 접근하게 되어 HNSW lock 충돌이 발생합니다.
    RuntimeError: Could not add to HNSW index

해결:
  Embedder와 ChromaStore 각각 별도 락 사용 (공유 락 사용 시 데드락 발생).
  get_chroma_store() 가 get_embedder() 를 내부 호출하므로 락이 달라야 합니다.
"""

import logging
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.embedding import Embedder
    from app.vectordb import ChromaStore
    from app.pipeline.translator import ContextTranslator

logger = logging.getLogger(__name__)

_embedder_lock = threading.Lock()
_chroma_lock = threading.Lock()
_translator_lock = threading.Lock()
_bm25_lock = threading.Lock()
_reranker_lock = threading.Lock()
_embedder = None
_chroma_store = None
_translator = None
_bm25_index = None
_reranker_model = None


def get_embedder() -> "Embedder":
    """프로세스 전역 Embedder 싱글톤을 반환합니다."""
    global _embedder
    if _embedder is None:
        with _embedder_lock:
            if _embedder is None:
                logger.info("[shared] Embedder 초기화 중...")
                from app.embedding import Embedder
                _embedder = Embedder()
                logger.info("[shared] Embedder 초기화 완료")
    return _embedder


def get_translator() -> "ContextTranslator":
    """
    프로세스 전역 ContextTranslator 싱글톤을 반환합니다.

    최초 호출 시 인스턴스를 생성하고 백그라운드 스레드에서 warmup()을 실행합니다.
    warmup은 M2M-100 모델을 미리 로드하여 첫 번째 사용자 요청의 cold start를 방지합니다.
    """
    global _translator
    if _translator is None:
        with _translator_lock:
            if _translator is None:
                logger.info("[shared] ContextTranslator 초기화 중...")
                from app.pipeline.translator import ContextTranslator
                _translator = ContextTranslator()
                # 모델 로드를 백그라운드 스레드에서 실행 (앱 기동 블로킹 방지)
                t = threading.Thread(
                    target=_translator.warmup,
                    name="translator-warmup",
                    daemon=True,
                )
                t.start()
                logger.info("[shared] ContextTranslator 초기화 완료 (warmup 백그라운드 실행 중)")
    return _translator


def get_bm25_index():
    """프로세스 전역 BM25Index 싱글톤을 반환합니다.

    원칙 2(비용·지연): ChromaDB 전량 로드 후 인메모리 BM25 인덱스 빌드.
    ~1,200 청크 기준 <1초. Streamlit 시작 시 1회 실행.
    """
    global _bm25_index
    if _bm25_index is None:
        with _bm25_lock:
            if _bm25_index is None:
                logger.info("[shared] BM25Index 초기화 중...")
                chroma = get_chroma_store()
                from app.vectordb.bm25_index import BM25Index
                _bm25_index = BM25Index(chroma)
                _bm25_index.build()
                logger.info("[shared] BM25Index 초기화 완료 (%d 문서)", _bm25_index.doc_count)
    return _bm25_index


def get_reranker_model():
    """프로세스 전역 CrossEncoder(Reranker) 싱글톤을 반환합니다.

    시작 시 동기 로드하여 첫 요청 시 lazy-load segfault를 방지합니다.
    segfault 발생 시 프로세스가 종료되며 run_backend.sh가 자동 재시작합니다.
    """
    global _reranker_model
    if _reranker_model is None:
        with _reranker_lock:
            if _reranker_model is None:
                from app.config import settings
                logger.info("[shared] Reranker CrossEncoder 로드 중: %s", settings.reranker.model_name)
                from sentence_transformers import CrossEncoder
                _reranker_model = CrossEncoder(
                    settings.reranker.model_name,
                    device=settings.reranker.device,
                )
                logger.info("[shared] Reranker CrossEncoder 로드 완료")
    return _reranker_model


def get_chroma_store() -> "ChromaStore":
    """
    프로세스 전역 ChromaStore 싱글톤을 반환합니다.

    주의: _chroma_lock 바깥에서 get_embedder()를 먼저 호출합니다.
    이렇게 해야 get_chroma_store() → get_embedder() 호출 시
    두 함수가 서로 다른 락을 사용하여 데드락이 발생하지 않습니다.
    """
    global _chroma_store
    if _chroma_store is None:
        embedder = get_embedder()          # _chroma_lock 밖에서 먼저 확보
        with _chroma_lock:
            if _chroma_store is None:      # double-check
                logger.info("[shared] ChromaStore 초기화 중...")
                from app.vectordb import ChromaStore
                _chroma_store = ChromaStore(embedder=embedder)
                logger.info("[shared] ChromaStore 초기화 완료")
    return _chroma_store
