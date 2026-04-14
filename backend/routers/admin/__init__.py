"""관리자 API 라우터 패키지."""

from fastapi import APIRouter

from backend.routers.admin.auth import router as auth_router
from backend.routers.admin.dashboard import router as dashboard_router
from backend.routers.admin.graduation import router as graduation_router
from backend.routers.admin.crawler import router as crawler_router
from backend.routers.admin.logs import router as logs_router
from backend.routers.admin.contacts import router as contacts_router
from backend.routers.admin.graph import router as graph_router

router = APIRouter(prefix="/api/admin", tags=["admin"])
router.include_router(auth_router)
router.include_router(dashboard_router)
router.include_router(graduation_router)
router.include_router(crawler_router)
router.include_router(logs_router)
router.include_router(contacts_router)
router.include_router(graph_router)
