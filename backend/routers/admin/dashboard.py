"""관리자 대시보드 KPI API — pages/admin.py dashboard 섹션 이식."""

from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends

from backend.routers.admin.auth import require_admin
from backend.schemas.admin import (
    DashboardData, KPIData, DailyCount, IntentCount, RecentChat,
)

router = APIRouter()


@router.get("/dashboard", response_model=DashboardData)
async def get_dashboard(_=Depends(require_admin)):
    """KPI + 일별 추이 + Intent 분포 + 최근 대화."""
    from app.logging import ChatLogger
    from app.graphdb.academic_graph import AcademicGraph

    logger = ChatLogger()
    all_logs = logger.read_all()
    today_logs = logger.read(date.today())
    total = len(all_logs)

    # KPI
    avg_dur = (sum(l.get("duration_ms", 0) for l in all_logs) / total / 1000) if total else 0.0
    try:
        graph = AcademicGraph()
        faq_count = len(graph._type_index.get("FAQ", []))
    except Exception:
        faq_count = 0

    kpi = KPIData(
        total_questions=total,
        today_questions=len(today_logs),
        avg_duration_sec=round(avg_dur, 1),
        faq_count=faq_count,
    )

    # 일별 차트 (최근 7일)
    daily = []
    for i in range(6, -1, -1):
        d = date.today() - timedelta(days=i)
        daily.append(DailyCount(date=d.strftime("%m-%d"), count=len(logger.read(d))))

    # Intent 분포
    intent_counter = Counter(l.get("intent", "GENERAL") for l in all_logs)
    intents = [IntentCount(intent=k, count=v) for k, v in intent_counter.most_common()]

    # 최근 대화 10건
    recent_sorted = sorted(all_logs, key=lambda x: x.get("timestamp", ""), reverse=True)[:10]
    recent = []
    for l in recent_sorted:
        ts = l.get("timestamp", "")
        if len(ts) >= 16:
            ts = ts[5:16].replace("T", " ")
        rating_val = l.get("rating")
        rating_str = f"{'*' * int(rating_val)}" if rating_val else "-"
        recent.append(RecentChat(
            time=ts,
            question=(l.get("question", ""))[:50],
            intent=l.get("intent", ""),
            duration_ms=l.get("duration_ms", 0),
            rating=rating_str,
        ))

    return DashboardData(kpi=kpi, daily_chart=daily, intent_distribution=intents, recent_chats=recent)
