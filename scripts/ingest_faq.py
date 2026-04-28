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

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
# 큐레이션 FAQ 코퍼스 — 동등 등급의 여러 파일을 단일 리스트로 병합 (FaqNodeBuilder
# 의 "미존재 삭제" 동작 때문에 분리 호출 시 직전 파일이 지워짐).
DEFAULT_FAQ_PATHS = [
    DATA_DIR / "faq_academic.json",
    DATA_DIR / "faq_library.json",
]
DEFAULT_FAQ_PATH = DEFAULT_FAQ_PATHS[0]  # 하위호환 (단일 경로 import)


def load_faq(path: Path) -> list:
    with path.open(encoding="utf-8") as f:
        data = json.load(f)
    if isinstance(data, dict) and "faq" in data:
        data = data["faq"]
    return data


def _search_question(item: dict) -> str:
    """
    검색·토크나이저가 사용할 질문 텍스트.

    관리자 큐레이션 FAQ(`source_question` 존재)의 경우, 학생 원문 질문을
    검색면에 포함해 비공식 문장 recall을 높인다.
    표시용(답변 HTML 상 'Q: ...' 라벨)도 병합본을 사용 — 관리자 책임 아래
    폴리싱된 question 이 전면에 오고 원문은 보조 구문으로 덧붙는다.
    """
    q = (item.get("question") or "").strip()
    sq = (item.get("source_question") or "").strip()
    if sq and sq not in q:
        return f"{q} {sq}".strip()
    return q


def _item_text(item: dict) -> str:
    """FAQ 항목의 해시 계산용 콘텐츠 (질문 + 답변 + 카테고리 + student_types + cohort)."""
    stypes = "|".join(item.get("student_types") or [])
    cohort = f"{item.get('cohort_from', '')}-{item.get('cohort_to', '')}"
    return (
        (item.get("category") or "")
        + "|"
        + _search_question(item)
        + "|"
        + (item.get("answer") or "")
        + "|"
        + stypes
        + "|"
        + cohort
    )


def _item_hash(item: dict) -> str:
    return hashlib.sha256(_item_text(item).encode("utf-8")).hexdigest()


def _graph_items(faq_data: list) -> list[dict]:
    """
    그래프 빌더에 넘길 때만 `source_question` 을 `question` 에 병합한
    얕은 복사본을 반환한다. 원본 리스트는 변경하지 않음.

    그래프 노드의 `구분` 속성(=question)이 토큰 인덱스의 소스이므로,
    학생 원문을 여기 포함해야 비공식 문장으로도 매칭이 가능해진다.
    """
    out: list[dict] = []
    for item in faq_data:
        sq = (item.get("source_question") or "").strip()
        if sq:
            clone = dict(item)
            clone["question"] = _search_question(item)
            out.append(clone)
        else:
            out.append(item)
    return out


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
    # 검색 텍스트: 관리자 FAQ 는 source_question 을 포함해 recall 향상.
    search_q = _search_question(item)
    text = f"{header}Q: {search_q}\n\nA: {answer}"
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
    # 관리자 메타 (선택)
    if item.get("source"):
        meta["source"] = item["source"]

    # student_types: 파이프 구분 문자열로 직렬화 (ChromaDB는 배열 미지원)
    raw_stypes = item.get("student_types")
    student_types_str = "|".join(raw_stypes) if raw_stypes and isinstance(raw_stypes, list) else ""
    # cohort_from/cohort_to: JSON에 없으면 기존 기본값 유지 (하위 호환)
    cohort_from = int(item.get("cohort_from", 2016))
    cohort_to   = int(item.get("cohort_to",   2030))

    return Chunk(
        chunk_id=chunk_id,
        text=text,
        page_number=0,
        source_file=source_file,
        student_id=None,
        doc_type="faq",
        cohort_from=cohort_from,
        cohort_to=cohort_to,
        semester="",
        student_types=student_types_str,
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

    # 그래프 전체 재빌드 (source_question 을 검색면에 병합)
    builder = FaqNodeBuilder()
    stats = builder.build_from_items(graph, _graph_items(faq_data))
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
    # source_question 을 검색면에 병합한 복사본을 넘긴다 (원본 유지).
    builder = FaqNodeBuilder()
    graph_stats = builder.build_from_items(graph, _graph_items(faq_data))
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
    parser.add_argument(
        "--faq",
        nargs="+",
        default=[str(p) for p in DEFAULT_FAQ_PATHS],
        help="FAQ JSON 파일 경로 (다중 지정 가능; 큐레이션 코퍼스는 병합 후 일괄 인제스트)",
    )
    parser.add_argument("--full", action="store_true", help="전체 재인제스트 (기본: 증분)")
    args = parser.parse_args()

    paths = [Path(p) for p in args.faq]
    faq_data: list = []
    for p in paths:
        if not p.exists():
            logger.warning("파일 없음 — 스킵: %s", p)
            continue
        items = load_faq(p)
        faq_data.extend(items)
        logger.info("FAQ 로드: %s — %d개 항목", p.name, len(items))

    if not faq_data:
        logger.error("로드된 FAQ가 없습니다: %s", paths)
        return

    logger.info("FAQ 코퍼스 합계: %d개 항목 (%d개 파일)", len(faq_data), len(paths))

    # source_file 기록은 첫 번째 경로명 — 단일 코퍼스로 인덱싱하므로 라벨링 용도.
    # 항목별 출처 분기가 필요하면 source 메타필드를 활용한다.
    source_label = paths[0].name

    embedder = Embedder()
    store = ChromaStore(embedder=embedder)
    graph = AcademicGraph()

    if args.full:
        ingest_full(store, graph, faq_data, source_label)
    else:
        ingest_incremental(store, graph, faq_data, source_label)

    graph.save()

    total = store.collection.count()
    logger.info("ChromaDB 총 청크: %d개", total)
    logger.info("Graph 총 노드: %d개, 엣지: %d개",
                graph.G.number_of_nodes(), graph.G.number_of_edges())


if __name__ == "__main__":
    main()
