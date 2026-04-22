"""통합 코퍼스 IDF 빌드 스크립트.

작업 2-A (2026-04-18): FAQ + 그래프 노드 + 벡터 청크를 통합 코퍼스로 묶어
IDF를 재계산하고 data/idf_corpus.json에 영속화.

사용:
    python scripts/build_corpus_idf.py

설계:
- 각 소스는 "문서 단위"로 카운트:
  · FAQ: 하나의 FAQ 노드 = 1 문서 (구분 + 설명)
  · 그래프 조건 노드: 하나의 노드 = 1 문서 (구분 + 원본키 + 값)
  · 벡터 청크: ChromaDB 각 청크 = 1 문서
- 중복 토큰은 문서당 1회만 카운트 (TF 아닌 DF)
- min_df=2로 극희귀어(오탈자 등) 제외

출력: data/idf_corpus.json {
  "token_count": int,
  "doc_count": int,
  "idf": {token: weight}
}
"""

from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

# 프로젝트 루트를 import path에 추가
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.pipeline.ko_tokenizer import compute_corpus_idf  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger(__name__)

OUTPUT_PATH = ROOT / "data" / "idf_corpus.json"


def collect_faq_docs() -> list[str]:
    """FAQ 노드를 '구분 + 설명' 문서로 변환."""
    from app.graphdb.academic_graph import AcademicGraph

    graph = AcademicGraph()
    docs: list[str] = []
    for nid, data in graph.G.nodes(data=True):
        if data.get("type") != "FAQ":
            continue
        q = str(data.get("구분", "") or "")
        a = str(data.get("설명", "") or "")
        if q or a:
            docs.append(f"{q} {a}".strip())
    logger.info("FAQ docs: %d", len(docs))
    return docs


def collect_graph_condition_docs() -> list[str]:
    """그래프 조건·규정 노드를 문서로 변환 (구분 + 원본키 + 값)."""
    from app.graphdb.academic_graph import AcademicGraph

    graph = AcademicGraph()
    docs: list[str] = []
    _TARGET_TYPES = {"조건", "졸업요건", "수강신청규칙", "전공이수방법", "학사일정"}
    for nid, data in graph.G.nodes(data=True):
        if data.get("type") not in _TARGET_TYPES:
            continue
        parts = [
            str(data.get("구분", "") or ""),
            str(data.get("원본키", "") or ""),
            str(data.get("값", "") or ""),
            str(data.get("설명", "") or ""),
        ]
        text = " ".join(p for p in parts if p).strip()
        if text:
            docs.append(text)
    logger.info("Graph condition docs: %d", len(docs))
    return docs


def collect_vector_chunk_docs() -> list[str]:
    """ChromaDB 벡터 청크 전체를 문서로 수집."""
    try:
        from app.vectordb.chroma_store import ChromaStore
    except Exception as e:
        logger.warning("ChromaStore 로드 실패 (벡터 청크 제외): %s", e)
        return []

    try:
        store = ChromaStore()
        # 전체 청크 조회 (get with no filter)
        result = store.collection.get(limit=50000)
        docs = [d for d in (result.get("documents") or []) if d]
        logger.info("Vector chunks: %d", len(docs))
        return docs
    except Exception as e:
        logger.warning("벡터 청크 수집 실패: %s", e)
        return []


def main() -> int:
    logger.info("통합 코퍼스 IDF 빌드 시작")

    corpus: list[str] = []
    corpus.extend(collect_faq_docs())
    corpus.extend(collect_graph_condition_docs())
    corpus.extend(collect_vector_chunk_docs())

    if not corpus:
        logger.error("코퍼스 비어 있음 — 중단")
        return 1

    logger.info("총 문서: %d", len(corpus))
    idf = compute_corpus_idf(corpus, min_token_len=2, min_df=2)
    logger.info("IDF 토큰: %d", len(idf))

    # 상위 10개 + 하위 10개 로그 (분포 점검)
    sorted_idf = sorted(idf.items(), key=lambda x: x[1])
    logger.info("낮은 IDF(흔한) top 5: %s", sorted_idf[:5])
    logger.info("높은 IDF(희귀) top 5: %s", sorted_idf[-5:])

    OUTPUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    with OUTPUT_PATH.open("w", encoding="utf-8") as f:
        json.dump(
            {
                "doc_count": len(corpus),
                "token_count": len(idf),
                "idf": idf,
            },
            f,
            ensure_ascii=False,
            indent=2,
        )
    logger.info("저장: %s", OUTPUT_PATH)
    return 0


if __name__ == "__main__":
    sys.exit(main())
