"""
ChromaDB 인덱스 재구축 + 중복 학사안내 첨부파일 제거

손상된 HNSW 인덱스를 복구하고, notice_attachment에 포함된
학사안내 중복본(PDF/HWP)을 제거합니다.

HNSW 인덱스 파일이 손상되어 임베딩 벡터 손실 → 텍스트에서 재임베딩 수행.

과정:
1. 기존 SQLite에서 텍스트+메타데이터 추출 (임베딩은 손실됨)
2. 중복 학사안내 첨부파일 필터링
3. 기존 ChromaDB 백업 후 삭제
4. 새 컬렉션 생성 + SentenceTransformer 재임베딩 + 삽입

사용법:
    .venv/Scripts/python scripts/rebuild_chromadb.py
"""

import logging
import shutil
import sqlite3
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

CHROMA_DIR = PROJECT_ROOT / "data" / "chromadb"
BACKUP_DIR = PROJECT_ROOT / "data" / "chromadb_backup"

# 중복 학사안내 패턴 (notice_attachment에서 제거)
_DUPLICATE_PATTERNS = ["학사 안내", "학사안내"]


def extract_from_sqlite(db_path: str) -> list:
    """SQLite에서 embedding_id, 텍스트, 메타데이터를 추출합니다."""
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    # embedding_id (해시 문자열) 추출
    cur.execute("SELECT id, embedding_id FROM embeddings ORDER BY id")
    id_map = {}  # rowid → embedding_id
    for rowid, emb_id in cur.fetchall():
        id_map[rowid] = emb_id

    # 문서 텍스트 추출 (fulltext search content 테이블 — rowid 기반)
    cur.execute("SELECT id, c0 FROM embedding_fulltext_search_content")
    docs_map = {}
    for rowid, text in cur.fetchall():
        docs_map[rowid] = text or ""

    # 메타데이터 추출 (id = rowid)
    cur.execute(
        "SELECT id, key, string_value, int_value, float_value, bool_value "
        "FROM embedding_metadata"
    )
    meta_map = {}
    for row in cur.fetchall():
        rowid, key, str_val, int_val, float_val, bool_val = row
        if rowid not in meta_map:
            meta_map[rowid] = {}
        if str_val is not None:
            meta_map[rowid][key] = str_val
        elif int_val is not None:
            meta_map[rowid][key] = int_val
        elif float_val is not None:
            meta_map[rowid][key] = float_val
        elif bool_val is not None:
            meta_map[rowid][key] = bool(bool_val)

    conn.close()

    # 조합: rowid 기준으로 embedding_id + text + metadata
    chunks = []
    for rowid, emb_id in id_map.items():
        text = docs_map.get(rowid, "")
        if not text or not text.strip():
            continue
        meta = meta_map.get(rowid, {})
        chunks.append({
            "id": emb_id,         # 원래 chunk ID (해시)
            "document": text,
            "metadata": meta,
        })

    return chunks


def is_duplicate_handbook(chunk: dict) -> bool:
    """notice_attachment 중 학사안내 중복본인지 판별합니다."""
    meta = chunk["metadata"]
    if meta.get("doc_type") != "notice_attachment":
        return False
    source = (meta.get("source_file", "") or "").lower()
    source_norm = source.replace(" ", "")
    for pattern in _DUPLICATE_PATTERNS:
        if pattern in source or pattern.replace(" ", "") in source_norm:
            return True
    return False


def rebuild(chunks: list):
    """필터링된 청크로 ChromaDB를 재구축합니다 (재임베딩 포함)."""
    import chromadb
    from app.config import settings
    from app.embedding.embedder import Embedder

    logger.info("ChromaDB 재구축 시작 (%d chunks)", len(chunks))

    # 기존 디렉토리 백업 후 삭제
    if BACKUP_DIR.exists():
        shutil.rmtree(BACKUP_DIR)
    shutil.copytree(CHROMA_DIR, BACKUP_DIR)
    logger.info("백업 완료: %s", BACKUP_DIR)

    shutil.rmtree(CHROMA_DIR)
    CHROMA_DIR.mkdir(parents=True)
    logger.info("기존 ChromaDB 삭제 완료")

    # 임베더 초기화
    embedder = Embedder()
    logger.info("임베더 로드 완료")

    # 새 클라이언트+컬렉션 생성
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    collection = client.create_collection(
        name=settings.chroma.collection_name,
        metadata={"hnsw:space": "cosine"},
    )

    # 배치 임베딩+삽입 (100개씩 — 임베딩 연산이 무거우므로)
    batch_size = 100
    total = len(chunks)
    for i in range(0, total, batch_size):
        batch = chunks[i:i + batch_size]

        ids = [c["id"] for c in batch]
        documents = [c["document"] for c in batch]
        # ChromaDB 내부 예약 키 제거
        metadatas = [
            {k: v for k, v in c["metadata"].items() if not k.startswith("chroma:")}
            for c in batch
        ]

        # 재임베딩 (passage 모드)
        embeddings = embedder.embed_passages_batch(documents)
        emb_lists = [e.tolist() for e in embeddings]

        collection.add(
            ids=ids,
            documents=documents,
            embeddings=emb_lists,
            metadatas=metadatas,
        )

        done = min(i + batch_size, total)
        logger.info("  %d/%d (%.0f%%)", done, total, done / total * 100)

    logger.info("재구축 완료: %d chunks", collection.count())


def main():
    db_path = str(CHROMA_DIR / "chroma.sqlite3")

    logger.info("=== Step 1: SQLite에서 데이터 추출 ===")
    all_chunks = extract_from_sqlite(db_path)
    logger.info("추출된 청크: %d개", len(all_chunks))

    logger.info("\n=== Step 2: 중복 학사안내 첨부파일 필터링 ===")
    duplicates = [c for c in all_chunks if is_duplicate_handbook(c)]
    clean_chunks = [c for c in all_chunks if not is_duplicate_handbook(c)]
    logger.info("중복 제거: %d개, 유지: %d개", len(duplicates), len(clean_chunks))

    dt_counts = Counter(c["metadata"].get("doc_type", "NONE") for c in clean_chunks)
    logger.info("정리 후 doc_type 분포:")
    for dt, count in dt_counts.most_common():
        logger.info("  %s: %d", dt, count)

    logger.info("\n=== Step 3: ChromaDB 재구축 (재임베딩 포함) ===")
    rebuild(clean_chunks)

    logger.info("\n=== 완료 ===")
    logger.info("백업: %s", BACKUP_DIR)


if __name__ == "__main__":
    main()
