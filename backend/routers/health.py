"""헬스체크 엔드포인트."""

from fastapi import APIRouter

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
async def health():
    """기본 헬스체크 + 파이프라인 초기화 상태."""
    from backend.dependencies import (
        get_analyzer, get_router, get_merger, get_generator, get_validator,
    )
    pipeline_ready = all([
        get_analyzer(), get_router(), get_merger(), get_generator(), get_validator(),
    ])
    return {
        "status": "ok" if pipeline_ready else "initializing",
        "version": "0.3.0",
        "pipeline_ready": pipeline_ready,
    }


@router.get("/search-timing")
async def search_timing(question: str = "2023학번 이후 학생의 한 학기 최대 수강신청 학점은 얼마인가?"):
    """Search 파이프라인 단계별 타이밍 계측."""
    import time
    from backend.dependencies import get_analyzer, get_router

    analyzer = get_analyzer()
    router_inst = get_router()

    # 1. Analyze
    t = time.monotonic()
    analysis = analyzer.analyze(question)
    ms_analyze = int((time.monotonic() - t) * 1000)

    # 2. Embedding
    t = time.monotonic()
    q_emb = router_inst.chroma_store.embedder.embed_query(question)
    ms_embed = int((time.monotonic() - t) * 1000)

    # 3. ChromaDB Phase 1 search
    from app.config import settings
    n_cand = max(15, settings.reranker.candidate_k)
    preferred = ["domestic", "guide", "faq"]

    t = time.monotonic()
    candidates = router_inst.chroma_store.search(
        query=question, n_results=n_cand,
        student_id=analysis.student_id,
        doc_type=preferred, query_embedding=q_emb,
    )
    ms_chroma_p1 = int((time.monotonic() - t) * 1000)

    # 4. BM25 search
    ms_bm25 = 0
    bm25_count = 0
    if router_inst.bm25_index and router_inst.bm25_index.is_built:
        t = time.monotonic()
        bm25_results = router_inst.bm25_index.search(question, 20, preferred)
        ms_bm25 = int((time.monotonic() - t) * 1000)
        bm25_count = len(bm25_results)

    # 5. Graph search
    t = time.monotonic()
    graph_results = router_inst.academic_graph.query_to_search_results(
        question=question, student_id=analysis.student_id,
        intent=analysis.intent, entities=analysis.entities,
    )
    ms_graph = int((time.monotonic() - t) * 1000)

    # 6. Reranker
    ms_reranker = 0
    reranker = router_inst.reranker
    if reranker and len(candidates) > 3:
        t = time.monotonic()
        reranked = reranker.rerank(
            query=question, results=candidates,
            top_k=settings.reranker.top_k, analysis=analysis,
        )
        ms_reranker = int((time.monotonic() - t) * 1000)

    total = ms_analyze + ms_embed + ms_chroma_p1 + ms_bm25 + ms_graph + ms_reranker
    return {
        "question": question,
        "total_ms": total,
        "stages": {
            "analyze": ms_analyze,
            "embedding": ms_embed,
            "chroma_phase1": ms_chroma_p1,
            "bm25": ms_bm25,
            "graph": ms_graph,
            "reranker": ms_reranker,
        },
        "counts": {
            "chroma_candidates": len(candidates),
            "bm25_results": bm25_count,
            "graph_results": len(graph_results),
        },
    }


@router.get("/llm")
async def health_llm():
    """LLM 서버 연결 상태 확인."""
    from backend.dependencies import get_generator

    generator = get_generator()
    if generator is None:
        return {"available": False, "model": "", "error": "Generator not initialized"}

    try:
        ok = await generator.health_check()
        from app.config import settings
        return {
            "available": ok,
            "model": settings.llm.model,
            "url": settings.llm.base_url,
        }
    except Exception as e:
        return {"available": False, "model": "", "error": str(e)}
