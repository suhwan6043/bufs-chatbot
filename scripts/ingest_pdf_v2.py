"""신규 파이프라인 v2 — 단일 PDF를 별도 ChromaDB 컬렉션에 인제스트.

기존 `bufs_academic` 컬렉션은 영향 없음. 검증용 컬렉션(`bufs_test_a`)에 저장하여
신규 vs 기존 결과 비교 가능.

사용:
    python scripts/ingest_pdf_v2.py <pdf_path> [--doc-type domestic] [--no-vlm]
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

# 프로젝트 루트 sys.path 보정
_HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(_HERE.parent))

from app.ingestion.chunking_v2 import (
    chunks_from_pdf, validate_chunk, ChunkV2,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger(__name__)


# ── ChromaDB 별도 컬렉션 ────────────────────────────────────────────────────
def _get_test_collection(collection_name: str):
    """테스트용 컬렉션 — 기본 컬렉션과 분리."""
    import chromadb
    from app.embedding import Embedder

    persist_dir = os.getenv("CHROMA_PERSIST_DIR", "data/chromadb_new")
    client = chromadb.PersistentClient(path=persist_dir)

    embedder = Embedder()
    # ChromaDB 임베딩 함수 인터페이스 (passage용)
    class _Embed:
        def __call__(self, input):
            # input: list[str]
            return embedder.embed_passages_batch(input)
        def name(self):
            return "bge-m3"

    coll = client.get_or_create_collection(
        name=collection_name,
        embedding_function=_Embed(),
        metadata={"hnsw:space": "cosine"},
    )
    return coll


def _flatten_metadata(meta: dict) -> dict:
    """ChromaDB는 scalar 메타만 지원 — 리스트·dict 직렬화."""
    out = {}
    for k, v in meta.items():
        if isinstance(v, (list, tuple, dict)):
            out[k] = json.dumps(v, ensure_ascii=False)
        elif v is None:
            continue  # ChromaDB가 None 거부
        elif isinstance(v, bool):
            out[k] = bool(v)
        elif isinstance(v, (int, float, str)):
            out[k] = v
        else:
            out[k] = str(v)
    return out


# ── 메인 ────────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="PDF v2 인제스트 (테스트 컬렉션)")
    parser.add_argument("pdf_path", help="대상 PDF 경로")
    parser.add_argument("--doc-type", default="domestic",
                        choices=["domestic", "foreign", "transfer", "guide",
                                 "notice", "scholarship", "timetable", "notice_attachment"])
    parser.add_argument("--collection", default="bufs_test_a",
                        help="저장할 컬렉션 이름 (기본: bufs_test_a)")
    parser.add_argument("--no-vlm", action="store_true",
                        help="VLM 폴백 비활성화 (디지털 추출만)")
    parser.add_argument("--dry-run", action="store_true",
                        help="ChromaDB 저장 없이 청크만 생성·통계 출력")
    parser.add_argument("--report", default=None,
                        help="결과 리포트 JSON 저장 경로")
    args = parser.parse_args()

    pdf_path = args.pdf_path
    if not Path(pdf_path).exists():
        logger.error("PDF 없음: %s", pdf_path)
        sys.exit(1)

    logger.info("=== PDF v2 인제스트 시작 ===")
    logger.info("파일: %s", pdf_path)
    logger.info("doc_type: %s, VLM: %s, dry-run: %s",
                args.doc_type, not args.no_vlm, args.dry_run)

    t0 = time.monotonic()

    # 청크 생성
    chunks = []
    rejected = []
    for chunk in chunks_from_pdf(
        pdf_path, doc_type=args.doc_type, enable_vlm=not args.no_vlm,
    ):
        valid, reason = validate_chunk(chunk)
        if valid:
            chunks.append(chunk)
        else:
            rejected.append((chunk.chunk_id, reason, chunk.text[:80]))
            logger.warning("[거부] %s: %s", chunk.chunk_id, reason)

    elapsed = time.monotonic() - t0
    logger.info("청킹 완료: 통과 %d, 거부 %d (%.1fs)",
                len(chunks), len(rejected), elapsed)

    # 통계
    stats = _compute_stats(chunks)
    print("\n" + "=" * 70)
    print("[청크 통계]")
    print("=" * 70)
    for k, v in stats.items():
        print(f"  {k:30s} {v}")

    # ChromaDB 저장
    if not args.dry_run:
        logger.info("ChromaDB 컬렉션 '%s'에 저장 중...", args.collection)
        coll = _get_test_collection(args.collection)
        # 기존 동일 source_hash 청크 삭제 (재인덱스)
        try:
            file_sha8 = chunks[0].metadata["source_hash"]
            existing = coll.get(where={"source_hash": file_sha8})
            if existing.get("ids"):
                coll.delete(ids=existing["ids"])
                logger.info("기존 동일 source 청크 %d개 삭제", len(existing["ids"]))
        except Exception as e:
            logger.warning("기존 청크 삭제 실패 (무시): %s", e)

        ids = [c.chunk_id for c in chunks]
        docs = [c.text for c in chunks]
        metas = [_flatten_metadata(c.metadata) for c in chunks]
        coll.add(ids=ids, documents=docs, metadatas=metas)
        logger.info("저장 완료: %d 청크", len(chunks))

    # 리포트 저장
    if args.report:
        report = {
            "pdf_path": pdf_path,
            "doc_type": args.doc_type,
            "vlm_enabled": not args.no_vlm,
            "elapsed_sec": round(elapsed, 2),
            "chunks_passed": len(chunks),
            "chunks_rejected": len(rejected),
            "rejected_samples": rejected[:10],
            "stats": stats,
            "chunks_preview": [
                {
                    "chunk_id": c.chunk_id,
                    "text_preview": c.text[:200],
                    "metadata": c.metadata,
                }
                for c in chunks[:20]
            ],
        }
        Path(args.report).write_text(
            json.dumps(report, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        logger.info("리포트 저장: %s", args.report)

    return 0


def _compute_stats(chunks: list[ChunkV2]) -> dict:
    if not chunks:
        return {"total": 0}
    text_lens = [len(c.text) for c in chunks]
    by_method = {}
    by_section_depth = {}
    cohort_inferred_count = 0
    table_count = 0
    has_section = 0
    avg_garbage = 0.0
    for c in chunks:
        m = c.metadata
        by_method[m.get("extraction_method")] = by_method.get(m.get("extraction_method"), 0) + 1
        d = m.get("section_depth", 0)
        by_section_depth[d] = by_section_depth.get(d, 0) + 1
        if m.get("cohort_inferred"): cohort_inferred_count += 1
        if m.get("is_table"): table_count += 1
        if m.get("section_path"): has_section += 1
        avg_garbage += m.get("garbage_ratio", 0)
    avg_garbage /= len(chunks)

    return {
        "total": len(chunks),
        "text_len_min": min(text_lens),
        "text_len_p50": sorted(text_lens)[len(text_lens) // 2],
        "text_len_p90": sorted(text_lens)[int(len(text_lens) * 0.9)],
        "text_len_max": max(text_lens),
        "extraction_method": by_method,
        "section_depth_dist": dict(sorted(by_section_depth.items())),
        "section_path_filled": f"{has_section}/{len(chunks)} ({has_section/len(chunks)*100:.0f}%)",
        "cohort_inferred (폴백)": f"{cohort_inferred_count}/{len(chunks)} ({cohort_inferred_count/len(chunks)*100:.0f}%)",
        "table_chunks": table_count,
        "avg_garbage_ratio": f"{avg_garbage*100:.2f}%",
    }


if __name__ == "__main__":
    sys.exit(main())
