"""
BUFS Academic Chatbot - 설정 관리
환경 변수 및 시스템 설정을 중앙 관리합니다.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

# ── Hugging Face 토큰 자동 적용 ──────────────────────
_hf_token = os.getenv("HF_TOKEN", "")
if _hf_token and not _hf_token.startswith("여기에"):
    os.environ["HUGGING_FACE_HUB_TOKEN"] = _hf_token
    os.environ["HF_TOKEN"] = _hf_token

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


@dataclass
class LLMConfig:
    base_url: str = os.getenv("LLM_BASE_URL", "http://localhost:1234")
    model: str = os.getenv("LLM_MODEL", "qwen3.5-9b-4bit")
    max_tokens: int = int(os.getenv("LLM_MAX_TOKENS", "2048"))
    temperature: float = float(os.getenv("LLM_TEMPERATURE", "0.1"))
    top_p: float = float(os.getenv("LLM_TOP_P", "0.9"))
    repeat_penalty: float = float(os.getenv("LLM_REPEAT_PENALTY", "1.0"))
    timeout: int = int(os.getenv("LLM_TIMEOUT", "60"))


@dataclass
class EmbeddingConfig:
    model_name: str = os.getenv(
        "EMBEDDING_MODEL", "BAAI/bge-m3"
    )
    device: str = os.getenv("EMBEDDING_DEVICE", "cpu")


@dataclass
class RerankerConfig:
    model_name: str = os.getenv("RERANKER_MODEL", "BAAI/bge-reranker-v2-m3")
    device: str = os.getenv("RERANKER_DEVICE", "cpu")
    enabled: bool = os.getenv("RERANKER_ENABLED", "true").lower() == "true"
    top_k: int = int(os.getenv("RERANKER_TOP_K", "7"))
    candidate_k: int = int(os.getenv("RERANKER_CANDIDATE_K", "30"))


@dataclass
class ChromaConfig:
    persist_dir: str = str(DATA_DIR / "chromadb")
    collection_name: str = os.getenv("CHROMA_COLLECTION", "bufs_academic")
    distance_metric: str = "cosine"
    n_results: int = int(os.getenv("CHROMA_N_RESULTS", "15"))


@dataclass
class GraphConfig:
    graph_path: str = str(DATA_DIR / "graphs" / "academic_graph.pkl")


@dataclass
class PDFConfig:
    pdf_dir: str = str(DATA_DIR / "pdfs")
    digital_threshold: int = 100  # 페이지당 최소 글자 수 (디지털 판별 기준)
    ocr_batch_size: int = int(os.getenv("OCR_BATCH_SIZE", "4"))
    ocr_dpi: int = int(os.getenv("OCR_DPI", "200"))
    ocr_languages: list = field(default_factory=lambda: ["ko", "en"])


@dataclass
class AppConfig:
    host: str = os.getenv("APP_HOST", "0.0.0.0")
    port: int = int(os.getenv("APP_PORT", "8000"))
    debug: bool = os.getenv("APP_DEBUG", "false").lower() == "true"
    log_level: str = os.getenv("LOG_LEVEL", "INFO")


@dataclass
class CrawlerConfig:
    # 크롤러 활성화 여부 (기본: 비활성 — .env에서 명시적으로 켜야 함)
    enabled: bool = os.getenv("CRAWLER_ENABLED", "false").lower() == "true"
    # 공지사항 크롤링 주기 (분)
    notice_interval_minutes: int = int(os.getenv("CRAWLER_NOTICE_INTERVAL", "30"))
    # 학사안내 PDF 체크 시각 (시, 0~23)
    guide_cron_hour: int = int(os.getenv("CRAWLER_GUIDE_HOUR", "2"))
    # 수업시간표 PDF 체크 시각 (시, 0~23)
    timetable_cron_hour: int = int(os.getenv("CRAWLER_TIMETABLE_HOUR", "3"))
    # HTTP 요청 타임아웃 (초)
    request_timeout: int = int(os.getenv("CRAWLER_TIMEOUT", "30"))
    # 게시판 목록 최대 순회 페이지 수
    max_pages_per_board: int = int(os.getenv("CRAWLER_MAX_PAGES", "5"))
    # 크롤러 User-Agent
    user_agent: str = os.getenv("CRAWLER_USER_AGENT", "BUFS-CamChat-Bot/1.0")


_ADMIN_PW_DEFAULT = "bufs_admin_2025"   # 절대 프로덕션에서 사용 금지


@dataclass
class AdminConfig:
    # ── 비밀번호 ────────────────────────────────────────────────
    # 반드시 .env 에서 ADMIN_PASSWORD=강력한비밀번호 로 변경하세요.
    # 미설정 시 기본값이 사용되며, 관리자 페이지에 경고 배너가 표시됩니다.
    password: str = os.getenv("ADMIN_PASSWORD", _ADMIN_PW_DEFAULT)

    # ── 브루트포스 방지 ─────────────────────────────────────────
    # 연속 로그인 실패 허용 횟수 (초과 시 세션 잠금)
    max_login_attempts: int = int(os.getenv("ADMIN_MAX_ATTEMPTS", "5"))
    # 잠금 유지 시간 (분)
    lockout_minutes: int = int(os.getenv("ADMIN_LOCKOUT_MINUTES", "15"))

    # ── 세션 타임아웃 ───────────────────────────────────────────
    # 마지막 활동 후 자동 로그아웃 시간 (분)
    session_timeout_minutes: int = int(os.getenv("ADMIN_SESSION_TIMEOUT", "30"))


@dataclass
class Settings:
    llm: LLMConfig = field(default_factory=LLMConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    pdf: PDFConfig = field(default_factory=PDFConfig)
    app: AppConfig = field(default_factory=AppConfig)
    admin: AdminConfig = field(default_factory=AdminConfig)
    crawler: CrawlerConfig = field(default_factory=CrawlerConfig)


settings = Settings()
