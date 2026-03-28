"""
검색 파이프라인 실전 테스트
1. 현재 ChromaDB 상태 확인
2. 실제 크롤 데이터를 IncrementalUpdater로 메인 DB에 인제스트
3. 다양한 쿼리로 검색 → 결과 + 메타데이터 출력
4. 첨부파일 청크가 실제 검색에 노출되는지 확인

실행: .venv/Scripts/python scripts/test_search_pipeline.py
"""

import sys
import io
import logging
from pathlib import Path

# Windows 터미널 cp949 인코딩 문제 해결
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding="utf-8", errors="replace")

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(
    level=logging.WARNING,   # 로그 줄이기
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
)

SEP = "=" * 60

# ──────────────────────────────────────────────
# STEP 1: 현재 DB 상태
# ──────────────────────────────────────────────
print(f"\n{SEP}")
print("STEP 1: 현재 ChromaDB 상태")
print(SEP)

from app.vectordb.chroma_store import ChromaStore
store = ChromaStore()
total_before = store.count()
print(f"  총 청크 수: {total_before}개")

# doc_type별 통계
result_all = store.collection.get(include=["metadatas"])
metas = result_all.get("metadatas", [])

from collections import Counter
doc_type_counts = Counter(m.get("doc_type", "?") for m in metas)
file_type_counts = Counter(m.get("file_type", "") for m in metas if m.get("file_type"))

print("  doc_type 분포:")
for dt, cnt in doc_type_counts.most_common():
    print(f"    {dt}: {cnt}개")
if file_type_counts:
    print("  file_type 분포 (첨부파일):")
    for ft, cnt in file_type_counts.most_common():
        print(f"    {ft}: {cnt}개")
else:
    print("  file_type: 없음 (첨부파일 미인제스트 상태)")

# ──────────────────────────────────────────────
# STEP 2: 실제 크롤 → IncrementalUpdater로 인제스트
# ──────────────────────────────────────────────
print(f"\n{SEP}")
print("STEP 2: 크롤링 + 첨부파일 IncrementalUpdater 인제스트")
print(SEP)

print("  공지사항 크롤링 중...")
from app.crawler.notice_crawler import NoticeCrawler
crawler = NoticeCrawler()
items = crawler.crawl()
print(f"  수집: {len(items)}건")
for it in items:
    print(f"    [{it.metadata.get('post_date','')}] {it.title[:45]}  첨부:{len(it.attachments)}개")

print()
from app.crawler.change_detector import ChangeDetector
from app.crawler.blacklist import ContentBlacklist
from app.ingestion.incremental_update import IncrementalUpdater

detector = ChangeDetector()
events = detector.detect(items)

new_cnt = sum(1 for e in events if e.change_type.value == "new")
mod_cnt = sum(1 for e in events if e.change_type.value == "modified")
del_cnt = sum(1 for e in events if e.change_type.value == "deleted")
print(f"  변경 감지: NEW={new_cnt}, MODIFIED={mod_cnt}, DELETED={del_cnt}")

if not events:
    print("  => 변경 없음. 강제로 전체 재인제스트합니다.")
    # 전체를 NEW로 강제 처리
    from app.crawler.change_detector import ChangeEvent, ChangeType
    events = [
        ChangeEvent(
            source_id=it.source_id,
            change_type=ChangeType.NEW,
            old_hash=None,
            new_hash=it.content_hash,
            title=it.title,
            content=it.content,
            attachments=it.attachments,
            metadata=it.metadata,
        )
        for it in items
    ]
    print(f"  => 강제 이벤트 {len(events)}건 생성")

blacklist = ContentBlacklist()
updater = IncrementalUpdater(chroma_store=store, blacklist=blacklist)

print("  인제스트 중 (첨부파일 다운로드 포함)...")
import logging as _log
_log.getLogger("app.ingestion").setLevel(_log.INFO)
_log.getLogger("app.crawler").setLevel(_log.INFO)

report = updater.process_events(events)

print(f"\n  인제스트 결과:")
print(f"    추가: {report.added}청크")
print(f"    수정: {report.updated}청크")
print(f"    삭제: {report.deleted}건")
print(f"    스킵: {report.skipped}건")
if report.errors:
    print(f"    오류: {len(report.errors)}건")
    for e in report.errors[:3]:
        print(f"      - {e}")

# 커밋 (성공한 이벤트만)
failed_ids = report.failed_source_ids
success_events = [e for e in events if e.source_id not in failed_ids]
detector.commit(success_events)
print(f"  변경 해시 커밋: {len(success_events)}건")

total_after = store.count()
print(f"\n  DB 청크 수: {total_before} -> {total_after} (+{total_after - total_before})")

# ──────────────────────────────────────────────
# STEP 3: 검색 테스트
# ──────────────────────────────────────────────
print(f"\n{SEP}")
print("STEP 3: 검색 쿼리 테스트")
print(SEP)

queries = [
    # 공지 본문 검색
    ("수강신청 기간은 언제야?",          None,   None),
    ("OCU 강의 신청 방법",               None,   None),
    # 첨부파일(XLSX) 검색
    ("성적 변경 신청 방법",              None,   None),
    ("수업 성적변경",                    None,   None),
    # 첨부파일(HWP) 검색
    ("OCU 중간고사 수강신청서",          None,   None),
    # 기존 PDF 학사안내 검색
    ("졸업 요건은 몇 학점이야?",         "2024", "2026-1"),
    ("전공 필수 이수 학점",              "2023", None),
]

for query, student_id, semester in queries:
    label = f"  Q: {query}"
    if student_id:
        label += f"  [학번:{student_id}]"
    if semester:
        label += f"  [학기:{semester}]"
    print(label)

    results = store.search(
        query=query,
        n_results=3,
        student_id=student_id,
        semester=semester,
    )

    if not results:
        print("    => 결과 없음")
    else:
        for i, r in enumerate(results, 1):
            meta = r.metadata
            doc_type = meta.get("doc_type", "?")
            file_type = meta.get("file_type", "")
            filename  = meta.get("filename", "")
            src_name  = meta.get("source_name", "")
            post_date = meta.get("post_date", "")
            title     = meta.get("title", "")
            is_table  = meta.get("is_table", False)
            score     = r.score

            type_tag = f"{doc_type}"
            if file_type:
                type_tag += f"/{file_type}"
            if is_table:
                type_tag += "/table"

            src_info = filename or title or src_name or r.source
            text_preview = r.text[:80].replace("\n", " ")

            print(f"    [{i}] score={score:.3f}  [{type_tag}]  {src_info[:40]}")
            print(f"         {text_preview!r}...")
    print()

# ──────────────────────────────────────────────
# STEP 4: 첨부파일 청크 존재 여부 직접 확인
# ──────────────────────────────────────────────
print(f"\n{SEP}")
print("STEP 4: 첨부파일 청크 직접 조회 (doc_type=notice_attachment)")
print(SEP)

try:
    attach_result = store.collection.get(
        where={"doc_type": {"$eq": "notice_attachment"}},
        include=["metadatas", "documents"],
    )
    attach_ids   = attach_result.get("ids", [])
    attach_docs  = attach_result.get("documents", [])
    attach_metas = attach_result.get("metadatas", [])

    print(f"  notice_attachment 청크 수: {len(attach_ids)}")
    for i, (doc, meta) in enumerate(zip(attach_docs, attach_metas), 1):
        ft       = meta.get("file_type", "?")
        fname    = meta.get("filename", "?")
        post_dt  = meta.get("post_date", "?")
        src_name = meta.get("source_name", "?")
        is_tbl   = meta.get("is_table", False)
        cohort   = f"{meta.get('cohort_from','?')}~{meta.get('cohort_to','?')}"
        sem      = meta.get("semester", "?")
        preview  = doc[:60].replace("\n", " ")

        print(f"\n  [{i}] {ft.upper()}  {fname}")
        print(f"       게시일={post_dt}  학기={sem}  학번범위={cohort}  테이블={is_tbl}")
        print(f"       출처={src_name}")
        print(f"       내용: {preview!r}...")
except Exception as e:
    print(f"  조회 실패: {e}")

print(f"\n{SEP}")
print("테스트 완료")
print(SEP)
print(f"  최종 DB 청크 수: {store.count()}개\n")
