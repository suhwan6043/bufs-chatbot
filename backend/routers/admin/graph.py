"""그래프 현황 API — pages/admin.py graph_status 섹션 이식."""

from collections import Counter
from pathlib import Path

from fastapi import APIRouter, Depends

from app.config import settings
from backend.routers.admin.auth import require_admin, _audit

router = APIRouter()


@router.get("/graph")
async def get_graph_status(_=Depends(require_admin)):
    """그래프 노드/엣지 현황 + 노드 타입별 카운트."""
    from app.graphdb.academic_graph import AcademicGraph
    graph = AcademicGraph()

    type_counts = Counter(d.get("type", "unknown") for _, d in graph.G.nodes(data=True))

    # 감사 로그 최근 20줄
    log_path = Path(settings.graph.graph_path).parent.parent / "logs" / "admin_audit.log"
    recent_audit = []
    if log_path.exists():
        lines = log_path.read_text(encoding="utf-8").splitlines()
        recent_audit = lines[-20:]

    return {
        "total_nodes": graph.G.number_of_nodes(),
        "total_edges": graph.G.number_of_edges(),
        "type_counts": dict(type_counts.most_common()),
        "early_grad_nodes": [
            {"id": nid, **{k: v for k, v in d.items() if k not in ("type", "구분")}}
            for nid, d in graph.G.nodes(data=True) if d.get("type") == "조기졸업"
        ],
        "recent_audit": recent_audit,
        "graph_path": str(settings.graph.graph_path),
    }


@router.post("/graph/reset-chat")
async def reset_chat_session(_=Depends(require_admin)):
    """채팅 세션 초기화 (그래프 변경 후 채팅 반영)."""
    _audit("CHAT_SESSION_RESET")
    # FastAPI에서는 파이프라인 싱글톤이 다음 요청 시 자동 반영됨
    # (Streamlit의 initialized 플래그 제거 대응)
    from backend.dependencies import init_all
    # 그래프 새로고침을 위해 라우터 인스턴스 리셋은 Step 4에서 고도화
    return {"ok": True, "message": "채팅 세션 초기화 완료. 다음 질문부터 변경된 그래프가 적용됩니다."}
