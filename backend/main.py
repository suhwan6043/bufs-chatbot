"""
CAMCHAT FastAPI 백엔드 — 앱 팩토리.

기존 파이프라인을 수정 없이 래핑. lifespan에서 싱글톤 초기화.
"""

import logging
import os
from contextlib import asynccontextmanager

# 2026-04-28 진단: lifespan init_all() 흐름 가시화 — uvicorn은 자체 logger만 등록하므로
# 우리 logger.info는 default로 silenced. force=True로 root handler 강제 설치.
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(name)s: %(message)s',
    force=True,
)

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.dependencies import init_all
from backend.database import init_db
from backend.routers import health, chat, session, transcript, feedback, source, user
from backend.routers.admin import router as admin_router

# 환경변수 로드 (.env)
from dotenv import load_dotenv
load_dotenv()

# 2026-05-13: root logger 정상화 — 진단 결과 root level=WARNING + handlers=[] 였음.
# 그 결과 app.pipeline.* 의 logger.info/debug 가 stdout에 출력 안 됨 (PIPELINE_TIMING
# 만 print 경유로 보임). LOG_LEVEL env로 동적 설정, format에 logger name 포함.
_lvl_name = os.getenv("LOG_LEVEL", "INFO").upper()
_lvl = getattr(logging, _lvl_name, logging.INFO)
logging.basicConfig(
    level=_lvl,
    format="%(asctime)s %(levelname)-7s %(name)s | %(message)s",
    force=True,  # uvicorn pre-config 덮어쓰기
)
# app.pipeline.* 강제 명시 (정확한 진단용)
logging.getLogger("app.pipeline").setLevel(_lvl)
logging.getLogger("backend").setLevel(_lvl)

logger = logging.getLogger(__name__)
logger.info("logging configured: level=%s (LOG_LEVEL env)", _lvl_name)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: 파이프라인 초기화 / Shutdown: 정리."""
    logger.info("FastAPI 서버 시작 — 파이프라인 초기화 중...")
    init_db()
    init_all()
    logger.info("파이프라인 초기화 완료. 서버 준비됨.")
    yield
    logger.info("FastAPI 서버 종료.")


app = FastAPI(
    title="CAMCHAT API",
    description="부산외국어대학교 학사 챗봇 API",
    version="0.3.0",
    lifespan=lifespan,
)

# CORS — Next.js 프론트엔드 허용
import os
_cors_origins = os.getenv("CORS_ORIGINS", "http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 라우터 등록
app.include_router(health.router)
app.include_router(chat.router)
app.include_router(session.router)
app.include_router(transcript.router)
app.include_router(feedback.router)
app.include_router(source.router)
app.include_router(user.router)
app.include_router(admin_router)


@app.get("/")
async def root():
    return {"message": "CAMCHAT API", "docs": "/docs"}
