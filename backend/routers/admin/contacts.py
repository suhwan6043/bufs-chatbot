"""연락처 관리 API — pages/admin.py contact 섹션 이식."""

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from backend.routers.admin.auth import require_admin, _audit

logger = logging.getLogger(__name__)
router = APIRouter()

_CONTACTS_FILE = Path(__file__).resolve().parent.parent.parent.parent / "data" / "contacts" / "departments.json"


class ContactsUpdate(BaseModel):
    json_content: str


@router.get("/contacts")
async def get_contacts(_=Depends(require_admin)):
    """전체 연락처 데이터."""
    try:
        from app.contacts import get_dept_searcher
        searcher = get_dept_searcher()
        flat = searcher._flat
        return {
            "total": len(flat),
            "entries": [
                {
                    "name": e["name"],
                    "college": e.get("college") or "",
                    "extension": e["extension"],
                    "phone": e["phone"],
                    "office": e.get("office") or "",
                }
                for e in flat
            ],
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"연락처 모듈 로드 실패: {e}")


@router.get("/contacts/search")
async def search_contacts(q: str, _=Depends(require_admin)):
    """연락처 검색 테스트."""
    from app.contacts import get_dept_searcher
    searcher = get_dept_searcher()
    is_contact = searcher.is_contact_query(q)
    results = searcher.search(q, top_k=5)
    return {
        "is_contact_query": is_contact,
        "results": [
            {
                "name": r.name, "college": r.college or "",
                "extension": r.extension, "phone": r.phone,
                "office": r.office or "", "match_type": r.match_type,
            }
            for r in results
        ],
    }


@router.get("/contacts/json")
async def get_contacts_json(_=Depends(require_admin)):
    """departments.json 원본 반환."""
    if not _CONTACTS_FILE.exists():
        raise HTTPException(status_code=404, detail="departments.json 파일 없음")
    return {"json_content": _CONTACTS_FILE.read_text(encoding="utf-8")}


@router.put("/contacts")
async def update_contacts(body: ContactsUpdate, _=Depends(require_admin)):
    """departments.json 직접 편집."""
    try:
        json.loads(body.json_content)  # 유효성 검사
    except json.JSONDecodeError as e:
        raise HTTPException(status_code=400, detail=f"JSON 형식 오류: {e}")

    _CONTACTS_FILE.write_text(body.json_content, encoding="utf-8")
    # 싱글톤 리셋
    try:
        import app.contacts.dept_search as _ds
        _ds._searcher = None
    except Exception:
        pass
    _audit("SAVE_CONTACTS", "departments.json updated via API")
    return {"ok": True}
