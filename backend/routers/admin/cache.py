"""
LLM 응답 캐시 관리 엔드포인트.

- GET  /api/admin/cache/stats   — 크기·TTL·max_entries·hit rate 조회
- POST /api/admin/cache/clear   — 전체 또는 특정 질문 포함 엔트리 삭제

오답이 캐시에 고착됐을 때 즉시 해소하기 위한 관리자 도구.
피드백(저평점) 기반 자동 무효화(feedback.py)의 수동 보완 경로.

플랜: wild-splashing-volcano Phase C.6.
원칙 3(지식 생애주기): 피드백 자동 무효화 + 재인제스트 + 관리자 수동 clear 3중 경로.
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import APIRouter, Depends, Query

from backend.routers.admin.auth import require_admin

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/cache/stats")
async def cache_stats(_=Depends(require_admin)) -> dict:
    """현재 캐시 상태 반환 (size · max_entries · ttl · hit/miss · hit_rate)."""
    from backend.dependencies import get_generator
    gen = get_generator()
    if not gen:
        return {"ok": False, "error": "generator not initialized"}
    return {"ok": True, **gen.cache_stats()}


@router.post("/cache/clear")
async def cache_clear(
    scope: str = Query("all", pattern="^(all|question)$"),
    question: Optional[str] = Query(None, max_length=2000),
    _=Depends(require_admin),
) -> dict:
    """
    LLM 응답 캐시 삭제.

    - scope=all       — 전체 삭제 (기본)
    - scope=question  — `question` 파라미터로 지정된 문자열을 포함한 엔트리만 삭제
    """
    from backend.dependencies import get_generator
    gen = get_generator()
    if not gen:
        return {"ok": False, "error": "generator not initialized"}

    if scope == "question":
        if not question or not question.strip():
            return {"ok": False, "error": "question required when scope=question"}
        removed = gen.invalidate_by_question(question)
        logger.info(
            "admin cache clear (question): %d entries removed (q=%s...)",
            removed, question[:40],
        )
    else:
        removed = gen.clear_all()
        logger.info("admin cache clear (all): %d entries removed", removed)
    return {"ok": True, "removed": removed, "scope": scope}
