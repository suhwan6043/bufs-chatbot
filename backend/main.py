"""
CAMCHAT FastAPI 백엔드 — 앱 팩토리.

기존 파이프라인을 수정 없이 래핑. lifespan에서 싱글톤 초기화.
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.dependencies import init_all
from backend.database import init_db
from backend.routers import health, chat, session, transcript, feedback, source, user
from backend.routers.admin import router as admin_router

logger = logging.getLogger(__name__)

# 환경변수 로드 (.env)
from dotenv import load_dotenv
load_dotenv()


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
