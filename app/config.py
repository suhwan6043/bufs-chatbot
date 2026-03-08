"""
BUFS Academic Chatbot - 설정 관리
환경 변수 및 시스템 설정을 중앙 관리합니다.
"""

import os
from pathlib import Path
from dataclasses import dataclass, field

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"


@dataclass
class OllamaConfig:
    base_url: str = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
    model: str = os.getenv("OLLAMA_MODEL", "exaone3.5:7.8b")
    fallback_model: str = os.getenv("OLLAMA_FALLBACK_MODEL", "exaone3.5:2.4b")
    num_ctx: int = int(os.getenv("OLLAMA_NUM_CTX", "2048"))
    temperature: float = float(os.getenv("OLLAMA_TEMPERATURE", "0.1"))
    top_p: float = float(os.getenv("OLLAMA_TOP_P", "0.9"))
    repeat_penalty: float = float(os.getenv("OLLAMA_REPEAT_PENALTY", "1.0"))
    timeout: int = int(os.getenv("OLLAMA_TIMEOUT", "60"))


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
    top_k: int = int(os.getenv("RERANKER_TOP_K", "5"))
    candidate_k: int = int(os.getenv("RERANKER_CANDIDATE_K", "15"))


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
class Settings:
    ollama: OllamaConfig = field(default_factory=OllamaConfig)
    embedding: EmbeddingConfig = field(default_factory=EmbeddingConfig)
    reranker: RerankerConfig = field(default_factory=RerankerConfig)
    chroma: ChromaConfig = field(default_factory=ChromaConfig)
    graph: GraphConfig = field(default_factory=GraphConfig)
    pdf: PDFConfig = field(default_factory=PDFConfig)
    app: AppConfig = field(default_factory=AppConfig)


settings = Settings()
