"""
전체 재인제스트: PDF + 정적 페이지를 단일 프로세스에서 순차 실행.
ChromaDB HNSW 인덱스 손상 방지를 위해 하나의 클라이언트로 처리합니다.

사용법:
  python scripts/ingest_all.py
"""

import sys
import json
import logging
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from scripts.ingest_pdf import ingest_pdf
from scripts.pdf_to_graph import build_graph_from_pdf
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

CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "static_pages.json"

# ingest_static_page.py와 동일 매핑
_TYPE_MAP = {
    "leave_of_absence": "휴복학",
    "scholarship": "장학금",
    "free_semester": "자유학기제",
    "registration_guide": "수강신청규칙",
    "attendance": "전자출결",
    "grading": "성적처리",
    "tuition_refund": "등록금반환",
    "graduation_guide": "졸업요건",
    "teacher_training": "교직",
}
_PREFIX_MAP = {
    "leave_of_absence": "leave_info_",
    "scholarship": "sch_info_",
    "free_semester": "free_sem_",
    "registration_guide": "reg_guide_",
    "attendance": "attend_",
    "grading": "grade_",
    "tuition_refund": "refund_",
    "graduation_guide": "grad_guide_",
    "teacher_training": "teacher_page_",
}
_METHOD_MAP = {
    "leave_of_absence": "add_leave_info",
    "scholarship": "add_scholarship_page_info",
    "registration_guide": "add_registration_guide_info",
    "graduation_guide": "add_graduation_guide_info",
    "teacher_training": "add_teacher_training_page_info",
    "free_semester": "add_static_page_info",
    "attendance": "add_static_page_info",
    "grading": "add_static_page_info",
    "tuition_refund": "add_static_page_info",
}


def _classify_grade_node(title: str, text: str) -> str:
    """성적처리 노드를 키워드 기반으로 분류합니다."""
    t = (title + " " + text).lower()
    if "ocu" in t or "사이버" in t or "컨소시엄" in t or "상대평가" in t:
        return "OCU"
    if "성적선택" in t or "성적평가 선택" in t or "성적포기" in t or "부분적 성적" in t:
        return "성적선택제"
    if "학사경고" in t:
        return "학사경고"
    if any(kw in t for kw in ("p/np", "캡스톤", "현장실습", "사회봉사", "진로탐색", "취업커뮤니티")):
        return "P/NP"
    return "일반"


def main():
    # ── 0. PDF → 그래프 빌드 (학사일정, 수강신청규칙, 졸업요건 등) ──
    pdf_path = "data/pdfs/2026학년도1학기학사안내.pdf"
    if Path(pdf_path).exists():
        # 원칙 3: PDF 버전 체크 — 미변경 시 재빌드 건너뜀
        from app.crawler.pdf_version_tracker import PdfVersionTracker
        tracker = PdfVersionTracker()
        pdf_changed = tracker.has_changed(pdf_path)

        if not pdf_changed:
            logger.info("PDF 미변경 — 그래프 재빌드 건너뜀")
            graph_result = AcademicGraph()
        else:
            logger.info("=== 그래프 빌드 (PDF → 그래프) ===")
            graph_result = build_graph_from_pdf(str(Path(pdf_path).resolve()))
            if graph_result:
                # 조기졸업·장학금 데이터 보완 (build_graph.py와 동일)
                from scripts.build_graph import _add_early_graduation_data, _add_scholarship_data
                _add_early_graduation_data(graph_result)
                _add_scholarship_data(graph_result)
                graph_result.save()
                tracker.update(
                    pdf_path,
                    node_count=graph_result.G.number_of_nodes(),
                    edge_count=graph_result.G.number_of_edges(),
                )
                logger.info("그래프 빌드 완료: %d노드, %d엣지",
                            graph_result.G.number_of_nodes(), graph_result.G.number_of_edges())

    # ── 1. PDF 인제스트 (ChromaDB) ───────────────────────────
    if Path(pdf_path).exists():
        logger.info("=== PDF 인제스트: %s ===", pdf_path)
        ingest_pdf(pdf_path=pdf_path, student_id="2025", doc_type="domestic")
    else:
        logger.warning("PDF 없음: %s", pdf_path)

    # ── 2. 정적 페이지 전체 크롤링 ──────────────────────────
    with CONFIG_PATH.open(encoding="utf-8") as f:
        config = json.load(f)

    crawler = StaticPageCrawler()
    all_crawled_items = []
    all_sections_map = {}  # url → (sections, entry)

    for category, entries in config.items():
        logger.info("=== 카테고리 '%s' 크롤링: %d개 항목 ===", category, len(entries))
        for entry in entries:
            url = entry["url"]
            logger.info("--- 크롤링: %s ---", entry["source_name"])
            crawled_item, sections = crawler.fetch_and_parse(url, entry["source_name"])
            crawled_item.content_type = entry.get("content_type", "guide")
            crawled_item.metadata["content_type"] = crawled_item.content_type
            all_crawled_items.append(crawled_item)
            all_sections_map[url] = (sections, entry)

    # ── 3. ChromaDB 일괄 인제스트 (ChangeDetector에 전체 전달) ──
    logger.info("=== ChromaDB 인제스트: %d개 페이지 ===", len(all_crawled_items))
    embedder = Embedder()
    chroma_store = ChromaStore(embedder=embedder)
    blacklist = ContentBlacklist()
    detector = ChangeDetector()
    updater = IncrementalUpdater(chroma_store, blacklist)

    events = detector.detect(all_crawled_items)
    if not events:
        logger.info("변경 없음 — ChromaDB 업데이트 스킵")
    else:
        report = updater.process_events(events)
        detector.commit(events)
        logger.info(
            "ChromaDB 업데이트: added=%d, updated=%d, errors=%d",
            report.added, report.updated, len(report.errors),
        )

    # ── 4. 그래프 인제스트 ───────────────────────────────────
    logger.info("=== 그래프 인제스트 ===")
    graph = AcademicGraph()

    for url, (sections, entry) in all_sections_map.items():
        if not sections:
            logger.warning("섹션 없음 — 스킵: %s", url)
            continue

        graph_type = entry["graph_type"]
        node_type = _TYPE_MAP[graph_type]
        source_name = entry["source_name"]

        # 기존 동일 출처 노드 제거
        existing = [
            nid for nid, data in graph.G.nodes(data=True)
            if data.get("type") == node_type and data.get("출처URL") == url
        ]
        for nid in existing:
            graph.G.remove_node(nid)
        if existing:
            logger.info("기존 %s 노드 %d개 제거", node_type, len(existing))

        added_nodes = []
        for section in sections:
            title = section["title"]
            fields = dict(section["fields"])
            full_text = section.get("full_text", "").strip()
            if full_text:
                fields["설명"] = full_text
            fields["출처URL"] = url
            fields["출처명"] = source_name

            method_name = _METHOD_MAP[graph_type]
            method = getattr(graph, method_name)
            if method_name == "add_static_page_info":
                node_id = method(
                    name=title, data=fields,
                    node_type=node_type, prefix=_PREFIX_MAP[graph_type],
                )
            else:
                node_id = method(name=title, data=fields)
            added_nodes.append(node_id)

        # ── 성적처리 노드: 분류태그 추가 + grading_root → 자식 엣지 ──
        if graph_type == "grading" and added_nodes:
            for nid in added_nodes:
                data = graph.G.nodes[nid]
                tag = _classify_grade_node(
                    data.get("구분", ""), data.get("설명", "")
                )
                graph.G.nodes[nid]["분류태그"] = tag

            # grading_root 노드 생성 (한 번만)
            root_id = "grading_root"
            if root_id not in graph.G.nodes:
                graph.G.add_node(root_id, type="성적처리", 구분="성적처리기준")
                graph._index_add(root_id, "성적처리")
            for nid in added_nodes:
                graph.G.add_edge(root_id, nid, relation="포함한다")

            logger.info(
                "성적처리 분류: %s",
                {nid: graph.G.nodes[nid].get("분류태그") for nid in added_nodes},
            )

        logger.info("%s: %d개 노드 추가", source_name, len(added_nodes))

    graph.save()
    logger.info(
        "그래프 저장 완료: 전체 노드=%d, 엣지=%d",
        graph.G.number_of_nodes(), graph.G.number_of_edges(),
    )

    # ── 5. 검증 ──────────────────────────────────────────────
    count = chroma_store.collection.count()
    logger.info("=== 최종 검증 ===")
    logger.info("ChromaDB: %d개 청크", count)
    logger.info("Graph: %d개 노드, %d개 엣지", graph.G.number_of_nodes(), graph.G.number_of_edges())
    logger.info("=== 전체 인제스트 완료 ===")


if __name__ == "__main__":
    main()
