"""
중복 학사안내 청크 정리 스크립트

notice_attachment doc_type에 포함된 학사안내 PDF/HWP 중복본을 제거합니다.
원본 domestic 청크(409개)만 유지하고, 첨부파일로 들어온 중복본을 삭제합니다.

사용법:
    .venv/Scripts/python scripts/cleanup_duplicate_chunks.py          # dry-run
    .venv/Scripts/python scripts/cleanup_duplicate_chunks.py --apply  # 실제 삭제
"""

import argparse
import logging
import sqlite3
import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

# 삭제 대상 패턴: notice_attachment인데 학사안내 원본과 중복되는 파일
_DUPLICATE_PATTERNS = [
    "학사 안내",   # "2026학년도 1학기 학사 안내_0123.pdf" 등
    "학사안내",
]


def find_duplicates(db_path: str) -> list:
    """SQLite에서 중복 학사안내 청크 ID를 찾습니다."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # notice_attachment이면서 학사안내 패턴에 매칭되는 source_file 찾기
    cur.execute("""
        SELECT DISTINCT em_sf.string_value
        FROM embedding_metadata em_sf
        JOIN embedding_metadata em_dt ON em_sf.id = em_dt.id
        WHERE em_sf.key = 'source_file'
          AND em_dt.key = 'doc_type'
          AND em_dt.string_value = 'notice_attachment'
    """)

    duplicate_sources = []
    for (source_file,) in cur.fetchall():
        sf_lower = source_file.lower()
        for pattern in _DUPLICATE_PATTERNS:
            if pattern in sf_lower or pattern.replace(" ", "") in sf_lower.replace(" ", ""):
                duplicate_sources.append(source_file)
                break

    if not duplicate_sources:
        logger.info("중복 학사안내 첨부파일 ���음")
        conn.close()
        return []

    logger.info("중복 학사안내 첨부파일 %d개 발견:", len(duplicate_sources))
    for sf in duplicate_sources:
        cur.execute("""
            SELECT COUNT(*) FROM embedding_metadata
            WHERE key = 'source_file' AND string_value = ?
        """, (sf,))
        count = cur.fetchone()[0]
        logger.info("  [%d chunks] %s", count, sf[-80:])

    # 해당 source_file의 ChromaDB 청크 ID 수집 (embeddings.embedding_id)
    # embedding_metadata.id는 내부 row id, 실제 chunk id는 embeddings.embedding_id
    placeholders = ",".join("?" * len(duplicate_sources))
    cur.execute(f"""
        SELECT DISTINCT e.embedding_id
        FROM embeddings e
        JOIN embedding_metadata em ON e.id = em.id
        WHERE em.key = 'source_file' AND em.string_value IN ({placeholders})
    """, duplicate_sources)
    ids = [row[0] for row in cur.fetchall() if row[0]]

    conn.close()
    return ids


def delete_chunks(ids: list) -> int:
    """ChromaDB API로 청크를 삭제합니다."""
    from app.config import settings
    import chromadb

    client = chromadb.PersistentClient(path=str(settings.chroma.persist_dir))
    collection = client.get_collection(settings.chroma.collection_name)

    # ChromaDB delete는 배치 제한이 있으므로 500개씩 처리
    batch_size = 500
    deleted = 0
    for i in range(0, len(ids), batch_size):
        batch = ids[i:i + batch_size]
        try:
            collection.delete(ids=batch)
            deleted += len(batch)
            logger.info("  삭제: %d/%d", deleted, len(ids))
        except Exception as e:
            logger.error("  삭제 실패 (batch %d): %s", i, e)

    return deleted


def main():
    parser = argparse.ArgumentParser(description="중복 학사안내 청크 정리")
    parser.add_argument("--apply", action="store_true", help="실제 삭제 수행")
    args = parser.parse_args()

    db_path = str(PROJECT_ROOT / "data" / "chromadb" / "chroma.sqlite3")
    ids = find_duplicates(db_path)

    if not ids:
        logger.info("삭��할 청크가 없습니다.")
        return

    logger.info("총 %d개 중복 청크 발견", len(ids))

    if not args.apply:
        logger.info("(dry-run) --apply 플래그로 실제 삭제를 수행하세요.")
        return

    deleted = delete_chunks(ids)
    logger.info("완료: %d개 청크 삭제", deleted)


if __name__ == "__main__":
    main()
