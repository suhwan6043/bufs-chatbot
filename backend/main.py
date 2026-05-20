"""
CAMCHAT FastAPI 백엔드 — 앱 팩토리.

기존 파이프라인을 수정 없이 래핑. lifespan에서 싱글톤 초기화.
"""

import logging
from contextlib import asynccontextmanager
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

from backend.trace_context import TraceFilter

# 2026-04-28 진단: lifespan init_all() 흐름 가시화 — uvicorn은 자체 logger만 등록하므로
# 우리 logger.info는 default로 silenced. force=True로 root handler 강제 설치.
#
# 2026-05-09: 영속 파일 로깅 추가.
#   - StreamHandler: stdout (기존 docker logs와 동일 — 컨테이너 재시작 시 손실)
#   - TimedRotatingFileHandler: data/logs/backend/app.log (호스트 볼륨 마운트로 영구 보존)
#     매일 자정 회전 + 30일치 보존. 함수명·라인번호 포함.
#   compose의 volume `../data:/app/data` 마운트로 호스트 bufs-chatbot/data/logs/backend/ 에 저장됨.
#
# 2026-05-10: 요청 단위 trace_id 자동 prefix.
#   format에 [%(trace_id)s] 포함 — chat 라우터에서 set_trace_id() 호출하면 같은 요청의
#   모든 logger 출력에 동일 ID 박힘. `grep <8자리>`로 한 요청 흐름 통째 재구성 가능.
#   StreamHandler·FileHandler 양쪽 모두 TraceFilter 부착.
_TRACE_FMT = "%(asctime)s [%(trace_id)s] %(levelname)s %(name)s:%(funcName)s:%(lineno)d - %(message)s"

_trace_filter = TraceFilter()

_LOG_DIR = Path("data/logs/backend")
_LOG_DIR.mkdir(parents=True, exist_ok=True)
_file_handler = TimedRotatingFileHandler(
    _LOG_DIR / "app.log",
    when="midnight",
    backupCount=30,
    encoding="utf-8",
)
_file_handler.setFormatter(logging.Formatter(_TRACE_FMT))
_file_handler.addFilter(_trace_filter)

_stream_handler = logging.StreamHandler()
_stream_handler.setFormatter(logging.Formatter(_TRACE_FMT))
_stream_handler.addFilter(_trace_filter)

logging.basicConfig(
    level=logging.INFO,
    force=True,
    handlers=[
        _stream_handler,  # stdout → docker logs (단기, 재시작 시 손실)
        _file_handler,    # 파일 → 호스트 영구 보존
    ],
)

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
