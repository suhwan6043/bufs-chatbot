"""
정적 HTML/ASP.NET 페이지 → ChromaDB + NetworkX 그래프 인제스트

사용법:
  # 단일 페이지 직접 지정
  python scripts/ingest_static_page.py \\
      --url "https://m.bufs.ac.kr/Information/SAHJ1010.aspx?mc=0951" \\
      --source-name 휴복학안내 \\
      --graph-type leave_of_absence

  # 사전 정의 preset 사용 (config/static_pages.json)
  python scripts/ingest_static_page.py --preset leave_of_absence
  python scripts/ingest_static_page.py --preset scholarship_info

  # 카테고리 전체 일괄 실행
  python scripts/ingest_static_page.py --category 학사
  python scripts/ingest_static_page.py --category 장학
"""

import sys
import json
import argparse
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.crawler.static_page_crawler import StaticPageCrawler
from app.crawler.change_detector import ChangeDetector
from app.crawler.blacklist import ContentBlacklist
from app.ingestion.incremental_update import IncrementalUpdater
from app.embedding import Embedder
from app.vectordb import ChromaStore
from app.graphdb.academic_graph import AcademicGraph

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger(__name__)

# 그래프 타입 → (node type 문자열, node ID prefix)
_TYPE_MAP = {
    "leave_of_absence": "휴복학",
    "scholarship": "장학금",
}
_PREFIX_MAP = {
    "leave_of_absence": "leave_info_",
    "scholarship": "sch_info_",
}

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "static_pages.json"


def _load_config() -> dict:
    if not CONFIG_PATH.exists():
        logger.error("설정 파일 없음: %s", CONFIG_PATH)
        sys.exit(1)
    with CONFIG_PATH.open(encoding="utf-8") as f:
        return json.load(f)


def _find_preset(preset_id: str) -> dict:
    config = _load_config()
    for entries in config.values():
        for entry in entries:
            if entry.get("id") == preset_id:
                return entry
    logger.error("preset '%s' 를 config에서 찾을 수 없습니다.", preset_id)
    sys.exit(1)


def ingest_static_page(
    url: str,
    source_name: str,
    content_type: str,
    graph_type: str,
) -> None:
    """
    단일 정적 페이지를 ChromaDB와 NetworkX 그래프에 인제스트합니다.

    Args:
        url          : 크롤링할 URL
        source_name  : 출처명 (예: 휴복학안내)
        content_type : ChromaDB 메타데이터용 콘텐츠 유형 (guide, notice 등)
        graph_type   : 그래프 노드 유형 (leave_of_absence | scholarship)
    """
    node_type = _TYPE_MAP[graph_type]

    # ── 1. 크롤링 ────────────────────────────────────────────────
    logger.info("크롤링 시작: %s", url)
    crawler = StaticPageCrawler()
    crawled_item, sections = crawler.fetch_and_parse(url, source_name)

    # CLI content-type 인자를 CrawledItem에 반영
    crawled_item.content_type = content_type
    crawled_item.metadata["content_type"] = content_type

    logger.info(
        "크롤링 완료: 제목=%s, 섹션=%d개, 전체텍스트=%d자",
        crawled_item.title, len(sections), len(crawled_item.content),
    )

    # ── 2. ChromaDB 인제스트 ─────────────────────────────────────
    logger.info("ChromaDB 인제스트 시작")
    embedder = Embedder()
    chroma_store = ChromaStore(embedder=embedder)
    blacklist = ContentBlacklist()
    detector = ChangeDetector()
    updater = IncrementalUpdater(chroma_store, blacklist)

    events = detector.detect([crawled_item])

    if not events:
        logger.info("변경 없음 — ChromaDB 업데이트 스킵 (내용 동일)")
    else:
        report = updater.process_events(events)
        detector.commit(events)
        logger.info(
            "ChromaDB 업데이트 완료: added=%d, updated=%d, errors=%d",
            report.added, report.updated, len(report.errors),
        )
        for err in report.errors:
            logger.warning("ChromaDB 에러: %s", err)

    # ── 3. NetworkX 그래프 인제스트 ──────────────────────────────
    if not sections:
        logger.warning("파싱된 섹션 없음 — 그래프 업데이트 스킵")
        return

    logger.info("그래프 인제스트 시작: %d섹션 (graph_type=%s)", len(sections), graph_type)
    graph = AcademicGraph()

    # 기존 동일 출처 노드 제거 (재실행 시 중복 방지)
    existing_nodes = [
        nid for nid, data in graph.G.nodes(data=True)
        if data.get("type") == node_type and data.get("출처URL") == url
    ]
    for nid in existing_nodes:
        graph.G.remove_node(nid)
    if existing_nodes:
        logger.info("기존 %s 노드 %d개 제거 (재인제스트)", node_type, len(existing_nodes))

    added_nodes = []
    for section in sections:
        title = section["title"]
        fields = dict(section["fields"])
        full_text = section.get("full_text", "").strip()

        if full_text:
            fields["설명"] = full_text

        # 출처 메타데이터 추가
        fields["출처URL"] = url
        fields["출처명"] = source_name

        if graph_type == "leave_of_absence":
            node_id = graph.add_leave_info(name=title, data=fields)
        elif graph_type == "scholarship":
            node_id = graph.add_scholarship_page_info(name=title, data=fields)
        else:
            raise ValueError(f"지원하지 않는 graph_type: {graph_type}")

        added_nodes.append(node_id)
        logger.info("  노드 추가: %s ← %s", node_id, title)

    # 섹션 순서 엣지 (선택적)
    for i in range(len(added_nodes) - 1):
        graph.add_relation(added_nodes[i], added_nodes[i + 1], "연결된다")

    graph.save()
    logger.info(
        "그래프 저장 완료: %s 노드 %d개 추가, 전체 노드=%d개",
        node_type, len(added_nodes), graph.G.number_of_nodes(),
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="정적 HTML 페이지를 ChromaDB + 그래프에 인제스트합니다.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
예시:
  # 직접 지정
  python scripts/ingest_static_page.py \\
      --url "https://m.bufs.ac.kr/Information/SAHJ1010.aspx?mc=0951" \\
      --source-name 휴복학안내 --graph-type leave_of_absence

  # preset 사용
  python scripts/ingest_static_page.py --preset leave_of_absence
  python scripts/ingest_static_page.py --preset scholarship_info

  # 카테고리 전체
  python scripts/ingest_static_page.py --category 학사
  python scripts/ingest_static_page.py --category 장학
""",
    )
    parser.add_argument("--url", help="크롤링할 URL (preset/category 미사용 시 필수)")
    parser.add_argument("--source-name", help="출처명 (예: 휴복학안내)")
    parser.add_argument(
        "--content-type",
        default="guide",
        choices=["guide", "notice", "news", "event", "timetable"],
        help="콘텐츠 유형 (기본: guide)",
    )
    parser.add_argument(
        "--graph-type",
        default="leave_of_absence",
        choices=list(_TYPE_MAP.keys()),
        help="그래프 노드 유형 (기본: leave_of_absence)",
    )
    parser.add_argument(
        "--preset",
        help="config/static_pages.json에 정의된 preset ID",
    )
    parser.add_argument(
        "--category",
        help="config/static_pages.json 카테고리 전체 실행 (예: 학사, 장학)",
    )
    args = parser.parse_args()

    # ── 카테고리 일괄 실행 ───────────────────────────────────────
    if args.category:
        config = _load_config()
        entries = config.get(args.category)
        if not entries:
            logger.error("카테고리 '%s' 를 config에서 찾을 수 없습니다.", args.category)
            sys.exit(1)
        logger.info("카테고리 '%s' 일괄 실행: %d개 항목", args.category, len(entries))
        for entry in entries:
            ingest_static_page(
                url=entry["url"],
                source_name=entry["source_name"],
                content_type=entry.get("content_type", "guide"),
                graph_type=entry["graph_type"],
            )
        return

    # ── Preset 로드 ──────────────────────────────────────────────
    if args.preset:
        entry = _find_preset(args.preset)
        url = args.url or entry["url"]
        source_name = args.source_name or entry["source_name"]
        content_type = entry.get("content_type", args.content_type)
        graph_type = entry["graph_type"]
    else:
        # 직접 지정 모드 — --url, --source-name 필수
        if not args.url or not args.source_name:
            parser.error("--preset 또는 --category 미사용 시 --url, --source-name 이 필요합니다.")
        url = args.url
        source_name = args.source_name
        content_type = args.content_type
        graph_type = args.graph_type

    ingest_static_page(
        url=url,
        source_name=source_name,
        content_type=content_type,
        graph_type=graph_type,
    )


if __name__ == "__main__":
    main()
