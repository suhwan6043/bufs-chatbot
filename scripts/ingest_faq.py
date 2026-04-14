"""
FAQ 데이터 인제스트 — JSON Q&A 쌍을 ChromaDB + GraphDB에 반영합니다.

원칙 1: FAQ는 그래프 1급 시민 (FaqNodeBuilder가 카테고리별 커뮤니티 생성)
원칙 2: 카테고리→노드타입 매핑으로 필요한 엣지만 생성
원칙 3: ChangeDetector 기반 증분 업데이트 (SHA-256 해시 비교)

사용법:
    python scripts/ingest_faq.py                 # 증분 모드 (기본)
    python scripts/ingest_faq.py --full          # 전체 재인제스트
    python scripts/ingest_faq.py --faq path.json # 다른 JSON 파일
"""

import sys
import json
import hashlib
import logging
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.models import Chunk
from app.embedding import Embedder
from app.vectordb import ChromaStore
from app.graphdb.academic_graph import AcademicGraph
from app.graphdb.faq_node_builder import FaqNodeBuilder
from app.crawler.change_detector import ChangeDetector, ChangeType, CrawledItem

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger(__name__)

DEFAULT_FAQ_PATH = Path(__file__).resolve().parent.parent / "data" / "faq_academic.json"


def load_faq(path: Path) -> list:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "faq" in data:
        data = data["faq"]
    return data


def _item_text(item: dict) -> str:
    """FAQ 항목의 해시 계산용 콘텐츠 (질문 + 답변 + 카테고리)."""
    return (
        (item.get("category") or "")
        + "|"
        + (item.get("question") or "")
        + "|"
        + (item.get("answer") or "")
    )


def _item_hash(item: dict) -> str:
    return hashlib.sha256(_item_text(item).encode("utf-8")).hexdigest()


def to_crawled_item(item: dict) -> CrawledItem:
    """FAQ dict를 ChangeDetector 호환 CrawledItem으로 변환."""
    faq_id = item.get("id", "")
    return CrawledItem(
        source_id=f"faq://{faq_id}",
        title=item.get("question", "")[:100],
        content=_item_text(item),
        content_type="faq",
        content_hash=_item_hash(item),
        crawled_at=datetime.now(),
        source_name="FAQ",
        metadata={"faq_id": faq_id, "category": item.get("category", "")},
    )


def create_chunk(item: dict, source_file: str) -> Chunk | None:
    """FAQ 항목 → ChromaDB Chunk 변환."""
    question = item.get("question", "").strip()
    answer = item.get("answer", "").strip()
    if not question or not answer:
        return None

    category = item.get("category", "")
    header = f"[{category}] " if category else ""
    text = f"{header}Q: {question}\n\nA: {answer}"
    faq_id = item.get("id", "")
    chunk_id = hashlib.md5(f"{source_file}:{faq_id}:{question}".encode()).hexdigest()

    meta: dict = {
        "category": category,
        "faq_id": faq_id,
        "content_type": "faq",
    }
    # answer_type (선택) — "redirect" / "data" — 리다이렉트 휴리스틱 override용
    answer_type = item.get("answer_type")
    if answer_type:
        meta["answer_type"] = answer_type

    return Chunk(
        chunk_id=chunk_id,
        text=text,
        page_number=0,
        source_file=source_file,
        student_id=None,
        doc_type="faq",
        cohort_from=2016,
        cohort_to=2030,
        semester="",
        metadata=meta,
    )


def ingest_full(store: ChromaStore, graph: AcademicGraph, faq_data: list, source_file: str):
    """전체 재인제스트 (기존 동작) — idempotent."""
    chunks = []
    for item in faq_data:
        c = create_chunk(item, source_file)
        if c:
            chunks.append(c)

    # 기존 FAQ 청크 제거
    existing = store.collection.get(where={"doc_type": "faq"})
    if existing and existing["ids"]:
        store.collection.delete(ids=existing["ids"])
        logger.info("기존 FAQ 청크 %d개 제거", len(existing["ids"]))

    store.add_chunks(chunks)
    logger.info("FAQ 전체 인제스트: %d개 청크 → ChromaDB", len(chunks))

    # 그래프 전체 재빌드
    builder = FaqNodeBuilder()
    stats = builder.build_from_items(graph, faq_data)
    logger.info(
        "FAQ 그래프: 추가=%d, 업데이트=%d, 제거=%d, 엣지=%d",
        stats["added"], stats["updated"], stats["removed"], stats["edges"],
    )


def ingest_incremental(store: ChromaStore, graph: AcademicGraph, faq_data: list, source_file: str):
    """
    증분 인제스트 — ChangeDetector 기반 변경분만 반영.

    원칙 3: SHA-256 해시 비교로 변경된 FAQ만 벡터/그래프 업데이트.
    """
    detector = ChangeDetector()

    crawled_items = [to_crawled_item(item) for item in faq_data]
    events = detector.detect(crawled_items)

    if not events:
        logger.info("FAQ 변경 없음 — 벡터/그래프 스킵")
        return

    # faq_id → item 매핑 (NEW/MODIFIED 처리용)
    item_by_id = {item.get("id"): item for item in faq_data if item.get("id")}

    added = updated = removed = 0
    chunks_to_add: list[Chunk] = []
    ids_to_delete: list[str] = []

    for event in events:
        faq_id = event.metadata.get("faq_id") if event.metadata else None
        if not faq_id:
            # source_id = "faq://FAQ-xxxx" 에서 추출
            if event.source_id.startswith("faq://"):
                faq_id = event.source_id[len("faq://"):]

        if event.change_type == ChangeType.DELETED:
            # 해당 faq_id의 ChromaDB 청크 삭제
            existing = store.collection.get(where={"faq_id": faq_id}) if faq_id else None
            if existing and existing.get("ids"):
                ids_to_delete.extend(existing["ids"])
            removed += 1
            continue

        # NEW 또는 MODIFIED → 기존 청크 삭제 후 재추가
        existing = store.collection.get(where={"faq_id": faq_id}) if faq_id else None
        if existing and existing.get("ids"):
            ids_to_delete.extend(existing["ids"])

        item = item_by_id.get(faq_id)
        if not item:
            continue
        chunk = create_chunk(item, source_file)
        if chunk:
            chunks_to_add.append(chunk)
        if event.change_type == ChangeType.NEW:
            added += 1
        else:
            updated += 1

    # ChromaDB 업데이트
    if ids_to_delete:
        store.collection.delete(ids=ids_to_delete)
        logger.info("FAQ 청크 삭제: %d개", len(ids_to_delete))
    if chunks_to_add:
        store.add_chunks(chunks_to_add)
        logger.info("FAQ 청크 추가: %d개", len(chunks_to_add))

    # 그래프 증분 반영 — FaqNodeBuilder.build_from_items은 upsert + 미존재 삭제
    builder = FaqNodeBuilder()
    graph_stats = builder.build_from_items(graph, faq_data)
    logger.info(
        "FAQ 그래프 증분: 추가=%d, 업데이트=%d, 제거=%d, 엣지=%d",
        graph_stats["added"], graph_stats["updated"], graph_stats["removed"],
        graph_stats["edges"],
    )

    # 변경 확정 (해시 커밋)
    detector.commit(events)
    logger.info(
        "FAQ 증분 완료: ChromaDB +%d -%d, 이벤트 NEW=%d MODIFIED=%d DELETED=%d",
        len(chunks_to_add), len(ids_to_delete), added, updated, removed,
    )


def main():
    import argparse
    parser = argparse.ArgumentParser(description="FAQ → ChromaDB + GraphDB 인제스트")
    parser.add_argument("--faq", default=str(DEFAULT_FAQ_PATH), help="FAQ JSON 파일 경로")
    parser.add_argument("--full", action="store_true", help="전체 재인제스트 (기본: 증분)")
    args = parser.parse_args()

    path = Path(args.faq)
    if not path.exists():
        logger.error("파일 없음: %s", path)
        return

    faq_data = load_faq(path)
    logger.info("FAQ 로드: %d개 항목", len(faq_data))

    embedder = Embedder()
    store = ChromaStore(embedder=embedder)
    graph = AcademicGraph()

    if args.full:
        ingest_full(store, graph, faq_data, path.name)
    else:
        ingest_incremental(store, graph, faq_data, path.name)

    graph.save()

    total = store.collection.count()
    logger.info("ChromaDB 총 청크: %d개", total)
    logger.info("Graph 총 노드: %d개, 엣지: %d개",
                graph.G.number_of_nodes(), graph.G.number_of_edges())


if __name__ == "__main__":
    main()
