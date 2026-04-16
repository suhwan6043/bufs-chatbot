"""
evaluation/ 공용 pytest 설정

마커 등록:
  llm     — Ollama 연결 필요
  offline — Ollama 없이 실행 가능

chromadb / sentence-transformers / torch 등 무거운 패키지가
이 환경에 설치되어 있지 않아도 평가 1~3번 테스트가 실행되도록
app.pipeline.__init__ 임포트 체인을 미리 stub 처리합니다.
"""

import sys
from types import ModuleType
from unittest.mock import MagicMock


def _stub(name: str) -> None:
    """name과 그 하위 패키지를 sys.modules에 stub으로 등록합니다."""
    if name not in sys.modules:
        sys.modules[name] = MagicMock()


# chromadb와 연쇄 의존성 stub
for _mod in [
    "chromadb",
    "chromadb.api",
    "chromadb.config",
    "sentence_transformers",
    "torch",
    "FlagEmbedding",
]:
    _stub(_mod)

# app.vectordb stub — QueryRouter가 임포트하는 패키지
_vdb = ModuleType("app.vectordb")
_vdb.ChromaStore = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("app.vectordb", _vdb)
sys.modules.setdefault("app.vectordb.chroma_store", MagicMock())

# app.pipeline.query_router stub
_qr_mod = ModuleType("app.pipeline.query_router")
_qr_mod.QueryRouter = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("app.pipeline.query_router", _qr_mod)

# app.pipeline.reranker stub
_rr_mod = ModuleType("app.pipeline.reranker")
_rr_mod.Reranker = MagicMock()  # type: ignore[attr-defined]
sys.modules.setdefault("app.pipeline.reranker", _rr_mod)

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "llm: requires running Ollama server")
    config.addinivalue_line("markers", "offline: runs without Ollama")
