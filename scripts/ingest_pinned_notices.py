"""
고정공지 1회 정적 수집 스크립트

학사공지 게시판의 고정공지(핀 아이콘)만 크롤링하여 벡터DB + 그래프DB에 저장합니다.
번호게시글은 스케줄러가 자동 크롤링하므로 여기서는 처리하지 않습니다.

사용법:
    .venv/Scripts/python scripts/ingest_pinned_notices.py
    .venv/Scripts/python scripts/ingest_pinned_notices.py --dry-run   # 수집만 하고 저장 안함
"""

import argparse
import logging
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawler.notice_crawler import NoticeCrawler
from app.crawler.change_detector import ChangeDetector, ChangeEvent, ChangeType
from app.crawler.blacklist import ContentBlacklist
from app.ingestion.incremental_update import IncrementalUpdater
from app.shared_resources import get_chroma_store

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)-5s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)


def main():
    parser = argparse.ArgumentParser(description="고정공지 정적 수집")
    parser.add_argument("--dry-run", action="store_true", help="수집만 하고 저장하지 않음")
    args = parser.parse_args()

    start = time.time()

    # ── 1. 고정공지만 크롤링 ────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("고정공지 크롤링 시작")
    logger.info("=" * 60)

    crawler = NoticeCrawler()
    items = crawler.crawl(pinned_only=True)

    logger.info("수집된 고정공지: %d건", len(items))
    for i, item in enumerate(items, 1):
        logger.info(
            "  [%02d] %s (날짜: %s, 첨부: %d개)",
            i,
            item.title[:60],
            item.metadata.get("post_date", "?"),
            len(item.attachments),
        )

    if not items:
        logger.warning("수집된 고정공지가 없습니다.")
        return

    if args.dry_run:
        # dry-run에서도 태그 감지 결과를 보여줌
        from app.graphdb.notice_graph_builder import detect_tags
        logger.info("─" * 60)
        logger.info("[DRY-RUN] 태그 감지 결과:")
        for item in items:
            tags = detect_tags(item.title, item.content)
            logger.info("  %s → %s", item.title[:50], tags or "(태그 없음)")
        logger.info("[DRY-RUN] 저장을 건너뜁니다.")
        return

    # ── 2. 변경 감지 (NEW/MODIFIED만 처리) ──────────────────────────
    detector = ChangeDetector()
    events = detector.detect(items)

    new_count = sum(1 for e in events if e.change_type == ChangeType.NEW)
    mod_count = sum(1 for e in events if e.change_type == ChangeType.MODIFIED)
    # 고정공지는 DELETED 이벤트 무시 (정적 콘텐츠)
    events = [e for e in events if e.change_type != ChangeType.DELETED]

    logger.info("변경 감지: NEW=%d, MODIFIED=%d", new_count, mod_count)

    if not events:
        logger.info("변경 사항 없음 — 이미 최신 상태입니다.")
        return

    # ── 3. ChromaDB 증분 업데이트 ───────────────────────────────────
    chroma = get_chroma_store()
    blacklist = ContentBlacklist()
    updater = IncrementalUpdater(chroma, blacklist)
    report = updater.process_events(events)

    # ── 4. 해시 커밋 (성공한 이벤트만) ──────────────────────────────
    successful = [
        e for e in events
        if e.source_id not in report.failed_source_ids
    ]
    if successful:
        detector.commit(successful)

    # ── 5. 그래프DB 업데이트 ────────────────────────────────────────
    logger.info("그래프 업데이트 시작...")
    from app.graphdb.academic_graph import AcademicGraph
    from app.graphdb.notice_graph_builder import NoticeGraphBuilder

    graph = AcademicGraph()
    builder = NoticeGraphBuilder()
    graph_stats = builder.build_from_items(graph, items)
    graph.save()

    logger.info(
        "그래프 업데이트 완료: 노드=%d, 엣지=%d (전체: %d노드/%d엣지)",
        graph_stats["added"] + graph_stats["updated"],
        graph_stats["edges"],
        graph.G.number_of_nodes(),
        graph.G.number_of_edges(),
    )

    elapsed = time.time() - start

    # ── 결과 출력 ───────────────────────────────────────────────────
    logger.info("=" * 60)
    logger.info("고정공지 수집 완료")
    logger.info("─" * 60)
    logger.info("  수집: %d건", len(items))
    logger.info("  신규(벡터): %d건", new_count)
    logger.info("  수정(벡터): %d건", mod_count)
    logger.info("  벡터 결과: %s", report.summary())
    logger.info("  그래프 추가: %d건, 엣지: %d건", graph_stats["added"], graph_stats["edges"])
    if report.failed_source_ids:
        logger.warning("  실패: %s", report.failed_source_ids)
    logger.info("  소요: %.1f초", elapsed)
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
