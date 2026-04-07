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
from app.crawler.change_detector import ChangeDetector, ChangeType
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
# 정적 페이지 → 그래프 노드 매핑
# 제거됨: registration_guide (PDF 수강신청규칙이 더 정확)
# 제거됨: graduation_guide (PDF 졸업요건이 학번별로 더 상세)
# 제거됨: scholarship (data/scholarships.json이 더 상세)
_TYPE_MAP = {
    "leave_of_absence": "휴복학",
    "free_semester": "자유학기제",
    "attendance": "전자출결",
    "grading": "성적처리",
    "tuition_refund": "등록금반환",
    "teacher_training": "교직",
    # v3: 학생포털 11개 카테고리 확장
    "registration": "수강신청안내",
    "graduation": "졸업안내",
    "curriculum": "교육과정",
    "major_change": "전공/전과",
    "scholarship": "장학금",
    "exchange": "교류프로그램",
}
_PREFIX_MAP = {
    "leave_of_absence": "leave_info_",
    "free_semester": "free_sem_",
    "attendance": "attend_",
    "grading": "grade_",
    "tuition_refund": "refund_",
    "teacher_training": "teacher_page_",
    # v3 확장
    "registration": "reg_guide_",
    "graduation": "grad_guide_",
    "curriculum": "curr_",
    "major_change": "major_page_",
    "scholarship": "sch_page_",
    "exchange": "exch_",
}
_METHOD_MAP = {
    "leave_of_absence": "add_leave_info",
    "teacher_training": "add_teacher_training_page_info",
    "free_semester": "add_static_page_info",
    "attendance": "add_static_page_info",
    "grading": "add_static_page_info",
    "tuition_refund": "add_static_page_info",
    # v3 확장 — 범용 메서드로 처리
    "registration": "add_static_page_info",
    "graduation": "add_static_page_info",
    "curriculum": "add_static_page_info",
    "major_change": "add_static_page_info",
    "scholarship": "add_static_page_info",
    "exchange": "add_static_page_info",
}
# 루트 노드 생성 대상 (grading_root 패턴 확장)
_ROOT_NODE_MAP = {
    "leave_of_absence": "leave_root",
    "free_semester": "free_sem_root",
    "attendance": "attend_root",
    "tuition_refund": "refund_root",
    "teacher_training": "teacher_root",
    # grading: 이미 별도 로직에서 grading_root 생성
    # v3 확장
    "registration": "reg_guide_root",
    "graduation": "grad_guide_root",
    "curriculum": "curr_root",
    "major_change": "major_page_root",
    "scholarship": "sch_page_root",
    "exchange": "exch_root",
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
        ingest_pdf(pdf_path=pdf_path, student_id="2024", doc_type="domestic")
    else:
        logger.warning("PDF 없음: %s", pdf_path)

    # ── 1-2. 수업시간표 PDF 인제스트 (ChromaDB) ─────────────
    timetable_path = "data/pdfs/2026학년도 1학기 수업시간표.pdf"
    if Path(timetable_path).exists():
        logger.info("=== PDF 인제스트: %s ===", timetable_path)
        ingest_pdf(pdf_path=timetable_path, student_id="", doc_type="timetable")
    else:
        logger.warning("PDF 없음: %s", timetable_path)

    # ── 1b. 포털 PDF 인제스트 (학생포털시스템 캡처 PDF) ────────
    portal_dir = ROOT / "data" / "pdfs" / "portal"
    if portal_dir.exists():
        portal_pdfs = sorted(portal_dir.glob("*.pdf"))
        if portal_pdfs:
            logger.info("=== 포털 PDF 인제스트: %d개 ===", len(portal_pdfs))
            for pdf_file in portal_pdfs:
                logger.info("  → %s", pdf_file.name)
                ingest_pdf(pdf_path=str(pdf_file), student_id="2024", doc_type="domestic")

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

        # ── 루트 노드 + 엣지 구조 (grading_root 패턴 확장) ──
        root_id = _ROOT_NODE_MAP.get(graph_type)
        if root_id and added_nodes and graph_type != "grading":
            if root_id not in graph.G.nodes:
                graph.G.add_node(root_id, type=node_type, 구분=f"{node_type} 안내")
                graph._index_add(root_id, node_type)
            for nid in added_nodes:
                graph.G.add_edge(root_id, nid, relation="포함한다")
            logger.info(
                "%s → %d개 하위 노드 엣지 생성", root_id, len(added_nodes)
            )

        logger.info("%s: %d개 노드 추가", source_name, len(added_nodes))

    graph.save()
    logger.info(
        "그래프 저장 완료: 전체 노드=%d, 엣지=%d",
        graph.G.number_of_nodes(), graph.G.number_of_edges(),
    )

    # ── 5. 고정공지 인제스트 (벡터DB + 그래프DB) ──────────────
    logger.info("=== 고정공지 인제스트 ===")
    from app.crawler.notice_crawler import NoticeCrawler
    from app.graphdb.notice_graph_builder import NoticeGraphBuilder

    notice_crawler = NoticeCrawler()
    notice_items = notice_crawler.crawl(pinned_only=True)
    logger.info("고정공지 수집: %d건", len(notice_items))

    if notice_items:
        # ChromaDB 업데이트
        notice_events = detector.detect(notice_items)
        notice_events = [e for e in notice_events if e.change_type != ChangeType.DELETED]
        if notice_events:
            notice_report = updater.process_events(notice_events)
            successful = [
                e for e in notice_events
                if e.source_id not in notice_report.failed_source_ids
            ]
            if successful:
                detector.commit(successful)
            logger.info(
                "고정공지 ChromaDB: added=%d, updated=%d, errors=%d",
                notice_report.added, notice_report.updated, len(notice_report.errors),
            )
        else:
            logger.info("고정공지 변경 없음 — ChromaDB 스킵")

        # 그래프DB 업데이트
        graph = AcademicGraph()  # 최신 그래프 reload
        notice_builder = NoticeGraphBuilder()
        graph_stats = notice_builder.build_from_items(graph, notice_items)
        graph.save()
        logger.info(
            "고정공지 그래프: 노드=%d, 엣지=%d",
            graph_stats["added"] + graph_stats["updated"],
            graph_stats["edges"],
        )

    # ── 6. FAQ 인제스트 (벡터DB + 그래프DB) ────────────────────
    # 원칙 1: FAQ를 그래프 1급 시민으로 편입 (FaqNodeBuilder)
    # 원칙 3: ChangeDetector 기반 증분 업데이트
    logger.info("=== FAQ 인제스트 ===")
    from app.graphdb.faq_node_builder import FaqNodeBuilder
    from scripts.ingest_faq import (
        load_faq as _faq_load,
        to_crawled_item as _faq_to_item,
        create_chunk as _faq_chunk,
    )

    faq_path = Path("data/faq_academic.json")
    if faq_path.exists():
        faq_data = _faq_load(faq_path)
        logger.info("FAQ 로드: %d개 항목", len(faq_data))

        # 증분 감지
        faq_crawled = [_faq_to_item(item) for item in faq_data]
        faq_events = detector.detect(faq_crawled)

        if not faq_events:
            logger.info("FAQ 변경 없음 — 벡터/그래프 스킵")
        else:
            # ChromaDB 업데이트 (변경/신규 FAQ만)
            item_by_id = {item.get("id"): item for item in faq_data if item.get("id")}
            chunks_to_add = []
            ids_to_delete = []
            for event in faq_events:
                faq_id = event.metadata.get("faq_id") if event.metadata else None
                if not faq_id and event.source_id.startswith("faq://"):
                    faq_id = event.source_id[len("faq://"):]

                existing = chroma_store.collection.get(where={"faq_id": faq_id}) if faq_id else None
                if existing and existing.get("ids"):
                    ids_to_delete.extend(existing["ids"])

                if event.change_type != ChangeType.DELETED:
                    item = item_by_id.get(faq_id)
                    if item:
                        chunk = _faq_chunk(item, faq_path.name)
                        if chunk:
                            chunks_to_add.append(chunk)

            if ids_to_delete:
                chroma_store.collection.delete(ids=ids_to_delete)
            if chunks_to_add:
                chroma_store.add_chunks(chunks_to_add)
            logger.info(
                "FAQ ChromaDB: +%d -%d",
                len(chunks_to_add), len(ids_to_delete),
            )

            # 그래프 업데이트 — build_from_items이 upsert + 미존재 삭제 처리
            graph = AcademicGraph()
            faq_builder = FaqNodeBuilder()
            faq_stats = faq_builder.build_from_items(graph, faq_data)
            graph.save()
            logger.info(
                "FAQ 그래프: 추가=%d, 업데이트=%d, 제거=%d, 엣지=%d",
                faq_stats["added"], faq_stats["updated"],
                faq_stats["removed"], faq_stats["edges"],
            )

            detector.commit(faq_events)
    else:
        logger.warning("FAQ 파일 없음: %s", faq_path)

    # ── 7. 검증 ──────────────────────────────────────────────
    count = chroma_store.collection.count()
    final_graph = AcademicGraph()
    logger.info("=== 최종 검증 ===")
    logger.info("ChromaDB: %d개 청크", count)
    logger.info("Graph: %d개 노드, %d개 엣지", final_graph.G.number_of_nodes(), final_graph.G.number_of_edges())
    logger.info("FAQ 노드: %d개", len(final_graph._type_index.get("FAQ", [])))
    logger.info("=== 전체 인제스트 완료 ===")


if __name__ == "__main__":
    main()
