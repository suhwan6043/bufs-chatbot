"""대화 로그 조회/내보내기 API — pages/admin.py logs 섹션 이식."""

import io
import json
from collections import Counter
from datetime import date, timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse

from backend.routers.admin.auth import require_admin

router = APIRouter()

_INTENT_LABELS = {
    "GRADUATION_REQ": "졸업요건", "REGISTRATION": "수강신청",
    "SCHEDULE": "학사일정", "COURSE_INFO": "교과목", "MAJOR_CHANGE": "전과",
    "ALTERNATIVE": "대안/선택", "GENERAL": "일반",
    "LEAVE_OF_ABSENCE": "학적변동", "EARLY_GRADUATION": "조기졸업",
    "SCHOLARSHIP": "장학금", "CONTACT": "연락처",
}


@router.get("/logs/dates")
async def get_log_dates(_=Depends(require_admin)):
    """사용 가능한 로그 날짜 목록."""
    from app.logging import ChatLogger
    logger = ChatLogger()
    dates = logger.list_dates()
    return {"dates": [d.isoformat() for d in dates]}


@router.get("/logs")
async def get_logs(
    log_date: str = Query(None, description="날짜 (YYYY-MM-DD). 없으면 전체"),
    intent: str = Query(None, description="인텐트 필터"),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _=Depends(require_admin),
):
    """대화 로그 조회."""
    from app.logging import ChatLogger

    logger = ChatLogger()

    if log_date:
        try:
            parts = log_date.split("-")
            d = date(int(parts[0]), int(parts[1]), int(parts[2]))
            entries = logger.read(d)
        except Exception:
            entries = []
    else:
        entries = logger.read_all()

    # 인텐트 필터
    if intent:
        entries = [e for e in entries if e.get("intent") == intent]

    total = len(entries)

    # KPI
    today_count = len(logger.read(date.today()))
    avg_ms = (sum(e.get("duration_ms", 0) for e in entries) / total) if total else 0
    intent_counter = Counter(e.get("intent", "") for e in entries if e.get("intent"))
    top_intent_raw = intent_counter.most_common(1)
    top_intent = _INTENT_LABELS.get(top_intent_raw[0][0], top_intent_raw[0][0]) if top_intent_raw else "-"

    # 페이지네이션 + 최신순 정렬
    sorted_entries = sorted(entries, key=lambda x: x.get("timestamp", ""), reverse=True)
    paged = sorted_entries[offset:offset + limit]

    return {
        "total": total,
        "today_count": today_count,
        "avg_duration_ms": round(avg_ms),
        "top_intent": top_intent,
        "entries": paged,
    }


@router.get("/logs/export/csv")
async def export_csv(
    log_date: str = Query(None),
    _=Depends(require_admin),
):
    """CSV 내보내기."""
    from app.logging import ChatLogger

    logger = ChatLogger()
    if log_date:
        try:
            parts = log_date.split("-")
            entries = logger.read(date(int(parts[0]), int(parts[1]), int(parts[2])))
        except Exception:
            entries = logger.read_all()
    else:
        entries = logger.read_all()

    import csv
    buf = io.StringIO()
    fields = ["timestamp", "session_id", "student_id", "intent", "question", "answer", "duration_ms", "rating"]
    writer = csv.DictWriter(buf, fieldnames=fields, extrasaction="ignore")
    writer.writeheader()
    for e in entries:
        writer.writerow(e)

    output = buf.getvalue().encode("utf-8-sig")
    filename = f"camchat_logs_{date.today().isoformat()}.csv"

    return StreamingResponse(
        io.BytesIO(output),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )


@router.get("/logs/export/jsonl")
async def export_jsonl(
    log_date: str = Query(None),
    _=Depends(require_admin),
):
    """JSONL 내보내기."""
    from app.logging import ChatLogger

    logger = ChatLogger()
    if log_date:
        try:
            parts = log_date.split("-")
            entries = logger.read(date(int(parts[0]), int(parts[1]), int(parts[2])))
        except Exception:
            entries = logger.read_all()
    else:
        entries = logger.read_all()

    output = "\n".join(json.dumps(e, ensure_ascii=False) for e in entries).encode("utf-8")
    filename = f"camchat_logs_{date.today().isoformat()}.jsonl"

    return StreamingResponse(
        io.BytesIO(output),
        media_type="application/json",
        headers={"Content-Disposition": f"attachment; filename={filename}"},
    )
