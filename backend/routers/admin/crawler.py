"""크롤러 관리 API — pages/admin.py crawler 섹션 이식."""

import json
import logging
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends

from app.config import settings
from backend.routers.admin.auth import require_admin, _audit
from backend.schemas.admin import CrawlerStatus

logger = logging.getLogger(__name__)
router = APIRouter()

_CRAWL_META = Path(settings.graph.graph_path).parent.parent / "crawl_meta"
_HASH_FILE = _CRAWL_META / "content_hashes.json"
_HIST_FILE = _CRAWL_META / "crawl_history.jsonl"


@router.get("/crawler", response_model=CrawlerStatus)
async def get_crawler_status(_=Depends(require_admin)):
    """크롤러 상태 조회."""
    from app.scheduler import get_scheduler
    sched = get_scheduler()
    jobs = sched.get_jobs_info()

    notice_count = 0
    if _HASH_FILE.exists():
        try:
            notice_count = len(json.loads(_HASH_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass

    next_run = ""
    for j in jobs:
        if j.get("id") == "notice_crawl":
            next_run = j.get("next_run", "")
            break

    return CrawlerStatus(
        enabled=settings.crawler.enabled,
        is_running=sched.is_running(),
        interval_minutes=settings.crawler.notice_interval_minutes,
        next_run=next_run,
        notice_count=notice_count,
    )


@router.post("/crawler/trigger")
async def trigger_crawl(_=Depends(require_admin)):
    """수동 즉시 크롤링 실행."""
    from app.scheduler import get_scheduler
    _audit("CRAWL_TRIGGERED", "manual trigger from admin API")
    sched = get_scheduler()
    sched.trigger_notice_now()
    return {"ok": True, "message": "크롤링 실행 완료."}


@router.post("/crawler/reset-hashes")
async def reset_hashes(_=Depends(require_admin)):
    """해시 초기화 (다음 크롤링 시 전체 재수집)."""
    if _HASH_FILE.exists():
        _HASH_FILE.write_text("{}", encoding="utf-8")
    _audit("HASH_RESET", "manual hash reset from admin API")
    return {"ok": True, "message": "해시 초기화 완료."}


@router.post("/crawler/reingest")
async def full_reingest(_=Depends(require_admin)):
    """전체 재인제스트: notice/notice_attachment 삭제 → 해시 초기화 → 크롤링."""
    from app.shared_resources import get_chroma_store
    from app.scheduler import get_scheduler

    _audit("FULL_REINGEST_TRIGGERED", "manual full re-ingest from admin API")
    chroma = get_chroma_store()

    deleted_notice = chroma.delete_all_by_doc_type("notice")
    deleted_attach = chroma.delete_all_by_doc_type("notice_attachment")

    if _HASH_FILE.exists():
        _HASH_FILE.write_text("{}", encoding="utf-8")

    get_scheduler().trigger_notice_now()

    _audit("FULL_REINGEST_DONE", f"deleted notice={deleted_notice} attach={deleted_attach}")
    return {
        "ok": True,
        "deleted_notice": deleted_notice,
        "deleted_attachment": deleted_attach,
    }


@router.get("/crawler/history")
async def get_crawl_history(_=Depends(require_admin)):
    """크롤 히스토리 조회."""
    if not _HIST_FILE.exists():
        return {"records": []}
    raw = _HIST_FILE.read_text(encoding="utf-8").strip().splitlines()
    records = [json.loads(l) for l in raw if l.strip()]
    records.reverse()  # 최신순
    return {"records": records[:50]}


@router.get("/crawler/attachments")
async def get_attachment_status(_=Depends(require_admin)):
    """첨부파일 다운로드 현황."""
    from app.config import DATA_DIR
    dirs = {
        "pdf": DATA_DIR / "pdfs" / "crawled",
        "hwp": DATA_DIR / "attachments" / "hwp",
        "other": DATA_DIR / "attachments" / "other",
    }
    result = {}
    for key, adir in dirs.items():
        files = [f for f in adir.glob("*") if f.is_file()] if adir.exists() else []
        result[key] = {
            "count": len(files),
            "total_kb": sum(f.stat().st_size for f in files) // 1024,
        }
    return result


@router.get("/crawler/notices")
async def get_tracked_notices(_=Depends(require_admin)):
    """추적 중인 공지 목록 (FAQ 제외, 크롤링된 공지만)."""
    if not _HASH_FILE.exists():
        return {"notices": []}
    hashes = json.loads(_HASH_FILE.read_text(encoding="utf-8"))
    notices = []
    for url, val in sorted(hashes.items(), key=lambda x: x[1].get("metadata", {}).get("post_date", ""), reverse=True):
        # FAQ 항목 제외 (faq:// 스킴)
        if url.startswith("faq://"):
            continue
        meta = val.get("metadata", {})
        notices.append({
            "title": val.get("title", ""),
            "post_date": meta.get("post_date", ""),
            "semester": meta.get("semester", ""),
            "first_seen": val.get("first_seen", "")[:10],
            "last_seen": val.get("last_seen", "")[:10],
            "url": url,
        })
    return {"notices": notices}
